import logging
import asyncio
import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from kiteconnect.exceptions import TokenException, NetworkException

from services.kite_service import require_authenticated_client
from services.strategy_engine import get_engine, get_nifty_engine, get_banknifty_engine
from services.paper_trade import read_trades, get_summary, CSV_PATHS
from services.candle_logger import list_log_files, LOG_DIR

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auto-trading", tags=["auto-trading"])


def _get_kite():
    try:
        return require_authenticated_client()
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))


async def _start_engine(instrument: str):
    """Shared start logic for any instrument engine."""
    engine = get_engine(instrument)
    state  = engine.get_status()
    if state["engine_running"]:
        raise HTTPException(status_code=400, detail=f"{instrument} engine is already running")

    kite = _get_kite()
    try:
        loop = asyncio.get_event_loop()
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
        logger.error("%s engine start failed: %s", instrument, e)
        raise HTTPException(status_code=500, detail=f"Engine start failed: {e}")

    return {
        "status":   "started",
        "strategy": f"{instrument}_INTRADAY_VWAP_EMA_BREAKOUT",
        "mode":     info["mode"],
        "message":  (
            f"{instrument} engine running in {info['mode']} mode. "
            f"ATM={info['atm_strike']} expiry={info['expiry']}. "
            "Waiting for 22 candles (~1h 50m) for indicators to warm up."
        ),
        "instruments": {"ce": info["ce"], "pe": info["pe"]},
    }


async def _stop_engine(instrument: str):
    """Shared stop logic for any instrument engine."""
    engine = get_engine(instrument)
    state  = engine.get_status()
    if not state["engine_running"]:
        raise HTTPException(status_code=400, detail=f"{instrument} engine is not running")

    kite = _get_kite()
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, engine.stop, kite)
    except Exception as e:
        logger.error("%s engine stop error: %s", instrument, e)
        raise HTTPException(status_code=500, detail=f"Error stopping engine: {e}")

    final = engine.get_status()
    return {
        "status":       "stopped",
        "instrument":   instrument,
        "trades_today": final["trades_today"],
        "exit_reason":  final["exit_reason"],
        "final_pnl":    final["pnl"],
    }


# ===========================================================================
# Common start/stop all endpoints
# ===========================================================================

@router.post("/start-all")
async def start_all():
    """Start both NIFTY and BANKNIFTY engines with a single call."""
    kite = _get_kite()
    results = {}
    errors  = {}

    for instrument in ("NIFTY", "BANKNIFTY"):
        engine = get_engine(instrument)
        state  = engine.get_status()
        if state["engine_running"]:
            results[instrument] = {"status": "already_running"}
            continue
        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, engine.start, kite)
            results[instrument] = {
                "status": "started",
                "mode":   info["mode"],
                "atm":    info["atm_strike"],
                "ce":     info["ce"],
                "pe":     info["pe"],
            }
        except Exception as e:
            logger.error("%s engine start failed in start-all: %s", instrument, e)
            errors[instrument] = str(e)

    return {
        "results": results,
        "errors":  errors,
        "message": "Both engines started. Waiting 22 candles (~1h 50m) for warm indicators.",
    }


@router.post("/stop-all")
async def stop_all():
    """Stop both NIFTY and BANKNIFTY engines with a single call."""
    kite = _get_kite()
    results = {}
    errors  = {}

    for instrument in ("NIFTY", "BANKNIFTY"):
        engine = get_engine(instrument)
        state  = engine.get_status()
        if not state["engine_running"]:
            results[instrument] = {"status": "already_stopped"}
            continue
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, engine.stop, kite)
            final = engine.get_status()
            results[instrument] = {
                "status":       "stopped",
                "trades_today": final["trades_today"],
                "final_pnl":    final["pnl"],
            }
        except Exception as e:
            logger.error("%s engine stop failed in stop-all: %s", instrument, e)
            errors[instrument] = str(e)

    return {"results": results, "errors": errors}


# ===========================================================================
# NIFTY endpoints (original routes preserved unchanged)
# ===========================================================================

@router.post("/start")
async def start_nifty():
    return await _start_engine("NIFTY")


@router.post("/stop")
async def stop_nifty():
    return await _stop_engine("NIFTY")


@router.get("/status")
async def get_nifty_status():
    return get_nifty_engine().get_status()


@router.get("/paper-log")
async def get_nifty_paper_log():
    return {
        "trades":  read_trades("NIFTY"),
        "summary": get_summary("NIFTY"),
    }


@router.get("/candles")
async def get_nifty_candles():
    return await _get_candles_for("NIFTY")


@router.get("/candle-log/list")
async def list_nifty_candle_logs():
    return {"files": list_log_files("NIFTY")}


@router.get("/candle-log/download/{date}")
async def download_nifty_candle_log(date: str):
    return _download_candle_log(date, "NIFTY")


@router.get("/paper-log/download")
async def download_nifty_paper_log():
    path = CSV_PATHS["NIFTY"]
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No NIFTY paper trades logged yet")
    return FileResponse(path=path, media_type="text/csv", filename="paper_trades_nifty.csv")


# ===========================================================================
# BANKNIFTY endpoints
# ===========================================================================

@router.post("/banknifty/start")
async def start_banknifty():
    return await _start_engine("BANKNIFTY")


@router.post("/banknifty/stop")
async def stop_banknifty():
    return await _stop_engine("BANKNIFTY")


@router.get("/banknifty/status")
async def get_banknifty_status():
    return get_banknifty_engine().get_status()


@router.get("/banknifty/paper-log")
async def get_banknifty_paper_log():
    return {
        "trades":  read_trades("BANKNIFTY"),
        "summary": get_summary("BANKNIFTY"),
    }


@router.get("/banknifty/candles")
async def get_banknifty_candles():
    return await _get_candles_for("BANKNIFTY")


@router.get("/banknifty/candle-log/list")
async def list_banknifty_candle_logs():
    return {"files": list_log_files("BANKNIFTY")}


@router.get("/banknifty/candle-log/download/{date}")
async def download_banknifty_candle_log(date: str):
    return _download_candle_log(date, "BANKNIFTY")


@router.get("/banknifty/paper-log/download")
async def download_banknifty_paper_log():
    path = CSV_PATHS["BANKNIFTY"]
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No BANKNIFTY paper trades logged yet")
    return FileResponse(path=path, media_type="text/csv", filename="paper_trades_banknifty.csv")


# ===========================================================================
# Shared helpers
# ===========================================================================

async def _get_candles_for(instrument: str):
    """Return 5-min candles with per-candle indicators for the given instrument."""
    from services.indicators import compute_ema, compute_rsi
    from services.strategy_engine import get_engine

    engine = get_engine(instrument)
    state  = engine._state_mgr.get_state()
    candles = state.candles
    if not candles:
        return {"candles": []}

    today  = candles[-1].timestamp.date()
    closes = [c.close for c in candles]
    ema20_series = compute_ema(closes, 20)
    rsi14_series = compute_rsi(closes, 14)

    vwap_cum_tp  = 0.0
    vwap_cum_vol = 0.0
    IST_OFFSET   = 19800   # 5.5 * 3600

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
            "open":     c.open,
            "high":     c.high,
            "low":      c.low,
            "close":    c.close,
            "volume":   c.volume,
            "ema20":    round(ema20_series[i], 2) if ema20_series[i] is not None else None,
            "rsi14":    round(rsi14_series[i], 2) if rsi14_series[i] is not None else None,
            "vwap":     vwap_val,
            "is_today": is_today,
        })

    return {"candles": result}


def _download_candle_log(date: str, instrument: str):
    prefix = "banknifty" if instrument == "BANKNIFTY" else "nifty"
    path   = os.path.join(LOG_DIR, f"{prefix}_candles_{date}.csv")
    # Also try old format for NIFTY
    if not os.path.exists(path) and instrument == "NIFTY":
        path = os.path.join(LOG_DIR, f"candles_{date}.csv")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"No {instrument} candle log for {date}")
    return FileResponse(
        path=path,
        media_type="text/csv",
        filename=f"{prefix}_candles_{date}.csv",
    )
