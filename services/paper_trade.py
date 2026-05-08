"""
Paper trading logger — records every simulated trade to CSV.
No real orders are placed. Entry/exit prices come from live LTP via WebSocket.
Separate CSV files per instrument: paper_trades_nifty.csv, paper_trades_banknifty.csv

v3 — schema extended with leg_1/2/3 columns to support partial booking
(50%@+7%, 30%@+14%, trail final 20%). The legacy `qty` / `exit_price` /
`pnl_rupees` columns now hold weighted-average values across all legs so
existing readers (dashboard, summary stats) keep working unchanged.

On schema upgrade, an existing CSV with the old 26-column header is rotated
to `paper_trades_<inst>.legacy.csv` and a fresh file with the new schema
is started. Old data remains intact in the .legacy file.
"""
import csv
import logging
import os
from datetime import datetime
from typing import Optional
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

# Schema v3 — leg + v3 metadata columns appended at the tail
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
    # ── v3 schema additions (appended) ───────────────────────────────
    "leg_1_qty", "leg_1_exit_price", "leg_1_exit_time", "leg_1_reason",
    "leg_2_qty", "leg_2_exit_price", "leg_2_exit_time", "leg_2_reason",
    "leg_3_qty", "leg_3_exit_price", "leg_3_exit_time", "leg_3_reason",
    "weighted_avg_exit_price",
    "total_pnl_rupees",
    "day_bias",
    "vix_at_entry",
    "entry_mode",
]

# Legacy schema (pre-v3) — used to detect old files for one-shot rotation
_LEGACY_FIELDNAMES = [
    "date", "trade_number", "option_symbol", "option_type", "strike", "expiry",
    "entry_time", "entry_price", "exit_time", "exit_price", "qty",
    "pnl_points", "pnl_rupees", "pnl_pct", "result",
    "nifty_spot_entry", "nifty_spot_exit",
    "vwap_entry", "ema20_entry", "rsi14_entry",
    "market_state_entry", "efficiency_entry",
    "reason_for_entry", "reason_for_exit",
    "trailing_sl_used", "breakeven_set",
]


def _get_csv_path(instrument: str = "NIFTY") -> str:
    return CSV_PATHS.get(instrument.upper(), CSV_PATHS["NIFTY"])


def _ensure_header(path: str) -> None:
    """
    Write CSV header if file doesn't exist or is empty.
    On schema mismatch (legacy 26-col header), rotate the file to
    `<name>.legacy.csv` and start fresh with the v3 header.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        logger.info("Created paper trades CSV at %s", path)
        return

    # Read header line and compare with v3 schema
    with open(path, "r", newline="") as f:
        first_line = f.readline().strip()
    existing_cols = first_line.split(",") if first_line else []

    missing = [c for c in FIELDNAMES if c not in existing_cols]
    if not missing:
        return  # already on v3 schema (or superset)

    # Schema upgrade — rotate once.
    legacy_path = path.replace(".csv", ".legacy.csv")
    if os.path.exists(legacy_path):
        # Already rotated; the existing file must be a partial v3 — leave alone
        logger.warning(
            "Schema mismatch on %s but %s exists — leaving file unchanged",
            path, legacy_path,
        )
        return
    os.rename(path, legacy_path)
    logger.info(
        "Rotated %s → %s on schema upgrade (missing: %s)",
        path, legacy_path, missing,
    )
    with open(path, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()


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
    nifty_spot_entry: float = 0.0,
    nifty_spot_exit: float = 0.0,
    vwap_entry: float = 0.0,
    ema20_entry: float = 0.0,
    rsi14_entry: float = 0.0,
    market_state_entry: str = "",
    efficiency_entry: float = 0.0,
    instrument: str = "NIFTY",
    # ── v3 additions ────────────────────────────────────────────────
    legs: Optional[list[dict]] = None,
    day_bias: str = "",
    vix_at_entry: float = 0.0,
    entry_mode: str = "",
) -> None:
    """
    Append one completed trade record to the instrument's paper trades CSV.

    `legs` (optional) is a list of leg dicts when the position was exited in
    pieces (partial booking). Each leg dict should have:
      {qty, exit_price, exit_time (datetime), reason}
    When provided, the legacy `qty`, `exit_price`, `exit_time`, `pnl_*` fields
    hold WEIGHTED AVERAGES across all legs so existing readers see consistent
    aggregate values.
    """
    path = _get_csv_path(instrument)
    _ensure_header(path)

    # Compute aggregate exit + leg columns
    leg_cols: dict[str, object] = {f"leg_{i}_qty": "" for i in range(1, 4)}
    leg_cols.update({f"leg_{i}_exit_price": "" for i in range(1, 4)})
    leg_cols.update({f"leg_{i}_exit_time": "" for i in range(1, 4)})
    leg_cols.update({f"leg_{i}_reason": "" for i in range(1, 4)})

    if legs:
        total_qty = sum(int(leg.get("qty", 0)) for leg in legs)
        total_exit_value = sum(
            int(leg.get("qty", 0)) * float(leg.get("exit_price", 0))
            for leg in legs
        )
        weighted_exit = total_exit_value / total_qty if total_qty > 0 else exit_price
        for i, leg in enumerate(legs[:3], start=1):
            leg_cols[f"leg_{i}_qty"] = int(leg.get("qty", 0))
            leg_cols[f"leg_{i}_exit_price"] = round(float(leg.get("exit_price", 0)), 2)
            t = leg.get("exit_time")
            leg_cols[f"leg_{i}_exit_time"] = (
                t.strftime("%H:%M:%S") if isinstance(t, datetime) else str(t or "")
            )
            leg_cols[f"leg_{i}_reason"] = str(leg.get("reason", ""))
        # Legacy aggregate fields use weighted values
        exit_price = weighted_exit
        qty = total_qty

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
        "entry_price":        round(entry_price, 2),
        "exit_time":          exit_time.strftime("%H:%M:%S"),
        "exit_price":         round(exit_price, 2),
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
        # v3 columns
        **leg_cols,
        "weighted_avg_exit_price": round(exit_price, 2) if legs else "",
        "total_pnl_rupees":   pnl_rupees,
        "day_bias":           day_bias,
        "vix_at_entry":       round(vix_at_entry, 2) if vix_at_entry else "",
        "entry_mode":         entry_mode,
    }

    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow(row)

    logger.info(
        "PAPER TRADE LOGGED | %s | %s %s | entry=%.2f exit=%.2f | PnL: ₹%.2f (%.1f%%) | %s%s",
        instrument, option_symbol, option_type, entry_price, exit_price,
        pnl_rupees, pnl_pct, reason_for_exit,
        f" | legs={len(legs)}" if legs else "",
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
