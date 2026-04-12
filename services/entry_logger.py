"""
Entry attempt logger.
Writes one row to entry_attempts.csv whenever a valid signal fires but entry
is blocked by any gate (max trades, time, stoploss rule, dynamic SL ceiling).

This is the missing link between "signal generated" and "trade entered" —
makes it possible to diagnose why a trade was skipped without reading server
logs line by line.
"""
import csv
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

LOG_PATHS = {
    "NIFTY":     "entry_attempts_nifty.csv",
    "BANKNIFTY": "entry_attempts_banknifty.csv",
}
LOG_PATH = LOG_PATHS["NIFTY"]   # backward-compat alias

HEADERS = [
    "date", "time",
    "signal",
    "nifty_spot", "atm_strike", "option_ltp",
    "vwap_distance_pct", "rsi14", "body_pct",
    "market_state",
    "skip_reason",
    "sl_pct_computed",   # filled when blocked by dynamic SL ceiling
]


def log_entry_attempt(
    signal: str,
    nifty_spot: float,
    atm_strike: int,
    option_ltp: float,
    vwap_distance_pct: float,
    rsi14: float,
    body_pct: float,
    market_state: str,
    skip_reason: str,
    sl_pct_computed: float = 0.0,
    instrument: str = "NIFTY",
) -> None:
    """Append one row. Non-fatal — never raises."""
    try:
        path = LOG_PATHS.get(instrument.upper(), LOG_PATHS["NIFTY"])
        now = datetime.now(IST)
        write_hdr = not os.path.exists(path)
        row = {
            "date":               now.strftime("%Y-%m-%d"),
            "time":               now.strftime("%H:%M"),
            "signal":             signal,
            "nifty_spot":         round(nifty_spot, 2),
            "atm_strike":         atm_strike,
            "option_ltp":         round(option_ltp, 2),
            "vwap_distance_pct":  round(vwap_distance_pct, 4),
            "rsi14":              round(rsi14, 2) if rsi14 else "",
            "body_pct":           round(body_pct, 2) if body_pct else "",
            "market_state":       market_state,
            "skip_reason":        skip_reason,
            "sl_pct_computed":    round(sl_pct_computed, 1) if sl_pct_computed else "",
        }
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HEADERS)
            if write_hdr:
                w.writeheader()
            w.writerow(row)
        logger.info(
            "Entry attempt logged | signal=%s skip=%s sl_pct=%s",
            signal, skip_reason,
            f"{sl_pct_computed:.1f}%" if sl_pct_computed else "n/a",
        )
    except Exception as e:
        logger.warning("Entry attempt log failed (non-fatal): %s", e)
