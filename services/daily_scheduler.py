"""
DailyScheduler — runs as a background daemon thread to:
  • Auto-start both engines at 09:15 IST (force-restart if stuck)
  • Auto-stop  both engines at 15:35 IST
"""
import logging
import threading
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Scheduler windows: action fires if current time falls within a 4-minute window
# (30-second polling means we'll catch it within 30 s of the target minute)
_START_HOUR, _START_MIN = 9, 15     # auto-start window: 09:15 – 09:18
_STOP_HOUR,  _STOP_MIN  = 15, 35    # auto-stop  window: 15:35 – 15:38
_POLL_INTERVAL_S        = 30        # scheduler loop tick


class DailyScheduler:
    def __init__(self):
        self._today_started:  date | None = None
        self._today_stopped:  date | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="DailyScheduler", daemon=True
        )
        self._thread.start()
        logger.info("DailyScheduler started")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:
                logger.error("DailyScheduler unexpected error", exc_info=True)
            time.sleep(_POLL_INTERVAL_S)

    def _tick(self) -> None:
        from services.kite_service import get_stored_token

        now   = datetime.now(IST)
        today = now.date()
        h, m  = now.hour, now.minute

        # ── Auto-start: 09:15–09:18 ────────────────────────────────────
        if (h == _START_HOUR and _START_MIN <= m < _START_MIN + 4
                and self._today_started != today):
            if get_stored_token():
                logger.info("DailyScheduler: 09:15 window — auto-starting engines")
                self._start_engines()
                self._today_started = today
            else:
                logger.info("DailyScheduler: 09:15 reached but not logged in to Zerodha — skipping")

        # ── Auto-stop: 15:35–15:38 ─────────────────────────────────────
        if (h == _STOP_HOUR and _STOP_MIN <= m < _STOP_MIN + 4
                and self._today_stopped != today):
            logger.info("DailyScheduler: 15:35 window — auto-stopping engines")
            self._stop_engines()
            self._today_stopped = today

    # ------------------------------------------------------------------
    # Engine auto-start
    # ------------------------------------------------------------------

    def _start_engines(self) -> None:
        from services.kite_service import require_authenticated_client
        from services.strategy_engine import get_nifty_engine, get_banknifty_engine

        try:
            kite = require_authenticated_client()
        except Exception as exc:
            logger.error("DailyScheduler auto-start: auth failed — %s", exc)
            return

        for engine in (get_nifty_engine(), get_banknifty_engine()):
            name = engine._instrument_name
            try:
                # Force-stop if stuck / already running so we get a clean slate
                state = engine.get_status()
                if state["engine_running"]:
                    logger.info("DailyScheduler: %s appears running — force-stopping first", name)
                    self._force_stop_engine(engine, kite)

                engine.start(kite)
                logger.info("DailyScheduler: %s engine started successfully", name)

            except Exception as exc:
                logger.error("DailyScheduler: failed to start %s — %s", name, exc)

    def _force_stop_engine(self, engine, kite) -> None:
        """Attempt a clean stop; if it fails, forcibly reset engine state."""
        name = engine._instrument_name
        try:
            engine.stop(kite)
        except Exception as exc:
            logger.warning(
                "DailyScheduler: clean stop of %s failed (%s) — force-resetting state", name, exc
            )
            try:
                engine._market_data.unregister_instrument(name)
            except Exception:
                pass
            try:
                engine._state_mgr.update_state(engine_running=False)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Engine auto-stop
    # ------------------------------------------------------------------

    def _stop_engines(self) -> None:
        from services.kite_service import get_stored_token, require_authenticated_client
        from services.strategy_engine import get_nifty_engine, get_banknifty_engine

        kite = None
        if get_stored_token():
            try:
                kite = require_authenticated_client()
            except Exception as exc:
                logger.warning("DailyScheduler auto-stop: auth error — %s", exc)

        for engine in (get_nifty_engine(), get_banknifty_engine()):
            name = engine._instrument_name
            try:
                state = engine.get_status()
                if not state["engine_running"]:
                    logger.info("DailyScheduler: %s already stopped", name)
                    continue

                if kite:
                    engine.stop(kite)
                    logger.info("DailyScheduler: %s engine stopped", name)
                else:
                    # No kite client — force-reset without closing orders
                    logger.warning(
                        "DailyScheduler: no kite client — force-stopping %s without order closure", name
                    )
                    try:
                        engine._market_data.unregister_instrument(name)
                    except Exception:
                        pass
                    engine._state_mgr.update_state(engine_running=False)

            except Exception as exc:
                logger.error("DailyScheduler: failed to stop %s — %s", name, exc)


# Module-level singleton
_scheduler = DailyScheduler()


def get_scheduler() -> DailyScheduler:
    return _scheduler
