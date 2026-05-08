"""
Day-bias classifier — runs once at the close of the 09:45-09:50 candle.

Classifies the trading day as "UP" / "DOWN" / "NEUTRAL" / "NO_TRADE"
based on gap %, opening RSI, VWAP slope, and opening efficiency.

Pure function. No side effects. Returns "NO_TRADE" if data is insufficient.
"""
from __future__ import annotations

import logging
from datetime import time
from typing import Literal, Optional

from services.indicators import compute_efficiency, compute_rsi, compute_vwap
from services.trading_state import Candle

logger = logging.getLogger(__name__)

DayBias = Literal["UP", "DOWN", "NEUTRAL", "NO_TRADE"]


def classify_day_bias(
    candles_today: list[Candle],
    prev_close: float,
    cfg: dict,
) -> DayBias:
    """
    Inputs:
      candles_today: list of today's 5-min candles from 09:15 to 09:50 (≥4 candles).
                     Older candles (yesterday's seed) MUST NOT be included —
                     this function filters by date defensively.
      prev_close:    previous trading day's last 5-min close (underlying spot).
                     Pass 0.0 if unavailable → returns "NO_TRADE".
      cfg:           per-instrument config dict (INSTRUMENT_CONFIG[<INSTRUMENT>]).

    Returns one of "UP" / "DOWN" / "NEUTRAL" / "NO_TRADE".
    """
    if not candles_today or prev_close <= 0:
        return "NO_TRADE"

    # Defensive: keep only the most recent date's candles.
    today_date = candles_today[-1].timestamp.date()
    today = [c for c in candles_today if c.timestamp.date() == today_date]
    if len(today) < 4:
        return "NO_TRADE"

    today_open = today[0].open
    spot_now = today[-1].close

    gap_pct = (today_open - prev_close) / prev_close * 100.0

    # Opening RSI on closes 09:15..09:50 — RSI(14) needs ≥15 closes, so we only
    # have a meaningful opening_rsi if seeds were merged in upstream. With only
    # ~7 closes today, compute_rsi returns None — gracefully degrade.
    closes = [c.close for c in today]
    rsi_series = compute_rsi(closes, period=14)
    opening_rsi: Optional[float] = rsi_series[-1] if rsi_series and rsi_series[-1] is not None else None

    # VWAP slope between 09:25 and 09:50.
    vwap_late = compute_vwap(today)
    early_window = [c for c in today if c.timestamp.time() <= time(9, 25)]
    vwap_early = compute_vwap(early_window) if early_window else vwap_late
    vwap_slope = (1 if vwap_late > vwap_early
                  else -1 if vwap_late < vwap_early
                  else 0)

    opening_efficiency = compute_efficiency(today) if len(today) >= 7 else 0.0

    # Bias-specific keys; intentionally distinct from legacy strategy.py
    # keys (opening_rsi_overbought / opening_rsi_oversold) to avoid collision.
    gap_no_trade = float(cfg.get("bias_gap_pct_no_trade", 999.0))
    rsi_ob = float(cfg.get("bias_opening_rsi_ob", 999.0))
    rsi_os = float(cfg.get("bias_opening_rsi_os", -1.0))
    eff_min = float(cfg.get("bias_opening_efficiency_min", 0.0))
    gap_min = float(cfg.get("gap_pct_min", 0.10))
    bias_rsi_min_up = float(cfg.get("bias_rsi_min_up", 55))
    bias_rsi_max_up = float(cfg.get("bias_rsi_max_up", 73))
    bias_rsi_min_down = float(cfg.get("bias_rsi_min_down", 27))
    bias_rsi_max_down = float(cfg.get("bias_rsi_max_down", 45))

    # ── NO_TRADE checks ────────────────────────────────────────────────
    if abs(gap_pct) >= gap_no_trade:
        logger.info("day_bias=NO_TRADE | gap %.2f%% ≥ no-trade %.2f%%", gap_pct, gap_no_trade)
        return "NO_TRADE"
    if opening_rsi is not None and (opening_rsi >= rsi_ob or opening_rsi <= rsi_os):
        logger.info("day_bias=NO_TRADE | opening RSI %.1f in danger zone", opening_rsi)
        return "NO_TRADE"
    if opening_efficiency > 0 and opening_efficiency < eff_min:
        logger.info(
            "day_bias=NO_TRADE | opening efficiency %.2f < min %.2f",
            opening_efficiency, eff_min,
        )
        return "NO_TRADE"

    # ── UP / DOWN ──────────────────────────────────────────────────────
    rsi_in_up = (
        opening_rsi is not None
        and bias_rsi_min_up <= opening_rsi <= bias_rsi_max_up
    )
    rsi_in_down = (
        opening_rsi is not None
        and bias_rsi_min_down <= opening_rsi <= bias_rsi_max_down
    )

    is_up = (
        gap_pct >= gap_min
        and rsi_in_up
        and vwap_slope > 0
        and spot_now > vwap_late
    )
    is_down = (
        gap_pct <= -gap_min
        and rsi_in_down
        and vwap_slope < 0
        and spot_now < vwap_late
    )

    if is_up:
        logger.info(
            "day_bias=UP | gap=%.2f%% rsi=%s slope=+ spot>VWAP",
            gap_pct, f"{opening_rsi:.1f}" if opening_rsi is not None else "n/a",
        )
        return "UP"
    if is_down:
        logger.info(
            "day_bias=DOWN | gap=%.2f%% rsi=%s slope=- spot<VWAP",
            gap_pct, f"{opening_rsi:.1f}" if opening_rsi is not None else "n/a",
        )
        return "DOWN"

    logger.info(
        "day_bias=NEUTRAL | gap=%.2f%% rsi=%s slope=%d",
        gap_pct, f"{opening_rsi:.1f}" if opening_rsi is not None else "n/a", vwap_slope,
    )
    return "NEUTRAL"
