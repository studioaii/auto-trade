"""Replay BankNifty 2.0 strategy against stored candle logs.

Reads candle_logs/banknifty2_candles_YYYY-MM-DD.csv (with BN1 logs as seed
for the first session) and drives strategy_v2 + the engine's per-candle
flow without any Kite/I-O. Output: per-day list of (FIRED|SKIPPED|LIMIT_*)
attempts with reasons, plus a summary table.

A CONFIG_PATCH dict at the top can override INSTRUMENT_CONFIG["BANKNIFTY_2"]
to test what-if changes against the same candle data.
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from services.trading_state import Candle  # noqa: E402
from services.indicators import (  # noqa: E402
    get_latest_indicators,
    candle_body_pct,
    MIN_CANDLES,
)
from services.strategy_v2 import (  # noqa: E402
    V2Signal, V2Model, V2Setup, PendingLimitOrder,
    DayContext, DayClass,
    classify_day, reclassify_chop_if_dead, update_consecutive_legs,
    do_not_enter_reasons, high_quality_score,
    model_allowed_by_day,
    evaluate_model_a_setup, maybe_fire_model_a,
    evaluate_model_b, evaluate_model_c, evaluate_model_d,
    volume_ratio,
)
from config import INSTRUMENT_CONFIG  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
LOG_DIR = REPO / "candle_logs"

# Engine constants mirrored here so the harness can apply --patch overrides.
MIN_QUALITY_SCORE = 7

# Which dates to replay
DAYS = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"]


# ---------------------------------------------------------------------------
# Candle loading
# ---------------------------------------------------------------------------

def _load_csv_candles(path: Path) -> list[Candle]:
    out: list[Candle] = []
    with path.open() as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                t = row["time"]
                d = row["date"]
                hh, mm = t.split(":")[:2]
                ts = datetime(*map(int, d.split("-")), int(hh), int(mm), tzinfo=IST)
                out.append(Candle(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(float(row["volume"])),
                ))
            except (KeyError, ValueError):
                continue
    return out


def load_bn2_day_with_metadata(date_str: str) -> tuple[list[Candle], dict]:
    """Returns (candles, meta) where meta has the recorded day_class/gap_pct/vwap_drift_pct.

    Needed because BN2 candle logs start at 09:35 (engine boots after market open),
    so we can't recompute opening-range / gap accurately. The live engine fetched
    09:15-09:30 historically and classified correctly — we trust those recorded fields.
    """
    p = LOG_DIR / f"banknifty2_candles_{date_str}.csv"
    candles: list[Candle] = []
    meta: dict = {"day_class": "UNKNOWN", "gap_pct": None, "vwap_drift_pct": None,
                  "downgrade_time": None}
    with p.open() as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                d = row["date"]; t = row["time"]
                hh, mm = t.split(":")[:2]
                ts = datetime(*map(int, d.split("-")), int(hh), int(mm), tzinfo=IST)
                candles.append(Candle(
                    timestamp=ts,
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=int(float(row["volume"])),
                ))
                dc = row.get("day_class", "UNKNOWN") or "UNKNOWN"
                if dc != "UNKNOWN":
                    if meta["day_class"] == "UNKNOWN":
                        meta["day_class"] = dc
                        meta["gap_pct"] = float(row["gap_pct"]) if row.get("gap_pct") else None
                        meta["vwap_drift_pct"] = float(row["vwap_drift_pct"]) if row.get("vwap_drift_pct") else None
                    elif dc == "CHOP_DAY" and meta["day_class"] != "CHOP_DAY":
                        meta["downgrade_time"] = t
                        meta["day_class"] = "CHOP_DAY"
            except (KeyError, ValueError):
                continue
    return candles, meta


def load_bn2_day(date_str: str) -> list[Candle]:
    p = LOG_DIR / f"banknifty2_candles_{date_str}.csv"
    return _load_csv_candles(p)


def load_bn1_day(date_str: str) -> list[Candle]:
    p = LOG_DIR / f"banknifty_candles_{date_str}.csv"
    if not p.exists():
        return []
    return _load_csv_candles(p)


def load_seed_for(day_idx: int) -> list[Candle]:
    """Load enough prior-session candles to satisfy MIN_CANDLES warmup."""
    if day_idx == 0:
        # No BN2 prior; use BN1 May 15 (last available BN1 day before May 18).
        prior = load_bn1_day("2026-05-15")
    else:
        prev = DAYS[day_idx - 1]
        prior = load_bn2_day(prev)
    # Take just enough to seed (40 candles gives EMA20 warmth + RSI buffer).
    return prior[-40:] if len(prior) >= 40 else prior


# ---------------------------------------------------------------------------
# Engine emulator (subset of strategy_engine_v2._on_candle_ready)
# ---------------------------------------------------------------------------

@dataclass
class Attempt:
    when: datetime
    model: str
    signal: str
    outcome: str          # FIRED | SKIPPED | LIMIT_PLACED | LIMIT_CANCELLED
    day_class: str
    quality_score: int
    skip_reasons: list[str]
    reason: str
    spot: float
    body_pct: float
    vol_ratio: float
    rsi: float
    vwap_dist_pct: float


@dataclass
class DailyOutcome:
    date: str
    day_class: str
    fired: int = 0
    skipped: int = 0
    limit_placed: int = 0
    limit_cancelled: int = 0
    attempts: list[Attempt] = field(default_factory=list)


def replay_day(
    date_str: str,
    seed: list[Candle],
    today: list[Candle],
    cfg: dict,
    min_quality_score: int,
    prev_close: Optional[float],
    pre_classified: Optional[dict] = None,
    max_trades_per_day: Optional[int] = None,
    verbose: bool = False,
) -> DailyOutcome:
    """Walk `today` candles in order, running v2's per-candle pipeline.

    Mimics strategy_engine_v2._on_candle_ready, with two adjustments for the
    backtest harness:
      1. `update_consecutive_legs` is only called for today's candles (the
         live engine boots after market open and does NOT run it on the
         historically-loaded seed/pre-09:35 candles).
      2. Day class is taken from `pre_classified` when available (the live
         engine's classification is authoritative because the candle logs
         miss the 09:15-09:30 candles that classify_day needs).
    """
    candles: list[Candle] = list(seed)
    ctx = DayContext(prev_close=prev_close)
    if pre_classified and pre_classified.get("day_class") != "UNKNOWN":
        ctx.day_class = DayClass(pre_classified["day_class"]) if pre_classified["day_class"] != "CHOP_DAY" or pre_classified.get("downgrade_time") is None else DayClass(
            # Initially treat downgraded days as their pre-downgrade class so
            # the chop downgrade re-fires in our harness at the right time.
            "NORMAL_DAY"
        )
        ctx.gap_pct = pre_classified.get("gap_pct")
        ctx.vwap_drift_at_950 = pre_classified.get("vwap_drift_pct")
    pending: Optional[PendingLimitOrder] = None
    last_chop_check_at: Optional[datetime] = None
    fired_count = 0
    outcome = DailyOutcome(date=date_str, day_class=ctx.day_class.value)
    # Cap trades only if specified (None = unlimited for what-if analysis)
    cap = max_trades_per_day if max_trades_per_day is not None else 10**9
    entry_start = time(*cfg.get("entry_window_start", (9, 50)))

    for candle in today:
        candles.append(candle)
        indicators = get_latest_indicators(candles)
        now = candle.timestamp

        # Per-candle: leg tracker (today's candles only, matching live engine)
        update_consecutive_legs(ctx, candle)

        today_candles_only = [c for c in candles if c.timestamp.date() == now.date()]
        if ctx.day_class == DayClass.UNKNOWN and now.time() >= entry_start:
            # Fallback path if we don't have pre_classified
            cls = classify_day(ctx, today_candles_only, indicators.get("vwap", 0.0), cfg)
            outcome.day_class = cls.value

        # Hourly chop re-check (still active so we model the May 22 downgrade)
        if last_chop_check_at is None:
            last_chop_check_at = now
        else:
            mins = (now - last_chop_check_at).total_seconds() / 60.0
            if reclassify_chop_if_dead(ctx, today_candles_only, indicators.get("vwap", 0.0), int(mins)):
                last_chop_check_at = now
                outcome.day_class = ctx.day_class.value

        # ── Pending Model-A: try to fire on this candle's close ──
        if pending is not None:
            setup = maybe_fire_model_a(
                pending,
                state_spot := candle.close,  # use candle close as spot proxy
                candle,
                candles,
                cfg,
            )
            if setup is not None:
                # Engine bypasses do_not_enter for Model A firing — that's the bug
                # that lets climactic moves through. Optionally apply it here.
                a_skip = []
                if cfg.get("_apply_donot_enter_to_model_a", False):
                    a_skip = do_not_enter_reasons(candles, indicators, ctx, cfg, now, setup.signal)
                if a_skip:
                    outcome.attempts.append(Attempt(
                        when=now, model="A_COMPRESSION_BREAKOUT", signal=setup.signal.value,
                        outcome="SKIPPED", day_class=ctx.day_class.value,
                        quality_score=0, skip_reasons=a_skip, reason=setup.reason,
                        spot=candle.close, body_pct=candle_body_pct(candle),
                        vol_ratio=volume_ratio(candles),
                        rsi=indicators.get("rsi14") or 0.0,
                        vwap_dist_pct=(
                            (candle.close - indicators.get("vwap", 0.0)) /
                            indicators.get("vwap", 1.0) * 100 if indicators.get("vwap") else 0
                        ),
                    ))
                    outcome.skipped += 1
                    pending = None  # also cancel pending
                    continue
                if fired_count < cap:
                    outcome.attempts.append(Attempt(
                        when=now, model="A_COMPRESSION_BREAKOUT", signal=setup.signal.value,
                        outcome="FIRED", day_class=ctx.day_class.value,
                        quality_score=10, skip_reasons=[], reason=setup.reason,
                        spot=candle.close, body_pct=candle_body_pct(candle),
                        vol_ratio=volume_ratio(candles),
                        rsi=indicators.get("rsi14") or 0.0,
                        vwap_dist_pct=(
                            (candle.close - indicators.get("vwap", 0.0)) /
                            indicators.get("vwap", 1.0) * 100 if indicators.get("vwap") else 0
                        ),
                    ))
                    outcome.fired += 1
                    fired_count += 1
                    pending = None
                    continue
                # Otherwise let pending live; cap reached.

        # Age pending; expire if past TTL
        if pending is not None:
            pending.candles_alive += 1
            if pending.candles_alive > cfg.get("model_a_setup_ttl_candles", 6):
                outcome.attempts.append(Attempt(
                    when=now, model="A_COMPRESSION_BREAKOUT", signal=pending.signal.value,
                    outcome="LIMIT_CANCELLED", day_class=ctx.day_class.value,
                    quality_score=0, skip_reasons=["ttl expired"], reason=pending.reason,
                    spot=candle.close, body_pct=candle_body_pct(candle),
                    vol_ratio=volume_ratio(candles),
                    rsi=indicators.get("rsi14") or 0.0,
                    vwap_dist_pct=0,
                ))
                outcome.limit_cancelled += 1
                pending = None

        if fired_count >= cap:
            continue
        if not indicators.get("enough_data"):
            continue
        if ctx.day_class == DayClass.UNKNOWN:
            continue

        # Try Model B/C/D in order
        candidate: Optional[V2Setup] = None
        for model_enum, evaluator in (
            (V2Model.B, evaluate_model_b),
            (V2Model.C, evaluate_model_c),
            (V2Model.D, evaluate_model_d),
        ):
            if not model_allowed_by_day(model_enum, ctx.day_class):
                continue
            setup = evaluator(candles, indicators, cfg, now)
            if setup is None:
                continue
            skip = do_not_enter_reasons(candles, indicators, ctx, cfg, now, setup.signal)
            if skip:
                outcome.attempts.append(Attempt(
                    when=now, model=setup.model.value, signal=setup.signal.value,
                    outcome="SKIPPED", day_class=ctx.day_class.value,
                    quality_score=0, skip_reasons=skip, reason=setup.reason,
                    spot=candle.close, body_pct=candle_body_pct(candle),
                    vol_ratio=volume_ratio(candles),
                    rsi=indicators.get("rsi14") or 0.0,
                    vwap_dist_pct=(
                        (candle.close - indicators.get("vwap", 0.0)) /
                        indicators.get("vwap", 1.0) * 100 if indicators.get("vwap") else 0
                    ),
                ))
                outcome.skipped += 1
                continue
            qscore, qfailed = high_quality_score(
                candles, indicators, ctx, cfg, now, setup.signal, setup.model
            )
            if qscore < min_quality_score:
                outcome.attempts.append(Attempt(
                    when=now, model=setup.model.value, signal=setup.signal.value,
                    outcome="SKIPPED", day_class=ctx.day_class.value,
                    quality_score=qscore,
                    skip_reasons=[f"quality={qscore}/10"] + qfailed,
                    reason=setup.reason,
                    spot=candle.close, body_pct=candle_body_pct(candle),
                    vol_ratio=volume_ratio(candles),
                    rsi=indicators.get("rsi14") or 0.0,
                    vwap_dist_pct=(
                        (candle.close - indicators.get("vwap", 0.0)) /
                        indicators.get("vwap", 1.0) * 100 if indicators.get("vwap") else 0
                    ),
                ))
                outcome.skipped += 1
                continue
            candidate = setup
            break

        if candidate is not None:
            outcome.attempts.append(Attempt(
                when=now, model=candidate.model.value, signal=candidate.signal.value,
                outcome="FIRED", day_class=ctx.day_class.value,
                quality_score=10, skip_reasons=[], reason=candidate.reason,
                spot=candle.close, body_pct=candle_body_pct(candle),
                vol_ratio=volume_ratio(candles),
                rsi=indicators.get("rsi14") or 0.0,
                vwap_dist_pct=(
                    (candle.close - indicators.get("vwap", 0.0)) /
                    indicators.get("vwap", 1.0) * 100 if indicators.get("vwap") else 0
                ),
            ))
            outcome.fired += 1
            fired_count += 1
            continue

        # Try Model A setup (only if no candidate found from B/C/D)
        if model_allowed_by_day(V2Model.A, ctx.day_class) and pending is None:
            new_pending = evaluate_model_a_setup(candles, indicators, cfg, now)
            if new_pending is not None:
                pending = new_pending
                outcome.attempts.append(Attempt(
                    when=now, model="A_COMPRESSION_BREAKOUT", signal=new_pending.signal.value,
                    outcome="LIMIT_PLACED", day_class=ctx.day_class.value,
                    quality_score=0, skip_reasons=[],
                    reason=new_pending.reason,
                    spot=candle.close, body_pct=candle_body_pct(candle),
                    vol_ratio=volume_ratio(candles),
                    rsi=indicators.get("rsi14") or 0.0,
                    vwap_dist_pct=0,
                ))
                outcome.limit_placed += 1

    return outcome


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _prev_close_for(day_idx: int, seed_candles: list[Candle], today: list[Candle]) -> Optional[float]:
    """Derive prev_close: last candle of prior day if same date as seed tail, else compute from gap."""
    if not seed_candles:
        return None
    last_seed = seed_candles[-1]
    if not today:
        return last_seed.close
    if last_seed.timestamp.date() != today[0].timestamp.date():
        return last_seed.close
    return None


def run(cfg_patch: dict, min_quality_score: int, label: str, max_trades_per_day: Optional[int]) -> list[DailyOutcome]:
    base_cfg = INSTRUMENT_CONFIG["BANKNIFTY_2"]
    cfg = {**base_cfg, **cfg_patch}
    print(f"\n{'=' * 78}")
    print(f"RUN: {label}")
    print(f"{'=' * 78}")
    if cfg_patch:
        print("Patched keys:", {k: cfg[k] for k in cfg_patch})
    print(f"MIN_QUALITY_SCORE: {min_quality_score}")
    print(f"max_trades_per_day cap: {'∞ (analysis)' if max_trades_per_day is None else max_trades_per_day}")

    daily_outcomes: list[DailyOutcome] = []
    for idx, d in enumerate(DAYS):
        today, meta = load_bn2_day_with_metadata(d)
        seed = load_seed_for(idx)
        prev_close = _prev_close_for(idx, seed, today)
        out = replay_day(
            date_str=d, seed=seed, today=today, cfg=cfg,
            min_quality_score=min_quality_score,
            prev_close=prev_close,
            pre_classified=meta,
            max_trades_per_day=max_trades_per_day,
        )
        daily_outcomes.append(out)

    # Summary
    print(f"\n{'Date':12} {'DayClass':14} {'FIRED':>6} {'SKIP':>6} {'LIM-PLACE':>10} {'LIM-CANCEL':>11}")
    print("-" * 78)
    tot_f = tot_s = tot_lp = tot_lc = 0
    for o in daily_outcomes:
        print(f"{o.date:12} {o.day_class:14} {o.fired:>6} {o.skipped:>6} {o.limit_placed:>10} {o.limit_cancelled:>11}")
        tot_f += o.fired; tot_s += o.skipped; tot_lp += o.limit_placed; tot_lc += o.limit_cancelled
    print("-" * 78)
    print(f"{'TOTALS':12} {'':14} {tot_f:>6} {tot_s:>6} {tot_lp:>10} {tot_lc:>11}")

    # Detailed attempts
    print("\nAttempt detail (all outcomes):")
    for o in daily_outcomes:
        for a in o.attempts:
            t = a.when.strftime("%H:%M")
            extra = ""
            if a.outcome == "SKIPPED":
                extra = f" | skip: {';'.join(a.skip_reasons[:3])}"
            print(f"  {o.date} {t}  {a.outcome:13}  {a.model:25}  {a.signal:7}  "
                  f"spot={a.spot:.0f} vol_r={a.vol_ratio:.2f} rsi={a.rsi:.1f} "
                  f"body={a.body_pct:.0f}% vwap_d={a.vwap_dist_pct:+.2f}% "
                  f"| {a.reason}{extra}")

    return daily_outcomes


# ---------------------------------------------------------------------------
# Configurations to test
# ---------------------------------------------------------------------------

BASELINE = {}   # current production config

PROPOSED_V1 = {
    # ── DO-NOT-ENTER relaxations ─────────────────────────────────────────
    "max_vol_ratio":              5.0,     # was 3.0 — let institutional flow through
    "max_consecutive_same_dir":   5,       # was 3 — BN trends in 5–8 candle clusters
    # ── Model A — make compression actually achievable ──────────────────
    "model_a_max_body_pct":       60.0,    # was 50.0
    "model_a_max_vol_ratio_avg":  0.95,    # was 0.85
    "model_a_setup_ttl_candles":  8,       # was 6
    "min_breakout_margin_pts":    30,      # was 50
}

PROPOSED_V2 = {
    # Same as V1 but apply DO-NOT-ENTER to Model A firing too (closes the
    # climactic-volume loophole that let May 22 12:50 through).
    **PROPOSED_V1,
    "max_vol_ratio":              4.0,     # tighter; still well above normal 1–2.5×
    "_apply_donot_enter_to_model_a": True,
}


if __name__ == "__main__":
    # Single run: read the NEW config from config.py and simulate the engine's
    # new "apply do_not_enter to Model A" behavior. Must match PROPOSED V2.
    run({"_apply_donot_enter_to_model_a": True}, 6,
        "FINAL (config.py reloaded + Model A do_not_enter applied)",
        max_trades_per_day=2)
