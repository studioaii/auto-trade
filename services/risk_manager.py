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
# Strategy risk parameters — v2 (optimised from backtest analysis)
# ---------------------------------------------------------------------------
STOPLOSS_PCT        = 20.0    # Tighter SL: 20% (was 25%) — limits damage per loss
PROFIT_TARGET_PCT   = 35.0    # Lower target: 35% (was 40%) — more achievable, locks more wins
BREAKEVEN_TRIGGER   = 15.0    # Faster breakeven: 15% (was 20%) — protect gains sooner
TRAIL_TRIGGER       = 20.0    # Earlier trail: 20% (was 30%) — capture more profitable exits
TRAIL_SL_PCT        = 10.0    # Tighter trail: 10% below peak (was 15%) — locks more profit

MAX_TRADES_PER_DAY  = 2
FORCE_EXIT_TIME     = time(15, 20)
LAST_ENTRY_TIME     = time(14, 0)    # Aligned with strategy.py (was 14:30)
MARKET_WAIT         = time(9, 50)    # Skip opening noise (was 9:20)


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
    # Backtest shows: 12 "double bad" days vs only 9 "recovery" days over 15 months.
    # Eliminating double-SL days cuts max drawdown by 30% (572→402 pts).
    if state.trades_today >= 1 and state.exit_reason == "STOPLOSS_HIT":
        return False, "Second entry blocked — first trade hit hard SL today"

    t = _now_ist()
    if t < MARKET_WAIT:
        return False, f"Too early — wait until {MARKET_WAIT}"

    if t >= LAST_ENTRY_TIME:
        return False, f"Past last entry time ({LAST_ENTRY_TIME})"

    return True, ""


# ---------------------------------------------------------------------------
# Exit logic with trailing stop
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

    # 2. Primary target hit
    if pnl_pct >= PROFIT_TARGET_PCT:
        logger.info("TARGET HIT | entry=%.2f current=%.2f pnl=+%.1f%%", entry, current, pnl_pct)
        return True, "TARGET_HIT"

    # 3. Update trailing stop state (mutates position in-place — called only from monitoring loop under lock)
    _update_trailing_stop(position, current, pnl_pct)

    # 4. Trailing SL hit (after updating)
    if position.trail_active and current <= position.trailing_sl_price:
        logger.info("TRAILING SL HIT | sl=%.2f current=%.2f", position.trailing_sl_price, current)
        return True, "TRAILING_STOP"

    # 5. Breakeven SL hit (when SL moved to cost, price dipped back)
    if position.breakeven_set and not position.trail_active and current <= entry:
        logger.info("BREAKEVEN SL HIT | entry=%.2f current=%.2f", entry, current)
        return True, "BREAKEVEN_EXIT"

    # 6. Initial hard SL
    if not position.breakeven_set and pnl_pct <= -STOPLOSS_PCT:
        logger.info("STOPLOSS HIT | entry=%.2f current=%.2f pnl=%.1f%%", entry, current, pnl_pct)
        return True, "STOPLOSS_HIT"

    return False, ""


def _update_trailing_stop(position: PositionInfo, current: float, pnl_pct: float) -> None:
    """Mutates position trailing stop fields. Called only from monitoring loop."""
    # Move SL to breakeven at +20%
    if not position.breakeven_set and pnl_pct >= BREAKEVEN_TRIGGER:
        position.breakeven_set = True
        position.trailing_sl_price = position.entry_price
        logger.info("BREAKEVEN SET | SL moved to entry price %.2f", position.entry_price)

    # Activate trailing at +30%
    if pnl_pct >= TRAIL_TRIGGER:
        position.trail_active = True
        if current > position.highest_price_seen:
            position.highest_price_seen = current
            new_trail_sl = current * (1 - TRAIL_SL_PCT / 100)
            if new_trail_sl > position.trailing_sl_price:
                position.trailing_sl_price = new_trail_sl
                logger.info(
                    "TRAIL SL UPDATED | highest=%.2f sl=%.2f",
                    position.highest_price_seen, position.trailing_sl_price
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
