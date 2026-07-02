"""
NIFTY 2.0 — Risk Management.

This mirrors NIFTY 1.0's wide-tail risk profile (the edge is a fat tail of
+18..+35% trailing winners; tight targets / time-stops destroy it):

  • Hard SL at −18% (v1 value — cuts dead-loser bleed without clipping the
    deep-dip-then-recover winners; the +29% winner dipped to −15.4% first).
  • Trailing SL: activates at +15% gain, gap 6% below peak, tightens 1% per
    additional +10% gain, floored at 3%. Trail SL only ever moves up.
  • NO fixed target, NO breakeven move, NO time-stop (all three were rejected
    by the analysis — they kill the tail or chop winners flat).
  • Opposite-signal exit is orchestrated by the engine (needs candle/VWAP
    context + the 2-consecutive-close confirmation); it is not evaluated here.
  • Force exit at 15:20 IST.
  • Daily gates: max 2 trades, block 2nd entry after a hard SL, optional
    candle cooldown (default 0 = off, matching v1), entry window 09:50–14:00
    (the 11:00 morning wall was removed 2026-07-02 — its forward test blocked
    only winners; chop protection now lives in the strategy's session gate).

Instrumentation: extras track tick-resolution MFE (max_pnl_pct_seen) AND MAE
(min_pnl_pct_seen) — updated on every call (tick + candle).

All functions are pure — the engine reads state, calls these, acts on the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class N2ExitLayer(str, Enum):
    NONE            = "NONE"
    STOPLOSS_HIT    = "STOPLOSS_HIT"     # hard −18% floor hit (trips skip_second_after_hard_sl)
    TRAILING_STOP   = "TRAILING_STOP"    # trailing SL hit after it armed (a profitable exit)
    OPPOSITE_SIGNAL = "OPPOSITE_SIGNAL"  # confirmed reverse breakout (engine-driven)
    TIME_EXIT       = "TIME_EXIT"        # 15:20 force exit
    MANUAL_STOP     = "MANUAL_STOP"      # engine stop / shutdown


@dataclass
class N2PositionExtras:
    """Engine-managed extras tracking trail / MFE / MAE / candle count."""
    model:               str = "V1_BREAKOUT"
    entry_spot:          float = 0.0
    entry_vwap:          float = 0.0
    hard_sl_premium:     float = 0.0      # entry × (1 − sl_pct/100)
    trail_sl_premium:    float = 0.0      # current SL — starts = hard_sl, only moves up
    breakeven_set:       bool = False     # unused (no BE in wide-tail); kept for log compat
    trail_active:        bool = False
    peak_premium:        float = 0.0
    candles_since_entry: int = 0
    max_pnl_pct_seen:    float = -100.0   # MFE (tick resolution)
    min_pnl_pct_seen:    float = 100.0    # MAE (tick resolution)
    consec_wrong_side_vwap: int = 0       # consecutive candle closes on the wrong VWAP side


@dataclass
class N2ExitDecision:
    should_exit: bool = False
    layer:       N2ExitLayer = N2ExitLayer.NONE
    reason:      str = ""
    new_sl_premium: Optional[float] = None    # if non-None, engine updates trail SL display


# ---------------------------------------------------------------------------
# Entry gate
# ---------------------------------------------------------------------------

@dataclass
class N2EntryGateInput:
    engine_running:        bool
    trades_today:          int
    first_trade_was_sl:    bool
    has_open_position:     bool
    last_exit_candle_idx:  int        # candle index when last trade exited (-1 if none)
    current_candle_idx:    int        # candle index of latest closed candle


def can_enter_trade_n2(
    gate: N2EntryGateInput,
    cfg: dict,
    now: datetime,
) -> tuple[bool, str]:
    if not gate.engine_running:
        return False, "engine not running"
    if gate.has_open_position:
        return False, "position already open"
    if gate.trades_today >= cfg.get("max_trades_per_day", 2):
        return False, "max trades for the day reached"
    if (gate.trades_today >= 1
            and gate.first_trade_was_sl
            and cfg.get("skip_second_after_hard_sl", True)):
        return False, "second entry blocked — first trade hit hard SL"
    # optional cooldown (candle count) — default 0 = off (v1 has no cooldown)
    cooldown = cfg.get("cooldown_candles", 0)
    if cooldown > 0 and gate.last_exit_candle_idx >= 0:
        elapsed = gate.current_candle_idx - gate.last_exit_candle_idx
        if elapsed < cooldown:
            return False, f"cooldown — {cooldown - elapsed} candles left"
    # time window — v1 session start (09:50) and v1 last-entry (14:00)
    start = time(*cfg.get("entry_window_start", (9, 50)))
    end   = time(*cfg.get("entry_window_end",   (14, 0)))
    if now.time() < start:
        return False, f"before entry window ({start.strftime('%H:%M')})"
    if now.time() >= end:
        return False, f"past last entry ({end.strftime('%H:%M')})"
    return True, ""


# ---------------------------------------------------------------------------
# SL initialisation at entry
# ---------------------------------------------------------------------------

def initial_sl_target(entry_premium: float, cfg: dict) -> tuple[float, float, float]:
    """
    Returns (hard_sl_premium, target_premium, sl_pct_used).
    target_premium is 0.0 — the wide-tail strategy has NO fixed target; it
    exits via trailing SL, opposite signal, or time. Returned for signature
    compatibility with the engine/logger only.
    """
    sl_pct  = cfg.get("sl_pct", 18.0)
    hard_sl = round(entry_premium * (1 - sl_pct / 100.0), 2)
    return hard_sl, 0.0, sl_pct


# ---------------------------------------------------------------------------
# Trailing-stop update (v1 dynamic gap)
# ---------------------------------------------------------------------------

def _update_trail(extras: N2PositionExtras, current: float, pnl_pct: float, cfg: dict) -> Optional[float]:
    """
    v1 trailing logic. Mutates extras in place. Returns the new SL premium if it
    moved up this call, else None.

      - Activates at trail_trigger_pct (+15%)
      - Gap starts at trail_gap_base_pct (6%) below the peak
      - Each extra +10% gain tightens the gap by trail_gap_step_pct (1%)
      - Gap floored at trail_gap_min_pct (3%); SL only ever moves up
    """
    trig = cfg.get("trail_trigger_pct", 15.0)
    if pnl_pct < trig:
        return None

    extras.trail_active = True
    if current > extras.peak_premium:
        extras.peak_premium = current

    base  = cfg.get("trail_gap_base_pct", 6.0)
    step  = cfg.get("trail_gap_step_pct", 1.0)
    floor = cfg.get("trail_gap_min_pct", 3.0)
    extra_steps = int((pnl_pct - trig) / 10)
    gap = max(base - extra_steps * step, floor)

    new_sl = extras.peak_premium * (1 - gap / 100.0)
    if new_sl > extras.trail_sl_premium:
        extras.trail_sl_premium = new_sl
        return new_sl
    return None


# ---------------------------------------------------------------------------
# Exit evaluator
# ---------------------------------------------------------------------------

def evaluate_exit_n2(
    *,
    entry_price: float,
    current_price: float,
    extras: N2PositionExtras,
    cfg: dict,
    now: Optional[datetime] = None,
    allow_sl_moves: bool = True,
) -> N2ExitDecision:
    """
    Priority:
      0. force-exit time (15:20)
      1. SL hit — hard (−18%) or trailing
      2. trailing-SL update (returns new_sl_premium, no exit)
    MFE/MAE are updated on EVERY call (tick + candle) for tick-resolution
    instrumentation. The trailing SL only ARMS on confirmed candle closes
    (allow_sl_moves=True) so a single anomalous tick can't inflate the peak /
    arm the trail; the SL LEVEL itself is still checked on every tick so exits
    stay tick-fast. Opposite-signal exit is handled by the engine, not here.
    """
    now = now or datetime.now(IST)

    force_t = time(*cfg.get("force_exit_hhmm", (15, 20)))
    if now.time() >= force_t:
        return N2ExitDecision(True, N2ExitLayer.TIME_EXIT, f"force exit at {force_t.strftime('%H:%M')}")

    if current_price <= 0 or entry_price <= 0:
        return N2ExitDecision(False, N2ExitLayer.NONE, "")

    pnl_pct = (current_price - entry_price) / entry_price * 100.0
    extras.max_pnl_pct_seen = max(extras.max_pnl_pct_seen, pnl_pct)
    extras.min_pnl_pct_seen = min(extras.min_pnl_pct_seen, pnl_pct)

    moved_sl = _update_trail(extras, current_price, pnl_pct, cfg) if allow_sl_moves else None

    # SL hit — trail_sl_premium starts equal to the hard SL and only moves up.
    sl_level = extras.trail_sl_premium if extras.trail_sl_premium > 0 else extras.hard_sl_premium
    if sl_level > 0 and current_price <= sl_level:
        layer = N2ExitLayer.TRAILING_STOP if extras.trail_active else N2ExitLayer.STOPLOSS_HIT
        return N2ExitDecision(True, layer,
                              f"SL hit | sl={sl_level:.2f} cur={current_price:.2f} pnl={pnl_pct:.1f}%")

    if moved_sl is not None:
        return N2ExitDecision(False, N2ExitLayer.NONE, new_sl_premium=moved_sl)

    return N2ExitDecision(False, N2ExitLayer.NONE, "")


# ---------------------------------------------------------------------------
# P&L helper
# ---------------------------------------------------------------------------

def calc_pnl_n2(entry_price: float, current_price: float, qty: int) -> dict:
    pnl_pts = current_price - entry_price
    return {
        "entry_price":   round(entry_price, 2),
        "current_price": round(current_price, 2),
        "pnl_points":    round(pnl_pts, 2),
        "pnl_rupees":    round(pnl_pts * qty, 2),
        "pnl_pct":       round(pnl_pts / entry_price * 100.0, 2) if entry_price > 0 else 0.0,
        "qty":           qty,
    }
