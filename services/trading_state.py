import threading
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class PositionInfo:
    option_symbol: str
    instrument_token: int
    option_type: str        # "CE" or "PE"
    strike: int
    expiry: date
    entry_price: float
    qty: int
    order_id: str           # "PAPER-001" in paper mode
    entry_time: datetime
    reason_for_entry: str   # human-readable signal description
    current_price: float = 0.0
    # Trailing stop tracking
    trailing_sl_price: float = 0.0      # current dynamic SL price
    highest_price_seen: float = 0.0     # for trail calculation
    trail_active: bool = False          # True when profit >= 30%
    breakeven_set: bool = False         # True when SL moved to cost
    # Indicator snapshot at entry — stored for CSV analysis
    nifty_spot_entry: float = 0.0       # Nifty index level when entering
    vwap_entry: float = 0.0
    ema20_entry: Optional[float] = None
    rsi14_entry: Optional[float] = None
    market_state_entry: str = "UNKNOWN"
    efficiency_entry: float = 0.0       # directional efficiency ratio at entry


@dataclass
class TradingState:
    engine_running: bool = False
    trading_mode: str = "PAPER"         # "PAPER" or "LIVE"
    trades_today: int = 0               # max 2 per day
    trade_done: bool = False            # True when max trades hit
    position: Optional[PositionInfo] = None
    candles: list = field(default_factory=list)   # List[Candle]
    nifty_spot: float = 0.0
    ce_ltp: float = 0.0                 # live ATM CE price
    pe_ltp: float = 0.0                 # live ATM PE price
    last_signal: str = "NO_SIGNAL"
    market_state: str = "UNKNOWN"       # "TRENDING" | "SIDEWAYS" | "UNKNOWN"
    last_candle_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None
    pnl: Optional[dict] = None
    error_message: Optional[str] = None
    # VWAP accumulator (reset daily)
    vwap_cum_tp_vol: float = 0.0        # cumulative (typical_price * volume)
    vwap_cum_vol: float = 0.0           # cumulative volume


# ---------------------------------------------------------------------------
# Module-level singleton + lock
# ---------------------------------------------------------------------------
_state = TradingState()
_lock = threading.Lock()


def get_state() -> TradingState:
    """Return a shallow copy for safe reading (no lock held during use)."""
    with _lock:
        import copy
        return copy.copy(_state)


def update_state(**kwargs) -> None:
    """Thread-safe field update."""
    with _lock:
        for key, value in kwargs.items():
            if hasattr(_state, key):
                setattr(_state, key, value)
            else:
                logger.warning("TradingState has no field: %s", key)


def get_lock() -> threading.Lock:
    return _lock


def get_raw_state() -> TradingState:
    """Direct reference — caller MUST hold _lock."""
    return _state


def reset_daily_state(mode: str = "PAPER") -> None:
    """Reset all trade-related fields at engine start each morning."""
    with _lock:
        _state.trading_mode = mode
        _state.trades_today = 0
        _state.trade_done = False
        _state.position = None
        _state.candles = []
        _state.nifty_spot = 0.0
        _state.ce_ltp = 0.0
        _state.pe_ltp = 0.0
        _state.last_signal = "NO_SIGNAL"
        _state.market_state = "UNKNOWN"
        _state.last_candle_time = None
        _state.exit_reason = None
        _state.exit_price = None
        _state.pnl = None
        _state.error_message = None
        _state.vwap_cum_tp_vol = 0.0
        _state.vwap_cum_vol = 0.0
    logger.info("Daily trading state reset | mode=%s", mode)
