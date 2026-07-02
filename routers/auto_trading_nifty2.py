"""
NIFTY 2.0 — REST endpoints.

Mirrors the BankNifty 2.0 routes but targets the dedicated NIFTY 2.0 engine
and CSV files. Mounted at /auto-trading/nifty2/...
"""
import asyncio
import logging
import os
import time as _time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from kiteconnect.exceptions import TokenException, NetworkException

from services.kite_service import require_authenticated_client
from services.nifty_engine_v2 import get_nifty2_engine
from services.nifty_paper_trade_v2 import read_trades_n2, get_summary_n2, CSV_PATH as N2_CSV_PATH
from services.nifty_candle_logger_v2 import list_log_files_n2, LOG_DIR as N2_LOG_DIR
from services.nifty_instrumentation_v2 import POST_EXIT_CSV as N2_POST_EXIT_CSV, SHADOW_CSV as N2_SHADOW_CSV

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auto-trading/nifty2", tags=["nifty2"])

_STATUS_CACHE: dict[str, tuple[float, dict]] = {}
_STATUS_TTL = 1.0


def _get_kite():
    try:
        return require_authenticated_client()
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.post("/start")
async def start_n2():
    engine = get_nifty2_engine()
    state = engine.get_status()
    if state["engine_running"]:
        raise HTTPException(status_code=400, detail="NIFTY 2.0 engine is already running")
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
        logger.error("N2 engine start failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Engine start failed: {e}")
    return {
        "status":   "started",
        "strategy": "NIFTY_2_VWAP_EMA_BREAKOUT_V1PLUS",
        "mode":     info["mode"],
        "message":  (
            f"NIFTY 2.0 engine running in {info['mode']} mode. "
            f"ATM={info['atm_strike']} expiry={info['expiry']}. "
            "VWAP+EMA breakout (v1 + improvements): entries 09:50–14:00, "
            "session chop gate, SL −18%, trailing from +15%, force exit 15:20."
        ),
        "instruments": {"ce": info["ce"], "pe": info["pe"]},
    }


@router.post("/stop")
async def stop_n2():
    engine = get_nifty2_engine()
    state = engine.get_status()
    if not state["engine_running"]:
        raise HTTPException(status_code=400, detail="NIFTY 2.0 engine is not running")
    kite = _get_kite()
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, engine.stop, kite)
    except Exception as e:
        logger.error("N2 engine stop error: %s", e)
        raise HTTPException(status_code=500, detail=f"Error stopping engine: {e}")
    final = engine.get_status()
    return {
        "status":       "stopped",
        "instrument":   "NIFTY_2",
        "trades_today": final["trades_today"],
        "exit_reason":  final["exit_reason"],
        "final_pnl":    final["pnl"],
    }


@router.get("/status")
async def get_n2_status():
    now = _time.monotonic()
    cached = _STATUS_CACHE.get("NIFTY_2")
    if cached and now - cached[0] < _STATUS_TTL:
        return cached[1]
    result = get_nifty2_engine().get_status()
    _STATUS_CACHE["NIFTY_2"] = (now, result)
    return result


@router.get("/paper-log")
async def get_n2_paper_log():
    return {
        "trades":  read_trades_n2(),
        "summary": get_summary_n2(),
    }


@router.get("/paper-log/download")
async def download_n2_paper_log():
    if not os.path.exists(N2_CSV_PATH):
        raise HTTPException(status_code=404, detail="No NIFTY 2.0 paper trades logged yet")
    return FileResponse(path=N2_CSV_PATH, media_type="text/csv", filename="paper_trades_nifty_2.csv")


@router.get("/candle-log/list")
async def list_n2_candle_logs():
    return {"files": list_log_files_n2()}


@router.get("/candle-log/download/{date}")
async def download_n2_candle_log(date: str):
    path = os.path.join(N2_LOG_DIR, f"nifty2_candles_{date}.csv")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"No NIFTY 2.0 candle log for {date}")
    return FileResponse(path=path, media_type="text/csv",
                        filename=f"nifty2_candles_{date}.csv")


# ---------------------------------------------------------------------------
# Live chart data — 5-min candles + per-candle indicators (mirrors v1)
# ---------------------------------------------------------------------------

@router.get("/candles")
async def get_n2_candles():
    """Return 5-min candles with per-candle EMA20/RSI14/VWAP for the chart."""
    from services.indicators import compute_ema, compute_rsi
    from services.market_data import get_market_data_service
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    IST = ZoneInfo("Asia/Kolkata")
    IST_OFFSET = 19800   # 5.5 * 3600 — chart lib expects epoch shifted to IST wall-clock

    engine = get_nifty2_engine()
    state  = engine._state_mgr.get_state()
    candles = state.candles
    if not candles:
        return {"candles": [], "live_candle": None}

    today  = candles[-1].timestamp.date()
    closes = [c.close for c in candles]
    ema20_series = compute_ema(closes, 20)
    rsi14_series = compute_rsi(closes, 14)

    vwap_cum_tp = 0.0
    vwap_cum_vol = 0.0
    result = []
    for i, c in enumerate(candles):
        is_today = c.timestamp.date() == today
        vwap_val = None
        if is_today:
            tp = (c.high + c.low + c.close) / 3.0
            vwap_cum_tp  += tp * c.volume
            vwap_cum_vol += c.volume
            vwap_val = round(vwap_cum_tp / vwap_cum_vol, 2) if vwap_cum_vol > 0 else None
        result.append({
            "time":     int(c.timestamp.timestamp()) + IST_OFFSET,
            "open":     c.open, "high": c.high, "low": c.low, "close": c.close,
            "volume":   c.volume,
            "ema20":    round(ema20_series[i], 2) if ema20_series[i] is not None else None,
            "rsi14":    round(rsi14_series[i], 2) if rsi14_series[i] is not None else None,
            "vwap":     vwap_val,
            "is_today": is_today,
        })

    live_candle = None
    mds = get_market_data_service()
    raw_live = mds.get_live_candle("NIFTY_2")
    if raw_live and raw_live.get("timestamp"):
        ts = raw_live["timestamp"]
        now_ist = _dt.now(IST)
        if ts.date() == now_ist.date() and ts.hour >= 9:
            live_candle = {
                "time":     int(ts.timestamp()) + IST_OFFSET,
                "open":     raw_live["open"], "high": raw_live["high"],
                "low":      raw_live["low"],  "close": raw_live["close"],
                "volume":   raw_live["volume"],
                "ema20":    None, "rsi14": None, "vwap": None,
                "is_today": True, "is_live": True,
            }
    return {"candles": result, "live_candle": live_candle}


# ---------------------------------------------------------------------------
# Instrumentation log downloads (post-exit option path + blocked-signal shadow)
# ---------------------------------------------------------------------------

@router.get("/instrumentation/post-exit/download")
async def download_n2_post_exit():
    if not os.path.exists(N2_POST_EXIT_CSV):
        raise HTTPException(status_code=404, detail="No NIFTY 2.0 post-exit data yet")
    return FileResponse(path=N2_POST_EXIT_CSV, media_type="text/csv",
                        filename="post_exit_paths_nifty_2.csv")


@router.get("/instrumentation/shadow/download")
async def download_n2_shadow():
    if not os.path.exists(N2_SHADOW_CSV):
        raise HTTPException(status_code=404, detail="No NIFTY 2.0 shadow-signal data yet")
    return FileResponse(path=N2_SHADOW_CSV, media_type="text/csv",
                        filename="shadow_signals_nifty_2.csv")
