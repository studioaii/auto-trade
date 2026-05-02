import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from kiteconnect import KiteTicker

from services.trading_state import Candle

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _is_market_hours_now() -> bool:
    """True when current IST time is within NSE market hours (09:15–15:30, weekdays)."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= minutes <= 15 * 60 + 30


class CandleBuilder:
    """
    Aggregates live ticks into 5-minute OHLC candles.
    Candle boundaries: every slot where minute // 5 * 5 changes.

    Kite volume_traded is cumulative daily volume. We track the last seen value
    and use only the delta (per-tick volume) so candle volumes match historical data.
    """

    def __init__(self):
        self._open: Optional[float] = None
        self._high: Optional[float] = None
        self._low: Optional[float] = None
        self._close: Optional[float] = None
        self._volume: int = 0
        self._candle_slot: Optional[int] = None
        self._candle_start: Optional[datetime] = None
        self._last_cum_volume: int = 0
        self._vol_initialized: bool = False  # True after first tick establishes baseline

    def _current_slot(self, ts: datetime) -> int:
        return (ts.minute // 5) * 5

    def _tick_volume_delta(self, cum_volume: int) -> int:
        """Convert cumulative daily volume to per-tick delta volume.

        The first tick after (re)start carries the full day's cumulative volume
        since 9:15 — we must NOT add that to a candle.  We store it as the
        baseline and return 0, so only incremental volume from subsequent ticks
        is counted.  This keeps live candle volumes consistent with historical data.
        """
        if cum_volume <= 0:
            return 0                        # index ticks have no real volume

        if not self._vol_initialized:
            # First real tick — set baseline only, contribute 0 to candle
            self._last_cum_volume = cum_volume
            self._vol_initialized = True
            return 0

        if cum_volume < self._last_cum_volume:
            # New trading day — cumulative reset to 0 and climbing again
            delta = cum_volume
        else:
            delta = cum_volume - self._last_cum_volume

        self._last_cum_volume = cum_volume
        return max(0, delta)

    def process_tick(self, price: float, cum_volume: int, timestamp: datetime) -> Optional[Candle]:
        tick_vol = self._tick_volume_delta(cum_volume)
        slot = self._current_slot(timestamp)

        if self._candle_slot is None:
            self._start_new_candle(price, tick_vol, timestamp, slot)
            return None

        if slot == self._candle_slot:
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            self._close = price
            self._volume += tick_vol
            return None

        completed = Candle(
            timestamp=self._candle_start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
        )
        self._start_new_candle(price, tick_vol, timestamp, slot)
        return completed

    def _start_new_candle(self, price: float, tick_vol: int, timestamp: datetime, slot: int):
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._volume = tick_vol
        self._candle_slot = slot
        self._candle_start = timestamp.replace(minute=slot, second=0, microsecond=0)

    def get_live_candle(self) -> Optional[dict]:
        """Return the in-progress candle data, or None if no tick received yet."""
        if self._candle_slot is None or self._candle_start is None:
            return None
        return {
            "timestamp": self._candle_start,
            "open":   self._open,
            "high":   self._high,
            "low":    self._low,
            "close":  self._close,
            "volume": self._volume,
        }


@dataclass
class InstrumentSubscription:
    """
    All data needed to route WebSocket ticks for one instrument
    and inject candles/spots into that instrument's state.
    """
    instrument_name: str
    index_token: int
    futures_token: int                          # 0 if not available
    option_tokens: list
    candle_callback: Callable                   # (Candle) -> None
    spot_callback: Callable                     # (float) -> None
    option_ltp_callback: Callable               # (token, ltp) -> None
    # State accessors — injected by TradingEngine so backfill writes to the right state
    get_lock_fn: Callable
    get_raw_state_fn: Callable
    get_state_fn: Callable
    update_state_fn: Callable
    candle_builder: CandleBuilder = field(default_factory=CandleBuilder)


class MarketDataService:
    """
    Manages ONE KiteTicker WebSocket shared across all instruments.
    Each instrument registers an InstrumentSubscription; ticks are routed
    to the correct instrument by token.
    """

    def __init__(self):
        self._ticker: Optional[KiteTicker] = None
        self._api_key: str = ""
        self._access_token: str = ""
        self._running: bool = False   # True once start() has been called
        self._connected: bool = False  # True only when WebSocket is live
        self._lock = threading.Lock()
        # instrument_name -> InstrumentSubscription
        self._subscriptions: dict[str, InstrumentSubscription] = {}
        # token -> instrument_name (fast routing)
        self._token_to_instrument: dict[int, str] = {}
        self._watchdog_thread: Optional[threading.Thread] = None
        self._disconnected_at: Optional[float] = None   # monotonic time of last disconnect
        self._monitor_thread: Optional[threading.Thread] = None
        # Tick-liveness watchdog: monotonic timestamp of the most recent tick batch.
        # None = no clock yet (fresh start or just after a forced restart).
        self._last_tick_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, api_key: str, access_token: str, subscription: InstrumentSubscription) -> None:
        """
        Register an instrument and start the WebSocket if not already running.
        If already running and connected, subscribe the new instrument immediately.
        If connecting (running but not yet connected), _on_connect will pick it up.
        """
        with self._lock:
            self._api_key = api_key
            self._access_token = access_token
            self._add_subscription_locked(subscription)

            if self._running:
                if self._connected:
                    # WebSocket is live — subscribe right now
                    self._subscribe_tokens_for(subscription)
                    logger.info(
                        "Registered %s on existing WebSocket", subscription.instrument_name
                    )
                else:
                    # Still connecting — _on_connect will subscribe all registered instruments
                    logger.info(
                        "Registered %s — will subscribe once WebSocket connects",
                        subscription.instrument_name,
                    )
                return

            self._running = True

        # First instrument: start the ticker (outside lock)
        self._start_ticker(api_key, access_token)

    def _start_ticker(self, api_key: str, access_token: str) -> None:
        """Create a fresh KiteTicker and connect it. Called outside the lock."""
        ticker = KiteTicker(
            api_key,
            access_token,
            reconnect=True,
            reconnect_max_tries=300,    # library max — ~hours of retries with back-off
            reconnect_max_delay=30,     # cap back-off at 30 s
            connect_timeout=30,
        )
        ticker.on_connect     = self._on_connect
        ticker.on_ticks       = self._on_ticks
        ticker.on_close       = self._on_close
        ticker.on_error       = self._on_error
        ticker.on_reconnect   = self._on_reconnect
        ticker.on_noreconnect = self._on_noreconnect

        with self._lock:
            self._ticker = ticker

        ticker.connect(threaded=True)
        logger.info("KiteTicker started (threaded)")

        # Start persistent connection monitor (only one per service lifetime)
        self._start_connection_monitor()

    def _start_connection_monitor(self) -> None:
        """Persistent daemon thread that watches for disconnections and reconnects.

        Checks every 15 s. If the WebSocket has been down for ≥ 45 s (covers
        network switches, ISP drops, WiFi→hotspot swaps) it closes the stale
        ticker and spawns a fresh one — regardless of what caused the drop.
        Runs for the lifetime of the service; a second call is a no-op.
        """
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        def _monitor():
            import time as _time
            GRACE      = 45.0   # seconds disconnected before forcing a new ticker
            CHECK      = 15.0   # polling interval
            TICK_STALL = 60.0   # seconds without ticks during market hours = silent stall

            while True:
                _time.sleep(CHECK)

                stall_silence: Optional[float] = None
                disconn_elapsed: Optional[float] = None

                with self._lock:
                    if not self._running:
                        return  # service stopped — exit thread
                    api_key      = self._api_key
                    access_token = self._access_token

                    if self._connected:
                        self._disconnected_at = None
                        # Silent-stall check: socket reports connected but no ticks flowing.
                        # Only act during market hours to avoid pointless restarts pre-/post-market.
                        last_tick = self._last_tick_at
                        if last_tick is not None and _is_market_hours_now():
                            silence = _time.monotonic() - last_tick
                            if silence >= TICK_STALL:
                                stall_silence = silence
                    else:
                        # Not connected — record when we first noticed
                        if self._disconnected_at is None:
                            self._disconnected_at = _time.monotonic()
                        elapsed = _time.monotonic() - self._disconnected_at
                        if elapsed >= GRACE:
                            disconn_elapsed = elapsed

                if stall_silence is None and disconn_elapsed is None:
                    continue  # nothing to do this tick

                if stall_silence is not None:
                    logger.info(
                        "Tick stall detected — no ticks for %.0f seconds, forcing reconnect",
                        stall_silence,
                    )
                else:
                    logger.warning(
                        "ConnectionMonitor: WebSocket down for %.0f s — forcing fresh ticker",
                        disconn_elapsed,
                    )

                # Kill the stale ticker (it may be stuck retrying the old interface)
                try:
                    if self._ticker:
                        self._ticker.close()
                except Exception:
                    logger.debug("ConnectionMonitor: ticker close raised (best-effort)", exc_info=True)
                with self._lock:
                    self._ticker = None
                    self._disconnected_at = None   # reset so we don't spam
                    self._last_tick_at = None      # clean clock for the new ticker
                    self._connected = False        # gate further restarts via GRACE path

                if api_key and access_token:
                    self._start_ticker(api_key, access_token)
                    logger.info("ConnectionMonitor: new ticker spawned")

        t = threading.Thread(target=_monitor, name="WS-ConnectionMonitor", daemon=True)
        self._monitor_thread = t
        t.start()
        logger.info("ConnectionMonitor started (check=15s grace=45s)")

    def unregister_instrument(self, instrument_name: str) -> None:
        """
        Remove an instrument's subscription and unsubscribe its tokens.
        Stops the ticker entirely when no subscriptions remain.
        """
        tokens_to_remove = []
        remaining = 0

        with self._lock:
            sub = self._subscriptions.pop(instrument_name, None)
            if sub:
                tokens_to_remove = [sub.index_token]
                if sub.futures_token:
                    tokens_to_remove.append(sub.futures_token)
                tokens_to_remove.extend(sub.option_tokens or [])
                for tok in tokens_to_remove:
                    self._token_to_instrument.pop(tok, None)
            remaining = len(self._subscriptions)

        if tokens_to_remove and self._ticker:
            try:
                self._ticker.unsubscribe(tokens_to_remove)
            except Exception as e:
                logger.warning("Failed to unsubscribe %s tokens: %s", instrument_name, e)

        logger.info("Unregistered %s | remaining instruments=%d", instrument_name, remaining)

        if remaining == 0:
            self.stop()

    def stop(self) -> None:
        """Disconnect WebSocket and clear all subscriptions."""
        with self._lock:
            self._running = False
            self._connected = False
            self._subscriptions.clear()
            self._token_to_instrument.clear()

        if self._ticker:
            try:
                self._ticker.close()
            except Exception as e:
                logger.warning("Error closing ticker: %s", e)
            self._ticker = None
        logger.info("KiteTicker stopped")

    def get_live_candle(self, instrument_name: str) -> Optional[dict]:
        """Return the in-progress candle for an instrument, or None."""
        with self._lock:
            sub = self._subscriptions.get(instrument_name)
        if sub is None:
            return None
        return sub.candle_builder.get_live_candle()

    def force_reconnect(self) -> None:
        """Close existing ticker and spawn a fresh one — used by health-check watchdog."""
        with self._lock:
            if not self._running:
                return
            api_key      = self._api_key
            access_token = self._access_token

        logger.info("force_reconnect: closing existing ticker")
        try:
            if self._ticker:
                self._ticker.close()
        except Exception:
            logger.debug("force_reconnect: ticker close raised (best-effort)", exc_info=True)

        with self._lock:
            self._ticker    = None
            self._connected = False

        if api_key and access_token:
            self._start_ticker(api_key, access_token)
            logger.info("force_reconnect: new ticker spawned")

    def swap_option_subscriptions(
        self, instrument_name: str, old_tokens: list[int], new_tokens: list[int]
    ) -> None:
        """Unsubscribe old option tokens and subscribe new ones (ATM reselection)."""
        with self._lock:
            sub = self._subscriptions.get(instrument_name)
            if sub:
                for tok in sub.option_tokens:
                    self._token_to_instrument.pop(tok, None)
                sub.option_tokens = list(new_tokens)
                for tok in new_tokens:
                    self._token_to_instrument[tok] = instrument_name

        if not self._ticker:
            return
        try:
            if old_tokens:
                self._ticker.unsubscribe(old_tokens)
            if new_tokens:
                self._ticker.subscribe(new_tokens)
                self._ticker.set_mode(self._ticker.MODE_LTP, new_tokens)
            logger.info(
                "Option subscriptions swapped | %s | old=%s → new=%s",
                instrument_name, old_tokens, new_tokens,
            )
        except Exception as e:
            logger.error("Failed to swap option subscriptions: %s", e)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_subscription_locked(self, sub: InstrumentSubscription) -> None:
        """Register subscription and update token routing. Call with self._lock held."""
        self._subscriptions[sub.instrument_name] = sub
        self._token_to_instrument[sub.index_token] = sub.instrument_name
        if sub.futures_token:
            self._token_to_instrument[sub.futures_token] = sub.instrument_name
        for tok in (sub.option_tokens or []):
            self._token_to_instrument[tok] = sub.instrument_name

    def _subscribe_tokens_for(self, sub: InstrumentSubscription) -> None:
        """Subscribe and set modes for one instrument's tokens. Ticker must be running."""
        if not self._ticker:
            return
        try:
            tokens = [sub.index_token]
            full_mode = [sub.index_token]
            if sub.futures_token:
                tokens.append(sub.futures_token)
                full_mode.append(sub.futures_token)
            if sub.option_tokens:
                tokens.extend(sub.option_tokens)

            self._ticker.subscribe(tokens)
            self._ticker.set_mode(self._ticker.MODE_FULL, full_mode)
            if sub.option_tokens:
                self._ticker.set_mode(self._ticker.MODE_LTP, sub.option_tokens)
        except Exception as e:
            logger.error("Failed to subscribe tokens for %s: %s", sub.instrument_name, e)

    # ------------------------------------------------------------------
    # KiteTicker callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, ws, response) -> None:
        import time as _time
        all_tokens = []
        full_mode_tokens = []
        ltp_mode_tokens = []

        # Snapshot subscriptions (avoid holding lock while doing I/O)
        with self._lock:
            self._connected = True
            self._disconnected_at = None   # clear disconnect timer on successful connect
            self._last_tick_at = _time.monotonic()  # start tick-liveness clock
            subs = list(self._subscriptions.values())

        for sub in subs:
            all_tokens.append(sub.index_token)
            full_mode_tokens.append(sub.index_token)
            if sub.futures_token:
                all_tokens.append(sub.futures_token)
                full_mode_tokens.append(sub.futures_token)
            if sub.option_tokens:
                all_tokens.extend(sub.option_tokens)
                ltp_mode_tokens.extend(sub.option_tokens)

        ws.subscribe(all_tokens)
        ws.set_mode(ws.MODE_FULL, full_mode_tokens)
        if ltp_mode_tokens:
            ws.set_mode(ws.MODE_LTP, ltp_mode_tokens)

        logger.info(
            "WebSocket connected | instruments=%s | tokens=%d",
            [s.instrument_name for s in subs], len(all_tokens),
        )

        # Backfill candles for each instrument in background
        for sub in subs:
            state = sub.get_state_fn()
            if state.last_candle_time is not None:
                threading.Thread(
                    target=self._backfill_missing_candles,
                    args=(sub,),
                    name=f"CandleBackfill-{sub.instrument_name}",
                    daemon=True,
                ).start()
            else:
                threading.Thread(
                    target=self._backfill_today_candles,
                    args=(sub,),
                    name=f"CandleBackfillToday-{sub.instrument_name}",
                    daemon=True,
                ).start()

    def _backfill_today_candles(self, sub: InstrumentSubscription) -> None:
        """Fetch all completed 5-min candles from market open to now for a subscription."""
        from services.kite_service import require_authenticated_client
        try:
            now = datetime.now(IST)
            market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)

            if now <= market_open:
                return

            kite = require_authenticated_client()
            candle_token = sub.futures_token or sub.index_token

            logger.info(
                "Backfilling today's candles | %s | from=%s to=%s token=%s",
                sub.instrument_name,
                market_open.strftime("%H:%M"), now.strftime("%H:%M"), candle_token,
            )

            historical = kite.historical_data(
                instrument_token=candle_token,
                from_date=market_open,
                to_date=now,
                interval="5minute",
            )

            if not historical:
                return

            current_slot_start = now.replace(
                minute=(now.minute // 5) * 5, second=0, microsecond=0
            )
            new_candles: list[Candle] = []
            for row in historical:
                ts = row["date"]
                if hasattr(ts, "astimezone"):
                    ts = ts.astimezone(IST)
                if ts >= current_slot_start:
                    continue
                new_candles.append(Candle(
                    timestamp=ts,
                    open=row["open"], high=row["high"],
                    low=row["low"],  close=row["close"],
                    volume=row.get("volume", 0),
                ))

            if not new_candles:
                return

            with sub.get_lock_fn():
                raw = sub.get_raw_state_fn()
                existing_times = {c.timestamp for c in raw.candles}
                to_add = [c for c in new_candles if c.timestamp not in existing_times]
                raw.candles.extend(to_add)
                raw.candles.sort(key=lambda c: c.timestamp)
                if raw.candles:
                    raw.last_candle_time = raw.candles[-1].timestamp

            logger.info(
                "Today backfill complete | %s | %d candles | %s → %s",
                sub.instrument_name, len(new_candles),
                new_candles[0].timestamp.strftime("%H:%M"),
                new_candles[-1].timestamp.strftime("%H:%M"),
            )

        except Exception as e:
            logger.warning("Today candle backfill failed (%s, non-fatal): %s", sub.instrument_name, e)

    def _backfill_missing_candles(self, sub: InstrumentSubscription) -> None:
        """Fetch candles missed during a WebSocket reconnect gap."""
        from services.kite_service import require_authenticated_client
        try:
            state = sub.get_state_fn()
            last_candle_time = state.last_candle_time
            if last_candle_time is None:
                return

            now = datetime.now(IST)
            from_dt = last_candle_time + timedelta(minutes=5)
            if from_dt >= now:
                return

            kite = require_authenticated_client()
            candle_token = sub.futures_token or sub.index_token

            logger.info(
                "Backfilling missed candles after reconnect | %s | from=%s to=%s",
                sub.instrument_name, from_dt.strftime("%H:%M"), now.strftime("%H:%M"),
            )

            historical = kite.historical_data(
                instrument_token=candle_token,
                from_date=from_dt,
                to_date=now,
                interval="5minute",
            )

            if not historical:
                return

            current_slot_start = now.replace(
                minute=(now.minute // 5) * 5, second=0, microsecond=0
            )
            new_candles: list[Candle] = []
            for row in historical:
                ts = row["date"]
                if hasattr(ts, "astimezone"):
                    ts = ts.astimezone(IST)
                if ts >= current_slot_start:
                    continue
                new_candles.append(Candle(
                    timestamp=ts,
                    open=row["open"], high=row["high"],
                    low=row["low"],  close=row["close"],
                    volume=row.get("volume", 0),
                ))

            if not new_candles:
                return

            with sub.get_lock_fn():
                raw = sub.get_raw_state_fn()
                existing_times = {c.timestamp for c in raw.candles}
                to_add = [c for c in new_candles if c.timestamp not in existing_times]
                raw.candles.extend(to_add)
                raw.candles.sort(key=lambda c: c.timestamp)
                if raw.candles:
                    raw.last_candle_time = raw.candles[-1].timestamp

            logger.info(
                "Backfill complete | %s | %d candles | %s → %s",
                sub.instrument_name, len(new_candles),
                new_candles[0].timestamp.strftime("%H:%M"),
                new_candles[-1].timestamp.strftime("%H:%M"),
            )

        except Exception as e:
            logger.warning("Candle backfill failed (%s, non-fatal): %s", sub.instrument_name, e)

    def _on_ticks(self, ws, ticks: list[dict]) -> None:
        if not self._running:
            return

        # Tick-liveness watchdog — single atomic write, no lock needed (CPython GIL).
        import time as _time
        self._last_tick_at = _time.monotonic()

        for tick in ticks:
            token = tick.get("instrument_token")
            ltp   = tick.get("last_price", 0)

            instrument_name = self._token_to_instrument.get(token)
            if not instrument_name:
                continue

            sub = self._subscriptions.get(instrument_name)
            if not sub:
                continue

            candle_token = sub.futures_token or sub.index_token

            if token == sub.index_token:
                # Spot price from index (official level)
                if sub.spot_callback and ltp > 0:
                    sub.spot_callback(ltp)
                # Build candles from index only when no futures available
                if candle_token == sub.index_token:
                    self._process_candle_tick(tick, ltp, sub)

            elif token == sub.futures_token:
                # Build candles from futures (real volume)
                self._process_candle_tick(tick, ltp, sub)
                if ltp > 0:
                    sub.update_state_fn(nifty_futures_ltp=ltp)

            else:
                # Option LTP
                if sub.option_ltp_callback and ltp > 0:
                    sub.option_ltp_callback(token, ltp)

    def _process_candle_tick(self, tick: dict, ltp: float, sub: InstrumentSubscription) -> None:
        volume = tick.get("volume_traded", 0)
        ts_raw = tick.get("timestamp") or tick.get("last_trade_time")
        if ts_raw:
            ts = ts_raw.astimezone(IST) if hasattr(ts_raw, "astimezone") else datetime.now(IST)
        else:
            ts = datetime.now(IST)

        if ltp > 0:
            candle = sub.candle_builder.process_tick(ltp, volume or 0, ts)
            if candle and sub.candle_callback:
                logger.info(
                    "5-min candle closed | %s %s O=%.2f H=%.2f L=%.2f C=%.2f V=%d",
                    sub.instrument_name, candle.timestamp.strftime("%H:%M"),
                    candle.open, candle.high, candle.low, candle.close, candle.volume,
                )
                sub.candle_callback(candle)

    def _on_close(self, ws, code, reason) -> None:
        import time as _time
        logger.warning("WebSocket closed | code=%s reason=%s | will attempt reconnect", code, reason)
        with self._lock:
            self._connected = False
            if self._disconnected_at is None:
                self._disconnected_at = _time.monotonic()
            subs = list(self._subscriptions.values())
        for sub in subs:
            sub.update_state_fn(error_message=f"WebSocket disconnected (reconnecting...): {reason}")

    def _on_error(self, ws, code, reason) -> None:
        import time as _time
        logger.error("WebSocket error | code=%s reason=%s", code, reason)
        with self._lock:
            self._connected = False
            if self._disconnected_at is None:
                self._disconnected_at = _time.monotonic()
            subs = list(self._subscriptions.values())
        for sub in subs:
            sub.update_state_fn(error_message=f"WebSocket error: {reason}")

    def _on_reconnect(self, ws, attempt_count) -> None:
        logger.info("WebSocket reconnecting | attempt=%d", attempt_count)
        with self._lock:
            self._running = True
            self._connected = False
            subs = list(self._subscriptions.values())
        for sub in subs:
            sub.update_state_fn(error_message=None)

    def _on_noreconnect(self, ws) -> None:
        """
        Kite's built-in retry gave up — spawn our own recovery so trading
        continues without a server restart.
        """
        logger.warning(
            "KiteTicker gave up built-in retries — launching independent recovery thread"
        )
        with self._lock:
            self._connected = False
            api_key      = self._api_key
            access_token = self._access_token
            subs         = list(self._subscriptions.values())

        for sub in subs:
            sub.update_state_fn(error_message="WebSocket lost — reconnecting…")

        def _recover():
            import time as _time
            for delay in [5, 10, 20, 30, 60]:
                logger.info("Recovery attempt in %ds…", delay)
                _time.sleep(delay)
                with self._lock:
                    if not self._running:
                        logger.info("Recovery aborted — engines stopped")
                        return
                try:
                    if self._ticker:
                        self._ticker.close()
                except Exception:
                    logger.debug("recovery: ticker close raised (best-effort)", exc_info=True)
                with self._lock:
                    self._ticker = None
                logger.info("Recovery: spawning new KiteTicker")
                self._start_ticker(api_key, access_token)
                # Wait up to 30 s for connection
                _time.sleep(30)
                with self._lock:
                    if self._connected:
                        logger.info("Recovery succeeded")
                        for sub in subs:
                            sub.update_state_fn(error_message=None)
                        return
            logger.error("Recovery exhausted all attempts")

        threading.Thread(target=_recover, name="WS-Recovery", daemon=True).start()


# ---------------------------------------------------------------------------
# Module-level shared singleton — both engines use this one instance
# ---------------------------------------------------------------------------
_shared_market_data = MarketDataService()


def get_market_data_service() -> MarketDataService:
    return _shared_market_data
