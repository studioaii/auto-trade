"""
NIFTY 2.0 — Simple early-entry strategy.

Three numeric, named entry models that catch moves earlier than v1:

  M1 — VWAP Reclaim       (catches turning points; signal candle crosses VWAP)
  M2 — Opening-Range Break (catches the first trend of the day, 09:35–10:15)
  M3 — Pullback Continuation (catches the second leg of a fresh trend)

Universal filters (cheap, few) and tight RSI bands block exhaustion entries.
Pure functions — no I/O, no state mutation. The engine calls per closed candle.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional

from services.trading_state import Candle
from services.indicators import candle_body_pct


# ---------------------------------------------------------------------------
# Enums / setup descriptor
# ---------------------------------------------------------------------------

class N2Signal(str, Enum):
    BUY_CE    = "BUY_CE"
    BUY_PE    = "BUY_PE"
    NO_SIGNAL = "NO_SIGNAL"


class N2Model(str, Enum):
    NONE = "NONE"
    M1   = "M1_VWAP_RECLAIM"
    M2   = "M2_ORB"
    M3   = "M3_PULLBACK"


@dataclass
class N2Setup:
    """Outcome of evaluating the strategy on the latest candle."""
    signal: N2Signal = N2Signal.NO_SIGNAL
    model:  N2Model  = N2Model.NONE
    reason: str = ""
    skip_reason: str = ""


@dataclass
class N2DayContext:
    """Per-day runtime context — mutated by the engine, read by strategy fns."""
    or_high: Optional[float] = None
    or_low:  Optional[float] = None
    or_locked: bool = False                 # True once OR window has elapsed
    consecutive_up_candles: int = 0
    consecutive_dn_candles: int = 0
    orb_used: bool = False                  # True once we've taken our 1 ORB trade


# ---------------------------------------------------------------------------
# OR computation + per-candle leg tracker
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
# Universal entry gates — return list of skip reasons (empty = clear)
# ---------------------------------------------------------------------------

def _universal_skips(
    candle: Candle,
    indicators: dict,
    ctx: N2DayContext,
    cfg: dict,
    now: datetime,
) -> list[str]:
    reasons: list[str] = []
    body = candle_body_pct(candle)
    rng_pct = (candle.high - candle.low) / candle.close * 100.0 if candle.close > 0 else 0
    vwap = indicators.get("vwap") or 0.0

    # spike (range >0.85% of price = volatile bar)
    if rng_pct > 0.85:
        reasons.append(f"spike {rng_pct:.2f}%")

    if body < cfg.get("body_min_pct", 55.0):
        reasons.append(f"body {body:.0f}% < {cfg.get('body_min_pct', 55):.0f}")

    if vwap > 0:
        dist = abs(candle.close - vwap) / vwap * 100.0
        if dist < cfg.get("vwap_dist_min_pct", 0.10):
            reasons.append(f"vwap dist {dist:.2f}% < min")
        elif dist > cfg.get("vwap_dist_max_pct", 0.40):
            reasons.append(f"vwap dist {dist:.2f}% > max")
    else:
        reasons.append("no VWAP")

    # 4+ same-dir legs
    max_legs = cfg.get("max_consecutive_same_dir", 4)
    if ctx.consecutive_up_candles >= max_legs:
        reasons.append(f"{ctx.consecutive_up_candles} legs up")
    if ctx.consecutive_dn_candles >= max_legs:
        reasons.append(f"{ctx.consecutive_dn_candles} legs down")

    # time window
    win_start = time(*cfg.get("entry_window_start", (9, 35)))
    win_end   = time(*cfg.get("entry_window_end",   (13, 30)))
    if now.time() < win_start:
        reasons.append(f"before {win_start}")
    elif now.time() >= win_end:
        reasons.append(f"past {win_end}")

    return reasons


def _rsi_in_band(rsi: float, side: N2Signal, cfg: dict) -> bool:
    if side == N2Signal.BUY_CE:
        return cfg.get("rsi_min_ce", 50.0) <= rsi <= cfg.get("rsi_max_ce", 67.0)
    if side == N2Signal.BUY_PE:
        return cfg.get("rsi_min_pe", 33.0) <= rsi <= cfg.get("rsi_max_pe", 50.0)
    return False


# ---------------------------------------------------------------------------
# Model 1 — VWAP Reclaim
# ---------------------------------------------------------------------------

def evaluate_m1_vwap_reclaim(
    candles: list[Candle],
    indicators: dict,
    cfg: dict,
    now: datetime,
) -> Optional[N2Setup]:
    """Previous candle on one side of VWAP, current closes across with body confirmation."""
    if len(candles) < 2:
        return None
    cur = candles[-1]
    prev = candles[-2]
    vwap = indicators.get("vwap") or 0.0
    rsi  = indicators.get("rsi14") or 50.0
    if vwap <= 0:
        return None

    # CE reclaim: prev below VWAP, current closes above + bullish body
    if prev.close < vwap and cur.close > vwap and cur.close > cur.open:
        if _rsi_in_band(rsi, N2Signal.BUY_CE, cfg):
            return N2Setup(
                signal=N2Signal.BUY_CE,
                model=N2Model.M1,
                reason=f"M1 | reclaim VWAP={vwap:.0f} prev<{vwap:.0f} cur>{vwap:.0f} | RSI={rsi:.0f}",
            )

    # PE reclaim: prev above VWAP, current closes below + bearish body
    if prev.close > vwap and cur.close < vwap and cur.close < cur.open:
        if _rsi_in_band(rsi, N2Signal.BUY_PE, cfg):
            return N2Setup(
                signal=N2Signal.BUY_PE,
                model=N2Model.M1,
                reason=f"M1 | break VWAP={vwap:.0f} prev>{vwap:.0f} cur<{vwap:.0f} | RSI={rsi:.0f}",
            )
    return None


# ---------------------------------------------------------------------------
# Model 2 — Opening-Range Breakout
# ---------------------------------------------------------------------------

def evaluate_m2_orb(
    candles: list[Candle],
    indicators: dict,
    ctx: N2DayContext,
    cfg: dict,
    now: datetime,
) -> Optional[N2Setup]:
    """First-trend breakout from the OR (locked at OR-window-end)."""
    if ctx.orb_used:
        return None
    if not ctx.or_locked or ctx.or_high is None or ctx.or_low is None:
        return None
    orb_start = time(*cfg.get("orb_window_start", (9, 35)))
    orb_end   = time(*cfg.get("orb_window_end",   (10, 15)))
    if not (orb_start <= now.time() <= orb_end):
        return None
    if not candles:
        return None
    cur  = candles[-1]
    vwap = indicators.get("vwap") or 0.0
    rsi  = indicators.get("rsi14") or 50.0
    if vwap <= 0:
        return None
    min_dist = cfg.get("orb_min_vwap_dist_pct", 0.15)
    dist = abs(cur.close - vwap) / vwap * 100.0

    if dist < min_dist:
        return None

    # CE: bullish breakout above OR-high AND close > VWAP
    if cur.close > ctx.or_high and cur.close > vwap and cur.close > cur.open:
        if _rsi_in_band(rsi, N2Signal.BUY_CE, cfg):
            return N2Setup(
                signal=N2Signal.BUY_CE,
                model=N2Model.M2,
                reason=f"M2 | break ORH={ctx.or_high:.0f} close={cur.close:.0f} | RSI={rsi:.0f}",
            )
    # PE: bearish break below OR-low AND close < VWAP
    if cur.close < ctx.or_low and cur.close < vwap and cur.close < cur.open:
        if _rsi_in_band(rsi, N2Signal.BUY_PE, cfg):
            return N2Setup(
                signal=N2Signal.BUY_PE,
                model=N2Model.M2,
                reason=f"M2 | break ORL={ctx.or_low:.0f} close={cur.close:.0f} | RSI={rsi:.0f}",
            )
    return None


# ---------------------------------------------------------------------------
# Model 3 — Pullback Continuation
# ---------------------------------------------------------------------------

def evaluate_m3_pullback(
    candles: list[Candle],
    indicators: dict,
    cfg: dict,
    now: datetime,
) -> Optional[N2Setup]:
    """
    Look 2…N candles back for a strong signal candle (body ≥ 60%), then verify
    the pullback held VWAP side and the current candle resumes direction.
    """
    if len(candles) < 5:
        return None
    cur  = candles[-1]
    prev = candles[-2]
    vwap = indicators.get("vwap") or 0.0
    rsi  = indicators.get("rsi14") or 50.0
    if vwap <= 0:
        return None
    body_min = cfg.get("pullback_signal_body_min", 60.0)
    k_min    = cfg.get("pullback_lookback_min", 2)
    k_max    = cfg.get("pullback_lookback_max", 4)

    for k in range(k_min, k_max + 1):
        if len(candles) < k + 1:
            break
        sig = candles[-k - 1]
        if candle_body_pct(sig) < body_min:
            continue
        pull = candles[-k:-1]
        if not pull:
            continue

        # CE: signal bullish, pullback closes held above VWAP, current resumes
        if sig.close > sig.open:
            if any(c.close < vwap for c in pull):
                continue
            if cur.close <= prev.high:
                continue
            if cur.close <= cur.open:
                continue
            if cur.close <= max(c.high for c in pull):
                continue
            if _rsi_in_band(rsi, N2Signal.BUY_CE, cfg):
                return N2Setup(
                    signal=N2Signal.BUY_CE,
                    model=N2Model.M3,
                    reason=f"M3 | sig@-{k} body={candle_body_pct(sig):.0f}% | break above pull",
                )
        # PE: signal bearish, pullback held below VWAP, current resumes
        if sig.close < sig.open:
            if any(c.close > vwap for c in pull):
                continue
            if cur.close >= prev.low:
                continue
            if cur.close >= cur.open:
                continue
            if cur.close >= min(c.low for c in pull):
                continue
            if _rsi_in_band(rsi, N2Signal.BUY_PE, cfg):
                return N2Setup(
                    signal=N2Signal.BUY_PE,
                    model=N2Model.M3,
                    reason=f"M3 | sig@-{k} body={candle_body_pct(sig):.0f}% | break below pull",
                )
    return None


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
    """Combine universal filters and the 3 models. Returns N2Setup."""
    if not candles:
        return N2Setup(skip_reason="no candles")
    cur = candles[-1]

    # Universal skips
    skips = _universal_skips(cur, indicators, ctx, cfg, now)
    if skips:
        return N2Setup(skip_reason="; ".join(skips))

    # Try models in order — M1 (fastest signal), M2 (ORB only window), M3 (continuation)
    for model_fn in (
        lambda: evaluate_m1_vwap_reclaim(candles, indicators, cfg, now),
        lambda: evaluate_m2_orb(candles, indicators, ctx, cfg, now),
        lambda: evaluate_m3_pullback(candles, indicators, cfg, now),
    ):
        setup = model_fn()
        if setup is not None:
            return setup

    return N2Setup(skip_reason="no model matched")
