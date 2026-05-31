"""NIFTY Futures — Opening-Range Breakout (ORB) entry logic.

Trades the future DIRECTLY (long/short). Pure function mirroring the
backtested+hardened orb_lab config exactly (see backtest/orb_lab.py):

  OR = high/low of the first `or_bars` candles of the day (09:15–09:25 for 3).
  Within the entry window, LONG when a closed bar closes above OR-high×(1+buffer%)
  with close>open; SHORT mirrored below OR-low. Requires a directional body
  (≥ body_frac of range), a volume surge (≥ vol_mult × rolling baseline), and a
  non-exhausted RSI (LONG: RSI≤rsi_cap, SHORT: RSI≥100−rsi_cap). One breakout
  direction per day (the engine enforces this via day-context `orb_used`).

No look-ahead: only the just-closed candle and prior bars are read.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional

from services.trading_state import Candle


class FutSignal(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_SIGNAL = "NO_SIGNAL"


@dataclass
class FutSetup:
    signal: FutSignal = FutSignal.NO_SIGNAL
    reason: str = ""
    skip_reason: str = ""
    or_high: Optional[float] = None
    or_low: Optional[float] = None


def _t(hhmm) -> time:
    return time(hhmm[0], hhmm[1])


def _session_rsi(today_candles: list[Candle], period: int = 14) -> Optional[float]:
    """Wilder RSI on the current session's closes only (resets each day) — matches
    the backtested config (backtest/indicators.py rsi). None until period+1 closes."""
    closes = [c.close for c in today_candles]
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for a, b in zip(closes, closes[1:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for k in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[k]) / period
        avg_loss = (avg_loss * (period - 1) + losses[k]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def evaluate_orb(candles: list[Candle], rsi14: Optional[float], cfg: dict,
                 now: datetime) -> FutSetup:
    """Evaluate the ORB breakout on the just-closed candle (candles[-1])."""
    if not candles:
        return FutSetup(skip_reason="no_candles")

    today = candles[-1].timestamp.date()
    today_candles = [c for c in candles if c.timestamp.date() == today]

    or_bars = int(cfg["or_bars"])
    # Need the full OR window plus at least one bar after it to break out.
    if len(today_candles) <= or_bars:
        return FutSetup(skip_reason="building_opening_range")

    or_high = max(c.high for c in today_candles[:or_bars])
    or_low = min(c.low for c in today_candles[:or_bars])

    # Per-session RSI (matches the backtested config exactly): reset each day,
    # and None (no RSI filter) until 15 session closes — NOT the continuous
    # multi-day RSI, which would activate the exhaustion filter too early.
    rsi14 = _session_rsi(today_candles)

    cur = today_candles[-1]
    t = cur.timestamp.time()
    if t < _t(cfg["entry_window_start"]) or t > _t(cfg["entry_window_end"]):
        return FutSetup(skip_reason="outside_entry_window", or_high=or_high, or_low=or_low)

    rng = cur.high - cur.low
    if rng <= 0:
        return FutSetup(skip_reason="zero_range_candle", or_high=or_high, or_low=or_low)

    body_frac = float(cfg.get("body_frac", 0.0))
    if body_frac > 0 and abs(cur.close - cur.open) < body_frac * rng:
        return FutSetup(skip_reason="weak_body", or_high=or_high, or_low=or_low)

    vol_mult = float(cfg.get("vol_mult", 0.0))
    if vol_mult and vol_mult > 1.0:
        i = len(today_candles) - 1
        lb0 = max(or_bars, i - int(cfg.get("vol_lookback", 5)))
        if i > lb0:
            prior = today_candles[lb0:i]
            avg = sum(c.volume for c in prior) / len(prior) if prior else 0.0
            if avg > 0 and cur.volume < vol_mult * avg:
                return FutSetup(skip_reason="no_volume_surge", or_high=or_high, or_low=or_low)

    buffer_pct = float(cfg["buffer_pct"])
    long_trig = or_high * (1.0 + buffer_pct / 100.0)
    short_trig = or_low * (1.0 - buffer_pct / 100.0)

    cap = cfg.get("rsi_cap")
    r_ok_long = (cap is None) or (rsi14 is None) or (rsi14 <= cap)
    r_ok_short = (cap is None) or (rsi14 is None) or (rsi14 >= (100 - cap))

    if cur.close > long_trig and cur.close > cur.open and r_ok_long:
        return FutSetup(
            signal=FutSignal.LONG, or_high=or_high, or_low=or_low,
            reason=(f"close={cur.close:.1f} > OR-high×buf={long_trig:.1f} "
                    f"(OR {or_low:.0f}-{or_high:.0f}) | body+vol ok | RSI={rsi14}"))
    if cur.close < short_trig and cur.close < cur.open and r_ok_short:
        return FutSetup(
            signal=FutSignal.SHORT, or_high=or_high, or_low=or_low,
            reason=(f"close={cur.close:.1f} < OR-low×buf={short_trig:.1f} "
                    f"(OR {or_low:.0f}-{or_high:.0f}) | body+vol ok | RSI={rsi14}"))

    return FutSetup(skip_reason="no_breakout", or_high=or_high, or_low=or_low)
