import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from routers import auth, trading, auto_trading, backtest

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
    from services.kite_service import get_stored_token, require_authenticated_client
    if get_stored_token():
        try:
            kite = require_authenticated_client()
            for eng in (get_nifty_engine(), get_banknifty_engine()):
                if eng.get_status()["engine_running"]:
                    eng.stop(kite)
                    logger.info("%s engine stopped on shutdown", eng._instrument_name)
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
app.include_router(backtest.router)


# ---------------------------------------------------------------------------
# Vue frontend — served from frontend/dist after `npm run build`
# ---------------------------------------------------------------------------
# Mount LAST so API routes take priority
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

