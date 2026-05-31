"""Vectorized intraday indicators for the futures backtest (numpy/pandas).

All functions are pure and operate on numpy arrays. VWAP and any session-based
indicator must be computed per trading session (reset each day) — use the
`session_id` helper to split. No look-ahead: every value at index i uses only
data up to and including i.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average (TradingView-compatible: SMA seed)."""
    v = np.asarray(values, dtype=float)
    out = np.full_like(v, np.nan)
    if len(v) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    seed = v[:period].mean()
    out[period - 1] = seed
    for i in range(period, len(v)):
        out[i] = alpha * v[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI."""
    v = np.asarray(values, dtype=float)
    out = np.full_like(v, np.nan)
    if len(v) <= period:
        return out
    delta = np.diff(v)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = gain[:period].mean()
    avg_loss = loss[:period].mean()
    for i in range(period, len(v)):
        g = gain[i - 1]
        l = loss[i - 1]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else np.inf
        out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def session_vwap(df: pd.DataFrame) -> np.ndarray:
    """Per-session VWAP. df must have columns high, low, close, volume and a
    'session' column (date). Resets at each session boundary."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    cum_pv = pv.groupby(df["session"]).cumsum()
    cum_v = df["volume"].groupby(df["session"]).cumsum()
    vwap = cum_pv / cum_v.replace(0, np.nan)
    return vwap.to_numpy()


def atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """Average True Range (Wilder)."""
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    prev_c = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    out = np.full_like(tr, np.nan)
    if len(tr) <= period:
        return out
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0):
    """Returns (supertrend_line, direction) where direction is +1 (up/long) or -1."""
    a = atr(df, period)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    hl2 = (h + l) / 2.0
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    n = len(c)
    st = np.full(n, np.nan)
    dir_ = np.ones(n, dtype=int)
    f_upper = upper.copy()
    f_lower = lower.copy()
    for i in range(1, n):
        if np.isnan(a[i]):
            continue
        f_upper[i] = upper[i] if (upper[i] < f_upper[i - 1] or c[i - 1] > f_upper[i - 1]) else f_upper[i - 1]
        f_lower[i] = lower[i] if (lower[i] > f_lower[i - 1] or c[i - 1] < f_lower[i - 1]) else f_lower[i - 1]
        if c[i] > f_upper[i - 1]:
            dir_[i] = 1
        elif c[i] < f_lower[i - 1]:
            dir_[i] = -1
        else:
            dir_[i] = dir_[i - 1]
        st[i] = f_lower[i] if dir_[i] == 1 else f_upper[i]
    return st, dir_
