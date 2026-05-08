"""
Risk-management gates for the trading engine.

v3 — cfg-driven. Per-instrument config lives in `INSTRUMENT_CONFIG[<INSTRUMENT>]`
and is passed in through `cfg`. Module-level constants are kept for backward
compat (legacy strategy + the backtest harness import them); when `cfg` is
None, all gates fall back to those legacy values.

Two functions:
- `can_enter_trade(state, cfg=None, instrument=None) -> (allowed, reason)`
- `check_exit_conditions(position, state=None, cfg=None) -> (action, reason, qty)`
   - action ∈ {"NONE", "FULL_EXIT", "PARTIAL_EXIT"}
   - qty is the partial-leg qty for PARTIAL_EXIT, else 0
"""
import logging
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from services.trading_state import PositionInfo, TradingState, get_raw_state

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Legacy constants (used as defaults when cfg is None / key missing)
# ---------------------------------------------------------------------------

INITIAL_SL_PCT      = 20.0   # Premium-% SL (legacy)

TRAIL_TRIGGER       = 15.0
TRAIL_GAP_BASE      = 6.0
TRAIL_GAP_STEP      = 1.0
TRAIL_GAP_MIN       = 3.0

MAX_TRADES_PER_DAY  = 2
FORCE_EXIT_TIME     = time(15, 20)
LAST_ENTRY_TIME     = time(14, 0)
MARKET_WAIT         = time(9, 50)


def _now_ist() -> time:
    return datetime.now(IST).time()


def _now_dt_ist() -> datetime:
    return datetime.now(IST)


def _parse_time(value, default: time) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str) and ":" in value:
        try:
            h, m = value.split(":", 1)
            return time(int(h), int(m))
        except (TypeError, ValueError):
            return default
    return default


# ---------------------------------------------------------------------------
# Entry gate
# ---------------------------------------------------------------------------
def can_enter_trade(
    state: TradingState,
    cfg: Optional[dict] = None,
    instrument: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Gatekeeper before any entry. Returns (allowed, reason).

    cfg keys consulted (defaults preserve legacy):
      max_trades_per_day, force_exit_time, vix_max
    plus state.day_bias and state.event_today (set elsewhere).
    """
    if not state.engine_running:
        return False, "Engine is not running"

    if state.position is not None:
        return False, "Position already open"

    max_trades = int(cfg.get("max_trades_per_day", MAX_TRADES_PER_DAY)) if cfg else MAX_TRADES_PER_DAY
    if state.trades_today >= max_trades:
        return False, f"Max trades for today reached ({max_trades})"

    # Sticky SL block — kept as defence-in-depth even when max=1.
    if state.trades_today >= 1 and state.first_trade_was_sl:
        return False, "Second entry blocked — first trade hit hard SL today"

    # Event-day skip (cfg-gated by `state.event_today` being set at engine start).
    if state.event_today:
        return False, f"EVENT_DAY:{state.event_today}"

    # VIX gate (no-op when vix_max is large or VIX unavailable).
    vix_max = float(cfg.get("vix_max", 999.0)) if cfg else 999.0
    if vix_max < 999.0:
        try:
            from services import vix_state
            vix_now = vix_state.get_vix_ltp()
            if vix_now > 0 and vix_now > vix_max:
                return False, f"VIX_HIGH:{vix_now:.1f}"
        except Exception:
            logger.debug("VIX lookup failed (non-fatal)", exc_info=True)

    # Day-bias gate — only blocks when explicitly NO_TRADE.
    if state.day_bias == "NO_TRADE":
        return False, "DAY_BIAS_NO_TRADE"

    t = _now_ist()
    if t < MARKET_WAIT:
        return False, f"Too early — wait until {MARKET_WAIT}"

    last_entry = LAST_ENTRY_TIME
    if t >= last_entry:
        return False, f"Past last entry time ({last_entry})"

    return True, ""


# ---------------------------------------------------------------------------
# Exit logic — tri-state return (NONE / FULL_EXIT / PARTIAL_EXIT)
# ---------------------------------------------------------------------------
def check_exit_conditions(
    position: PositionInfo,
    state: Optional[TradingState] = None,
    cfg: Optional[dict] = None,
) -> tuple[str, str, int]:
    """
    Evaluate all exit conditions in priority order.
    Mutates position trailing-stop fields in-place.

    Returns (action, reason, qty):
      action ∈ {"NONE", "FULL_EXIT", "PARTIAL_EXIT"}
      qty = partial leg quantity when action == PARTIAL_EXIT, else 0
    """
    # Resolve cfg-driven thresholds with legacy fallbacks
    force_exit = _parse_time(cfg.get("force_exit_time") if cfg else None, FORCE_EXIT_TIME)
    sl_premium_pct = float(cfg.get("sl_premium_pct", INITIAL_SL_PCT)) if cfg else INITIAL_SL_PCT
    sl_spot_pct = float(cfg.get("sl_spot_pct", 999.0)) if cfg else 999.0
    time_stop_min = int(cfg.get("time_stop_min", 999)) if cfg else 999
    hold_ceiling_min = int(cfg.get("hold_ceiling_min", 999)) if cfg else 999
    pb_enabled = bool(cfg.get("partial_book_enabled", False)) if cfg else False
    pb1_pct = float(cfg.get("partial_book_1_pct", 999.0)) if cfg else 999.0
    pb1_size = float(cfg.get("partial_book_1_size", 0.50)) if cfg else 0.50
    pb2_pct = float(cfg.get("partial_book_2_pct", 999.0)) if cfg else 999.0
    pb2_size = float(cfg.get("partial_book_2_size", 0.30)) if cfg else 0.30
    min_lots_for_pb = int(cfg.get("min_lots_for_partial_book", 3)) if cfg else 3
    lot_size = int(cfg.get("lot_size", 0)) if cfg else 0

    # 1. Force exit by wall-clock time
    if _now_ist() >= force_exit:
        return "FULL_EXIT", "TIME_EXIT", 0

    current = position.current_price
    if current <= 0:
        return "NONE", "", 0

    entry = position.entry_price
    pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0.0

    # 2. Hold ceiling
    if hold_ceiling_min < 999:
        elapsed_min = (_now_dt_ist() - position.entry_time).total_seconds() / 60.0
        if elapsed_min >= hold_ceiling_min:
            return "FULL_EXIT", "HOLD_CEILING", 0

    # 3. Time stop — require deadline AND non-positive PnL
    if (
        time_stop_min < 999
        and position.time_stop_deadline is not None
        and _now_dt_ist() >= position.time_stop_deadline
        and pnl_pct <= 0
    ):
        return "FULL_EXIT", "TIME_STOP", 0

    # 4. Spot-based SL (engine updates nifty_spot_low_seen / high_seen each tick)
    if sl_spot_pct < 999.0 and position.spot_sl_price > 0 and state is not None:
        spot = state.nifty_spot
        if spot > 0:
            if position.option_type == "CE" and spot <= position.spot_sl_price:
                return "FULL_EXIT", "SPOT_SL_HIT", 0
            if position.option_type == "PE" and spot >= position.spot_sl_price:
                return "FULL_EXIT", "SPOT_SL_HIT", 0

    # 5. Partial booking gates — fire BEFORE trailing-stop updates so they
    #    aren't overshadowed by a tightened trail SL.
    if pb_enabled:
        original_qty = position.qty
        enough_lots = lot_size > 0 and original_qty >= min_lots_for_pb * lot_size
        if (
            enough_lots
            and not position.partial_book_1_hit
            and pnl_pct >= pb1_pct
        ):
            leg_qty = max(int(round(original_qty * pb1_size)), lot_size)
            leg_qty = min(leg_qty, position.qty_remaining or original_qty)
            return "PARTIAL_EXIT", "PARTIAL_BOOK_1", leg_qty
        if (
            enough_lots
            and not position.partial_book_2_hit
            and position.partial_book_1_hit
            and pnl_pct >= pb2_pct
        ):
            leg_qty = max(int(round(original_qty * pb2_size)), lot_size)
            leg_qty = min(leg_qty, position.qty_remaining or original_qty)
            return "PARTIAL_EXIT", "PARTIAL_BOOK_2", leg_qty

    # 6. Trailing stop update (mutates position state)
    _update_trailing_stop(position, current, pnl_pct, cfg)

    # 7. Premium SL check — covers initial SL and active trailing SL
    if current <= position.trailing_sl_price:
        if position.trail_active:
            logger.info(
                "TRAILING SL HIT | sl=%.2f current=%.2f pnl=%.1f%%",
                position.trailing_sl_price, current, pnl_pct,
            )
            return "FULL_EXIT", "TRAILING_STOP", 0
        else:
            logger.info(
                "STOPLOSS HIT | entry=%.2f current=%.2f pnl=%.1f%%",
                entry, current, pnl_pct,
            )
            return "FULL_EXIT", "STOPLOSS_HIT", 0

    # 8. Premium-% safety net (legacy fallback when cfg has sl_premium_pct)
    if sl_premium_pct > 0 and pnl_pct <= -abs(sl_premium_pct):
        return "FULL_EXIT", "STOPLOSS_HIT", 0

    return "NONE", "", 0


def _update_trailing_stop(
    position: PositionInfo,
    current: float,
    pnl_pct: float,
    cfg: Optional[dict] = None,
) -> None:
    """
    Mutates position trailing-stop fields. Called only from monitoring loop
    under lock. Reads cfg["trail_*"] keys; falls back to legacy constants.
    """
    trigger = float(cfg.get("trail_trigger", TRAIL_TRIGGER)) if cfg else TRAIL_TRIGGER
    gap_base = float(cfg.get("trail_gap_base", TRAIL_GAP_BASE)) if cfg else TRAIL_GAP_BASE
    gap_step = float(cfg.get("trail_gap_step", TRAIL_GAP_STEP)) if cfg else TRAIL_GAP_STEP
    gap_min = float(cfg.get("trail_gap_min", TRAIL_GAP_MIN)) if cfg else TRAIL_GAP_MIN

    if pnl_pct < trigger:
        return

    position.trail_active = True

    if current > position.highest_price_seen:
        position.highest_price_seen = current

    extra_steps = int((pnl_pct - trigger) / 10)
    trail_gap = gap_base - extra_steps * gap_step
    trail_gap = max(trail_gap, gap_min)

    new_trail_sl = position.highest_price_seen * (1 - trail_gap / 100)

    if new_trail_sl > position.trailing_sl_price:
        position.trailing_sl_price = new_trail_sl
        logger.info(
            "TRAIL SL UPDATED | pnl=+%.1f%% gap=%.1f%% highest=%.2f sl=%.2f",
            pnl_pct, trail_gap, position.highest_price_seen, position.trailing_sl_price,
        )


# ---------------------------------------------------------------------------
# P&L calculator
# ---------------------------------------------------------------------------
def calculate_pnl(entry_price: float, current_price: float, qty: int) -> dict:
    pnl_points = current_price - entry_price
    pnl_rupees = pnl_points * qty
    pnl_pct = (pnl_points / entry_price * 100) if entry_price > 0 else 0
    return {
        "entry_price":   round(entry_price, 2),
        "current_price": round(current_price, 2),
        "pnl_points":    round(pnl_points, 2),
        "pnl_rupees":    round(pnl_rupees, 2),
        "pnl_pct":       round(pnl_pct, 2),
        "qty":           qty,
    }
