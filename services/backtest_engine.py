"""
Backtesting engine for NIFTY_INTRADAY_VWAP_EMA_BREAKOUT strategy.

Uses Nifty 50 index 5-min historical candles from Kite API.
Since the index has no volume, TWAP (equal-weight) is used instead of VWAP.

P&L is direction-aware:
  BUY_CE profits when Nifty goes UP  → P&L = exit - entry (index points)
  BUY_PE profits when Nifty goes DOWN → P&L = entry - exit (index points)

Exit thresholds are point-based (not % of options premium):
  These approximate what the options strategy achieves assuming
  an ATM entry premium of ~₹150 and delta ~0.5.
  Est. options ₹ P&L = nifty_points × 0.5 × 75 (lot size)
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect

from services.trading_state import Candle
from services.instruments import NIFTY_INDEX_TOKEN
from services.indicators import get_latest_indicators
from services.strategy import Signal, generate_signal
from services.risk_manager import MAX_TRADES_PER_DAY

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

MARKET_OPEN_READY = time(9, 50)    # aligned with strategy.py v2
LAST_ENTRY_TIME   = time(14, 0)    # aligned with strategy.py v2
FORCE_EXIT_TIME   = time(15, 20)

# ---------------------------------------------------------------------------
# Point-based exit thresholds for index-level backtest
# (Options 40% target on ₹150 entry ≈ ₹60 gain ÷ 0.5 delta = 120 Nifty pts)
# (Options 25% SL    on ₹150 entry ≈ ₹37 loss ÷ 0.5 delta = 75  Nifty pts)
# ---------------------------------------------------------------------------
TARGET_POINTS    = 120   # Nifty pts in right direction → exit as TARGET_HIT
SL_POINTS        = 75    # Nifty pts against             → exit as STOPLOSS_HIT
BREAKEVEN_POINTS = 80    # Move SL to entry after this many favorable pts
TRAIL_POINTS     = 100   # Activate trailing SL after this many favorable pts
TRAIL_GAP        = 35    # Trail SL stays this many pts behind the peak move


# ---------------------------------------------------------------------------
# Internal position state
# ---------------------------------------------------------------------------
@dataclass
class _Position:
    num:          int
    signal:       str        # "BUY_CE" or "BUY_PE"
    entry_price:  float      # Nifty level at entry
    entry_time:   datetime
    entry_reason: str
    sl_points:    float      # current SL distance from entry (points)
    best_move:    float = 0.0   # peak favorable move seen so far (points)
    trail_active: bool = False
    breakeven_set: bool = False

    def favorable_move(self, current_nifty: float) -> float:
        """Positive = moving in right direction (up for CE, down for PE)."""
        if self.signal == "BUY_CE":
            return current_nifty - self.entry_price
        else:
            return self.entry_price - current_nifty

    def sl_nifty_level(self) -> float:
        """Current stop-loss as a Nifty index level."""
        if self.signal == "BUY_CE":
            return self.entry_price - self.sl_points
        else:
            return self.entry_price + self.sl_points

    def target_nifty_level(self) -> float:
        if self.signal == "BUY_CE":
            return self.entry_price + TARGET_POINTS
        else:
            return self.entry_price - TARGET_POINTS


# ---------------------------------------------------------------------------
# Public result shapes
# ---------------------------------------------------------------------------
@dataclass
class BacktestTrade:
    num:          int
    signal:       str
    entry_time:   str
    entry_nifty:  float
    entry_reason: str
    exit_time:    str
    exit_nifty:   float
    exit_reason:  str
    points:       float   # positive = profit, negative = loss (direction-aware)
    pct:          float
    result:       str     # "WIN" / "LOSS"
    est_options_pnl: float  # rough ₹ estimate = points × 0.5 × 75


@dataclass
class CandleBar:
    time:         str
    open:         float
    high:         float
    low:          float
    close:        float
    vwap:         float
    ema20:        Optional[float]
    market_state: str
    signal:       str
    in_trade:     bool
    note:         str


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class BacktestEngine:

    # ------------------------------------------------------------------
    # Multi-day backtest
    # ------------------------------------------------------------------
    def run_multi(self, kite: KiteConnect, from_date: date, to_date: date) -> dict:
        """
        Run strategy across a date range.
        Returns aggregate summary + per-day results (with trades and candles).
        Skips weekends and market holidays (days with no data).
        """
        current = from_date
        daily_results = []
        all_trades = []
        trading_days = 0
        skipped_days = 0

        while current <= to_date:
            # Skip weekends
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            result = self.run(kite, current)

            if "error" in result:
                # Market holiday or no data
                skipped_days += 1
                current += timedelta(days=1)
                continue

            trading_days += 1
            trades = result.get("trades", [])
            all_trades.extend(trades)

            daily_results.append({
                "date":    result["date"],
                "summary": result["summary"],
                "trades":  trades,
                "candles": result["candles"],
            })

            current += timedelta(days=1)

        # Aggregate
        total_trades = len(all_trades)
        wins   = [t for t in all_trades if t.get("points", 0) > 0]
        losses = [t for t in all_trades if t.get("points", 0) <= 0]
        total_pts = round(sum(t.get("points", 0) for t in all_trades), 2)
        total_rs  = round(sum(t.get("est_options_pnl", 0) for t in all_trades), 0)

        # Cumulative P&L curve
        cum_pnl = []
        running = 0.0
        for t in all_trades:
            running += t.get("points", 0)
            cum_pnl.append(round(running, 2))

        # Max drawdown
        max_dd = 0.0
        peak = 0.0
        for p in cum_pnl:
            if p > peak:
                peak = p
            dd = peak - p
            if dd > max_dd:
                max_dd = dd

        # Best / worst day
        best_day = None
        worst_day = None
        if daily_results:
            best_day  = max(daily_results, key=lambda d: d["summary"].get("total_points", 0))
            worst_day = min(daily_results, key=lambda d: d["summary"].get("total_points", 0))

        return {
            "from_date":     str(from_date),
            "to_date":       str(to_date),
            "trading_days":  trading_days,
            "skipped_days":  skipped_days,
            "aggregate": {
                "total_trades":    total_trades,
                "wins":            len(wins),
                "losses":          len(losses),
                "win_rate_pct":    round(len(wins) / total_trades * 100, 1) if total_trades else 0,
                "total_points":    total_pts,
                "total_est_rs":    total_rs,
                "avg_win_points":  round(sum(t["points"] for t in wins)   / len(wins),   2) if wins   else 0,
                "avg_loss_points": round(sum(t["points"] for t in losses) / len(losses), 2) if losses else 0,
                "max_drawdown_pts": round(max_dd, 2),
                "best_day":        best_day["date"] if best_day else None,
                "best_day_pts":    best_day["summary"]["total_points"] if best_day else 0,
                "worst_day":       worst_day["date"] if worst_day else None,
                "worst_day_pts":   worst_day["summary"]["total_points"] if worst_day else 0,
                "avg_pts_per_day": round(total_pts / trading_days, 2) if trading_days else 0,
                "cum_pnl":         cum_pnl,
            },
            "daily": [
                {
                    "date":          d["date"],
                    "total_trades":  d["summary"]["total_trades"],
                    "wins":          d["summary"]["wins"],
                    "losses":        d["summary"]["losses"],
                    "win_rate_pct":  d["summary"]["win_rate_pct"],
                    "total_points":  d["summary"]["total_points"],
                    "total_est_rs":  d["summary"]["total_est_rs"],
                    "trades":        d["trades"],
                    "candles":       d["candles"],
                }
                for d in daily_results
            ],
        }

    # ------------------------------------------------------------------
    # Single-day backtest
    # ------------------------------------------------------------------
    def run(self, kite: KiteConnect, trade_date: date) -> dict:
        candles = self._fetch_candles(kite, trade_date)
        if not candles:
            return {"error": f"No market data for {trade_date}. Market may have been closed."}

        bars:              list[CandleBar]   = []
        trades:            list[BacktestTrade] = []
        replay:            list[Candle]      = []
        position:          Optional[_Position] = None
        trades_today:      int               = 0
        first_trade_was_sl: bool             = False   # block T2 if T1 hit hard SL

        for candle in candles:
            ctime = candle.timestamp.time()

            # ── 1. Force-exit at 15:20 ───────────────────────────────
            if position and ctime >= FORCE_EXIT_TIME:
                self._close(position, candle.open, candle.timestamp, "TIME_EXIT", trades)
                position = None

            replay.append(candle)
            indicators   = get_latest_indicators(replay)
            vwap         = indicators.get("vwap", 0.0)
            ema20        = indicators.get("ema20")
            ema20_series = indicators.get("ema20_series", [])
            market_state = indicators.get("market_state", "UNKNOWN")
            enough       = indicators.get("enough_data", False)

            note       = ""
            signal_str = "—"

            # ── 2. Check exit conditions for open position ───────────
            if position is not None:
                exited, exit_px, exit_reason = self._check_exits(position, candle)
                if exited:
                    self._close(position, exit_px, candle.timestamp, exit_reason, trades)
                    note     = f"EXIT @ {exit_px:.1f}  ({exit_reason})"
                    # Track hard SL on the first trade so we can block the second
                    if exit_reason == "STOPLOSS_HIT" and trades_today == 1:
                        first_trade_was_sl = True
                    position = None
                else:
                    self._update_trail(position, candle)

            # ── 3. Try entry signal if flat ──────────────────────────
            if (
                position is None
                and trades_today < MAX_TRADES_PER_DAY
                and not first_trade_was_sl          # block T2 after hard SL on T1
                and enough
                and MARKET_OPEN_READY <= ctime < LAST_ENTRY_TIME
            ):
                signal, reason = generate_signal(
                    replay, vwap, ema20, ema20_series, market_state,
                    rsi14=indicators.get("rsi14"),
                    volume_surge=indicators.get("volume_surge", True),
                )
                signal_str = signal.value

                if signal != Signal.NO_SIGNAL:
                    trades_today += 1
                    entry_px = candle.close
                    position = _Position(
                        num          = trades_today,
                        signal       = signal.value,
                        entry_price  = entry_px,
                        entry_time   = candle.timestamp,
                        entry_reason = reason,
                        sl_points    = SL_POINTS,
                    )
                    note = f"ENTRY @ {entry_px:.1f}  ({signal.value})"

            bars.append(CandleBar(
                time         = candle.timestamp.strftime("%H:%M"),
                open         = candle.open,
                high         = candle.high,
                low          = candle.low,
                close        = candle.close,
                vwap         = round(vwap, 2),
                ema20        = round(ema20, 2) if ema20 is not None else None,
                market_state = market_state,
                signal       = signal_str,
                in_trade     = position is not None,
                note         = note,
            ))

        # ── 4. End-of-day close ──────────────────────────────────────
        if position and candles:
            last = candles[-1]
            self._close(position, last.close, last.timestamp, "EOD_EXIT", trades)

        return _build_result(trade_date, candles, bars, trades)

    # ------------------------------------------------------------------
    # Fetch & TWAP fix
    # ------------------------------------------------------------------
    def _fetch_candles(self, kite: KiteConnect, trade_date: date) -> list[Candle]:
        from_dt = datetime(trade_date.year, trade_date.month, trade_date.day,
                           9, 0, 0, tzinfo=IST)
        to_dt   = datetime(trade_date.year, trade_date.month, trade_date.day,
                           15, 30, 0, tzinfo=IST)
        try:
            raw = kite.historical_data(
                instrument_token=NIFTY_INDEX_TOKEN,
                from_date=from_dt,
                to_date=to_dt,
                interval="5minute",
            )
        except Exception as exc:
            logger.error("historical_data fetch failed: %s", exc)
            return []

        result = []
        for r in raw:
            ts = r["date"]
            if hasattr(ts, "astimezone"):
                ts = ts.astimezone(IST)
            result.append(Candle(
                timestamp = ts,
                open      = float(r["open"]),
                high      = float(r["high"]),
                low       = float(r["low"]),
                close     = float(r["close"]),
                volume    = int(r.get("volume", 0)),
            ))

        # Nifty index has no volume in historical data → VWAP would be 0.
        # Assign volume=1 (uniform weight) so VWAP becomes TWAP,
        # a proper intraday average price reference.
        if result and all(c.volume == 0 for c in result):
            logger.info("No volume in data — using TWAP (uniform weights) for VWAP")
            result = [
                Candle(timestamp=c.timestamp, open=c.open, high=c.high,
                       low=c.low, close=c.close, volume=1)
                for c in result
            ]

        logger.info("Fetched %d candles for %s", len(result), trade_date)
        return result

    # ------------------------------------------------------------------
    # Direction-aware exit logic
    # ------------------------------------------------------------------
    @staticmethod
    def _update_trail(pos: _Position, candle: Candle) -> None:
        """Update trailing SL using the most favorable intra-candle price."""
        best_px = candle.high if pos.signal == "BUY_CE" else candle.low
        move    = pos.favorable_move(best_px)

        if move > pos.best_move:
            pos.best_move = move

        if not pos.breakeven_set and pos.best_move >= BREAKEVEN_POINTS:
            pos.breakeven_set = True
            pos.sl_points = 0   # SL moves to breakeven (entry level)

        if pos.best_move >= TRAIL_POINTS:
            pos.trail_active = True
            trail_sl_pts = pos.best_move - TRAIL_GAP
            if trail_sl_pts > (TRAIL_POINTS - TRAIL_GAP):
                pos.sl_points = max(pos.sl_points, -(trail_sl_pts))
                # sl_points negative means SL is above entry for CE
                # (we'll lock in some profit)
                pos.sl_points = -(trail_sl_pts)  # allow going to profit side

    @staticmethod
    def _check_exits(pos: _Position, candle: Candle) -> tuple[bool, float, str]:
        """
        Check if target or SL was hit within this candle.
        Uses the most favorable intra-candle price for target check
        and the most adverse for SL check.
        """
        BacktestEngine._update_trail(pos, candle)

        target = pos.target_nifty_level()
        sl     = pos.sl_nifty_level()

        if pos.signal == "BUY_CE":
            # Target: high hit target level
            if candle.high >= target:
                return True, round(target, 2), "TARGET_HIT"
            # SL: low hit stop level
            if candle.low <= sl:
                reason = "TRAILING_STOP" if pos.trail_active else (
                    "BREAKEVEN_EXIT" if pos.breakeven_set else "STOPLOSS_HIT"
                )
                return True, round(sl, 2), reason
        else:  # BUY_PE
            # Target: low hit target level (Nifty fell enough)
            if candle.low <= target:
                return True, round(target, 2), "TARGET_HIT"
            # SL: high hit stop level (Nifty rose against PE)
            if candle.high >= sl:
                reason = "TRAILING_STOP" if pos.trail_active else (
                    "BREAKEVEN_EXIT" if pos.breakeven_set else "STOPLOSS_HIT"
                )
                return True, round(sl, 2), reason

        return False, 0.0, ""

    @staticmethod
    def _close(
        pos: _Position, exit_px: float, exit_ts: datetime,
        reason: str, out: list[BacktestTrade],
    ) -> None:
        # Direction-aware P&L
        points = pos.favorable_move(exit_px)
        pct    = (points / pos.entry_price * 100) if pos.entry_price else 0
        est_rs = round(points * 0.5 * 75, 0)   # rough options ₹ estimate

        out.append(BacktestTrade(
            num             = pos.num,
            signal          = pos.signal,
            entry_time      = pos.entry_time.strftime("%H:%M"),
            entry_nifty     = round(pos.entry_price, 2),
            entry_reason    = pos.entry_reason,
            exit_time       = exit_ts.strftime("%H:%M"),
            exit_nifty      = round(exit_px, 2),
            exit_reason     = reason,
            points          = round(points, 2),
            pct             = round(pct, 2),
            result          = "WIN" if points > 0 else "LOSS",
            est_options_pnl = est_rs,
        ))


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------
def _build_result(
    trade_date: date,
    candles: list[Candle],
    bars: list[CandleBar],
    trades: list[BacktestTrade],
) -> dict:
    wins   = [t for t in trades if t.points > 0]
    losses = [t for t in trades if t.points <= 0]
    total_pts = round(sum(t.points for t in trades), 2)
    total_rs  = round(sum(t.est_options_pnl for t in trades), 0)

    return {
        "date":          str(trade_date),
        "total_candles": len(candles),
        "summary": {
            "total_trades":    len(trades),
            "wins":            len(wins),
            "losses":          len(losses),
            "win_rate_pct":    round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "total_points":    total_pts,
            "total_est_rs":    total_rs,
            "avg_win_points":  round(sum(t.points for t in wins)   / len(wins),   2) if wins   else 0,
            "avg_loss_points": round(sum(t.points for t in losses) / len(losses), 2) if losses else 0,
            "note": "CE profit = Nifty UP, PE profit = Nifty DOWN. Est ₹ = pts × 0.5 × 75 lot",
        },
        "trades": [
            {
                "num":             t.num,
                "signal":          t.signal,
                "entry_time":      t.entry_time,
                "entry_nifty":     t.entry_nifty,
                "entry_reason":    t.entry_reason,
                "exit_time":       t.exit_time,
                "exit_nifty":      t.exit_nifty,
                "exit_reason":     t.exit_reason,
                "points":          t.points,
                "pct":             t.pct,
                "result":          t.result,
                "est_options_pnl": t.est_options_pnl,
            }
            for t in trades
        ],
        "candles": [
            {
                "time":         b.time,
                "open":         b.open,
                "high":         b.high,
                "low":          b.low,
                "close":        b.close,
                "vwap":         b.vwap,
                "ema20":        b.ema20,
                "market_state": b.market_state,
                "signal":       b.signal,
                "in_trade":     b.in_trade,
                "note":         b.note,
            }
            for b in bars
        ],
    }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_engine = BacktestEngine()


def get_backtest_engine() -> BacktestEngine:
    return _engine
