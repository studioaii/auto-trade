"""
NIFTY 2.0 paper-trade logger → paper_trades_nifty_2.csv.
"""
import csv
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(__file__))
CSV_PATH = os.path.join(_ROOT, "paper_trades_nifty_2.csv")

FIELDNAMES = [
    "date",
    "trade_number",
    "model",
    "option_symbol",
    "option_type",
    "strike",
    "expiry",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "qty",
    "pnl_points",
    "pnl_rupees",
    "pnl_pct",
    "result",
    "mfe_pct",
    "spot_entry",
    "spot_exit",
    "vwap_entry",
    "ema20_entry",
    "rsi14_entry",
    "hard_sl_premium",
    "sl_pct",
    "reason_for_entry",
    "exit_layer",
    "reason_for_exit",
    "breakeven_set",
    "trail_active",
]


def _ensure_header() -> None:
    if not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0:
        with open(CSV_PATH, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        logger.info("Created NIFTY 2.0 paper-trade CSV: %s", CSV_PATH)


def log_trade_n2(
    *,
    trade_number: int,
    model: str,
    option_symbol: str,
    option_type: str,
    strike: int,
    expiry,
    entry_time: datetime,
    entry_price: float,
    exit_time: datetime,
    exit_price: float,
    qty: int,
    spot_entry: float,
    spot_exit: float,
    vwap_entry: float,
    ema20_entry: float,
    rsi14_entry: float,
    hard_sl_premium: float,
    sl_pct: float,
    mfe_pct: float,
    reason_for_entry: str,
    exit_layer: str,
    reason_for_exit: str,
    breakeven_set: bool,
    trail_active: bool,
) -> None:
    _ensure_header()
    pnl_pts = round(exit_price - entry_price, 2)
    pnl_rs  = round(pnl_pts * qty, 2)
    pnl_pct = round(pnl_pts / entry_price * 100, 2) if entry_price > 0 else 0.0
    row = {
        "date":              entry_time.strftime("%Y-%m-%d"),
        "trade_number":      trade_number,
        "model":             model,
        "option_symbol":     option_symbol,
        "option_type":       option_type,
        "strike":            strike,
        "expiry":            str(expiry),
        "entry_time":        entry_time.strftime("%H:%M:%S"),
        "entry_price":       round(entry_price, 2),
        "exit_time":         exit_time.strftime("%H:%M:%S"),
        "exit_price":        round(exit_price, 2),
        "qty":               qty,
        "pnl_points":        pnl_pts,
        "pnl_rupees":        pnl_rs,
        "pnl_pct":           pnl_pct,
        "result":            "WIN" if pnl_pts > 0 else "LOSS",
        "mfe_pct":           round(mfe_pct, 2),
        "spot_entry":        round(spot_entry, 2),
        "spot_exit":         round(spot_exit, 2),
        "vwap_entry":        round(vwap_entry, 2),
        "ema20_entry":       round(ema20_entry, 2) if ema20_entry else "",
        "rsi14_entry":       round(rsi14_entry, 1) if rsi14_entry else "",
        "hard_sl_premium":   round(hard_sl_premium, 2),
        "sl_pct":            round(sl_pct, 2),
        "reason_for_entry":  reason_for_entry,
        "exit_layer":        exit_layer,
        "reason_for_exit":   reason_for_exit,
        "breakeven_set":     breakeven_set,
        "trail_active":      trail_active,
    }
    with open(CSV_PATH, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)
    logger.info(
        "N2 TRADE LOGGED | %s | model=%s %s %s | %.2f→%.2f | ₹%.2f (%.1f%%) | %s",
        option_symbol, model, option_type, strike,
        entry_price, exit_price, pnl_rs, pnl_pct, exit_layer,
    )


def read_trades_n2() -> list[dict]:
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, "r", newline="") as f:
        return list(csv.DictReader(f))


def get_summary_n2() -> dict:
    trades = read_trades_n2()
    if not trades:
        return {"total_trades": 0, "message": "No NIFTY 2.0 paper trades logged yet"}
    pnl_vals = []
    for t in trades:
        try:
            pnl_vals.append(float(t.get("pnl_rupees") or 0))
        except ValueError:
            pass
    wins = [p for p in pnl_vals if p > 0]
    losses = [p for p in pnl_vals if p < 0]
    total = len(trades)
    profit = sum(wins)
    loss = abs(sum(losses)) if losses else 0
    pf = round(profit / loss, 2) if loss > 0 else None
    return {
        "total_trades":   total,
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate_pct":   round(len(wins) / total * 100, 1) if total else 0,
        "total_pnl_rs":   round(sum(pnl_vals), 2),
        "avg_win_rs":     round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss_rs":    round(sum(losses) / len(losses), 2) if losses else 0,
        "max_win_rs":     round(max(pnl_vals), 2) if pnl_vals else 0,
        "max_loss_rs":    round(min(pnl_vals), 2) if pnl_vals else 0,
        "profit_factor":  pf,
        "csv_path":       CSV_PATH,
    }
