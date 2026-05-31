"""NIFTY Futures (ORB) — REST endpoints. Mounted at /auto-trading/nifty-fut/..."""
import asyncio
import logging
import os
import time as _time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from kiteconnect.exceptions import TokenException, NetworkException

from services.kite_service import require_authenticated_client
from services.nifty_fut_engine import get_nifty_fut_engine
from services.nifty_fut_paper_trade import read_trades_fut, get_summary_fut, CSV_PATH as FUT_CSV_PATH

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auto-trading/nifty-fut", tags=["nifty-fut"])

_STATUS_CACHE: dict[str, tuple[float, dict]] = {}
_STATUS_TTL = 1.0


def _get_kite():
    try:
        return require_authenticated_client()
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.post("/start")
async def start_fut():
    engine = get_nifty_fut_engine()
    if engine.get_status()["engine_running"]:
        raise HTTPException(status_code=400, detail="NIFTY Futures engine is already running")
    kite = _get_kite()
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, engine.start, kite)
    except TokenException:
        raise HTTPException(status_code=401, detail="Token expired. Re-authenticate via /login.")
    except NetworkException as e:
        raise HTTPException(status_code=503, detail=f"Kite API unreachable: {e}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("NIFTY_FUT engine start failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Engine start failed: {e}")
    return {
        "status": "started",
        "strategy": "NIFTY_FUTURES_ORB_V1",
        "mode": info["mode"],
        "message": (
            f"NIFTY Futures ORB engine running in {info['mode']} mode on "
            f"{info['futures_symbol']} (lot={info['lot_size']}). "
            "OR = 09:15–09:25; breakout entries 09:35–11:30; SL −30pts, target +70pts."
        ),
        "instruments": {"futures_symbol": info["futures_symbol"], "lot_size": info["lot_size"]},
    }


@router.post("/stop")
async def stop_fut():
    engine = get_nifty_fut_engine()
    if not engine.get_status()["engine_running"]:
        raise HTTPException(status_code=400, detail="NIFTY Futures engine is not running")
    kite = _get_kite()
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, engine.stop, kite)
    except Exception as e:
        logger.error("NIFTY_FUT engine stop error: %s", e)
        raise HTTPException(status_code=500, detail=f"Error stopping engine: {e}")
    final = engine.get_status()
    return {
        "status": "stopped",
        "instrument": "NIFTY_FUT",
        "trades_today": final["trades_today"],
        "exit_reason": final["exit_reason"],
        "final_pnl": final["pnl"],
    }


@router.get("/status")
async def get_fut_status():
    now = _time.monotonic()
    cached = _STATUS_CACHE.get("NIFTY_FUT")
    if cached and now - cached[0] < _STATUS_TTL:
        return cached[1]
    result = get_nifty_fut_engine().get_status()
    _STATUS_CACHE["NIFTY_FUT"] = (now, result)
    return result


@router.get("/candles")
async def get_fut_candles():
    """5-min futures candles with per-candle VWAP/EMA20/RSI14 for the live chart."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from services.indicators import compute_ema, compute_rsi
    from services.market_data import get_market_data_service
    IST = ZoneInfo("Asia/Kolkata")
    IST_OFFSET = 19800  # 5.5h — align epoch so the chart renders IST wall-clock

    state = get_nifty_fut_engine()._state_mgr.get_state()
    candles = state.candles
    if not candles:
        return {"candles": [], "live_candle": None}

    today = candles[-1].timestamp.date()
    closes = [c.close for c in candles]
    ema20 = compute_ema(closes, 20)
    rsi14 = compute_rsi(closes, 14)

    cum_tp = cum_vol = 0.0
    out = []
    for i, c in enumerate(candles):
        is_today = c.timestamp.date() == today
        vwap_val = None
        if is_today:
            tp = (c.high + c.low + c.close) / 3.0
            cum_tp += tp * c.volume
            cum_vol += c.volume
            vwap_val = round(cum_tp / cum_vol, 2) if cum_vol > 0 else None
        out.append({
            "time": int(c.timestamp.timestamp()) + IST_OFFSET,
            "open": c.open, "high": c.high, "low": c.low, "close": c.close,
            "volume": c.volume,
            "ema20": round(ema20[i], 2) if ema20[i] is not None else None,
            "rsi14": round(rsi14[i], 2) if rsi14[i] is not None else None,
            "vwap": vwap_val, "is_today": is_today,
        })

    live_candle = None
    raw_live = get_market_data_service().get_live_candle("NIFTY_FUT")
    if raw_live and raw_live["timestamp"]:
        ts = raw_live["timestamp"]
        now_ist = _dt.now(IST)
        if ts.date() == now_ist.date() and ts.hour >= 9:
            live_candle = {
                "time": int(ts.timestamp()) + IST_OFFSET,
                "open": raw_live["open"], "high": raw_live["high"],
                "low": raw_live["low"], "close": raw_live["close"],
                "volume": raw_live["volume"],
                "ema20": None, "rsi14": None, "vwap": None,
                "is_today": True, "is_live": True,
            }
    return {"candles": out, "live_candle": live_candle}


@router.get("/paper-log")
async def get_fut_paper_log():
    return {"trades": read_trades_fut(), "summary": get_summary_fut()}


@router.get("/paper-log/download")
async def download_fut_paper_log():
    if not os.path.exists(FUT_CSV_PATH):
        raise HTTPException(status_code=404, detail="No NIFTY Futures paper trades logged yet")
    return FileResponse(path=FUT_CSV_PATH, media_type="text/csv",
                        filename="paper_trades_nifty_fut.csv")
