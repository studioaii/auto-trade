"""
BankNifty 2.0 — High-Precision Entry Strategy.

Four entry models, each numeric-rule-based:

  Model A — Compression breakout (anticipation, LIMIT order)
  Model B — First pullback after a v1-style breakout (MARKET on resume)
  Model C — Liquidity-sweep reversal (V-bottom / V-top)
  Model D — Flag continuation (post-impulse, MARKET on flag break)

DO-NOT-ENTER and HIGH-QUALITY-ENTRY checklists gate every candidate
before the model-specific logic. Day classification (TREND / REVERSAL /
CHOP / NORMAL) further restricts which models may fire.

This module is pure functions — no I/O, no state mutations. The engine
calls these once per closed candle. The setup-tracking state lives in
the engine's own dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time, datetime
from enum import Enum
from typing import Optional

from services.trading_state import Candle
from services.indicators import (
    candle_body_pct,
    has_volume_surge,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class V2Signal(str, Enum):
    BUY_CE    = "BUY_CE"
    BUY_PE    = "BUY_PE"
    NO_SIGNAL = "NO_SIGNAL"


class V2Model(str, Enum):
    NONE = "NONE"
    A    = "A_COMPRESSION_BREAKOUT"
    B    = "B_FIRST_PULLBACK"
    C    = "C_LIQUIDITY_SWEEP"
    D    = "D_FLAG_CONTINUATION"


class DayClass(str, Enum):
    UNKNOWN  = "UNKNOWN"
    TREND    = "TREND_DAY"
    REVERSAL = "REVERSAL_DAY"
    CHOP     = "CHOP_DAY"
    NORMAL   = "NORMAL_DAY"


# ---------------------------------------------------------------------------
# Per-candle volume ratio (uses prior 10 candles as denominator)
# ---------------------------------------------------------------------------

def volume_ratio(candles: list[Candle]) -> float:
    """current_volume / mean(prior 10 volumes). 1.0 if data insufficient/uniform."""
    if len(candles) < 11:
        return 1.0
    recent = [c.volume for c in candles[-11:-1]]
    avg = sum(recent) / len(recent)
    if avg <= 0 or all(v == recent[0] for v in recent):
        return 1.0
    return candles[-1].volume / avg


def volume_ratio_at(candles: list[Candle], idx: int) -> float:
    """Volume ratio at a specific candle index (idx is positive offset from start)."""
    if idx < 10 or idx >= len(candles):
        return 1.0
    window = [c.volume for c in candles[idx - 10:idx]]
    avg = sum(window) / len(window)
    if avg <= 0 or all(v == window[0] for v in window):
        return 1.0
    return candles[idx].volume / avg


# ---------------------------------------------------------------------------
# Setup descriptor — what the strategy proposes to the engine
# ---------------------------------------------------------------------------

@dataclass
class V2Setup:
    """Outcome of evaluating the strategy on the latest candle."""
    signal: V2Signal = V2Signal.NO_SIGNAL
    model: V2Model = V2Model.NONE
    reason: str = ""                              # human-readable, logged to CSV
    structure_sl_spot: Optional[float] = None     # spot price at which SL is breached
    entry_method: str = "MARKET"                  # "MARKET" or "LIMIT"
    limit_price_spot: Optional[float] = None      # for LIMIT entries
    skip_reason: str = ""                         # only populated when signal=NO_SIGNAL


@dataclass
class PendingLimitOrder:
    """A Model-A limit order awaiting trigger."""
    signal: V2Signal
    trigger_spot: float          # CE: trigger when spot ≥ this; PE: spot ≤ this
    structure_sl_spot: float
    reason: str
    placed_at: datetime
    candles_alive: int = 0


# ---------------------------------------------------------------------------
# Day classifier
# ---------------------------------------------------------------------------

@dataclass
class DayContext:
    """Mutated by the engine across the session — passed read-only into strategy fns."""
    prev_close: Optional[float] = None            # provided by engine at start
    today_open: Optional[float] = None            # first candle's open
    or_high: Optional[float] = None               # opening-range high (09:15–09:45)
    or_low:  Optional[float] = None               # opening-range low
    gap_pct: Optional[float] = None               # computed once at 09:15
    vwap_drift_at_950: Optional[float] = None     # computed at 09:50
    day_class: DayClass = DayClass.UNKNOWN
    classified_at: Optional[datetime] = None
    # Running peak/trough tracker for "kth leg" detection
    consecutive_up_candles: int = 0
    consecutive_dn_candles: int = 0


def compute_opening_range(candles_today: list[Candle], cutoff_hhmm: tuple[int, int] = (9, 45)) -> tuple[Optional[float], Optional[float]]:
    """Highest high and lowest low across candles before cutoff (inclusive of 09:15–09:45)."""
    if not candles_today:
        return None, None
    cutoff = time(cutoff_hhmm[0], cutoff_hhmm[1])
    early = [c for c in candles_today if c.timestamp.time() <= cutoff]
    if not early:
        return None, None
    return max(c.high for c in early), min(c.low for c in early)


def classify_day(
    ctx: DayContext,
    candles_today: list[Candle],
    vwap: float,
    cfg: dict,
) -> DayClass:
    """
    Called once shortly after 09:50. Returns and caches a DayClass on ctx.
    Hourly re-evaluation can downgrade to CHOP via reclassify_chop().
    """
    if not candles_today or not ctx.prev_close or vwap <= 0:
        return DayClass.UNKNOWN

    if ctx.today_open is None:
        ctx.today_open = candles_today[0].open

    if ctx.gap_pct is None:
        ctx.gap_pct = (ctx.today_open - ctx.prev_close) / ctx.prev_close * 100.0

    or_h, or_l = compute_opening_range(candles_today, cfg.get("observe_until_hhmm", (9, 45)))
    if or_h is not None:
        ctx.or_high = or_h
    if or_l is not None:
        ctx.or_low = or_l

    or_pts = (ctx.or_high - ctx.or_low) if (ctx.or_high and ctx.or_low) else 0.0
    current_close = candles_today[-1].close
    ctx.vwap_drift_at_950 = (current_close - vwap) / vwap * 100.0

    gap_abs = abs(ctx.gap_pct)
    drift_abs = abs(ctx.vwap_drift_at_950)
    gap_min     = cfg.get("day_class_gap_min_pct", 0.30)
    drift_min   = cfg.get("day_class_drift_min_pct", 0.25)
    or_min      = cfg.get("day_class_or_min_pts", 150)
    rev_or_min  = cfg.get("day_class_reversal_or_pts", 200)

    # REVERSAL: gap was big but VWAP has drifted opposite
    if gap_abs >= gap_min and or_pts >= rev_or_min:
        if (ctx.gap_pct > 0 and ctx.vwap_drift_at_950 < 0) or (ctx.gap_pct < 0 and ctx.vwap_drift_at_950 > 0):
            ctx.day_class = DayClass.REVERSAL
            return ctx.day_class

    # TREND: gap big AND drift big AND OR adequate
    if gap_abs >= gap_min and drift_abs >= drift_min and or_pts >= or_min:
        ctx.day_class = DayClass.TREND
        return ctx.day_class

    # CHOP: tight OR OR drift near zero
    if or_pts < 120 or drift_abs < 0.10:
        ctx.day_class = DayClass.CHOP
        return ctx.day_class

    ctx.day_class = DayClass.NORMAL
    return ctx.day_class


def reclassify_chop_if_dead(
    ctx: DayContext,
    candles_today: list[Candle],
    vwap: float,
    minutes_since_last_check: int,
) -> bool:
    """
    If 60 min passed without an 80-pt candle AND drift hasn't shifted 0.15%,
    downgrade to CHOP. Returns True if downgrade applied.
    """
    if ctx.day_class == DayClass.CHOP:
        return False
    if len(candles_today) < 12 or minutes_since_last_check < 60:
        return False
    last_hour = candles_today[-12:]
    max_range = max((c.high - c.low) for c in last_hour)
    if max_range >= 80:
        return False
    cur_drift = (candles_today[-1].close - vwap) / vwap * 100.0 if vwap > 0 else 0
    if ctx.vwap_drift_at_950 is None:
        return False
    if abs(cur_drift - ctx.vwap_drift_at_950) >= 0.15:
        return False
    ctx.day_class = DayClass.CHOP
    return True


# ---------------------------------------------------------------------------
# DO-NOT-ENTER & HIGH-QUALITY checklists
# ---------------------------------------------------------------------------

def do_not_enter_reasons(
    candles: list[Candle],
    indicators: dict,
    ctx: DayContext,
    cfg: dict,
    now: datetime,
    side: V2Signal,
) -> list[str]:
    """
    Return a list of DO-NOT-ENTER reasons that match. Empty list = clear to proceed.
    Caller still needs to evaluate HIGH-QUALITY threshold separately.
    """
    reasons: list[str] = []
    if not candles:
        return ["no candles"]
    current = candles[-1]
    body = candle_body_pct(current)
    vol_r = volume_ratio(candles)
    rsi   = indicators.get("rsi14") or 0.0
    vwap  = indicators.get("vwap") or 0.0
    close = current.close

    # 1. Climax body
    if body > cfg.get("max_body_pct", 85.0):
        reasons.append(f"climax body {body:.1f}%")

    # 2. Climactic volume
    if vol_r > cfg.get("max_vol_ratio", 3.0):
        reasons.append(f"climactic vol {vol_r:.2f}x")

    # 3. Fourth-or-later leg in same direction
    max_legs = cfg.get("max_consecutive_same_dir", 3)
    if side == V2Signal.BUY_CE and ctx.consecutive_up_candles >= max_legs:
        reasons.append(f"{ctx.consecutive_up_candles}th leg up")
    if side == V2Signal.BUY_PE and ctx.consecutive_dn_candles >= max_legs:
        reasons.append(f"{ctx.consecutive_dn_candles}th leg down")

    # 4. RSI exhaustion zone
    if side == V2Signal.BUY_CE and rsi > cfg.get("rsi_max_ce_entry", 68.0):
        reasons.append(f"RSI {rsi:.1f} > {cfg.get('rsi_max_ce_entry', 68.0)}")
    if side == V2Signal.BUY_PE and rsi < cfg.get("rsi_min_pe_entry", 32.0):
        reasons.append(f"RSI {rsi:.1f} < {cfg.get('rsi_min_pe_entry', 32.0)}")

    # 5. VWAP whipsaw band
    if vwap > 0:
        dist_pct = abs(close - vwap) / vwap * 100
        if dist_pct < cfg.get("vwap_whipsaw_band_pct", 0.10):
            reasons.append(f"VWAP whipsaw ({dist_pct:.2f}%)")

    # 6. Dead-market session range
    today = current.timestamp.date()
    today_candles = [c for c in candles if c.timestamp.date() == today]
    if today_candles and now.time() >= time(11, 0):
        rng = max(c.high for c in today_candles) - min(c.low for c in today_candles)
        if rng < cfg.get("min_session_range_pts", 150):
            reasons.append(f"dead market ({rng:.0f}pt range)")

    # 7. After 14:00 hard block
    if now.time() >= time(*cfg.get("caution_window_end", (14, 0))):
        reasons.append("past last-entry time")

    return reasons


def high_quality_score(
    candles: list[Candle],
    indicators: dict,
    ctx: DayContext,
    cfg: dict,
    now: datetime,
    side: V2Signal,
    model: V2Model,
) -> tuple[int, list[str]]:
    """
    Score the entry against the 10 HIGH-QUALITY criteria. Need ≥7 to pass.
    Returns (count_passed, list_of_failed_criteria).
    """
    if not candles:
        return 0, ["no candles"]
    current = candles[-1]
    body = candle_body_pct(current)
    vol_r = volume_ratio(candles)
    rsi   = indicators.get("rsi14") or 50.0
    vwap  = indicators.get("vwap") or 0.0
    close = current.close
    ema_series = indicators.get("ema20_series", [])
    ema_vals = [v for v in ema_series[-6:] if v is not None]

    failed: list[str] = []
    score = 0

    # 1. Setup matches a named model
    if model != V2Model.NONE:
        score += 1
    else:
        failed.append("no model match")

    # 2. Body 50–85%
    if 50.0 <= body <= 85.0:
        score += 1
    else:
        failed.append(f"body {body:.1f}% not in 50–85")

    # 3. Vol ratio 1.3–2.5×
    if 1.3 <= vol_r <= 2.5:
        score += 1
    else:
        failed.append(f"vol {vol_r:.2f}x not in 1.3–2.5")

    # 4. RSI in zone
    if side == V2Signal.BUY_CE and 45.0 <= rsi <= 65.0:
        score += 1
    elif side == V2Signal.BUY_PE and 35.0 <= rsi <= 55.0:
        score += 1
    else:
        failed.append(f"RSI {rsi:.1f} out of zone")

    # 5. VWAP distance 0.20–0.80%
    if vwap > 0:
        dist = abs(close - vwap) / vwap * 100
        if 0.20 <= dist <= 0.80:
            score += 1
        else:
            failed.append(f"VWAP dist {dist:.2f}%")
    else:
        failed.append("no VWAP")

    # 6. EMA slope aligned (≥15 pts over 5)
    if len(ema_vals) >= 2:
        slope = ema_vals[-1] - ema_vals[0]
        if side == V2Signal.BUY_CE and slope >= 15:
            score += 1
        elif side == V2Signal.BUY_PE and slope <= -15:
            score += 1
        else:
            failed.append(f"EMA slope {slope:.1f}")
    else:
        failed.append("no EMA slope")

    # 7. Multi-candle confirmation (2 of last 3)
    if len(candles) >= 3:
        last3 = candles[-3:]
        if side == V2Signal.BUY_CE:
            confirm = sum(1 for c in last3 if c.close > c.open)
        else:
            confirm = sum(1 for c in last3 if c.close < c.open)
        if confirm >= 2:
            score += 1
        else:
            failed.append(f"multi-candle {confirm}/3")
    else:
        failed.append("not enough candles")

    # 8. Time window 09:50–13:30
    if time(9, 50) <= now.time() <= time(13, 30):
        score += 1
    else:
        failed.append(f"time {now.time().strftime('%H:%M')}")

    # 9. Not into prior major S/R (basic: ensure breakout actually clears prior swing by margin)
    margin_min = cfg.get("min_breakout_margin_pts", 50)
    if len(candles) >= 6:
        prior_swing_h = max(c.high for c in candles[-6:-1])
        prior_swing_l = min(c.low  for c in candles[-6:-1])
        if side == V2Signal.BUY_CE and current.high - prior_swing_h >= margin_min:
            score += 1
        elif side == V2Signal.BUY_PE and prior_swing_l - current.low >= margin_min:
            score += 1
        else:
            failed.append("thin breakout margin")
    else:
        failed.append("not enough swing data")

    # 10. Day-bias agrees: gap or drift confirms direction
    bias_ok = False
    if ctx.gap_pct is not None:
        if side == V2Signal.BUY_CE and ctx.gap_pct >= 0:
            bias_ok = True
        if side == V2Signal.BUY_PE and ctx.gap_pct <= 0:
            bias_ok = True
    if ctx.vwap_drift_at_950 is not None:
        if side == V2Signal.BUY_CE and ctx.vwap_drift_at_950 > 0:
            bias_ok = True
        if side == V2Signal.BUY_PE and ctx.vwap_drift_at_950 < 0:
            bias_ok = True
    if bias_ok:
        score += 1
    else:
        failed.append("day bias disagrees")

    return score, failed


# ---------------------------------------------------------------------------
# Model evaluators
# ---------------------------------------------------------------------------

def evaluate_model_a_setup(
    candles: list[Candle],
    indicators: dict,
    cfg: dict,
    now: datetime,
) -> Optional[PendingLimitOrder]:
    """
    Detect a compression range that may break out. Returns a PendingLimitOrder
    when the setup is valid; the engine places a LIMIT order at trigger_spot.
    """
    lookback = cfg.get("model_a_lookback", 4)
    if len(candles) < lookback + 1:
        return None
    if not time(*cfg.get("entry_window_start", (9, 50))) <= now.time() <= time(12, 30):
        return None

    recent = candles[-lookback:]
    rng_pts = max(c.high for c in recent) - min(c.low for c in recent)
    if rng_pts > cfg.get("model_a_max_range_pts", 80):
        return None

    # All bodies modest (no expansion yet)
    for c in recent:
        if candle_body_pct(c) > cfg.get("model_a_max_body_pct", 50.0):
            return None

    # Volume staying low
    vol_max = cfg.get("model_a_max_vol_ratio_any", 1.0)
    vol_avg_max = cfg.get("model_a_max_vol_ratio_avg", 0.85)
    vol_ratios = []
    base_idx = len(candles) - lookback
    for offset in range(lookback):
        vr = volume_ratio_at(candles, base_idx + offset)
        if vr > vol_max:
            return None
        vol_ratios.append(vr)
    if sum(vol_ratios) / len(vol_ratios) > vol_avg_max:
        return None

    rsi = indicators.get("rsi14") or 0.0
    if not (cfg.get("model_a_rsi_min", 45.0) <= rsi <= cfg.get("model_a_rsi_max", 60.0)):
        return None

    vwap = indicators.get("vwap") or 0.0
    if vwap <= 0:
        return None

    rng_high = max(c.high for c in recent)
    rng_low  = min(c.low  for c in recent)
    bias_pct = cfg.get("model_a_vwap_bias_pct", 0.10)
    offset = cfg.get("model_a_break_offset_pts", 8)

    # CE bias: range top is meaningfully above VWAP
    if rng_high >= vwap * (1 + bias_pct / 100.0):
        return PendingLimitOrder(
            signal=V2Signal.BUY_CE,
            trigger_spot=rng_high + offset,
            structure_sl_spot=rng_low - 5,
            reason=f"Model A | compression {rng_pts:.0f}pts | breakout >{rng_high + offset:.0f}",
            placed_at=now,
        )
    if rng_low <= vwap * (1 - bias_pct / 100.0):
        return PendingLimitOrder(
            signal=V2Signal.BUY_PE,
            trigger_spot=rng_low - offset,
            structure_sl_spot=rng_high + 5,
            reason=f"Model A | compression {rng_pts:.0f}pts | breakdown <{rng_low - offset:.0f}",
            placed_at=now,
        )
    return None


def maybe_fire_model_a(
    pending: PendingLimitOrder,
    current_spot: float,
    current_candle: Candle,
    candles: list[Candle],
    cfg: dict,
) -> Optional[V2Setup]:
    """
    Check whether the pending Model-A limit order has been triggered.
    Triggered when spot crosses trigger_spot AND the candle's volume confirms.
    """
    if pending.signal == V2Signal.BUY_CE:
        if current_spot < pending.trigger_spot:
            return None
        if current_candle.high < pending.trigger_spot:
            return None
    else:
        if current_spot > pending.trigger_spot:
            return None
        if current_candle.low > pending.trigger_spot:
            return None

    body = candle_body_pct(current_candle)
    if body >= 90.0:
        # Climax bar — cancel; wait for Model B pullback instead
        return None

    vr = volume_ratio(candles)
    if vr < cfg.get("model_a_min_break_vol_ratio", 1.3):
        return None

    return V2Setup(
        signal=pending.signal,
        model=V2Model.A,
        reason=pending.reason,
        structure_sl_spot=pending.structure_sl_spot,
        entry_method="LIMIT",
        limit_price_spot=pending.trigger_spot,
    )


def evaluate_model_b(
    candles: list[Candle],
    indicators: dict,
    cfg: dict,
    now: datetime,
) -> Optional[V2Setup]:
    """
    First-pullback re-entry. We look 2–4 candles back for a 'signal candle'
    (body 55–85%, vol 1.3–2.5x), then verify the in-between candles formed
    a shallow pullback that just resumed direction on the latest candle.
    """
    if len(candles) < 6:
        return None
    if not time(*cfg.get("entry_window_start", (9, 50))) <= now.time() <= time(*cfg.get("caution_window_end", (14, 0))):
        return None

    current = candles[-1]
    body_min = cfg.get("model_b_signal_body_min", 55.0)
    body_max = cfg.get("model_b_signal_body_max", 85.0)
    vol_min  = cfg.get("model_b_signal_vol_min", 1.3)
    vol_max  = cfg.get("model_b_signal_vol_max", 2.5)
    max_pull_candles = cfg.get("model_b_max_pullback_candles", 3)
    max_pull_pct     = cfg.get("model_b_max_pullback_pct", 60.0)
    ema_slope_min    = cfg.get("model_b_ema_slope_min_pts", 15)

    ema_series = indicators.get("ema20_series", [])
    ema_vals = [v for v in ema_series[-6:] if v is not None]
    if len(ema_vals) < 2:
        return None
    ema_slope = ema_vals[-1] - ema_vals[0]

    vwap = indicators.get("vwap") or 0.0
    if vwap <= 0:
        return None

    # Look for signal candle at index n-k where 2 ≤ k ≤ max_pull_candles+1
    for k in range(2, max_pull_candles + 2):
        if len(candles) < k + 1:
            break
        signal_candle = candles[-k - 1]
        body_s = candle_body_pct(signal_candle)
        vr_s   = volume_ratio_at(candles, len(candles) - k - 1)
        if not (body_min <= body_s <= body_max):
            continue
        if not (vol_min <= vr_s <= vol_max):
            continue

        # Direction inferred from signal candle
        bullish_signal = signal_candle.close > signal_candle.open
        bearish_signal = signal_candle.close < signal_candle.open
        if not (bullish_signal or bearish_signal):
            continue

        # Pullback candles between signal and current (exclusive of both)
        pull_candles = candles[-k:-1]
        if not pull_candles:
            continue

        # Pullback must hold above VWAP (CE) / below VWAP (PE)
        if bullish_signal:
            if any(c.close < vwap for c in pull_candles):
                continue
            if any(c.volume >= signal_candle.volume for c in pull_candles):
                continue
            # Pullback low must not exceed 60% retrace of signal candle range
            sig_range = signal_candle.high - signal_candle.low
            if sig_range <= 0:
                continue
            pull_low = min(c.low for c in pull_candles)
            retr_pct = (signal_candle.high - pull_low) / sig_range * 100
            if retr_pct > max_pull_pct:
                continue
            # Resume: current closes above previous candle's high
            if current.close <= candles[-2].high:
                continue
            if current.close <= current.open:
                continue
            if ema_slope < ema_slope_min:
                continue
            return V2Setup(
                signal=V2Signal.BUY_CE,
                model=V2Model.B,
                reason=f"Model B | sig@-{k} body={body_s:.0f}% vol={vr_s:.2f}x | pullback {retr_pct:.0f}% held VWAP",
                structure_sl_spot=pull_low - 5,
                entry_method="MARKET",
            )

        if bearish_signal:
            if any(c.close > vwap for c in pull_candles):
                continue
            if any(c.volume >= signal_candle.volume for c in pull_candles):
                continue
            sig_range = signal_candle.high - signal_candle.low
            if sig_range <= 0:
                continue
            pull_high = max(c.high for c in pull_candles)
            retr_pct = (pull_high - signal_candle.low) / sig_range * 100
            if retr_pct > max_pull_pct:
                continue
            if current.close >= candles[-2].low:
                continue
            if current.close >= current.open:
                continue
            if ema_slope > -ema_slope_min:
                continue
            return V2Setup(
                signal=V2Signal.BUY_PE,
                model=V2Model.B,
                reason=f"Model B | sig@-{k} body={body_s:.0f}% vol={vr_s:.2f}x | pullback {retr_pct:.0f}% held VWAP",
                structure_sl_spot=pull_high + 5,
                entry_method="MARKET",
            )

    return None


def evaluate_model_c(
    candles: list[Candle],
    indicators: dict,
    cfg: dict,
    now: datetime,
) -> Optional[V2Setup]:
    """
    Liquidity-sweep reversal. The PREVIOUS candle wicked past a recent swing
    and closed back inside. The CURRENT candle confirms by closing in the
    reversal direction beyond the sweep candle's midpoint.
    """
    if len(candles) < 8:
        return None
    if now.time() >= time(14, 0):
        return None

    current = candles[-1]
    sweep   = candles[-2]
    swing_window = candles[-2 - cfg.get("model_c_swing_lookback", 6):-2]
    if not swing_window:
        return None

    min_sweep_pts = cfg.get("model_c_min_sweep_pts", 20)
    min_wick_frac = cfg.get("model_c_min_wick_frac", 0.50)
    min_sweep_vol = cfg.get("model_c_min_sweep_vol", 1.4)
    min_conf_vol  = cfg.get("model_c_min_confirm_vol", 0.8)
    prior_trend_n = cfg.get("model_c_prior_trend_candles", 3)

    swing_high = max(c.high for c in swing_window)
    swing_low  = min(c.low  for c in swing_window)

    sweep_range = sweep.high - sweep.low
    if sweep_range <= 0:
        return None
    sweep_vr = volume_ratio_at(candles, len(candles) - 2)
    if sweep_vr < min_sweep_vol:
        return None
    conf_vr = volume_ratio(candles)
    if conf_vr < min_conf_vol:
        return None
    sweep_midpoint = (sweep.high + sweep.low) / 2.0
    prior = candles[-2 - prior_trend_n:-2]

    # CE reversal: low was swept
    if sweep.low <= swing_low - min_sweep_pts:
        wick_below = min(sweep.open, sweep.close) - sweep.low
        if wick_below / sweep_range < min_wick_frac:
            return None
        if sweep.close <= sweep.low + sweep_range * 0.5:
            return None  # didn't reclaim
        if len(prior) >= prior_trend_n:
            dn_count = sum(1 for c in prior if c.close < c.open)
            if dn_count < max(1, prior_trend_n - 1):
                return None
        if current.close <= sweep_midpoint:
            return None
        if current.close <= current.open:
            return None
        return V2Setup(
            signal=V2Signal.BUY_CE,
            model=V2Model.C,
            reason=f"Model C | swept low {swing_low:.0f}→{sweep.low:.0f} | confirmed",
            structure_sl_spot=sweep.low - 5,
            entry_method="MARKET",
        )

    # PE reversal: high was swept
    if sweep.high >= swing_high + min_sweep_pts:
        wick_above = sweep.high - max(sweep.open, sweep.close)
        if wick_above / sweep_range < min_wick_frac:
            return None
        if sweep.close >= sweep.high - sweep_range * 0.5:
            return None
        if len(prior) >= prior_trend_n:
            up_count = sum(1 for c in prior if c.close > c.open)
            if up_count < max(1, prior_trend_n - 1):
                return None
        if current.close >= sweep_midpoint:
            return None
        if current.close >= current.open:
            return None
        return V2Setup(
            signal=V2Signal.BUY_PE,
            model=V2Model.C,
            reason=f"Model C | swept high {swing_high:.0f}→{sweep.high:.0f} | confirmed",
            structure_sl_spot=sweep.high + 5,
            entry_method="MARKET",
        )
    return None


def evaluate_model_d(
    candles: list[Candle],
    indicators: dict,
    cfg: dict,
    now: datetime,
) -> Optional[V2Setup]:
    """
    Flag-break continuation. Looks for: impulse leg → consolidation
    in the upper/lower third of the impulse → break on a non-climax candle.
    """
    if len(candles) < 12:
        return None
    if not time(*cfg.get("entry_window_start", (9, 50))) <= now.time() <= time(*cfg.get("caution_window_end", (14, 0))):
        return None

    current = candles[-1]
    flag_min = cfg.get("model_d_flag_min_candles", 3)
    flag_max = cfg.get("model_d_flag_max_candles", 6)
    impulse_min_pts = cfg.get("model_d_impulse_min_pts", 100)
    impulse_max_cands = cfg.get("model_d_impulse_max_candles", 3)
    impulse_body_min = cfg.get("model_d_impulse_body_min", 60.0)
    impulse_vol_min  = cfg.get("model_d_impulse_vol_min", 1.5)
    flag_body_max = cfg.get("model_d_flag_body_max", 60.0)
    break_body_min = cfg.get("model_d_break_body_min", 50.0)
    break_body_max = cfg.get("model_d_break_body_max", 85.0)
    break_vol_min  = cfg.get("model_d_break_vol_min", 1.2)

    cur_body = candle_body_pct(current)
    if not (break_body_min <= cur_body <= break_body_max):
        return None
    if volume_ratio(candles) < break_vol_min:
        return None

    # Try flag windows of size flag_min..flag_max immediately before current
    for flag_size in range(flag_min, flag_max + 1):
        flag_start = len(candles) - 1 - flag_size
        if flag_start < impulse_max_cands:
            continue
        flag_window = candles[flag_start:-1]
        if len(flag_window) != flag_size:
            continue
        # Flag bodies modest
        if any(candle_body_pct(c) > flag_body_max for c in flag_window):
            continue
        # Locate impulse leg ending right before flag
        for imp_size in range(1, impulse_max_cands + 1):
            imp_start = flag_start - imp_size
            if imp_start < 0:
                continue
            impulse = candles[imp_start:flag_start]
            if len(impulse) != imp_size:
                continue
            imp_open  = impulse[0].open
            imp_close = impulse[-1].close
            imp_range = max(c.high for c in impulse) - min(c.low for c in impulse)

            # Bullish impulse → CE flag
            if imp_close - imp_open >= impulse_min_pts:
                # All impulse candles bullish-ish (body, direction)
                if any(candle_body_pct(c) < impulse_body_min or c.close < c.open for c in impulse):
                    continue
                if max(volume_ratio_at(candles, imp_start + i) for i in range(imp_size)) < impulse_vol_min:
                    continue
                # Flag must stay in upper 1/3 of impulse high zone
                impulse_high = max(c.high for c in impulse)
                threshold = imp_close - imp_range / 3.0
                if min(c.low for c in flag_window) < threshold:
                    continue
                # Break candle: close above flag high
                flag_high = max(c.high for c in flag_window)
                if current.close <= flag_high:
                    continue
                if current.close <= current.open:
                    continue
                return V2Setup(
                    signal=V2Signal.BUY_CE,
                    model=V2Model.D,
                    reason=(
                        f"Model D | impulse {imp_size}c +{imp_close - imp_open:.0f}pts "
                        f"| flag {flag_size}c | break >{flag_high:.0f}"
                    ),
                    structure_sl_spot=min(c.low for c in flag_window) - 5,
                    entry_method="MARKET",
                )

            # Bearish impulse → PE flag
            if imp_open - imp_close >= impulse_min_pts:
                if any(candle_body_pct(c) < impulse_body_min or c.close > c.open for c in impulse):
                    continue
                if max(volume_ratio_at(candles, imp_start + i) for i in range(imp_size)) < impulse_vol_min:
                    continue
                impulse_low = min(c.low for c in impulse)
                threshold = imp_close + imp_range / 3.0
                if max(c.high for c in flag_window) > threshold:
                    continue
                flag_low = min(c.low for c in flag_window)
                if current.close >= flag_low:
                    continue
                if current.close >= current.open:
                    continue
                return V2Setup(
                    signal=V2Signal.BUY_PE,
                    model=V2Model.D,
                    reason=(
                        f"Model D | impulse {imp_size}c -{imp_open - imp_close:.0f}pts "
                        f"| flag {flag_size}c | break <{flag_low:.0f}"
                    ),
                    structure_sl_spot=max(c.high for c in flag_window) + 5,
                    entry_method="MARKET",
                )
    return None


# ---------------------------------------------------------------------------
# Per-day-class model allowlists
# ---------------------------------------------------------------------------

DAY_CLASS_ALLOWED_MODELS: dict[DayClass, set[V2Model]] = {
    DayClass.TREND:    {V2Model.A, V2Model.B, V2Model.D},
    DayClass.REVERSAL: {V2Model.C},
    DayClass.CHOP:     {V2Model.A},          # only after 11:00, half size — enforced by engine
    DayClass.NORMAL:   {V2Model.A, V2Model.B, V2Model.C, V2Model.D},
    DayClass.UNKNOWN:  set(),                # never trade before classified
}


def model_allowed_by_day(model: V2Model, day_class: DayClass) -> bool:
    return model in DAY_CLASS_ALLOWED_MODELS.get(day_class, set())


# ---------------------------------------------------------------------------
# Consecutive-leg tracker — call once per closed candle from engine
# ---------------------------------------------------------------------------

def update_consecutive_legs(ctx: DayContext, candle: Candle) -> None:
    if candle.close > candle.open:
        ctx.consecutive_up_candles += 1
        ctx.consecutive_dn_candles = 0
    elif candle.close < candle.open:
        ctx.consecutive_dn_candles += 1
        ctx.consecutive_up_candles = 0
    else:
        # doji — reset both
        ctx.consecutive_up_candles = 0
        ctx.consecutive_dn_candles = 0
