"""
Technical indicator calculations.
Pure functions — no I/O, no state mutations.
All match TradingView standard implementations.
"""
from typing import Optional
from services.trading_state import Candle

# Minimum candles needed for EMA(20) to be valid
MIN_CANDLES = 22   # 20 for EMA + 2 extra to detect slope direction

# Sideways detection parameters
SIDEWAYS_LOOKBACK       = 10    # candles to inspect (50 min window)
SIDEWAYS_EFFICIENCY_MIN = 0.40  # raised from 0.35 → 0.40: skip choppy markets but allow moderate trends

# Volume confirmation
VOLUME_SURGE_RATIO = 1.2   # current candle volume must be >= 1.2× avg(last 10)

# VWAP distance filter — avoid entries too close to VWAP (reversal zone)
MIN_VWAP_DISTANCE_PCT = 0.15  # require at least 0.15% away from VWAP


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------
def compute_ema(values: list[float], period: int) -> list[Optional[float]]:
    """
    Standard EMA with smoothing = 2 / (period + 1).
    Seed value = SMA of first `period` values.
    Returns list of same length; first (period-1) entries are None.
    """
    result: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return result

    k = 2.0 / (period + 1)
    sma = sum(values[:period]) / period
    result[period - 1] = sma
    prev = sma

    for i in range(period, len(values)):
        ema = values[i] * k + prev * (1 - k)
        result[i] = ema
        prev = ema

    return result


# ---------------------------------------------------------------------------
# RSI (optional filter)
# ---------------------------------------------------------------------------
def compute_rsi(closes: list[float], period: int = 14) -> list[Optional[float]]:
    """Wilder's RSI — matches TradingView."""
    result: list[Optional[float]] = [None] * len(closes)
    if len(closes) < period + 1:
        return result

    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    result[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
        result[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    return result


# ---------------------------------------------------------------------------
# VWAP (intraday, cumulative — reset each session)
# ---------------------------------------------------------------------------
def compute_vwap(candles: list[Candle]) -> float:
    """
    Intraday VWAP = Σ(typical_price × volume) / Σ(volume)
    Typical price = (high + low + close) / 3
    Uses all candles since market open (cumulative from index 0).
    Returns 0 if no volume.
    """
    cum_tp_vol = 0.0
    cum_vol = 0.0
    for c in candles:
        tp = (c.high + c.low + c.close) / 3.0
        cum_tp_vol += tp * c.volume
        cum_vol += c.volume
    return cum_tp_vol / cum_vol if cum_vol > 0 else 0.0


# ---------------------------------------------------------------------------
# Candle character helpers
# ---------------------------------------------------------------------------
def candle_body_pct(c: Candle) -> float:
    """Body as % of total range. 0 if doji (range=0)."""
    rng = c.high - c.low
    if rng == 0:
        return 0.0
    return abs(c.close - c.open) / rng * 100


def is_strong_bullish(c: Candle, body_threshold: float = 55.0) -> bool:
    """
    Strong bullish candle:
    - close > open  (green)
    - body >= body_threshold% of range
    """
    return c.close > c.open and candle_body_pct(c) >= body_threshold


def is_strong_bearish(c: Candle, body_threshold: float = 55.0) -> bool:
    """
    Strong bearish candle:
    - close < open  (red)
    - body >= body_threshold% of range
    """
    return c.close < c.open and candle_body_pct(c) >= body_threshold


def candle_range_pct(c: Candle) -> float:
    """High-to-low range as % of close."""
    if c.close == 0:
        return 0.0
    return (c.high - c.low) / c.close * 100


def is_spike_candle(c: Candle, spike_threshold: float = 1.0) -> bool:
    """Returns True if candle moved more than spike_threshold% — avoid trading these."""
    return candle_range_pct(c) > spike_threshold


def has_volume_surge(candles: list[Candle], ratio: float = VOLUME_SURGE_RATIO) -> bool:
    """
    Returns True if the latest candle's volume >= ratio × average of prior 10.
    Confirms institutional participation in the breakout.
    Returns True (no-block) when volume data is unavailable or uniform
    (e.g. Nifty index TWAP mode where all candles have volume=1 or 0).
    """
    if len(candles) < 11:
        return True   # not enough data — don't block
    recent_vols = [c.volume for c in candles[-11:-1]]
    avg_vol = sum(recent_vols) / len(recent_vols)
    if avg_vol == 0:
        return True
    # Uniform volume (TWAP mode) — no real volume data available, skip filter
    if all(v == recent_vols[0] for v in recent_vols):
        return True
    return candles[-1].volume >= ratio * avg_vol


def is_far_enough_from_vwap(price: float, vwap: float, min_pct: float = MIN_VWAP_DISTANCE_PCT) -> bool:
    """
    Returns True if price is at least min_pct% away from VWAP.
    Entries near VWAP are prone to reversal whipsaws.
    """
    if vwap <= 0:
        return True
    distance_pct = abs(price - vwap) / vwap * 100
    return distance_pct >= min_pct


def multi_candle_confirmation(candles: list[Candle], direction: str) -> bool:
    """
    Require 2 out of the last 3 candles to confirm direction.
    direction: "bullish" or "bearish"
    Reduces false breakout entries caused by a single lucky candle.
    """
    if len(candles) < 3:
        return False
    last3 = candles[-3:]
    if direction == "bullish":
        confirming = sum(1 for c in last3 if c.close > c.open)
    else:
        confirming = sum(1 for c in last3 if c.close < c.open)
    return confirming >= 2


# ---------------------------------------------------------------------------
# Sideways detection
# ---------------------------------------------------------------------------
def is_sideways(candles: list[Candle], vwap: float) -> bool:
    """
    Returns True if market is in a sideways/ranging state.

    Logic (either condition triggers sideways):
    1. Efficiency ratio < SIDEWAYS_EFFICIENCY_MIN over last 10 candles
       Efficiency = |net_close_move| / (max_high - min_low)
       Low efficiency means price oscillates rather than trends.
       A gradual linear trend scores high (price moves purposefully).
       A choppy/whipsaw market scores low (price moves but goes nowhere).

    2. Price crossed VWAP even once in last 5 candles
       → price is straddling VWAP = no clear directional bias
    """
    if len(candles) < SIDEWAYS_LOOKBACK:
        return False

    recent   = candles[-SIDEWAYS_LOOKBACK:]
    closes   = [c.close for c in recent]
    max_high = max(c.high for c in recent)
    min_low  = min(c.low  for c in recent)

    if min_low == 0:
        return False

    total_range = max_high - min_low
    if total_range == 0:
        return True  # Completely flat = sideways

    # Condition 1: low directional efficiency → oscillating market
    net_move   = abs(closes[-1] - closes[0])
    efficiency = net_move / total_range
    if efficiency < SIDEWAYS_EFFICIENCY_MIN:
        return True

    # Condition 2: any VWAP crossing in last 5 candles → unstable/choppy
    # Even a single crossing means price is straddling VWAP = no clear side
    if vwap > 0:
        last5_closes = [c.close for c in candles[-5:]]
        crossings = sum(
            1 for i in range(1, len(last5_closes))
            if (last5_closes[i - 1] > vwap) != (last5_closes[i] > vwap)
        )
        if crossings >= 2:
            return True

    return False


# ---------------------------------------------------------------------------
# EMA trend direction
# ---------------------------------------------------------------------------
def ema_trending_up(ema_series: list[Optional[float]]) -> bool:
    """Returns True if the last two EMA values are rising."""
    vals = [v for v in ema_series[-3:] if v is not None]
    return len(vals) >= 2 and vals[-1] > vals[-2]


def ema_trending_down(ema_series: list[Optional[float]]) -> bool:
    """Returns True if the last two EMA values are falling."""
    vals = [v for v in ema_series[-3:] if v is not None]
    return len(vals) >= 2 and vals[-1] < vals[-2]


def ema_slope_strong_up(ema_series: list[Optional[float]], min_pts: float = 8.0) -> bool:
    """
    Returns True if EMA has risen by at least min_pts over the last 5 candles.
    8-pt threshold filters drift; on a real Nifty trend, EMA moves 10-20 pts in 25 min.
    """
    vals = [v for v in ema_series[-6:] if v is not None]
    return len(vals) >= 2 and (vals[-1] - vals[0]) >= min_pts


def ema_slope_strong_down(ema_series: list[Optional[float]], min_pts: float = 8.0) -> bool:
    """
    Returns True if EMA has fallen by at least min_pts over the last 5 candles.
    """
    vals = [v for v in ema_series[-6:] if v is not None]
    return len(vals) >= 2 and (vals[0] - vals[-1]) >= min_pts


# ---------------------------------------------------------------------------
# Master indicator bundle
# ---------------------------------------------------------------------------
def get_latest_indicators(candles: list[Candle]) -> dict:
    """
    Compute all indicators from candle list.
    Returns dict consumed by strategy.py.
    """
    n = len(candles)
    enough = n >= MIN_CANDLES

    if not enough:
        return {
            "enough_data": False,
            "candle_count": n,
            "candles_needed": MIN_CANDLES,
            "vwap": 0.0,
            "ema20": None,
            "ema20_series": [],
            "rsi14": None,
            "market_state": "UNKNOWN",
        }

    closes = [c.close for c in candles]
    ema20_series = compute_ema(closes, 20)
    rsi14_series = compute_rsi(closes, 14)

    # VWAP is intraday — compute only from today's candles so yesterday's seed
    # candles (used for EMA/RSI warmup) don't corrupt the intraday VWAP value.
    today_date = candles[-1].timestamp.date()
    today_candles = [c for c in candles if c.timestamp.date() == today_date]
    vwap = compute_vwap(today_candles) if today_candles else compute_vwap(candles)

    sideways = is_sideways(candles, vwap)
    market_state = "SIDEWAYS" if sideways else "TRENDING"

    return {
        "enough_data": True,
        "candle_count": n,
        "candles_needed": MIN_CANDLES,
        "vwap": round(vwap, 2),
        "ema20": ema20_series[-1],
        "ema20_series": ema20_series,
        "rsi14": rsi14_series[-1],
        "rsi14_series": rsi14_series,
        "market_state": market_state,
        "volume_surge": has_volume_surge(candles),
    }
