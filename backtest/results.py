"""CSV writers for backtest results."""
import csv
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from backtest.simulator import SimResult


TRADE_COLUMNS = [
    "date", "instrument", "trade_number",
    "option_symbol", "option_type", "strike",
    "entry_time", "entry_price",
    "exit_time", "exit_price",
    "qty", "pnl_points", "pnl_rupees", "pnl_pct", "result",
    "reason_for_entry", "reason_for_exit",
    "nifty_spot_entry", "vwap_entry", "ema20_entry", "rsi14_entry",
    "market_state_entry", "efficiency_entry", "trail_active",
]


SUMMARY_COLUMNS = [
    "date", "instrument", "trades", "wins", "losses",
    "gross_pnl", "win_rate_pct",
    "signal_mismatch", "candles_replayed",
    "opening_rsi",
]


def write_trades(path: Path, results: "Iterable[SimResult]") -> None:
    rows: list[dict] = []
    for r in results:
        rows.extend(r.trades)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_summary(path: Path, results: "Iterable[SimResult]") -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        w.writeheader()
        for r in results:
            if r.skipped:
                w.writerow({
                    "date": r.date.isoformat(),
                    "instrument": r.instrument,
                    "trades": "",
                    "wins": "",
                    "losses": "",
                    "gross_pnl": "",
                    "win_rate_pct": "",
                    "signal_mismatch": "",
                    "candles_replayed": 0,
                    "opening_rsi": "",
                })
                continue
            wins = sum(1 for t in r.trades if t["pnl_rupees"] > 0)
            losses = len(r.trades) - wins
            gross = sum(t["pnl_rupees"] for t in r.trades)
            win_rate = (wins / len(r.trades) * 100) if r.trades else 0.0
            mismatch = sum(1 for d in r.signal_diffs if d != 0)
            w.writerow({
                "date": r.date.isoformat(),
                "instrument": r.instrument,
                "trades": len(r.trades),
                "wins": wins,
                "losses": losses,
                "gross_pnl": round(gross, 2),
                "win_rate_pct": round(win_rate, 1),
                "signal_mismatch": mismatch,
                "candles_replayed": len(r.signal_diffs),
                "opening_rsi": round(r.opening_rsi, 2) if r.opening_rsi is not None else "",
            })
