"""
CSV-replay backtest harness.

Replays logged candle data through the production strategy + risk_manager
logic. Produces per-trade and per-day summary CSVs. Optional fidelity
check compares the recomputed signal vs the logged signal column.

Usage (from repo root):
    python -m backtest.replay --instrument NIFTY --start 2026-04-28 --end 2026-04-30
    python -m backtest.replay --instrument BANKNIFTY --start 2026-04-28 --end 2026-04-30 --check-fidelity
    python -m backtest.replay --instrument NIFTY --start 2026-04-30 --end 2026-04-30 --config-override variants/v3.json
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

# Allow running from repo root without installing as a package
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from services.trading_state import Candle  # noqa: E402

from backtest.results import write_summary, write_trades  # noqa: E402
from backtest.simulator import SimEngine, SimResult  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
LOG_DIR = _REPO_ROOT / "candle_logs"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
SEED_LOOKBACK_DAYS = 20

logger = logging.getLogger("backtest")


def _csv_path(instrument: str, day: date) -> Path:
    prefix = "banknifty" if instrument.upper() == "BANKNIFTY" else "nifty"
    return LOG_DIR / f"{prefix}_candles_{day.isoformat()}.csv"


def _read_candles(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def _row_to_candle(row: dict, day: date) -> Candle:
    t = datetime.strptime(row["time"], "%H:%M").time()
    ts = datetime.combine(day, t, tzinfo=IST)
    return Candle(
        timestamp=ts,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(float(row["volume"])),
    )


def _find_seed_csv(instrument: str, day: date) -> Path | None:
    """Find the most recent CSV before `day` (within SEED_LOOKBACK_DAYS)."""
    for offset in range(1, SEED_LOOKBACK_DAYS + 1):
        prior = day - timedelta(days=offset)
        path = _csv_path(instrument, prior)
        if path.exists():
            return path
    return None


def _load_seed_candles(instrument: str, day: date) -> list[Candle]:
    seed_path = _find_seed_csv(instrument, day)
    if seed_path is None:
        logger.info("[%s %s] no seed CSV in last %d days — running cold", instrument, day, SEED_LOOKBACK_DAYS)
        return []
    seed_day = date.fromisoformat(seed_path.stem.split("_")[-1])
    rows = _read_candles(seed_path)
    candles = [_row_to_candle(r, seed_day) for r in rows]
    logger.info("[%s %s] seeded with %d candles from %s", instrument, day, len(candles), seed_day)
    return candles


def _daterange(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def replay_day(
    instrument: str,
    day: date,
    cfg_override: dict | None = None,
) -> SimResult:
    path = _csv_path(instrument, day)
    if not path.exists():
        logger.warning("No candle log for %s on %s — skipping", instrument, day)
        return SimResult(date=day, instrument=instrument, trades=[], skipped=True)

    seed = _load_seed_candles(instrument, day)
    rows = _read_candles(path)
    candles = [_row_to_candle(r, day) for r in rows]

    sim = SimEngine(instrument=instrument, cfg_override=cfg_override)
    if seed:
        sim.seed(seed)
    for candle, row in zip(candles, rows):
        sim.on_candle(candle, row)

    sim.close_at_eod()
    return sim.result(day)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay logged candles through strategy logic")
    parser.add_argument("--instrument", required=True, choices=["NIFTY", "BANKNIFTY"])
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--check-fidelity", action="store_true",
                        help="Verify recomputed signal matches the logged 'signal' column")
    parser.add_argument("--config-override", help="Path to JSON file with cfg overrides")
    parser.add_argument("--out-prefix", default="run", help="Output filename prefix")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg_override = None
    if args.config_override:
        with open(args.config_override) as f:
            cfg_override = json.load(f)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    RESULTS_DIR.mkdir(exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    trades_csv = RESULTS_DIR / f"{args.out_prefix}_{args.instrument}_{run_id}_trades.csv"
    summary_csv = RESULTS_DIR / f"{args.out_prefix}_{args.instrument}_{run_id}_summary.csv"

    all_results: list[SimResult] = []
    for day in _daterange(start, end):
        res = replay_day(args.instrument, day, cfg_override=cfg_override)
        all_results.append(res)

        if not res.skipped:
            mismatches = sum(1 for d in res.signal_diffs if d != 0)
            wins = sum(1 for t in res.trades if t["pnl_rupees"] > 0)
            losses = len(res.trades) - wins
            gross = sum(t["pnl_rupees"] for t in res.trades)
            logger.info(
                "[%s] trades=%d (W:%d L:%d) pnl=%+.2f%s",
                day, len(res.trades), wins, losses, gross,
                f" mismatch={mismatches}/{len(res.signal_diffs)}" if args.check_fidelity else "",
            )

    write_trades(trades_csv, all_results)
    write_summary(summary_csv, all_results)

    total_trades = sum(len(r.trades) for r in all_results)
    total_pnl = sum(t["pnl_rupees"] for r in all_results for t in r.trades)
    total_wins = sum(1 for r in all_results for t in r.trades if t["pnl_rupees"] > 0)
    total_mismatch = sum(1 for r in all_results for d in r.signal_diffs if d != 0)
    total_candles = sum(len(r.signal_diffs) for r in all_results)

    logger.info("─" * 60)
    logger.info(
        "Replay complete | %s days=%d trades=%d wins=%d pnl=%+.2f",
        args.instrument, len(all_results), total_trades, total_wins, total_pnl,
    )
    if args.check_fidelity:
        logger.info(
            "Fidelity: %d/%d candles signal-mismatch (%.1f%% match)",
            total_mismatch, total_candles,
            (1 - total_mismatch / total_candles) * 100 if total_candles else 0,
        )
    logger.info("Trades:  %s", trades_csv)
    logger.info("Summary: %s", summary_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
