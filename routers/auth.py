import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from services.kite_service import get_login_url, generate_session, get_stored_token, clear_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


@router.get("/login")
def login():
    """Redirect user to Zerodha login page."""
    url = get_login_url()
    logger.info("Redirecting to Zerodha login: %s", url)
    return RedirectResponse(url=url)


@router.get("/callback")
def callback(request_token: str, status: str = "", action: str = "", type: str = ""):
    """
    Zerodha redirects here after login.
    Exchanges request_token for access_token, then redirects to dashboard.
    """
    try:
        data = generate_session(request_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info("Login successful for user: %s (%s)", data.get("user_name"), data.get("user_id"))
    return RedirectResponse(url="/?login=success")


@router.get("/logout")
def logout():
    """Clear stored access token."""
    if not get_stored_token():
        raise HTTPException(status_code=400, detail="No active session to logout from")
    clear_token()
    return {"status": "success", "message": "Logged out successfully"}
