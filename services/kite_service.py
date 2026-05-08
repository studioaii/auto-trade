import json
import logging
import os
import time
from kiteconnect import KiteConnect
from config import API_KEY, API_SECRET

logger = logging.getLogger(__name__)


class TransientKiteError(Exception):
    """Zerodha gateway/network blip — retryable. The request_token may still be valid."""

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
    """Write token file with 0600 permissions via atomic rename to prevent partial reads."""
    try:
        import stat, tempfile
        target = os.path.abspath(_TOKEN_FILE)
        dir_path = os.path.dirname(target)
        os.makedirs(dir_path, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path)
        try:
            os.write(fd, json.dumps(_token_store).encode())
        finally:
            os.close(fd)
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner read/write only
        os.replace(tmp_path, target)
    except Exception:
        logger.warning("Could not persist token", exc_info=True)


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


_TRANSIENT_MARKERS = (
    "503",
    "service unavailable",
    "unknown content-type (text/html)",
    "bad gateway",
    "504",
    "gateway timeout",
    "connection reset",
    "connection aborted",
    "max retries exceeded",
)


def _is_transient(err: Exception) -> bool:
    """Return True for upstream-gateway blips where the request_token wasn't consumed."""
    msg = str(err).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


def generate_session(request_token: str, max_attempts: int = 3) -> dict:
    """
    Exchange request_token for access_token.

    On transient gateway errors (503/504/HTML/network), retry up to
    `max_attempts` times with exponential backoff. If still failing,
    raise `TransientKiteError` so the caller can recover (e.g. redirect
    back to /login for a fresh request_token).
    Permanent errors (invalid/expired token) raise `ValueError` immediately.
    """
    kite = KiteConnect(api_key=API_KEY)
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            data = kite.generate_session(request_token, api_secret=API_SECRET)
            break
        except Exception as e:
            last_err = e
            if _is_transient(e) and attempt < max_attempts:
                backoff = 0.4 * (2 ** (attempt - 1))   # 0.4, 0.8, 1.6 s
                logger.warning(
                    "Zerodha session-gen transient (attempt %d/%d) — retrying in %.1fs: %s",
                    attempt, max_attempts, backoff, e,
                )
                time.sleep(backoff)
                continue
            if _is_transient(e):
                logger.error("Zerodha session-gen transient — exhausted retries: %s", e)
                raise TransientKiteError(str(e)) from e
            logger.error("Failed to generate session (permanent): %s", e)
            raise ValueError(f"Invalid request_token or session generation failed: {e}") from e
    else:  # all retries exhausted with transient error
        raise TransientKiteError(str(last_err)) from last_err

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
