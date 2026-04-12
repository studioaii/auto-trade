"""
Multi-instrument VWAP+EMA Breakout Trading Engine.
Supports NIFTY and BANKNIFTY independently with a shared WebSocket.
PAPER mode: simulates trades, logs to CSV. LIVE mode: places real Kite orders.
"""
import time as time_module
import logging
import threading
from datetime import datetime, timedelta, date as date_type
from typing import Optional
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

from config import API_KEY, TRADING_MODE, INSTRUMENT_CONFIG
from services.kite_service import require_authenticated_client
from services.trading_state import (
    Candle, PositionInfo, TradingState, InstrumentStateManager,
    get_state, get_raw_state, update_state, get_lock, reset_daily_state,
)
from services.instruments import (
    fetch_instruments, get_current_expiry_for_instrument, get_atm_strike,
    find_option_instrument, find_futures,
)
from services.indicators import get_latest_indicators, MIN_CANDLES, candle_body_pct
from services.strategy import (
    Signal, generate_signal, detect_opposite_signal,
    is_market_open_and_ready, is_force_exit_time,
)
from services.order_service import (
    place_entry_order, get_average_price, place_exit_order, verify_position_exists,
)
from services.risk_manager import (
    can_enter_trade, check_exit_conditions, calculate_pnl,
    INITIAL_SL_PCT, MAX_TRADES_PER_DAY,
)
from services.paper_trade import log_trade
from services.candle_logger import log_candle
from services.entry_logger import log_entry_attempt
from services.market_data import MarketDataService, InstrumentSubscription, get_market_data_service

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _log_attempt(
    signal,
    state,
    indicators: dict,
    skip_reason: str,
    ce_instrument,
    pe_instrument,
    option_ltp: float = 0.0,
    sl_pct_computed: float = 0.0,
    instrument: str = "NIFTY",
) -> None:
    try:
        instrument_obj = ce_instrument if signal.value == "BUY_CE" else pe_instrument
        atm_strike = int(instrument_obj["strike"]) if instrument_obj else 0
        ltp = option_ltp or (state.ce_ltp if signal.value == "BUY_CE" else state.pe_ltp)
        vwap = indicators.get("vwap", 0)
        vwap_dist = (state.nifty_spot - vwap) / vwap * 100 if vwap > 0 else 0.0
        candle = state.candles[-1] if state.candles else None
        body = candle_body_pct(candle) if candle else 0.0
        log_entry_attempt(
            signal=signal.value,
            nifty_spot=state.nifty_spot,
            atm_strike=atm_strike,
            option_ltp=ltp,
            vwap_distance_pct=vwap_dist,
            rsi14=indicators.get("rsi14") or 0.0,
            body_pct=body,
            market_state=indicators.get("market_state", ""),
            skip_reason=skip_reason,
            sl_pct_computed=sl_pct_computed,
            instrument=instrument,
        )
    except Exception:
        pass


class TradingEngine:
    """
    Orchestrates all services for VWAP+EMA breakout intraday options trading.
    Instrument-agnostic: pass instrument_name="NIFTY" or "BANKNIFTY".
    """

    def __init__(
        self,
        instrument_name: str = "NIFTY",
        state_manager: Optional[InstrumentStateManager] = None,
    ):
        self._instrument_name = instrument_name.upper()
        self._cfg = INSTRUMENT_CONFIG[self._instrument_name]

        # State management — each engine has its own isolated state
        self._state_mgr: InstrumentStateManager = state_manager or InstrumentStateManager(self._instrument_name)

        # Shared WebSocket service (one instance for the whole app)
        self._market_data: MarketDataService = get_market_data_service()

        self._instruments: list[dict] = []
        self._ce_instrument: Optional[dict] = None
        self._pe_instrument: Optional[dict] = None
        self._index_token: int = self._cfg["index_token"]
        self._futures_token: int = 0
        self._futures_symbol: str = ""
        self._monitor_thread: Optional[threading.Thread] = None
        self._kite: Optional[KiteConnect] = None
        self._paper_trade_counter = 0

    # ------------------------------------------------------------------
    # Convenience accessors that delegate to the state manager
    # ------------------------------------------------------------------

    def _get_state(self) -> TradingState:
        return self._state_mgr.get_state()

    def _update_state(self, **kwargs) -> None:
        self._state_mgr.update_state(**kwargs)

    def _get_lock(self) -> threading.Lock:
        return self._state_mgr.get_lock()

    def _get_raw_state(self) -> TradingState:
        return self._state_mgr.get_raw_state()

    # ------------------------------------------------------------------
    # Session candle preload (mid-session resume)
    # ------------------------------------------------------------------

    def _load_session_candles(self) -> None:
        """
        Preload historical 5-min candles so indicators are ready immediately.
        Pre-market: loads only previous-day seed candles.
        Mid-session: fetches today's completed candles + previous-day seed if needed.
        """
        try:
            now = datetime.now(IST)
            pre_market = now.hour < 9 or (now.hour == 9 and now.minute < 15)
            candle_token = self._futures_token or self._index_token
            today = now.date()

            today_candles: list[Candle] = []
            if not pre_market:
                session_start = datetime(today.year, today.month, today.day, 9, 15, 0, tzinfo=IST)
                current_slot_start = now.replace(
                    minute=(now.minute // 5) * 5, second=0, microsecond=0
                )
                logger.info(
                    "Preloading session candles | %s | token=%s | %s → %s",
                    self._instrument_name, candle_token,
                    session_start.strftime("%H:%M"), current_slot_start.strftime("%H:%M"),
                )
                raw_today = self._kite.historical_data(
                    instrument_token=candle_token,
                    from_date=session_start,
                    to_date=now,
                    interval="5minute",
                )
                for row in raw_today:
                    ts = row["date"]
                    if hasattr(ts, "astimezone"):
                        ts = ts.astimezone(IST)
                    if ts >= current_slot_start:
                        continue
                    today_candles.append(Candle(
                        timestamp=ts,
                        open=row["open"], high=row["high"],
                        low=row["low"],   close=row["close"],
                        volume=row.get("volume", 0),
                    ))
                logger.info(
                    "%s today's candles: %d (need %d for warm indicators)",
                    self._instrument_name, len(today_candles), MIN_CANDLES,
                )
            else:
                logger.info(
                    "Pre-market start (%s) — loading previous-day seed candles",
                    self._instrument_name,
                )

            all_candles = today_candles

            if len(today_candles) < MIN_CANDLES:
                seed_count = MIN_CANDLES - len(today_candles) + 5
                prev_day = today - timedelta(days=1)
                seed_candles: list[Candle] = []

                for _ in range(10):
                    while prev_day.weekday() >= 5:
                        prev_day -= timedelta(days=1)

                    prev_start = datetime(prev_day.year, prev_day.month, prev_day.day, 9, 15, 0, tzinfo=IST)
                    prev_end   = datetime(prev_day.year, prev_day.month, prev_day.day, 15, 30, 0, tzinfo=IST)

                    logger.info("Fetching seed candles from %s for %s", prev_day, self._instrument_name)

                    try:
                        raw_prev = self._kite.historical_data(
                            instrument_token=candle_token,
                            from_date=prev_start,
                            to_date=prev_end,
                            interval="5minute",
                        )
                        prev_candles = []
                        for row in raw_prev:
                            ts = row["date"]
                            if hasattr(ts, "astimezone"):
                                ts = ts.astimezone(IST)
                            prev_candles.append(Candle(
                                timestamp=ts,
                                open=row["open"], high=row["high"],
                                low=row["low"],   close=row["close"],
                                volume=row.get("volume", 0),
                            ))
                        if prev_candles:
                            seed_candles = prev_candles[-seed_count:]
                            logger.info(
                                "Seed from %s: %d candles | %s combined total: %d",
                                prev_day, len(seed_candles), self._instrument_name,
                                len(seed_candles) + len(today_candles),
                            )
                            break
                        else:
                            prev_day -= timedelta(days=1)
                    except Exception as e:
                        logger.warning("Seed fetch failed for %s %s: %s — trying prev day", self._instrument_name, prev_day, e)
                        prev_day -= timedelta(days=1)

                all_candles = seed_candles + today_candles

            if not all_candles:
                logger.info("No historical candles available for %s", self._instrument_name)
                return

            with self._get_lock():
                raw = self._get_raw_state()
                raw.candles = all_candles
                raw.last_candle_time = all_candles[-1].timestamp

            logger.info(
                "%s session candles ready: %d | %s → %s | indicators_ready=%s",
                self._instrument_name, len(all_candles),
                all_candles[0].timestamp.strftime("%Y-%m-%d %H:%M"),
                all_candles[-1].timestamp.strftime("%H:%M"),
                len(all_candles) >= MIN_CANDLES,
            )

            if len(all_candles) >= MIN_CANDLES:
                ind = get_latest_indicators(all_candles)
                if ind.get("enough_data"):
                    self._update_state(market_state=ind["market_state"])

        except Exception as e:
            logger.warning("%s session candle preload failed (non-fatal): %s", self._instrument_name, e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, kite: KiteConnect) -> dict:
        state = self._get_state()
        if state.engine_running:
            raise RuntimeError(f"{self._instrument_name} engine is already running")

        mode = TRADING_MODE.upper()
        if mode not in ("PAPER", "LIVE"):
            mode = "PAPER"

        self._kite = kite
        self._state_mgr.reset_daily_state(mode=mode)
        self._update_state(engine_running=True)

        logger.info("Starting %s engine in %s mode", self._instrument_name, mode)

        # Fetch instruments (options chain)
        self._instruments = fetch_instruments(kite, self._instrument_name)
        expiry = get_current_expiry_for_instrument(self._instruments, self._instrument_name)

        # Get live spot price
        ltp_symbol = self._cfg["ltp_symbol"]
        ltp_data = kite.ltp(ltp_symbol)
        spot = ltp_data[ltp_symbol]["last_price"]
        strike_interval = self._cfg["strike_interval"]
        atm = get_atm_strike(spot, strike_interval)
        self._update_state(nifty_spot=spot)

        self._ce_instrument = find_option_instrument(self._instruments, expiry, atm, "CE")
        self._pe_instrument = find_option_instrument(self._instruments, expiry, atm, "PE")

        # Find futures for candle building
        try:
            fut = find_futures(kite, self._instrument_name)
            self._futures_token  = fut["instrument_token"]
            self._futures_symbol = fut["tradingsymbol"]
            logger.info(
                "%s Futures for candles: %s (token=%s)",
                self._instrument_name, self._futures_symbol, self._futures_token,
            )
        except Exception as e:
            logger.warning("Could not find %s futures: %s — using index", self._instrument_name, e)
            self._futures_token  = 0
            self._futures_symbol = ""

        logger.info(
            "%s | spot=%.1f ATM=%d | CE=%s PE=%s | expiry=%s | candle_src=%s",
            self._instrument_name, spot, atm,
            self._ce_instrument["tradingsymbol"],
            self._pe_instrument["tradingsymbol"],
            expiry,
            self._futures_symbol or f"{self._instrument_name} INDEX (no volume)",
        )

        option_tokens = [
            self._ce_instrument["instrument_token"],
            self._pe_instrument["instrument_token"],
        ]

        # Preload candles before WebSocket starts
        self._load_session_candles()

        # Register with shared WebSocket service
        subscription = InstrumentSubscription(
            instrument_name=self._instrument_name,
            index_token=self._index_token,
            futures_token=self._futures_token,
            option_tokens=option_tokens,
            candle_callback=self._on_candle_ready,
            spot_callback=self._on_spot_update,
            option_ltp_callback=self._on_option_ltp,
            get_lock_fn=self._state_mgr.get_lock,
            get_raw_state_fn=self._state_mgr.get_raw_state,
            get_state_fn=self._state_mgr.get_state,
            update_state_fn=self._state_mgr.update_state,
        )
        self._market_data.start(
            api_key=API_KEY,
            access_token=kite.access_token,
            subscription=subscription,
        )

        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            name=f"TradingMonitor-{self._instrument_name}",
            daemon=True,
        )
        self._monitor_thread.start()

        return {
            "instrument": self._instrument_name,
            "mode":       mode,
            "atm_strike": atm,
            "expiry":     str(expiry),
            "ce":         self._ce_instrument["tradingsymbol"],
            "pe":         self._pe_instrument["tradingsymbol"],
        }

    def stop(self, kite: KiteConnect) -> None:
        state = self._get_state()
        if state.position is not None:
            logger.info("%s stopping — closing open position first", self._instrument_name)
            self._execute_exit(reason="MANUAL_STOP", forced=True)

        self._market_data.unregister_instrument(self._instrument_name)
        self._update_state(engine_running=False)
        logger.info("%s trading engine stopped", self._instrument_name)

    def get_status(self) -> dict:
        state = self._get_state()

        pnl_info = None
        if state.position and state.position.current_price > 0:
            pnl_info = calculate_pnl(
                state.position.entry_price,
                state.position.current_price,
                state.position.qty,
            )
            pnl_info["trailing_sl"]  = round(state.position.trailing_sl_price, 2)
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

        ind_snap = {}
        if len(state.candles) >= MIN_CANDLES:
            ind = get_latest_indicators(state.candles)
            if ind.get("enough_data"):
                ind_snap = {
                    "vwap":         ind.get("vwap"),
                    "ema20":        round(ind["ema20"], 2) if ind.get("ema20") else None,
                    "rsi14":        round(ind["rsi14"], 2) if ind.get("rsi14") else None,
                    "volume_surge": ind.get("volume_surge"),
                }

        instruments_info = None
        if self._ce_instrument:
            instruments_info = {
                "ce":           self._ce_instrument["tradingsymbol"],
                "pe":           self._pe_instrument["tradingsymbol"] if self._pe_instrument else None,
                "atm_strike":   int(self._ce_instrument["strike"]),
                "candle_source": self._futures_symbol or f"{self._instrument_name} INDEX (no volume)",
            }

        return {
            "instrument":        self._instrument_name,
            "strategy":          f"{self._instrument_name}_INTRADAY_VWAP_EMA_BREAKOUT",
            "mode":              state.trading_mode,
            "engine_running":    state.engine_running,
            "trades_today":      state.trades_today,
            "max_trades":        MAX_TRADES_PER_DAY,
            "nifty_spot":        round(state.nifty_spot, 2),
            "nifty_futures_ltp": round(state.nifty_futures_ltp, 2),
            "ce_ltp":            round(state.ce_ltp, 2),
            "pe_ltp":            round(state.pe_ltp, 2),
            "market_state":      state.market_state,
            "last_signal":       state.last_signal,
            "last_candle_time":  state.last_candle_time.strftime("%H:%M") if state.last_candle_time else None,
            "candle_count":      len(state.candles),
            "candles_needed":    22,
            "position":          position_info,
            "pnl":               pnl_info,
            "exit_reason":       state.exit_reason,
            "exit_price":        state.exit_price,
            "error":             state.error_message,
            "indicators":        ind_snap,
            "instruments":       instruments_info,
        }

    # ------------------------------------------------------------------
    # Dynamic ATM reselection
    # ------------------------------------------------------------------

    def _recalculate_atm(self) -> None:
        state = self._get_state()
        if state.position is not None:
            return
        if state.nifty_spot <= 0 or not self._ce_instrument:
            return

        strike_interval = self._cfg["strike_interval"]
        new_atm     = get_atm_strike(state.nifty_spot, strike_interval)
        current_atm = int(self._ce_instrument["strike"])

        if new_atm == current_atm:
            return

        logger.info(
            "%s ATM shift | %d → %d | spot=%.1f",
            self._instrument_name, current_atm, new_atm, state.nifty_spot,
        )

        try:
            expiry = get_current_expiry_for_instrument(self._instruments, self._instrument_name)
            new_ce = find_option_instrument(self._instruments, expiry, new_atm, "CE")
            new_pe = find_option_instrument(self._instruments, expiry, new_atm, "PE")
        except ValueError as e:
            logger.warning("%s ATM reselection failed: %s", self._instrument_name, e)
            return

        old_tokens = [
            self._ce_instrument["instrument_token"],
            self._pe_instrument["instrument_token"],
        ]
        new_tokens = [new_ce["instrument_token"], new_pe["instrument_token"]]

        self._ce_instrument = new_ce
        self._pe_instrument = new_pe
        self._market_data.swap_option_subscriptions(self._instrument_name, old_tokens, new_tokens)

        logger.info(
            "%s ATM updated | strike=%d | CE=%s PE=%s",
            self._instrument_name, new_atm, new_ce["tradingsymbol"], new_pe["tradingsymbol"],
        )

    # ------------------------------------------------------------------
    # WebSocket callbacks
    # ------------------------------------------------------------------

    def _on_spot_update(self, spot: float) -> None:
        self._update_state(nifty_spot=spot)

    def _on_option_ltp(self, token: int, ltp: float) -> None:
        with self._get_lock():
            raw = self._get_raw_state()
            if self._ce_instrument and token == self._ce_instrument["instrument_token"]:
                raw.ce_ltp = ltp
            elif self._pe_instrument and token == self._pe_instrument["instrument_token"]:
                raw.pe_ltp = ltp
            if raw.position and raw.position.instrument_token == token:
                raw.position.current_price = ltp

    def _on_candle_ready(self, candle: Candle) -> None:
        with self._get_lock():
            raw = self._get_raw_state()
            raw.candles.append(candle)
            raw.last_candle_time = candle.timestamp

        self._recalculate_atm()

        state = self._get_state()
        indicators = get_latest_indicators(state.candles)
        if indicators.get("enough_data"):
            self._update_state(market_state=indicators["market_state"])

        signal = Signal.NO_SIGNAL
        reason = ""
        trading_eligible = (
            state.position is None
            and state.trades_today < MAX_TRADES_PER_DAY
            and is_market_open_and_ready()
            and indicators.get("enough_data", False)
            and indicators.get("market_state") != "SIDEWAYS"
        )
        if trading_eligible:
            signal, reason = generate_signal(
                state.candles,
                indicators["vwap"],
                indicators["ema20"],
                indicators["ema20_series"],
                indicators["market_state"],
                rsi14=indicators.get("rsi14"),
                volume_surge=indicators.get("volume_surge", True),
            )
            self._update_state(last_signal=signal.value)

        atm_strike = int(self._ce_instrument["strike"]) if self._ce_instrument else None
        log_candle(candle, indicators, signal.value, state, atm_strike, instrument=self._instrument_name)

        if state.position is not None:
            return
        if state.trades_today >= MAX_TRADES_PER_DAY:
            return
        if not is_market_open_and_ready():
            return
        if not indicators.get("enough_data", False):
            logger.info(
                "%s warming up | candles=%d/%d",
                self._instrument_name, indicators["candle_count"], indicators["candles_needed"],
            )
            return
        if indicators.get("market_state") == "SIDEWAYS":
            logger.info("%s SIDEWAYS — no trades", self._instrument_name)
            return
        if signal == Signal.NO_SIGNAL:
            return

        allowed, block_reason = can_enter_trade(state)
        if not allowed:
            logger.info("%s entry blocked: %s", self._instrument_name, block_reason)
            _log_attempt(signal, state, indicators, block_reason,
                         self._ce_instrument, self._pe_instrument, instrument=self._instrument_name)
            return

        self._execute_entry(signal, reason, indicators)

    # ------------------------------------------------------------------
    # Entry execution
    # ------------------------------------------------------------------

    def _execute_entry(self, signal: Signal, reason: str, indicators: dict) -> None:
        instrument = self._ce_instrument if signal == Signal.BUY_CE else self._pe_instrument

        with self._get_lock():
            raw = self._get_raw_state()
            if raw.position is not None or raw.trades_today >= MAX_TRADES_PER_DAY:
                return
            raw.trades_today += 1

        state = self._get_state()
        mode  = state.trading_mode

        try:
            if mode == "PAPER":
                ltp = state.ce_ltp if signal == Signal.BUY_CE else state.pe_ltp
                if ltp <= 0:
                    logger.warning("%s option LTP not available for paper entry", self._instrument_name)
                    with self._get_lock():
                        self._get_raw_state().trades_today -= 1
                    return

                self._paper_trade_counter += 1
                order_id  = f"PAPER-{self._paper_trade_counter:03d}"
                avg_price = ltp
                logger.info(
                    "[PAPER] %s ENTRY | %s | price=%.2f | %s",
                    self._instrument_name, instrument["tradingsymbol"], avg_price, reason,
                )
            else:
                kite      = self._kite or require_authenticated_client()
                order_id  = place_entry_order(kite, instrument, signal)
                avg_price = get_average_price(kite, order_id)
                logger.info(
                    "[LIVE] %s ENTRY | %s | price=%.2f | order=%s",
                    self._instrument_name, instrument["tradingsymbol"], avg_price, order_id,
                )

            option_type = signal.value[-2:]
            sl_price    = round(avg_price * (1 - INITIAL_SL_PCT / 100), 2)
            sl_pct      = INITIAL_SL_PCT

            candles_snap = state.candles
            eff = 0.0
            if len(candles_snap) >= 10:
                recent = candles_snap[-10:]
                max_h  = max(c.high for c in recent)
                min_l  = min(c.low  for c in recent)
                rng    = max_h - min_l
                if rng > 0:
                    eff = round(abs(recent[-1].close - recent[0].close) / rng, 3)

            lot_size = int(instrument.get("lot_size") or self._cfg["lot_size"])
            position = PositionInfo(
                option_symbol=instrument["tradingsymbol"],
                instrument_token=instrument["instrument_token"],
                option_type=option_type,
                strike=int(instrument["strike"]),
                expiry=instrument["expiry"],
                entry_price=avg_price,
                qty=lot_size,
                order_id=order_id,
                entry_time=datetime.now(IST),
                reason_for_entry=reason,
                current_price=avg_price,
                trailing_sl_price=sl_price,
                highest_price_seen=avg_price,
                nifty_spot_entry=state.nifty_spot,
                vwap_entry=indicators.get("vwap", 0.0),
                ema20_entry=indicators.get("ema20") or 0.0,
                rsi14_entry=indicators.get("rsi14") or 0.0,
                market_state_entry=indicators.get("market_state", "UNKNOWN"),
                efficiency_entry=eff,
            )
            logger.info(
                "%s ENTRY CONFIRMED | %s | entry=%.2f sl=%.2f (%.1f%%) | %s",
                self._instrument_name, instrument["tradingsymbol"],
                avg_price, sl_price, sl_pct, reason,
            )
            self._update_state(position=position)

        except TokenException:
            logger.error("%s token expired during entry", self._instrument_name)
            with self._get_lock():
                self._get_raw_state().trades_today -= 1
            self._update_state(engine_running=False, error_message="Token expired. Re-authenticate via /login.")

        except Exception as e:
            logger.error("%s entry failed: %s", self._instrument_name, e)
            with self._get_lock():
                self._get_raw_state().trades_today -= 1
            self._update_state(error_message=str(e))

    # ------------------------------------------------------------------
    # Exit execution
    # ------------------------------------------------------------------

    def _execute_exit(self, reason: str, forced: bool = False) -> None:
        state    = self._get_state()
        position = state.position
        if position is None:
            return

        exit_price = position.current_price
        if exit_price <= 0:
            exit_price = position.entry_price

        mode = state.trading_mode

        try:
            if mode == "PAPER":
                logger.info(
                    "[PAPER] %s EXIT | %s | entry=%.2f exit=%.2f | reason=%s",
                    self._instrument_name, position.option_symbol,
                    position.entry_price, exit_price, reason,
                )
            else:
                kite = self._kite or require_authenticated_client()
                if not forced or verify_position_exists(kite, position.option_symbol):
                    place_exit_order(kite, position, reason)

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
                instrument=self._instrument_name,
            )

            pnl = calculate_pnl(position.entry_price, exit_price, position.qty)
            self._update_state(
                position=None,
                exit_reason=reason,
                exit_price=exit_price,
                pnl=pnl,
            )
            logger.info(
                "%s EXIT COMPLETE | reason=%s | PnL: ₹%.2f (%.1f%%)",
                self._instrument_name, reason, pnl["pnl_rupees"], pnl["pnl_pct"],
            )

        except TokenException:
            logger.error("%s token expired during exit — check Zerodha immediately!", self._instrument_name)
            self._update_state(
                engine_running=False,
                error_message="Token expired during exit! Check Zerodha app immediately.",
            )
        except Exception as e:
            logger.error("%s exit failed: %s — will retry", self._instrument_name, e)
            self._update_state(error_message=f"Exit failed: {e}")

    # ------------------------------------------------------------------
    # Monitoring loop
    # ------------------------------------------------------------------

    def _monitoring_loop(self) -> None:
        logger.info("%s monitoring loop started", self._instrument_name)
        _ltp_fallback_tick = 0
        while True:
            state = self._get_state()

            if not state.engine_running:
                break

            if state.position is not None:
                self._check_position_exits(state)

            _ltp_fallback_tick += 1
            if _ltp_fallback_tick >= 30:
                _ltp_fallback_tick = 0
                if (state.ce_ltp == 0 or state.pe_ltp == 0) and self._ce_instrument and self._pe_instrument:
                    self._fetch_option_ltp_rest()

            time_module.sleep(1)

        logger.info("%s monitoring loop terminated", self._instrument_name)

    def _fetch_option_ltp_rest(self) -> None:
        try:
            ce_sym  = f"NFO:{self._ce_instrument['tradingsymbol']}"
            pe_sym  = f"NFO:{self._pe_instrument['tradingsymbol']}"
            ltp_data = self._kite.ltp([ce_sym, pe_sym])
            ce_ltp   = ltp_data.get(ce_sym, {}).get("last_price", 0)
            pe_ltp   = ltp_data.get(pe_sym, {}).get("last_price", 0)
            with self._get_lock():
                raw = self._get_raw_state()
                if ce_ltp > 0:
                    raw.ce_ltp = ce_ltp
                if pe_ltp > 0:
                    raw.pe_ltp = pe_ltp
            logger.debug("%s REST LTP fallback | CE=%.2f PE=%.2f", self._instrument_name, ce_ltp, pe_ltp)
        except Exception as e:
            logger.debug("%s REST LTP fallback failed (non-fatal): %s", self._instrument_name, e)

    def _check_position_exits(self, state: TradingState) -> None:
        position = state.position
        if position is None:
            return

        with self._get_lock():
            raw_pos = self._get_raw_state().position
            if raw_pos is None:
                return
            should_exit, reason = check_exit_conditions(raw_pos)

        if not should_exit:
            candles    = state.candles
            indicators = get_latest_indicators(candles) if len(candles) >= 22 else {}
            if indicators.get("enough_data") and detect_opposite_signal(
                candles,
                position.option_type,
                indicators.get("vwap", 0),
                indicators.get("ema20_series", []),
                indicators.get("market_state", "UNKNOWN"),
            ):
                reason     = "OPPOSITE_SIGNAL"
                should_exit = True

        if should_exit:
            self._execute_exit(reason=reason)


# ---------------------------------------------------------------------------
# Engine singletons — one per instrument, shared for the app lifetime
# ---------------------------------------------------------------------------

# Nifty engine uses the module-level state functions (backward-compat)
_nifty_state_mgr = InstrumentStateManager("NIFTY")
_nifty_engine    = TradingEngine("NIFTY",     _nifty_state_mgr)

_banknifty_state_mgr = InstrumentStateManager("BANKNIFTY")
_banknifty_engine    = TradingEngine("BANKNIFTY", _banknifty_state_mgr)


def get_engine(instrument: str = "NIFTY") -> TradingEngine:
    return _banknifty_engine if instrument.upper() == "BANKNIFTY" else _nifty_engine


def get_nifty_engine() -> TradingEngine:
    return _nifty_engine


def get_banknifty_engine() -> TradingEngine:
    return _banknifty_engine
