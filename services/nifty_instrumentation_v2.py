"""
NIFTY 2.0 — instrumentation logs.

Two CSVs that exist to make the forward-test DECIDABLE (the June-2026 analysis
could not answer these from the existing logs):

  1. post_exit_paths_nifty_2.csv  — for every closed trade, the option's
     running max/min for N candles AFTER we exited. Answers "did we exit too
     early / too late?" (esp. for STOPLOSS / OPPOSITE_SIGNAL exits that may
     have recovered).

  2. shadow_signals_nifty_2.csv   — for every genuine breakout signal a GATE
     blocked (morning wait, regime gate, second-after-SL, max-trades), the
     would-be entry price and the would-be P&L had it been held to the 15:20
     force-exit. Answers "are the new gates blocking winners?".

Pure CSV appenders — no engine state.
"""
import csv
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(__file__))
POST_EXIT_CSV = os.path.join(_ROOT, "post_exit_paths_nifty_2.csv")
SHADOW_CSV    = os.path.join(_ROOT, "shadow_signals_nifty_2.csv")


# ---------------------------------------------------------------------------
# 1. Post-exit option path
# ---------------------------------------------------------------------------

POST_EXIT_FIELDS = [
    "date", "trade_number", "option_symbol", "option_type", "strike",
    "exit_time", "exit_reason", "entry_price", "exit_price",
    "candles_tracked", "post_max_ltp", "post_min_ltp",
    "post_max_vs_exit_pct",   # >0  ⇒ ran higher after we exited (exited early)
    "post_min_vs_exit_pct",   # <0  ⇒ dropped further after we exited (exited well)
    "post_max_vs_entry_pct",  # best total move vs our entry, had we held
]


def log_post_exit_n2(
    *,
    trade_number: int,
    option_symbol: str,
    option_type: str,
    strike: int,
    exit_time: datetime,
    exit_reason: str,
    entry_price: float,
    exit_price: float,
    candles_tracked: int,
    post_max_ltp: float,
    post_min_ltp: float,
) -> None:
    try:
        new = not os.path.exists(POST_EXIT_CSV) or os.path.getsize(POST_EXIT_CSV) == 0
        def pct(a, b):
            return round((a - b) / b * 100.0, 2) if b > 0 else ""
        row = {
            "date":                 exit_time.strftime("%Y-%m-%d"),
            "trade_number":         trade_number,
            "option_symbol":        option_symbol,
            "option_type":          option_type,
            "strike":               strike,
            "exit_time":            exit_time.strftime("%H:%M:%S"),
            "exit_reason":          exit_reason,
            "entry_price":          round(entry_price, 2),
            "exit_price":           round(exit_price, 2),
            "candles_tracked":      candles_tracked,
            "post_max_ltp":         round(post_max_ltp, 2),
            "post_min_ltp":         round(post_min_ltp, 2),
            "post_max_vs_exit_pct": pct(post_max_ltp, exit_price),
            "post_min_vs_exit_pct": pct(post_min_ltp, exit_price),
            "post_max_vs_entry_pct": pct(post_max_ltp, entry_price),
        }
        with open(POST_EXIT_CSV, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=POST_EXIT_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
    except Exception as e:
        logger.debug("N2 post-exit log failed (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# 2. Shadow (blocked) signal — would-be outcome
# ---------------------------------------------------------------------------

SHADOW_FIELDS = [
    "date", "time", "signal", "block_reason",
    "option_symbol", "option_type", "strike",
    "spot", "vwap", "rsi14",
    "wouldbe_entry_price", "force_exit_time", "force_exit_ltp",
    "path_max_ltp", "path_min_ltp",
    "wouldbe_pnl_pct",   # held to 15:20
    "wouldbe_mfe_pct",   # best the option reached after the blocked signal
    "wouldbe_mae_pct",   # worst it reached
    "result",            # WIN/LOSS if held to 15:20
]


def log_shadow_signal_n2(
    *,
    signal_time: datetime,
    signal: str,
    block_reason: str,
    option_symbol: str,
    option_type: str,
    strike: int,
    spot: float,
    vwap: float,
    rsi14: float,
    wouldbe_entry_price: float,
    force_exit_time: datetime,
    force_exit_ltp: float,
    path_max_ltp: float,
    path_min_ltp: float,
) -> None:
    try:
        new = not os.path.exists(SHADOW_CSV) or os.path.getsize(SHADOW_CSV) == 0
        e = wouldbe_entry_price
        def pct(a):
            return round((a - e) / e * 100.0, 2) if e > 0 else ""
        pnl_pct = pct(force_exit_ltp) if force_exit_ltp > 0 else ""
        row = {
            "date":                signal_time.strftime("%Y-%m-%d"),
            "time":                signal_time.strftime("%H:%M:%S"),
            "signal":              signal,
            "block_reason":        block_reason,
            "option_symbol":       option_symbol,
            "option_type":         option_type,
            "strike":              strike,
            "spot":                round(spot, 2),
            "vwap":                round(vwap, 2),
            "rsi14":               round(rsi14, 2) if rsi14 else "",
            "wouldbe_entry_price": round(wouldbe_entry_price, 2),
            "force_exit_time":     force_exit_time.strftime("%H:%M:%S"),
            "force_exit_ltp":      round(force_exit_ltp, 2),
            "path_max_ltp":        round(path_max_ltp, 2),
            "path_min_ltp":        round(path_min_ltp, 2),
            "wouldbe_pnl_pct":     pnl_pct,
            "wouldbe_mfe_pct":     pct(path_max_ltp),
            "wouldbe_mae_pct":     pct(path_min_ltp),
            "result":              ("" if pnl_pct == "" else ("WIN" if pnl_pct > 0 else "LOSS")),
        }
        with open(SHADOW_CSV, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SHADOW_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
    except Exception as ex:
        logger.debug("N2 shadow-signal log failed (non-fatal): %s", ex)
