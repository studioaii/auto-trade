"""Performance metrics for a list of engine.Trade objects."""
from __future__ import annotations

import numpy as np


def compute(trades, sessions: int) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "net_rupees": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
                "max_drawdown": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0,
                "sharpe": 0.0, "net_pts": 0.0, "sessions": sessions, "wins": 0, "losses": 0,
                "gross_pts": 0.0, "avg_bars_held": 0.0, "max_win": 0.0, "max_loss": 0.0,
                "return_per_session": 0.0}
    pnl = np.array([t.net_rupees for t in trades], float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak)
    max_dd = float(dd.min()) if len(dd) else 0.0
    # per-session returns for Sharpe (daily)
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t.session] = by_day.get(t.session, 0.0) + t.net_rupees
    daily = np.array(list(by_day.values()), float)
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0
    return {
        "trades": n,
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl < 0).sum()),
        "win_rate": round(100.0 * (pnl > 0).sum() / n, 1),
        "net_rupees": round(float(pnl.sum()), 0),
        "net_pts": round(float(sum(t.net_pts for t in trades)), 1),
        "gross_pts": round(float(sum(t.gross_pts for t in trades)), 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "max_drawdown": round(max_dd, 0),
        "avg_win": round(float(wins.mean()), 0) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 0) if len(losses) else 0.0,
        "max_win": round(float(wins.max()), 0) if len(wins) else 0.0,
        "max_loss": round(float(losses.min()), 0) if len(losses) else 0.0,
        "expectancy": round(float(pnl.mean()), 0),
        "sharpe": round(sharpe, 2),
        "avg_bars_held": round(float(np.mean([t.bars_held for t in trades])), 1),
        "sessions": sessions,
        "return_per_session": round(float(pnl.sum()) / max(sessions, 1), 0),
    }


def summary_line(m: dict) -> str:
    return (f"trades={m['trades']:>3} win%={m['win_rate']:>5} "
            f"PF={m['profit_factor']:>5} net=₹{m['net_rupees']:>9,.0f} "
            f"maxDD=₹{m['max_drawdown']:>9,.0f} sharpe={m['sharpe']:>5} "
            f"exp=₹{m['expectancy']:>6,.0f}")
