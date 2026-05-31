"""
BankNifty 2.0 — Risk Management (3 layers).

Layer 1 — Stop loss
    • Structure-based SL derived from setup (Model A/B/C/D defines it)
    • Hard cap (default 18%) / floor (default 8%) on premium SL
    • Early-tighten if no progress in 6 candles → reduce SL to entry × 0.94

Layer 2 — Profit booking
    • Partial 50% at time-bucket target (morning 18% / midday 15% / afternoon 12%)
    • After partial: runner with tightening trail, breakeven SL on remaining
    • Hard ceiling target by time bucket (no greed)

Layer 3 — Failure detection
    • STALL: 6 candles, max profit < 8% AND current ≤ 2%
    • STAGNATION: 12 candles without a ≥12% gain
    • STRUCTURE_BREAK: spot closes back through entry-side VWAP for 2 candles
    • TIME_FORCE_EXIT at 15:15 IST
    • MAX_DAILY_LOSS realised P&L cap → lock day
    • MAX_DAILY_PROFIT lock → protect win

All functions are pure; they read state and return decisions. The engine
mutates the position via the returned ExitDecision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from services.trading_state import Candle

IST = ZoneInfo("Asia/Kolkata")


class ExitLayer(str, Enum):
    NONE = "NONE"
    STRUCTURE_SL    = "STRUCTURE_SL"
    HARD_SL         = "HARD_SL"            # premium hit the hard floor/cap SL
    EARLY_TIGHT_SL  = "EARLY_TIGHT_SL"
    PARTIAL_TARGET  = "PARTIAL_TARGET"
    CEILING_TARGET  = "CEILING_TARGET"
    RUNNER_TRAIL    = "RUNNER_TRAIL"
    STALL           = "STALL_EXIT"
    STAGNATION      = "STAGNATION_EXIT"
    STRUCTURE_BREAK = "STRUCTURE_BREAK_EXIT"
    TIME_FORCE      = "TIME_FORCE_EXIT"
    DAILY_LOSS_LOCK = "DAILY_LOSS_LOCK"
    DAILY_PROFIT_LOCK = "DAILY_PROFIT_LOCK"
    MANUAL_STOP     = "MANUAL_STOP"
    OPPOSITE_SIGNAL = "OPPOSITE_SIGNAL"


@dataclass
class V2PositionExtras:
    """Extra fields v2 tracks on top of PositionInfo (held inside the engine)."""
    model: str = "NONE"                       # V2Model value
    entry_spot: float = 0.0                   # BankNifty spot at entry
    entry_vwap: float = 0.0                   # VWAP at entry
    entry_candle_low: float = 0.0             # for STRUCTURE_BREAK detection
    entry_candle_high: float = 0.0
    structure_sl_premium: float = 0.0         # initial premium SL after structure+floor+cap
    sl_pct: float = 0.0                       # premium SL % at entry
    target_partial_pct: float = 0.0
    target_ceiling_pct: float = 0.0
    partial_booked: bool = False
    partial_qty: int = 0
    partial_price: float = 0.0
    runner_trail_active: bool = False
    runner_trail_sl: float = 0.0
    candles_since_entry: int = 0
    max_pnl_pct_seen: float = -100.0          # tracks MFE; init below 0 so first update wins
    structure_break_candles: int = 0          # counter for VWAP-cross consecutive candles
    forced_lock: Optional[ExitLayer] = None   # daily-lock latch


@dataclass
class ExitDecision:
    should_exit: bool = False
    layer: ExitLayer = ExitLayer.NONE
    reason: str = ""
    qty_to_exit: int = 0                      # 0 = exit all remaining; >0 = partial exit
    new_trailing_sl_premium: Optional[float] = None  # if non-None: engine updates trail SL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_ist() -> datetime:
    return datetime.now(IST)


def time_bucket(t: time) -> str:
    """09:50–11:30 = morning, 11:30–13:00 = midday, 13:00–14:00 = afternoon."""
    if t < time(11, 30):
        return "morning"
    if t < time(13, 0):
        return "midday"
    return "afternoon"


def derive_initial_sl_premium(
    entry_premium: float,
    entry_spot: float,
    structure_sl_spot: Optional[float],
    cfg: dict,
) -> tuple[float, float]:
    """
    Translate structure-SL spot level into a premium SL, then apply floor/cap.
    Returns (sl_premium, sl_pct_applied).
    """
    delta = cfg.get("atm_delta_estimate", 0.55)
    cap   = cfg.get("sl_pct_cap_hard", 18.0)
    floor = cfg.get("sl_pct_floor", 8.0)

    if structure_sl_spot is not None and entry_spot > 0:
        spot_distance = abs(entry_spot - structure_sl_spot)
        expected_premium_drop = spot_distance * delta
        sl_pct = expected_premium_drop / entry_premium * 100.0 if entry_premium > 0 else cap
    else:
        sl_pct = cap

    sl_pct = max(min(sl_pct, cap), floor)
    sl_premium = entry_premium * (1 - sl_pct / 100.0)
    return round(sl_premium, 2), round(sl_pct, 2)


def derive_targets(entry_time: time, cfg: dict) -> tuple[float, float]:
    bucket = time_bucket(entry_time)
    if bucket == "morning":
        return cfg.get("partial_target_morning", 18.0), cfg.get("ceiling_morning_pct", 40.0)
    if bucket == "midday":
        return cfg.get("partial_target_midday", 15.0), cfg.get("ceiling_midday_pct", 30.0)
    return cfg.get("partial_target_afternoon", 12.0), cfg.get("ceiling_afternoon_pct", 20.0)


def runner_trail_gap(pnl_pct: float, cfg: dict) -> Optional[float]:
    """Return current trail gap (%) for the runner, or None if not yet active."""
    table = cfg.get("runner_trail_gaps", [(20.0, 8.0), (30.0, 6.0), (45.0, 5.0), (60.0, 4.0)])
    chosen = None
    for threshold, gap in table:
        if pnl_pct >= threshold:
            chosen = gap
    return chosen


# ---------------------------------------------------------------------------
# Entry gate
# ---------------------------------------------------------------------------

@dataclass
class V2EntryGateInput:
    engine_running: bool
    realized_pnl: float
    trades_today: int
    first_trade_was_loss: bool
    first_trade_was_stall_or_break: bool
    last_exit_time: Optional[datetime]
    forced_lock: Optional[ExitLayer]
    has_open_position: bool


def can_enter_trade_v2(gate: V2EntryGateInput, cfg: dict, now: datetime) -> tuple[bool, str]:
    if not gate.engine_running:
        return False, "engine not running"
    if gate.has_open_position:
        return False, "position already open"
    if gate.forced_lock is not None:
        return False, f"day locked ({gate.forced_lock.value})"
    if gate.realized_pnl <= cfg.get("daily_loss_lock_rupees", -6000.0):
        return False, "daily loss lock reached"
    if gate.realized_pnl >= cfg.get("daily_profit_lock_rupees", 15000.0):
        return False, "daily profit lock reached"
    if gate.trades_today >= cfg.get("max_trades_per_day", 2):
        return False, "max trades for the day reached"
    # Cooldown + skip-after-loss
    if gate.trades_today >= 1:
        if gate.first_trade_was_loss and cfg.get("skip_second_after_sl", True):
            return False, "second entry blocked — first trade was a stop-loss"
        if gate.first_trade_was_stall_or_break and cfg.get("skip_second_after_stall", True):
            return False, "second entry blocked — first trade was stall/structure-break"
        if gate.last_exit_time is not None:
            cooldown = cfg.get("second_trade_cooldown_min", 45)
            elapsed_min = (now - gate.last_exit_time).total_seconds() / 60.0
            if elapsed_min < cooldown:
                return False, f"cooldown — {cooldown - elapsed_min:.0f} min left"

    # Time windows
    entry_start = time(*cfg.get("entry_window_start", (9, 50)))
    last_entry  = time(*cfg.get("caution_window_end", (14, 0)))
    if now.time() < entry_start:
        return False, f"before entry window ({entry_start})"
    if now.time() >= last_entry:
        return False, f"past last entry time ({last_entry})"
    return True, ""


# ---------------------------------------------------------------------------
# Exit evaluator
# ---------------------------------------------------------------------------

def evaluate_exit_v2(
    *,
    entry_price: float,
    current_price: float,
    entry_time: datetime,
    option_type: str,
    qty_remaining: int,
    extras: V2PositionExtras,
    candles: list[Candle],
    vwap: float,
    cfg: dict,
    realized_pnl: float,
    now: Optional[datetime] = None,
) -> ExitDecision:
    """
    Evaluate all exit layers in priority order. Mutates `extras` for trail/MFE tracking.
    Returns an ExitDecision; the engine acts on `should_exit` / `qty_to_exit`.
    """
    now = now or _now_ist()

    # ── 0. Day-level locks ────────────────────────────────────────────────
    if extras.forced_lock is not None:
        return ExitDecision(True, extras.forced_lock, f"day locked ({extras.forced_lock.value})")

    if realized_pnl <= cfg.get("daily_loss_lock_rupees", -6000.0):
        extras.forced_lock = ExitLayer.DAILY_LOSS_LOCK
        return ExitDecision(True, ExitLayer.DAILY_LOSS_LOCK, "realised loss lock")

    if realized_pnl >= cfg.get("daily_profit_lock_rupees", 15000.0):
        extras.forced_lock = ExitLayer.DAILY_PROFIT_LOCK
        return ExitDecision(True, ExitLayer.DAILY_PROFIT_LOCK, "realised profit lock")

    # ── 1. Time force exit ────────────────────────────────────────────────
    force_t = time(*cfg.get("force_exit_hhmm", (15, 15)))
    if now.time() >= force_t:
        return ExitDecision(True, ExitLayer.TIME_FORCE, f"force exit at {force_t}")

    if current_price <= 0 or entry_price <= 0:
        return ExitDecision(False, ExitLayer.NONE, "")

    pnl_pct = (current_price - entry_price) / entry_price * 100.0
    extras.max_pnl_pct_seen = max(extras.max_pnl_pct_seen, pnl_pct)

    # ── 2. Stop-loss (premium-level, structure or trailing) ───────────────
    # If runner trail is active, use runner_trail_sl as the SL; else structure_sl_premium.
    sl_level = extras.runner_trail_sl if extras.runner_trail_active else extras.structure_sl_premium
    if sl_level > 0 and current_price <= sl_level:
        layer = ExitLayer.RUNNER_TRAIL if extras.runner_trail_active else (
            ExitLayer.HARD_SL if pnl_pct < 0 else ExitLayer.STRUCTURE_SL
        )
        return ExitDecision(True, layer, f"SL hit | sl={sl_level:.2f} cur={current_price:.2f} pnl={pnl_pct:.1f}%")

    # ── 3. Partial profit booking ─────────────────────────────────────────
    if (not extras.partial_booked
            and qty_remaining > 0
            and pnl_pct >= extras.target_partial_pct):
        # Book 50% (or specified fraction)
        frac = cfg.get("partial_book_pct", 50) / 100.0
        qty_to_book = max(1, int(round(qty_remaining * frac)))
        return ExitDecision(
            should_exit=True,
            layer=ExitLayer.PARTIAL_TARGET,
            reason=f"partial book @+{pnl_pct:.1f}% (target {extras.target_partial_pct:.0f}%)",
            qty_to_exit=qty_to_book,
        )

    # ── 4. Ceiling target on runner (or full if no partial taken) ─────────
    if pnl_pct >= extras.target_ceiling_pct:
        return ExitDecision(True, ExitLayer.CEILING_TARGET,
                            f"ceiling target +{pnl_pct:.1f}% ≥ {extras.target_ceiling_pct:.0f}%")

    # ── 5. Runner trailing logic — activate / update ──────────────────────
    if extras.partial_booked:
        gap = runner_trail_gap(pnl_pct, cfg)
        if gap is not None:
            new_trail = current_price * (1 - gap / 100.0)
            # Trail SL only moves up
            if new_trail > extras.runner_trail_sl:
                extras.runner_trail_sl = new_trail
                extras.runner_trail_active = True
                # Don't exit on this candle; just updated trail
                return ExitDecision(
                    should_exit=False,
                    layer=ExitLayer.NONE,
                    new_trailing_sl_premium=new_trail,
                )
        else:
            # Below trail trigger → keep SL at breakeven (or higher if already set)
            be = entry_price
            if extras.runner_trail_sl < be:
                extras.runner_trail_sl = be
                extras.runner_trail_active = True
                return ExitDecision(False, ExitLayer.NONE, new_trailing_sl_premium=be)

    # ── 6. Early tighten check (one-time, around candle N) ────────────────
    early_n = cfg.get("sl_early_tighten_candles", 6)
    if (extras.candles_since_entry >= early_n
            and not extras.partial_booked
            and extras.max_pnl_pct_seen < cfg.get("sl_early_tighten_min_gain", 8.0)):
        target_pct = cfg.get("sl_early_tighten_to_pct", -6.0)
        tight_sl = entry_price * (1 + target_pct / 100.0)   # target_pct is negative → SL ≈ -6%
        if tight_sl > extras.structure_sl_premium:
            extras.structure_sl_premium = tight_sl
            # If price already below tight SL → exit now
            if current_price <= tight_sl:
                return ExitDecision(True, ExitLayer.EARLY_TIGHT_SL,
                                    f"early tighten triggered at {tight_sl:.2f}")
            return ExitDecision(False, ExitLayer.NONE, new_trailing_sl_premium=tight_sl)

    # ── 7. Stall exit (Layer-3 trigger 1) ─────────────────────────────────
    stall_n = cfg.get("stall_max_candles", 6)
    stall_max_profit = cfg.get("stall_max_profit_pct", 8.0)
    stall_cur_max    = cfg.get("stall_current_max_pct", 2.0)
    if (extras.candles_since_entry >= stall_n
            and extras.max_pnl_pct_seen < stall_max_profit
            and pnl_pct <= stall_cur_max
            and not extras.partial_booked):
        return ExitDecision(True, ExitLayer.STALL,
                            f"stall: {extras.candles_since_entry}c MFE={extras.max_pnl_pct_seen:.1f}% cur={pnl_pct:.1f}%")

    # ── 8. Stagnation exit (Layer-3 trigger 2) ────────────────────────────
    stag_n = cfg.get("stagnation_candles", 12)
    stag_min = cfg.get("stagnation_min_gain_pct", 12.0)
    if (extras.candles_since_entry >= stag_n
            and extras.max_pnl_pct_seen < stag_min
            and pnl_pct <= 0):
        return ExitDecision(True, ExitLayer.STAGNATION,
                            f"stagnation: {extras.candles_since_entry}c MFE={extras.max_pnl_pct_seen:.1f}%")

    # ── 9. Structure break (Layer-3 trigger 3) ────────────────────────────
    if len(candles) >= 2 and vwap > 0:
        last_close = candles[-1].close
        prev_close = candles[-2].close
        mid = (extras.entry_candle_high + extras.entry_candle_low) / 2.0
        if option_type == "CE":
            # bearish structure break: close <vwap for 2 consecutive candles AND below entry midpoint
            if last_close < vwap and prev_close < vwap and last_close < mid:
                extras.structure_break_candles += 1
                if extras.structure_break_candles >= 1:
                    return ExitDecision(True, ExitLayer.STRUCTURE_BREAK,
                                        f"CE: 2c below VWAP, close={last_close:.0f}<mid={mid:.0f}")
            else:
                extras.structure_break_candles = 0
        else:  # PE
            if last_close > vwap and prev_close > vwap and last_close > mid:
                extras.structure_break_candles += 1
                if extras.structure_break_candles >= 1:
                    return ExitDecision(True, ExitLayer.STRUCTURE_BREAK,
                                        f"PE: 2c above VWAP, close={last_close:.0f}>mid={mid:.0f}")
            else:
                extras.structure_break_candles = 0

    return ExitDecision(False, ExitLayer.NONE, "")


# ---------------------------------------------------------------------------
# P&L helper (matches v1's calculate_pnl shape, no need to import)
# ---------------------------------------------------------------------------
def calc_pnl(entry_price: float, current_price: float, qty: int) -> dict:
    pnl_pts = current_price - entry_price
    return {
        "entry_price":   round(entry_price, 2),
        "current_price": round(current_price, 2),
        "pnl_points":    round(pnl_pts, 2),
        "pnl_rupees":    round(pnl_pts * qty, 2),
        "pnl_pct":       round(pnl_pts / entry_price * 100.0, 2) if entry_price > 0 else 0.0,
        "qty":           qty,
    }
