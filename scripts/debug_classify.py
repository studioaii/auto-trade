"""Trace classify_day / reclassify on May 18 to find the mismatch."""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import time
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from scripts.backtest_bn2 import load_bn2_day, load_seed_for, DAYS, _prev_close_for  # noqa: E402
from services.strategy_v2 import classify_day, reclassify_chop_if_dead, DayContext, DayClass, update_consecutive_legs  # noqa: E402
from services.indicators import get_latest_indicators  # noqa: E402
from config import INSTRUMENT_CONFIG  # noqa: E402

cfg = INSTRUMENT_CONFIG["BANKNIFTY_2"]

for idx, d in enumerate(DAYS):
    today = load_bn2_day(d)
    seed = load_seed_for(idx)
    prev_close = _prev_close_for(idx, seed, today)
    ctx = DayContext(prev_close=prev_close)
    print(f"\n=== {d} | prev_close={prev_close} | today_open={today[0].open if today else 'EMPTY'} ===")
    candles = list(seed)
    last_chop_check_at = None
    classified_at = None
    downgraded_at = None
    entry_start = time(*cfg.get("entry_window_start", (9, 50)))
    for c in today:
        candles.append(c)
        ind = get_latest_indicators(candles)
        update_consecutive_legs(ctx, c)
        today_only = [x for x in candles if x.timestamp.date() == c.timestamp.date()]
        if ctx.day_class == DayClass.UNKNOWN and c.timestamp.time() >= entry_start:
            cls = classify_day(ctx, today_only, ind.get("vwap", 0.0), cfg)
            classified_at = c.timestamp.strftime("%H:%M")
            print(f"  classify @{classified_at} -> {cls.value} | gap={ctx.gap_pct:.3f}% drift={ctx.vwap_drift_at_950:.3f}% or=[{ctx.or_low}, {ctx.or_high}] vwap={ind.get('vwap', 0):.1f}")
        if last_chop_check_at is None:
            last_chop_check_at = c.timestamp
        else:
            mins = (c.timestamp - last_chop_check_at).total_seconds() / 60.0
            if reclassify_chop_if_dead(ctx, today_only, ind.get("vwap", 0.0), int(mins)):
                downgraded_at = c.timestamp.strftime("%H:%M")
                print(f"  CHOP downgrade @{downgraded_at} | mins={int(mins)}")
                last_chop_check_at = c.timestamp
    print(f"  final day_class: {ctx.day_class.value}  (classified at {classified_at}, downgraded at {downgraded_at})")
