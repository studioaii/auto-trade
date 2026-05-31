"""Parametrized ORB + sweep/robustness lab for hardening the opening-range setup.

Single source of truth for ORB logic (look-ahead-safe, mirrors strategies/orb.py)
so every variant is comparable. Use as a library or CLI.

CLI:
  # evaluate one config (JSON), full + OOS split:
  python backtest/orb_lab.py eval '{"or_bars":3,"buffer_pct":0.08,"target_pts":80,"trail_trigger_pts":38}'

  # grid sweep over a JSON grid (lists of values per key); ranks by robustness:
  python backtest/orb_lab.py sweep '{"or_bars":[2,3],"buffer_pct":[0.04,0.08],"entry_end":["11:00","13:00"]}'

  # robustness battery on one config (jackknife, per-half regime, neighbourhood):
  python backtest/orb_lab.py stress '{"or_bars":3,"buffer_pct":0.08}'

Robustness score (sweep ranking) = min(train_PF, test_PF), with hard filters:
both splits net-positive, full trades >= MIN_TRADES. Higher = more robust.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "strategies"))

from engine import RiskParams, load_bars, run_backtest  # noqa: E402
import metrics as M  # noqa: E402

MIN_TRADES = 15
TRAIN = (None, "2026-05-08")
TEST = ("2026-05-11", None)

DEFAULTS = dict(
    or_bars=3, buffer_pct=0.08, vol_mult=1.3, vol_lookback=5, body_frac=0.60,
    rsi_cap=72, entry_start="9:35", entry_end="11:00",
    sl_pts=30, target_pts=80, trail_trigger_pts=38, trail_gap_pts=14,
    breakeven_pts=None, time_stop_bars=None, max_trades_per_day=2,
)


def _parse_t(s):
    h, m = str(s).split(":")
    return dt.time(int(h), int(m))


def make_strategy(p: dict):
    cfg = {**DEFAULTS, **p}

    class _ORB:
        name = "orb_var"
        risk = RiskParams(
            sl_pts=cfg["sl_pts"], target_pts=cfg["target_pts"],
            trail_trigger_pts=cfg["trail_trigger_pts"], trail_gap_pts=cfg["trail_gap_pts"],
            breakeven_pts=cfg["breakeven_pts"], time_stop_bars=cfg["time_stop_bars"],
            max_trades_per_day=cfg["max_trades_per_day"], entry_cutoff=dt.time(14, 30))
        OR_BARS = cfg["or_bars"]
        BUFFER_PCT = cfg["buffer_pct"]
        VOL_MULT = cfg["vol_mult"]
        VOL_LOOKBACK = cfg["vol_lookback"]
        BODY_FRAC = cfg["body_frac"]
        RSI_CAP = cfg["rsi_cap"]
        ENTRY_START = _parse_t(cfg["entry_start"])
        ENTRY_END = _parse_t(cfg["entry_end"])

        def on_session_start(self, ctx):
            self.or_high = None
            self.or_low = None
            self.done = False

        def on_bar(self, ctx):
            i = ctx.i
            if self.done or i < self.OR_BARS:
                return None
            if self.or_high is None:
                self.or_high = ctx.highs[:self.OR_BARS].max()
                self.or_low = ctx.lows[:self.OR_BARS].min()
            t = ctx.t.time()
            if t < self.ENTRY_START or t > self.ENTRY_END:
                return None
            rng = ctx.h - ctx.l
            if rng <= 0:
                return None
            if self.BODY_FRAC > 0 and abs(ctx.c - ctx.o) < self.BODY_FRAC * rng:
                return None
            if self.VOL_MULT and self.VOL_MULT > 1.0:
                lb0 = max(self.OR_BARS, i - self.VOL_LOOKBACK)
                if i > lb0:
                    avg = ctx.vols[lb0:i].mean()
                    if avg > 0 and ctx.v < self.VOL_MULT * avg:
                        return None
            long_trig = self.or_high * (1.0 + self.BUFFER_PCT / 100.0)
            short_trig = self.or_low * (1.0 - self.BUFFER_PCT / 100.0)
            r = ctx.rsi[i]
            cap = self.RSI_CAP
            r_ok_long = (cap is None) or np.isnan(r) or r <= cap
            r_ok_short = (cap is None) or np.isnan(r) or r >= (100 - cap)
            if ctx.c > long_trig and ctx.c > ctx.o and r_ok_long:
                self.done = True
                return "LONG"
            if ctx.c < short_trig and ctx.c < ctx.o and r_ok_short:
                self.done = True
                return "SHORT"
            return None

    return _ORB()


def evaluate(df, p: dict, frm=None, to=None):
    res = run_backtest(df, make_strategy(p), frm, to)
    return M.compute(res["trades"], res["sessions"]), res["trades"]


def eval_splits(df, p: dict):
    full, trades = evaluate(df, p)
    tr, _ = evaluate(df, p, *TRAIN)
    te, _ = evaluate(df, p, *TEST)
    return full, tr, te, trades


def robustness_score(full, tr, te):
    def pf(m):
        v = m["profit_factor"]
        return 99.0 if v == float("inf") else v
    if full["trades"] < MIN_TRADES or tr["net_rupees"] <= 0 or te["net_rupees"] <= 0:
        return -1.0
    return round(min(pf(tr), pf(te)), 2)


def cmd_eval(df, p):
    full, tr, te, trades = eval_splits(df, p)
    print("config:", json.dumps({**DEFAULTS, **p}))
    print("FULL :", M.summary_line(full))
    print("TRAIN:", M.summary_line(tr))
    print("TEST :", M.summary_line(te))
    print("robustness_score:", robustness_score(full, tr, te))
    print("JSON " + json.dumps({"config": {**DEFAULTS, **p}, "full": full, "train": tr,
                                "test": te, "robustness": robustness_score(full, tr, te)}))


def cmd_sweep(df, grid):
    keys = list(grid)
    combos = list(itertools.product(*[grid[k] for k in keys]))
    rows = []
    for combo in combos:
        p = dict(zip(keys, combo))
        full, tr, te, _ = eval_splits(df, p)
        rows.append((robustness_score(full, tr, te), p, full, tr, te))
    rows.sort(key=lambda r: r[0], reverse=True)
    print(f"swept {len(combos)} configs; top 15 by robustness (min train/test PF, both net+):")
    top = []
    for score, p, full, tr, te in rows[:15]:
        print(f"  score={score:>6}  full[PF={full['profit_factor']} net={full['net_rupees']:.0f} "
              f"n={full['trades']} dd={full['max_drawdown']:.0f}]  "
              f"train[PF={tr['profit_factor']} n={tr['trades']}]  "
              f"test[PF={te['profit_factor']} net={te['net_rupees']:.0f} n={te['trades']}]  {json.dumps(p)}")
        top.append({"score": score, "params": p, "full": full, "train": tr, "test": te})
    print("JSON " + json.dumps({"n_configs": len(combos), "top": top}))


def cmd_stress(df, p):
    full, tr, te, trades = eval_splits(df, p)
    print("config:", json.dumps({**DEFAULTS, **p}))
    print("baseline FULL:", M.summary_line(full))
    # jackknife: remove single best trade
    pnls = sorted(trades, key=lambda t: t.net_rupees, reverse=True)
    if pnls:
        best = pnls[0]
        rest = [t for t in trades if t is not best]
        m2 = M.compute(rest, full["sessions"])
        print(f"remove best trade (+Rs{best.net_rupees:.0f}): net=Rs{m2['net_rupees']:.0f} "
              f"PF={m2['profit_factor']} (best trade = {100*best.net_rupees/max(full['net_rupees'],1):.0f}% of net)")
    # per-half regime split by session
    sess = sorted({t.session for t in trades})
    if len(sess) >= 4:
        mid = sess[len(sess)//2]
        h1 = [t for t in trades if t.session < mid]
        h2 = [t for t in trades if t.session >= mid]
        m1 = M.compute(h1, full["sessions"]); mh2 = M.compute(h2, full["sessions"])
        print(f"first half (<{mid}):  net=Rs{m1['net_rupees']:.0f} PF={m1['profit_factor']} n={m1['trades']}")
        print(f"second half (>={mid}): net=Rs{mh2['net_rupees']:.0f} PF={mh2['profit_factor']} n={mh2['trades']}")
    # parameter neighbourhood: nudge buffer/target/trail +-1 step
    print("neighbourhood (perturb one param at a time):")
    base = {**DEFAULTS, **p}
    for key, deltas in [("buffer_pct", [-0.04, 0.04]), ("target_pts", [-20, 20]),
                        ("trail_trigger_pts", [-8, 8]), ("sl_pts", [-5, 5])]:
        if base.get(key) is None:
            continue
        for d in deltas:
            q = {**p, key: round(base[key] + d, 4)}
            f2, t2, e2, _ = eval_splits(df, q)
            print(f"  {key}{d:+}: full PF={f2['profit_factor']} net={f2['net_rupees']:.0f} "
                  f"| test PF={e2['profit_factor']} net={e2['net_rupees']:.0f}")


def main():
    cmd = sys.argv[1]
    payload = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    df = load_bars("fut")
    {"eval": cmd_eval, "sweep": cmd_sweep, "stress": cmd_stress}[cmd](df, payload)


if __name__ == "__main__":
    main()
