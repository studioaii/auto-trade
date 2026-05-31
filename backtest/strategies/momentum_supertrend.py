"""Supertrend + EMA momentum strategy.

Regime filter: ctx.st_dir (supertrend direction +1/-1).
Momentum: EMA9 vs EMA20 stack + price momentum.

LONG when supertrend is +1, EMA9>EMA20, price above EMA9 with a strong
directional close. SHORT mirrored. Ride trends with a trailing stop.
"""
import numpy as np
from engine import RiskParams


class Strategy:
    name = "momentum_supertrend"
    MOM_FLOOR = 1.5   # min |price-EMA20|/ATR for a momentum-continuation entry

    # Most ROBUST risk config found: smallest train/test divergence. A wide
    # trailing stop (trigger 60 / gap 18) lets the rare big momentum runs ride
    # while a 25pt SL caps the frequent reversions. NOTE: this archetype only
    # clears PF>=1.3 IN-SAMPLE; see robustness_notes — OOS stays sub-1.0.
    risk = RiskParams(sl_pts=25, target_pts=50, trail_trigger_pts=60,
                      trail_gap_pts=18, breakeven_pts=None, time_stop_bars=None,
                      max_trades_per_day=2)

    def on_session_start(self, ctx):
        pass

    def on_bar(self, ctx):
        i = ctx.i
        if i < 6:
            return None
        if (np.isnan(ctx.ema9[i]) or np.isnan(ctx.ema20[i]) or
                np.isnan(ctx.atr[i]) or np.isnan(ctx.vwap[i])):
            return None

        c = ctx.c
        ema9 = ctx.ema9[i]
        ema20 = ctx.ema20[i]
        st = ctx.st_dir[i]            # supertrend regime filter (+1/-1)
        atr = ctx.atr[i]
        vwap = ctx.vwap[i]

        body = abs(ctx.c - ctx.o)
        rng = ctx.h - ctx.l
        if rng <= 0 or atr <= 0:
            return None
        body_frac = body / rng
        mom = (c - ema20) / atr       # momentum normalised by ATR

        stack_up = ema9 > ema20
        stack_dn = ema9 < ema20

        # LONG: bullish supertrend regime, EMA9>EMA20 momentum stack, price
        # leading above EMA9 on a bullish bar, with STRONG momentum (price
        # >= MOM_FLOOR ATRs above EMA20). Strong extension here continues —
        # this is momentum continuation, not mean reversion.
        if (st == 1 and stack_up and c > ema9 and ctx.c > ctx.o and
                body_frac >= 0.45 and mom >= self.MOM_FLOOR):
            return "LONG"
        # SHORT: mirrored (only fires if supertrend ever turns bearish).
        if (st == -1 and stack_dn and c < ema9 and ctx.c < ctx.o and
                body_frac >= 0.45 and mom <= -self.MOM_FLOOR):
            return "SHORT"
        return None


STRATEGY = Strategy()
