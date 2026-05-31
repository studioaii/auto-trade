"""
BankNifty 2.0 — Standalone trading engine.

Independent state, strategy, and exits. Shares only:
  • The KiteConnect client (for order placement and instrument lookup)
  • The MarketDataService WebSocket singleton (multi-route routing)

Same BankNifty underlying tokens, same ATM strike picking, but a wholly
separate position/state machine and strategy logic. Always runs PAPER mode.

This module deliberately does NOT subclass v1's TradingEngine — the entry/exit
flows are too different. The minimal shared surface is wrapped in helper calls.
"""
from __future__ import annotations

import copy
import logging
import threading
import time as time_module
from dataclasses import dataclass
from datetime import datetime, time, timedelta, date as date_type
from typing import Optional
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

from config import API_KEY, TRADING_MODE, INSTRUMENT_CONFIG
from services.kite_service import require_authenticated_client
from services.trading_state import (
    Candle, PositionInfo, InstrumentStateManager, TradingState,
)
from services.instruments import (
    fetch_instruments, get_current_expiry_for_instrument, get_atm_strike,
    find_option_instrument, find_futures,
)
from services.indicators import get_latest_indicators, MIN_CANDLES, candle_body_pct
from services.strategy_v2 import (
    V2Signal, V2Model, V2Setup, PendingLimitOrder,
    DayContext, DayClass,
    classify_day, reclassify_chop_if_dead, update_consecutive_legs,
    do_not_enter_reasons, high_quality_score,
    model_allowed_by_day,
    evaluate_model_a_setup, maybe_fire_model_a,
    evaluate_model_b, evaluate_model_c, evaluate_model_d,
    volume_ratio,
)
from services.risk_manager_v2 import (
    V2PositionExtras, ExitDecision, ExitLayer,
    V2EntryGateInput, can_enter_trade_v2,
    derive_initial_sl_premium, derive_targets,
    evaluate_exit_v2, calc_pnl,
)
from services.paper_trade_v2 import log_trade_v2
from services.candle_logger_v2 import log_candle_v2
from services.entry_logger_v2 import log_attempt_v2
from services.market_data import (
    InstrumentSubscription, get_market_data_service,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Distinct instrument key used for routing/state isolation.
INSTRUMENT_NAME = "BANKNIFTY_2"
# Actual Zerodha NFO symbol name (instrument data uses "BANKNIFTY", not "BANKNIFTY_2").
_NFO_NAME = "BANKNIFTY"

# Quality-score gate
MIN_QUALITY_SCORE = 7


@dataclass
class V2DailyState:
    """Per-day runtime state (everything not in the engine's TradingState)."""
    realised_pnl: float = 0.0
    trades_today: int = 0
    first_trade_was_loss: bool = False
    first_trade_was_stall_or_break: bool = False
    last_exit_time: Optional[datetime] = None
    forced_lock: Optional[ExitLayer] = None
    pending_limit: Optional[PendingLimitOrder] = None
    day_ctx: DayContext = None
    last_chop_check_at: Optional[datetime] = None


class TradingEngineV2:
    """High-precision BankNifty engine. PAPER-only by design."""

    _POSITION_TICK_STALL_S = 30

    def __init__(self):
        # Mirror v1 engine's attribute so DailyScheduler can identify the engine
        # uniformly across both versions (it reads engine._instrument_name).
        self._instrument_name = INSTRUMENT_NAME
        self._cfg = INSTRUMENT_CONFIG[INSTRUMENT_NAME]
        self._state_mgr = InstrumentStateManager(INSTRUMENT_NAME)
        self._market_data = get_market_data_service()

        self._instruments: list[dict] = []
        self._ce_instrument: Optional[dict] = None
        self._pe_instrument: Optional[dict] = None
        self._index_token: int = self._cfg["index_token"]
        self._futures_token: int = 0
        self._futures_symbol: str = ""

        self._kite: Optional[KiteConnect] = None
        self._paper_counter = 0
        self._monitor_thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._last_candle_date: Optional[date_type] = None
        self._cached_indicators: dict = {}
        self._cached_indicators_at: Optional[datetime] = None
        self._last_position_tick_at: Optional[float] = None

        # v2-specific
        self._daily = V2DailyState()
        self._position_extras: Optional[V2PositionExtras] = None
        # Prev day's close — fetched at start, used by day classifier
        self._prev_close: Optional[float] = None

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    def _get_state(self) -> TradingState:
        return self._state_mgr.get_state()

    def _update_state(self, **kw) -> None:
        self._state_mgr.update_state(**kw)

    def _get_raw_state(self) -> TradingState:
        return self._state_mgr.get_raw_state()

    def _get_lock(self) -> threading.Lock:
        return self._state_mgr.get_lock()

    # ------------------------------------------------------------------
    # Mode helper — v2 ALWAYS paper
    # ------------------------------------------------------------------

    def _resolve_mode(self) -> str:
        if self._cfg.get("force_paper_mode", True):
            return "PAPER"
        return TRADING_MODE.upper() if TRADING_MODE.upper() in ("PAPER", "LIVE") else "PAPER"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, kite: KiteConnect) -> dict:
        with self._start_lock:
            with self._get_lock():
                raw = self._get_raw_state()
                if raw.engine_running:
                    raise RuntimeError(f"{INSTRUMENT_NAME} engine is already running")
                raw.engine_running = True
        try:
            return self._start_impl(kite)
        except Exception:
            self._update_state(engine_running=False)
            raise

    def _start_impl(self, kite: KiteConnect) -> dict:
        mode = self._resolve_mode()
        self._kite = kite
        self._last_position_tick_at = None

        # Reset state + per-day v2 state
        self._state_mgr.reset_daily_state(mode=mode)
        self._daily = V2DailyState(day_ctx=DayContext())
        self._position_extras = None

        logger.info("Starting BankNifty 2.0 engine in %s mode", mode)

        # Instruments + ATM
        self._instruments = fetch_instruments(kite, _NFO_NAME)
        expiry = get_current_expiry_for_instrument(self._instruments, _NFO_NAME)
        ltp_data = kite.ltp(self._cfg["ltp_symbol"])
        spot = ltp_data[self._cfg["ltp_symbol"]]["last_price"]
        atm = get_atm_strike(spot, self._cfg["strike_interval"])
        self._update_state(nifty_spot=spot)

        self._ce_instrument = find_option_instrument(self._instruments, expiry, atm, "CE")
        self._pe_instrument = find_option_instrument(self._instruments, expiry, atm, "PE")

        # Futures contract for candles
        try:
            fut = find_futures(kite, _NFO_NAME)
            self._futures_token = fut["instrument_token"]
            self._futures_symbol = fut["tradingsymbol"]
            logger.info("BN2 futures: %s (token=%s)", self._futures_symbol, self._futures_token)
        except Exception as e:
            logger.warning("BN2 futures lookup failed: %s — using index for candles", e)
            self._futures_token = 0
            self._futures_symbol = ""

        # Pre-fetch previous close for day classifier
        self._prev_close = self._fetch_prev_close()
        self._daily.day_ctx.prev_close = self._prev_close

        logger.info(
            "BN2 | spot=%.1f ATM=%d | CE=%s PE=%s | expiry=%s | prev_close=%s",
            spot, atm,
            self._ce_instrument["tradingsymbol"],
            self._pe_instrument["tradingsymbol"],
            expiry, self._prev_close,
        )

        option_tokens = [
            self._ce_instrument["instrument_token"],
            self._pe_instrument["instrument_token"],
        ]

        # Preload candles (shared logic with v1 — duplicated to keep engines decoupled)
        self._load_session_candles()

        # Register on the SHARED WebSocket with a DIFFERENT instrument key.
        # Token overlap with BANKNIFTY v1 is handled by market_data's multi-routing list.
        subscription = InstrumentSubscription(
            instrument_name=INSTRUMENT_NAME,
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
            name=f"TradingMonitor-{INSTRUMENT_NAME}",
            daemon=True,
        )
        self._monitor_thread.start()

        return {
            "instrument": INSTRUMENT_NAME,
            "mode":       mode,
            "atm_strike": atm,
            "expiry":     str(expiry),
            "ce":         self._ce_instrument["tradingsymbol"],
            "pe":         self._pe_instrument["tradingsymbol"],
        }

    def stop(self, kite: Optional[KiteConnect] = None) -> None:
        try:
            state = self._get_state()
            if state.position is not None:
                logger.info("BN2 stopping — closing open position first")
                self._execute_exit(layer=ExitLayer.MANUAL_STOP, reason="MANUAL_STOP", forced=True)
            self._market_data.unregister_instrument(INSTRUMENT_NAME)
        except Exception as e:
            logger.error("BN2 stop encountered error: %s", e)
            self._update_state(error_message=f"Stop error: {e}")
        finally:
            self._update_state(engine_running=False)
            logger.info("BN2 trading engine stopped")

    # ------------------------------------------------------------------
    # Status (for /status endpoint + dashboard)
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        state = self._get_state()

        pnl_info = None
        if state.position and state.position.current_price > 0:
            pnl_info = calc_pnl(
                state.position.entry_price,
                state.position.current_price,
                state.position.qty,
            )
        elif state.pnl:
            pnl_info = state.pnl

        position_info = None
        if state.position:
            p = state.position
            position_info = {
                "symbol":       p.option_symbol,
                "option_type":  p.option_type,
                "strike":       p.strike,
                "expiry":       str(p.expiry),
                "entry_price":  p.entry_price,
                "current_price":p.current_price,
                "qty":          p.qty,
                "entry_time":   p.entry_time.strftime("%H:%M:%S") if p.entry_time else None,
                "reason_entry": p.reason_for_entry,
                "model":        self._position_extras.model if self._position_extras else "",
                "partial_booked": self._position_extras.partial_booked if self._position_extras else False,
                "runner_trail_sl":
                    round(self._position_extras.runner_trail_sl, 2)
                    if (self._position_extras and self._position_extras.runner_trail_active)
                    else None,
                "structure_sl_premium":
                    round(self._position_extras.structure_sl_premium, 2)
                    if self._position_extras else None,
                "mfe_pct":
                    round(self._position_extras.max_pnl_pct_seen, 2)
                    if self._position_extras else None,
            }

        ind_snap = {}
        ind = self._cached_indicators if self._cached_indicators.get("enough_data") else {}
        if not ind and len(state.candles) >= MIN_CANDLES:
            ind = get_latest_indicators(state.candles)
        if ind.get("enough_data"):
            ind_snap = {
                "vwap":  ind.get("vwap"),
                "ema20": round(ind["ema20"], 2) if ind.get("ema20") else None,
                "rsi14": round(ind["rsi14"], 2) if ind.get("rsi14") else None,
                "vol_surge": ind.get("volume_surge"),
            }

        instruments_info = None
        if self._ce_instrument:
            instruments_info = {
                "ce":            self._ce_instrument["tradingsymbol"],
                "pe":            self._pe_instrument["tradingsymbol"] if self._pe_instrument else None,
                "atm_strike":    int(self._ce_instrument["strike"]),
                "candle_source": self._futures_symbol or f"{INSTRUMENT_NAME} INDEX (no volume)",
            }

        ctx = self._daily.day_ctx
        day_info = {
            "class":   ctx.day_class.value if ctx else "UNKNOWN",
            "gap_pct": round(ctx.gap_pct, 3) if ctx and ctx.gap_pct is not None else None,
            "vwap_drift_pct":
                round(ctx.vwap_drift_at_950, 3)
                if ctx and ctx.vwap_drift_at_950 is not None else None,
            "or_high": ctx.or_high if ctx else None,
            "or_low":  ctx.or_low if ctx else None,
        } if ctx else {}

        pending = self._daily.pending_limit
        pending_info = None
        if pending:
            pending_info = {
                "model":          "A",
                "side":           pending.signal.value,
                "trigger_spot":   round(pending.trigger_spot, 2),
                "candles_alive":  pending.candles_alive,
                "structure_sl":   round(pending.structure_sl_spot, 2),
                "reason":         pending.reason,
            }

        return {
            "instrument":         INSTRUMENT_NAME,
            "strategy":           "BANKNIFTY_2_HIGH_PRECISION_V1",
            "mode":               state.trading_mode,
            "engine_running":     state.engine_running,
            "trades_today":       self._daily.trades_today,
            "max_trades":         self._cfg.get("max_trades_per_day", 2),
            "realised_pnl":       round(self._daily.realised_pnl, 2),
            "daily_loss_lock":    self._cfg.get("daily_loss_lock_rupees", -6000.0),
            "daily_profit_lock":  self._cfg.get("daily_profit_lock_rupees", 15000.0),
            "forced_lock":        self._daily.forced_lock.value if self._daily.forced_lock else None,
            "nifty_spot":         round(state.nifty_spot, 2),
            "ce_ltp":             round(state.ce_ltp, 2),
            "pe_ltp":             round(state.pe_ltp, 2),
            "market_state":       state.market_state,
            "last_signal":        state.last_signal,
            "last_candle_time":   state.last_candle_time.strftime("%H:%M") if state.last_candle_time else None,
            "candle_count":       len(state.candles),
            "candles_needed":     MIN_CANDLES,
            "position":           position_info,
            "pnl":                pnl_info,
            "exit_reason":        state.exit_reason,
            "exit_price":         state.exit_price,
            "error":              state.error_message,
            "indicators":         ind_snap,
            "instruments":        instruments_info,
            "day_context":        day_info,
            "pending_limit":      pending_info,
        }

    # ------------------------------------------------------------------
    # Historical session-candle preload (same logic as v1)
    # ------------------------------------------------------------------

    def _fetch_prev_close(self) -> Optional[float]:
        try:
            now = datetime.now(IST)
            d = now.date() - timedelta(days=1)
            for _ in range(10):
                while d.weekday() >= 5:
                    d -= timedelta(days=1)
                from_dt = datetime(d.year, d.month, d.day, 9, 15, 0, tzinfo=IST)
                to_dt   = datetime(d.year, d.month, d.day, 15, 30, 0, tzinfo=IST)
                try:
                    rows = self._kite.historical_data(
                        instrument_token=self._futures_token or self._index_token,
                        from_date=from_dt, to_date=to_dt, interval="day",
                    )
                except Exception:
                    rows = []
                if rows:
                    return float(rows[-1]["close"])
                d -= timedelta(days=1)
        except Exception:
            logger.debug("BN2 prev-close fetch failed", exc_info=True)
        return None

    def _load_session_candles(self) -> None:
        try:
            now = datetime.now(IST)
            pre_market = now.hour < 9 or (now.hour == 9 and now.minute < 15)
            candle_token = self._futures_token or self._index_token
            today = now.date()

            today_candles: list[Candle] = []
            if not pre_market:
                session_start = datetime(today.year, today.month, today.day, 9, 15, 0, tzinfo=IST)
                current_slot_start = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
                rows = self._kite.historical_data(
                    instrument_token=candle_token,
                    from_date=session_start, to_date=now, interval="5minute",
                )
                for row in rows:
                    ts = row["date"]
                    if hasattr(ts, "astimezone"):
                        ts = ts.astimezone(IST)
                    if ts >= current_slot_start:
                        continue
                    today_candles.append(Candle(
                        timestamp=ts, open=row["open"], high=row["high"],
                        low=row["low"], close=row["close"], volume=row.get("volume", 0),
                    ))

            all_candles = today_candles
            if len(today_candles) < MIN_CANDLES:
                seed_count = MIN_CANDLES - len(today_candles) + 5
                d = today - timedelta(days=1)
                seed: list[Candle] = []
                for _ in range(10):
                    while d.weekday() >= 5:
                        d -= timedelta(days=1)
                    prev_start = datetime(d.year, d.month, d.day, 9, 15, 0, tzinfo=IST)
                    prev_end   = datetime(d.year, d.month, d.day, 15, 30, 0, tzinfo=IST)
                    try:
                        rows = self._kite.historical_data(
                            instrument_token=candle_token,
                            from_date=prev_start, to_date=prev_end, interval="5minute",
                        )
                        prev_candles = []
                        for row in rows:
                            ts = row["date"]
                            if hasattr(ts, "astimezone"):
                                ts = ts.astimezone(IST)
                            prev_candles.append(Candle(
                                timestamp=ts, open=row["open"], high=row["high"],
                                low=row["low"], close=row["close"], volume=row.get("volume", 0),
                            ))
                        if prev_candles:
                            needed = seed_count - len(seed)
                            seed = prev_candles[-needed:] + seed
                            if len(seed) >= seed_count:
                                break
                    except Exception:
                        pass
                    d -= timedelta(days=1)
                all_candles = seed + today_candles

            if not all_candles:
                return

            with self._get_lock():
                raw = self._get_raw_state()
                if raw.candles:
                    existing = {c.timestamp for c in raw.candles}
                    new_only = [c for c in all_candles if c.timestamp not in existing]
                    raw.candles = sorted(raw.candles + new_only, key=lambda c: c.timestamp)
                else:
                    raw.candles = all_candles
                raw.last_candle_time = raw.candles[-1].timestamp

            if len(all_candles) >= MIN_CANDLES:
                ind = get_latest_indicators(all_candles)
                if ind.get("enough_data"):
                    self._update_state(market_state=ind["market_state"])
        except Exception as e:
            logger.warning("BN2 session preload failed: %s", e)

    # ------------------------------------------------------------------
    # ATM reselection (independent from v1)
    # ------------------------------------------------------------------

    def _recalculate_atm(self) -> None:
        state = self._get_state()
        if state.position is not None or state.nifty_spot <= 0 or not self._ce_instrument:
            return
        new_atm = get_atm_strike(state.nifty_spot, self._cfg["strike_interval"])
        cur_atm = int(self._ce_instrument["strike"])
        if new_atm == cur_atm:
            return
        if abs(state.nifty_spot - cur_atm) < self._cfg["strike_interval"] * 0.40:
            return

        logger.info("BN2 ATM shift | %d → %d | spot=%.1f", cur_atm, new_atm, state.nifty_spot)
        try:
            expiry = get_current_expiry_for_instrument(self._instruments, _NFO_NAME)
            new_ce = find_option_instrument(self._instruments, expiry, new_atm, "CE")
            new_pe = find_option_instrument(self._instruments, expiry, new_atm, "PE")
        except ValueError as e:
            logger.warning("BN2 ATM reselection failed: %s", e)
            return

        old_tokens = [self._ce_instrument["instrument_token"], self._pe_instrument["instrument_token"]]
        new_tokens = [new_ce["instrument_token"], new_pe["instrument_token"]]
        with self._get_lock():
            raw = self._get_raw_state()
            raw.ce_ltp = 0.0
            raw.pe_ltp = 0.0
        self._ce_instrument = new_ce
        self._pe_instrument = new_pe
        self._market_data.swap_option_subscriptions(INSTRUMENT_NAME, old_tokens, new_tokens)

    # ------------------------------------------------------------------
    # Tick callbacks
    # ------------------------------------------------------------------

    def _on_spot_update(self, spot: float) -> None:
        self._update_state(nifty_spot=spot)
        # Check Model-A pending limit on every tick (price-sensitive)
        self._check_pending_limit_on_spot(spot)

    def _on_option_ltp(self, token: int, ltp: float) -> None:
        with self._get_lock():
            raw = self._get_raw_state()
            if self._ce_instrument and token == self._ce_instrument["instrument_token"]:
                raw.ce_ltp = ltp
            elif self._pe_instrument and token == self._pe_instrument["instrument_token"]:
                raw.pe_ltp = ltp
            if raw.position and raw.position.instrument_token == token:
                raw.position.current_price = ltp
                self._last_position_tick_at = time_module.monotonic()

    # ------------------------------------------------------------------
    # Main candle handler
    # ------------------------------------------------------------------

    def _on_candle_ready(self, candle: Candle) -> None:
        # Append candle to state (de-dup)
        with self._get_lock():
            raw = self._get_raw_state()
            if raw.candles and raw.candles[-1].timestamp == candle.timestamp:
                return
            raw.candles.append(candle)
            raw.last_candle_time = candle.timestamp

        self._recalculate_atm()

        state = self._get_state()
        indicators = get_latest_indicators(state.candles)
        if indicators.get("enough_data"):
            self._update_state(market_state=indicators["market_state"])
            self._cached_indicators = indicators
            self._cached_indicators_at = candle.timestamp

        # Per-day reset
        candle_date = candle.timestamp.date()
        if candle_date != self._last_candle_date:
            self._last_candle_date = candle_date
            self._daily = V2DailyState(day_ctx=DayContext(prev_close=self._prev_close))

        ctx = self._daily.day_ctx
        update_consecutive_legs(ctx, candle)

        # Increment position-candle counter
        if self._position_extras is not None:
            self._position_extras.candles_since_entry += 1

        # Day classification at 09:50 (or first candle after)
        today_candles = [c for c in state.candles if c.timestamp.date() == candle_date]
        now = datetime.now(IST)
        entry_start = time(*self._cfg.get("entry_window_start", (9, 50)))
        if ctx.day_class == DayClass.UNKNOWN and candle.timestamp.time() >= entry_start:
            cls = classify_day(ctx, today_candles, indicators.get("vwap", 0.0), self._cfg)
            logger.info(
                "BN2 day classified: %s | gap=%.2f%% drift=%.2f%% OR=[%s, %s]",
                cls.value,
                ctx.gap_pct or 0, ctx.vwap_drift_at_950 or 0,
                ctx.or_low, ctx.or_high,
            )

        # Hourly chop re-check
        if self._daily.last_chop_check_at is None:
            self._daily.last_chop_check_at = candle.timestamp
        else:
            mins = (candle.timestamp - self._daily.last_chop_check_at).total_seconds() / 60.0
            if reclassify_chop_if_dead(ctx, today_candles, indicators.get("vwap", 0.0), int(mins)):
                logger.info("BN2 downgraded to CHOP (dead market)")
                self._daily.last_chop_check_at = candle.timestamp

        # Persist last_signal logging for status panel — recomputed below
        last_signal_value = "NO_SIGNAL"
        last_model_value  = V2Model.NONE.value

        # Always log this candle to BN2 candle log (regardless of trade)
        atm_strike = int(self._ce_instrument["strike"]) if self._ce_instrument else None
        position_qty = state.position.qty if state.position else 0
        partial_booked = bool(self._position_extras and self._position_extras.partial_booked)
        log_candle_v2(
            candle=candle,
            indicators=indicators,
            state=state,
            atm_strike=atm_strike,
            day_class=ctx.day_class.value,
            gap_pct=ctx.gap_pct,
            vwap_drift_pct=ctx.vwap_drift_at_950,
            model_a_pending=self._daily.pending_limit is not None,
            model_a_trigger=self._daily.pending_limit.trigger_spot if self._daily.pending_limit else None,
            model_a_side=self._daily.pending_limit.signal.value if self._daily.pending_limit else "",
            signal_v2=last_signal_value,
            model_v2=last_model_value,
            position_qty=position_qty,
            partial_booked=partial_booked,
        )

        # If we have an open position, only manage exits here (entries gated by gate fn)
        if state.position is not None:
            self._check_exits_on_candle(state, indicators)
            return

        # Age the pending limit, cancel if expired
        if self._daily.pending_limit is not None:
            self._daily.pending_limit.candles_alive += 1
            if self._daily.pending_limit.candles_alive > self._cfg.get("model_a_setup_ttl_candles", 6):
                logger.info("BN2 Model A pending expired — cancelled")
                self._log_attempt(
                    when=now, model="A", signal=self._daily.pending_limit.signal.value,
                    outcome="LIMIT_CANCELLED",
                    spot=state.nifty_spot, atm_strike=atm_strike or 0,
                    option_ltp=0.0, vwap=indicators.get("vwap") or 0.0,
                    rsi14=indicators.get("rsi14") or 0.0,
                    body_pct=candle_body_pct(candle),
                    vol_ratio=volume_ratio(state.candles),
                    day_class=ctx.day_class.value, quality_score=0,
                    skip_reasons=["ttl expired"], reason=self._daily.pending_limit.reason,
                )
                self._daily.pending_limit = None

        # Entry gate
        gate = V2EntryGateInput(
            engine_running=state.engine_running,
            realized_pnl=self._daily.realised_pnl,
            trades_today=self._daily.trades_today,
            first_trade_was_loss=self._daily.first_trade_was_loss,
            first_trade_was_stall_or_break=self._daily.first_trade_was_stall_or_break,
            last_exit_time=self._daily.last_exit_time,
            forced_lock=self._daily.forced_lock,
            has_open_position=state.position is not None,
        )
        allowed, reason = can_enter_trade_v2(gate, self._cfg, now)
        if not allowed:
            return

        if not indicators.get("enough_data"):
            return

        # Day must be classified
        if ctx.day_class == DayClass.UNKNOWN:
            return

        # Try Model B / C / D first (firing-on-candle models); then Model A setup detect
        candidate: Optional[V2Setup] = None
        for model_enum, evaluator in (
            (V2Model.B, evaluate_model_b),
            (V2Model.C, evaluate_model_c),
            (V2Model.D, evaluate_model_d),
        ):
            if not model_allowed_by_day(model_enum, ctx.day_class):
                continue
            setup = evaluator(state.candles, indicators, self._cfg, now)
            if setup is None:
                continue
            # Apply DO-NOT-ENTER + HIGH-QUALITY checklists
            skip = do_not_enter_reasons(state.candles, indicators, ctx, self._cfg, now, setup.signal)
            if skip:
                self._log_attempt(
                    when=now, model=setup.model.value, signal=setup.signal.value,
                    outcome="SKIPPED",
                    spot=state.nifty_spot, atm_strike=atm_strike or 0,
                    option_ltp=state.ce_ltp if setup.signal == V2Signal.BUY_CE else state.pe_ltp,
                    vwap=indicators.get("vwap") or 0.0,
                    rsi14=indicators.get("rsi14") or 0.0,
                    body_pct=candle_body_pct(candle),
                    vol_ratio=volume_ratio(state.candles),
                    day_class=ctx.day_class.value, quality_score=0,
                    skip_reasons=skip, reason=setup.reason,
                )
                continue
            qscore, qfailed = high_quality_score(
                state.candles, indicators, ctx, self._cfg, now, setup.signal, setup.model
            )
            if qscore < MIN_QUALITY_SCORE:
                self._log_attempt(
                    when=now, model=setup.model.value, signal=setup.signal.value,
                    outcome="SKIPPED",
                    spot=state.nifty_spot, atm_strike=atm_strike or 0,
                    option_ltp=state.ce_ltp if setup.signal == V2Signal.BUY_CE else state.pe_ltp,
                    vwap=indicators.get("vwap") or 0.0,
                    rsi14=indicators.get("rsi14") or 0.0,
                    body_pct=candle_body_pct(candle),
                    vol_ratio=volume_ratio(state.candles),
                    day_class=ctx.day_class.value, quality_score=qscore,
                    skip_reasons=[f"quality={qscore}/10"] + qfailed, reason=setup.reason,
                )
                continue
            candidate = setup
            break

        # If no candidate from B/C/D, attempt to register a Model-A setup
        if candidate is None and model_allowed_by_day(V2Model.A, ctx.day_class):
            if self._daily.pending_limit is None:
                pending = evaluate_model_a_setup(state.candles, indicators, self._cfg, now)
                if pending is not None:
                    self._daily.pending_limit = pending
                    logger.info("BN2 Model A setup placed | %s", pending.reason)
                    self._log_attempt(
                        when=now, model="A", signal=pending.signal.value,
                        outcome="LIMIT_PLACED",
                        spot=state.nifty_spot, atm_strike=atm_strike or 0,
                        option_ltp=state.ce_ltp if pending.signal == V2Signal.BUY_CE else state.pe_ltp,
                        vwap=indicators.get("vwap") or 0.0,
                        rsi14=indicators.get("rsi14") or 0.0,
                        body_pct=candle_body_pct(candle),
                        vol_ratio=volume_ratio(state.candles),
                        day_class=ctx.day_class.value, quality_score=0,
                        skip_reasons=[], reason=pending.reason,
                    )

        if candidate is not None:
            self._execute_entry(candidate, indicators, now)

    # ------------------------------------------------------------------
    # Model A: tick-driven trigger evaluation
    # ------------------------------------------------------------------

    def _check_pending_limit_on_spot(self, spot: float) -> None:
        """Called from spot tick callback. Only fires on candle CLOSE not on tick."""
        # We require candle-close confirmation, so this is intentionally a no-op
        # for now. A future tick-mode could rewire this to fire intra-candle.
        return

    def _try_fire_model_a_on_candle(
        self,
        state: TradingState,
        indicators: dict,
        now: datetime,
    ) -> Optional[V2Setup]:
        if self._daily.pending_limit is None or not state.candles:
            return None
        return maybe_fire_model_a(
            self._daily.pending_limit,
            state.nifty_spot,
            state.candles[-1],
            state.candles,
            self._cfg,
        )

    # ------------------------------------------------------------------
    # Entry execution (paper only)
    # ------------------------------------------------------------------

    def _execute_entry(self, setup: V2Setup, indicators: dict, now: datetime) -> None:
        state = self._get_state()
        instrument = self._ce_instrument if setup.signal == V2Signal.BUY_CE else self._pe_instrument
        if instrument is None:
            return

        # Resolve option premium (LTP from state, REST fallback if stale)
        ltp = state.ce_ltp if setup.signal == V2Signal.BUY_CE else state.pe_ltp
        if ltp <= 0:
            try:
                sym = f"NFO:{instrument['tradingsymbol']}"
                data = self._kite.ltp([sym])
                ltp = data.get(sym, {}).get("last_price", 0)
            except Exception as e:
                logger.warning("BN2 REST LTP fallback failed: %s", e)
        if ltp <= 0:
            logger.warning("BN2 option LTP not available — skipping entry")
            return

        # Position sizing
        full_lot = int(instrument.get("lot_size") or self._cfg["lot_size"])
        qty = full_lot
        # CHOP_DAY → half size on Model A; second trade → half size; entry after 13:00 → half size
        if self._daily.day_ctx.day_class == DayClass.CHOP:
            qty = max(1, full_lot // 2)
        if self._daily.trades_today >= 1 and self._cfg.get("second_trade_half_size", True):
            qty = max(1, full_lot // 2)
        if now.time() >= time(13, 0):
            qty = max(1, qty // 2) if qty == full_lot else qty
        if qty > full_lot:
            qty = full_lot

        # Initial SL from structure
        sl_premium, sl_pct = derive_initial_sl_premium(
            entry_premium=ltp,
            entry_spot=state.nifty_spot,
            structure_sl_spot=setup.structure_sl_spot,
            cfg=self._cfg,
        )
        partial_target, ceiling_target = derive_targets(now.time(), self._cfg)

        # Atomic claim
        with self._get_lock():
            raw = self._get_raw_state()
            if raw.position is not None:
                return
            self._daily.trades_today += 1

        self._paper_counter += 1
        order_id = f"BN2-PAPER-{self._paper_counter:03d}"
        option_type = "CE" if setup.signal == V2Signal.BUY_CE else "PE"
        position = PositionInfo(
            option_symbol=instrument["tradingsymbol"],
            instrument_token=instrument["instrument_token"],
            option_type=option_type,
            strike=int(instrument["strike"]),
            expiry=instrument["expiry"],
            entry_price=ltp,
            qty=qty,
            order_id=order_id,
            entry_time=now,
            reason_for_entry=setup.reason,
            current_price=ltp,
            trailing_sl_price=sl_premium,
            highest_price_seen=ltp,
            nifty_spot_entry=state.nifty_spot,
            vwap_entry=indicators.get("vwap", 0.0),
            ema20_entry=indicators.get("ema20") or 0.0,
            rsi14_entry=indicators.get("rsi14") or 0.0,
            market_state_entry=indicators.get("market_state", "UNKNOWN"),
            efficiency_entry=indicators.get("efficiency_ratio", 0.0),
        )

        # v2 extras
        last_candle = state.candles[-1] if state.candles else None
        extras = V2PositionExtras(
            model=setup.model.value,
            entry_spot=state.nifty_spot,
            entry_vwap=indicators.get("vwap", 0.0),
            entry_candle_low=last_candle.low if last_candle else 0.0,
            entry_candle_high=last_candle.high if last_candle else 0.0,
            structure_sl_premium=sl_premium,
            sl_pct=sl_pct,
            target_partial_pct=partial_target,
            target_ceiling_pct=ceiling_target,
        )

        self._update_state(position=position, last_signal=setup.signal.value)
        self._position_extras = extras
        self._last_position_tick_at = time_module.monotonic()

        # Clear any pending Model-A order (we're now in a position)
        if self._daily.pending_limit is not None:
            self._daily.pending_limit = None

        logger.info(
            "[BN2 PAPER] ENTRY | %s | %s @ %.2f qty=%d | sl=%.2f(%.1f%%) target_partial=%.0f%% ceiling=%.0f%% | %s",
            instrument["tradingsymbol"], option_type, ltp, qty,
            sl_premium, sl_pct, partial_target, ceiling_target, setup.reason,
        )

        self._log_attempt(
            when=now, model=setup.model.value, signal=setup.signal.value,
            outcome="FIRED",
            spot=state.nifty_spot, atm_strike=int(instrument["strike"]),
            option_ltp=ltp,
            vwap=indicators.get("vwap") or 0.0,
            rsi14=indicators.get("rsi14") or 0.0,
            body_pct=candle_body_pct(last_candle) if last_candle else 0.0,
            vol_ratio=volume_ratio(state.candles),
            day_class=self._daily.day_ctx.day_class.value,
            quality_score=10,  # passed full gate by reaching here
            skip_reasons=[], reason=setup.reason,
        )

    # ------------------------------------------------------------------
    # Exit evaluation per candle
    # ------------------------------------------------------------------

    def _check_exits_on_candle(self, state: TradingState, indicators: dict) -> None:
        if state.position is None or self._position_extras is None:
            return

        decision = evaluate_exit_v2(
            entry_price=state.position.entry_price,
            current_price=state.position.current_price or state.position.entry_price,
            entry_time=state.position.entry_time,
            option_type=state.position.option_type,
            qty_remaining=state.position.qty,
            extras=self._position_extras,
            candles=state.candles,
            vwap=indicators.get("vwap", 0.0),
            cfg=self._cfg,
            realized_pnl=self._daily.realised_pnl,
        )

        # Apply trailing-SL update without exit
        if not decision.should_exit and decision.new_trailing_sl_premium is not None:
            with self._get_lock():
                raw = self._get_raw_state()
                if raw.position is not None:
                    raw.position.trailing_sl_price = decision.new_trailing_sl_premium

        if decision.should_exit:
            self._execute_exit(
                layer=decision.layer,
                reason=decision.reason,
                qty_to_exit=decision.qty_to_exit,
            )

    # ------------------------------------------------------------------
    # Tick-driven exit checker (called every second from monitor loop)
    # ------------------------------------------------------------------

    def _check_exits_on_tick(self) -> None:
        state = self._get_state()
        if state.position is None or self._position_extras is None:
            return
        indicators = self._cached_indicators if self._cached_indicators.get("enough_data") else {}
        decision = evaluate_exit_v2(
            entry_price=state.position.entry_price,
            current_price=state.position.current_price or state.position.entry_price,
            entry_time=state.position.entry_time,
            option_type=state.position.option_type,
            qty_remaining=state.position.qty,
            extras=self._position_extras,
            candles=state.candles,
            vwap=indicators.get("vwap", 0.0),
            cfg=self._cfg,
            realized_pnl=self._daily.realised_pnl,
        )
        if not decision.should_exit and decision.new_trailing_sl_premium is not None:
            with self._get_lock():
                raw = self._get_raw_state()
                if raw.position is not None:
                    raw.position.trailing_sl_price = decision.new_trailing_sl_premium
        if decision.should_exit:
            self._execute_exit(
                layer=decision.layer,
                reason=decision.reason,
                qty_to_exit=decision.qty_to_exit,
            )

    # ------------------------------------------------------------------
    # Exit execution (paper)
    # ------------------------------------------------------------------

    def _execute_exit(
        self,
        layer: ExitLayer,
        reason: str,
        qty_to_exit: int = 0,
        forced: bool = False,
    ) -> None:
        with self._get_lock():
            raw = self._get_raw_state()
            if raw.position is None:
                return
            position = copy.copy(raw.position)
            extras = copy.copy(self._position_extras) if self._position_extras else None

            # Decide if this is partial or full
            is_partial = qty_to_exit > 0 and qty_to_exit < position.qty
            if is_partial:
                # Reduce qty in-place; keep the position open
                raw.position.qty = position.qty - qty_to_exit
                exit_qty = qty_to_exit
            else:
                exit_qty = position.qty
                raw.position = None

        exit_price = position.current_price if position.current_price > 0 else position.entry_price
        spot_exit = self._get_state().nifty_spot
        leg_pnl = (exit_price - position.entry_price) * exit_qty
        pnl_pct = ((exit_price - position.entry_price) / position.entry_price * 100.0
                   if position.entry_price > 0 else 0.0)

        # Update daily P&L + flags
        self._daily.realised_pnl += leg_pnl
        if not is_partial:
            self._daily.last_exit_time = datetime.now(IST)
            # Determine if first trade was loss / stall-break
            if self._daily.trades_today == 1:
                self._daily.first_trade_was_loss = leg_pnl < 0
                self._daily.first_trade_was_stall_or_break = layer in (
                    ExitLayer.STALL, ExitLayer.STRUCTURE_BREAK, ExitLayer.STAGNATION
                )
            # Latch day-lock
            if layer in (ExitLayer.DAILY_LOSS_LOCK, ExitLayer.DAILY_PROFIT_LOCK):
                self._daily.forced_lock = layer

        # Persist exit metadata to engine state for UI surfacing
        self._update_state(
            exit_reason=reason,
            exit_price=exit_price,
            pnl=calc_pnl(position.entry_price, exit_price, exit_qty),
        )

        leg_name = "PARTIAL" if is_partial else ("RUNNER" if (extras and extras.partial_booked) else "FULL")

        log_trade_v2(
            trade_number=self._daily.trades_today,
            leg=leg_name,
            model=extras.model if extras else "",
            day_class=self._daily.day_ctx.day_class.value,
            option_symbol=position.option_symbol,
            option_type=position.option_type,
            strike=position.strike,
            expiry=position.expiry,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=datetime.now(IST),
            exit_price=exit_price,
            qty=exit_qty,
            spot_entry=position.nifty_spot_entry,
            spot_exit=spot_exit,
            vwap_entry=position.vwap_entry,
            ema20_entry=position.ema20_entry or 0.0,
            rsi14_entry=position.rsi14_entry or 0.0,
            structure_sl_premium=extras.structure_sl_premium if extras else 0.0,
            sl_pct=extras.sl_pct if extras else 0.0,
            partial_booked=extras.partial_booked if extras else False,
            mfe_pct=extras.max_pnl_pct_seen if extras else 0.0,
            reason_for_entry=position.reason_for_entry,
            exit_layer=layer.value,
            reason_for_exit=reason,
        )

        logger.info(
            "[BN2 PAPER] EXIT %s | %s | %s | qty=%d entry=%.2f exit=%.2f | P&L: ₹%.2f (%.1f%%) | layer=%s",
            leg_name, position.option_symbol, position.option_type, exit_qty,
            position.entry_price, exit_price, leg_pnl, pnl_pct, layer.value,
        )

        # Mark partial booked on extras (still tracking runner)
        if is_partial and self._position_extras is not None:
            self._position_extras.partial_booked = True
            self._position_extras.partial_qty = exit_qty
            self._position_extras.partial_price = exit_price
            # After partial: tighten SL on runner to entry (breakeven)
            with self._get_lock():
                raw = self._get_raw_state()
                if raw.position is not None:
                    raw.position.trailing_sl_price = position.entry_price
            self._position_extras.runner_trail_sl = position.entry_price
            self._position_extras.runner_trail_active = True
        elif not is_partial:
            # Position fully closed
            self._position_extras = None
            self._last_position_tick_at = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log_attempt(self, **kw) -> None:
        try:
            log_attempt_v2(**kw)
        except Exception:
            logger.debug("BN2 attempt log failed", exc_info=True)

    # ------------------------------------------------------------------
    # Monitoring loop
    # ------------------------------------------------------------------

    def _monitoring_loop(self) -> None:
        logger.info("BN2 monitoring loop started")
        _ltp_tick = 0
        while True:
            state = self._get_state()
            if not state.engine_running:
                break

            # Tick-frequency exit checks (SL/trail/partial)
            if state.position is not None:
                self._check_exits_on_tick()
                stale = (
                    self._last_position_tick_at is None
                    or time_module.monotonic() - self._last_position_tick_at > self._POSITION_TICK_STALL_S
                )
                if stale and self._ce_instrument and self._pe_instrument:
                    self._fetch_option_ltp_rest()
                    self._last_position_tick_at = time_module.monotonic()

            # Per-second Model A trigger check off the latest spot
            if (self._daily.pending_limit is not None
                    and state.position is None
                    and state.candles):
                now = datetime.now(IST)
                setup = self._try_fire_model_a_on_candle(state, self._cached_indicators or {}, now)
                if setup is not None:
                    indicators = self._cached_indicators or get_latest_indicators(state.candles)
                    # Apply DO-NOT-ENTER to Model A fires too (closes climax/climactic-vol loophole).
                    skip = do_not_enter_reasons(
                        state.candles, indicators, self._daily.day_ctx, self._cfg, now, setup.signal
                    )
                    if skip:
                        last_candle = state.candles[-1]
                        atm = int(self._ce_instrument["strike"]) if self._ce_instrument else 0
                        self._log_attempt(
                            when=now, model=setup.model.value, signal=setup.signal.value,
                            outcome="SKIPPED",
                            spot=state.nifty_spot, atm_strike=atm,
                            option_ltp=state.ce_ltp if setup.signal == V2Signal.BUY_CE else state.pe_ltp,
                            vwap=indicators.get("vwap") or 0.0,
                            rsi14=indicators.get("rsi14") or 0.0,
                            body_pct=candle_body_pct(last_candle),
                            vol_ratio=volume_ratio(state.candles),
                            day_class=self._daily.day_ctx.day_class.value, quality_score=0,
                            skip_reasons=skip, reason=setup.reason,
                        )
                        logger.info("BN2 Model A fire blocked by DO-NOT-ENTER: %s", "; ".join(skip))
                        self._daily.pending_limit = None
                    else:
                        self._execute_entry(setup, indicators, now)

            _ltp_tick += 1
            if _ltp_tick >= 30:
                _ltp_tick = 0
                if (state.ce_ltp == 0 or state.pe_ltp == 0) and self._ce_instrument and self._pe_instrument:
                    self._fetch_option_ltp_rest()

            time_module.sleep(1)
        logger.info("BN2 monitoring loop terminated")

    def _fetch_option_ltp_rest(self) -> None:
        try:
            ce_sym = f"NFO:{self._ce_instrument['tradingsymbol']}"
            pe_sym = f"NFO:{self._pe_instrument['tradingsymbol']}"
            data = self._kite.ltp([ce_sym, pe_sym])
            ce = data.get(ce_sym, {}).get("last_price", 0)
            pe = data.get(pe_sym, {}).get("last_price", 0)
            with self._get_lock():
                raw = self._get_raw_state()
                if ce > 0:
                    raw.ce_ltp = ce
                if pe > 0:
                    raw.pe_ltp = pe
                if raw.position:
                    if raw.position.option_type == "CE" and ce > 0:
                        raw.position.current_price = ce
                    elif raw.position.option_type == "PE" and pe > 0:
                        raw.position.current_price = pe
        except Exception as e:
            logger.debug("BN2 REST LTP fallback failed: %s", e)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_banknifty2_engine = TradingEngineV2()


def get_banknifty2_engine() -> TradingEngineV2:
    return _banknifty2_engine
