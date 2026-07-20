from datetime import date, datetime, time
import pandas as pd
import pytest

from zoneinfo import ZoneInfo


def make_fake_calendar(close_ts):
    class Fake:
        def is_session(self, ts):
            # accept pd.Timestamp or datetime
            try:
                d = pd.Timestamp(ts).date()
            except Exception:
                return False
            return close_ts is not None and d == close_ts.date()

        def session_close(self, ts):
            if close_ts is None:
                return None
            return pd.Timestamp(close_ts).tz_localize(ZoneInfo("Asia/Shanghai"))

    return Fake()


def test_is_trading_day_and_close(monkeypatch):
    # simulate 2026-07-17 closing at 15:00
    close_dt = datetime(2026, 7, 17, 15, 0)
    # inject a fake exchange_calendars module before importing
    import types, sys

    mod = types.ModuleType("exchange_calendars")
    mod.get_calendar = lambda name: make_fake_calendar(close_dt)
    monkeypatch.setitem(sys.modules, "exchange_calendars", mod)

    from src.market import calendar as mcal
    # ensure any cached calendar is cleared so tests' injected module is used
    mcal._get_calendar.cache_clear()

    d = date(2026, 7, 17)
    assert mcal.is_trading_day(d) is True
    close = mcal.get_session_close(d)
    assert close is not None
    assert close.tzinfo is not None
    assert close.astimezone(ZoneInfo("Asia/Shanghai")).hour == 15


def test_non_trading_day_returns_false_and_none(monkeypatch):
    import types, sys
    mod = types.ModuleType("exchange_calendars")
    mod.get_calendar = lambda name: make_fake_calendar(None)
    monkeypatch.setitem(sys.modules, "exchange_calendars", mod)
    from src.market import calendar as mcal
    mcal._get_calendar.cache_clear()
    d = date(2026, 7, 18)  # simulated non-trading
    assert mcal.is_trading_day(d) is False
    assert mcal.get_session_close(d) is None


def test_get_market_now_has_timezone():
    from src.market import calendar as mcal

    now = mcal.get_market_now()
    assert now.tzinfo is not None
