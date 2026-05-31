"""
NIFTY 2.0 — every signal-evaluation attempt logged.
Path: entry_attempts_nifty_2.csv
"""
import csv
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(__file__))
CSV_PATH = os.path.join(_ROOT, "entry_attempts_nifty_2.csv")

FIELDNAMES = [
    "date", "time",
    "model", "signal", "outcome",   # outcome: FIRED | SKIPPED
    "spot", "atm_strike", "option_ltp",
    "vwap", "vwap_dist_pct",
    "rsi14", "body_pct",
    "or_high", "or_low",
    "skip_reasons",
    "reason",
]


def _ensure_header() -> None:
    if not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0:
        with open(CSV_PATH, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()


def log_attempt_n2(
    *,
    when: datetime,
    model: str,
    signal: str,
    outcome: str,
    spot: float,
    atm_strike: int,
    option_ltp: float,
    vwap: float,
    rsi14: float,
    body_pct: float,
    or_high: float,
    or_low: float,
    skip_reasons: list[str],
    reason: str,
) -> None:
    try:
        _ensure_header()
        vwap_dist_pct = ((spot - vwap) / vwap * 100) if vwap > 0 else 0
        row = {
            "date":          when.strftime("%Y-%m-%d"),
            "time":          when.strftime("%H:%M:%S"),
            "model":         model,
            "signal":        signal,
            "outcome":       outcome,
            "spot":          round(spot, 2),
            "atm_strike":    atm_strike,
            "option_ltp":    round(option_ltp, 2),
            "vwap":          round(vwap, 2),
            "vwap_dist_pct": round(vwap_dist_pct, 3),
            "rsi14":         round(rsi14, 2) if rsi14 else "",
            "body_pct":      round(body_pct, 2),
            "or_high":       round(or_high, 2) if or_high else "",
            "or_low":        round(or_low, 2) if or_low else "",
            "skip_reasons":  ";".join(skip_reasons or []),
            "reason":        reason,
        }
        with open(CSV_PATH, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)
    except Exception as e:
        logger.debug("entry_logger_n2 write failed (non-fatal): %s", e)
