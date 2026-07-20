"""Calendar helpers using exchange_calendars.

This module provides a small, well-tested surface used by the scheduled
review runner. It intentionally avoids global mutable state and uses
an LRU cache for calendar objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

import importlib
import pandas as pd

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
MARKET_CALENDAR_NAME = "XSHG"


@lru_cache(maxsize=8)
def _get_calendar(calendar_name: str):
    try:
        xcals = importlib.import_module("exchange_calendars")
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError("exchange_calendars is not available; install it to use market calendar features")

    try:
        return xcals.get_calendar(calendar_name)
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"failed to load calendar '{calendar_name}': {exc}")


def is_trading_day(
    day: date,
    *,
    calendar_name: str = MARKET_CALENDAR_NAME,
) -> bool:
    """Return True if ``day`` is a trading session for the named calendar.

    Prefer the public API `calendar.is_session(pd.Timestamp)`.
    Raises RuntimeError when the calendar cannot be accessed or its API
    raises an unexpected error. TypeError is raised for invalid `day`.
    """
    if not isinstance(day, date):
        raise TypeError("day must be a datetime.date")

    cal = _get_calendar(calendar_name)
    try:
        ts = pd.Timestamp(day)
        return bool(cal.is_session(ts))
    except Exception as exc:
        raise RuntimeError(f"calendar query failed: {exc}")


def get_session_close(
    day: date,
    *,
    calendar_name: str = MARKET_CALENDAR_NAME,
) -> Optional[datetime]:
    """Return the market close datetime for the session starting on ``day``.

    Uses the public `calendar.session_close(pd.Timestamp)` API. Returns
    None when ``day`` is not a trading session. The returned datetime is
    timezone-aware in Asia/Shanghai.
    """
    if not isinstance(day, date):
        raise TypeError("day must be a datetime.date")

    cal = _get_calendar(calendar_name)
    try:
        ts = pd.Timestamp(day)
        if not bool(cal.is_session(ts)):
            return None
        close_ts = cal.session_close(ts)
    except AttributeError:
        raise RuntimeError("calendar does not expose session_close API")
    except Exception as exc:
        raise RuntimeError(f"calendar query failed: {exc}")

    if close_ts is None:
        return None

    if hasattr(close_ts, "to_pydatetime"):
        dt = close_ts.to_pydatetime()
    else:
        dt = pd.Timestamp(close_ts).to_pydatetime()

    try:
        if dt.tzinfo is None:
            # assume returned timestamps without tz are UTC
            dt = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(MARKET_TIMEZONE)
        else:
            dt = dt.astimezone(MARKET_TIMEZONE)
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"failed to convert session close to datetime: {exc}")

    return dt


def get_market_now() -> datetime:
    """Return the current time in the market timezone.

    This helper is small and easy to override in tests.
    """
    return datetime.now(MARKET_TIMEZONE)
