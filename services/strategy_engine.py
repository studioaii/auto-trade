"""
NIFTY_INTRADAY_VWAP_EMA_BREAKOUT — Trading Engine
Supports PAPER mode (log to CSV, no real orders) and LIVE mode.
"""
import time as time_module
import logging
import threading
from datetime import datetime, timedelta, date as date_type
from typing import Optional
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

from config import API_KEY, TRADING_MODE
from services.kite_service import require_authenticated_client
from services.trading_state import (
    Candle, PositionInfo, TradingState,
    get_state, get_raw_state, update_state, get_lock, reset_daily_state,
)
from services.instruments import (
    fetch_nifty_instruments, get_current_expiry, get_atm_strike,
    find_option_instrument, get_nifty_index_token, find_nifty_futures,
)
from services.indicators import get_latest_indicators, MIN_CANDLES
from services.strategy import (
    Signal, generate_signal, detect_opposite_signal,
    is_market_open_and_ready, is_force_exit_time,
)
from services.order_service import (
    place_entry_order, get_average_price, place_exit_order, verify_position_exists,
)
from services.risk_manager import (
    can_enter_trade, check_exit_conditions, calculate_pnl,
    compute_dynamic_sl, MAX_TRADES_PER_DAY,
)
from services.paper_trade import log_trade
from services.market_data import MarketDataService

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class TradingEngine:
    """
    Orchestrates all services for VWAP+EMA breakout intraday options trading.
    PAPER mode: simulates trades, logs to CSV. No real orders.
    LIVE mode: places real Kite orders (use only after paper trading validation).
    """

    def __init__(self):
        self._market_data = MarketDataService()
        self._instruments: list[dict] = []
        self._ce_instrument: Optional[dict] = None
        self._pe_instrument: Optional[dict] = None
        self._nifty_index_token = get_nifty_index_token()
        self._nifty_futures_token: int = 0
        self._nifty_futures_symbol: str = ""
        self._monitor_thread: Optional[threading.Thread] = None
        self._kite: Optional[KiteConnect] = None
        self._paper_trade_counter = 0

    # ------------------------------------------------------------------
    # Session candle preload (mid-session resume)
    # ------------------------------------------------------------------

    def _load_session_candles(self) -> None:
        """
        Preload today's completed 5-min candles from Kite historical API so that
        indicators are ready immediately when the engine starts mid-session.

        - Fetches today from 9:15 AM up to (but not including) the current incomplete candle.
        - If today has < MIN_CANDLES candles, prepends the last N candles from the previous
          trading day so EMA-20 and RSI-14 can warm up correctly.
        - VWAP is computed separately in indicators.py using today-only candles.
        - Safe to call before WebSocket starts (uses REST historical API).
        """
        try:
            now = datetime.now(IST)
            # Nothing to load before the session begins
            if now.hour < 9 or (now.hour == 9 and now.minute < 15):
                logger.info("Before 9:15 AM — skipping historical candle preload")
                return

            candle_token = self._nifty_futures_token or self._nifty_index_token
            today = now.date()

            session_start = datetime(today.year, today.month, today.day, 9, 15, 0, tzinfo=IST)
            # Current incomplete candle slot — exclude it
            current_slot_start = now.replace(
                minute=(now.minute // 5) * 5, second=0, microsecond=0
            )

            logger.info(
                "Preloading session candles | token=%s | %s → %s",
                candle_token,
                session_start.strftime("%H:%M"),
                current_slot_start.strftime("%H:%M"),
            )

            raw_today = self._kite.historical_data(
                instrument_token=candle_token,
                from_date=session_start,
                to_date=now,
                interval="5minute",
            )

            today_candles: list[Candle] = []
            for row in raw_today:
                ts = row["date"]
                if hasattr(ts, "astimezone"):
                    ts = ts.astimezone(IST)
                if ts >= current_slot_start:
                    continue  # skip the still-open candle
                today_candles.append(Candle(
                    timestamp=ts,
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row.get("volume", 0),
                ))

            logger.info(
                "Today's candles: %d (need %d for warm indicators)",
                len(today_candles), MIN_CANDLES,
            )

            all_candles = today_candles

            # Prepend previous trading day's candles when today doesn't have enough for EMA/RSI
            if len(today_candles) < MIN_CANDLES:
                seed_count = MIN_CANDLES - len(today_candles) + 5  # small buffer

                # Walk back to the last actual trading day (skip weekends + public holidays)
                # Try up to 10 days back to handle long holiday stretches
                prev_day = today - timedelta(days=1)
                seed_candles: list[Candle] = []

                for _ in range(10):
                    # Skip weekends first
                    while prev_day.weekday() >= 5:
                        prev_day -= timedelta(days=1)

                    prev_start = datetime(prev_day.year, prev_day.month, prev_day.day, 9, 15, 0, tzinfo=IST)
                    prev_end   = datetime(prev_day.year, prev_day.month, prev_day.day, 15, 30, 0, tzinfo=IST)

                    logger.info(
                        "Fetching seed candles from %s for EMA/RSI warmup (need ~%d)",
                        prev_day, seed_count,
                    )

                    try:
                        raw_prev = self._kite.historical_data(
                            instrument_token=candle_token,
                            from_date=prev_start,
                            to_date=prev_end,
                            interval="5minute",
                        )

                        prev_candles: list[Candle] = []
                        for row in raw_prev:
                            ts = row["date"]
                            if hasattr(ts, "astimezone"):
                                ts = ts.astimezone(IST)
                            prev_candles.append(Candle(
                                timestamp=ts,
                                open=row["open"],
                                high=row["high"],
                                low=row["low"],
                                close=row["close"],
                                volume=row.get("volume", 0),
                            ))

                        if prev_candles:
                            seed_candles = prev_candles[-seed_count:]
                            logger.info(
                                "Seed from %s: %d candles | Combined total: %d",
                                prev_day, len(seed_candles), len(seed_candles) + len(today_candles),
                            )
                            break  # Found a trading day with data — stop looking
                        else:
                            logger.info("%s had no candles (holiday?) — trying previous day", prev_day)
                            prev_day -= timedelta(days=1)

                    except Exception as e:
                        logger.warning("Seed candle fetch failed for %s (%s) — trying previous day", prev_day, e)
                        prev_day -= timedelta(days=1)

                all_candles = seed_candles + today_candles

            if not all_candles:
                logger.info("No historical candles available to preload")
                return

            with get_lock():
                raw = get_raw_state()
                raw.candles = all_candles
                raw.last_candle_time = all_candles[-1].timestamp

            logger.info(
                "Session candles ready: %d | first=%s last=%s | indicators_ready=%s",
                len(all_candles),
                all_candles[0].timestamp.strftime("%Y-%m-%d %H:%M"),
                all_candles[-1].timestamp.strftime("%H:%M"),
                len(all_candles) >= MIN_CANDLES,
            )

        except Exception as e:
            logger.warning("Session candle preload failed (non-fatal): %s", e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, kite: KiteConnect) -> dict:
        state = get_state()
        if state.engine_running:
            raise RuntimeError("Engine is already running")

        mode = TRADING_MODE.upper()
        if mode not in ("PAPER", "LIVE"):
            mode = "PAPER"

        self._kite = kite
        reset_daily_state(mode=mode)
        update_state(engine_running=True)

        logger.info("Starting engine in %s mode", mode)

        # Fetch instruments
        self._instruments = fetch_nifty_instruments(kite)
        expiry = get_current_expiry(self._instruments)

        # Get live spot
        ltp_data = kite.ltp("NSE:NIFTY 50")
        spot = ltp_data["NSE:NIFTY 50"]["last_price"]
        atm = get_atm_strike(spot)
        update_state(nifty_spot=spot)

        self._ce_instrument = find_option_instrument(self._instruments, expiry, atm, "CE")
        self._pe_instrument = find_option_instrument(self._instruments, expiry, atm, "PE")

        # Find Nifty futures for candle building (real volume + OI)
        try:
            fut = find_nifty_futures(kite)
            self._nifty_futures_token = fut["instrument_token"]
            self._nifty_futures_symbol = fut["tradingsymbol"]
            logger.info("Using Nifty Futures for candles: %s (token=%s)",
                        self._nifty_futures_symbol, self._nifty_futures_token)
        except Exception as e:
            logger.warning("Could not find Nifty futures: %s — falling back to index (no volume)", e)
            self._nifty_futures_token = 0
            self._nifty_futures_symbol = ""

        logger.info(
            "Instruments | spot=%.1f ATM=%d | CE=%s PE=%s | expiry=%s | candle_src=%s",
            spot, atm,
            self._ce_instrument["tradingsymbol"],
            self._pe_instrument["tradingsymbol"],
            expiry,
            self._nifty_futures_symbol or "INDEX (no volume)",
        )

        option_tokens = [
            self._ce_instrument["instrument_token"],
            self._pe_instrument["instrument_token"],
        ]

        # Preload today's candles so indicators are ready immediately if starting mid-session
        self._load_session_candles()

        self._market_data.start(
            api_key=API_KEY,
            access_token=kite.access_token,
            nifty_index_token=self._nifty_index_token,
            option_tokens=option_tokens,
            candle_callback=self._on_candle_ready,
            spot_callback=self._on_spot_update,
            option_ltp_callback=self._on_option_ltp,
            nifty_futures_token=self._nifty_futures_token,
        )

        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            name="TradingMonitor",
            daemon=True,
        )
        self._monitor_thread.start()

        return {
            "mode": mode,
            "atm_strike": atm,
            "expiry": str(expiry),
            "ce": self._ce_instrument["tradingsymbol"],
            "pe": self._pe_instrument["tradingsymbol"],
        }

    def stop(self, kite: KiteConnect) -> None:
        state = get_state()
        if state.position is not None and not (state.trades_today >= MAX_TRADES_PER_DAY and state.position is None):
            logger.info("Stopping engine — closing open position first")
            self._execute_exit(reason="MANUAL_STOP", forced=True)

        self._market_data.stop()
        update_state(engine_running=False)
        logger.info("Trading engine stopped")

    def get_status(self) -> dict:
        state = get_state()

        pnl_info = None
        if state.position and state.position.current_price > 0:
            pnl_info = calculate_pnl(
                state.position.entry_price,
                state.position.current_price,
                state.position.qty,
            )
            # Include trailing stop info
            pnl_info["trailing_sl"] = round(state.position.trailing_sl_price, 2)
            pnl_info["trail_active"] = state.position.trail_active
            pnl_info["breakeven_set"] = state.position.breakeven_set
        elif state.pnl:
            pnl_info = state.pnl

        position_info = None
        if state.position:
            p = state.position
            position_info = {
                "symbol":        p.option_symbol,
                "option_type":   p.option_type,
                "strike":        p.strike,
                "expiry":        str(p.expiry),
                "entry_price":   p.entry_price,
                "current_price": p.current_price,
                "qty":           p.qty,
                "entry_time":    p.entry_time.strftime("%H:%M:%S") if p.entry_time else None,
                "reason_entry":  p.reason_for_entry,
                "trailing_sl":   round(p.trailing_sl_price, 2),
                "trail_active":  p.trail_active,
                "breakeven_set": p.breakeven_set,
            }

        return {
            "strategy":        "NIFTY_INTRADAY_VWAP_EMA_BREAKOUT",
            "mode":            state.trading_mode,
            "engine_running":  state.engine_running,
            "trades_today":    state.trades_today,
            "max_trades":      MAX_TRADES_PER_DAY,
            "nifty_spot":      round(state.nifty_spot, 2),
            "ce_ltp":          round(state.ce_ltp, 2),
            "pe_ltp":          round(state.pe_ltp, 2),
            "market_state":    state.market_state,
            "last_signal":     state.last_signal,
            "last_candle_time": state.last_candle_time.strftime("%H:%M") if state.last_candle_time else None,
            "candle_count":    len(state.candles),
            "candles_needed":  22,
            "position":        position_info,
            "pnl":             pnl_info,
            "exit_reason":     state.exit_reason,
            "exit_price":      state.exit_price,
            "error":           state.error_message,
            "instruments": {
                "ce": self._ce_instrument["tradingsymbol"] if self._ce_instrument else None,
                "pe": self._pe_instrument["tradingsymbol"] if self._pe_instrument else None,
                "candle_source": self._nifty_futures_symbol or "NIFTY INDEX (no volume)",
            } if self._ce_instrument else None,
        }

    # ------------------------------------------------------------------
    # WebSocket callbacks
    # ------------------------------------------------------------------

    def _on_spot_update(self, spot: float) -> None:
        update_state(nifty_spot=spot)

    def _on_option_ltp(self, token: int, ltp: float) -> None:
        """Track CE/PE LTP for paper entry price and P&L monitoring."""
        with get_lock():
            raw = get_raw_state()
            if self._ce_instrument and token == self._ce_instrument["instrument_token"]:
                raw.ce_ltp = ltp
            elif self._pe_instrument and token == self._pe_instrument["instrument_token"]:
                raw.pe_ltp = ltp
            # Update open position's current price
            if raw.position and raw.position.instrument_token == token:
                raw.position.current_price = ltp

    def _on_candle_ready(self, candle: Candle) -> None:
        """Called every 5-min candle close from WebSocket thread."""
        with get_lock():
            get_raw_state().candles.append(candle)
            get_raw_state().last_candle_time = candle.timestamp

        state = get_state()

        # Skip if position is open (exit logic is in monitoring loop)
        if state.position is not None:
            return

        if state.trades_today >= MAX_TRADES_PER_DAY:
            return

        if not is_market_open_and_ready():
            return

        # Compute indicators
        indicators = get_latest_indicators(state.candles)
        update_state(market_state=indicators["market_state"])

        if not indicators["enough_data"]:
            logger.info(
                "Warming up | candles=%d/%d",
                indicators["candle_count"], indicators["candles_needed"]
            )
            return

        if indicators["market_state"] == "SIDEWAYS":
            logger.info("SIDEWAYS market detected — no trades")
            return

        signal, reason = generate_signal(
            state.candles,
            indicators["vwap"],
            indicators["ema20"],
            indicators["ema20_series"],
            indicators["market_state"],
            rsi14=indicators.get("rsi14"),
            volume_surge=indicators.get("volume_surge", True),
        )
        update_state(last_signal=signal.value)

        if signal == Signal.NO_SIGNAL:
            return

        allowed, block_reason = can_enter_trade(state)
        if not allowed:
            logger.info("Entry blocked: %s", block_reason)
            return

        self._execute_entry(signal, reason, indicators)

    # ------------------------------------------------------------------
    # Entry execution
    # ------------------------------------------------------------------

    def _execute_entry(self, signal: Signal, reason: str, indicators: dict) -> None:
        """
        Atomically claim the trade slot, then simulate (PAPER) or place (LIVE) order.
        """
        instrument = self._ce_instrument if signal == Signal.BUY_CE else self._pe_instrument

        # Atomic guard
        with get_lock():
            raw = get_raw_state()
            if raw.position is not None or raw.trades_today >= MAX_TRADES_PER_DAY:
                return
            raw.trades_today += 1   # Claim slot BEFORE any I/O

        state = get_state()
        mode = state.trading_mode

        try:
            if mode == "PAPER":
                # Use live LTP as simulated entry price
                ltp = state.ce_ltp if signal == Signal.BUY_CE else state.pe_ltp
                if ltp <= 0:
                    logger.warning("Option LTP not available yet for paper entry")
                    with get_lock():
                        get_raw_state().trades_today -= 1  # rollback
                    return

                self._paper_trade_counter += 1
                order_id = f"PAPER-{self._paper_trade_counter:03d}"
                avg_price = ltp
                logger.info(
                    "[PAPER] ENTRY | %s | price=%.2f | %s",
                    instrument["tradingsymbol"], avg_price, reason
                )

            else:  # LIVE
                kite = self._kite or require_authenticated_client()
                order_id = place_entry_order(kite, instrument, signal)
                avg_price = get_average_price(kite, order_id)
                logger.info(
                    "[LIVE] ENTRY | %s | price=%.2f | order=%s",
                    instrument["tradingsymbol"], avg_price, order_id
                )

            # Compute dynamic SL from candle structure
            option_type = signal.value[-2:]   # "CE" or "PE"
            sl_result = compute_dynamic_sl(
                candles=state.candles,
                option_type=option_type,
                nifty_spot=state.nifty_spot,
                entry_price=avg_price,
            )
            if sl_result is None:
                # Candle structure too wide — skip this trade
                logger.info("Trade skipped — dynamic SL too wide for current candle structure")
                with get_lock():
                    get_raw_state().trades_today -= 1  # rollback slot
                return
            sl_price, sl_pct = sl_result

            # Compute efficiency ratio for logging
            candles_snap = state.candles
            eff = 0.0
            if len(candles_snap) >= 10:
                recent = candles_snap[-10:]
                closes = [c.close for c in recent]
                max_h = max(c.high for c in recent)
                min_l = min(c.low  for c in recent)
                rng = max_h - min_l
                if rng > 0:
                    eff = round(abs(closes[-1] - closes[0]) / rng, 3)

            position = PositionInfo(
                option_symbol=instrument["tradingsymbol"],
                instrument_token=instrument["instrument_token"],
                option_type=option_type,
                strike=int(instrument["strike"]),
                expiry=instrument["expiry"],
                entry_price=avg_price,
                qty=int(instrument.get("lot_size", 75)),
                order_id=order_id,
                entry_time=datetime.now(IST),
                reason_for_entry=reason,
                current_price=avg_price,
                trailing_sl_price=sl_price,      # dynamic structure-based SL
                highest_price_seen=avg_price,
                # Indicator snapshot
                nifty_spot_entry=state.nifty_spot,
                vwap_entry=indicators.get("vwap", 0.0),
                ema20_entry=indicators.get("ema20") or 0.0,
                rsi14_entry=indicators.get("rsi14") or 0.0,
                market_state_entry=indicators.get("market_state", "UNKNOWN"),
                efficiency_entry=eff,
            )
            logger.info(
                "ENTRY CONFIRMED | %s | entry=%.2f sl=%.2f (%.1f%%) | %s",
                instrument["tradingsymbol"], avg_price, sl_price, sl_pct, reason,
            )

            update_state(position=position)

        except TokenException:
            logger.error("Token expired during entry")
            with get_lock():
                get_raw_state().trades_today -= 1  # rollback
            update_state(engine_running=False, error_message="Token expired. Re-authenticate via /login.")

        except Exception as e:
            logger.error("Entry failed: %s", e)
            with get_lock():
                get_raw_state().trades_today -= 1  # rollback
            update_state(error_message=str(e))

    # ------------------------------------------------------------------
    # Exit execution
    # ------------------------------------------------------------------

    def _execute_exit(self, reason: str, forced: bool = False) -> None:
        """Place exit order (PAPER or LIVE) and log trade."""
        state = get_state()
        position = state.position
        if position is None:
            return

        exit_price = position.current_price
        if exit_price <= 0:
            exit_price = position.entry_price   # fallback

        mode = state.trading_mode

        try:
            if mode == "PAPER":
                logger.info(
                    "[PAPER] EXIT | %s | entry=%.2f exit=%.2f | reason=%s",
                    position.option_symbol, position.entry_price, exit_price, reason
                )
            else:  # LIVE
                kite = self._kite or require_authenticated_client()
                if not forced or verify_position_exists(kite, position.option_symbol):
                    place_exit_order(kite, position, reason)

            # Log to CSV (both modes)
            log_trade(
                trade_number=state.trades_today,
                option_symbol=position.option_symbol,
                option_type=position.option_type,
                strike=position.strike,
                expiry=position.expiry,
                entry_time=position.entry_time,
                entry_price=position.entry_price,
                exit_time=datetime.now(IST),
                exit_price=exit_price,
                qty=position.qty,
                reason_for_entry=position.reason_for_entry,
                reason_for_exit=reason,
                trailing_sl_used=position.trail_active,
                breakeven_set=position.breakeven_set,
                nifty_spot_entry=position.nifty_spot_entry,
                nifty_spot_exit=state.nifty_spot,
                vwap_entry=position.vwap_entry,
                ema20_entry=position.ema20_entry,
                rsi14_entry=position.rsi14_entry,
                market_state_entry=position.market_state_entry,
                efficiency_entry=position.efficiency_entry,
            )

            pnl = calculate_pnl(position.entry_price, exit_price, position.qty)
            update_state(
                position=None,
                exit_reason=reason,
                exit_price=exit_price,
                pnl=pnl,
            )
            logger.info(
                "EXIT COMPLETE | reason=%s | PnL: ₹%.2f (%.1f%%)",
                reason, pnl["pnl_rupees"], pnl["pnl_pct"]
            )

        except TokenException:
            logger.error("Token expired during exit — check Zerodha immediately!")
            update_state(
                engine_running=False,
                error_message="Token expired during exit! Check Zerodha app immediately.",
            )
        except Exception as e:
            logger.error("Exit failed: %s — will retry", e)
            update_state(error_message=f"Exit failed: {e}")

    # ------------------------------------------------------------------
    # Monitoring loop
    # ------------------------------------------------------------------

    def _monitoring_loop(self) -> None:
        logger.info("Monitoring loop started")
        while True:
            state = get_state()

            if not state.engine_running:
                break

            if state.position is not None:
                self._check_position_exits(state)

            time_module.sleep(1)

        logger.info("Monitoring loop terminated")

    def _check_position_exits(self, state: TradingState) -> None:
        """Check exit conditions and trigger exit if needed."""
        position = state.position
        if position is None:
            return

        # Update trailing stop in-place (requires lock)
        with get_lock():
            raw_pos = get_raw_state().position
            if raw_pos is None:
                return
            should_exit, reason = check_exit_conditions(raw_pos)

        if not should_exit:
            # Also check opposite signal on candle close
            candles = state.candles
            indicators = get_latest_indicators(candles) if len(candles) >= 22 else {}
            if indicators.get("enough_data") and detect_opposite_signal(
                candles,
                position.option_type,
                indicators.get("vwap", 0),
                indicators.get("ema20_series", []),
                indicators.get("market_state", "UNKNOWN"),
            ):
                reason = "OPPOSITE_SIGNAL"
                should_exit = True

        if should_exit:
            self._execute_exit(reason=reason)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_engine = TradingEngine()


def get_engine() -> TradingEngine:
    return _engine
