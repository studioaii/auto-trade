import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from routers import (
    auth, trading, auto_trading, auto_trading_v2, auto_trading_nifty2,
    auto_trading_nifty_fut,
)

# ---------------------------------------------------------------------------
# Logging — rotated, cwd-independent
# ---------------------------------------------------------------------------
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            _LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Logesh Auto Trading Engine starting up")
    from services.daily_scheduler import get_scheduler
    get_scheduler().start()
    yield
    logger.info("Logesh Auto Trading Engine shutting down")
    from services.strategy_engine import get_nifty_engine, get_banknifty_engine
    from services.strategy_engine_v2 import get_banknifty2_engine
    from services.nifty_engine_v2 import get_nifty2_engine
    from services.nifty_fut_engine import get_nifty_fut_engine
    from services.kite_service import get_stored_token, require_authenticated_client
    if get_stored_token():
        try:
            kite = require_authenticated_client()
            for eng in (get_nifty_engine(), get_banknifty_engine(), get_banknifty2_engine(), get_nifty2_engine(), get_nifty_fut_engine()):
                try:
                    if eng.get_status()["engine_running"]:
                        eng.stop(kite)
                        name = getattr(eng, "_instrument_name", None) or "ENGINE"
                        logger.info("%s engine stopped on shutdown", name)
                except Exception:
                    logger.warning("Engine stop on shutdown failed", exc_info=True)
        except Exception:
            logger.warning("Could not cleanly stop engines on shutdown", exc_info=True)


app = FastAPI(
    title="Logesh Auto Trading Engine",
    description="Automated Nifty Options Trading — VWAP+EMA Breakout Strategy",
    version="3.0.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(trading.router)
app.include_router(auto_trading.router)
app.include_router(auto_trading_v2.router)
app.include_router(auto_trading_nifty2.router)
app.include_router(auto_trading_nifty_fut.router)


# ---------------------------------------------------------------------------
# CSRF protection — block cross-origin mutation requests (C4)
# ---------------------------------------------------------------------------
_PROTECTED_PREFIXES = (
    "/auto-trading/start",
    "/auto-trading/stop",
    "/auto-trading/banknifty/start",
    "/auto-trading/banknifty/stop",
    "/auto-trading/banknifty2/start",
    "/auto-trading/banknifty2/stop",
    "/auto-trading/nifty2/start",
    "/auto-trading/nifty2/stop",
    "/auto-trading/nifty-fut/start",
    "/auto-trading/nifty-fut/stop",
)
_ALLOWED_ORIGINS = {
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://localhost:5173",   # vite dev server
    "https://caffeinehead.in",
    "https://www.caffeinehead.in",
}


@app.middleware("http")
async def csrf_protect(request: Request, call_next):
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        if any(request.url.path.startswith(p) for p in _PROTECTED_PREFIXES):
            origin = request.headers.get("origin", "")
            if origin and origin not in _ALLOWED_ORIGINS:
                logger.warning("CSRF blocked: origin=%s path=%s", origin, request.url.path)
                return JSONResponse({"error": "Cross-origin requests not allowed"}, status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Vue frontend — served from frontend/dist after `npm run build`
# ---------------------------------------------------------------------------
_DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
if not os.path.isdir(_DIST_DIR):
    logger.warning(
        "frontend/dist not found — run `npm run build` inside frontend/ to enable the dashboard. "
        "API routes are still available."
    )
else:
    # Mount LAST so API routes take priority (M8)
    app.mount("/", StaticFiles(directory=_DIST_DIR, html=True), name="frontend")

