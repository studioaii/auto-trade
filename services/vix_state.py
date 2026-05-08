"""
India VIX state singleton.

VIX is a single shared signal — not per-instrument — so it lives in its
own state module rather than in `TradingState`. Subscribed via the
shared MarketDataService at app startup.

Token: 264969 (NSE: India VIX)
"""
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class _VixState:
    ltp: float = 0.0
    last_tick_at: Optional[datetime] = None


_state = _VixState()
_lock = threading.Lock()


def set_vix_ltp(value: float) -> None:
    """Called from the market-data tick callback."""
    if value <= 0:
        return
    with _lock:
        _state.ltp = float(value)
        _state.last_tick_at = datetime.now(IST)


def get_vix_ltp() -> float:
    with _lock:
        return _state.ltp


def get_vix_snapshot() -> dict:
    """Returns a dict snapshot for status endpoints."""
    with _lock:
        return {
            "ltp": _state.ltp,
            "last_tick_at": _state.last_tick_at.isoformat() if _state.last_tick_at else None,
        }
