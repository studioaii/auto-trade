"""VWAP + EMA20 trend breakout (improved cousin of ref_vwap_ema).

Trade WITH the trend: price decisively beyond session VWAP, aligned with a
rising/falling EMA20 slope, breaking the prior N-bar high/low on a strong-body
candle, with an RSI directional filter. Avoid the first ~15 min noise.
"""
import numpy as np
from engine import RiskParams


class Strategy:
    name = "vwap_ema_trend"
    risk = RiskParams(sl_pts=35, target_pts=60, trail_trigger_pts=None,
                      trail_gap_pts=20, breakeven_pts=None, time_stop_bars=None,
                      max_trades_per_day=2)

    # tunables
    LOOKBACK = 8
    MIN_DIST_PCT = 0.18
    BODY_FRAC = 0.65
    EMA_SLOPE_BARS = 3
    RSI_LONG = 55.0
    RSI_SHORT = 45.0
    CLOSE_POS = 0.60   # close must be in top/bottom fraction of bar range
    VOL_MULT = 1.0     # breakout bar volume vs recent avg
    MIN_SLOPE_PCT = 0.02  # |EMA20 slope over EMA_SLOPE_BARS| as % of price

    def on_session_start(self, ctx):
        pass

    def on_bar(self, ctx):
        i = ctx.i
        if i < self.LOOKBACK + self.EMA_SLOPE_BARS:
            return None
        if np.isnan(ctx.ema20[i]) or np.isnan(ctx.rsi[i]) or np.isnan(ctx.vwap[i]):
            return None
        # skip first 15 min; also skip the midday lunch chop (12:00-13:30)
        tt = ctx.t.time()
        if tt.hour == 9 and tt.minute < 30:
            return None
        mins = tt.hour * 60 + tt.minute
        if 12 * 60 <= mins < 13 * 60 + 30:
            return None

        c = ctx.c
        o = ctx.o
        rng = ctx.h - ctx.l
        if rng <= 0:
            return None
        body = abs(c - o)
        body_frac = body / rng

        vwap = ctx.vwap[i]
        dist_pct = (c - vwap) / vwap * 100.0
        ema_now = ctx.ema20[i]
        ema_prev = ctx.ema20[i - self.EMA_SLOPE_BARS]
        ema_slope = ema_now - ema_prev
        slope_pct = abs(ema_slope) / ema_now * 100.0
        slope_strong = slope_pct >= self.MIN_SLOPE_PCT

        prior_high = ctx.highs[i - self.LOOKBACK:i].max()
        prior_low = ctx.lows[i - self.LOOKBACK:i].min()

        # volume surge vs recent average (genuine breakout participation)
        vol_avg = ctx.vols[i - self.LOOKBACK:i].mean()
        vol_ok = vol_avg <= 0 or ctx.v >= self.VOL_MULT * vol_avg

        # close position within bar range (near high for long, near low for short)
        close_pos = (c - ctx.l) / rng

        # structural alignment: EMA20 on the same side of VWAP as our trade
        ema_above_vwap = ema_now > vwap
        ema_below_vwap = ema_now < vwap

        # LONG: above VWAP, above rising EMA20 (and EMA20>VWAP), RSI strong,
        # strong green body closing near its high, breaking prior high.
        if (dist_pct >= self.MIN_DIST_PCT
                and c > ema_now and ema_slope > 0 and ema_above_vwap and slope_strong
                and ctx.rsi[i] > self.RSI_LONG
                and c > o and body_frac >= self.BODY_FRAC
                and close_pos >= self.CLOSE_POS and vol_ok
                and ctx.h >= prior_high):
            return "LONG"

        if (dist_pct <= -self.MIN_DIST_PCT
                and c < ema_now and ema_slope < 0 and ema_below_vwap and slope_strong
                and ctx.rsi[i] < self.RSI_SHORT
                and c < o and body_frac >= self.BODY_FRAC
                and close_pos <= (1.0 - self.CLOSE_POS) and vol_ok
                and ctx.l <= prior_low):
            return "SHORT"

        return None


STRATEGY = Strategy()
