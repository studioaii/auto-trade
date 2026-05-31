"""Opening Range Breakout (ORB).

Opening range = high/low of the first 3 bars (09:15, 09:20, 09:25).
Within 09:35-11:00, go LONG on a 5-min close above OR-high + buffer,
SHORT on a close below OR-low - buffer. Requires a volume surge and a
directional body. One breakout direction per day.
"""
import datetime as dt

import numpy as np
from engine import RiskParams


class Strategy:
    name = "orb"
    risk = RiskParams(sl_pts=30, target_pts=80, trail_trigger_pts=38,
                      trail_gap_pts=14, breakeven_pts=None, time_stop_bars=None,
                      max_trades_per_day=2)

    OR_BARS = 3                 # first 3 bars define the opening range
    BUFFER_PCT = 0.08           # close must clear OR by this % to count
    VOL_MULT = 1.3              # breakout bar volume vs recent rolling avg
    VOL_LOOKBACK = 5            # bars for the rolling volume baseline
    BODY_FRAC = 0.60            # |close-open| must be >= this fraction of bar range
    RSI_CAP = 72                # reject exhausted breakouts (LONG rsi>cap / SHORT rsi<100-cap)
    ENTRY_START = dt.time(9, 35)
    ENTRY_END = dt.time(11, 0)

    def on_session_start(self, ctx):
        self.or_high = None
        self.or_low = None
        self.done = False        # one breakout direction per day

    def on_bar(self, ctx):
        i = ctx.i
        if self.done or i < self.OR_BARS:
            return None
        # Define OR from the first OR_BARS closed bars (computed lazily — the
        # engine doesn't call on_bar for the pre-09:30 OR bars).
        if self.or_high is None:
            self.or_high = ctx.highs[:self.OR_BARS].max()
            self.or_low = ctx.lows[:self.OR_BARS].min()

        t = ctx.t.time()
        if t < self.ENTRY_START or t > self.ENTRY_END:
            return None

        # body / volume confirmation
        rng = ctx.h - ctx.l
        if rng <= 0:
            return None
        body = abs(ctx.c - ctx.o)
        if body < self.BODY_FRAC * rng:
            return None
        # volume surge vs recent rolling baseline (exclude current bar)
        lb0 = max(self.OR_BARS, i - self.VOL_LOOKBACK)
        if i > lb0:
            recent_avg = ctx.vols[lb0:i].mean()
            if recent_avg > 0 and ctx.v < self.VOL_MULT * recent_avg:
                return None

        long_trig = self.or_high * (1.0 + self.BUFFER_PCT / 100.0)
        short_trig = self.or_low * (1.0 - self.BUFFER_PCT / 100.0)
        r = ctx.rsi[i]
        rsi_ok = np.isnan(r)  # if RSI unavailable, don't block

        if ctx.c > long_trig and ctx.c > ctx.o and (rsi_ok or r <= self.RSI_CAP):
            self.done = True
            return "LONG"
        if ctx.c < short_trig and ctx.c < ctx.o and (rsi_ok or r >= (100 - self.RSI_CAP)):
            self.done = True
            return "SHORT"
        return None


STRATEGY = Strategy()
