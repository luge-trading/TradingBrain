import pandas as pd
import pytest

from zoneinfo import ZoneInfo

from src.market import calendar as mcal


def test_exchange_calendars_integration():
    xcals = pytest.importorskip("exchange_calendars")
    # ensure XSHG available
    names = xcals.get_calendar_names()
    assert "XSHG" in names

    cal = xcals.get_calendar("XSHG")
    # public APIs used by our code
    session = pd.Timestamp("2026-07-17")
    assert hasattr(cal, "is_session")
    assert hasattr(cal, "session_close")

    assert cal.is_session(session) is True
    close_ts = cal.session_close(session)
    assert close_ts is not None
    # ensure result can be converted to datetime with Asia/Shanghai tz
    if hasattr(close_ts, "to_pydatetime"):
        dt = close_ts.to_pydatetime()
    else:
        dt = pd.Timestamp(close_ts).to_pydatetime()
    assert dt.tzinfo is not None
    dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
    assert str(dt.tzinfo) == "Asia/Shanghai"

    # weekend should be non-session and our wrapper returns None safely
    weekend = pd.Timestamp("2026-07-18")
    assert cal.is_session(weekend) is False
    assert mcal.get_session_close(weekend.date()) is None
