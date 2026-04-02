import logging
from fastapi import APIRouter, HTTPException
from kiteconnect.exceptions import TokenException, NetworkException
from services.kite_service import require_authenticated_client

logger = logging.getLogger(__name__)
router = APIRouter(tags=["trading"])


def _handle_kite_call(fn):
    """Execute a Kite API call with unified error handling."""
    try:
        return fn()
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except TokenException as e:
        raise HTTPException(
            status_code=401,
            detail=f"Token expired or invalid. Please re-authenticate via /login. ({e})",
        )
    except NetworkException as e:
        raise HTTPException(status_code=503, detail=f"Kite API unreachable: {e}")
    except Exception as e:
        logger.error("Kite API error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profile")
def get_profile():
    """Fetch logged-in user's profile."""
    def call():
        kite = require_authenticated_client()
        return kite.profile()

    return _handle_kite_call(call)


@router.get("/holdings")
def get_holdings():
    """Fetch equity holdings."""
    def call():
        kite = require_authenticated_client()
        return kite.holdings()

    return _handle_kite_call(call)


@router.get("/positions")
def get_positions():
    """Fetch current day and net positions."""
    def call():
        kite = require_authenticated_client()
        return kite.positions()

    return _handle_kite_call(call)


@router.get("/orders")
def get_orders():
    """Fetch list of orders for the day."""
    def call():
        kite = require_authenticated_client()
        return kite.orders()

    return _handle_kite_call(call)
