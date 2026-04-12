"""
Paper trading logger — records every simulated trade to CSV.
No real orders are placed. Entry/exit prices come from live LTP via WebSocket.
Separate CSV files per instrument: paper_trades_nifty.csv, paper_trades_banknifty.csv
"""
import csv
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_ROOT = os.path.dirname(os.path.dirname(__file__))

CSV_PATHS = {
    "NIFTY":     os.path.join(_ROOT, "paper_trades_nifty.csv"),
    "BANKNIFTY": os.path.join(_ROOT, "paper_trades_banknifty.csv"),
}
# Backward-compat alias
CSV_PATH = CSV_PATHS["NIFTY"]

FIELDNAMES = [
    "date",
    "trade_number",
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
    # Index context
    "nifty_spot_entry",
    "nifty_spot_exit",
    # Indicator snapshot at entry
    "vwap_entry",
    "ema20_entry",
    "rsi14_entry",
    "market_state_entry",
    "efficiency_entry",
    # Signal & risk metadata
    "reason_for_entry",
    "reason_for_exit",
    "trailing_sl_used",
    "breakeven_set",
]


def _get_csv_path(instrument: str = "NIFTY") -> str:
    return CSV_PATHS.get(instrument.upper(), CSV_PATHS["NIFTY"])


def _ensure_header(path: str) -> None:
    """Write CSV header if file doesn't exist or is empty."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
        logger.info("Created paper trades CSV at %s", path)


def log_trade(
    trade_number: int,
    option_symbol: str,
    option_type: str,
    strike: int,
    expiry,
    entry_time: datetime,
    entry_price: float,
    exit_time: datetime,
    exit_price: float,
    qty: int,
    reason_for_entry: str,
    reason_for_exit: str,
    trailing_sl_used: bool = False,
    breakeven_set: bool = False,
    # Indicator snapshot fields (all optional for backward compat)
    nifty_spot_entry: float = 0.0,
    nifty_spot_exit: float = 0.0,
    vwap_entry: float = 0.0,
    ema20_entry: float = 0.0,
    rsi14_entry: float = 0.0,
    market_state_entry: str = "",
    efficiency_entry: float = 0.0,
    instrument: str = "NIFTY",
) -> None:
    """Append one completed trade record to the instrument's paper trades CSV."""
    path = _get_csv_path(instrument)
    _ensure_header(path)

    pnl_points = round(exit_price - entry_price, 2)
    pnl_rupees = round(pnl_points * qty, 2)
    pnl_pct    = round(pnl_points / entry_price * 100, 2) if entry_price > 0 else 0

    row = {
        "date":               entry_time.strftime("%Y-%m-%d"),
        "trade_number":       trade_number,
        "option_symbol":      option_symbol,
        "option_type":        option_type,
        "strike":             strike,
        "expiry":             str(expiry),
        "entry_time":         entry_time.strftime("%H:%M:%S"),
        "entry_price":        entry_price,
        "exit_time":          exit_time.strftime("%H:%M:%S"),
        "exit_price":         exit_price,
        "qty":                qty,
        "pnl_points":         pnl_points,
        "pnl_rupees":         pnl_rupees,
        "pnl_pct":            pnl_pct,
        "result":             "WIN" if pnl_points > 0 else "LOSS",
        "nifty_spot_entry":   round(nifty_spot_entry, 2),
        "nifty_spot_exit":    round(nifty_spot_exit, 2),
        "vwap_entry":         round(vwap_entry, 2),
        "ema20_entry":        round(ema20_entry, 2) if ema20_entry else "",
        "rsi14_entry":        round(rsi14_entry, 1) if rsi14_entry else "",
        "market_state_entry": market_state_entry,
        "efficiency_entry":   round(efficiency_entry, 3),
        "reason_for_entry":   reason_for_entry,
        "reason_for_exit":    reason_for_exit,
        "trailing_sl_used":   trailing_sl_used,
        "breakeven_set":      breakeven_set,
    }

    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow(row)

    logger.info(
        "PAPER TRADE LOGGED | %s | %s %s | entry=%.2f exit=%.2f | PnL: ₹%.2f (%.1f%%) | %s",
        instrument, option_symbol, option_type, entry_price, exit_price,
        pnl_rupees, pnl_pct, reason_for_exit
    )


def read_trades(instrument: str = "NIFTY") -> list[dict]:
    """Return all logged paper trades for the given instrument."""
    path = _get_csv_path(instrument)
    if not os.path.exists(path):
        return []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def get_summary(instrument: str = "NIFTY") -> dict:
    """Compute summary statistics from all logged paper trades for an instrument."""
    trades = read_trades(instrument)
    if not trades:
        return {"total_trades": 0, "message": "No paper trades logged yet"}

    total = len(trades)
    pnl_values = []
    for t in trades:
        try:
            pnl_values.append(float(t["pnl_rupees"]))
        except (ValueError, KeyError):
            pass

    wins   = [p for p in pnl_values if p > 0]
    losses = [p for p in pnl_values if p < 0]

    return {
        "total_trades":   total,
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate_pct":   round(len(wins) / total * 100, 1) if total else 0,
        "total_pnl_rs":   round(sum(pnl_values), 2),
        "avg_win_rs":     round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss_rs":    round(sum(losses) / len(losses), 2) if losses else 0,
        "max_win_rs":     round(max(pnl_values), 2) if pnl_values else 0,
        "max_loss_rs":    round(min(pnl_values), 2) if pnl_values else 0,
        "csv_path":       _get_csv_path(instrument),
    }
