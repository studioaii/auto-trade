"""Intraday NIFTY-futures backtest engine (shared harness).

The engine owns market mechanics, the EXIT/risk model, and cost accounting so
every strategy is compared on equal footing. A Strategy supplies only ENTRY
alpha plus its preferred risk parameters.

────────────────────────────────────────────────────────────────────────────
STRATEGY INTERFACE  (see strategies/_template.py)
────────────────────────────────────────────────────────────────────────────
A strategy is a class:

    class MyStrategy:
        name = "my_strategy"
        # risk params the engine's exit model will use for each trade:
        risk = RiskParams(sl_pts=30, target_pts=60, trail_trigger_pts=40,
                          trail_gap_pts=20, time_stop_bars=12)

        def on_session_start(self, ctx): ...        # optional, reset per day
        def on_bar(self, ctx) -> str | None:
            # return "LONG", "SHORT", or None. Called once per CLOSED bar.
            # Only allowed to enter when ctx.position == 0.
            ...

`ctx` (BarContext) exposes, at the current bar index i (this bar has CLOSED):
    ctx.i, ctx.n               int index, total bars in session
    ctx.t                      pandas.Timestamp of this bar
    ctx.o/h/l/c/v              float scalars for this bar
    ctx.opens/highs/lows/closes/vols   np arrays for the WHOLE day (use [:i+1])
    ctx.ema20, ctx.ema9, ctx.vwap, ctx.rsi, ctx.atr   np arrays (day), value at i
    ctx.st_dir                 supertrend direction array (+1/-1)
    ctx.position               0 flat (entries only allowed here)
    ctx.session_date           date
    ctx.bars_since_open        i
    ctx.minutes_to_close       int
No look-ahead: arrays past index i are NaN-safe but MUST NOT be read.

Exit model (engine-owned), applied intrabar on subsequent bars:
  - hard SL / fixed target (points)
  - trailing SL once trail_trigger_pts reached (trails trail_gap_pts off peak)
  - optional breakeven
  - time stop (bars in trade) if still < breakeven
  - hard end-of-day force exit
Intrabar fill priority when both SL & target lie inside a bar's range: SL first
(conservative). Entries fill at next bar's OPEN (no same-bar look-ahead).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from indicators import ema, rsi, session_vwap, atr, supertrend

DATA = Path(__file__).resolve().parent / "data"

# ── Market / cost constants (NIFTY futures, Zerodha, conservative) ──────────
LOT_SIZE = 65                 # qty per lot; 1 index point = ₹65 per lot
COST_RUPEES_ROUNDTRIP = 450.0  # brokerage+STT+exch+GST+stamp per lot round trip
SLIPPAGE_PTS_PER_SIDE = 1.0    # modelled adverse fill, each side
SESSION_OPEN = dt.time(9, 15)
ENTRY_START = dt.time(9, 30)   # default; strategy may be stricter
ENTRY_CUTOFF = dt.time(14, 30)  # no new entries after
FORCE_EXIT = dt.time(15, 15)


@dataclass
class RiskParams:
    sl_pts: float = 30.0
    target_pts: float = 60.0
    trail_trigger_pts: float | None = None   # None disables trailing
    trail_gap_pts: float = 20.0
    breakeven_pts: float | None = None        # move SL to entry after +X pts
    time_stop_bars: int | None = None         # exit if still <BE after N bars
    max_trades_per_day: int = 2
    entry_cutoff: dt.time = ENTRY_CUTOFF


@dataclass
class Trade:
    session: str
    side: str          # LONG / SHORT
    entry_time: str
    entry_px: float
    exit_time: str
    exit_px: float
    gross_pts: float
    net_pts: float
    net_rupees: float
    reason: str
    bars_held: int


def load_bars(name: str) -> pd.DataFrame:
    """name in {'fut','spot'} or a filename. Returns df with precomputed indicators."""
    fname = {"fut": "nifty_fut_5min.csv", "spot": "nifty_spot_5min.csv"}.get(name, name)
    df = pd.read_csv(DATA / fname, parse_dates=["datetime"])
    df["session"] = df["datetime"].dt.date.astype(str)
    df = df.sort_values("datetime").reset_index(drop=True)
    # Precompute indicators per session (no cross-day leakage).
    df["vwap"] = session_vwap(df)
    parts_ema20, parts_ema9, parts_rsi, parts_atr, parts_stdir = [], [], [], [], []
    for _, g in df.groupby("session", sort=False):
        parts_ema20.append(ema(g["close"].to_numpy(), 20))
        parts_ema9.append(ema(g["close"].to_numpy(), 9))
        parts_rsi.append(rsi(g["close"].to_numpy(), 14))
        parts_atr.append(atr(g, 14))
        _, d = supertrend(g, 10, 3.0)
        parts_stdir.append(d)
    df["ema20"] = np.concatenate(parts_ema20)
    df["ema9"] = np.concatenate(parts_ema9)
    df["rsi"] = np.concatenate(parts_rsi)
    df["atr"] = np.concatenate(parts_atr)
    df["st_dir"] = np.concatenate(parts_stdir)
    return df


class BarContext:
    __slots__ = ("i", "n", "t", "o", "h", "l", "c", "v", "opens", "highs", "lows",
                 "closes", "vols", "ema20", "ema9", "vwap", "rsi", "atr", "st_dir",
                 "position", "session_date", "bars_since_open", "minutes_to_close", "_t")

    def __init__(self, g: pd.DataFrame):
        self.opens = g["open"].to_numpy(float)
        self.highs = g["high"].to_numpy(float)
        self.lows = g["low"].to_numpy(float)
        self.closes = g["close"].to_numpy(float)
        self.vols = g["volume"].to_numpy(float)
        self._t = g["datetime"].to_numpy()
        self.ema20 = g["ema20"].to_numpy(float)
        self.ema9 = g["ema9"].to_numpy(float)
        self.vwap = g["vwap"].to_numpy(float)
        self.rsi = g["rsi"].to_numpy(float)
        self.atr = g["atr"].to_numpy(float)
        self.st_dir = g["st_dir"].to_numpy(int)
        self.n = len(g)
        self.session_date = g["session"].iloc[0]
        self.position = 0

    def _set(self, i):
        self.i = i
        self.t = pd.Timestamp(self._t[i])
        self.o, self.h, self.l, self.c, self.v = (
            self.opens[i], self.highs[i], self.lows[i], self.closes[i], self.vols[i])
        self.bars_since_open = i
        close_dt = self.t.replace(hour=15, minute=30)
        self.minutes_to_close = int((close_dt - self.t).total_seconds() // 60)


def _simulate_exit(g_h, g_l, g_o, times, entry_i, entry_px, side, rp: RiskParams):
    """Walk bars after entry_i; return (exit_i, exit_px, reason, bars_held)."""
    sign = 1 if side == "LONG" else -1
    sl = entry_px - sign * rp.sl_pts
    tgt = entry_px + sign * rp.target_pts
    peak = 0.0   # best favorable excursion in POINTS (not price)
    be_done = False
    trailing = False
    n = len(g_o)
    for j in range(entry_i + 1, n):
        hi, lo = g_h[j], g_l[j]
        tj = pd.Timestamp(times[j])
        # End-of-day force exit at/after FORCE_EXIT -> exit at this bar open
        if tj.time() >= FORCE_EXIT:
            return j, g_o[j], "EOD", j - entry_i
        fav = (hi - entry_px) if side == "LONG" else (entry_px - lo)
        peak = max(peak, fav)
        # breakeven
        if rp.breakeven_pts is not None and not be_done and fav >= rp.breakeven_pts:
            sl = entry_px
            be_done = True
        # trailing
        if rp.trail_trigger_pts is not None and peak >= rp.trail_trigger_pts:
            trailing = True
        if trailing:
            new_sl = (entry_px + sign * (peak - rp.trail_gap_pts))
            sl = max(sl, new_sl) if side == "LONG" else min(sl, new_sl)
        # SL checked before target (conservative) within the bar
        if side == "LONG":
            if lo <= sl:
                return j, sl, "TRAIL_SL" if trailing else ("BE" if be_done and sl == entry_px else "SL"), j - entry_i
            if hi >= tgt:
                return j, tgt, "TARGET", j - entry_i
        else:
            if hi >= sl:
                return j, sl, "TRAIL_SL" if trailing else ("BE" if be_done and sl == entry_px else "SL"), j - entry_i
            if lo <= tgt:
                return j, tgt, "TARGET", j - entry_i
        # time stop
        if rp.time_stop_bars is not None and (j - entry_i) >= rp.time_stop_bars:
            if fav < (rp.breakeven_pts or 0):
                return j, g_o[j] if j + 1 >= n else g_o[j], "TIME", j - entry_i
    # ran out of bars (shouldn't happen — EOD catches) -> close at last
    return n - 1, g_o[n - 1], "EOD", n - 1 - entry_i


def run_backtest(df: pd.DataFrame, strategy, date_from=None, date_to=None) -> dict:
    """Run a strategy across sessions. Returns {'trades': [...], 'equity': [...]}.

    date_from/date_to are 'YYYY-MM-DD' strings (inclusive) for train/test splits.
    """
    rp: RiskParams = getattr(strategy, "risk", RiskParams())
    trades: list[Trade] = []
    sessions = sorted(df["session"].unique())
    if date_from:
        sessions = [s for s in sessions if s >= date_from]
    if date_to:
        sessions = [s for s in sessions if s <= date_to]

    for sess in sessions:
        g = df[df["session"] == sess].reset_index(drop=True)
        if len(g) < 25:
            continue
        ctx = BarContext(g)
        g_h, g_l, g_o = ctx.highs, ctx.lows, ctx.opens
        times = ctx._t
        if hasattr(strategy, "on_session_start"):
            strategy.on_session_start(ctx)
        i = 0
        day_trades = 0
        while i < ctx.n - 1:
            ctx._set(i)
            ctx.position = 0
            if day_trades >= rp.max_trades_per_day:
                break
            if ctx.t.time() < ENTRY_START or ctx.t.time() > rp.entry_cutoff:
                i += 1
                continue
            sig = strategy.on_bar(ctx)
            if sig in ("LONG", "SHORT"):
                entry_i = i + 1                       # fill next bar open
                if entry_i >= ctx.n:
                    break
                raw_entry = g_o[entry_i]
                sign = 1 if sig == "LONG" else -1
                entry_px = raw_entry + sign * SLIPPAGE_PTS_PER_SIDE  # slip against us
                ex_i, ex_px_raw, reason, held = _simulate_exit(
                    g_h, g_l, g_o, times, entry_i, raw_entry, sig, rp)
                exit_px = ex_px_raw - sign * SLIPPAGE_PTS_PER_SIDE   # slip against us
                gross = sign * (ex_px_raw - raw_entry)
                net_pts = sign * (exit_px - entry_px)
                net_rupees = net_pts * LOT_SIZE - COST_RUPEES_ROUNDTRIP
                trades.append(Trade(
                    session=sess, side=sig,
                    entry_time=str(pd.Timestamp(times[entry_i]).time()),
                    entry_px=round(entry_px, 2),
                    exit_time=str(pd.Timestamp(times[ex_i]).time()),
                    exit_px=round(exit_px, 2),
                    gross_pts=round(gross, 2), net_pts=round(net_pts, 2),
                    net_rupees=round(net_rupees, 2), reason=reason, bars_held=held))
                day_trades += 1
                i = ex_i + 1                          # resume after exit
            else:
                i += 1

    equity = np.cumsum([t.net_rupees for t in trades]).tolist()
    return {"trades": trades, "equity": equity, "sessions": len(sessions)}
