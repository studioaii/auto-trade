"""ORB + Trend/Volume Hybrid (most selective of the breakout family).

Opening-range breakout, but only taken when it AGREES with:
  - VWAP side (price on breakout side of session VWAP)
  - EMA20 slope (directional confirmation)
  - a volume surge vs the running session-average volume
And we SKIP breakouts that immediately fade back inside the OR.

Fewer, higher-quality trades. One clean breakout per direction.
"""
import datetime as dt

import numpy as np
from engine import RiskParams


# Opening range = bars from session open up to (but not incl.) this time.
OR_END = dt.time(9, 35)        # OR = 09:15, 09:20, 09:25, 09:30 (15-min OR)
ENTRY_WINDOW_END = dt.time(13, 30)  # ORB window: morning / early-afternoon


class Strategy:
    name = "orb_trend_hybrid"
    risk = RiskParams(
        sl_pts=22,
        target_pts=55,
        trail_trigger_pts=30,
        trail_gap_pts=15,
        breakeven_pts=None,
        time_stop_bars=10,
        max_trades_per_day=2,
    )

    def on_session_start(self, ctx):
        self._or_hi = None
        self._or_lo = None
        self._or_vols = []
        self._or_locked = False

    def _build_or(self, ctx):
        """Compute OR high/low/vol from bars at/after open, strictly before OR_END."""
        import pandas as pd
        i = ctx.i
        his, los, vols = ctx.highs, ctx.lows, ctx.vols
        hi = -np.inf
        lo = np.inf
        ovols = []
        for j in range(0, i + 1):
            t = pd.Timestamp(ctx._t[j]).time()
            if t < OR_END:
                hi = max(hi, his[j])
                lo = min(lo, los[j])
                ovols.append(vols[j])
        if len(ovols) >= 3:
            self._or_hi = hi
            self._or_lo = lo
            self._or_vols = ovols
            self._or_locked = True

    def on_bar(self, ctx):
        i = ctx.i
        if i < 4:
            return None

        # Lock the opening range once we are past OR_END.
        if not self._or_locked and ctx.t.time() >= OR_END:
            self._build_or(ctx)
        if not self._or_locked or self._or_hi is None:
            return None

        # Only fresh breakouts in the morning/early-afternoon window.
        if ctx.t.time() >= ENTRY_WINDOW_END:
            return None

        if (np.isnan(ctx.ema20[i]) or np.isnan(ctx.vwap[i])
                or np.isnan(ctx.rsi[i]) or np.isnan(ctx.atr[i])):
            return None

        c = ctx.c
        vwap = ctx.vwap[i]
        or_hi = self._or_hi
        or_lo = self._or_lo
        or_range = or_hi - or_lo
        # OR-range regime filter: too narrow = chop, too wide = exhausted.
        if or_range < 18 or or_range > 150:
            return None

        # Breakout must clear the OR edge by a small buffer (not a marginal poke).
        buf = max(3.0, 0.06 * or_range)

        # EMA20 slope over the last few bars (directional confirmation).
        ema_now = ctx.ema20[i]
        ema_prev = ctx.ema20[i - 3]
        ema_slope = ema_now - ema_prev

        # Volume surge vs the recent rolling average (last ~6 bars before now).
        lo_idx = max(0, i - 6)
        recent_vol = np.nanmean(ctx.vols[lo_idx:i]) if i > lo_idx else ctx.v
        vol_surge = ctx.v >= 1.5 * recent_vol if recent_vol > 0 else False

        # Don't chase an over-extended move: cap distance from VWAP.
        dist_pct = abs(c - vwap) / vwap * 100.0
        if dist_pct > 1.2:
            return None

        # Anti-fade: decisive close beyond edge with body on the right side.
        body = c - ctx.o

        # ---- LONG breakout above OR high ----
        long_break = (
            ctx.h >= or_hi
            and c >= or_hi + buf               # decisive close above edge
            and c > vwap                       # VWAP agrees
            and c > ema_now and ema_slope > 0  # EMA20 slope up
            and ctx.rsi[i] > 52
            and vol_surge
            and body > 0
        )
        if long_break:
            return "LONG"

        # ---- SHORT breakout below OR low ----
        short_break = (
            ctx.l <= or_lo
            and c <= or_lo - buf
            and c < vwap
            and c < ema_now and ema_slope < 0
            and ctx.rsi[i] < 48
            and vol_surge
            and body < 0
        )
        if short_break:
            return "SHORT"

        return None


STRATEGY = Strategy()
