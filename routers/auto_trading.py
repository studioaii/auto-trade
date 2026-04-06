import logging
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from kiteconnect.exceptions import TokenException, NetworkException

from services.kite_service import require_authenticated_client
from services.trading_state import get_state
from services.strategy_engine import get_engine
from services.paper_trade import read_trades, get_summary, CSV_PATH
import os

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auto-trading", tags=["auto-trading"])


def _get_kite():
    try:
        return require_authenticated_client()
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.post("/start")
async def start_auto_trading():
    """
    Start the VWAP+EMA breakout trading engine.
    Mode is controlled by TRADING_MODE env var (PAPER or LIVE).
    """
    state = get_state()
    if state.engine_running:
        raise HTTPException(status_code=400, detail="Engine is already running")

    kite = _get_kite()
    engine = get_engine()

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
        logger.error("Engine start failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Engine start failed: {e}")

    return {
        "status": "started",
        "strategy": "NIFTY_INTRADAY_VWAP_EMA_BREAKOUT",
        "mode": info["mode"],
        "message": (
            f"Engine running in {info['mode']} mode. "
            f"ATM={info['atm_strike']} expiry={info['expiry']}. "
            "Waiting for 22 candles (~1h 50m) for indicators to warm up."
        ),
        "instruments": {
            "ce": info["ce"],
            "pe": info["pe"],
        },
    }


@router.post("/stop")
async def stop_auto_trading():
    """Stop the engine. Exits any open position and logs it to CSV."""
    state = get_state()
    if not state.engine_running:
        raise HTTPException(status_code=400, detail="Engine is not running")

    kite = _get_kite()
    engine = get_engine()

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, engine.stop, kite)
    except Exception as e:
        logger.error("Engine stop error: %s", e)
        raise HTTPException(status_code=500, detail=f"Error stopping engine: {e}")

    state = get_state()
    return {
        "status": "stopped",
        "trades_today": state.trades_today,
        "exit_reason": state.exit_reason,
        "final_pnl": state.pnl,
    }


@router.get("/status")
async def get_status():
    """Live engine status, candle count, indicators, position, and P&L."""
    return get_engine().get_status()


@router.get("/paper-log")
async def get_paper_log():
    """Return all paper trades as JSON."""
    return {
        "trades": read_trades(),
        "summary": get_summary(),
    }


@router.get("/candles")
async def get_candles():
    """Return today's session candles with per-candle EMA20, RSI14, VWAP for live chart."""
    from services.indicators import compute_ema, compute_rsi

    state = get_state()
    candles = state.candles
    if not candles:
        return {"candles": []}

    today = candles[-1].timestamp.date()
    closes = [c.close for c in candles]
    ema20_series = compute_ema(closes, 20)
    rsi14_series = compute_rsi(closes, 14)

    # Cumulative intraday VWAP — resets at today's first candle
    vwap_cum_tp = 0.0
    vwap_cum_vol = 0.0

    # IST offset so browser chart (UTC display) shows correct IST times
    IST_OFFSET = 19800  # 5.5 * 3600

    result = []
    for i, c in enumerate(candles):
        is_today = c.timestamp.date() == today
        vwap_val = None
        if is_today:
            tp = (c.high + c.low + c.close) / 3.0
            vwap_cum_tp += tp * c.volume
            vwap_cum_vol += c.volume
            vwap_val = round(vwap_cum_tp / vwap_cum_vol, 2) if vwap_cum_vol > 0 else None

        result.append({
            "time":    int(c.timestamp.timestamp()) + IST_OFFSET,
            "open":    c.open,
            "high":    c.high,
            "low":     c.low,
            "close":   c.close,
            "volume":  c.volume,
            "ema20":   round(ema20_series[i], 2) if ema20_series[i] is not None else None,
            "rsi14":   round(rsi14_series[i], 2) if rsi14_series[i] is not None else None,
            "vwap":    vwap_val,
            "is_today": is_today,
        })

    return {"candles": result}


@router.get("/paper-log/download")
async def download_paper_log():
    """Download paper_trades.csv file directly."""
    if not os.path.exists(CSV_PATH):
        raise HTTPException(status_code=404, detail="No paper trades logged yet")
    return FileResponse(
        path=CSV_PATH,
        media_type="text/csv",
        filename="paper_trades.csv",
    )
