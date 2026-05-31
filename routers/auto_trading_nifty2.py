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
        "strategy": "NIFTY_2_SIMPLE_EARLY_ENTRY_V1",
        "mode":     info["mode"],
        "message":  (
            f"NIFTY 2.0 engine running in {info['mode']} mode. "
            f"ATM={info['atm_strike']} expiry={info['expiry']}. "
            "OR locks 09:30; entries 09:35–13:30; target +12%, SL −10%."
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
