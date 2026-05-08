"""
SimEngine — minimal offline mirror of TradingEngine for backtesting.

Reuses production code paths for indicators (`get_latest_indicators`),
signal generation (`generate_signal`), and exit detection
(`detect_opposite_signal` + the trailing-stop logic from `risk_manager`).

Time-based gating (09:50 entry / 14:00 last entry / 15:20 force exit) is
done inline using each candle's own timestamp, since the production
helpers consult wall-clock time via `_now_ist()`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from datetime import time as time_type

from config import INSTRUMENT_CONFIG
from services.indicators import get_latest_indicators
from services.day_bias import classify_day_bias
from services.strategy import (
    Signal,
    detect_opposite_signal,
    generate_signal,
    FORCE_EXIT_TIME,
    LAST_ENTRY_TIME,
    MARKET_OPEN_READY,
)
from services.risk_manager import (
    INITIAL_SL_PCT,
    TRAIL_GAP_BASE,
    TRAIL_GAP_MIN,
    TRAIL_GAP_STEP,
    TRAIL_TRIGGER,
    MAX_TRADES_PER_DAY,
)
from services.trading_state import Candle, PositionInfo, TradingState

logger = logging.getLogger("backtest.sim")


@dataclass
class SimResult:
    date: date
    instrument: str
    trades: list[dict] = field(default_factory=list)
    signal_diffs: list[int] = field(default_factory=list)  # 0 match / 1 mismatch
    skipped: bool = False
    opening_rsi: Optional[float] = None


class SimEngine:
    """Replays one trading day through production strategy logic."""

    def __init__(self, instrument: str, cfg_override: Optional[dict] = None):
        self._instrument = instrument
        self._cfg = dict(INSTRUMENT_CONFIG[instrument])
        if cfg_override:
            self._cfg.update(cfg_override)

        self._state = TradingState()
        self._state.engine_running = True
        self._state.trading_mode = "PAPER"

        self._opening_rsi: Optional[float] = None
        self._completed_trades: list[dict] = []
        self._signal_diffs: list[int] = []
        # v3: previous-day last close for day_bias classification
        self._prev_close: float = 0.0

    # ──────────────────────────────────────────────────────────────────
    # Seeding (previous day's candles for indicator warm-up)
    # ──────────────────────────────────────────────────────────────────
    def seed(self, seed_candles: list[Candle]) -> None:
        """
        Pre-load yesterday's candles for indicator warm-up.
        VWAP is auto-filtered to today's date by `get_latest_indicators`.
        """
        self._state.candles.extend(seed_candles)
        if seed_candles:
            self._prev_close = seed_candles[-1].close

    # ──────────────────────────────────────────────────────────────────
    # Main per-candle loop
    # ──────────────────────────────────────────────────────────────────
    def on_candle(self, candle: Candle, row: dict) -> None:
        self._state.candles.append(candle)
        self._state.last_candle_time = candle.timestamp

        ce_ltp = _safe_float(row.get("ce_ltp"))
        pe_ltp = _safe_float(row.get("pe_ltp"))
        spot = _safe_float(row.get("nifty_spot"))
        atm = int(_safe_float(row.get("atm_strike")))
        logged_signal = (row.get("signal") or "NO_SIGNAL").strip() or "NO_SIGNAL"

        self._state.ce_ltp = ce_ltp
        self._state.pe_ltp = pe_ltp
        self._state.nifty_spot = spot

        indicators = get_latest_indicators(self._state.candles)

        if (
            self._opening_rsi is None
            and indicators.get("rsi14") is not None
            and candle.timestamp.date() == _today_date(self._state.candles)
        ):
            self._opening_rsi = indicators["rsi14"]

        if indicators.get("enough_data"):
            self._state.market_state = indicators["market_state"]

        # v3: classify day bias once at the first candle ≥09:50
        if (
            candle.timestamp.time() >= time_type(9, 50)
            and self._state.day_bias == "PENDING"
            and self._prev_close > 0
        ):
            today_candles = [
                c for c in self._state.candles
                if c.timestamp.date() == candle.timestamp.date()
            ]
            if len(today_candles) >= 4:
                self._state.day_bias = classify_day_bias(
                    today_candles, self._prev_close, self._cfg,
                )

        # v3: track session high/low (used by trend-pullback gate)
        if spot > 0:
            if self._state.session_high == 0 or spot > self._state.session_high:
                self._state.session_high = spot
            if self._state.session_low == 0 or spot < self._state.session_low:
                self._state.session_low = spot

        # ── 1. Position management: update price + check exits ────────
        if self._state.position is not None:
            current = ce_ltp if self._state.position.option_type == "CE" else pe_ltp
            if current > 0:
                self._state.position.current_price = current
                if current > self._state.position.highest_price_seen:
                    self._state.position.highest_price_seen = current

            t = candle.timestamp.time()
            if t >= FORCE_EXIT_TIME:
                self._close_position(candle, current, "TIME_EXIT")
                self._signal_diffs.append(0 if logged_signal == "NO_SIGNAL" else 1)
                return

            self._update_trail()
            if current > 0 and current <= self._state.position.trailing_sl_price:
                reason = (
                    "TRAILING_STOP" if self._state.position.trail_active
                    else "STOPLOSS_HIT"
                )
                self._close_position(candle, current, reason)
                # fall through to entry path? no — trades_today incremented blocks reentry

            elif indicators.get("enough_data"):
                opposite = detect_opposite_signal(
                    self._state.candles,
                    self._state.position.option_type if self._state.position else "CE",
                    indicators["vwap"],
                    indicators["ema20_series"],
                    indicators.get("market_state", "UNKNOWN"),
                )
                if opposite and self._state.position is not None:
                    self._close_position(candle, current, "OPPOSITE_SIGNAL")

        # ── 2. Entry path ──────────────────────────────────────────────
        signal_val = "NO_SIGNAL"
        t = candle.timestamp.time()

        eligible = (
            self._state.position is None
            and self._state.trades_today < MAX_TRADES_PER_DAY
            and not (self._state.trades_today >= 1 and self._state.first_trade_was_sl)
            and MARKET_OPEN_READY <= t < LAST_ENTRY_TIME
            and indicators.get("enough_data", False)
            and indicators.get("market_state") != "SIDEWAYS"
        )

        if eligible:
            signal, reason = generate_signal(
                state=self._state,
                indicators=indicators,
                cfg=self._cfg,
                opening_rsi=self._opening_rsi,
            )
            signal_val = signal.value
            if signal == Signal.BUY_CE and ce_ltp > 0:
                self._open_position(candle, "CE", ce_ltp, reason, indicators, atm)
            elif signal == Signal.BUY_PE and pe_ltp > 0:
                self._open_position(candle, "PE", pe_ltp, reason, indicators, atm)

        self._signal_diffs.append(0 if signal_val == logged_signal else 1)

    # ──────────────────────────────────────────────────────────────────
    # Position management
    # ──────────────────────────────────────────────────────────────────
    def _open_position(
        self, candle: Candle, opt_type: str, entry_price: float,
        reason: str, indicators: dict, atm: int,
    ) -> None:
        sl_price = round(entry_price * (1 - INITIAL_SL_PCT / 100), 2)
        self._state.position = PositionInfo(
            option_symbol=f"SIM-{opt_type}-{atm}",
            instrument_token=0,
            option_type=opt_type,
            strike=atm,
            expiry=candle.timestamp.date(),
            entry_price=entry_price,
            qty=self._cfg["lot_size"],
            order_id=f"SIM-{len(self._completed_trades)+1}",
            entry_time=candle.timestamp,
            reason_for_entry=reason,
            current_price=entry_price,
            trailing_sl_price=sl_price,
            highest_price_seen=entry_price,
            nifty_spot_entry=self._state.nifty_spot,
            vwap_entry=indicators.get("vwap") or 0.0,
            ema20_entry=indicators.get("ema20"),
            rsi14_entry=indicators.get("rsi14"),
            market_state_entry=indicators.get("market_state", "UNKNOWN"),
            efficiency_entry=indicators.get("efficiency_ratio", 0.0),
        )

    def _update_trail(self) -> None:
        pos = self._state.position
        if pos is None or pos.entry_price <= 0:
            return
        pnl_pct = (pos.current_price - pos.entry_price) / pos.entry_price * 100
        if pnl_pct < TRAIL_TRIGGER:
            return
        pos.trail_active = True
        extra_steps = int((pnl_pct - TRAIL_TRIGGER) / 10)
        gap = max(TRAIL_GAP_BASE - extra_steps * TRAIL_GAP_STEP, TRAIL_GAP_MIN)
        new_sl = pos.highest_price_seen * (1 - gap / 100)
        if new_sl > pos.trailing_sl_price:
            pos.trailing_sl_price = new_sl

    def _close_position(self, candle: Candle, exit_price: float, reason: str) -> None:
        pos = self._state.position
        if pos is None:
            return
        if exit_price <= 0:
            exit_price = pos.entry_price
        pnl_points = exit_price - pos.entry_price
        pnl_rupees = pnl_points * pos.qty
        pnl_pct = (pnl_points / pos.entry_price * 100) if pos.entry_price > 0 else 0.0
        result = "WIN" if pnl_rupees > 0 else "LOSS"

        self._completed_trades.append({
            "date": candle.timestamp.date().isoformat(),
            "instrument": self._instrument,
            "trade_number": len(self._completed_trades) + 1,
            "option_symbol": pos.option_symbol,
            "option_type": pos.option_type,
            "strike": pos.strike,
            "entry_time": pos.entry_time.strftime("%H:%M"),
            "entry_price": round(pos.entry_price, 2),
            "exit_time": candle.timestamp.strftime("%H:%M"),
            "exit_price": round(exit_price, 2),
            "qty": pos.qty,
            "pnl_points": round(pnl_points, 2),
            "pnl_rupees": round(pnl_rupees, 2),
            "pnl_pct": round(pnl_pct, 2),
            "result": result,
            "reason_for_entry": pos.reason_for_entry,
            "reason_for_exit": reason,
            "nifty_spot_entry": round(pos.nifty_spot_entry, 2),
            "vwap_entry": round(pos.vwap_entry, 2),
            "ema20_entry": round(pos.ema20_entry or 0, 2),
            "rsi14_entry": round(pos.rsi14_entry or 0, 2),
            "market_state_entry": pos.market_state_entry,
            "efficiency_entry": round(pos.efficiency_entry, 4),
            "trail_active": pos.trail_active,
        })

        if reason == "STOPLOSS_HIT":
            self._state.first_trade_was_sl = True

        self._state.position = None
        self._state.trades_today += 1

    def close_at_eod(self) -> None:
        """Force-close any still-open position using last candle's price."""
        if self._state.position is None or not self._state.candles:
            return
        last = self._state.candles[-1]
        pos = self._state.position
        exit_price = pos.current_price if pos.current_price > 0 else pos.entry_price
        self._close_position(last, exit_price, "EOD_FORCE_CLOSE")

    def result(self, day: date) -> SimResult:
        return SimResult(
            date=day,
            instrument=self._instrument,
            trades=self._completed_trades,
            signal_diffs=self._signal_diffs,
            skipped=False,
            opening_rsi=self._opening_rsi,
        )


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
def _safe_float(v) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _today_date(candles: list[Candle]):
    """Return the most recent candle's date, used for opening-RSI gating."""
    if not candles:
        return None
    return candles[-1].timestamp.date()
