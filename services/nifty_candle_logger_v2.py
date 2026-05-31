"""
NIFTY 2.0 candle logger.

Writes one row per closed 5-min candle to:
  candle_logs/nifty2_candles_YYYY-MM-DD.csv

One row per closed 5-min candle with v2 columns.
"""
import csv
import os
import queue
import threading
import logging
from typing import Optional

from services.trading_state import Candle, TradingState

logger = logging.getLogger(__name__)

LOG_DIR = "candle_logs"

_write_queue: queue.Queue = queue.Queue()
_writer_started = False
_writer_lock = threading.Lock()


HEADERS = [
    "date", "time",
    "open", "high", "low", "close", "volume",
    "vwap", "ema20", "rsi14",
    "vwap_distance_pct",
    "body_pct",
    "candle_direction",
    "or_high", "or_low", "orb_used",
    "signal_v2", "model_v2", "skip_reason",
    "spot", "atm_strike", "ce_ltp", "pe_ltp",
    "in_position", "position_type",
    "position_entry_price", "position_current_price",
    "trail_active", "breakeven_set",
]


def _ensure_writer_running() -> None:
    global _writer_started
    with _writer_lock:
        if _writer_started:
            return
        _writer_started = True

    def _worker():
        while True:
            task = _write_queue.get()
            if task is None:
                break
            path, write_hdr, row = task
            try:
                with open(path, "a", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=HEADERS)
                    if write_hdr:
                        w.writeheader()
                    w.writerow(row)
            except Exception as e:
                logger.warning("N2 candle log write failed: %s", e)

    threading.Thread(target=_worker, name="N2CandleLogWriter", daemon=True).start()


def _log_path(date_str: str) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, f"nifty2_candles_{date_str}.csv")


def log_candle_n2(
    *,
    candle: Candle,
    indicators: dict,
    state: TradingState,
    atm_strike: Optional[int],
    or_high: Optional[float],
    or_low: Optional[float],
    orb_used: bool,
    signal_v2: str,
    model_v2: str,
    skip_reason: str,
    trail_active: bool,
    breakeven_set: bool,
) -> None:
    try:
        from services.indicators import candle_body_pct
        ts = candle.timestamp
        date_str = ts.strftime("%Y-%m-%d")
        path = _log_path(date_str)
        write_hdr = not os.path.exists(path)

        vwap = indicators.get("vwap") or 0.0
        vwap_dist = round((candle.close - vwap) / vwap * 100, 4) if vwap > 0 else ""
        body = round(candle_body_pct(candle), 2)
        if candle.close > candle.open:
            direction = 1
        elif candle.close < candle.open:
            direction = -1
        else:
            direction = 0

        pos = state.position
        row = {
            "date":                   date_str,
            "time":                   ts.strftime("%H:%M"),
            "open":                   candle.open,
            "high":                   candle.high,
            "low":                    candle.low,
            "close":                  candle.close,
            "volume":                 candle.volume,
            "vwap":                   round(vwap, 2) if vwap else "",
            "ema20":                  round(indicators["ema20"], 2) if indicators.get("ema20") else "",
            "rsi14":                  round(indicators["rsi14"], 2) if indicators.get("rsi14") else "",
            "vwap_distance_pct":      vwap_dist,
            "body_pct":               body,
            "candle_direction":       direction,
            "or_high":                round(or_high, 2) if or_high else "",
            "or_low":                 round(or_low, 2) if or_low else "",
            "orb_used":               orb_used,
            "signal_v2":              signal_v2,
            "model_v2":               model_v2,
            "skip_reason":            skip_reason,
            "spot":                   round(state.nifty_spot, 2) if state.nifty_spot else "",
            "atm_strike":             atm_strike or "",
            "ce_ltp":                 round(state.ce_ltp, 2) if state.ce_ltp > 0 else "",
            "pe_ltp":                 round(state.pe_ltp, 2) if state.pe_ltp > 0 else "",
            "in_position":            pos is not None,
            "position_type":          pos.option_type if pos else "",
            "position_entry_price":   round(pos.entry_price, 2) if pos else "",
            "position_current_price": round(pos.current_price, 2) if pos else "",
            "trail_active":           trail_active,
            "breakeven_set":          breakeven_set,
        }
        _ensure_writer_running()
        _write_queue.put((path, write_hdr, row))
    except Exception as e:
        logger.warning("N2 candle log write failed (non-fatal): %s", e)


def list_log_files_n2() -> list[dict]:
    if not os.path.isdir(LOG_DIR):
        return []
    files = []
    for fname in sorted(os.listdir(LOG_DIR), reverse=True):
        if not fname.startswith("nifty2_candles_") or not fname.endswith(".csv"):
            continue
        path = os.path.join(LOG_DIR, fname)
        date_str = fname[len("nifty2_candles_"):-len(".csv")]
        size_kb = round(os.path.getsize(path) / 1024, 1)
        with open(path) as f:
            rows = max(0, sum(1 for _ in f) - 1)
        files.append({"date": date_str, "rows": rows, "size_kb": size_kb, "path": path})
    return files
