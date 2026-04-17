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
        "index_token":    256265,
        "strike_interval": 50,
        "lot_size":        65,
        "ltp_symbol":     "NSE:NIFTY 50",
        "futures_name":   "NIFTY",
        "display_name":   "NIFTY 50",
        # Strategy quality filters (tuned from Apr-13/15/16/17 data)
        "rsi_min_ce":           50,    # RSI floor for CE entry
        "rsi_max_ce":           72,    # RSI cap  for CE — overbought guard
        "rsi_min_pe":           28,    # RSI floor for PE — oversold guard
        "rsi_max_pe":           50,    # RSI cap  for PE entry
        "vwap_dist_min_pct":    0.20,  # min % distance from VWAP (was 0.15)
        "price_ema_gap_min_ce": 0.05,  # price must be ≥0.05% above EMA20
        "price_ema_gap_max_ce": 0.35,  # price must be ≤0.35% above EMA20
        "price_ema_gap_min_pe": 0.10,  # price must be ≥0.10% below EMA20
        "efficiency_min_ce":    0.45,
        "efficiency_min_pe":    0.60,
    },
    "BANKNIFTY": {
        "index_token":    260105,
        "strike_interval": 100,
        "lot_size":        30,
        "ltp_symbol":     "NSE:NIFTY BANK",
        "futures_name":   "BANKNIFTY",
        "display_name":   "BANK NIFTY",
        # Strategy quality filters (tuned from Apr-13/15/16/17 BankNifty data)
        "rsi_min_ce":             50,    # RSI floor for CE
        "rsi_max_ce":             70,    # RSI cap  for CE — overbought guard
        "rsi_min_pe":             30,    # RSI floor for PE — oversold guard (BN reverses fast)
        "rsi_max_pe":             48,    # RSI cap  for PE
        "vwap_dist_min_pct":      0.15,  # keep original — Apr-17 WIN had only 0.15%
        "opening_rsi_overbought": 80,    # opening RSI > 80 → block ALL trades that day
        "opening_rsi_oversold":   25,    # opening RSI < 25 → block ALL trades that day
        "price_ema_gap_min_ce":   0.05,  # price must be ≥0.05% above EMA20
        "price_ema_gap_max_ce":   0.40,  # price must be ≤0.40% above EMA20
        "price_ema_gap_min_pe":   0.10,  # price must be ≥0.10% below EMA20
        "price_ema_gap_max_pe":   0.40,  # price must be ≤0.40% below EMA20 — Apr-16 was 0.54% (BLOCKED)
        "vwap_dist_max_pe_pct":   0.50,  # PE: price must not be >0.50% below VWAP — Apr-16 was 0.65% (BLOCKED)
        "efficiency_min_ce":      0.55,
        "efficiency_min_pe":      0.55,
    },
}
