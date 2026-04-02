import json
import logging
import os
from kiteconnect import KiteConnect
from config import API_KEY, API_SECRET

logger = logging.getLogger(__name__)

_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", ".kite_session.json")

# In-memory token store (single user)
_token_store: dict = {
    "access_token": None,
    "public_token": None,
    "user_id": None,
}


def _load_token_from_file() -> None:
    """Load persisted token on startup so re-login isn't needed after a reload."""
    try:
        with open(_TOKEN_FILE) as f:
            data = json.load(f)
        _token_store["access_token"] = data.get("access_token")
        _token_store["public_token"] = data.get("public_token")
        _token_store["user_id"]      = data.get("user_id")
        if _token_store["access_token"]:
            logger.info("Restored Kite session for user: %s", _token_store["user_id"])
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _save_token_to_file() -> None:
    try:
        with open(_TOKEN_FILE, "w") as f:
            json.dump(_token_store, f)
    except Exception as e:
        logger.warning("Could not persist token: %s", e)


# Load on import (runs on every server start / reload)
_load_token_from_file()


def get_kite_client() -> KiteConnect:
    kite = KiteConnect(api_key=API_KEY)
    if _token_store["access_token"]:
        kite.set_access_token(_token_store["access_token"])
    return kite


def get_login_url() -> str:
    kite = KiteConnect(api_key=API_KEY)
    return kite.login_url()


def generate_session(request_token: str) -> dict:
    kite = KiteConnect(api_key=API_KEY)
    try:
        data = kite.generate_session(request_token, api_secret=API_SECRET)
    except Exception as e:
        logger.error("Failed to generate session: %s", e)
        raise ValueError(f"Invalid request_token or session generation failed: {e}") from e

    _token_store["access_token"] = data["access_token"]
    _token_store["public_token"] = data.get("public_token")
    _token_store["user_id"] = data.get("user_id")

    _save_token_to_file()
    logger.info("Session generated for user: %s", _token_store["user_id"])
    return data


def get_stored_token() -> str | None:
    return _token_store["access_token"]


def clear_token() -> None:
    _token_store["access_token"] = None
    _token_store["public_token"] = None
    _token_store["user_id"] = None
    try:
        os.remove(_TOKEN_FILE)
    except FileNotFoundError:
        pass
    logger.info("Access token cleared")


def require_authenticated_client() -> KiteConnect:
    if not _token_store["access_token"]:
        raise PermissionError("Not authenticated. Please visit /login first.")
    kite = get_kite_client()
    return kite
