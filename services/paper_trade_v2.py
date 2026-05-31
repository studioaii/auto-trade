"""
BankNifty 2.0 paper-trade logger.

Writes to paper_trades_banknifty_2.csv with extra v2-specific columns:
  • model (A/B/C/D)
  • day_class (TREND/REVERSAL/CHOP/NORMAL)
  • partial_booked, partial_price, partial_qty
  • exit_layer (STRUCTURE_SL / PARTIAL_TARGET / RUNNER_TRAIL / STALL / ...)
  • mfe_pct (max favourable excursion observed)

A v2 trade can produce TWO rows when a partial is taken (PARTIAL row + RUNNER row).
"""
import csv
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_ROOT = os.path.dirname(os.path.dirname(__file__))
CSV_PATH = os.path.join(_ROOT, "paper_trades_banknifty_2.csv")

FIELDNAMES = [
    "date",
    "trade_number",
    "leg",                 # PARTIAL | RUNNER | FULL
    "model",
    "day_class",
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
    # Index context
    "spot_entry",
    "spot_exit",
    # Indicator snapshot
    "vwap_entry",
    "ema20_entry",
    "rsi14_entry",
    # Setup details
    "structure_sl_premium",
    "sl_pct",
    "partial_booked",
    # Reasons
    "reason_for_entry",
    "exit_layer",
    "reason_for_exit",
]


def _ensure_header() -> None:
    if not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0:
        with open(CSV_PATH, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        logger.info("Created BankNifty 2.0 paper-trade CSV: %s", CSV_PATH)


def log_trade_v2(
    *,
    trade_number: int,
    leg: str,
    model: str,
    day_class: str,
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
    structure_sl_premium: float,
    sl_pct: float,
    partial_booked: bool,
    mfe_pct: float,
    reason_for_entry: str,
    exit_layer: str,
    reason_for_exit: str,
) -> None:
    _ensure_header()
    pnl_pts = round(exit_price - entry_price, 2)
    pnl_rs  = round(pnl_pts * qty, 2)
    pnl_pct = round(pnl_pts / entry_price * 100, 2) if entry_price > 0 else 0.0
    row = {
        "date":                entry_time.strftime("%Y-%m-%d"),
        "trade_number":        trade_number,
        "leg":                 leg,
        "model":               model,
        "day_class":           day_class,
        "option_symbol":       option_symbol,
        "option_type":         option_type,
        "strike":              strike,
        "expiry":              str(expiry),
        "entry_time":          entry_time.strftime("%H:%M:%S"),
        "entry_price":         round(entry_price, 2),
        "exit_time":           exit_time.strftime("%H:%M:%S"),
        "exit_price":          round(exit_price, 2),
        "qty":                 qty,
        "pnl_points":          pnl_pts,
        "pnl_rupees":          pnl_rs,
        "pnl_pct":             pnl_pct,
        "result":              "WIN" if pnl_pts > 0 else "LOSS",
        "mfe_pct":             round(mfe_pct, 2),
        "spot_entry":          round(spot_entry, 2),
        "spot_exit":           round(spot_exit, 2),
        "vwap_entry":          round(vwap_entry, 2),
        "ema20_entry":         round(ema20_entry, 2) if ema20_entry else "",
        "rsi14_entry":         round(rsi14_entry, 1) if rsi14_entry else "",
        "structure_sl_premium":round(structure_sl_premium, 2),
        "sl_pct":              round(sl_pct, 2),
        "partial_booked":      partial_booked,
        "reason_for_entry":    reason_for_entry,
        "exit_layer":          exit_layer,
        "reason_for_exit":     reason_for_exit,
    }
    with open(CSV_PATH, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)
    logger.info(
        "V2 TRADE LOGGED | %s | leg=%s model=%s | %s %s | %.2f→%.2f | ₹%.2f (%.1f%%) | %s",
        option_symbol, leg, model, option_type, strike,
        entry_price, exit_price, pnl_rs, pnl_pct, exit_layer,
    )


def read_trades_v2() -> list[dict]:
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, "r", newline="") as f:
        return list(csv.DictReader(f))


def get_summary_v2() -> dict:
    trades = read_trades_v2()
    if not trades:
        return {"total_trades": 0, "message": "No BankNifty 2.0 paper trades logged yet"}

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
    loss   = abs(sum(losses)) if losses else 0
    pf = round(profit / loss, 2) if loss > 0 else None
    return {
        "total_legs":     total,
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
