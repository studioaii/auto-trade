"""
Strategy entry signals — three modes dispatched via `cfg["entry_mode"]`:

  - "vwap_ema_breakout" (legacy v2)         — `_generate_signal_legacy`
  - "trend_pullback"     (NIFTY v3)         — `generate_trend_pullback_signal`
  - "mean_reversion"     (BANKNIFTY v3)     — `generate_mean_reversion_signal`

Public entry point: `generate_signal(state, indicators, cfg, opening_rsi=None)`.

Per-instrument thresholds are read from cfg (INSTRUMENT_CONFIG in config.py).
Day-bias state is read from `state.day_bias` (set at 09:50 by the engine).
"""
import logging
from datetime import datetime, time
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from services.trading_state import Candle, TradingState
from services.indicators import (
    ema_trending_up, ema_trending_down,
    ema_slope_strong_up, ema_slope_strong_down,
    is_strong_bullish, is_strong_bearish,
    is_spike_candle, candle_range_pct, candle_body_pct,
    has_volume_surge, is_far_enough_from_vwap,
    multi_candle_confirmation,
    upper_wick_pct, lower_wick_pct,
    compute_ema, compute_efficiency,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN_READY = time(9, 50)   # skip first 30 min opening noise
LAST_ENTRY_TIME   = time(14, 0)   # no entries after 2 PM
FORCE_EXIT_TIME   = time(15, 20)  # legacy force-exit (cfg overrides in v3)


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


# ════════════════════════════════════════════════════════════════════════════
# Public dispatcher
# ════════════════════════════════════════════════════════════════════════════

def generate_signal(
    state: TradingState,
    indicators: dict,
    cfg: Optional[dict] = None,
    opening_rsi: Optional[float] = None,
) -> tuple[Signal, str]:
    """
    Dispatch to the correct entry-signal function based on cfg["entry_mode"].
    Returns (Signal, reason_string).
    """
    mode = cfg.get("entry_mode", "vwap_ema_breakout") if cfg else "vwap_ema_breakout"

    if mode == "trend_pullback":
        return generate_trend_pullback_signal(state, indicators, cfg, opening_rsi)
    if mode == "mean_reversion":
        return generate_mean_reversion_signal(state, indicators, cfg, opening_rsi)

    # Legacy fallback
    return _generate_signal_legacy(
        candles=state.candles,
        vwap=indicators.get("vwap", 0.0),
        ema20=indicators.get("ema20"),
        ema20_series=indicators.get("ema20_series", []),
        market_state=indicators.get("market_state", "UNKNOWN"),
        rsi14=indicators.get("rsi14"),
        volume_surge=indicators.get("volume_surge", True),
        efficiency=indicators.get("efficiency_ratio", 0.0),
        opening_rsi=opening_rsi,
        cfg=cfg,
    )


# ════════════════════════════════════════════════════════════════════════════
# Variant 1: Legacy VWAP+EMA breakout (v2)
# ════════════════════════════════════════════════════════════════════════════

def _generate_signal_legacy(
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
    """Original v2 VWAP+EMA breakout-continuation strategy. Unchanged."""
    if len(candles) < 3:
        return Signal.NO_SIGNAL, "insufficient candles"

    if market_state == "SIDEWAYS":
        return Signal.NO_SIGNAL, "market is sideways — skipping"

    if ema20 is None:
        return Signal.NO_SIGNAL, "EMA20 not ready"

    current = candles[-1]
    prev    = candles[-2]

    if is_spike_candle(current):
        logger.info("Spike candle detected (range %.2f%%) — skipping",
                    (current.high - current.low) / current.close * 100)
        return Signal.NO_SIGNAL, f"spike candle ({(current.high - current.low) / current.close * 100:.2f}% range)"

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

    if opening_rsi is not None:
        if opening_rsi > opening_rsi_ob or opening_rsi < opening_rsi_os:
            logger.info("Opening RSI %.1f in danger zone — all trades blocked for the day", opening_rsi)
            return Signal.NO_SIGNAL, f"opening RSI {opening_rsi:.1f} danger zone — day blocked"

    if not volume_surge:
        return Signal.NO_SIGNAL, "low volume — no institutional participation"

    if not is_far_enough_from_vwap(current.close, vwap, min_pct=vwap_dist_min):
        return Signal.NO_SIGNAL, "too close to VWAP"

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

    ce_failed = [k for k, v in ce_conditions.items() if not v]
    pe_failed = [k for k, v in pe_conditions.items() if not v]
    logger.info("No signal | CE failed: %s | PE failed: %s", ce_failed, pe_failed)

    return Signal.NO_SIGNAL, ""


# ════════════════════════════════════════════════════════════════════════════
# Variant 2: NIFTY trend-pullback continuation
# ════════════════════════════════════════════════════════════════════════════

def generate_trend_pullback_signal(
    state: TradingState,
    indicators: dict,
    cfg: Optional[dict] = None,
    opening_rsi: Optional[float] = None,
) -> tuple[Signal, str]:
    """
    Trend-pullback strategy for NIFTY.
    Enter on a green resume candle that follows a low-volume pullback into
    VWAP / 9-EMA — only when day-bias matches.
    """
    cfg = cfg or {}
    candles = state.candles
    if len(candles) < 22:
        return Signal.NO_SIGNAL, "insufficient candles"

    if not indicators.get("enough_data"):
        return Signal.NO_SIGNAL, "indicators not ready"

    bias = state.day_bias
    if bias == "NO_TRADE":
        return Signal.NO_SIGNAL, "DAY_BIAS_NO_TRADE"
    if bias not in ("UP", "DOWN"):
        return Signal.NO_SIGNAL, f"BIAS_NOT_DIRECTIONAL ({bias})"

    direction = "CE" if bias == "UP" else "PE"

    today_date = candles[-1].timestamp.date()
    today_candles = [c for c in candles if c.timestamp.date() == today_date]
    if len(today_candles) < 7:
        return Signal.NO_SIGNAL, "not enough today candles"

    current = today_candles[-1]
    vwap = indicators.get("vwap", 0.0)
    ema20 = indicators.get("ema20")
    if vwap <= 0 or ema20 is None:
        return Signal.NO_SIGNAL, "VWAP/EMA not ready"

    spike_threshold = float(cfg.get("spike_threshold_pct", 0.60))
    if is_spike_candle(current, spike_threshold):
        return Signal.NO_SIGNAL, f"spike candle ({candle_range_pct(current):.2f}% range)"
    if len(today_candles) >= 2:
        prev_today = today_candles[-2]
        if is_spike_candle(prev_today, spike_threshold):
            return Signal.NO_SIGNAL, "previous candle was spike"

    range_anomaly_mult = float(cfg.get("range_anomaly_mult", 1.5))
    if len(candles) >= 21:
        recent_ranges = [c.high - c.low for c in candles[-21:-1]]
        avg_range = sum(recent_ranges) / max(len(recent_ranges), 1)
        if avg_range > 0 and (current.high - current.low) > range_anomaly_mult * avg_range:
            return Signal.NO_SIGNAL, "range anomaly"

    # Pre-condition: spot has held above/below VWAP for ≥N candles
    hold_min = int(cfg.get("vwap_hold_min_candles", 6))
    if len(today_candles) >= hold_min:
        last_n = today_candles[-hold_min:]
        if direction == "CE" and not all(c.close > vwap for c in last_n):
            return Signal.NO_SIGNAL, "VWAP not held above"
        if direction == "PE" and not all(c.close < vwap for c in last_n):
            return Signal.NO_SIGNAL, "VWAP not held below"
    else:
        return Signal.NO_SIGNAL, "not enough candles for VWAP-hold check"

    # 9-EMA trend (today only — avoid seed contamination)
    ema_period = int(cfg.get("ema_period_secondary", 9))
    closes_today = [c.close for c in today_candles]
    ema9_series = compute_ema(closes_today, ema_period)
    ema9_now = ema9_series[-1] if ema9_series else None
    if ema9_now is None:
        return Signal.NO_SIGNAL, "9-EMA not ready"
    last3 = [v for v in ema9_series[-3:] if v is not None]
    if len(last3) >= 2:
        if direction == "CE" and not (last3[-1] > last3[-2]):
            return Signal.NO_SIGNAL, "9-EMA not rising"
        if direction == "PE" and not (last3[-1] < last3[-2]):
            return Signal.NO_SIGNAL, "9-EMA not falling"

    # Detect pullback window in last 6 candles
    pb_window = _find_pullback_window(today_candles, direction)
    if pb_window is None:
        return Signal.NO_SIGNAL, "no pullback found"

    # Pullback structural checks
    pullback_retrace_pct = float(cfg.get("pullback_retrace_pct", 0.20))
    rsi_low = float(cfg.get("pullback_rsi_low", 45))
    rsi_high = float(cfg.get("pullback_rsi_high", 55))
    pb_vol_max = float(cfg.get("pullback_vol_max_ratio", 0.85))

    pb_min_close = min(c.close for c in pb_window)
    pb_max_close = max(c.close for c in pb_window)
    session_low = state.session_low or min(c.low for c in today_candles)
    session_high = state.session_high or max(c.high for c in today_candles)

    if direction == "CE":
        # pullback must not break day low
        pb_low = min(c.low for c in pb_window)
        if pb_low <= session_low:
            return Signal.NO_SIGNAL, "pullback broke day low"
        # bottom within retrace_pct of VWAP or 9-EMA
        ref_above = max(vwap, ema9_now)
        retrace = abs(pb_min_close - ref_above) / ref_above * 100
        if retrace > pullback_retrace_pct:
            return Signal.NO_SIGNAL, f"pullback didn't reach VWAP/EMA ({retrace:.2f}% > {pullback_retrace_pct}%)"
    else:
        pb_high = max(c.high for c in pb_window)
        if pb_high >= session_high:
            return Signal.NO_SIGNAL, "pullback broke day high"
        ref_below = min(vwap, ema9_now)
        retrace = abs(pb_max_close - ref_below) / ref_below * 100
        if retrace > pullback_retrace_pct:
            return Signal.NO_SIGNAL, f"pullback didn't reach VWAP/EMA ({retrace:.2f}% > {pullback_retrace_pct}%)"

    # RSI on pullback bottom in healthy zone
    rsi14_series = indicators.get("rsi14_series", [])
    pb_idx_in_today = (
        len(today_candles) - 1 - (today_candles[::-1].index(pb_window[-1]))
    )
    seed_offset = len(candles) - len(today_candles)
    pb_global_idx = seed_offset + pb_idx_in_today
    rsi_at_pullback = (
        rsi14_series[pb_global_idx] if 0 <= pb_global_idx < len(rsi14_series) else None
    )
    if rsi_at_pullback is None or not (rsi_low <= rsi_at_pullback <= rsi_high):
        return Signal.NO_SIGNAL, f"pullback RSI not in [{rsi_low},{rsi_high}] (got {rsi_at_pullback})"

    # Pullback volume — should be lower than the trend candles before it
    if len(today_candles) >= len(pb_window) + 5:
        pb_vols = [c.volume for c in pb_window]
        idx_pb_start = today_candles.index(pb_window[0])
        prev_trend = today_candles[max(0, idx_pb_start - 5):idx_pb_start]
        if prev_trend:
            avg_prev = sum(c.volume for c in prev_trend) / len(prev_trend)
            avg_pb = sum(pb_vols) / len(pb_vols)
            if avg_prev > 0 and avg_pb / avg_prev > pb_vol_max:
                return Signal.NO_SIGNAL, f"pullback volume too high ({avg_pb/avg_prev:.2f}× > {pb_vol_max}×)"

    # Resume-candle trigger conditions
    body_min = float(cfg.get("resume_body_pct", 50))
    vol_surge_ratio = float(cfg.get("resume_vol_surge_ratio", 1.5))
    vwap_dist_min = float(cfg.get("resume_vwap_dist_min", 0.20))
    vwap_dist_max = float(cfg.get("resume_vwap_dist_max", 0.80))
    ema_gap_min = float(cfg.get("ema_gap_min", 0.05))
    ema_gap_max = float(cfg.get("ema_gap_max", 0.40))
    rsi14 = indicators.get("rsi14")

    if direction == "CE":
        rsi_min_resume = float(cfg.get("resume_rsi_min_ce", 55))
        rsi_max_resume = float(cfg.get("resume_rsi_max_ce", 70))
        if not (current.close > current.open):
            return Signal.NO_SIGNAL, "resume not green"
        pb_high = max(c.high for c in pb_window)
        if not (current.close > pb_high):
            return Signal.NO_SIGNAL, "didn't clear pullback high"
        ema_gap_pct = (current.close - ema9_now) / ema9_now * 100
        vwap_dist = (current.close - vwap) / vwap * 100
    else:
        rsi_min_resume = float(cfg.get("resume_rsi_min_pe", 30))
        rsi_max_resume = float(cfg.get("resume_rsi_max_pe", 45))
        if not (current.close < current.open):
            return Signal.NO_SIGNAL, "resume not red"
        pb_low = min(c.low for c in pb_window)
        if not (current.close < pb_low):
            return Signal.NO_SIGNAL, "didn't clear pullback low"
        ema_gap_pct = (ema9_now - current.close) / ema9_now * 100
        vwap_dist = (vwap - current.close) / vwap * 100

    resume_conditions = {
        "body":         candle_body_pct(current) >= body_min,
        "volume":       has_volume_surge(candles, ratio=vol_surge_ratio),
        "rsi range":    rsi14 is not None and rsi_min_resume <= rsi14 <= rsi_max_resume,
        "vwap-dist":    vwap_dist_min <= vwap_dist <= vwap_dist_max,
        "ema-gap":      ema_gap_min <= ema_gap_pct <= ema_gap_max,
    }
    if not all(resume_conditions.values()):
        failed = [k for k, v in resume_conditions.items() if not v]
        return Signal.NO_SIGNAL, f"resume conditions failed: {failed}"

    sig = Signal.BUY_CE if direction == "CE" else Signal.BUY_PE
    reason = (
        f"trend_pullback {direction} | bias={bias} | "
        f"pullback={len(pb_window)}c rsi_pb={rsi_at_pullback:.1f} | "
        f"resume close={current.close:.1f} body={candle_body_pct(current):.1f}% "
        f"rsi={rsi14:.1f} vwap_dist={vwap_dist:.2f}% ema_gap={ema_gap_pct:.2f}%"
    )
    logger.info("%s signal | %s", sig.value, reason)
    return sig, reason


def _find_pullback_window(today_candles: list[Candle], direction: str) -> Optional[list[Candle]]:
    """
    Find the most recent contiguous run (1-3 candles) AGAINST the bias
    direction, ending within the last 6 candles.
    Returns the run (oldest first) or None.
    """
    if len(today_candles) < 4:
        return None
    # Skip the most recent (resume-trigger candidate) when looking for pullback
    lookback = today_candles[-7:-1] if len(today_candles) >= 7 else today_candles[:-1]
    if not lookback:
        return None
    against_dir = (lambda c: c.close < c.open) if direction == "CE" else (lambda c: c.close > c.open)

    # Scan from the end backwards, find the contiguous "against" run nearest to the resume
    run: list[Candle] = []
    for c in reversed(lookback):
        if against_dir(c):
            run.insert(0, c)
            if len(run) >= 3:
                break
        else:
            if run:
                break

    if not (1 <= len(run) <= 3):
        return None
    return run


# ════════════════════════════════════════════════════════════════════════════
# Variant 3: BANKNIFTY mean-reversion / fade-failed-spike
# ════════════════════════════════════════════════════════════════════════════

def generate_mean_reversion_signal(
    state: TradingState,
    indicators: dict,
    cfg: Optional[dict] = None,
    opening_rsi: Optional[float] = None,
) -> tuple[Signal, str]:
    """
    Mean-reversion strategy for BANKNIFTY.
    Detect a spike (3-candle window of ≥0.6% from VWAP, RSI extreme),
    then enter the OPPOSITE option on the next failure-rejection candle.
    """
    cfg = cfg or {}
    candles = state.candles
    if len(candles) < 22:
        return Signal.NO_SIGNAL, "insufficient candles"
    if not indicators.get("enough_data"):
        return Signal.NO_SIGNAL, "indicators not ready"

    if state.day_bias == "NO_TRADE":
        return Signal.NO_SIGNAL, "DAY_BIAS_NO_TRADE"

    failed_max = int(cfg.get("failed_reversion_max", 2))
    if state.failed_reversion_attempts_today >= failed_max:
        return Signal.NO_SIGNAL, "failed_reversion_max reached"

    today_date = candles[-1].timestamp.date()
    today_candles = [c for c in candles if c.timestamp.date() == today_date]
    spike_window_n = int(cfg.get("bnf_spike_window_candles", 3))
    if len(today_candles) < spike_window_n + 1:
        return Signal.NO_SIGNAL, "not enough today candles"

    vwap = indicators.get("vwap", 0.0)
    if vwap <= 0:
        return Signal.NO_SIGNAL, "VWAP not ready"

    spike_pct_min = float(cfg.get("bnf_spike_pct", 0.60))
    spike_vol_surge = float(cfg.get("bnf_spike_vol_surge", 2.5))
    rsi_overbought = float(cfg.get("bnf_fade_rsi_overbought", 70))
    rsi_oversold = float(cfg.get("bnf_fade_rsi_oversold", 30))
    wick_min = float(cfg.get("fade_wick_min_pct", 40))
    body_min = float(cfg.get("fade_body_min_pct", 40))

    # Trigger candle = current; spike window = the 3 candles BEFORE the trigger
    trigger = today_candles[-1]
    spike_window = today_candles[-1 - spike_window_n:-1]
    if len(spike_window) < spike_window_n:
        return Signal.NO_SIGNAL, "spike window incomplete"

    rsi14_series = indicators.get("rsi14_series", [])
    if not rsi14_series:
        return Signal.NO_SIGNAL, "RSI series unavailable"
    rsi_trigger_idx = len(candles) - 1
    rsi_spike_end_idx = rsi_trigger_idx - 1
    rsi_trigger = rsi14_series[rsi_trigger_idx] if rsi_trigger_idx < len(rsi14_series) else None
    rsi_spike_end = (
        rsi14_series[rsi_spike_end_idx] if rsi_spike_end_idx < len(rsi14_series) else None
    )
    if rsi_trigger is None or rsi_spike_end is None:
        return Signal.NO_SIGNAL, "RSI not ready at trigger/spike"

    win_close = spike_window[-1].close
    win_low = min(c.low for c in spike_window)
    win_high = max(c.high for c in spike_window)
    dist_above_pct = (win_close - vwap) / vwap * 100 if vwap > 0 else 0
    dist_below_pct = (vwap - win_close) / vwap * 100 if vwap > 0 else 0

    bullish_spike_ok = (
        dist_above_pct >= spike_pct_min
        and win_low > vwap
        and rsi_spike_end >= rsi_overbought
        and has_volume_surge(candles, ratio=spike_vol_surge)
    )
    bearish_spike_ok = (
        dist_below_pct >= spike_pct_min
        and win_high < vwap
        and rsi_spike_end <= rsi_oversold
        and has_volume_surge(candles, ratio=spike_vol_surge)
    )

    if not (bullish_spike_ok or bearish_spike_ok):
        return Signal.NO_SIGNAL, "no qualifying spike"

    session_high = state.session_high or max(c.high for c in today_candles)
    session_low = state.session_low or min(c.low for c in today_candles)

    # Bullish spike → fade with PE
    if bullish_spike_ok:
        # Exclusion: spike broke today's high AND held there for 2 subsequent candles
        # (we only have 1 candle past spike_window — the trigger — so just check trigger)
        if win_high >= session_high * 1.0001:
            # spike made new day high; need failure (which we are about to evaluate)
            pass
        upper_w = upper_wick_pct(trigger)
        body = candle_body_pct(trigger)
        cond = {
            "upper_wick":    upper_w >= wick_min,
            "bearish close": trigger.close < trigger.open,
            "body":          body >= body_min,
            "fail_high":     trigger.high <= win_high,
            "rsi_rolls_dn":  rsi_trigger < rsi_spike_end,
        }
        if not all(cond.values()):
            failed = [k for k, v in cond.items() if not v]
            return Signal.NO_SIGNAL, f"bullish spike — fade failed: {failed}"
        reason = (
            f"mean_rev PE-fade | bullish spike d={dist_above_pct:.2f}% rsi={rsi_spike_end:.1f} | "
            f"trigger upper_wick={upper_w:.0f}% body={body:.0f}% rsi={rsi_trigger:.1f}"
        )
        logger.info("BUY_PE signal (mean_rev) | %s", reason)
        return Signal.BUY_PE, reason

    # Bearish spike → fade with CE
    if bearish_spike_ok:
        lower_w = lower_wick_pct(trigger)
        body = candle_body_pct(trigger)
        cond = {
            "lower_wick":    lower_w >= wick_min,
            "bullish close": trigger.close > trigger.open,
            "body":          body >= body_min,
            "fail_low":      trigger.low >= win_low,
            "rsi_rolls_up":  rsi_trigger > rsi_spike_end,
        }
        if not all(cond.values()):
            failed = [k for k, v in cond.items() if not v]
            return Signal.NO_SIGNAL, f"bearish spike — fade failed: {failed}"
        reason = (
            f"mean_rev CE-fade | bearish spike d={dist_below_pct:.2f}% rsi={rsi_spike_end:.1f} | "
            f"trigger lower_wick={lower_w:.0f}% body={body:.0f}% rsi={rsi_trigger:.1f}"
        )
        logger.info("BUY_CE signal (mean_rev) | %s", reason)
        return Signal.BUY_CE, reason

    return Signal.NO_SIGNAL, ""


# ════════════════════════════════════════════════════════════════════════════
# Opposite-signal exit detector (legacy + variants)
# ════════════════════════════════════════════════════════════════════════════

def detect_opposite_signal(
    candles: list[Candle],
    current_option_type: str,   # "CE" or "PE"
    vwap: float,
    ema20_series: list,
    market_state: str,
) -> bool:
    """Return True if a signal opposite to the open position has formed."""
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
