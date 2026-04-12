"""
Candle data logger.
Writes one row per 5-min candle to candle_logs/candles_YYYY-MM-DD.csv.
Captures OHLCV + every indicator the strategy uses, so you can replay
exactly what the engine saw at each candle and analyse why it entered,
skipped, or did nothing.
"""
import csv
import os
import logging
from typing import Optional
from services.trading_state import Candle, TradingState

logger = logging.getLogger(__name__)

LOG_DIR = "candle_logs"

HEADERS = [
    # Candle identity
    "date", "time",
    # OHLCV
    "open", "high", "low", "close", "volume",
    # Core indicators
    "vwap", "ema20", "rsi14",
    # Derived indicator values (every sub-condition the strategy checks)
    "vwap_distance_pct",       # (close - vwap) / vwap * 100
    "ema_slope_strong_up",     # EMA rose ≥8 pts in last 5 values
    "ema_slope_strong_down",   # EMA fell ≥8 pts in last 5 values
    "volume_surge",            # current vol ≥ 1.2× avg of prior 10
    "body_pct",                # candle body / (high - low) * 100
    "is_spike",                # range > 1% of close
    "multi_candle_bullish",    # 2 of last 3 candles close > open
    "multi_candle_bearish",    # 2 of last 3 candles close < open
    "efficiency_ratio",        # |net_close_move| / (max_high - min_low) over last 10
    # Market / signal state
    "market_state",            # TRENDING | SIDEWAYS | UNKNOWN
    "enough_data",             # True once ≥22 candles available
    "signal",                  # NO_SIGNAL | BUY_CE | BUY_PE
    # Live market snapshot at candle close
    "nifty_spot",
    "atm_strike",
    "ce_ltp",
    "pe_ltp",
    # Position snapshot
    "in_position",
    "position_type",           # CE | PE | ""
    "position_entry_price",
    "position_current_price",
]


def _log_path(date_str: str, instrument: str = "NIFTY") -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    prefix = "banknifty" if instrument.upper() == "BANKNIFTY" else "nifty"
    return os.path.join(LOG_DIR, f"{prefix}_candles_{date_str}.csv")


def log_candle(
    candle: Candle,
    indicators: dict,
    signal_value: str,
    state: TradingState,
    atm_strike: Optional[int],
    instrument: str = "NIFTY",
) -> None:
    """
    Append one row to today's candle CSV.
    Non-fatal — any error is logged and swallowed so it never affects trading.
    """
    try:
        from services.indicators import (
            candle_body_pct, is_spike_candle,
            ema_slope_strong_up, ema_slope_strong_down,
            multi_candle_confirmation,
        )

        ts       = candle.timestamp
        date_str = ts.strftime("%Y-%m-%d")
        path     = _log_path(date_str, instrument)
        write_hdr = not os.path.exists(path)

        candles       = state.candles
        vwap          = indicators.get("vwap") or 0.0
        ema20_series  = indicators.get("ema20_series") or []

        # ── sub-condition values ──────────────────────────────────────
        body_pct  = round(candle_body_pct(candle), 2)
        spike     = is_spike_candle(candle)
        ema_up    = ema_slope_strong_up(ema20_series)   if ema20_series else False
        ema_down  = ema_slope_strong_down(ema20_series) if ema20_series else False
        m_bull    = multi_candle_confirmation(candles, "bullish") if len(candles) >= 3 else False
        m_bear    = multi_candle_confirmation(candles, "bearish") if len(candles) >= 3 else False

        # Efficiency ratio over last 10 candles
        eff = ""
        if len(candles) >= 10:
            recent = candles[-10:]
            rng    = max(c.high for c in recent) - min(c.low for c in recent)
            if rng > 0:
                eff = round(abs(recent[-1].close - recent[0].close) / rng, 4)

        # VWAP distance %
        vwap_dist = ""
        if vwap > 0 and candle.close > 0:
            vwap_dist = round((candle.close - vwap) / vwap * 100, 4)

        # Position snapshot
        pos = state.position
        row = {
            "date":                   date_str,
            "time":                   ts.strftime("%H:%M"),
            "open":                   candle.open,
            "high":                   candle.high,
            "low":                    candle.low,
            "close":                  candle.close,
            "volume":                 candle.volume,
            "vwap":                   indicators.get("vwap") or "",
            "ema20":                  round(indicators["ema20"], 2) if indicators.get("ema20") else "",
            "rsi14":                  round(indicators["rsi14"], 2) if indicators.get("rsi14") else "",
            "vwap_distance_pct":      vwap_dist,
            "ema_slope_strong_up":    ema_up,
            "ema_slope_strong_down":  ema_down,
            "volume_surge":           indicators.get("volume_surge", ""),
            "body_pct":               body_pct,
            "is_spike":               spike,
            "multi_candle_bullish":   m_bull,
            "multi_candle_bearish":   m_bear,
            "efficiency_ratio":       eff,
            "market_state":           indicators.get("market_state", ""),
            "enough_data":            indicators.get("enough_data", False),
            "signal":                 signal_value,
            "nifty_spot":             round(state.nifty_spot, 2) if state.nifty_spot else "",
            "atm_strike":             atm_strike or "",
            "ce_ltp":                 round(state.ce_ltp, 2) if state.ce_ltp > 0 else "",
            "pe_ltp":                 round(state.pe_ltp, 2) if state.pe_ltp > 0 else "",
            "in_position":            pos is not None,
            "position_type":          pos.option_type if pos else "",
            "position_entry_price":   pos.entry_price if pos else "",
            "position_current_price": round(pos.current_price, 2) if pos else "",
        }

        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HEADERS)
            if write_hdr:
                w.writeheader()
            w.writerow(row)

    except Exception as e:
        logger.warning("Candle log write failed (non-fatal): %s", e)


def list_log_files(instrument: str = "") -> list[dict]:
    """Return metadata for all available candle log CSVs, optionally filtered by instrument."""
    if not os.path.isdir(LOG_DIR):
        return []
    files = []
    for fname in sorted(os.listdir(LOG_DIR), reverse=True):
        if not fname.endswith(".csv"):
            continue
        path = os.path.join(LOG_DIR, fname)
        if fname.startswith("nifty_candles_"):
            inst = "NIFTY"
            date_str = fname[len("nifty_candles_"):-len(".csv")]
        elif fname.startswith("banknifty_candles_"):
            inst = "BANKNIFTY"
            date_str = fname[len("banknifty_candles_"):-len(".csv")]
        elif fname.startswith("candles_"):   # old format — treat as NIFTY
            inst = "NIFTY"
            date_str = fname[len("candles_"):-len(".csv")]
        else:
            continue
        if instrument and inst != instrument.upper():
            continue
        size_kb = round(os.path.getsize(path) / 1024, 1)
        with open(path) as f:
            rows = max(0, sum(1 for _ in f) - 1)
        files.append({"date": date_str, "instrument": inst, "rows": rows, "size_kb": size_kb, "path": path})
    return files
