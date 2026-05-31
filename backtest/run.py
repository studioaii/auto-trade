"""Backtest runner.

  python backtest/run.py <strategy_module> [--data fut|spot] [--split]

Loads strategies/<module>.py (must expose STRATEGY), runs the full period and,
with --split, an out-of-sample train/test split. Prints a JSON blob on the last
line (machine-readable for agents) plus a human summary above it.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "strategies"))

from engine import load_bars, run_backtest  # noqa: E402
import metrics as M  # noqa: E402

# OOS splits (inclusive). fut data spans Apr 1–May 29; spot spans Mar 2–May 29.
SPLITS = {
    "fut": {"train": (None, "2026-05-08"), "test": ("2026-05-11", None)},
    "spot": {"train": (None, "2026-04-30"), "test": ("2026-05-01", None)},
}


def _run(df, strat, frm, to):
    res = run_backtest(df, strat, frm, to)
    return M.compute(res["trades"], res["sessions"]), res["trades"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("--data", default="fut", choices=["fut", "spot"])
    ap.add_argument("--split", action="store_true")
    ap.add_argument("--trades", action="store_true", help="dump individual trades")
    args = ap.parse_args()

    mod = importlib.import_module(args.module)
    strat = mod.STRATEGY
    df = load_bars(args.data)

    out = {"strategy": strat.name, "data": args.data,
           "risk": vars(strat.risk) | {"entry_cutoff": str(strat.risk.entry_cutoff)}}
    full_m, full_trades = _run(df, strat, None, None)
    out["full"] = full_m
    print(f"[{strat.name}] FULL ({args.data}):  {M.summary_line(full_m)}")

    if args.split:
        sp = SPLITS[args.data]
        tr_m, _ = _run(df, strat, *sp["train"])
        te_m, _ = _run(df, strat, *sp["test"])
        out["train"], out["test"] = tr_m, te_m
        print(f"[{strat.name}] TRAIN:         {M.summary_line(tr_m)}")
        print(f"[{strat.name}] TEST  (OOS):   {M.summary_line(te_m)}")

    if args.trades:
        for t in full_trades:
            print(f"  {t.session} {t.side:5} {t.entry_time}->{t.exit_time} "
                  f"{t.entry_px}->{t.exit_px} net={t.net_pts:+.1f}pts "
                  f"₹{t.net_rupees:+.0f} {t.reason}")

    print("JSON " + json.dumps(out))


if __name__ == "__main__":
    main()
