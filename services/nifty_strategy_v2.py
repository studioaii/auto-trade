"""
NIFTY 2.0 — strategy.

This is NIFTY 1.0's VWAP+EMA breakout (the wide-tail trend-rider) PLUS the
improvements from the June-2026 NIFTY-1.0 log analysis:

  • Base entry  — v1's 9-condition CE/PE breakout (close vs VWAP, EMA trend +
    strong slope, strong body, breakout vs prior high/low, 2-of-3 confirmation,
    RSI band, efficiency, volume surge, no spike, not SIDEWAYS).
  • Regime gate — on top of the base signal, reject the range-top wick-poke
    breakouts that fail immediately:
      (1) close-confirmed breakout: the CLOSE (not just the high/low) must clear
          the prior swing by a margin — kills single-wick pokes (06-16/06-17);
      (2) VWAP-crossing chop guard: too many VWAP crossings recently = ranging;
      (3) session-cumulative chop gate (added 2026-07-02): once today's closes
          have flipped sides of VWAP ≥ session_max_vwap_crossings times, block
          entries for the rest of the day — chop-day entries ran 22% WR in the
          n=35 v1 study. (The 11:00 morning wall was removed the same day; its
          forward test blocked only winners.)
  • Softened opposite-signal exit detector (the 2-consecutive-close confirmation
    is counted in the engine; this module exposes the single-candle detector).

`evaluate_signal` returns an N2Setup carrying BOTH the final `signal` (after the
regime gate) and the raw `base_signal` (what v1 alone would have done) so the
engine can shadow-log every setup a gate blocked.

Pure functions — no I/O, no state mutation. The engine calls per closed candle.
Per-instrument thresholds are read from cfg (INSTRUMENT_CONFIG["NIFTY_2"]).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional

from services.trading_state import Candle
from services.indicators import (
    candle_body_pct,
    ema_trending_up, ema_trending_down,
    ema_slope_strong_up, ema_slope_strong_down,
    is_strong_bullish, is_strong_bearish,
    is_spike_candle, has_volume_surge,
    is_far_enough_from_vwap, multi_candle_confirmation,
)


# ---------------------------------------------------------------------------
# Enums / setup descriptor (symbol names kept for engine-import compatibility)
# ---------------------------------------------------------------------------

class N2Signal(str, Enum):
    BUY_CE    = "BUY_CE"
    BUY_PE    = "BUY_PE"
    NO_SIGNAL = "NO_SIGNAL"


class N2Model(str, Enum):
    NONE = "NONE"
    V1   = "V1_BREAKOUT"     # the only model now — v1 VWAP+EMA breakout
    # legacy names kept so any external reference still resolves
    M1   = "M1_VWAP_RECLAIM"
    M2   = "M2_ORB"
    M3   = "M3_PULLBACK"


@dataclass
class N2Setup:
    """Outcome of evaluating the strategy on the latest candle."""
    signal: N2Signal = N2Signal.NO_SIGNAL          # final decision (after regime gate)
    model:  N2Model  = N2Model.NONE
    reason: str = ""
    skip_reason: str = ""
    base_signal: N2Signal = N2Signal.NO_SIGNAL     # what v1 alone would have fired


@dataclass
class N2DayContext:
    """Per-day runtime context — mutated by the engine, read by strategy fns.

    Retained for engine compatibility (opening-range tracking is still computed
    and logged as informational context even though the breakout strategy does
    not gate on it)."""
    or_high: Optional[float] = None
    or_low:  Optional[float] = None
    or_locked: bool = False
    consecutive_up_candles: int = 0
    consecutive_dn_candles: int = 0
    orb_used: bool = False


# ---------------------------------------------------------------------------
# OR computation + per-candle leg tracker (kept for engine compatibility)
# ---------------------------------------------------------------------------

def compute_opening_range(
    candles_today: list[Candle],
    or_start_hhmm: tuple[int, int],
    or_end_hhmm: tuple[int, int],
) -> tuple[Optional[float], Optional[float]]:
    """Return (or_high, or_low) for candles whose timestamp is in [start, end]."""
    if not candles_today:
        return None, None
    start = time(*or_start_hhmm)
    end   = time(*or_end_hhmm)
    in_range = [c for c in candles_today if start <= c.timestamp.time() <= end]
    if not in_range:
        return None, None
    return max(c.high for c in in_range), min(c.low for c in in_range)


def update_consecutive_legs(ctx: N2DayContext, candle: Candle) -> None:
    if candle.close > candle.open:
        ctx.consecutive_up_candles += 1
        ctx.consecutive_dn_candles = 0
    elif candle.close < candle.open:
        ctx.consecutive_dn_candles += 1
        ctx.consecutive_up_candles = 0
    else:
        ctx.consecutive_up_candles = 0
        ctx.consecutive_dn_candles = 0


# ---------------------------------------------------------------------------
# Regime-stability gate (Tier-B improvement #1, mechanism half)
# ---------------------------------------------------------------------------

def _vwap_crossings(candles: list[Candle], vwap: float, lookback: int) -> int:
    """Count VWAP side-changes across the last `lookback` candle closes."""
    if vwap <= 0 or len(candles) < 2:
        return 0
    closes = [c.close for c in candles[-lookback:]]
    return sum(
        1 for i in range(1, len(closes))
        if (closes[i - 1] > vwap) != (closes[i] > vwap)
    )


def _session_vwap_crossings(candles: list[Candle]) -> int:
    """Count VWAP side-changes across ALL of today's session candle closes.

    Recomputes the running intraday VWAP at each candle (cumulative typical
    price × volume, session candles only — preloaded previous-day seed candles
    are excluded by the date filter) so every close is compared against the
    VWAP as it stood at that moment. This matches the candle-log data the
    gate threshold was derived from; comparing old closes against the *latest*
    VWAP (as `_vwap_crossings` does for its short window) would drift on a
    session-length window."""
    if not candles:
        return 0
    today = candles[-1].timestamp.date()
    session = [c for c in candles if c.timestamp.date() == today]
    if len(session) < 2:
        return 0
    cum_pv = 0.0
    cum_v = 0
    sides: list[bool] = []
    for c in session:
        typical = (c.high + c.low + c.close) / 3.0
        cum_pv += typical * c.volume
        cum_v += c.volume
        vwap = cum_pv / cum_v if cum_v > 0 else c.close
        sides.append(c.close > vwap)
    return sum(1 for i in range(1, len(sides)) if sides[i] != sides[i - 1])


def _regime_block_reason(
    candles: list[Candle],
    side: N2Signal,
    vwap: float,
    cfg: dict,
) -> str:
    """Return a non-empty reason if the regime gate blocks this breakout, else ''."""
    cur = candles[-1]

    # (1) close-confirmed breakout — the CLOSE must clear the prior swing by a
    #     margin. v1's base only required high>prev.high (a single wick poke
    #     qualifies); requiring a confirming close kills the range-top pokes.
    if cfg.get("require_close_breakout", True):
        lookback = int(cfg.get("breakout_lookback", 3))
        margin   = float(cfg.get("breakout_margin_pct", 0.05)) / 100.0
        prior = candles[-(lookback + 1):-1]
        if prior:
            if side == N2Signal.BUY_CE:
                swing_high = max(c.high for c in prior)
                if cur.close <= swing_high * (1 + margin):
                    return f"regime: close {cur.close:.1f} did not clear swing-high {swing_high:.1f} by {margin*100:.2f}%"
            else:
                swing_low = min(c.low for c in prior)
                if cur.close >= swing_low * (1 - margin):
                    return f"regime: close {cur.close:.1f} did not clear swing-low {swing_low:.1f} by {margin*100:.2f}%"

    # (2) VWAP-crossing chop guard — straddling VWAP = ranging, not trending.
    max_cross = int(cfg.get("regime_max_vwap_crossings", 2))
    lb        = int(cfg.get("regime_vwap_lookback", 5))
    crossings = _vwap_crossings(candles, vwap, lb)
    if crossings >= max_cross:
        return f"regime: {crossings} VWAP crossings in last {lb} (chop)"

    # (3) Session-cumulative chop gate — too many VWAP flips since the open
    #     marks a ranging DAY, not just a ranging patch. In the n=35 v1 study
    #     entries taken with ≥6 session crossings ran 22% WR (−₹9,529 net).
    #     Crossings only accumulate, so once tripped this kills the day.
    max_sess = int(cfg.get("session_max_vwap_crossings", 0))
    if max_sess > 0:
        sess_cross = _session_vwap_crossings(candles)
        if sess_cross >= max_sess:
            return f"regime: {sess_cross} VWAP crossings this session (chop day)"

    return ""


# ---------------------------------------------------------------------------
# Base entry — v1 VWAP+EMA breakout
# ---------------------------------------------------------------------------

def _base_breakout(
    candles: list[Candle],
    indicators: dict,
    cfg: dict,
) -> tuple[N2Signal, str, str]:
    """
    v1's exact breakout logic. Returns (signal, reason, skip_reason).
    signal is NO_SIGNAL when no breakout; skip_reason explains common-filter blocks.
    """
    if len(candles) < 3:
        return N2Signal.NO_SIGNAL, "", "insufficient candles"

    market_state = indicators.get("market_state", "UNKNOWN")
    if market_state == "SIDEWAYS":
        return N2Signal.NO_SIGNAL, "", "market is sideways"

    ema20        = indicators.get("ema20")
    ema20_series = indicators.get("ema20_series", [])
    vwap         = indicators.get("vwap") or 0.0
    rsi14        = indicators.get("rsi14")
    efficiency   = indicators.get("efficiency_ratio", 0.0)
    volume_surge = indicators.get("volume_surge", True)

    if ema20 is None:
        return N2Signal.NO_SIGNAL, "", "EMA20 not ready"

    current = candles[-1]
    prev    = candles[-2]

    if is_spike_candle(current):
        rng = (current.high - current.low) / current.close * 100 if current.close else 0
        return N2Signal.NO_SIGNAL, "", f"spike candle ({rng:.2f}% range)"

    rsi_min_ce        = cfg.get("rsi_min_ce", 50)
    rsi_max_ce        = cfg.get("rsi_max_ce", 100)
    rsi_min_pe        = cfg.get("rsi_min_pe", 0)
    rsi_max_pe        = cfg.get("rsi_max_pe", 50)
    vwap_dist_min     = cfg.get("vwap_dist_min_pct", 0.15)
    efficiency_min_ce = cfg.get("efficiency_min_ce", 0.45)
    efficiency_min_pe = cfg.get("efficiency_min_pe", 0.45)

    # ── Common filters ────────────────────────────────────────────────
    if not volume_surge:
        return N2Signal.NO_SIGNAL, "", "low volume — no participation"
    if not is_far_enough_from_vwap(current.close, vwap, min_pct=vwap_dist_min):
        return N2Signal.NO_SIGNAL, "", "too close to VWAP"

    # ── CE breakout ───────────────────────────────────────────────────
    ce_conditions = {
        "close > VWAP":          current.close > vwap,
        "EMA20 trending up":     ema_trending_up(ema20_series),
        "EMA20 slope strong":    ema_slope_strong_up(ema20_series),
        "strong bullish candle": is_strong_bullish(current),
        "breakout high":         current.high > prev.high,
        "2/3 candles bullish":   multi_candle_confirmation(candles, "bullish"),
        "RSI in range":          rsi14 is not None and rsi_min_ce <= rsi14 <= rsi_max_ce,
        "efficiency":            efficiency >= efficiency_min_ce,
    }
    if all(ce_conditions.values()):
        reason = (
            f"close={current.close:.1f} > VWAP={vwap:.1f} | "
            f"EMA20={ema20:.1f} up | RSI={rsi14:.1f} | "
            f"breakout: high {current.high:.1f} > {prev.high:.1f}"
        )
        return N2Signal.BUY_CE, reason, ""

    # ── PE breakout ───────────────────────────────────────────────────
    pe_conditions = {
        "close < VWAP":          current.close < vwap,
        "EMA20 trending down":   ema_trending_down(ema20_series),
        "EMA20 slope strong":    ema_slope_strong_down(ema20_series),
        "strong bearish candle": is_strong_bearish(current),
        "breakout low":          current.low < prev.low,
        "2/3 candles bearish":   multi_candle_confirmation(candles, "bearish"),
        "RSI in range":          rsi14 is not None and rsi_min_pe <= rsi14 <= rsi_max_pe,
        "efficiency":            efficiency >= efficiency_min_pe,
    }
    if all(pe_conditions.values()):
        reason = (
            f"close={current.close:.1f} < VWAP={vwap:.1f} | "
            f"EMA20={ema20:.1f} down | RSI={rsi14:.1f} | "
            f"breakout: low {current.low:.1f} < {prev.low:.1f}"
        )
        return N2Signal.BUY_PE, reason, ""

    return N2Signal.NO_SIGNAL, "", "no breakout"


# ---------------------------------------------------------------------------
# Top-level evaluator
# ---------------------------------------------------------------------------

def evaluate_signal(
    candles: list[Candle],
    indicators: dict,
    ctx: N2DayContext,
    cfg: dict,
    now: datetime,
) -> N2Setup:
    """
    Evaluate the base v1 breakout, then apply the regime-stability gate.
    Returns an N2Setup whose `base_signal` records what v1 alone would have
    fired (for shadow logging) and whose `signal` is the final decision.
    """
    if not candles:
        return N2Setup(skip_reason="no candles")

    base, reason, skip = _base_breakout(candles, indicators, cfg)
    if base == N2Signal.NO_SIGNAL:
        return N2Setup(skip_reason=skip)

    # A genuine v1 breakout fired — run it through the regime gate.
    vwap = indicators.get("vwap") or 0.0
    block = _regime_block_reason(candles, base, vwap, cfg)
    if block:
        return N2Setup(signal=N2Signal.NO_SIGNAL, base_signal=base,
                       skip_reason=block, reason=reason)

    return N2Setup(signal=base, base_signal=base, model=N2Model.V1, reason=reason)


# ---------------------------------------------------------------------------
# Opposite-signal exit detector (single-candle; engine adds the 2-close confirm)
# ---------------------------------------------------------------------------

def detect_opposite_signal_v1(
    candles: list[Candle],
    current_option_type: str,   # "CE" or "PE"
    vwap: float,
    ema20_series: list,
    market_state: str,
) -> bool:
    """
    True if a breakout OPPOSITE to the open position has formed on this candle.
    Mirrors v1's detect_opposite_signal. The engine only acts on this once the
    price has also closed on the wrong side of VWAP for N consecutive candles
    (opposite_exit_confirm_closes), which is the softened-exit improvement.
    """
    if len(candles) < 2 or market_state == "SIDEWAYS":
        return False

    current = candles[-1]
    prev = candles[-2]

    if current_option_type == "CE":
        return (
            current.close < vwap
            and ema_trending_down(ema20_series)
            and is_strong_bearish(current)
            and current.low < prev.low
        )
    else:
        return (
            current.close > vwap
            and ema_trending_up(ema20_series)
            and is_strong_bullish(current)
            and current.high > prev.high
        )
