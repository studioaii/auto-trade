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
    # Vector / delta fields — rate-of-change for momentum analysis
    "ema_delta",               # EMA20 change from previous candle
    "rsi_delta",               # RSI14 change from previous candle
    "volume_ratio",            # actual ratio: current_vol / avg(last 10) — not just boolean
    "efficiency_delta",        # efficiency change from previous candle
    "vwap_dist_delta",         # VWAP distance % change from previous candle
    "candle_direction",        # +1 bullish / -1 bearish / 0 doji
    "price_momentum_pct",      # % price change from previous candle close
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
            multi_candle_confirmation, compute_efficiency,
        )

        ts       = candle.timestamp
        date_str = ts.strftime("%Y-%m-%d")
        path     = _log_path(date_str, instrument)
        write_hdr = not os.path.exists(path)

        candles       = state.candles
        vwap          = indicators.get("vwap") or 0.0
        ema20_series  = indicators.get("ema20_series") or []
        rsi14_series  = indicators.get("rsi14_series") or []

        # ── sub-condition values ──────────────────────────────────────
        body_pct  = round(candle_body_pct(candle), 2)
        spike     = is_spike_candle(candle)
        ema_up    = ema_slope_strong_up(ema20_series)   if ema20_series else False
        ema_down  = ema_slope_strong_down(ema20_series) if ema20_series else False
        m_bull    = multi_candle_confirmation(candles, "bullish") if len(candles) >= 3 else False
        m_bear    = multi_candle_confirmation(candles, "bearish") if len(candles) >= 3 else False

        # Efficiency ratio over last 10 candles
        eff = indicators.get("efficiency_ratio", "")

        # VWAP distance %
        vwap_dist = ""
        if vwap > 0 and candle.close > 0:
            vwap_dist = round((candle.close - vwap) / vwap * 100, 4)

        # ── vector / delta fields ─────────────────────────────────────

        # EMA delta: change in EMA20 from previous candle
        ema_delta = ""
        if ema20_series:
            ema_vals = [v for v in ema20_series if v is not None]
            if len(ema_vals) >= 2:
                ema_delta = round(ema_vals[-1] - ema_vals[-2], 2)

        # RSI delta: change in RSI14 from previous candle
        rsi_delta = ""
        if rsi14_series:
            rsi_vals = [v for v in rsi14_series if v is not None]
            if len(rsi_vals) >= 2:
                rsi_delta = round(rsi_vals[-1] - rsi_vals[-2], 2)

        # Volume ratio: actual ratio (current vol / avg of last 10), not just True/False
        volume_ratio = ""
        if len(candles) >= 11:
            recent_vols = [c.volume for c in candles[-11:-1]]
            avg_vol = sum(recent_vols) / len(recent_vols)
            if avg_vol > 0 and not all(v == recent_vols[0] for v in recent_vols):
                volume_ratio = round(candles[-1].volume / avg_vol, 3)

        # Efficiency delta: change in efficiency from previous candle
        efficiency_delta = ""
        if len(candles) >= 11:
            eff_prev = compute_efficiency(candles[:-1])
            eff_curr = indicators.get("efficiency_ratio")
            if eff_curr != "" and eff_curr is not None:
                efficiency_delta = round(float(eff_curr) - eff_prev, 4)

        # VWAP distance delta: change in VWAP distance % from previous candle
        # Uses same VWAP (cumulative intraday) as reference for both candles
        vwap_dist_delta = ""
        if vwap > 0 and len(candles) >= 2:
            prev_dist = (candles[-2].close - vwap) / vwap * 100
            curr_dist = (candle.close - vwap) / vwap * 100
            vwap_dist_delta = round(curr_dist - prev_dist, 4)

        # Candle direction: +1 bullish / -1 bearish / 0 doji
        if candle.close > candle.open:
            candle_direction = 1
        elif candle.close < candle.open:
            candle_direction = -1
        else:
            candle_direction = 0

        # Price momentum %: close % change from previous candle
        price_momentum_pct = ""
        if len(candles) >= 2 and candles[-2].close > 0:
            price_momentum_pct = round(
                (candle.close - candles[-2].close) / candles[-2].close * 100, 4
            )

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
            "ema_delta":              ema_delta,
            "rsi_delta":              rsi_delta,
            "volume_ratio":           volume_ratio,
            "efficiency_delta":       efficiency_delta,
            "vwap_dist_delta":        vwap_dist_delta,
            "candle_direction":       candle_direction,
            "price_momentum_pct":     price_momentum_pct,
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
