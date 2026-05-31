"""VWAP Mean-Reversion / Fade.

Fade overextension back toward VWAP. SHORT when price is stretched far ABOVE
VWAP with RSI overbought and an exhaustion/reversal candle. LONG mirrored below
VWAP with RSI oversold. Counter-trend, selective, tight SL/target.
"""
import numpy as np
from engine import RiskParams


class Strategy:
    name = "vwap_reversion"
    risk = RiskParams(sl_pts=30, target_pts=40, trail_trigger_pts=None,
                      trail_gap_pts=14, breakeven_pts=None, time_stop_bars=10,
                      max_trades_per_day=2)

    STRETCH_PCT = 0.42    # min |close - vwap| / vwap % at the extreme
    RSI_LO = 36
    MAX_VWAP_SLOPE = 0.06  # |VWAP %-slope over 5 bars| ceiling -> trade chop only

    def on_session_start(self, ctx):
        pass

    def on_bar(self, ctx):
        i = ctx.i
        if i < 6 or np.isnan(ctx.vwap[i]) or np.isnan(ctx.rsi[i]) or np.isnan(ctx.ema20[i]):
            return None
        c, o, h, l = ctx.c, ctx.o, ctx.h, ctx.l
        cp = ctx.closes[i - 1]      # prior close
        vwap = ctx.vwap[i]
        rng = h - l
        if rng <= 0:
            return None

        dist_pct = (c - vwap) / vwap * 100.0
        prior_dist_pct = (cp - vwap) / vwap * 100.0
        close_pos = (c - l) / rng    # 0=close at low, 1=close at high

        # Only fade on chop/range days: VWAP must be roughly flat. On strong
        # trend days the stretch just persists and the fade gets run over.
        vwap_slope_pct = abs((vwap - ctx.vwap[i - 5]) / ctx.vwap[i - 5] * 100.0)
        if vwap_slope_pct > self.MAX_VWAP_SLOPE:
            return None

        # LONG ONLY. On this rising-market dataset, fading rallies (shorts)
        # is structurally unprofitable; the durable reversion edge is fading
        # oversold dips below VWAP that have stalled. Stretched BELOW VWAP,
        # oversold, prior bar also stretched, and this bar turned back up.
        if (dist_pct <= -self.STRETCH_PCT and ctx.rsi[i] <= self.RSI_LO
                and prior_dist_pct <= -self.STRETCH_PCT and c > cp
                and close_pos >= 0.60):
            return "LONG"

        return None


STRATEGY = Strategy()
