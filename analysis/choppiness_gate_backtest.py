#!/usr/bin/env python3
"""
Choppiness-gate backtest harness  (NIFTY 1.0 / BANKNIFTY schema)
================================================================

Purpose
-------
Validate, WITHOUT look-ahead bias, whether skipping entries on "choppy" market
conditions improves the VWAP+EMA breakout strategy. The regime analysis that
motivated this used WHOLE-DAY choppiness (how many VWAP crossings the day had in
total, what % of the day was SIDEWAYS) — that is look-ahead: you can't know it at
entry. This harness recomputes every choppiness metric AS OF THE DECISION CANDLE
(only candles strictly before the entry), so the gate is something you could
actually have traded.

What it does
------------
For each completed trade in paper_trades_<inst>.csv:
  1. Find the day's candle log and the decision candle (last closed candle BEFORE
     entry_time).
  2. From session-so-far candles (09:15 .. decision candle, inclusive) compute:
       - vwap_x_session : # of VWAP crossings since open
       - vwap_x_last5   : # of VWAP crossings in the last 5 candles (mirrors the
                          engine's own SIDEWAYS detector)
       - sideways_share : fraction of session candles flagged market_state==SIDEWAYS
       - market_state   : engine label on the decision candle
  3. Attach the trade's ACTUAL realised pnl (no exit-policy re-sim — isolates the
     gate's effect from the SL-tightening change).
Then it sweeps gate thresholds, reports KEPT-vs-baseline net/WR/PF, lists which
trades each gate removes, and runs leave-one-out + threshold-sensitivity checks.

Reusable: re-run as more paper trades accumulate, or on another instrument, via
  python3 analysis/choppiness_gate_backtest.py --inst nifty
  python3 analysis/choppiness_gate_backtest.py --inst banknifty

NO look-ahead: every gate metric uses only candles with time < entry_time.
"""
from __future__ import annotations
import argparse, glob, os, sys
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_trades(inst: str) -> pd.DataFrame:
    path = os.path.join(REPO, f"paper_trades_{inst}.csv")
    t = pd.read_csv(path)
    t["entry_hhmm"] = t["entry_time"].str[:5]
    t["pnl_pct"] = pd.to_numeric(t["pnl_pct"], errors="coerce")
    t["pnl_rupees"] = pd.to_numeric(t["pnl_rupees"], errors="coerce")
    return t.dropna(subset=["pnl_pct", "pnl_rupees"]).reset_index(drop=True)


def _vwap_crossings(closes, vwaps):
    """# of sign changes of (close - vwap) over the sequence."""
    signs = []
    for c, v in zip(closes, vwaps):
        if v is None or v <= 0:
            continue
        signs.append(1 if c >= v else -1)
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def chop_metrics_at_entry(cday: pd.DataFrame, entry_hhmm: str) -> dict:
    """Metrics from candles STRICTLY BEFORE entry_hhmm (the decision is made on the
    close of the last such candle). Pure as-of-entry — no look-ahead."""
    cday = cday.copy()
    cday["close"] = pd.to_numeric(cday["close"], errors="coerce")
    cday["vwap"] = pd.to_numeric(cday["vwap"], errors="coerce")
    pre = cday[cday["time"] < entry_hhmm].dropna(subset=["close"])
    if len(pre) == 0:
        return dict(vwap_x_session=0, vwap_x_last5=0, sideways_share=0.0,
                    n_candles=0, market_state="UNKNOWN")
    closes = pre["close"].tolist()
    vwaps = pre["vwap"].tolist()
    ms = pre["market_state"].astype(str) if "market_state" in pre else pd.Series(["UNKNOWN"] * len(pre))
    return dict(
        vwap_x_session=_vwap_crossings(closes, vwaps),
        vwap_x_last5=_vwap_crossings(closes[-5:], vwaps[-5:]),
        sideways_share=round((ms == "SIDEWAYS").mean(), 3),
        n_candles=len(pre),
        market_state=str(pre["market_state"].iloc[-1]) if "market_state" in pre else "UNKNOWN",
    )


def attach_metrics(trades: pd.DataFrame, inst: str) -> pd.DataFrame:
    rows = []
    for _, t in trades.iterrows():
        d = t["date"]
        clp = os.path.join(REPO, "candle_logs", f"{inst}_candles_{d}.csv")
        if not os.path.exists(clp):
            print(f"  [warn] no candle log for {inst} {d}; skipping trade", file=sys.stderr)
            continue
        cday = pd.read_csv(clp)
        m = chop_metrics_at_entry(cday, t["entry_hhmm"])
        rows.append({**t.to_dict(), **m})
    return pd.DataFrame(rows)


def evaluate(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return dict(n=0, net=0, wr=0.0, pf=0.0, W=0, L=0)
    wins = (df["pnl_rupees"] > 0).sum()   # same field as net/PF so WR can't disagree with them
    net = df["pnl_rupees"].sum()
    gp = df.loc[df["pnl_rupees"] > 0, "pnl_rupees"].sum()
    gl = -df.loc[df["pnl_rupees"] < 0, "pnl_rupees"].sum()
    pf = (gp / gl) if gl > 0 else float("inf")
    return dict(n=n, net=round(net), wr=round(wins / n * 100, 1),
                pf=round(pf, 2), W=int(wins), L=int(n - wins))


def gate_predicates():
    """name -> (predicate keeping a trade i.e. NOT choppy). Each uses as-of-entry metrics."""
    return {
        "baseline (no gate)":            lambda r: True,
        "skip vwap_x_session>=5":        lambda r: r["vwap_x_session"] < 5,
        "skip vwap_x_session>=6":        lambda r: r["vwap_x_session"] < 6,
        "skip vwap_x_session>=7":        lambda r: r["vwap_x_session"] < 7,
        "skip vwap_x_last5>=3":          lambda r: r["vwap_x_last5"] < 3,
        "skip vwap_x_last5>=2":          lambda r: r["vwap_x_last5"] < 2,
        "skip sideways_share>=0.4":      lambda r: r["sideways_share"] < 0.4,
        "skip sideways_share>=0.5":      lambda r: r["sideways_share"] < 0.5,
        "skip market_state==SIDEWAYS":   lambda r: r["market_state"] != "SIDEWAYS",
    }


def run(inst: str):
    trades = load_trades(inst)
    df = attach_metrics(trades, inst)
    print(f"\n===== Choppiness-gate backtest — {inst.upper()} =====")
    print(f"{len(df)} trades with reconstructable as-of-entry metrics "
          f"(of {len(trades)} in paper_trades_{inst}.csv)\n")

    # per-trade table
    cols = ["date", "option_type", "entry_hhmm", "pnl_pct", "result",
            "vwap_x_session", "vwap_x_last5", "sideways_share", "market_state"]
    print(df[cols].to_string(index=False))

    # gate sweep
    print(f"\n{'gate':32} | {'net':>7} {'WR%':>6} {'W/L':>6} {'PF':>5} {'kept':>5} {'removed':>8}")
    print("-" * 90)
    base = evaluate(df)
    preds = gate_predicates()
    results = {}
    for name, keep in preds.items():
        kept = df[df.apply(keep, axis=1)]
        m = evaluate(kept)
        removed = len(df) - m["n"]
        rem_net = df[~df.apply(keep, axis=1)]["pnl_rupees"].sum()
        results[name] = m
        tag = ""
        if name != "baseline (no gate)":
            tag = f"  Δnet {m['net']-base['net']:+}"
        print(f"{name:32} | {m['net']:>7} {m['wr']:>6} "
              f"{str(m['W'])+'/'+str(m['L']):>6} {m['pf']:>5} {m['n']:>5} "
              f"{str(removed)+' (₹'+str(round(rem_net))+')':>8}{tag}")

    # leave-one-out stability for the headline gate (sideways_share>=0.5)
    print("\nLeave-one-out stability (gate: skip sideways_share>=0.5):")
    keep = preds["skip sideways_share>=0.5"]
    loo_nets, loo_wrs = [], []
    for i in range(len(df)):
        sub = df.drop(df.index[i])
        m = evaluate(sub[sub.apply(keep, axis=1)])
        loo_nets.append(m["net"]); loo_wrs.append(m["wr"])
    if loo_nets:
        print(f"  net range [{min(loo_nets)}, {max(loo_nets)}], "
              f"WR range [{min(loo_wrs)}, {max(loo_wrs)}]  (n={len(df)} trades, each dropped once)")
    return df, results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--inst", default="nifty", help="instrument prefix (nifty, banknifty)")
    args = ap.parse_args()
    run(args.inst)
