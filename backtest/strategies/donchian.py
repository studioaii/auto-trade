"""Donchian Channel Breakout (turtle-style) adapted to 5-min NIFTY futures.

Compute the highest-high / lowest-low of the prior N bars (excluding the
current bar to avoid self-reference). Enter LONG when the current bar's high
breaks above the prior N-bar high, SHORT when the low breaks below the prior
N-bar low. A VWAP + EMA20 trend filter keeps us on the right side of the day
so we don't fade into the prevailing trend.
"""
from datetime import time as dt_t

import numpy as np
from engine import RiskParams


class Strategy:
    name = "donchian"
    risk = RiskParams(sl_pts=32, target_pts=80, trail_trigger_pts=36,
                      trail_gap_pts=14, breakeven_pts=None, time_stop_bars=10,
                      max_trades_per_day=2)

    N = 12  # Donchian lookback (prior bars)

    def on_session_start(self, ctx):
        pass

    def on_bar(self, ctx):
        i = ctx.i
        N = self.N
        # need N prior bars + warmed indicators
        if i < N + 1:
            return None
        if (np.isnan(ctx.ema20[i]) or np.isnan(ctx.vwap[i])
                or np.isnan(ctx.rsi[i]) or np.isnan(ctx.atr[i])):
            return None

        # Restrict to the trend-development window (skip the open noise and the
        # dead midday/late-day chop where breakouts mostly fail).
        tm = ctx.t.time()
        if tm < dt_t(9, 45) or tm > dt_t(13, 0):
            return None

        c = ctx.c
        o = ctx.o
        vwap = ctx.vwap[i]
        ema20 = ctx.ema20[i]
        atrv = ctx.atr[i]

        # Donchian channel of the PRIOR N bars (exclude current bar i)
        prior_high = ctx.highs[i - N:i].max()
        prior_low = ctx.lows[i - N:i].min()

        # require a meaningful channel (avoid chop): channel width vs ATR
        width = prior_high - prior_low
        if width < 1.0 * atrv:
            return None

        # strong breakout bar: directional body that closes beyond the channel
        body = abs(c - o)
        rng = ctx.h - ctx.l
        if rng <= 0:
            return None
        strong_body = body >= 0.5 * rng

        # volume surge vs recent average (follow-through conviction)
        avg_vol = ctx.vols[i - N:i].mean()
        vol_ok = avg_vol > 0 and ctx.v >= 1.2 * avg_vol

        dist_pct = (c - vwap) / vwap * 100.0

        # LONG: CLOSE above prior N-bar high, up-trend filter, strong + volume
        if (c > prior_high
                and c > vwap and c > ema20
                and dist_pct >= 0.10
                and ctx.rsi[i] > 55
                and c > o and strong_body and vol_ok):
            return "LONG"

        # SHORT: CLOSE below prior N-bar low, down-trend filter, strong + volume
        if (c < prior_low
                and c < vwap and c < ema20
                and dist_pct <= -0.10
                and ctx.rsi[i] < 45
                and c < o and strong_body and vol_ok):
            return "SHORT"

        return None


STRATEGY = Strategy()
