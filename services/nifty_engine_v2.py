"""
NIFTY 2.0 — Standalone trading engine.

Independent state, strategy, and exits. Shares only:
  • The KiteConnect client (for instrument lookup + REST LTP fallback)
  • The MarketDataService WebSocket singleton (multi-route routing)

Same NIFTY underlying tokens as v1, same ATM strike picking, but a wholly
separate position/state machine. Strategy: NIFTY 1.0's VWAP+EMA breakout plus
the June-2026 analysis improvements (11:00 morning wait + regime gate, softened
2-close opposite-signal exit, −18% SL / +15% trailing, full instrumentation).
Always PAPER mode.
"""
from __future__ import annotations

import copy
import logging
import threading
import time as time_module
from datetime import datetime, time, timedelta, date as date_type
from typing import Optional
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect

from config import API_KEY, TRADING_MODE, INSTRUMENT_CONFIG
from services.trading_state import (
    Candle, PositionInfo, InstrumentStateManager, TradingState,
)
from services.instruments import (
    fetch_instruments, get_current_expiry_for_instrument, get_atm_strike,
    find_option_instrument, find_futures,
)
from services.indicators import (
    get_latest_indicators, MIN_CANDLES, candle_body_pct,
)
from services.nifty_strategy_v2 import (
    N2Signal, N2Model, N2Setup, N2DayContext,
    evaluate_signal, update_consecutive_legs, compute_opening_range,
    detect_opposite_signal_v1,
)
from services.nifty_risk_manager_v2 import (
    N2PositionExtras, N2ExitDecision, N2ExitLayer,
    N2EntryGateInput, can_enter_trade_n2,
    initial_sl_target, evaluate_exit_n2, calc_pnl_n2,
)
from services.nifty_paper_trade_v2 import log_trade_n2
from services.nifty_candle_logger_v2 import log_candle_n2
from services.nifty_entry_logger_v2 import log_attempt_n2
from services.nifty_instrumentation_v2 import log_post_exit_n2, log_shadow_signal_n2
from services.market_data import (
    InstrumentSubscription, get_market_data_service,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Distinct instrument key used for routing/state isolation.
INSTRUMENT_NAME = "NIFTY_2"
_NFO_NAME = "NIFTY"


# ---------------------------------------------------------------------------
# Per-day runtime state
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field

@dataclass
class N2DailyState:
    realised_pnl:        float = 0.0
    trades_today:        int = 0
    first_trade_was_sl:  bool = False
    last_exit_candle_idx: int = -1
    day_ctx: N2DayContext = field(default_factory=N2DayContext)


@dataclass
class OptionPathTracker:
    """Tracks an option's LTP path for instrumentation.

    POST_EXIT — the just-closed trade's option, for `post_exit_track_candles`
                candles after exit (did we exit too early/late?).
    SHADOW    — a genuine breakout signal a gate BLOCKED, tracked until the
                15:20 force-exit (would the blocked signal have won?).
    """
    kind:           str            # "POST_EXIT" | "SHADOW"
    tradingsymbol:  str
    option_type:    str
    strike:         int
    ref_price:      float          # POST_EXIT: exit price | SHADOW: would-be entry
    start_time:     datetime
    reason:         str
    max_ltp:        float
    min_ltp:        float
    last_ltp:       float
    candles_left:   int = 0        # POST_EXIT only
    candles_done:   int = 0
    until_force_exit: bool = False  # SHADOW: track until force-exit time
    trade_number:   int = 0         # POST_EXIT
    entry_price:    float = 0.0     # POST_EXIT
    signal:         str = ""        # SHADOW
    spot:           float = 0.0     # SHADOW
    vwap:           float = 0.0     # SHADOW
    rsi14:          float = 0.0     # SHADOW


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class NiftyEngineV2:
    """NIFTY 2.0 simple-entry tight-risk engine. PAPER-only by design."""

    _POSITION_TICK_STALL_S = 30

    def __init__(self):
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
        self._last_position_tick_at: Optional[float] = None

        self._daily = N2DailyState()
        self._position_extras: Optional[N2PositionExtras] = None

        # Instrumentation: post-exit + shadow (blocked-signal) option-path trackers.
        self._path_trackers: list[OptionPathTracker] = []
        self._tracker_lock = threading.Lock()

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
        self._daily = N2DailyState()
        self._position_extras = None

        logger.info("Starting NIFTY 2.0 engine in %s mode", mode)

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
            logger.info("N2 futures: %s (token=%s)", self._futures_symbol, self._futures_token)
        except Exception as e:
            logger.warning("N2 futures lookup failed: %s — using index for candles", e)
            self._futures_token = 0
            self._futures_symbol = ""

        logger.info(
            "N2 | spot=%.1f ATM=%d | CE=%s PE=%s | expiry=%s",
            spot, atm,
            self._ce_instrument["tradingsymbol"],
            self._pe_instrument["tradingsymbol"], expiry,
        )

        option_tokens = [
            self._ce_instrument["instrument_token"],
            self._pe_instrument["instrument_token"],
        ]

        self._load_session_candles()

        # Register on the SHARED WebSocket with a DIFFERENT instrument key.
        # Token overlap with NIFTY v1 is handled by market_data's multi-routing.
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
                logger.info("N2 stopping — closing open position first")
                self._execute_exit(layer=N2ExitLayer.MANUAL_STOP, reason="MANUAL_STOP")
            self._flush_path_trackers()
            self._market_data.unregister_instrument(INSTRUMENT_NAME)
        except Exception as e:
            logger.error("N2 stop encountered error: %s", e)
            self._update_state(error_message=f"Stop error: {e}")
        finally:
            self._update_state(engine_running=False)
            logger.info("N2 trading engine stopped")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        state = self._get_state()

        pnl_info = None
        if state.position and state.position.current_price > 0:
            pnl_info = calc_pnl_n2(
                state.position.entry_price,
                state.position.current_price,
                state.position.qty,
            )
        elif state.pnl:
            pnl_info = state.pnl

        position_info = None
        if state.position:
            p = state.position
            extras = self._position_extras
            position_info = {
                "symbol":              p.option_symbol,
                "option_type":         p.option_type,
                "strike":              p.strike,
                "expiry":              str(p.expiry),
                "entry_price":         p.entry_price,
                "current_price":       p.current_price,
                "qty":                 p.qty,
                "entry_time":          p.entry_time.strftime("%H:%M:%S") if p.entry_time else None,
                "reason_entry":        p.reason_for_entry,
                "model":               extras.model if extras else "",
                "hard_sl_premium":     round(extras.hard_sl_premium, 2) if extras else None,
                "trail_sl_premium":    round(extras.trail_sl_premium, 2) if extras else None,
                "breakeven_set":       extras.breakeven_set if extras else False,
                "trail_active":        extras.trail_active if extras else False,
                "mfe_pct":             round(extras.max_pnl_pct_seen, 2) if extras else None,
                "candles_since_entry": extras.candles_since_entry if extras else 0,
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
            "or_high":  ctx.or_high,
            "or_low":   ctx.or_low,
            "or_locked": ctx.or_locked,
            "orb_used": ctx.orb_used,
        }

        return {
            "instrument":         INSTRUMENT_NAME,
            "strategy":           "NIFTY_2_VWAP_EMA_BREAKOUT_V1PLUS",
            "mode":               state.trading_mode,
            "engine_running":     state.engine_running,
            "trades_today":       self._daily.trades_today,
            "max_trades":         self._cfg.get("max_trades_per_day", 2),
            "realised_pnl":       round(self._daily.realised_pnl, 2),
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
            "first_trade_was_sl": self._daily.first_trade_was_sl,
        }

    # ------------------------------------------------------------------
    # Historical session-candle preload
    # ------------------------------------------------------------------

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
            logger.warning("N2 session preload failed: %s", e)

    # ------------------------------------------------------------------
    # ATM reselection
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

        logger.info("N2 ATM shift | %d → %d | spot=%.1f", cur_atm, new_atm, state.nifty_spot)
        try:
            expiry = get_current_expiry_for_instrument(self._instruments, _NFO_NAME)
            new_ce = find_option_instrument(self._instruments, expiry, new_atm, "CE")
            new_pe = find_option_instrument(self._instruments, expiry, new_atm, "PE")
        except ValueError as e:
            logger.warning("N2 ATM reselection failed: %s", e)
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

        # Per-day reset
        candle_date = candle.timestamp.date()
        if candle_date != self._last_candle_date:
            self._last_candle_date = candle_date
            self._daily = N2DailyState()

        ctx = self._daily.day_ctx
        update_consecutive_legs(ctx, candle)
        if self._position_extras is not None:
            self._position_extras.candles_since_entry += 1

        # OR computation — lock once OR window has elapsed
        today_candles = [c for c in state.candles if c.timestamp.date() == candle_date]
        if not ctx.or_locked:
            or_end_t = time(*self._cfg.get("or_window_end", (9, 30)))
            # OR locks at the first candle whose timestamp > or_end (i.e. OR window has closed)
            if candle.timestamp.time() > or_end_t:
                oh, ol = compute_opening_range(
                    today_candles,
                    self._cfg.get("or_window_start", (9, 15)),
                    self._cfg.get("or_window_end",   (9, 30)),
                )
                if oh and ol:
                    ctx.or_high = oh
                    ctx.or_low  = ol
                    ctx.or_locked = True
                    logger.info("N2 OR locked | high=%.2f low=%.2f range=%.0f",
                                oh, ol, oh - ol)

        atm_strike = int(self._ce_instrument["strike"]) if self._ce_instrument else None
        now = datetime.now(IST)

        # Instrumentation: advance post-exit + shadow option-path trackers (1 REST/candle).
        self._update_path_trackers(now)

        # Always-on candle log row
        signal_value = "NO_SIGNAL"
        model_value  = N2Model.NONE.value
        skip_value   = ""
        trail_active = bool(self._position_extras and self._position_extras.trail_active)
        be_set       = bool(self._position_extras and self._position_extras.breakeven_set)

        # If we have an open position, manage exits and return
        if state.position is not None:
            self._check_exits_on_candle(state, indicators)
            log_candle_n2(
                candle=candle, indicators=indicators, state=state,
                atm_strike=atm_strike,
                or_high=ctx.or_high, or_low=ctx.or_low, orb_used=ctx.orb_used,
                signal_v2=signal_value, model_v2=model_value, skip_reason=skip_value,
                trail_active=trail_active, breakeven_set=be_set,
            )
            return

        # Entry gate
        gate = N2EntryGateInput(
            engine_running=state.engine_running,
            trades_today=self._daily.trades_today,
            first_trade_was_sl=self._daily.first_trade_was_sl,
            has_open_position=False,
            last_exit_candle_idx=self._daily.last_exit_candle_idx,
            current_candle_idx=len(state.candles) - 1,
        )
        allowed, gate_reason = can_enter_trade_n2(gate, self._cfg, now)

        # Always evaluate the signal (pure/cheap) so we can shadow-log any
        # genuine breakout a gate blocked, even when the entry gate denies.
        setup = (evaluate_signal(state.candles, indicators, ctx, self._cfg, now)
                 if indicators.get("enough_data") else None)

        if setup is not None and setup.signal != N2Signal.NO_SIGNAL and allowed:
            signal_value = setup.signal.value
            model_value  = setup.model.value
            self._execute_entry(setup, indicators, now)
        else:
            base = setup.base_signal if setup else N2Signal.NO_SIGNAL
            if base != N2Signal.NO_SIGNAL:
                # A real v1 breakout fired but was blocked — by the regime gate
                # (setup.signal cleared) or by the entry gate (e.g. before 11:00).
                block_reason = setup.skip_reason if setup.signal == N2Signal.NO_SIGNAL else gate_reason
                self._register_shadow(base, indicators, now, block_reason)
                skip_value = block_reason
            else:
                skip_value = (setup.skip_reason if setup else "not enough data") or (
                    gate_reason if not allowed else "")
            self._log_attempt(
                when=now, model="ANY",
                signal=(base.value if base != N2Signal.NO_SIGNAL else "NO_SIGNAL"),
                outcome="SKIPPED",
                spot=state.nifty_spot, atm_strike=atm_strike or 0,
                option_ltp=0.0,
                vwap=indicators.get("vwap") or 0.0,
                rsi14=indicators.get("rsi14") or 0.0,
                body_pct=candle_body_pct(candle),
                or_high=ctx.or_high or 0.0, or_low=ctx.or_low or 0.0,
                skip_reasons=[skip_value] if skip_value else [],
                reason="",
            )

        log_candle_n2(
            candle=candle, indicators=indicators, state=state,
            atm_strike=atm_strike,
            or_high=ctx.or_high, or_low=ctx.or_low, orb_used=ctx.orb_used,
            signal_v2=signal_value, model_v2=model_value, skip_reason=skip_value,
            trail_active=trail_active, breakeven_set=be_set,
        )

    # ------------------------------------------------------------------
    # Entry execution (paper)
    # ------------------------------------------------------------------

    def _execute_entry(self, setup: N2Setup, indicators: dict, now: datetime) -> None:
        state = self._get_state()
        instrument = self._ce_instrument if setup.signal == N2Signal.BUY_CE else self._pe_instrument
        if instrument is None:
            return

        ltp = state.ce_ltp if setup.signal == N2Signal.BUY_CE else state.pe_ltp
        if ltp <= 0:
            try:
                sym = f"NFO:{instrument['tradingsymbol']}"
                data = self._kite.ltp([sym])
                ltp = data.get(sym, {}).get("last_price", 0)
            except Exception as e:
                logger.warning("N2 REST LTP fallback failed: %s", e)
        if ltp <= 0:
            logger.warning("N2 option LTP not available — skipping entry")
            return

        qty = int(instrument.get("lot_size") or self._cfg["lot_size"])

        # SL + target
        hard_sl, target_px, sl_pct = initial_sl_target(ltp, self._cfg)

        # Atomic claim
        with self._get_lock():
            raw = self._get_raw_state()
            if raw.position is not None:
                return
            self._daily.trades_today += 1

        self._paper_counter += 1
        order_id = f"N2-PAPER-{self._paper_counter:03d}"
        option_type = "CE" if setup.signal == N2Signal.BUY_CE else "PE"

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
            trailing_sl_price=hard_sl,
            highest_price_seen=ltp,
            nifty_spot_entry=state.nifty_spot,
            vwap_entry=indicators.get("vwap", 0.0),
            ema20_entry=indicators.get("ema20") or 0.0,
            rsi14_entry=indicators.get("rsi14") or 0.0,
            market_state_entry=indicators.get("market_state", "UNKNOWN"),
            efficiency_entry=indicators.get("efficiency_ratio", 0.0),
        )

        last_candle = state.candles[-1] if state.candles else None
        extras = N2PositionExtras(
            model=setup.model.value,
            entry_spot=state.nifty_spot,
            entry_vwap=indicators.get("vwap", 0.0),
            hard_sl_premium=hard_sl,
            trail_sl_premium=hard_sl,
            peak_premium=ltp,
        )

        self._update_state(position=position, last_signal=setup.signal.value)
        self._position_extras = extras
        self._last_position_tick_at = time_module.monotonic()

        # Mark ORB as used if Model 2 fired
        if setup.model == N2Model.M2:
            self._daily.day_ctx.orb_used = True

        atm_strike = int(instrument["strike"])
        logger.info(
            "[N2 PAPER] ENTRY | %s | %s @ %.2f qty=%d | hard_sl=%.2f(−%.1f%%) trail@+%.0f%% | %s",
            instrument["tradingsymbol"], option_type, ltp, qty,
            hard_sl, sl_pct, self._cfg.get("trail_trigger_pct", 15.0), setup.reason,
        )

        self._log_attempt(
            when=now, model=setup.model.value, signal=setup.signal.value,
            outcome="FIRED",
            spot=state.nifty_spot, atm_strike=atm_strike,
            option_ltp=ltp,
            vwap=indicators.get("vwap") or 0.0,
            rsi14=indicators.get("rsi14") or 0.0,
            body_pct=candle_body_pct(last_candle) if last_candle else 0.0,
            or_high=self._daily.day_ctx.or_high or 0.0,
            or_low=self._daily.day_ctx.or_low or 0.0,
            skip_reasons=[], reason=setup.reason,
        )

    # ------------------------------------------------------------------
    # Exit evaluation per candle / per tick
    # ------------------------------------------------------------------

    def _check_exits_on_candle(self, state: TradingState, indicators: dict) -> None:
        if state.position is None or self._position_extras is None:
            return
        decision = evaluate_exit_n2(
            entry_price=state.position.entry_price,
            current_price=state.position.current_price or state.position.entry_price,
            extras=self._position_extras,
            cfg=self._cfg,
        )
        if not decision.should_exit and decision.new_sl_premium is not None:
            with self._get_lock():
                raw = self._get_raw_state()
                if raw.position is not None:
                    raw.position.trailing_sl_price = decision.new_sl_premium
        if decision.should_exit:
            self._execute_exit(layer=decision.layer, reason=decision.reason)
            return

        # Softened opposite-signal exit (candle-close only): fire only after the
        # price has closed on the wrong side of VWAP for N consecutive candles
        # AND a reverse breakout has formed (kills single-bar VWAP headfakes).
        self._maybe_opposite_exit(state, indicators)

    def _maybe_opposite_exit(self, state: TradingState, indicators: dict) -> None:
        extras = self._position_extras
        pos = state.position
        if extras is None or pos is None:
            return
        confirm = int(self._cfg.get("opposite_exit_confirm_closes", 2))
        if confirm <= 0:
            return
        vwap = indicators.get("vwap") or 0.0
        if vwap <= 0 or not state.candles:
            return
        cur = state.candles[-1]
        wrong_side = (cur.close < vwap) if pos.option_type == "CE" else (cur.close > vwap)
        extras.consec_wrong_side_vwap = (extras.consec_wrong_side_vwap + 1) if wrong_side else 0
        if extras.consec_wrong_side_vwap < confirm:
            return
        if detect_opposite_signal_v1(
            state.candles, pos.option_type, vwap,
            indicators.get("ema20_series", []),
            indicators.get("market_state", "UNKNOWN"),
        ):
            self._execute_exit(
                layer=N2ExitLayer.OPPOSITE_SIGNAL,
                reason=f"OPPOSITE_SIGNAL ({extras.consec_wrong_side_vwap} wrong-side VWAP closes)",
            )

    def _check_exits_on_tick(self) -> None:
        state = self._get_state()
        if state.position is None or self._position_extras is None:
            return
        decision = evaluate_exit_n2(
            entry_price=state.position.entry_price,
            current_price=state.position.current_price or state.position.entry_price,
            extras=self._position_extras,
            cfg=self._cfg,
            allow_sl_moves=False,   # ticks fire target/SL fast; breakeven/trail arm only on candle close
        )
        if not decision.should_exit and decision.new_sl_premium is not None:
            with self._get_lock():
                raw = self._get_raw_state()
                if raw.position is not None:
                    raw.position.trailing_sl_price = decision.new_sl_premium
        if decision.should_exit:
            self._execute_exit(layer=decision.layer, reason=decision.reason)

    # ------------------------------------------------------------------
    # Exit execution (paper)
    # ------------------------------------------------------------------

    def _execute_exit(self, layer: N2ExitLayer, reason: str) -> None:
        with self._get_lock():
            raw = self._get_raw_state()
            if raw.position is None:
                return
            position = copy.copy(raw.position)
            extras = copy.copy(self._position_extras) if self._position_extras else None
            raw.position = None

        exit_price = position.current_price if position.current_price > 0 else position.entry_price
        spot_exit = self._get_state().nifty_spot
        leg_pnl = (exit_price - position.entry_price) * position.qty
        pnl_pct = ((exit_price - position.entry_price) / position.entry_price * 100.0
                   if position.entry_price > 0 else 0.0)

        self._daily.realised_pnl += leg_pnl
        self._daily.last_exit_candle_idx = max(0, len(self._get_state().candles) - 1)

        if self._daily.trades_today == 1:
            self._daily.first_trade_was_sl = (layer == N2ExitLayer.STOPLOSS_HIT)

        self._update_state(
            exit_reason=reason,
            exit_price=exit_price,
            pnl=calc_pnl_n2(position.entry_price, exit_price, position.qty),
        )

        log_trade_n2(
            trade_number=self._daily.trades_today,
            model=extras.model if extras else "",
            option_symbol=position.option_symbol,
            option_type=position.option_type,
            strike=position.strike,
            expiry=position.expiry,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=datetime.now(IST),
            exit_price=exit_price,
            qty=position.qty,
            spot_entry=position.nifty_spot_entry,
            spot_exit=spot_exit,
            vwap_entry=position.vwap_entry,
            ema20_entry=position.ema20_entry or 0.0,
            rsi14_entry=position.rsi14_entry or 0.0,
            hard_sl_premium=extras.hard_sl_premium if extras else 0.0,
            sl_pct=self._cfg.get("sl_pct", 18.0),
            mfe_pct=extras.max_pnl_pct_seen if extras else 0.0,
            mae_pct=extras.min_pnl_pct_seen if extras else 0.0,
            reason_for_entry=position.reason_for_entry,
            exit_layer=layer.value,
            reason_for_exit=reason,
            breakeven_set=extras.breakeven_set if extras else False,
            trail_active=extras.trail_active if extras else False,
        )

        logger.info(
            "[N2 PAPER] EXIT | %s | %s qty=%d entry=%.2f exit=%.2f | P&L: ₹%.2f (%.1f%%) | layer=%s",
            position.option_symbol, position.option_type, position.qty,
            position.entry_price, exit_price, leg_pnl, pnl_pct, layer.value,
        )

        # Instrumentation: track the exited option's path for N more candles.
        if layer != N2ExitLayer.MANUAL_STOP:
            self._register_post_exit_tracker(position, exit_price, reason, self._daily.trades_today)

        self._position_extras = None
        self._last_position_tick_at = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log_attempt(self, **kw) -> None:
        try:
            log_attempt_n2(**kw)
        except Exception:
            logger.debug("N2 attempt log failed", exc_info=True)

    # ------------------------------------------------------------------
    # Instrumentation: post-exit + shadow (blocked-signal) path tracking
    # ------------------------------------------------------------------

    def _fetch_ltp_symbol(self, tradingsymbol: str) -> float:
        try:
            sym = f"NFO:{tradingsymbol}"
            data = self._kite.ltp([sym])
            return data.get(sym, {}).get("last_price", 0) or 0.0
        except Exception:
            return 0.0

    def _register_post_exit_tracker(self, position, exit_price: float,
                                    reason: str, trade_number: int) -> None:
        n = int(self._cfg.get("post_exit_track_candles", 8))
        if n <= 0 or exit_price <= 0:
            return
        t = OptionPathTracker(
            kind="POST_EXIT",
            tradingsymbol=position.option_symbol,
            option_type=position.option_type,
            strike=position.strike,
            ref_price=exit_price,
            start_time=datetime.now(IST),
            reason=reason,
            max_ltp=exit_price, min_ltp=exit_price, last_ltp=exit_price,
            candles_left=n,
            trade_number=trade_number,
            entry_price=position.entry_price,
        )
        with self._tracker_lock:
            self._path_trackers.append(t)

    def _register_shadow(self, base_signal: N2Signal, indicators: dict,
                         now: datetime, block_reason: str) -> None:
        cap = int(self._cfg.get("max_shadow_trackers", 6))
        with self._tracker_lock:
            if sum(1 for t in self._path_trackers if t.kind == "SHADOW") >= cap:
                return
        inst = self._ce_instrument if base_signal == N2Signal.BUY_CE else self._pe_instrument
        if inst is None:
            return
        state = self._get_state()
        ltp = state.ce_ltp if base_signal == N2Signal.BUY_CE else state.pe_ltp
        if ltp <= 0:
            ltp = self._fetch_ltp_symbol(inst["tradingsymbol"])
        if ltp <= 0:
            return
        t = OptionPathTracker(
            kind="SHADOW",
            tradingsymbol=inst["tradingsymbol"],
            option_type="CE" if base_signal == N2Signal.BUY_CE else "PE",
            strike=int(inst["strike"]),
            ref_price=ltp,
            start_time=now,
            reason=block_reason,
            max_ltp=ltp, min_ltp=ltp, last_ltp=ltp,
            until_force_exit=True,
            signal=base_signal.value,
            spot=state.nifty_spot,
            vwap=indicators.get("vwap") or 0.0,
            rsi14=indicators.get("rsi14") or 0.0,
        )
        with self._tracker_lock:
            self._path_trackers.append(t)
        logger.info("[N2 SHADOW] %s blocked (%s) — tracking would-be outcome from %.2f",
                    base_signal.value, block_reason, ltp)

    def _update_path_trackers(self, now: datetime) -> None:
        with self._tracker_lock:
            trackers = list(self._path_trackers)
        if not trackers:
            return
        syms = list({f"NFO:{t.tradingsymbol}" for t in trackers})
        quotes: dict = {}
        try:
            data = self._kite.ltp(syms)
            for s, v in data.items():
                quotes[s] = v.get("last_price", 0) or 0.0
        except Exception as e:
            logger.debug("N2 path-tracker LTP fetch failed: %s", e)
            return

        force_t = time(*self._cfg.get("force_exit_hhmm", (15, 20)))
        done_ids: set = set()
        for t in trackers:
            ltp = quotes.get(f"NFO:{t.tradingsymbol}", 0.0)
            if ltp and ltp > 0:
                t.max_ltp = max(t.max_ltp, ltp)
                t.min_ltp = min(t.min_ltp, ltp)
                t.last_ltp = ltp
            t.candles_done += 1
            if t.kind == "POST_EXIT":
                t.candles_left -= 1
                if t.candles_left <= 0:
                    self._finalize_post_exit(t)
                    done_ids.add(id(t))
            else:  # SHADOW
                if now.time() >= force_t:
                    self._finalize_shadow(t, now)
                    done_ids.add(id(t))
        if done_ids:
            with self._tracker_lock:
                self._path_trackers = [t for t in self._path_trackers if id(t) not in done_ids]

    def _finalize_post_exit(self, t: OptionPathTracker) -> None:
        try:
            log_post_exit_n2(
                trade_number=t.trade_number,
                option_symbol=t.tradingsymbol,
                option_type=t.option_type,
                strike=t.strike,
                exit_time=t.start_time,
                exit_reason=t.reason,
                entry_price=t.entry_price,
                exit_price=t.ref_price,
                candles_tracked=t.candles_done,
                post_max_ltp=t.max_ltp,
                post_min_ltp=t.min_ltp,
            )
        except Exception:
            logger.debug("N2 post-exit finalize failed", exc_info=True)

    def _finalize_shadow(self, t: OptionPathTracker, now: datetime) -> None:
        try:
            log_shadow_signal_n2(
                signal_time=t.start_time,
                signal=t.signal,
                block_reason=t.reason,
                option_symbol=t.tradingsymbol,
                option_type=t.option_type,
                strike=t.strike,
                spot=t.spot, vwap=t.vwap, rsi14=t.rsi14,
                wouldbe_entry_price=t.ref_price,
                force_exit_time=now,
                force_exit_ltp=t.last_ltp,
                path_max_ltp=t.max_ltp,
                path_min_ltp=t.min_ltp,
            )
        except Exception:
            logger.debug("N2 shadow finalize failed", exc_info=True)

    def _flush_path_trackers(self) -> None:
        """Finalize any pending trackers (called on stop) so no data is lost."""
        with self._tracker_lock:
            trackers = list(self._path_trackers)
            self._path_trackers = []
        now = datetime.now(IST)
        for t in trackers:
            if t.kind == "POST_EXIT":
                self._finalize_post_exit(t)
            else:
                self._finalize_shadow(t, now)

    # ------------------------------------------------------------------
    # Monitoring loop — tick-frequency exit checks + REST LTP fallback
    # ------------------------------------------------------------------

    def _monitoring_loop(self) -> None:
        logger.info("N2 monitoring loop started")
        _ltp_tick = 0
        while True:
            state = self._get_state()
            if not state.engine_running:
                break

            if state.position is not None:
                self._check_exits_on_tick()
                stale = (
                    self._last_position_tick_at is None
                    or time_module.monotonic() - self._last_position_tick_at > self._POSITION_TICK_STALL_S
                )
                if stale and self._ce_instrument and self._pe_instrument:
                    self._fetch_option_ltp_rest()
                    self._last_position_tick_at = time_module.monotonic()

            _ltp_tick += 1
            if _ltp_tick >= 30:
                _ltp_tick = 0
                if (state.ce_ltp == 0 or state.pe_ltp == 0) and self._ce_instrument and self._pe_instrument:
                    self._fetch_option_ltp_rest()

            time_module.sleep(1)
        logger.info("N2 monitoring loop terminated")

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
            logger.debug("N2 REST LTP fallback failed: %s", e)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_nifty2_engine = NiftyEngineV2()


def get_nifty2_engine() -> NiftyEngineV2:
    return _nifty2_engine
