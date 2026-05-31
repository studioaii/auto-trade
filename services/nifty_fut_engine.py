"""NIFTY Futures — standalone ORB engine. Trades the future DIRECTLY (long/short).

Mirrors the NIFTY 2.0 engine's public interface (start/stop/get_status,
_instrument_name, _market_data, _state_mgr) so the daily scheduler, router and
shutdown hook treat it like any other engine. Differences:
  • Position is the FUTURE itself (LONG/SHORT), not a CE/PE option.
  • Candles are built from the futures contract (real volume → ORB vol filter).
  • Live price comes from state.nifty_futures_ltp (written by market_data on
    every futures tick); no option tokens are subscribed.
  • Risk is FIXED risk-reward in index POINTS (no premium %, no trailing).
Always PAPER.
"""
from __future__ import annotations

import logging
import threading
import time as time_module
from dataclasses import dataclass
from datetime import datetime, time, timedelta, date as date_type
from typing import Optional
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect

from config import API_KEY, TRADING_MODE, INSTRUMENT_CONFIG
from services.trading_state import Candle, InstrumentStateManager, TradingState
from services.instruments import find_futures
from services.indicators import get_latest_indicators, MIN_CANDLES
from services.nifty_fut_strategy import FutSignal, FutSetup, evaluate_orb
from services.nifty_fut_risk import (
    FutExitLayer, initial_levels, evaluate_exit, calc_pnl,
)
from services.nifty_fut_paper_trade import log_trade_fut
from services.market_data import InstrumentSubscription, get_market_data_service

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

INSTRUMENT_NAME = "NIFTY_FUT"
_NFO_NAME = "NIFTY"


@dataclass
class FutPosition:
    direction: str            # LONG / SHORT
    entry_price: float
    qty: int
    entry_time: datetime
    sl_price: float
    target_price: float
    current_price: float
    reason_for_entry: str
    spot_entry: float = 0.0
    rsi14_entry: float = 0.0
    or_high: float = 0.0
    or_low: float = 0.0
    order_id: str = ""


@dataclass
class FutDailyState:
    realised_pnl: float = 0.0
    trades_today: int = 0
    orb_used: bool = False         # one breakout direction per day


class NiftyFutEngine:
    """NIFTY futures ORB engine. PAPER-only by design."""

    _POSITION_TICK_STALL_S = 30

    def __init__(self):
        self._instrument_name = INSTRUMENT_NAME
        self._cfg = INSTRUMENT_CONFIG[INSTRUMENT_NAME]
        self._state_mgr = InstrumentStateManager(INSTRUMENT_NAME)
        self._market_data = get_market_data_service()

        self._index_token: int = self._cfg["index_token"]
        self._futures_token: int = 0
        self._futures_symbol: str = ""
        self._lot_size: int = int(self._cfg.get("lot_size", 65))

        self._kite: Optional[KiteConnect] = None
        self._paper_counter = 0
        self._monitor_thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._last_candle_date: Optional[date_type] = None
        self._cached_indicators: dict = {}
        self._last_position_tick_at: Optional[float] = None

        self._daily = FutDailyState()
        self._position: Optional[FutPosition] = None

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

        self._state_mgr.reset_daily_state(mode=mode)
        self._daily = FutDailyState()
        self._position = None

        logger.info("Starting NIFTY FUTURES (ORB) engine in %s mode", mode)

        # Front-month futures contract — this is the instrument we trade & chart.
        fut = find_futures(kite, _NFO_NAME)
        self._futures_token = fut["instrument_token"]
        self._futures_symbol = fut["tradingsymbol"]
        self._lot_size = int(fut.get("lot_size") or self._cfg.get("lot_size", 65))
        logger.info("NIFTY_FUT contract: %s token=%s lot=%d",
                    self._futures_symbol, self._futures_token, self._lot_size)

        # Official spot level (informational)
        try:
            ltp_data = kite.ltp(self._cfg["ltp_symbol"])
            self._update_state(nifty_spot=ltp_data[self._cfg["ltp_symbol"]]["last_price"])
        except Exception:
            pass

        self._load_session_candles()

        subscription = InstrumentSubscription(
            instrument_name=INSTRUMENT_NAME,
            index_token=self._index_token,
            futures_token=self._futures_token,
            option_tokens=[],                       # trade the future directly
            candle_callback=self._on_candle_ready,
            spot_callback=self._on_spot_update,
            option_ltp_callback=lambda *_: None,
            get_lock_fn=self._state_mgr.get_lock,
            get_raw_state_fn=self._state_mgr.get_raw_state,
            get_state_fn=self._state_mgr.get_state,
            update_state_fn=self._state_mgr.update_state,
        )
        self._market_data.start(api_key=API_KEY, access_token=kite.access_token,
                                subscription=subscription)

        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            name=f"TradingMonitor-{INSTRUMENT_NAME}", daemon=True)
        self._monitor_thread.start()

        return {
            "instrument": INSTRUMENT_NAME,
            "mode": mode,
            "futures_symbol": self._futures_symbol,
            "lot_size": self._lot_size,
        }

    def stop(self, kite: Optional[KiteConnect] = None) -> None:
        try:
            if self._position is not None:
                logger.info("NIFTY_FUT stopping — closing open position first")
                self._execute_exit(layer=FutExitLayer.MANUAL_STOP, reason="MANUAL_STOP")
            self._market_data.unregister_instrument(INSTRUMENT_NAME)
        except Exception as e:
            logger.error("NIFTY_FUT stop error: %s", e)
            self._update_state(error_message=f"Stop error: {e}")
        finally:
            self._update_state(engine_running=False)
            logger.info("NIFTY_FUT engine stopped")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        state = self._get_state()
        pos = self._position

        pnl_info = None
        if pos and pos.current_price > 0:
            pnl_info = calc_pnl(pos.direction, pos.entry_price, pos.current_price, pos.qty)
        elif state.pnl:
            pnl_info = state.pnl

        position_info = None
        if pos:
            position_info = {
                "futures_symbol": self._futures_symbol,
                "direction": pos.direction,
                "entry_price": round(pos.entry_price, 2),
                "current_price": round(pos.current_price, 2),
                "qty": pos.qty,
                "sl_price": round(pos.sl_price, 2),
                "target_price": round(pos.target_price, 2),
                "entry_time": pos.entry_time.strftime("%H:%M:%S") if pos.entry_time else None,
                "reason_entry": pos.reason_for_entry,
            }

        ind_snap = {}
        ind = self._cached_indicators if self._cached_indicators.get("enough_data") else {}
        if ind.get("enough_data"):
            ind_snap = {
                "vwap": ind.get("vwap"),
                "ema20": round(ind["ema20"], 2) if ind.get("ema20") else None,
                "rsi14": round(ind["rsi14"], 2) if ind.get("rsi14") else None,
            }

        return {
            "instrument": INSTRUMENT_NAME,
            "strategy": "NIFTY_FUTURES_ORB_V1",
            "mode": state.trading_mode,
            "engine_running": state.engine_running,
            "trades_today": self._daily.trades_today,
            "max_trades": self._cfg.get("max_trades_per_day", 1),
            "realised_pnl": round(self._daily.realised_pnl, 2),
            "nifty_spot": round(state.nifty_spot, 2),
            "futures_ltp": round(state.nifty_futures_ltp, 2),
            "last_signal": state.last_signal,
            "last_candle_time": state.last_candle_time.strftime("%H:%M") if state.last_candle_time else None,
            "candle_count": len(state.candles),
            "candles_needed": MIN_CANDLES,
            "position": position_info,
            "pnl": pnl_info,
            "exit_reason": state.exit_reason,
            "exit_price": state.exit_price,
            "error": state.error_message,
            "indicators": ind_snap,
            "instruments": {
                "futures_symbol": self._futures_symbol,
                "lot_size": self._lot_size,
                "candle_source": self._futures_symbol or "INDEX",
            },
            "orb_used": self._daily.orb_used,
        }

    # ------------------------------------------------------------------
    # Historical session-candle preload (from the futures contract)
    # ------------------------------------------------------------------

    def _load_session_candles(self) -> None:
        try:
            now = datetime.now(IST)
            pre_market = now.hour < 9 or (now.hour == 9 and now.minute < 15)
            token = self._futures_token or self._index_token
            today = now.date()

            today_candles: list[Candle] = []
            if not pre_market:
                session_start = datetime(today.year, today.month, today.day, 9, 15, 0, tzinfo=IST)
                slot_start = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
                rows = self._kite.historical_data(token, session_start, now, "5minute")
                for row in rows:
                    ts = row["date"]
                    if hasattr(ts, "astimezone"):
                        ts = ts.astimezone(IST)
                    if ts >= slot_start:
                        continue
                    today_candles.append(Candle(
                        timestamp=ts, open=row["open"], high=row["high"],
                        low=row["low"], close=row["close"], volume=row.get("volume", 0)))

            all_candles = today_candles
            if len(today_candles) < MIN_CANDLES:
                seed_count = MIN_CANDLES - len(today_candles) + 5
                d = today - timedelta(days=1)
                seed: list[Candle] = []
                for _ in range(10):
                    while d.weekday() >= 5:
                        d -= timedelta(days=1)
                    ps = datetime(d.year, d.month, d.day, 9, 15, 0, tzinfo=IST)
                    pe = datetime(d.year, d.month, d.day, 15, 30, 0, tzinfo=IST)
                    try:
                        rows = self._kite.historical_data(token, ps, pe, "5minute")
                        prev = []
                        for row in rows:
                            ts = row["date"]
                            if hasattr(ts, "astimezone"):
                                ts = ts.astimezone(IST)
                            prev.append(Candle(
                                timestamp=ts, open=row["open"], high=row["high"],
                                low=row["low"], close=row["close"], volume=row.get("volume", 0)))
                        if prev:
                            seed = prev[-(seed_count - len(seed)):] + seed
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
                    raw.candles = sorted(
                        raw.candles + [c for c in all_candles if c.timestamp not in existing],
                        key=lambda c: c.timestamp)
                else:
                    raw.candles = all_candles
                raw.last_candle_time = raw.candles[-1].timestamp
        except Exception as e:
            logger.warning("NIFTY_FUT session preload failed: %s", e)

    # ------------------------------------------------------------------
    # Tick callbacks
    # ------------------------------------------------------------------

    def _on_spot_update(self, spot: float) -> None:
        self._update_state(nifty_spot=spot)

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

        state = self._get_state()
        indicators = get_latest_indicators(state.candles)
        if indicators.get("enough_data"):
            self._update_state(market_state=indicators.get("market_state", "UNKNOWN"))
            self._cached_indicators = indicators

        # Per-day reset
        candle_date = candle.timestamp.date()
        if candle_date != self._last_candle_date:
            self._last_candle_date = candle_date
            self._daily = FutDailyState()

        now = datetime.now(IST)

        # Manage open position on candle close (tick loop also checks every second)
        if self._position is not None:
            self._check_exits(now)
            return

        # Entry gate
        if self._daily.orb_used:
            return
        if self._daily.trades_today >= self._cfg.get("max_trades_per_day", 1):
            return

        rsi14 = indicators.get("rsi14") if indicators.get("enough_data") else None
        setup = evaluate_orb(state.candles, rsi14, self._cfg, now)
        if setup.signal != FutSignal.NO_SIGNAL:
            self._update_state(last_signal=setup.signal.value)
            self._execute_entry(setup, indicators, now)

    # ------------------------------------------------------------------
    # Entry execution (paper)
    # ------------------------------------------------------------------

    def _execute_entry(self, setup: FutSetup, indicators: dict, now: datetime) -> None:
        state = self._get_state()
        price = state.nifty_futures_ltp
        if price <= 0:
            price = state.candles[-1].close if state.candles else 0.0
        if price <= 0:
            logger.warning("NIFTY_FUT entry skipped — no futures price")
            return

        side = setup.signal.value  # LONG / SHORT
        sl_price, target_price = initial_levels(price, side, self._cfg)

        # Atomic claim — one position at a time
        with self._get_lock():
            if self._position is not None:
                return
            self._daily.trades_today += 1
            self._daily.orb_used = True

        self._paper_counter += 1
        pos = FutPosition(
            direction=side, entry_price=price, qty=self._lot_size, entry_time=now,
            sl_price=sl_price, target_price=target_price, current_price=price,
            reason_for_entry=setup.reason, spot_entry=state.nifty_spot,
            rsi14_entry=indicators.get("rsi14") or 0.0,
            or_high=setup.or_high or 0.0, or_low=setup.or_low or 0.0,
            order_id=f"FUT-PAPER-{self._paper_counter:03d}")
        self._position = pos
        self._last_position_tick_at = time_module.monotonic()

        logger.info(
            "[NIFTY_FUT PAPER] ENTRY | %s %s @ %.2f qty=%d | SL=%.1f target=%.1f | %s",
            self._futures_symbol, side, price, pos.qty, sl_price, target_price, setup.reason)

    # ------------------------------------------------------------------
    # Exit evaluation
    # ------------------------------------------------------------------

    def _check_exits(self, now: datetime) -> None:
        pos = self._position
        if pos is None:
            return
        decision = evaluate_exit(
            pos.direction, pos.entry_price, pos.current_price,
            pos.sl_price, pos.target_price, self._cfg, now)
        if decision.should_exit:
            self._execute_exit(layer=decision.layer, reason=decision.reason)

    def _execute_exit(self, layer: FutExitLayer, reason: str) -> None:
        with self._get_lock():
            pos = self._position
            if pos is None:
                return
            self._position = None

        exit_price = pos.current_price if pos.current_price > 0 else pos.entry_price
        pnl = calc_pnl(pos.direction, pos.entry_price, exit_price, pos.qty)
        self._daily.realised_pnl += pnl["rupees"]
        self._update_state(exit_reason=reason, exit_price=exit_price, pnl=pnl)

        log_trade_fut(
            trade_number=self._daily.trades_today, futures_symbol=self._futures_symbol,
            direction=pos.direction, entry_time=pos.entry_time, entry_price=pos.entry_price,
            exit_time=datetime.now(IST), exit_price=exit_price, qty=pos.qty,
            pnl_points=pnl["points"], pnl_rupees=pnl["rupees"], pnl_pct=pnl["pct"],
            sl_price=pos.sl_price, target_price=pos.target_price, spot_entry=pos.spot_entry,
            rsi14_entry=pos.rsi14_entry, or_high=pos.or_high, or_low=pos.or_low,
            reason_for_entry=pos.reason_for_entry, exit_layer=layer.value,
            reason_for_exit=reason)

        logger.info(
            "[NIFTY_FUT PAPER] EXIT | %s %s entry=%.2f exit=%.2f | P&L: %.1f pts ₹%.0f | %s",
            self._futures_symbol, pos.direction, pos.entry_price, exit_price,
            pnl["points"], pnl["rupees"], layer.value)
        self._last_position_tick_at = None

    # ------------------------------------------------------------------
    # Monitoring loop — per-second exit checks + REST price fallback
    # ------------------------------------------------------------------

    def _monitoring_loop(self) -> None:
        logger.info("NIFTY_FUT monitoring loop started")
        while True:
            state = self._get_state()
            if not state.engine_running:
                break
            if self._position is not None:
                # Sync live price from the futures tick feed
                if state.nifty_futures_ltp > 0:
                    self._position.current_price = state.nifty_futures_ltp
                    self._last_position_tick_at = time_module.monotonic()
                else:
                    self._fetch_futures_ltp_rest()
                self._check_exits(datetime.now(IST))
            time_module.sleep(1)
        logger.info("NIFTY_FUT monitoring loop terminated")

    def _fetch_futures_ltp_rest(self) -> None:
        try:
            sym = f"NFO:{self._futures_symbol}"
            data = self._kite.ltp([sym])
            ltp = data.get(sym, {}).get("last_price", 0)
            if ltp > 0:
                self._update_state(nifty_futures_ltp=ltp)
                if self._position is not None:
                    self._position.current_price = ltp
        except Exception as e:
            logger.debug("NIFTY_FUT REST LTP fallback failed: %s", e)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_nifty_fut_engine = NiftyFutEngine()


def get_nifty_fut_engine() -> NiftyFutEngine:
    return _nifty_fut_engine
