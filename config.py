import os
from dotenv import load_dotenv

load_dotenv()

API_KEY      = os.getenv("API_KEY", "")
API_SECRET   = os.getenv("API_SECRET", "")
REDIRECT_URL = os.getenv("REDIRECT_URL", "http://127.0.0.1:8000/callback")
TRADING_MODE = os.getenv("TRADING_MODE", "PAPER")   # "PAPER" or "LIVE"

if not API_KEY or not API_SECRET:
    raise EnvironmentError("API_KEY and API_SECRET must be set in environment variables")

# Per-instrument configuration — add new instruments here
INSTRUMENT_CONFIG = {
    "NIFTY": {
        "index_token":     256265,
        "strike_interval": 50,
        "lot_size":        65,
        "ltp_symbol":      "NSE:NIFTY 50",
        "futures_name":    "NIFTY",
        "display_name":    "NIFTY 50",
        # v2 strategy parameters
        "rsi_min_ce":      50,     # CE: RSI > 50
        "rsi_max_ce":      100,    # no overbought cap
        "rsi_min_pe":      0,      # no oversold floor
        "rsi_max_pe":      50,     # PE: RSI < 50
        "vwap_dist_min_pct": 0.15, # ≥0.15% from VWAP
        "efficiency_min_ce": 0.45,
        "efficiency_min_pe": 0.45,
    },
    # ------------------------------------------------------------------
    # NIFTY 2.0 — v1 VWAP+EMA breakout (wide-tail trend-rider) + the
    # improvements from the June/July-2026 NIFTY-1.0 log analysis. Independent
    # state, paper-only. This is NIFTY 1.0's proven strategy PLUS:
    #   • Session chop gate + a regime-stability gate that
    #     rejects wick-poke / range-top breakouts (close-confirmed break).
    #   • Softened opposite-signal exit: needs 2 consecutive closes on the
    #     wrong side of VWAP before it fires (vs v1's single candle).
    #   • Risk unchanged from v1: −18% hard SL, trailing from +15%. No
    #     fixed target / breakeven / time-stop (those kill the fat tail).
    #   • Full instrumentation: tick MAE/MFE, post-exit option path, and
    #     would-be P&L for every signal a gate blocked (shadow log).
    # ------------------------------------------------------------------
    "NIFTY_2": {
        "index_token":     256265,
        "strike_interval": 50,
        "lot_size":        65,
        "ltp_symbol":      "NSE:NIFTY 50",
        "futures_name":    "NIFTY",
        "display_name":    "NIFTY 2.0",

        # Hard mode lock — v2 always paper.
        "force_paper_mode": True,
        "max_lots":        1,        # never scale size — edge is unproven (t≈0.22)

        # ── Entry signal thresholds (v1 VWAP+EMA breakout) ────────────
        # RSI bands match v1 EXACTLY — NO overbought cap (loser/winner RSI
        # don't separate; an upper cap only clips winners).
        "rsi_min_ce":          50,
        "rsi_max_ce":          100,
        "rsi_min_pe":          0,
        "rsi_max_pe":          50,
        "vwap_dist_min_pct":   0.15,   # ≥0.15% from VWAP (reversal-zone guard)
        "efficiency_min_ce":   0.45,
        "efficiency_min_pe":   0.45,

        # ── Entry window ──────────────────────────────────────────────
        # 11:00 morning wall REMOVED 2026-07-02: forward test (06-18..06-30)
        # showed it blocked only winners (+8.7%, +33.9% v1-actual) while the
        # historically weak bucket (11:00–12:00) sat INSIDE the wall. Time
        # cuts proved unstable out-of-sample — rely on mechanism gates below.
        "entry_window_start":  (9, 50),   # v1 earliest entry
        "entry_window_end":    (14, 0),   # v1 last-entry time (unchanged)
        # Regime-stability gate (mechanism, not a clock): reject the
        # range-top wick-poke breakouts that fail immediately.
        "require_close_breakout":     True,   # close (not just high) must clear the swing
        "breakout_lookback":          3,      # swing = prior N candles before the signal
        "breakout_margin_pct":        0.02,   # close must clear the swing by ≥0.02% of price
                                              # (~5 pts on NIFTY — filters wick-pokes, not grinds.
                                              #  WATCH: blocked a +67% runner on 06-23; if it costs
                                              #  another tail winner, drop margin to 0 and keep only
                                              #  the close-confirmation)
        "regime_vwap_lookback":       5,      # window for recent-window VWAP-crossing chop check
        "regime_max_vwap_crossings":  2,      # ≥2 crossings in the window = chop → block
        # Session-cumulative chop gate (ADDED 2026-07-02, n=35 v1 study):
        # once today's candle closes have flipped sides of VWAP ≥ N times,
        # block all further entries for the day (crossings only accumulate,
        # so this is a natural day-kill switch). In-sample: dropped 9 trades
        # worth −₹9,529 (22% WR), monotone across thresholds 4–8, LOO-stable,
        # permutation p=0.021. Pre-registered at 6 — judge via the shadow log
        # (shadow_signals_nifty_2.csv), do NOT retune on the same sample.
        "session_max_vwap_crossings": 6,      # 0 disables the gate

        # ── Tier-B improvement #2: softened OPPOSITE-SIGNAL exit ──────
        "opposite_exit_confirm_closes": 2,    # need 2 consecutive wrong-side-VWAP closes

        # ── Risk: v1 wide-tail (−18% SL + trailing, no target) ────────
        "sl_pct":              18.0,   # hard SL = entry × 0.82 (v1 value, already live)
        "trail_trigger_pct":   15.0,   # trailing activates at +15%
        "trail_gap_base_pct":  6.0,    # starting gap (% below peak) at +15%
        "trail_gap_step_pct":  1.0,    # gap tightens 1% per additional +10% gain
        "trail_gap_min_pct":   3.0,    # gap floor

        # ── Risk gates ────────────────────────────────────────────────
        "max_trades_per_day":         2,
        "skip_second_after_hard_sl":  True,    # block 2nd entry if 1st hit hard SL
        "cooldown_candles":           0,        # v1 has no cooldown between trades
        "force_exit_hhmm":            (15, 20), # v1 force-exit time

        # ── Instrumentation ───────────────────────────────────────────
        "post_exit_track_candles":    8,        # log option max/min for 8 candles after exit
        "max_shadow_trackers":        6,        # cap concurrent would-be-signal trackers
    },
}
