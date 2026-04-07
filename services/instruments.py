import logging
from datetime import date, timedelta
from typing import Optional
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

# Nifty 50 index instrument token — static, never changes on NSE/Kite
NIFTY_INDEX_TOKEN = 256265
NIFTY_STRIKE_INTERVAL = 50   # Nifty options strike gap


def fetch_nifty_instruments(kite: KiteConnect) -> list[dict]:
    """
    Downloads full NFO instrument dump and filters to current-week
    Nifty CE/PE options only (~100 rows).
    Caches nothing — caller should store the result.
    """
    logger.info("Fetching NFO instruments from Kite (this may take 2-3s)...")
    all_instruments = kite.instruments("NFO")

    expiry = get_current_expiry_from_instruments(all_instruments)
    logger.info("Target expiry: %s", expiry)

    filtered = [
        inst for inst in all_instruments
        if inst["name"] == "NIFTY"
        and inst["instrument_type"] in ("CE", "PE")
        and inst["expiry"] == expiry
    ]

    logger.info("Found %d Nifty option instruments for expiry %s", len(filtered), expiry)
    return filtered


def get_current_expiry_from_instruments(instruments: list[dict]) -> date:
    """
    Find the nearest upcoming Thursday expiry from the actual instrument data.
    This is more reliable than date arithmetic since exchanges can change expiry days.

    On expiry day itself, skip to the next expiry — same-day options have near-zero
    time value, making premiums too small for the dynamic SL calculation to work.
    """
    today = date.today()
    nifty_expiries = sorted(set(
        inst["expiry"] for inst in instruments
        if inst["name"] == "NIFTY"
        and inst["instrument_type"] in ("CE", "PE")
        and inst["expiry"] >= today
    ))
    if not nifty_expiries:
        raise ValueError("No upcoming Nifty expiries found in instrument data")

    # Skip today's expiry — use next week's options instead
    if nifty_expiries[0] == today:
        if len(nifty_expiries) < 2:
            raise ValueError("On expiry day but no next expiry found in instrument data")
        logger.info("Today is expiry day (%s) — switching to next expiry: %s", today, nifty_expiries[1])
        return nifty_expiries[1]

    return nifty_expiries[0]


def get_current_expiry(instruments: list[dict]) -> date:
    return get_current_expiry_from_instruments(instruments)


def get_atm_strike(spot_price: float) -> int:
    """Round spot price to nearest Nifty strike interval (50)."""
    return round(spot_price / NIFTY_STRIKE_INTERVAL) * NIFTY_STRIKE_INTERVAL


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
            f"No instrument found for NIFTY {strike} {option_type} expiry {expiry}. "
            f"Available strikes: {sorted(set(int(i['strike']) for i in instruments if i['instrument_type'] == option_type))[:10]}..."
        )
    return matches[0]


def get_nifty_index_token() -> int:
    return NIFTY_INDEX_TOKEN


def find_nifty_futures(kite: KiteConnect) -> dict:
    """
    Find the current-month Nifty futures contract from NFO instruments.
    Futures have real volume & open interest — ideal for candle building.
    Returns the instrument dict with keys: instrument_token, tradingsymbol, expiry, lot_size, etc.
    """
    today = date.today()
    all_nfo = kite.instruments("NFO")

    # Filter to NIFTY FUT contracts with expiry >= today
    nifty_futures = [
        inst for inst in all_nfo
        if inst["name"] == "NIFTY"
        and inst["instrument_type"] == "FUT"
        and inst["expiry"] >= today
    ]

    if not nifty_futures:
        raise ValueError("No active Nifty futures found in NFO instruments")

    # Pick the nearest expiry (current-month contract — most liquid)
    nifty_futures.sort(key=lambda x: x["expiry"])
    fut = nifty_futures[0]

    logger.info(
        "Nifty Futures: %s | token=%s | expiry=%s | lot_size=%s",
        fut["tradingsymbol"], fut["instrument_token"], fut["expiry"], fut.get("lot_size")
    )
    return fut
