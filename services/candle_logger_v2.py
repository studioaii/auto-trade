"""
BankNifty 2.0 candle logger.

Writes one row per closed 5-min candle to:
  candle_logs/banknifty2_candles_YYYY-MM-DD.csv

Extends the v1 candle log with v2-specific columns:
  • day_class, gap_pct, vwap_drift_pct
  • model_a_active (pending limit live)
  • signal_v2, model_v2
  • position_qty, partial_booked
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
    "body_pct", "vol_ratio",
    "ema_slope_pts", "rsi_delta",
    "candle_direction", "price_momentum_pct",
    "market_state",
    "day_class", "gap_pct", "vwap_drift_pct",
    "model_a_pending", "model_a_trigger", "model_a_side",
    "signal_v2", "model_v2",
    "spot", "atm_strike", "ce_ltp", "pe_ltp",
    "in_position", "position_type", "position_qty",
    "position_entry_price", "position_current_price",
    "partial_booked",
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
                logger.warning("BN2 candle log write failed: %s", e)

    threading.Thread(target=_worker, name="BN2CandleLogWriter", daemon=True).start()


def _log_path(date_str: str) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, f"banknifty2_candles_{date_str}.csv")


def log_candle_v2(
    *,
    candle: Candle,
    indicators: dict,
    state: TradingState,
    atm_strike: Optional[int],
    day_class: str,
    gap_pct: Optional[float],
    vwap_drift_pct: Optional[float],
    model_a_pending: bool,
    model_a_trigger: Optional[float],
    model_a_side: str,
    signal_v2: str,
    model_v2: str,
    position_qty: int,
    partial_booked: bool,
) -> None:
    try:
        from services.strategy_v2 import volume_ratio
        from services.indicators import candle_body_pct

        ts = candle.timestamp
        date_str = ts.strftime("%Y-%m-%d")
        path = _log_path(date_str)
        write_hdr = not os.path.exists(path)

        candles = state.candles
        vwap = indicators.get("vwap") or 0.0
        ema_series = indicators.get("ema20_series") or []
        rsi_series = indicators.get("rsi14_series") or []
        ema_vals = [v for v in ema_series[-6:] if v is not None]
        rsi_vals = [v for v in rsi_series[-2:] if v is not None]
        ema_slope = round(ema_vals[-1] - ema_vals[0], 2) if len(ema_vals) >= 2 else ""
        rsi_delta = round(rsi_vals[-1] - rsi_vals[-2], 2) if len(rsi_vals) >= 2 else ""
        vwap_dist = round((candle.close - vwap) / vwap * 100, 4) if vwap > 0 else ""
        body = round(candle_body_pct(candle), 2)
        vr   = round(volume_ratio(candles), 3) if len(candles) >= 11 else ""
        if candle.close > candle.open:
            direction = 1
        elif candle.close < candle.open:
            direction = -1
        else:
            direction = 0
        momentum = ""
        if len(candles) >= 2 and candles[-2].close > 0:
            momentum = round((candle.close - candles[-2].close) / candles[-2].close * 100, 4)

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
            "vol_ratio":              vr,
            "ema_slope_pts":          ema_slope,
            "rsi_delta":              rsi_delta,
            "candle_direction":       direction,
            "price_momentum_pct":     momentum,
            "market_state":           indicators.get("market_state", ""),
            "day_class":              day_class,
            "gap_pct":                round(gap_pct, 3) if gap_pct is not None else "",
            "vwap_drift_pct":         round(vwap_drift_pct, 3) if vwap_drift_pct is not None else "",
            "model_a_pending":        model_a_pending,
            "model_a_trigger":        round(model_a_trigger, 2) if model_a_trigger else "",
            "model_a_side":           model_a_side,
            "signal_v2":              signal_v2,
            "model_v2":               model_v2,
            "spot":                   round(state.nifty_spot, 2) if state.nifty_spot else "",
            "atm_strike":             atm_strike or "",
            "ce_ltp":                 round(state.ce_ltp, 2) if state.ce_ltp > 0 else "",
            "pe_ltp":                 round(state.pe_ltp, 2) if state.pe_ltp > 0 else "",
            "in_position":            pos is not None,
            "position_type":          pos.option_type if pos else "",
            "position_qty":           position_qty,
            "position_entry_price":   round(pos.entry_price, 2) if pos else "",
            "position_current_price": round(pos.current_price, 2) if pos else "",
            "partial_booked":         partial_booked,
        }
        _ensure_writer_running()
        _write_queue.put((path, write_hdr, row))
    except Exception as e:
        logger.warning("BN2 candle log write failed (non-fatal): %s", e)


def list_log_files_v2() -> list[dict]:
    if not os.path.isdir(LOG_DIR):
        return []
    files = []
    for fname in sorted(os.listdir(LOG_DIR), reverse=True):
        if not fname.startswith("banknifty2_candles_") or not fname.endswith(".csv"):
            continue
        path = os.path.join(LOG_DIR, fname)
        date_str = fname[len("banknifty2_candles_"):-len(".csv")]
        size_kb = round(os.path.getsize(path) / 1024, 1)
        with open(path) as f:
            rows = max(0, sum(1 for _ in f) - 1)
        files.append({"date": date_str, "rows": rows, "size_kb": size_kb, "path": path})
    return files
