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
    },
    "BANKNIFTY": {
        "index_token":    260105,
        "strike_interval": 100,
        "lot_size":        30,
        "ltp_symbol":     "NSE:NIFTY BANK",
        "futures_name":   "BANKNIFTY",
        "display_name":   "BANK NIFTY",
    },
}
