"""Reference strategy: VWAP + EMA20 trend breakout (validates the harness).

LONG when: price closes above VWAP by >=0.10%, above EMA20, RSI>52, breaks the
high of the last 3 bars. SHORT mirrored. One direction per session bias.
"""
import numpy as np
from engine import RiskParams


class Strategy:
    name = "ref_vwap_ema"
    risk = RiskParams(sl_pts=30, target_pts=55, trail_trigger_pts=35,
                      trail_gap_pts=18, breakeven_pts=15, time_stop_bars=12,
                      max_trades_per_day=2)

    def on_session_start(self, ctx):
        pass

    def on_bar(self, ctx):
        i = ctx.i
        if i < 5 or np.isnan(ctx.ema20[i]) or np.isnan(ctx.rsi[i]) or np.isnan(ctx.vwap[i]):
            return None
        c = ctx.c
        vwap = ctx.vwap[i]
        dist_pct = (c - vwap) / vwap * 100.0
        prior_high = ctx.highs[i - 3:i].max()
        prior_low = ctx.lows[i - 3:i].min()
        if dist_pct >= 0.10 and c > ctx.ema20[i] and ctx.rsi[i] > 52 and ctx.h >= prior_high:
            return "LONG"
        if dist_pct <= -0.10 and c < ctx.ema20[i] and ctx.rsi[i] < 48 and ctx.l <= prior_low:
            return "SHORT"
        return None


STRATEGY = Strategy()
