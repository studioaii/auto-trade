"""NIFTY Futures — paper-trade CSV logger + summary stats.

Writes paper_trades_nifty_fut.csv at the project root. Futures columns
(direction/points), NOT option columns (strike/expiry/premium).
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "paper_trades_nifty_fut.csv",
)

_FIELDS = [
    "date", "trade_number", "futures_symbol", "direction",
    "entry_time", "entry_price", "exit_time", "exit_price", "qty",
    "pnl_points", "pnl_rupees", "pnl_pct", "result",
    "sl_price", "target_price", "spot_entry", "rsi14_entry",
    "or_high", "or_low", "reason_for_entry", "exit_layer", "reason_for_exit",
]


def log_trade_fut(*, trade_number: int, futures_symbol: str, direction: str,
                  entry_time: datetime, entry_price: float, exit_time: datetime,
                  exit_price: float, qty: int, pnl_points: float, pnl_rupees: float,
                  pnl_pct: float, sl_price: float, target_price: float,
                  spot_entry: float, rsi14_entry: float, or_high: float,
                  or_low: float, reason_for_entry: str, exit_layer: str,
                  reason_for_exit: str) -> None:
    result = "WIN" if pnl_rupees > 0 else ("LOSS" if pnl_rupees < 0 else "FLAT")
    row = {
        "date": entry_time.strftime("%Y-%m-%d"),
        "trade_number": trade_number,
        "futures_symbol": futures_symbol,
        "direction": direction,
        "entry_time": entry_time.strftime("%H:%M:%S"),
        "entry_price": round(entry_price, 2),
        "exit_time": exit_time.strftime("%H:%M:%S"),
        "exit_price": round(exit_price, 2),
        "qty": qty,
        "pnl_points": round(pnl_points, 2),
        "pnl_rupees": round(pnl_rupees, 2),
        "pnl_pct": round(pnl_pct, 2),
        "result": result,
        "sl_price": round(sl_price, 2),
        "target_price": round(target_price, 2),
        "spot_entry": round(spot_entry, 2),
        "rsi14_entry": round(rsi14_entry, 2) if rsi14_entry else 0.0,
        "or_high": round(or_high, 2) if or_high else 0.0,
        "or_low": round(or_low, 2) if or_low else 0.0,
        "reason_for_entry": reason_for_entry,
        "exit_layer": exit_layer,
        "reason_for_exit": reason_for_exit,
    }
    try:
        new_file = not os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS)
            if new_file:
                w.writeheader()
            w.writerow(row)
    except Exception:
        logger.error("Failed to write futures paper trade", exc_info=True)


def read_trades_fut() -> list[dict]:
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, newline="") as f:
        return list(csv.DictReader(f))


def get_summary_fut() -> dict:
    trades = read_trades_fut()
    if not trades:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "net_rupees": 0.0, "net_points": 0.0, "profit_factor": 0.0}
    net_r = sum(float(t["pnl_rupees"]) for t in trades)
    net_p = sum(float(t["pnl_points"]) for t in trades)
    wins = [t for t in trades if float(t["pnl_rupees"]) > 0]
    losses = [t for t in trades if float(t["pnl_rupees"]) < 0]
    gross_win = sum(float(t["pnl_rupees"]) for t in wins)
    gross_loss = -sum(float(t["pnl_rupees"]) for t in losses)
    n = len(trades)
    return {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / n, 1),
        "net_rupees": round(net_r, 2),
        "net_points": round(net_p, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0,
    }
