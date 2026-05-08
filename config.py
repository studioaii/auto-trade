import os
from dotenv import load_dotenv

load_dotenv()

API_KEY      = os.getenv("API_KEY", "")
API_SECRET   = os.getenv("API_SECRET", "")
REDIRECT_URL = os.getenv("REDIRECT_URL", "http://127.0.0.1:8000/callback")
TRADING_MODE = os.getenv("TRADING_MODE", "PAPER")   # "PAPER" or "LIVE"

if not API_KEY or not API_SECRET:
    raise EnvironmentError("API_KEY and API_SECRET must be set in environment variables")


# Per-instrument configuration.
# v3 keys are additive — defaults preserve legacy behaviour
# (entry_mode="vwap_ema_breakout", no day-bias/VIX/event gating, partial-book disabled).
# Flip individual keys per phase rollout — see plans/along-the-above-plan-validated-wozniak.md.
INSTRUMENT_CONFIG = {
    "NIFTY": {
        # ── identity ─────────────────────────────────────────────────────
        "index_token":          256265,
        "strike_interval":      50,
        "lot_size":             65,
        "ltp_symbol":           "NSE:NIFTY 50",
        "futures_name":         "NIFTY",
        "display_name":         "NIFTY 50",

        # ── strategy variant dispatcher ──────────────────────────────────
        # "vwap_ema_breakout" (legacy) | "trend_pullback" | "mean_reversion"
        # Phase 7 flip: NIFTY → trend_pullback. Other gates (partial booking,
        # spot SL, VIX, 1/day cap, ITM strikes, 14:30 force-exit) deferred to
        # Phase 9. This commit only changes entry-signal generation.
        "entry_mode":           "trend_pullback",

        # ── legacy v2 thresholds (read by vwap_ema_breakout) ────────────
        "rsi_min_ce":           50,
        "rsi_max_ce":           100,
        "rsi_min_pe":           0,
        "rsi_max_pe":           50,
        "vwap_dist_min_pct":    0.15,
        "efficiency_min_ce":    0.45,
        "efficiency_min_pe":    0.45,

        # ── trend-pullback (NIFTY v3) ───────────────────────────────────
        "vwap_hold_min_candles": 6,        # ≥30 min on bias side
        "pullback_retrace_pct":  0.20,     # within 0.20% of VWAP/EMA
        "pullback_rsi_low":      45,
        "pullback_rsi_high":     55,
        "pullback_vol_max_ratio": 0.85,    # low-volume dip
        "resume_rsi_min_ce":     55,
        "resume_rsi_max_ce":     70,       # NEW upper cap
        "resume_rsi_min_pe":     30,
        "resume_rsi_max_pe":     45,
        "resume_vwap_dist_min":  0.20,
        "resume_vwap_dist_max":  0.80,
        "resume_body_pct":       50,
        "resume_vol_surge_ratio": 1.5,
        "ema_gap_min":           0.05,
        "ema_gap_max":           0.40,
        "ema_period_secondary":  9,        # 9-EMA for pullback structure
        "range_anomaly_mult":    1.5,
        "spike_threshold_pct":   0.60,

        # ── day-bias classifier (bias_* prefix to avoid collision w/ legacy) ─
        "gap_pct_min":               0.10,
        "bias_gap_pct_no_trade":     0.80,
        "bias_opening_rsi_ob":       78,
        "bias_opening_rsi_os":       22,
        "bias_opening_efficiency_min": 0.35,
        "bias_rsi_min_up":           55,
        "bias_rsi_max_up":           73,
        "bias_rsi_min_down":         27,
        "bias_rsi_max_down":         45,

        # ── VIX gate (Phase 9 active) ──────────────────────────────────
        "vix_max":               22.0,

        # ── strike selection (Phase 9: 1-strike ITM) ───────────────────
        "strike_offset_ce":      -1,       # CE = ATM-50 (1 strike ITM)
        "strike_offset_pe":      1,        # PE = ATM+50 (1 strike ITM)

        # ── SL / risk (Phase 9 active) ─────────────────────────────────
        "sl_spot_pct":           0.25,     # 0.25% spot move against entry
        "sl_premium_pct":        20.0,     # safety-net premium SL
        "time_stop_min":         15,       # no-progress exit window
        "force_exit_time":       "14:30",  # tighter than legacy 15:20
        "max_trades_per_day":    1,        # hard cap

        # ── partial booking (Phase 9 active) ───────────────────────────
        "partial_book_enabled":  True,
        "partial_book_1_pct":    7.0,
        "partial_book_1_size":   0.50,
        "partial_book_2_pct":    14.0,
        "partial_book_2_size":   0.30,
        "trail_gap_pct":         4.0,
        "min_lots_for_partial_book": 3,

        # ── hold ceiling (Phase 9 active) ──────────────────────────────
        "hold_ceiling_min":      180,      # NIFTY 3 hours
    },

    "BANKNIFTY": {
        # ── identity ─────────────────────────────────────────────────────
        "index_token":          260105,
        "strike_interval":      100,
        "lot_size":             30,
        "ltp_symbol":           "NSE:NIFTY BANK",
        "futures_name":         "BANKNIFTY",
        "display_name":         "BANK NIFTY",

        # ── strategy variant dispatcher ──────────────────────────────────
        # Phase 8 flip: BNF → mean_reversion (fade-failed-spike).
        "entry_mode":           "mean_reversion",

        # ── legacy v2 thresholds ────────────────────────────────────────
        "rsi_min_ce":           50,
        "rsi_max_ce":           100,
        "rsi_min_pe":           0,
        "rsi_max_pe":           50,
        "vwap_dist_min_pct":    0.15,
        "efficiency_min_ce":    0.45,
        "efficiency_min_pe":    0.45,

        # ── mean-reversion (BNF v3) ─────────────────────────────────────
        "bnf_spike_window_candles":  3,    # 15-min window
        "bnf_spike_pct":             0.60, # min distance from VWAP
        "bnf_spike_vol_surge":       2.5,
        "bnf_fade_rsi_overbought":   70,
        "bnf_fade_rsi_oversold":     30,
        "fade_wick_min_pct":         40,
        "fade_body_min_pct":         40,
        "failed_reversion_max":      2,
        "vwap_retest_target_pct":    70,
        "range_anomaly_mult":        1.5,
        "spike_threshold_pct":       0.60,

        # ── day-bias classifier (bias_* prefix to avoid collision w/ legacy) ─
        "gap_pct_min":               0.10,
        "bias_gap_pct_no_trade":     1.00,    # BNF more volatile → wider tolerance
        "bias_opening_rsi_ob":       78,
        "bias_opening_rsi_os":       22,
        "bias_opening_efficiency_min": 0.35,
        "bias_rsi_min_up":           55,
        "bias_rsi_max_up":           73,
        "bias_rsi_min_down":         27,
        "bias_rsi_max_down":         45,

        # ── VIX gate (Phase 9 active) ──────────────────────────────────
        "vix_max":               22.0,

        # ── strike selection (Phase 9: 1-strike ITM) ───────────────────
        "strike_offset_ce":      -1,       # CE = ATM-100 (1 strike ITM)
        "strike_offset_pe":      1,        # PE = ATM+100 (1 strike ITM)

        # ── SL / risk (Phase 9 active) ─────────────────────────────────
        "sl_spot_pct":           0.35,     # BNF more volatile → wider
        "sl_premium_pct":        20.0,
        "time_stop_min":         10,       # BNF reverts faster → tighter
        "force_exit_time":       "14:30",
        "max_trades_per_day":    1,

        # ── partial booking (Phase 9 active) ───────────────────────────
        "partial_book_enabled":  True,
        "partial_book_1_pct":    7.0,
        "partial_book_1_size":   0.50,
        "partial_book_2_pct":    14.0,
        "partial_book_2_size":   0.30,
        "trail_gap_pct":         4.0,
        "min_lots_for_partial_book": 3,

        # ── hold ceiling (Phase 9 active) ──────────────────────────────
        "hold_ceiling_min":      90,       # BNF reverts within 90 min
    },
}
