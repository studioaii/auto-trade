import logging
from datetime import date, timedelta
from typing import Optional
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

# Nifty 50 index instrument token — static, never changes on NSE/Kite
NIFTY_INDEX_TOKEN = 256265
NIFTY_STRIKE_INTERVAL = 50   # Nifty options strike gap

# BankNifty index instrument token
BANKNIFTY_INDEX_TOKEN = 260105
BANKNIFTY_STRIKE_INTERVAL = 100   # BankNifty options strike gap


# ---------------------------------------------------------------------------
# Generic helpers (instrument-agnostic)
# ---------------------------------------------------------------------------

def fetch_instruments(kite: KiteConnect, instrument_name: str) -> list[dict]:
    """
    Downloads full NFO instrument dump and filters to current-week
    CE/PE options for the given instrument name (e.g. "NIFTY", "BANKNIFTY").
    """
    logger.info("Fetching NFO instruments for %s (this may take 2-3s)...", instrument_name)
    all_instruments = kite.instruments("NFO")

    expiry = get_current_expiry_for_instrument(all_instruments, instrument_name)
    logger.info("%s target expiry: %s", instrument_name, expiry)

    filtered = [
        inst for inst in all_instruments
        if inst["name"] == instrument_name
        and inst["instrument_type"] in ("CE", "PE")
        and inst["expiry"] == expiry
    ]

    logger.info("Found %d %s option instruments for expiry %s", len(filtered), instrument_name, expiry)
    return filtered


def get_current_expiry_for_instrument(instruments: list[dict], instrument_name: str) -> date:
    """
    Find the nearest upcoming expiry for any instrument from live instrument data.
    On expiry day itself, skips to the next expiry (near-zero time value).
    """
    today = date.today()
    expiries = sorted(set(
        inst["expiry"] for inst in instruments
        if inst["name"] == instrument_name
        and inst["instrument_type"] in ("CE", "PE")
        and inst["expiry"] >= today
    ))
    if not expiries:
        raise ValueError(f"No upcoming {instrument_name} expiries found in instrument data")

    if expiries[0] == today:
        if len(expiries) < 2:
            raise ValueError(f"On expiry day but no next {instrument_name} expiry found")
        logger.info(
            "Today is expiry day (%s) — switching to next expiry: %s", today, expiries[1]
        )
        return expiries[1]

    return expiries[0]


def get_atm_strike(spot_price: float, strike_interval: int = NIFTY_STRIKE_INTERVAL) -> int:
    """Round spot price to nearest strike interval."""
    return round(spot_price / strike_interval) * strike_interval


def find_option_instrument(
    instruments: list[dict],
    expiry: date,
    strike: int,
    option_type: str,   # "CE" or "PE"
) -> dict:
    """
    Find the exact instrument for the given strike/expiry/type.
    Raises ValueError if not found.
    """
    matches = [
        inst for inst in instruments
        if inst["expiry"] == expiry
        and int(inst["strike"]) == strike
        and inst["instrument_type"] == option_type
    ]
    if not matches:
        raise ValueError(
            f"No instrument found for {strike} {option_type} expiry {expiry}. "
            f"Available strikes: {sorted(set(int(i['strike']) for i in instruments if i['instrument_type'] == option_type))[:10]}..."
        )
    return matches[0]


def find_futures(kite: KiteConnect, instrument_name: str) -> dict:
    """
    Find the nearest-expiry futures contract for any instrument from NFO.
    Futures have real volume & open interest — ideal for candle building.
    """
    today = date.today()
    all_nfo = kite.instruments("NFO")

    futures = [
        inst for inst in all_nfo
        if inst["name"] == instrument_name
        and inst["instrument_type"] == "FUT"
        and inst["expiry"] >= today
    ]

    if not futures:
        raise ValueError(f"No active {instrument_name} futures found in NFO instruments")

    futures.sort(key=lambda x: x["expiry"])
    fut = futures[0]

    logger.info(
        "%s Futures: %s | token=%s | expiry=%s | lot_size=%s",
        instrument_name, fut["tradingsymbol"], fut["instrument_token"],
        fut["expiry"], fut.get("lot_size"),
    )
    return fut


# ---------------------------------------------------------------------------
# Nifty-specific wrappers (backward compat)
# ---------------------------------------------------------------------------

def fetch_nifty_instruments(kite: KiteConnect) -> list[dict]:
    return fetch_instruments(kite, "NIFTY")


def get_current_expiry_from_instruments(instruments: list[dict]) -> date:
    return get_current_expiry_for_instrument(instruments, "NIFTY")


def get_current_expiry(instruments: list[dict]) -> date:
    return get_current_expiry_for_instrument(instruments, "NIFTY")


def get_nifty_index_token() -> int:
    return NIFTY_INDEX_TOKEN


def find_nifty_futures(kite: KiteConnect) -> dict:
    return find_futures(kite, "NIFTY")


# ---------------------------------------------------------------------------
# BankNifty-specific wrappers
# ---------------------------------------------------------------------------

def fetch_banknifty_instruments(kite: KiteConnect) -> list[dict]:
    return fetch_instruments(kite, "BANKNIFTY")


def get_banknifty_index_token() -> int:
    return BANKNIFTY_INDEX_TOKEN


def find_banknifty_futures(kite: KiteConnect) -> dict:
    return find_futures(kite, "BANKNIFTY")
