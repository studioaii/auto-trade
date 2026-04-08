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

# Dynamic SL bounds (applied to option premium %)
SL_FLOOR_PCT        = 12.0   # Minimum SL — tighter risks noise stop-out on options
SL_CEILING_PCT      = 22.0   # Default ceiling for normal-IV days (premium < 150)
SL_CEILING_HIGH_IV  = 28.0   # Ceiling for high-IV days (premium 150–199)
SL_CEILING_VERY_HIGH_IV = 35.0  # Ceiling for very-high-IV days (premium ≥ 200)
ATM_DELTA           = 0.5    # ATM option delta approximation for Nifty → option conversion

# Trailing SL (activates at +20%, widens by 4% per additional 10% gain)
TRAIL_TRIGGER       = 20.0   # % gain at which trailing activates
TRAIL_GAP_BASE      = 8.0    # Starting trail gap (% below peak) at +20%
TRAIL_GAP_STEP      = 4.0    # Gap increase per additional 10% gain
TRAIL_GAP_MAX       = 20.0   # Cap trail gap so we don't give back too much on huge moves

MAX_TRADES_PER_DAY  = 2
FORCE_EXIT_TIME     = time(15, 20)
LAST_ENTRY_TIME     = time(14, 0)
MARKET_WAIT         = time(9, 50)


def _now_ist() -> time:
    return datetime.now(IST).time()


def _adaptive_sl_ceiling(entry_price: float) -> float:
    """
    Return the SL ceiling % appropriate for the current IV environment,
    proxied by the ATM option premium.

    Higher premium = elevated IV = market expects a bigger move, so the
    Nifty structure range (and thus the raw sl_pct) will naturally be wider.
    Raising the ceiling on those days lets the strategy trade while still
    capping risk at a sensible absolute level.

    premium < 150  → 22%  (normal IV)
    premium 150–199 → 28%  (high IV)
    premium ≥ 200  → 35%  (very high IV / event days)
    """
    if entry_price >= 200:
        return SL_CEILING_VERY_HIGH_IV
    if entry_price >= 150:
        return SL_CEILING_HIGH_IV
    return SL_CEILING_PCT


# ---------------------------------------------------------------------------
# Dynamic SL computation (called at entry)
# ---------------------------------------------------------------------------
def compute_dynamic_sl(
    candles: list,
    option_type: str,
    nifty_spot: float,
    entry_price: float,
) -> tuple[float | None, float]:
    """
    Compute dynamic SL price from Nifty candle structure.

    Approach:
    - CE: SL anchored just below the lowest low of the last 3 candles
    - PE: SL anchored just above the highest high of the last 3 candles
    - Nifty point distance converted to option % via ATM delta
    - Floor: SL_FLOOR_PCT (12%) — avoids noise stop-outs on options
    - Ceiling: adaptive (22% / 28% / 35%) based on option premium level

    Returns (sl_price, sl_pct).
    sl_price is None if SL is too wide (trade should be skipped).
    sl_pct is always returned so the caller can log it even on skip.
    """
    if entry_price <= 0:
        return None, 0.0

    ceiling = _adaptive_sl_ceiling(entry_price)

    if len(candles) < 3:
        # Not enough candles for structure — use floor
        sl_pct = SL_FLOOR_PCT
        return entry_price * (1 - sl_pct / 100), sl_pct

    if option_type == "CE":
        structure_level = min(c.low for c in candles[-3:]) * 0.995   # 0.5% below the low
        nifty_sl_points = max(nifty_spot - structure_level, 0)
    else:  # PE
        structure_level = max(c.high for c in candles[-3:]) * 1.005  # 0.5% above the high
        nifty_sl_points = max(structure_level - nifty_spot, 0)

    # Convert Nifty points → option premium points using ATM delta
    option_sl_points = nifty_sl_points * ATM_DELTA
    sl_pct = (option_sl_points / entry_price) * 100

    if sl_pct > ceiling:
        logger.info(
            "Dynamic SL too wide (%.1f%% > ceiling %.1f%%) — skipping | "
            "structure=%.2f spot=%.2f entry=%.2f",
            sl_pct, ceiling, structure_level, nifty_spot, entry_price,
        )
        return None, sl_pct

    sl_pct = max(sl_pct, SL_FLOOR_PCT)
    sl_price = entry_price * (1 - sl_pct / 100)

    logger.info(
        "Dynamic SL | type=%s ceiling=%.1f%% structure=%.2f "
        "nifty_pts=%.1f opt_pts=%.1f sl_pct=%.1f%% sl_price=%.2f",
        option_type, ceiling, structure_level,
        nifty_sl_points, option_sl_points, sl_pct, sl_price,
    )
    return sl_price, sl_pct


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
    - Activates when pnl_pct >= TRAIL_TRIGGER (20%)
    - Starting gap: TRAIL_GAP_BASE (8%) below peak
    - Each additional 10% gain widens the gap by TRAIL_GAP_STEP (4%)
      e.g. +20% → 8%, +30% → 12%, +40% → 16%, +50% → 20% (capped)
    - Trail SL only moves up, never down
    """
    if pnl_pct < TRAIL_TRIGGER:
        return

    position.trail_active = True

    # Track the highest price seen
    if current > position.highest_price_seen:
        position.highest_price_seen = current

    # Dynamic gap: widens by TRAIL_GAP_STEP for each 10% above TRAIL_TRIGGER
    extra_steps = int((pnl_pct - TRAIL_TRIGGER) / 10)
    trail_gap = TRAIL_GAP_BASE + extra_steps * TRAIL_GAP_STEP
    trail_gap = min(trail_gap, TRAIL_GAP_MAX)

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
