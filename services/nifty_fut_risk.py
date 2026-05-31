"""NIFTY Futures — risk model. Fixed risk-reward in INDEX POINTS (no trailing).

Unlike the option setups (premium %), futures P&L is linear in points:
  LONG  pnl_points = exit - entry
  SHORT pnl_points = entry - exit
  pnl_rupees = pnl_points × qty   (qty = lot size, ₹1/pt per unit)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional


class FutExitLayer(Enum):
    HARD_SL = "HARD_SL"
    TARGET = "TARGET"
    TIME_FORCE = "TIME_FORCE"     # 15:15 intraday force-exit
    MANUAL_STOP = "MANUAL_STOP"


@dataclass
class FutExitDecision:
    should_exit: bool = False
    layer: Optional[FutExitLayer] = None
    reason: str = ""


def initial_levels(entry_price: float, side: str, cfg: dict) -> tuple[float, float]:
    """Return (sl_price, target_price) in index points for a futures entry."""
    sl_pts = float(cfg["sl_points"])
    tgt_pts = float(cfg["target_points"])
    if side == "LONG":
        return entry_price - sl_pts, entry_price + tgt_pts
    return entry_price + sl_pts, entry_price - tgt_pts


def evaluate_exit(side: str, entry_price: float, current_price: float,
                  sl_price: float, target_price: float, cfg: dict,
                  now: datetime) -> FutExitDecision:
    """Fixed risk-reward + EOD force-exit. SL checked before target (conservative)."""
    fe = cfg.get("force_exit_hhmm", (15, 15))
    if now.time() >= time(fe[0], fe[1]):
        return FutExitDecision(True, FutExitLayer.TIME_FORCE,
                               f"force exit {fe[0]:02d}:{fe[1]:02d}")
    if current_price <= 0:
        return FutExitDecision(False)

    if side == "LONG":
        if current_price <= sl_price:
            return FutExitDecision(True, FutExitLayer.HARD_SL,
                                   f"price {current_price:.1f} ≤ SL {sl_price:.1f}")
        if current_price >= target_price:
            return FutExitDecision(True, FutExitLayer.TARGET,
                                   f"price {current_price:.1f} ≥ target {target_price:.1f}")
    else:  # SHORT
        if current_price >= sl_price:
            return FutExitDecision(True, FutExitLayer.HARD_SL,
                                   f"price {current_price:.1f} ≥ SL {sl_price:.1f}")
        if current_price <= target_price:
            return FutExitDecision(True, FutExitLayer.TARGET,
                                   f"price {current_price:.1f} ≤ target {target_price:.1f}")
    return FutExitDecision(False)


def calc_pnl(side: str, entry_price: float, current_price: float, qty: int) -> dict:
    pts = (current_price - entry_price) if side == "LONG" else (entry_price - current_price)
    return {
        "points": round(pts, 2),
        "rupees": round(pts * qty, 2),
        "pct": round(pts / entry_price * 100.0, 2) if entry_price > 0 else 0.0,
    }
