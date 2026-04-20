"""
NIFTY_INTRADAY_VWAP_EMA_BREAKOUT strategy — v2.

Entry rules (ALL conditions must be true):
  CE: close > VWAP (≥0.15% away), EMA20 trending up + strong slope,
      strong bullish candle (body ≥55%), breakout (high > prev high),
      2/3 candles bullish, RSI > 50, efficiency ≥ 0.45,
      volume surge ≥1.2× avg, not a spike candle
  PE: close < VWAP (≥0.15% away), EMA20 trending down + strong slope,
      strong bearish candle (body ≥55%), breakout (low < prev low),
      2/3 candles bearish, RSI < 50, efficiency ≥ 0.45,
      volume surge ≥1.2× avg, not a spike candle

Do NOT trade:
  - Sideways market (efficiency < 45% + VWAP crossings ≥2)
  - Spike candles (range > 1%)
  - Before 9:50 AM (skip opening noise) or after 14:00 (time-decay risk)

Per-instrument thresholds are read from cfg (INSTRUMENT_CONFIG in config.py).
"""
import logging
from datetime import datetime, time
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from services.trading_state import Candle
from services.indicators import (
    ema_trending_up, ema_trending_down,
    ema_slope_strong_up, ema_slope_strong_down,
    is_strong_bullish, is_strong_bearish,
    is_spike_candle,
    has_volume_surge, is_far_enough_from_vwap,
    multi_candle_confirmation,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN_READY = time(9, 50)   # skip first 30 min opening noise/gap fills
LAST_ENTRY_TIME   = time(14, 0)   # no entries after 2 PM — time-decay risk
FORCE_EXIT_TIME   = time(15, 20)


class Signal(str, Enum):
    BUY_CE    = "BUY_CE"
    BUY_PE    = "BUY_PE"
    NO_SIGNAL = "NO_SIGNAL"


def now_ist() -> datetime:
    return datetime.now(IST)


def is_market_open_and_ready() -> bool:
    t = now_ist().time()
    return MARKET_OPEN_READY <= t < LAST_ENTRY_TIME


def is_force_exit_time() -> bool:
    return now_ist().time() >= FORCE_EXIT_TIME


def generate_signal(
    candles: list[Candle],
    vwap: float,
    ema20: Optional[float],
    ema20_series: list,
    market_state: str,
    rsi14: Optional[float] = None,
    volume_surge: bool = True,
    efficiency: float = 0.0,
    opening_rsi: Optional[float] = None,
    cfg: Optional[dict] = None,
) -> tuple[Signal, str]:
    """
    Returns (Signal, reason_string).
    reason_string describes WHY the signal was triggered (for CSV logging).
    """
    if len(candles) < 3:
        return Signal.NO_SIGNAL, "insufficient candles"

    if market_state == "SIDEWAYS":
        return Signal.NO_SIGNAL, "market is sideways — skipping"

    if ema20 is None:
        return Signal.NO_SIGNAL, "EMA20 not ready"

    current = candles[-1]
    prev    = candles[-2]

    # Safety: skip spike candles
    if is_spike_candle(current):
        logger.info("Spike candle detected (range %.2f%%) — skipping", (current.high - current.low) / current.close * 100)
        return Signal.NO_SIGNAL, f"spike candle ({(current.high - current.low) / current.close * 100:.2f}% range)"

    # ── Pull per-instrument thresholds from cfg (safe defaults = original behaviour) ──
    rsi_min_ce             = cfg.get("rsi_min_ce",             50)   if cfg else 50
    rsi_max_ce             = cfg.get("rsi_max_ce",             100)  if cfg else 100
    rsi_min_pe             = cfg.get("rsi_min_pe",             0)    if cfg else 0
    rsi_max_pe             = cfg.get("rsi_max_pe",             50)   if cfg else 50
    vwap_dist_min          = cfg.get("vwap_dist_min_pct",      0.15) if cfg else 0.15
    price_ema_gap_min_ce   = cfg.get("price_ema_gap_min_ce",   0.0)  if cfg else 0.0
    price_ema_gap_max_ce   = cfg.get("price_ema_gap_max_ce",   999)  if cfg else 999
    price_ema_gap_min_pe   = cfg.get("price_ema_gap_min_pe",   0.0)  if cfg else 0.0
    price_ema_gap_max_pe   = cfg.get("price_ema_gap_max_pe",   999)  if cfg else 999
    vwap_dist_max_pe       = cfg.get("vwap_dist_max_pe_pct",   999)  if cfg else 999
    opening_rsi_ob         = cfg.get("opening_rsi_overbought", 999)  if cfg else 999
    opening_rsi_os         = cfg.get("opening_rsi_oversold",   0)    if cfg else 0
    efficiency_min_ce      = cfg.get("efficiency_min_ce",      0.0)  if cfg else 0.0
    efficiency_min_pe      = cfg.get("efficiency_min_pe",      0.0)  if cfg else 0.0

    # ── Opening day RSI bias — block dangerous sessions entirely ──────────
    if opening_rsi is not None:
        if opening_rsi > opening_rsi_ob or opening_rsi < opening_rsi_os:
            logger.info("Opening RSI %.1f in danger zone — all trades blocked for the day", opening_rsi)
            return Signal.NO_SIGNAL, f"opening RSI {opening_rsi:.1f} danger zone — day blocked"

    # ── COMMON FILTERS (apply before CE/PE checks) ──────────────────────
    # Volume: require above-average volume to confirm breakout
    if not volume_surge:
        logger.debug("No volume surge — skipping entry")
        return Signal.NO_SIGNAL, "low volume — no institutional participation"

    # VWAP distance: avoid entries hugging VWAP (reversal zone)
    if not is_far_enough_from_vwap(current.close, vwap, min_pct=vwap_dist_min):
        logger.debug("Too close to VWAP (%.2f vs %.2f) — skipping", current.close, vwap)
        return Signal.NO_SIGNAL, "too close to VWAP"

    # ── CE (CALL) ENTRY ──────────────────────────────────────────────────
    ce_gap_pct = (current.close - ema20) / ema20 * 100 if ema20 else 0.0
    ce_conditions = {
        "close > VWAP":            current.close > vwap,
        "EMA20 trending up":       ema_trending_up(ema20_series),
        "EMA20 slope strong":      ema_slope_strong_up(ema20_series),
        "strong bullish candle":   is_strong_bullish(current),
        "breakout high":           current.high > prev.high,
        "2/3 candles bullish":     multi_candle_confirmation(candles, "bullish"),
        "RSI in range":            rsi14 is not None and rsi_min_ce <= rsi14 <= rsi_max_ce,
        "price-EMA gap":           price_ema_gap_min_ce <= ce_gap_pct <= price_ema_gap_max_ce,
        "efficiency":              efficiency >= efficiency_min_ce,
    }

    if all(ce_conditions.values()):
        reason = (
            f"close={current.close:.1f} > VWAP={vwap:.1f} | "
            f"EMA20={ema20:.1f} trending up | "
            f"RSI={rsi14:.1f} | "
            f"breakout: high {current.high:.1f} > {prev.high:.1f}"
        )
        logger.info("BUY_CE signal | %s", reason)
        return Signal.BUY_CE, reason

    # ── PE (PUT) ENTRY ───────────────────────────────────────────────────
    pe_gap_pct     = (ema20 - current.close) / ema20 * 100 if ema20 else 0.0
    vwap_below_pct = (vwap - current.close) / vwap * 100  if vwap > 0 else 0.0
    pe_conditions = {
        "close < VWAP":            current.close < vwap,
        "EMA20 trending down":     ema_trending_down(ema20_series),
        "EMA20 slope strong":      ema_slope_strong_down(ema20_series),
        "strong bearish candle":   is_strong_bearish(current),
        "breakout low":            current.low < prev.low,
        "2/3 candles bearish":     multi_candle_confirmation(candles, "bearish"),
        "RSI in range":            rsi14 is not None and rsi_min_pe <= rsi14 <= rsi_max_pe,
        "price-EMA gap":           price_ema_gap_min_pe <= pe_gap_pct <= price_ema_gap_max_pe,
        "VWAP dist not extreme":   vwap_below_pct <= vwap_dist_max_pe,
        "efficiency":              efficiency >= efficiency_min_pe,
    }

    if all(pe_conditions.values()):
        reason = (
            f"close={current.close:.1f} < VWAP={vwap:.1f} | "
            f"EMA20={ema20:.1f} trending down | "
            f"RSI={rsi14:.1f} | "
            f"breakout: low {current.low:.1f} < {prev.low:.1f}"
        )
        logger.info("BUY_PE signal | %s", reason)
        return Signal.BUY_PE, reason

    # Log which conditions nearly fired (useful for debugging)
    ce_failed = [k for k, v in ce_conditions.items() if not v]
    pe_failed = [k for k, v in pe_conditions.items() if not v]
    logger.info("No signal | CE failed: %s | PE failed: %s", ce_failed, pe_failed)

    return Signal.NO_SIGNAL, ""


def detect_opposite_signal(
    candles: list[Candle],
    current_option_type: str,   # "CE" or "PE"
    vwap: float,
    ema20_series: list,
    market_state: str,
) -> bool:
    """
    Returns True if a signal opposite to the open position has formed.
    Used as an additional exit trigger.
    """
    if len(candles) < 2 or market_state == "SIDEWAYS":
        return False

    current = candles[-1]
    prev = candles[-2]

    if current_option_type == "CE":
        # Opposite = bearish setup forming
        return (
            current.close < vwap
            and ema_trending_down(ema20_series)
            and is_strong_bearish(current)
            and current.low < prev.low
        )
    else:
        # Opposite = bullish setup forming
        return (
            current.close > vwap
            and ema_trending_up(ema20_series)
            and is_strong_bullish(current)
            and current.high > prev.high
        )
