"""
Backtest API router.
POST /backtest/run  — run strategy replay for a past date
"""
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.backtest_engine import get_backtest_engine
from services.kite_service import require_authenticated_client

router = APIRouter(prefix="/backtest", tags=["backtest"])
logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class BacktestRequest(BaseModel):
    date: str   # "YYYY-MM-DD"


class MultiBacktestRequest(BaseModel):
    from_date: str   # "YYYY-MM-DD"
    to_date: str     # "YYYY-MM-DD"


@router.get("/debug-data")
def debug_historical_data():
    """Temporary: test if kite.historical_data() works at all."""
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    from services.instruments import NIFTY_INDEX_TOKEN
    IST2 = ZoneInfo("Asia/Kolkata")
    try:
        kite = require_authenticated_client()
    except Exception as e:
        return {"auth": "FAILED", "error": str(e)}
    try:
        raw = kite.historical_data(
            instrument_token=NIFTY_INDEX_TOKEN,
            from_date=dt(2026, 3, 19, 9, 0, 0, tzinfo=IST2),
            to_date=dt(2026, 3, 19, 10, 0, 0, tzinfo=IST2),
            interval="5minute",
        )
        return {"auth": "OK", "candles_returned": len(raw), "sample": raw[:2] if raw else []}
    except Exception as e:
        return {"auth": "OK", "historical_data_error": str(e), "type": type(e).__name__}


@router.post("/run")
def run_backtest(req: BacktestRequest):
    try:
        trade_date = date.fromisoformat(req.date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date '{req.date}'. Use YYYY-MM-DD format.")

    today = datetime.now(IST).date()
    if trade_date >= today:
        raise HTTPException(status_code=400, detail="Backtest date must be a past date.")

    try:
        kite = require_authenticated_client()
    except Exception:
        raise HTTPException(status_code=401, detail="Not authenticated. Please login via /login first.")

    result = get_backtest_engine().run(kite, trade_date)

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return result


@router.post("/run-multi")
def run_multi_backtest(req: MultiBacktestRequest):
    try:
        from_dt = date.fromisoformat(req.from_date)
        to_dt   = date.fromisoformat(req.to_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    today = datetime.now(IST).date()
    if to_dt >= today:
        raise HTTPException(status_code=400, detail="to_date must be a past date.")
    if from_dt > to_dt:
        raise HTTPException(status_code=400, detail="from_date must be before to_date.")
    if (to_dt - from_dt).days > 90:
        raise HTTPException(status_code=400, detail="Maximum 90 days range allowed.")

    try:
        kite = require_authenticated_client()
    except Exception:
        raise HTTPException(status_code=401, detail="Not authenticated. Please login via /login first.")

    result = get_backtest_engine().run_multi(kite, from_dt, to_dt)
    return result
