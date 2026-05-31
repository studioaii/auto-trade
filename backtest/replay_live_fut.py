"""Validate the LIVE futures modules reproduce the backtested ORB.

Drives services/nifty_fut_strategy.evaluate_orb + nifty_fut_risk over the cached
futures candles, mirroring the engine's candle-by-candle flow (continuous RSI via
services.indicators, per-day orb_used reset, intrabar SL-before-target exits,
15:15 force-exit). Reports trades and net P&L to compare against the backtest
(orb_lab eval of the same config: 17 trades, +₹15,490, OOS PF 1.57).
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from services.trading_state import Candle
from services.indicators import get_latest_indicators, MIN_CANDLES
from services.nifty_fut_strategy import evaluate_orb, FutSignal
from services.nifty_fut_risk import initial_levels
from config import INSTRUMENT_CONFIG

IST = ZoneInfo("Asia/Kolkata")
CFG = INSTRUMENT_CONFIG["NIFTY_FUT"]
QTY = 65
COST = 450.0          # match backtest cost model
SLIP = 1.0


def load() -> list[Candle]:
    out = []
    with open(REPO / "backtest/data/nifty_fut_5min.csv") as f:
        for row in csv.DictReader(f):
            ts = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            out.append(Candle(ts, float(row["open"]), float(row["high"]),
                              float(row["low"]), float(row["close"]), int(float(row["volume"]))))
    return out


def main():
    candles = load()
    trades = []
    pos = None
    cur_day = None
    orb_used = False
    i = 0
    n = len(candles)
    while i < n:
        c = candles[i]
        d = c.timestamp.date()
        if d != cur_day:
            cur_day = d
            orb_used = False
            # force-close any held position at prior EOD already handled below
        window = candles[: i + 1]                       # continuous stream (like live)
        ind = get_latest_indicators(window) if len(window) >= MIN_CANDLES else {}
        rsi = ind.get("rsi14") if ind.get("enough_data") else None

        if pos is None and not orb_used:
            setup = evaluate_orb(window, rsi, CFG, c.timestamp)
            if setup.signal != FutSignal.NO_SIGNAL:
                side = setup.signal.value
                entry = c.close + (SLIP if side == "LONG" else -SLIP)
                sl, tgt = initial_levels(c.close, side, CFG)
                pos = dict(side=side, entry=entry, raw_entry=c.close, sl=sl, tgt=tgt,
                           etime=c.timestamp, ehour=c.timestamp)
                orb_used = True
                # simulate exit over subsequent candles (intrabar SL-before-target)
                j = i + 1
                while j < n and candles[j].timestamp.date() == d:
                    b = candles[j]
                    if b.timestamp.time() >= __import__("datetime").time(15, 15):
                        exit_raw = b.open; reason = "TIME_FORCE"; break
                    if side == "LONG":
                        if b.low <= sl: exit_raw = sl; reason = "HARD_SL"; break
                        if b.high >= tgt: exit_raw = tgt; reason = "TARGET"; break
                    else:
                        if b.high >= sl: exit_raw = sl; reason = "HARD_SL"; break
                        if b.low <= tgt: exit_raw = tgt; reason = "TARGET"; break
                    j += 1
                else:
                    exit_raw = candles[min(j, n - 1)].close; reason = "EOD"
                exit_px = exit_raw - (SLIP if side == "LONG" else -SLIP)
                pts = (exit_px - pos["entry"]) if side == "LONG" else (pos["entry"] - exit_px)
                rupees = pts * QTY - COST
                trades.append(dict(day=str(d), side=side, entry=round(pos["entry"], 1),
                                   exit=round(exit_px, 1), pts=round(pts, 1),
                                   rupees=round(rupees, 0), reason=reason,
                                   etime=c.timestamp.strftime("%H:%M")))
                pos = None
        i += 1

    net = sum(t["rupees"] for t in trades)
    wins = [t for t in trades if t["rupees"] > 0]
    gw = sum(t["rupees"] for t in wins)
    gl = -sum(t["rupees"] for t in trades if t["rupees"] < 0)
    print(f"LIVE-MODULE REPLAY over {len({t['day'] for t in trades})} trade-days")
    print(f"  trades={len(trades)} win%={100*len(wins)//max(len(trades),1)} "
          f"net=₹{net:,.0f} PF={gw/gl:.2f} (gross_win={gw:.0f} gross_loss={gl:.0f})")
    print(f"  backtest reference (orb_lab eval): trades=17 net=₹15,490 full PF=1.77")
    for t in trades:
        print(f"  {t['day']} {t['side']:5} {t['etime']} {t['entry']}->{t['exit']} "
              f"{t['pts']:+.1f}pts ₹{t['rupees']:+.0f} {t['reason']}")


if __name__ == "__main__":
    main()
