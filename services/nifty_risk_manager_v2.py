"""
NIFTY 2.0 — Risk Management.

Single-layer, tight risk:
  • Hard SL at −10% (cap losses fast)
  • Target +12% (book and walk away)
  • Breakeven: SL → entry once pnl ≥ +6%
  • Trailing: activates at +7%, gap 2.5% below peak
  • Time stop: after 6 candles (30 min), exit IF pnl < 0
  • Force exit at 15:15 IST
  • Cooldown: 4 candles between trades
  • Daily limits: max 2 trades, block 2nd after hard SL

All functions are pure — engine reads state, calls these, acts on the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from services.trading_state import Candle

IST = ZoneInfo("Asia/Kolkata")


class N2ExitLayer(str, Enum):
    NONE        = "NONE"
    HARD_SL     = "HARD_SL"     # true −10% floor hit (no BE move yet) — trips skip_second_after_hard_sl
    BE_STOP     = "BE_STOP"     # SL=entry was tightened by breakeven, then hit; near-flat exit, does NOT trip second-entry block
    TARGET      = "TARGET"
    TRAIL_SL    = "TRAIL_SL"
    TIME_STOP   = "TIME_STOP"
    TIME_FORCE  = "TIME_FORCE_EXIT"
    MANUAL_STOP = "MANUAL_STOP"


@dataclass
class N2PositionExtras:
    """Engine-managed extras tracking trail/MFE/candle count for v2 NIFTY."""
    model:               str = "NONE"
    entry_spot:          float = 0.0
    entry_vwap:          float = 0.0
    hard_sl_premium:     float = 0.0      # initial entry × (1 − sl_pct/100)
    trail_sl_premium:    float = 0.0      # current SL — starts = hard_sl_premium, only moves up
    breakeven_set:       bool = False
    trail_active:        bool = False
    peak_premium:        float = 0.0
    candles_since_entry: int = 0
    max_pnl_pct_seen:    float = -100.0


@dataclass
class N2ExitDecision:
    should_exit: bool = False
    layer:       N2ExitLayer = N2ExitLayer.NONE
    reason:      str = ""
    new_sl_premium: Optional[float] = None    # if non-None, engine updates trail SL


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
    # cooldown (candle count)
    if gate.last_exit_candle_idx >= 0:
        cooldown = cfg.get("cooldown_candles", 4)
        if gate.current_candle_idx - gate.last_exit_candle_idx < cooldown:
            left = cooldown - (gate.current_candle_idx - gate.last_exit_candle_idx)
            return False, f"cooldown — {left} candles left"
    # time window (also enforced by strategy, but belt-and-braces here)
    start = time(*cfg.get("entry_window_start", (9, 35)))
    end   = time(*cfg.get("entry_window_end",   (13, 30)))
    if now.time() < start:
        return False, f"before entry window ({start})"
    if now.time() >= end:
        return False, f"past last entry ({end})"
    return True, ""


# ---------------------------------------------------------------------------
# SL/target initialisation at entry
# ---------------------------------------------------------------------------

def initial_sl_target(entry_premium: float, cfg: dict) -> tuple[float, float, float]:
    """Returns (hard_sl_premium, target_premium, sl_pct_used)."""
    sl_pct     = cfg.get("sl_pct", 10.0)
    target_pct = cfg.get("target_pct", 12.0)
    hard_sl    = round(entry_premium * (1 - sl_pct / 100.0), 2)
    target_px  = round(entry_premium * (1 + target_pct / 100.0), 2)
    return hard_sl, target_px, sl_pct


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
) -> N2ExitDecision:
    """
    Priority:
      0. force-exit time
      1. target hit (+12%)
      2. SL hit (hard or trailing)
      3. time-stop after N candles if pnl < 0
      4. breakeven / trail updates (returns new_sl_premium, no exit)
    Mutates extras (peak / MFE / candles_since_entry caller-managed; this fn
    updates peak and max_pnl_pct_seen only).
    """
    now = now or datetime.now(IST)

    force_t = time(*cfg.get("force_exit_hhmm", (15, 15)))
    if now.time() >= force_t:
        return N2ExitDecision(True, N2ExitLayer.TIME_FORCE, f"force exit at {force_t}")

    if current_price <= 0 or entry_price <= 0:
        return N2ExitDecision(False, N2ExitLayer.NONE, "")

    pnl_pct = (current_price - entry_price) / entry_price * 100.0
    extras.max_pnl_pct_seen = max(extras.max_pnl_pct_seen, pnl_pct)
    if current_price > extras.peak_premium:
        extras.peak_premium = current_price

    target_pct = cfg.get("target_pct", 12.0)
    if pnl_pct >= target_pct:
        return N2ExitDecision(True, N2ExitLayer.TARGET,
                              f"target +{pnl_pct:.1f}% ≥ {target_pct:.0f}%")

    # SL hit
    sl_level = extras.trail_sl_premium if extras.trail_sl_premium > 0 else extras.hard_sl_premium
    if sl_level > 0 and current_price <= sl_level:
        if extras.trail_active:
            layer = N2ExitLayer.TRAIL_SL
        elif extras.breakeven_set:
            layer = N2ExitLayer.BE_STOP
        else:
            layer = N2ExitLayer.HARD_SL
        return N2ExitDecision(True, layer,
                              f"SL hit | sl={sl_level:.2f} cur={current_price:.2f} pnl={pnl_pct:.1f}%")

    # Time stop — only fires if still losing after N candles
    ts_n = cfg.get("time_stop_candles", 6)
    if extras.candles_since_entry >= ts_n and pnl_pct < 0:
        return N2ExitDecision(True, N2ExitLayer.TIME_STOP,
                              f"time stop {extras.candles_since_entry}c pnl={pnl_pct:.1f}%")

    # Breakeven move
    be_pct = cfg.get("breakeven_at_pct", 6.0)
    if not extras.breakeven_set and pnl_pct >= be_pct and extras.trail_sl_premium < entry_price:
        extras.trail_sl_premium = entry_price
        extras.breakeven_set = True
        return N2ExitDecision(False, N2ExitLayer.NONE, new_sl_premium=entry_price)

    # Trailing move
    trig = cfg.get("trail_trigger_pct", 7.0)
    gap  = cfg.get("trail_gap_pct", 2.5)
    if pnl_pct >= trig:
        candidate = extras.peak_premium * (1 - gap / 100.0)
        if candidate > extras.trail_sl_premium:
            extras.trail_sl_premium = candidate
            extras.trail_active = True
            return N2ExitDecision(False, N2ExitLayer.NONE, new_sl_premium=candidate)

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
