import json
from types import SimpleNamespace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest


def make_args(tmp_path, **kwargs):
    ns = SimpleNamespace()
    ns.symbols = None
    ns.watchlist = None
    ns.database_path = str(tmp_path / "test.db")
    ns.output_dir = str(tmp_path / "reports")
    ns.limit = 500
    ns.no_update = True
    ns.force = kwargs.get("force", False)
    ns.log_path = str(tmp_path / "logs" / "run.jsonl")
    return ns


def test_skip_non_trading_day(monkeypatch, tmp_path):
    from src.engine import scheduled_review as sr

    # simulate market closed
    fake_now = datetime(2026, 7, 18, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    monkeypatch.setattr("src.market.calendar.is_trading_day", lambda d, calendar_name=None: False)
    called = {"executed": False}

    def fake_execute(args):
        called["executed"] = True
        return 0

    monkeypatch.setattr("src.engine.daily_review.execute_daily_review", fake_execute)
    args = make_args(tmp_path)
    res = sr.run_scheduled_review(args, now=fake_now)
    assert res.exit_code == 0
    assert res.status == "skipped_non_trading_day"
    assert called["executed"] is False
    # log written
    log_lines = list(Path(args.log_path).read_text(encoding="utf-8").splitlines())
    assert len(log_lines) == 1
    rec = json.loads(log_lines[0])
    assert rec["status"] == "skipped_non_trading_day"


def test_skip_before_close(monkeypatch, tmp_path):
    from src.engine import scheduled_review as sr

    # simulate trading day but now before close
    market_date = datetime(2026, 7, 17, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("src.market.calendar.is_trading_day", lambda d, calendar_name=None: True)
    monkeypatch.setattr("src.market.calendar.get_session_close", lambda d, calendar_name=None: datetime(2026, 7, 17, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    called = {"executed": False}

    def fake_execute(args):
        called["executed"] = True
        return 0

    monkeypatch.setattr("src.engine.daily_review.execute_daily_review", fake_execute)
    args = make_args(tmp_path)
    res = sr.run_scheduled_review(args, now=market_date)
    assert res.exit_code == 0
    assert res.status == "skipped_before_close"
    assert called["executed"] is False
    rec = json.loads(Path(args.log_path).read_text(encoding="utf-8"))
    assert rec["status"] == "skipped_before_close"


def test_execute_after_close_and_force(monkeypatch, tmp_path):
    from src.engine import scheduled_review as sr

    # after close
    market_date = datetime(2026, 7, 17, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("src.market.calendar.is_trading_day", lambda d, calendar_name=None: True)
    monkeypatch.setattr("src.market.calendar.get_session_close", lambda d, calendar_name=None: datetime(2026, 7, 17, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    called = {"executed": 0}

    def fake_execute(args):
        called["executed"] += 1
        return 0

    monkeypatch.setattr("src.engine.daily_review.execute_daily_review", fake_execute)
    args = make_args(tmp_path, force=True)
    res = sr.run_scheduled_review(args, now=market_date)
    assert res.exit_code == 0
    assert res.status == "completed"
    assert called["executed"] == 1
    rec = json.loads(Path(args.log_path).read_text(encoding="utf-8"))
    assert rec["status"] == "completed"
    assert rec["forced"] is True


def test_execution_returns_error_and_logging(monkeypatch, tmp_path):
    from src.engine import scheduled_review as sr

    market_date = datetime(2026, 7, 17, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("src.market.calendar.is_trading_day", lambda d, calendar_name=None: True)
    monkeypatch.setattr("src.market.calendar.get_session_close", lambda d, calendar_name=None: datetime(2026, 7, 17, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    def fake_execute(args):
        return 1

    monkeypatch.setattr("src.engine.daily_review.execute_daily_review", fake_execute)
    args = make_args(tmp_path)
    res = sr.run_scheduled_review(args, now=market_date)
    assert res.exit_code == 1
    assert res.status == "completed_with_errors"
    rec = json.loads(Path(args.log_path).read_text(encoding="utf-8"))
    assert rec["status"] == "completed_with_errors"


def test_calendar_exception_returns_failed_and_logs(monkeypatch, tmp_path):
    from src.engine import scheduled_review as sr

    def raise_err(d, calendar_name=None):
        raise RuntimeError("boom calendar")

    monkeypatch.setattr("src.market.calendar.is_trading_day", raise_err)
    args = make_args(tmp_path)
    res = sr.run_scheduled_review(args, now=datetime(2026, 7, 17, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert res.exit_code == 1
    assert res.status == "failed"
    rec = json.loads(Path(args.log_path).read_text(encoding="utf-8"))
    assert rec["status"] == "failed"
