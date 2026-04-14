"""
NIFTY_INTRADAY_VWAP_EMA_BREAKOUT — Risk Management
All parameters tunable at the top.
"""
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from services.trading_state import TradingState, PositionInfo, get_raw_state

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Strategy risk parameters
# ---------------------------------------------------------------------------

INITIAL_SL_PCT      = 20.0   # Fixed SL — 20% below entry price

# Trailing SL (activates at +15%, tightens by 1% per additional 10% gain)
TRAIL_TRIGGER       = 15.0   # % gain at which trailing activates
TRAIL_GAP_BASE      = 6.0    # Starting trail gap (% below peak) at +15%
TRAIL_GAP_STEP      = 1.0    # Gap decrease per additional 10% gain (tightens as profit grows)
TRAIL_GAP_MIN       = 3.0    # Floor — never trail looser than 3% below peak

MAX_TRADES_PER_DAY  = 2
FORCE_EXIT_TIME     = time(15, 20)
LAST_ENTRY_TIME     = time(14, 0)
MARKET_WAIT         = time(9, 50)


def _now_ist() -> time:
    return datetime.now(IST).time()


# ---------------------------------------------------------------------------
# Entry gate
# ---------------------------------------------------------------------------
def can_enter_trade(state: TradingState) -> tuple[bool, str]:
    """Gatekeeper before any entry. Returns (allowed, reason)."""
    if not state.engine_running:
        return False, "Engine is not running"

    if state.position is not None:
        return False, "Position already open"

    if state.trades_today >= MAX_TRADES_PER_DAY:
        return False, f"Max trades for today reached ({MAX_TRADES_PER_DAY})"

    # Block second entry if first trade was stopped out at hard SL.
    if state.trades_today >= 1 and state.exit_reason == "STOPLOSS_HIT":
        return False, "Second entry blocked — first trade hit hard SL today"

    t = _now_ist()
    if t < MARKET_WAIT:
        return False, f"Too early — wait until {MARKET_WAIT}"

    if t >= LAST_ENTRY_TIME:
        return False, f"Past last entry time ({LAST_ENTRY_TIME})"

    return True, ""


# ---------------------------------------------------------------------------
# Exit logic with dynamic trailing stop
# ---------------------------------------------------------------------------
def check_exit_conditions(position: PositionInfo) -> tuple[bool, str]:
    """
    Evaluate all exit conditions in priority order.
    Also updates trailing stop on the position object in-place.
    Returns (should_exit, reason).
    """
    # 1. Force exit by time
    if _now_ist() >= FORCE_EXIT_TIME:
        return True, "TIME_EXIT"

    current = position.current_price
    if current <= 0:
        return False, ""

    entry   = position.entry_price
    pnl_pct = (current - entry) / entry * 100

    # 2. Update trailing stop state (mutates position in-place)
    _update_trailing_stop(position, current, pnl_pct)

    # 3. SL hit — covers both initial dynamic SL and active trailing SL
    #    trailing_sl_price starts as the dynamic SL set at entry and only ever moves up
    if current <= position.trailing_sl_price:
        if position.trail_active:
            logger.info(
                "TRAILING SL HIT | sl=%.2f current=%.2f pnl=%.1f%%",
                position.trailing_sl_price, current, pnl_pct,
            )
            return True, "TRAILING_STOP"
        else:
            logger.info(
                "STOPLOSS HIT | entry=%.2f current=%.2f pnl=%.1f%%",
                entry, current, pnl_pct,
            )
            return True, "STOPLOSS_HIT"

    return False, ""


def _update_trailing_stop(position: PositionInfo, current: float, pnl_pct: float) -> None:
    """
    Mutates position trailing stop fields. Called only from monitoring loop under lock.

    Trail logic:
    - Activates when pnl_pct >= TRAIL_TRIGGER (15%)
    - Starting gap: TRAIL_GAP_BASE (6%) below peak
    - Each additional 10% gain tightens the gap by TRAIL_GAP_STEP (1%)
      e.g. +15% → 6%, +25% → 5%, +35% → 4%, +45% → 3% (floored)
    - Trail SL only moves up, never down
    """
    if pnl_pct < TRAIL_TRIGGER:
        return

    position.trail_active = True

    # Track the highest price seen
    if current > position.highest_price_seen:
        position.highest_price_seen = current

    # Dynamic gap: tightens by TRAIL_GAP_STEP for each 10% above TRAIL_TRIGGER
    extra_steps = int((pnl_pct - TRAIL_TRIGGER) / 10)
    trail_gap = TRAIL_GAP_BASE - extra_steps * TRAIL_GAP_STEP
    trail_gap = max(trail_gap, TRAIL_GAP_MIN)

    new_trail_sl = position.highest_price_seen * (1 - trail_gap / 100)

    # SL only moves up
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
