"""
Static event-day calendar loader.

`events.json` is hand-maintained at the repo root. Event types:
RBI_POLICY, FED_FOMC, BIG_BANK_EARNINGS, ELECTION_RESULT, BUDGET, MANUAL_BLOCK.

Schema:
  {"events": [{"date": "YYYY-MM-DD", "type": "...", "instruments": ["NIFTY","BANKNIFTY"]}]}

The `instruments` field is optional — if absent, the event applies to both indices.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

# Repo root — this module lives at services/events.py
_EVENTS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "events.json")


@lru_cache(maxsize=1)
def _load_events() -> list[dict]:
    """Load and cache the events list. Returns [] if file missing or malformed."""
    if not os.path.exists(_EVENTS_PATH):
        logger.info("No events.json at %s — event-day gate disabled", _EVENTS_PATH)
        return []
    try:
        with open(_EVENTS_PATH) as f:
            data = json.load(f)
        events = data.get("events", [])
        logger.info("Loaded %d events from %s", len(events), _EVENTS_PATH)
        return events
    except Exception:
        logger.exception("Failed to read events.json — event-day gate disabled")
        return []


def reload_events() -> None:
    """Drop the cache so the next call re-reads disk."""
    _load_events.cache_clear()


def is_event_day(today: date, instrument: str) -> tuple[bool, str]:
    """
    Returns (True, "<TYPE>") if today is on the calendar for this instrument,
    else (False, "").
    """
    today_str = today.isoformat()
    inst = instrument.upper()
    for ev in _load_events():
        if ev.get("date") != today_str:
            continue
        instruments: Optional[list] = ev.get("instruments")
        if instruments is None or inst in [x.upper() for x in instruments]:
            return True, str(ev.get("type", "EVENT_DAY"))
    return False, ""


def get_today_events(today: date) -> list[dict]:
    """Returns the raw event entries for today (used by /events endpoint)."""
    today_str = today.isoformat()
    return [ev for ev in _load_events() if ev.get("date") == today_str]
