"""Run the BN2 v2 strategy (NEW config) against BN1's longer candle archive.

BN1 candle logs cover May 4 - May 22 (15 trading days), giving us 3× the
data of BN2's window. For each day:
  - load BN1 candles
  - approximate day_class from available data (BN1 logs also start at 09:35,
    so opening-range / gap are approximations)
  - run the v2 strategy with the NEW config from config.py
  - print v2's decisions alongside BN1's actual paper trades + outcomes
  - estimate forward spot move after each v2 fire (proxy for hypothetical P&L)

Goal: validate that the new config (a) catches BN1's WINs, (b) avoids BN1's
LOSSes, and (c) doesn't produce a flood of false-positive fires.
"""
from __future__ import annotations
import csv
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from services.trading_state import Candle  # noqa: E402
from services.indicators import get_latest_indicators, candle_body_pct, MIN_CANDLES  # noqa: E402
from services.strategy_v2 import (  # noqa: E402
    V2Signal, V2Model, PendingLimitOrder, DayContext, DayClass,
    classify_day, reclassify_chop_if_dead, update_consecutive_legs,
    do_not_enter_reasons, high_quality_score, model_allowed_by_day,
    evaluate_model_a_setup, maybe_fire_model_a,
    evaluate_model_b, evaluate_model_c, evaluate_model_d, volume_ratio,
)
from config import INSTRUMENT_CONFIG  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
LOG_DIR = REPO / "candle_logs"
MIN_QUALITY_SCORE = 7  # match engine constant

DAYS = [
    "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
    "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14", "2026-05-15",
    "2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22",
]


def _load_bn1(date_str: str) -> list[Candle]:
    p = LOG_DIR / f"banknifty_candles_{date_str}.csv"
    if not p.exists():
        return []
    out: list[Candle] = []
    with p.open() as f:
        for row in csv.DictReader(f):
            try:
                hh, mm = row["time"].split(":")[:2]
                ts = datetime(*map(int, row["date"].split("-")), int(hh), int(mm), tzinfo=IST)
                out.append(Candle(
                    timestamp=ts,
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=int(float(row["volume"])),
                ))
            except (KeyError, ValueError):
                continue
    return out


def _seed_for(day_idx: int) -> list[Candle]:
    if day_idx == 0:
        return []
    return _load_bn1(DAYS[day_idx - 1])[-40:]


def _prev_close(seed: list[Candle], today: list[Candle]) -> Optional[float]:
    if seed and today and seed[-1].timestamp.date() != today[0].timestamp.date():
        return seed[-1].close
    return None


@dataclass
class Fire:
    when: datetime
    model: str
    signal: str
    spot: float
    body_pct: float
    vol_ratio: float
    reason: str


@dataclass
class DaySummary:
    date: str
    day_class: str
    fires: list[Fire]
    skips: list[tuple[datetime, str, str, list[str]]]  # (when, model, signal, reasons)


def replay(date_str: str, seed: list[Candle], today: list[Candle],
           cfg: dict, prev_close: Optional[float]) -> DaySummary:
    candles = list(seed)
    ctx = DayContext(prev_close=prev_close)
    pending: Optional[PendingLimitOrder] = None
    last_chop_check_at: Optional[datetime] = None
    fired = 0
    summary = DaySummary(date=date_str, day_class="UNKNOWN", fires=[], skips=[])
    cap = cfg.get("max_trades_per_day", 2)
    entry_start = time(*cfg.get("entry_window_start", (9, 50)))

    for c in today:
        candles.append(c)
        ind = get_latest_indicators(candles)
        now = c.timestamp
        update_consecutive_legs(ctx, c)

        today_only = [x for x in candles if x.timestamp.date() == now.date()]
        if ctx.day_class == DayClass.UNKNOWN and now.time() >= entry_start:
            cls = classify_day(ctx, today_only, ind.get("vwap", 0.0), cfg)
            summary.day_class = cls.value
        if last_chop_check_at is None:
            last_chop_check_at = now
        else:
            mins = (now - last_chop_check_at).total_seconds() / 60.0
            if reclassify_chop_if_dead(ctx, today_only, ind.get("vwap", 0.0), int(mins)):
                last_chop_check_at = now
                summary.day_class = ctx.day_class.value

        # Pending Model-A fire check
        if pending is not None:
            setup = maybe_fire_model_a(pending, c.close, c, candles, cfg)
            if setup is not None:
                skip = do_not_enter_reasons(candles, ind, ctx, cfg, now, setup.signal)
                if skip:
                    summary.skips.append((now, setup.model.value, setup.signal.value, skip))
                    pending = None
                    continue
                if fired < cap:
                    summary.fires.append(Fire(
                        when=now, model=setup.model.value, signal=setup.signal.value,
                        spot=c.close, body_pct=candle_body_pct(c),
                        vol_ratio=volume_ratio(candles), reason=setup.reason,
                    ))
                    fired += 1
                    pending = None
                    continue
            pending.candles_alive += 1
            if pending is not None and pending.candles_alive > cfg.get("model_a_setup_ttl_candles", 8):
                pending = None

        if fired >= cap or not ind.get("enough_data") or ctx.day_class == DayClass.UNKNOWN:
            continue

        # Try B/C/D
        candidate = None
        for model_enum, evaluator in (
            (V2Model.B, evaluate_model_b),
            (V2Model.C, evaluate_model_c),
            (V2Model.D, evaluate_model_d),
        ):
            if not model_allowed_by_day(model_enum, ctx.day_class):
                continue
            setup = evaluator(candles, ind, cfg, now)
            if setup is None:
                continue
            skip = do_not_enter_reasons(candles, ind, ctx, cfg, now, setup.signal)
            if skip:
                summary.skips.append((now, setup.model.value, setup.signal.value, skip))
                continue
            qscore, qfailed = high_quality_score(candles, ind, ctx, cfg, now, setup.signal, setup.model)
            if qscore < MIN_QUALITY_SCORE:
                summary.skips.append((now, setup.model.value, setup.signal.value,
                                      [f"q={qscore}/10"] + qfailed[:2]))
                continue
            candidate = setup
            break

        if candidate is not None:
            summary.fires.append(Fire(
                when=now, model=candidate.model.value, signal=candidate.signal.value,
                spot=c.close, body_pct=candle_body_pct(c),
                vol_ratio=volume_ratio(candles), reason=candidate.reason,
            ))
            fired += 1
            continue

        # Model A setup
        if model_allowed_by_day(V2Model.A, ctx.day_class) and pending is None:
            new_pending = evaluate_model_a_setup(candles, ind, cfg, now)
            if new_pending is not None:
                pending = new_pending

    return summary


# ---------------------------------------------------------------------------
# Hypothetical forward-P&L proxy
# ---------------------------------------------------------------------------

def forward_proxy(today: list[Candle], entry_idx: int, signal: str) -> dict:
    """Return max favorable & adverse spot move over next 60 min, plus a
    premium-% estimate calibrated against BN1's actual wins.

    Calibration (from paper_trades_banknifty.csv winners):
      May 12 PE: spot −0.49% → premium +13.28%  ratio 27×
      May 14 PE: spot −0.36% → premium +9.03%   ratio 25×
      May 14 CE: spot +0.41% → premium +14.38%  ratio 35×
      May 21 PE: spot −0.32% → premium +22.66%  ratio 71× (low-priced ATM)
    Use 30× as a realistic mid-point for ATM short-dated BankNifty options.
    """
    entry_spot = today[entry_idx].close
    window = today[entry_idx + 1 : entry_idx + 13]
    if not window:
        return {"max_fav_pts": 0, "max_adv_pts": 0, "est_premium_pct": 0, "est_adv_pct": 0}
    if signal == "BUY_CE":
        fav = max(c.high for c in window) - entry_spot
        adv = entry_spot - min(c.low for c in window)
    else:
        fav = entry_spot - min(c.low for c in window)
        adv = max(c.high for c in window) - entry_spot
    leverage = 30.0
    return {
        "max_fav_pts": round(fav, 0),
        "max_adv_pts": round(adv, 0),
        "est_premium_pct": round((fav / entry_spot) * 100 * leverage, 1),
        "est_adv_pct": round((adv / entry_spot) * 100 * leverage, 1),
    }


def find_candle_idx(today: list[Candle], ts: datetime) -> int:
    for i, c in enumerate(today):
        if c.timestamp == ts:
            return i
    return -1


# ---------------------------------------------------------------------------
# Load BN1 actual trades for side-by-side comparison
# ---------------------------------------------------------------------------

def load_bn1_trades() -> dict[str, list[dict]]:
    p = REPO / "paper_trades_banknifty.csv"
    out: dict[str, list[dict]] = {}
    with p.open() as f:
        for row in csv.DictReader(f):
            out.setdefault(row["date"], []).append({
                "time": row["entry_time"][:5],
                "type": row["option_type"],
                "strike": row["strike"],
                "pct": float(row["pnl_pct"]),
                "result": row["result"],
                "exit_reason": row.get("reason_for_exit", ""),
            })
    return out


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _patched(base: dict, **overrides) -> dict:
    d = dict(base)
    d.update(overrides)
    return d


def _allowlist_patch_reversal_extended():
    """Temporarily extend REVERSAL_DAY allowlist to include B and D for testing."""
    from services import strategy_v2 as sv2
    sv2.DAY_CLASS_ALLOWED_MODELS[sv2.DayClass.REVERSAL] = {sv2.V2Model.B, sv2.V2Model.C, sv2.V2Model.D}


def _allowlist_restore():
    from services import strategy_v2 as sv2
    sv2.DAY_CLASS_ALLOWED_MODELS[sv2.DayClass.REVERSAL] = {sv2.V2Model.C}


if __name__ == "__main__":
    cfg = INSTRUMENT_CONFIG["BANKNIFTY_2"]
    bn1_trades = load_bn1_trades()

    print(f"{'=' * 100}")
    print(f"BN2 v2 strategy (NEW config) replayed against BN1 candle archive (May 4 – May 22)")
    print(f"{'=' * 100}")
    print(f"Config: max_vol_ratio={cfg['max_vol_ratio']}, max_consec_dir={cfg['max_consecutive_same_dir']}, "
          f"A_body={cfg['model_a_max_body_pct']}, A_ttl={cfg['model_a_setup_ttl_candles']}, "
          f"breakout_margin={cfg['min_breakout_margin_pts']}")
    print()

    grand_v2_fires = 0
    grand_bn1_trades = 0
    grand_v2_est_pnl = 0.0
    grand_bn1_actual_pnl = 0.0
    confusion = {"v2_win_bn1_win": 0, "v2_skip_bn1_loss": 0,
                 "v2_win_bn1_skip": 0, "v2_loss_bn1_win": 0,
                 "v2_loss_bn1_loss": 0, "v2_win_bn1_loss": 0,
                 "v2_skip_bn1_win": 0, "skip_both": 0}

    for idx, d in enumerate(DAYS):
        today = _load_bn1(d)
        if not today:
            print(f"{d}: NO DATA")
            continue
        seed = _seed_for(idx)
        prev_c = _prev_close(seed, today)
        summary = replay(d, seed, today, cfg, prev_c)
        bn1 = bn1_trades.get(d, [])

        print(f"┌─ {d}  v2_day_class={summary.day_class}")
        for f in summary.fires:
            i = find_candle_idx(today, f.when)
            fwd = forward_proxy(today, i, f.signal) if i >= 0 else {}
            t = f.when.strftime("%H:%M")
            verdict = ""
            if fwd:
                if fwd["est_premium_pct"] >= 18:
                    verdict = "  WIN (likely TARGET/TSL)"
                elif fwd["est_premium_pct"] >= 8:
                    verdict = "  WIN (likely TSL)"
                elif fwd["est_premium_pct"] >= 0:
                    verdict = "  ~FLAT"
                else:
                    verdict = "  LOSS (likely SL)"
            print(f"│  v2 FIRE   {t}  {f.model:25} {f.signal:7} spot={f.spot:.0f}  "
                  f"fwd60m: fav={fwd.get('max_fav_pts', '?'):>4}pt adv={fwd.get('max_adv_pts', '?'):>4}pt "
                  f"premium~{fwd.get('est_premium_pct', 0):+.1f}%/adv{fwd.get('est_adv_pct', 0):+.1f}%{verdict}")
            grand_v2_fires += 1
            grand_v2_est_pnl += fwd.get("est_premium_pct", 0)
        if not summary.fires:
            # On days where v2 didn't fire but might have wanted to, show the closest near-miss
            closest = sorted(summary.skips, key=lambda s: 0 if "q=" not in (s[3][0] if s[3] else "") else int(s[3][0].split("/")[0].split("=")[-1]), reverse=True)
            if closest:
                w, m, sig, reasons = closest[0]
                print(f"│  v2 FIRE   (none)  closest miss: {w.strftime('%H:%M')} {m} {sig} skip={reasons[:2]}")
            else:
                print(f"│  v2 FIRE   (none)  no candidate even tried this day")
        for t in bn1:
            mark = "WIN " if t["result"] == "WIN" else "LOSS"
            print(f"│  BN1 trade {t['time']}  {t['type']} @{t['strike']:5}  "
                  f"{mark} {t['pct']:+.2f}%  exit:{t['exit_reason']}")
            grand_bn1_trades += 1
            grand_bn1_actual_pnl += t["pct"]
        if not bn1:
            print(f"│  BN1 trade (none)")

        # Outcome cell
        bn1_dir = {t["type"] for t in bn1}
        v2_dir = {("CE" if f.signal == "BUY_CE" else "PE") for f in summary.fires}
        if not bn1 and not summary.fires:
            confusion["skip_both"] += 1
        elif bn1 and not summary.fires:
            any_win = any(t["result"] == "WIN" for t in bn1)
            if any_win:
                confusion["v2_skip_bn1_win"] += 1
            else:
                confusion["v2_skip_bn1_loss"] += 1
        elif not bn1 and summary.fires:
            confusion["v2_win_bn1_skip"] += 1
        else:
            # Both fired — check if v2 would have won
            for f in summary.fires:
                i = find_candle_idx(today, f.when)
                fwd = forward_proxy(today, i, f.signal) if i >= 0 else {"est_premium_pct": 0}
                v2_outcome = "WIN" if fwd.get("est_premium_pct", 0) > 10 else "LOSS"
                bn1_outcome = "WIN" if any(t["result"] == "WIN" for t in bn1) else "LOSS"
                key = f"v2_{v2_outcome.lower()}_bn1_{bn1_outcome.lower()}"
                confusion[key] = confusion.get(key, 0) + 1
        print(f"└─")
        print()

    print(f"{'=' * 100}")
    print(f"TOTALS over {len(DAYS)} trading days")
    print(f"{'=' * 100}")
    print(f"  BN1 actual trades:   {grand_bn1_trades} (sum pnl_pct: {grand_bn1_actual_pnl:+.2f}%)")
    print(f"  v2 hypothetical fires: {grand_v2_fires} (sum est_premium_pct: {grand_v2_est_pnl:+.2f}%)")
    print()
    print(f"  Day-level outcomes:")
    for k, v in confusion.items():
        if v > 0:
            print(f"    {k:25} : {v}")

    # =====================================================================
    # CANDIDATE EXTRA TWEAKS: try (a) model_b_signal_vol_max 2.5 → 3.0,
    # and (b) REVERSAL_DAY allows B/C/D. See if May 12 + May 14 unlock.
    # =====================================================================
    print(f"\n{'=' * 100}")
    print("CANDIDATE EXTRA TWEAKS: model_b_signal_vol_max 2.5→3.0  +  REVERSAL_DAY allows B/C/D")
    print(f"{'=' * 100}")
    cfg2 = _patched(cfg, model_b_signal_vol_max=3.0)
    _allowlist_patch_reversal_extended()
    extra_fires = 0
    extra_est_pnl = 0.0
    new_catches = []
    for idx, d in enumerate(DAYS):
        today = _load_bn1(d)
        if not today:
            continue
        seed = _seed_for(idx)
        prev_c = _prev_close(seed, today)
        s = replay(d, seed, today, cfg2, prev_c)
        bn1 = bn1_trades.get(d, [])
        for f in s.fires:
            i = find_candle_idx(today, f.when)
            fwd = forward_proxy(today, i, f.signal) if i >= 0 else {}
            verdict = "WIN" if fwd.get("est_premium_pct", 0) >= 8 else ("LOSS" if fwd.get("est_premium_pct", 0) < 0 else "~FLAT")
            extra_fires += 1
            extra_est_pnl += fwd.get("est_premium_pct", 0)
            new_catches.append((d, f.when.strftime("%H:%M"), f.model, f.signal,
                                fwd.get("est_premium_pct", 0), verdict,
                                "WIN" if any(t["result"] == "WIN" for t in bn1) else "LOSS" if bn1 else "no-trade"))
    _allowlist_restore()
    print(f"Total fires: {extra_fires}  sum est_premium_pct: {extra_est_pnl:+.2f}%")
    print(f"{'date':12} {'time':6} {'model':25} {'sig':7} {'premium%':>9}  {'v2 verdict':10}  BN1")
    for c in new_catches:
        print(f"{c[0]:12} {c[1]:6} {c[2]:25} {c[3]:7} {c[4]:>+9.1f}  {c[5]:10}  {c[6]}")
