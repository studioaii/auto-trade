import logging
import threading
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from kiteconnect import KiteTicker

from services.trading_state import Candle, get_lock, get_raw_state, get_state, update_state

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class CandleBuilder:
    """
    Aggregates live ticks into 5-minute OHLC candles.
    Candle boundaries: every slot where minute // 5 * 5 changes.
    """

    def __init__(self):
        self._open: Optional[float] = None
        self._high: Optional[float] = None
        self._low: Optional[float] = None
        self._close: Optional[float] = None
        self._volume: int = 0
        self._candle_slot: Optional[int] = None   # minute slot of current candle
        self._candle_start: Optional[datetime] = None

    def _current_slot(self, ts: datetime) -> int:
        return (ts.minute // 5) * 5

    def process_tick(self, price: float, volume: int, timestamp: datetime) -> Optional[Candle]:
        """
        Feed a tick. Returns a completed Candle if the 5-min boundary was crossed.
        """
        slot = self._current_slot(timestamp)

        # First tick ever
        if self._candle_slot is None:
            self._start_new_candle(price, volume, timestamp, slot)
            return None

        # Same candle — update OHLC
        if slot == self._candle_slot:
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            self._close = price
            self._volume += volume
            return None

        # New slot — finalize previous candle, start new one
        completed = Candle(
            timestamp=self._candle_start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
        )
        self._start_new_candle(price, volume, timestamp, slot)
        return completed

    def _start_new_candle(self, price: float, volume: int, timestamp: datetime, slot: int):
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._volume = volume
        self._candle_slot = slot
        self._candle_start = timestamp.replace(minute=slot, second=0, microsecond=0)


class MarketDataService:
    """
    Manages KiteTicker WebSocket connection.
    Runs in a background thread (KiteTicker is non-async).
    """

    def __init__(self):
        self._ticker: Optional[KiteTicker] = None
        self._nifty_builder = CandleBuilder()
        self._nifty_index_token: int = 0
        self._nifty_futures_token: int = 0   # Futures for candle building (has real volume)
        self._option_tokens: list[int] = []
        self._candle_callback: Optional[Callable[[Candle], None]] = None
        self._spot_callback: Optional[Callable[[float], None]] = None
        self._option_ltp_callback: Optional[Callable[[int, float], None]] = None
        self._running = False
        self._lock = threading.Lock()

    def start(
        self,
        api_key: str,
        access_token: str,
        nifty_index_token: int,
        option_tokens: list[int],
        candle_callback: Callable[[Candle], None],
        spot_callback: Callable[[float], None],
        option_ltp_callback: Callable[[int, float], None],
        nifty_futures_token: int = 0,
    ) -> None:
        """Start KiteTicker in background thread."""
        with self._lock:
            if self._running:
                logger.warning("MarketDataService already running")
                return

            self._nifty_index_token = nifty_index_token
            self._nifty_futures_token = nifty_futures_token
            self._option_tokens = option_tokens
            self._candle_callback = candle_callback
            self._spot_callback = spot_callback
            self._option_ltp_callback = option_ltp_callback
            self._running = True

        ticker = KiteTicker(api_key, access_token, reconnect=True)
        ticker.on_connect = self._on_connect
        ticker.on_ticks = self._on_ticks
        ticker.on_close = self._on_close
        ticker.on_error = self._on_error
        ticker.on_reconnect = self._on_reconnect
        ticker.on_noreconnect = self._on_noreconnect

        self._ticker = ticker
        # threaded=True: starts its own thread, call returns immediately
        ticker.connect(threaded=True)
        logger.info("KiteTicker started (threaded)")

    def stop(self) -> None:
        """Disconnect WebSocket and clean up."""
        with self._lock:
            self._running = False

        if self._ticker:
            try:
                self._ticker.close()
            except Exception as e:
                logger.warning("Error closing ticker: %s", e)
            self._ticker = None
        logger.info("KiteTicker stopped")

    def update_option_subscriptions(self, new_tokens: list[int]) -> None:
        """Subscribe additional option tokens after engine start."""
        if not self._ticker:
            return
        try:
            self._ticker.subscribe(new_tokens)
            self._ticker.set_mode(self._ticker.MODE_LTP, new_tokens)
            logger.info("Subscribed to option tokens: %s", new_tokens)
        except Exception as e:
            logger.error("Failed to subscribe to tokens: %s", e)


    # ------------------------------------------------------------------
    # KiteTicker callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, ws, response) -> None:
        # Build subscription list
        all_tokens = [self._nifty_index_token] + self._option_tokens
        full_mode_tokens = [self._nifty_index_token]

        # If futures token is available, subscribe it in FULL mode for candle building
        # (futures have real volume, index does not)
        if self._nifty_futures_token:
            all_tokens.append(self._nifty_futures_token)
            full_mode_tokens.append(self._nifty_futures_token)

        ws.subscribe(all_tokens)
        ws.set_mode(ws.MODE_FULL, full_mode_tokens)
        if self._option_tokens:
            ws.set_mode(ws.MODE_LTP, self._option_tokens)
        logger.info(
            "WebSocket connected | index=%s futures=%s options=%s",
            self._nifty_index_token,
            self._nifty_futures_token or "none",
            self._option_tokens,
        )

        # If we already have candle data this is a reconnect — backfill any missed candles
        if get_state().last_candle_time is not None:
            threading.Thread(
                target=self._backfill_missing_candles,
                name="CandleBackfill",
                daemon=True,
            ).start()

    def _backfill_missing_candles(self) -> None:
        """
        Called in a background thread after reconnect.
        Fetches completed 5-min candles from Kite historical API for the gap
        between the last locally-known candle and now, then injects them into state.
        """
        from services.kite_service import require_authenticated_client

        try:
            state = get_state()
            last_candle_time = state.last_candle_time
            if last_candle_time is None:
                return

            now = datetime.now(IST)
            # Start fetching from the candle slot AFTER the last one we already have
            from_dt = last_candle_time + timedelta(minutes=5)
            if from_dt >= now:
                logger.info("Reconnect: no missing candles — last candle is recent")
                return

            kite = require_authenticated_client()
            candle_token = self._nifty_futures_token or self._nifty_index_token

            logger.info(
                "Backfilling missed candles after reconnect | from=%s to=%s token=%s",
                from_dt.strftime("%H:%M"), now.strftime("%H:%M"), candle_token,
            )

            historical = kite.historical_data(
                instrument_token=candle_token,
                from_date=from_dt,
                to_date=now,
                interval="5minute",
            )

            if not historical:
                logger.info("Backfill: no historical candles returned")
                return

            # Exclude the current incomplete candle (its slot hasn't closed yet)
            current_slot_start = now.replace(
                minute=(now.minute // 5) * 5, second=0, microsecond=0
            )
            new_candles: list[Candle] = []
            for row in historical:
                ts = row["date"]
                if hasattr(ts, "astimezone"):
                    ts = ts.astimezone(IST)
                if ts >= current_slot_start:
                    continue   # incomplete candle — skip
                new_candles.append(Candle(
                    timestamp=ts,
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row.get("volume", 0),
                ))

            if not new_candles:
                logger.info("Backfill: all returned candles are incomplete or already held")
                return

            with get_lock():
                raw = get_raw_state()
                raw.candles.extend(new_candles)
                raw.candles.sort(key=lambda c: c.timestamp)
                raw.last_candle_time = raw.candles[-1].timestamp

            logger.info(
                "Backfill complete: %d candles injected | %s → %s",
                len(new_candles),
                new_candles[0].timestamp.strftime("%H:%M"),
                new_candles[-1].timestamp.strftime("%H:%M"),
            )

        except Exception as e:
            logger.warning("Candle backfill failed (non-fatal): %s", e)

    def _on_ticks(self, ws, ticks: list[dict]) -> None:
        if not self._running:
            return

        # Determine which token to use for candle building:
        # Prefer futures (has real volume) over index (no volume)
        candle_token = self._nifty_futures_token or self._nifty_index_token

        for tick in ticks:
            token = tick.get("instrument_token")
            ltp = tick.get("last_price", 0)

            if token == self._nifty_index_token:
                # Always update spot price from index (official Nifty level)
                if self._spot_callback and ltp > 0:
                    self._spot_callback(ltp)

                # Build candles from index ONLY if no futures available
                if candle_token == self._nifty_index_token:
                    self._process_candle_tick(tick, ltp)

            elif token == self._nifty_futures_token:
                # Build candles from futures (real volume + OI)
                self._process_candle_tick(tick, ltp)
                # Track futures LTP separately for UI comparison — do NOT use as spot
                if ltp > 0:
                    from services.trading_state import update_state
                    update_state(nifty_futures_ltp=ltp)

            elif token in self._option_tokens:
                # Option LTP update for P&L monitoring
                if self._option_ltp_callback and ltp > 0:
                    self._option_ltp_callback(token, ltp)

    def _process_candle_tick(self, tick: dict, ltp: float) -> None:
        """Build 5-min candle from a tick (index or futures)."""
        volume = tick.get("volume_traded", 0)
        ts_raw = tick.get("timestamp") or tick.get("last_trade_time")
        if ts_raw:
            ts = ts_raw.astimezone(IST) if hasattr(ts_raw, "astimezone") else datetime.now(IST)
        else:
            ts = datetime.now(IST)

        if ltp > 0:
            candle = self._nifty_builder.process_tick(ltp, volume or 0, ts)
            if candle and self._candle_callback:
                logger.info(
                    "5-min candle closed | %s O=%.2f H=%.2f L=%.2f C=%.2f V=%d",
                    candle.timestamp.strftime("%H:%M"),
                    candle.open, candle.high, candle.low, candle.close,
                    candle.volume,
                )
                self._candle_callback(candle)

    def _on_close(self, ws, code, reason) -> None:
        logger.warning("WebSocket closed | code=%s reason=%s | will attempt reconnect", code, reason)
        update_state(error_message=f"WebSocket disconnected (reconnecting...): {reason}")
        # Do NOT set self._running = False here — KiteTicker will reconnect automatically.
        # _running is only set False in stop() (intentional) or _on_noreconnect (all retries exhausted).

    def _on_error(self, ws, code, reason) -> None:
        logger.error("WebSocket error | code=%s reason=%s", code, reason)
        update_state(error_message=f"WebSocket error: {reason}")

    def _on_reconnect(self, ws, attempt_count) -> None:
        logger.info("WebSocket reconnecting | attempt=%d", attempt_count)
        # Restore running flag in case it was cleared by a previous close
        with self._lock:
            self._running = True
        update_state(error_message=None)

    def _on_noreconnect(self, ws) -> None:
        logger.error("WebSocket reconnection failed permanently — all retries exhausted")
        with self._lock:
            self._running = False
        update_state(error_message="WebSocket reconnection failed. Please restart the engine.")
