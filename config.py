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
    "BANKNIFTY": {
        "index_token":     260105,
        "strike_interval": 100,
        "lot_size":        30,
        "ltp_symbol":      "NSE:NIFTY BANK",
        "futures_name":    "BANKNIFTY",
        "display_name":    "BANK NIFTY",
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
    # NIFTY 2.0 — Simple early-entry strategy, tight SL/target.
    # Independent state, paper-only. Built from May 2026 nifty log analysis.
    # Entries: 3 models (VWAP reclaim, opening-range breakout, pullback).
    # Risk: −10% SL, +12% target, BE at +3%, trail at +4% / 2.5% gap.
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

        # ── Entry filters (universal) ─────────────────────────────────
        "vwap_dist_min_pct":          0.10,    # below = whipsaw zone
        "vwap_dist_max_pct":          0.40,    # above = chase / overextended
        "body_min_pct":               55.0,    # full body required on signal candle
        "max_consecutive_same_dir":   4,       # 4th+ leg in same direction = SKIP

        # RSI bands (block exhaustion entries)
        "rsi_min_ce":                 50.0,
        "rsi_max_ce":                 67.0,
        "rsi_min_pe":                 33.0,
        "rsi_max_pe":                 50.0,

        # ── Model 2: Opening-Range Breakout ───────────────────────────
        "or_window_start":            (9, 15),
        "or_window_end":              (9, 30),
        "orb_window_start":           (9, 35),
        "orb_window_end":             (10, 15),  # last 45 min after OR forms
        "orb_min_vwap_dist_pct":      0.15,
        "max_orb_per_day":            1,

        # ── Model 3: Pullback ─────────────────────────────────────────
        "pullback_signal_body_min":   60.0,
        "pullback_lookback_min":      2,        # signal candle 2…4 back
        "pullback_lookback_max":      4,

        # ── Risk: tight SL/target ─────────────────────────────────────
        "sl_pct":                     10.0,     # hard SL = entry × 0.90
        "target_pct":                 12.0,     # book full at +12%
        "breakeven_at_pct":           6.0,      # SL → entry at +6% (raised from 3% — tick spikes <6% no longer lock a flat exit)
        "trail_trigger_pct":          7.0,      # trail activates at +7%
        "trail_gap_pct":              2.5,      # SL = peak × (1 − 2.5%)
        "time_stop_candles":          6,        # 30 min — only fires if pnl < 0
        "cooldown_candles":           4,        # 20 min between trades

        # ── Risk gates ────────────────────────────────────────────────
        "max_trades_per_day":         2,
        "skip_second_after_hard_sl":  True,

        # ── Time windows (IST) ────────────────────────────────────────
        "entry_window_start":         (9, 35),
        "entry_window_end":           (13, 30),  # last entry
        "force_exit_hhmm":            (15, 15),
    },
    # ------------------------------------------------------------------
    # BankNifty 2.0 — high-precision standalone engine.
    # Shares BankNifty's index/futures tokens but runs independent state,
    # strategy (Models A/B/C/D), and 3-layer exits. Always PAPER mode.
    # ------------------------------------------------------------------
    "BANKNIFTY_2": {
        # Market identity — same underlying as BANKNIFTY
        "index_token":     260105,
        "strike_interval": 100,
        "lot_size":        30,
        "ltp_symbol":      "NSE:NIFTY BANK",
        "futures_name":    "BANKNIFTY",
        "display_name":    "BANK NIFTY 2.0",

        # Hard mode lock — v2 always paper-trades regardless of TRADING_MODE env var.
        "force_paper_mode": True,

        # ── Day classification (computed at 09:50) ────────────────────
        "day_class_gap_min_pct":      0.30,   # ≥0.30% gap for TREND/REVERSAL
        "day_class_drift_min_pct":    0.25,   # VWAP drift at 09:50
        "day_class_or_min_pts":       150,    # opening-range height min
        "day_class_reversal_or_pts":  200,    # reversal-day OR height

        # ── DO-NOT-ENTER filters ──────────────────────────────────────
        "max_body_pct":               85.0,   # body >85% = climax → skip
        "max_vol_ratio":              4.0,    # vol_ratio >4× = climactic exhaustion (was 3.0; raised after 5-day backtest showed institutional flow being rejected)
        "max_consecutive_same_dir":   5,      # 6th+ leg in same direction = SKIP (was 3; BN trends in 5–8 candle clusters)
        "rsi_max_ce_entry":           68.0,   # RSI >68 blocks CE
        "rsi_min_pe_entry":           32.0,   # RSI <32 blocks PE
        "min_breakout_margin_pts":    30,     # break must clear prior swing by ≥30 pts (was 50)
        "min_session_range_pts":      150,    # if range <150 pts after 11:00 → SKIP
        "vwap_whipsaw_band_pct":      0.10,   # ±0.10% around VWAP = whipsaw zone

        # ── Model A — Compression breakout ────────────────────────────
        "model_a_lookback":           4,      # last 4 candles
        "model_a_max_range_pts":      80,     # max_high - min_low ≤ 80
        "model_a_max_body_pct":       60.0,   # all bodies ≤ 60% (was 50; BN noise routinely prints 55–65% bodies in compressions)
        "model_a_max_vol_ratio_avg":  0.95,   # avg vol ratio ≤ 0.95× (was 0.85)
        "model_a_max_vol_ratio_any":  1.0,    # no candle >1.0× vol
        "model_a_rsi_min":            45.0,
        "model_a_rsi_max":            60.0,
        "model_a_vwap_bias_pct":      0.10,   # range edge ≥0.10% from VWAP
        "model_a_break_offset_pts":   8,      # limit order: break ± 8 pts
        "model_a_min_break_vol_ratio":1.3,    # confirmation candle vol ≥1.3×
        "model_a_setup_ttl_candles":  8,      # cancel pending if 8 candles pass (was 6)

        # ── Model B — First pullback ──────────────────────────────────
        "model_b_signal_body_min":    55.0,
        "model_b_signal_body_max":    85.0,
        "model_b_signal_vol_min":     1.3,
        "model_b_signal_vol_max":     2.5,
        "model_b_ema_slope_min_pts":  15,     # EMA20 ≥15 pts over 5 candles
        "model_b_max_pullback_pct":   60.0,   # pullback ≤60% retrace
        "model_b_max_pullback_candles": 3,    # ≤3 candles wait

        # ── Model C — Liquidity sweep reversal ────────────────────────
        "model_c_swing_lookback":     6,      # 30-min swing (6 × 5-min)
        "model_c_min_sweep_pts":      20,     # wick beyond swing by ≥20 pts
        "model_c_min_wick_frac":      0.50,   # long wick ≥50% of candle range
        "model_c_min_sweep_vol":      1.4,
        "model_c_prior_trend_candles":3,      # prior 3 candles in swept direction
        "model_c_min_confirm_vol":    0.8,    # confirmation candle vol ≥0.8×

        # ── Model D — Flag continuation ───────────────────────────────
        "model_d_impulse_min_pts":    100,
        "model_d_impulse_max_candles":3,
        "model_d_impulse_body_min":   60.0,
        "model_d_impulse_vol_min":    1.5,
        "model_d_flag_min_candles":   3,
        "model_d_flag_max_candles":   6,
        "model_d_flag_body_max":      60.0,
        "model_d_break_body_min":     50.0,
        "model_d_break_body_max":     85.0,
        "model_d_break_vol_min":      1.2,

        # ── Exit Layer 1 — Stop loss ──────────────────────────────────
        "atm_delta_estimate":         0.55,   # used to translate spot SL → premium SL
        "sl_pct_cap_hard":            18.0,   # absolute max premium SL
        "sl_pct_floor":               8.0,    # absolute min premium SL
        "sl_early_tighten_candles":   6,      # candles until early tighten check
        "sl_early_tighten_min_gain":  8.0,    # need ≥8% gain by candle 6, else tighten
        "sl_early_tighten_to_pct":    -6.0,   # tighten SL to entry × 0.94

        # ── Exit Layer 2 — Partial booking + time-bucket targets ──────
        "partial_book_pct":           50,     # book 50% at bucket target
        "partial_target_morning":     18.0,   # entry 09:50–11:30
        "partial_target_midday":      15.0,   # entry 11:30–13:00
        "partial_target_afternoon":   12.0,   # entry 13:00–14:00
        "ceiling_morning_pct":        40.0,
        "ceiling_midday_pct":         30.0,
        "ceiling_afternoon_pct":      20.0,

        # Runner trailing (kicks in after partial booked, +20% on remaining)
        "runner_trail_trigger_pct":   20.0,
        "runner_trail_gaps": [        # (pnl_threshold, trail_gap_pct)
            (20.0, 8.0),
            (30.0, 6.0),
            (45.0, 5.0),
            (60.0, 4.0),
        ],

        # ── Exit Layer 3 — Failure detection ──────────────────────────
        "stall_max_candles":          6,
        "stall_max_profit_pct":       8.0,    # if max < 8% at candle 6 → stall
        "stall_current_max_pct":      2.0,    # AND current ≤ 2% → exit
        "stagnation_candles":         12,     # 12 candles no ≥12% gain → exit
        "stagnation_min_gain_pct":    12.0,

        # ── Risk gates ────────────────────────────────────────────────
        "max_trades_per_day":         2,
        "daily_loss_lock_rupees":    -6000.0, # realised ≤ this → lock day
        "daily_profit_lock_rupees":  15000.0, # realised ≥ this → lock day (protect win)
        "skip_second_after_sl":       True,
        "skip_second_after_stall":    True,
        "second_trade_cooldown_min":  45,
        "second_trade_half_size":     True,

        # ── Time windows (IST) ────────────────────────────────────────
        "observe_until_hhmm":         (9, 45),   # OR locks at 09:45
        "entry_window_start":         (9, 50),
        "prime_window_end":           (11, 30),
        "standard_window_end":        (13, 0),
        "caution_window_end":         (14, 0),   # last entry
        "force_exit_hhmm":            (15, 15),
    },
    # ------------------------------------------------------------------
    # NIFTY FUTURES — trades the NIFTY future DIRECTLY (long/short), not options.
    # Strategy: hardened Opening-Range Breakout (ORB). SL/target are in INDEX
    # POINTS (not premium %). Always PAPER. Config = output of the multi-agent
    # backtest + ORB-hardening run on Apr–May 2026 futures 5-min data:
    #   full PF 1.77, OOS PF 1.57, 17 trades, maxDD −₹2,530. Fixed risk-reward
    #   exit (no trailing). PAPER-FORWARD-TEST until it accrues 30+ live trades.
    # ------------------------------------------------------------------
    "NIFTY_FUT": {
        # Market identity — trade the future directly; index token is only for
        # the official spot level. Candles are built from the futures contract
        # (real volume, required by the ORB volume filter).
        "index_token":     256265,
        "futures_name":    "NIFTY",
        "ltp_symbol":      "NSE:NIFTY 50",
        "display_name":    "NIFTY Futures (ORB)",
        "lot_size":        65,            # fallback; real lot size read from contract

        # Hard mode lock — always paper.
        "force_paper_mode": True,

        # ── ORB entry (hardened config) ───────────────────────────────
        "or_bars":          3,            # opening range = first 3 bars (09:15–09:25)
        "buffer_pct":       0.07,         # close must clear OR by 0.07% (plateau center)
        "vol_mult":         1.3,          # breakout bar volume ≥ 1.3× rolling baseline
        "vol_lookback":     5,            # bars for rolling volume baseline
        "body_frac":        0.60,         # |close-open| ≥ 60% of bar range
        "rsi_cap":          72,           # LONG only if RSI ≤ 72; SHORT only if RSI ≥ 28
        "entry_window_start": (9, 35),
        "entry_window_end":   (11, 30),   # last breakout entry
        "max_trades_per_day": 1,          # ORB takes one breakout direction per day

        # ── Risk: FIXED risk-reward in index POINTS (no trailing) ─────
        "sl_points":        30.0,         # hard SL = entry ∓ 30 pts
        "target_points":    70.0,         # target  = entry ± 70 pts
        "force_exit_hhmm":  (15, 15),
    },
}
