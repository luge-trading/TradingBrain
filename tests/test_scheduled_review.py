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
    ns.email_config = str(tmp_path / "missing-email.toml")
    ns.no_email = kwargs.get("no_email", False)
    return ns


def test_skip_non_trading_day(monkeypatch, tmp_path):
    from src.engine import scheduled_review as sr

    # simulate market closed
    fake_now = datetime(2026, 7, 18, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    monkeypatch.setattr("src.market.calendar.is_trading_day", lambda d, calendar_name=None: False)
    called = {"executed": False}

    def fake_execute(args, **kwargs):
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

    def fake_execute(args, **kwargs):
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

    def fake_execute(args, **kwargs):
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

    def fake_execute(args, **kwargs):
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


def _write_email_config(path, enabled=True):
    path.write_text(
        f'''version = 1
enabled = {str(enabled).lower()}
[smtp]
host = "smtp.example.com"
port = 465
timeout_seconds = 5
[message]
sender = "sender@example.com"
recipients = ["recipient@example.com"]
subject_prefix = "[TradingBrain]"
attach_summary = true
[keychain]
service = "com.example.smtp"
''', encoding="utf-8"
    )


def test_completed_sends_once_after_log(monkeypatch, tmp_path):
    from src.engine import scheduled_review as sr

    now = datetime(2026, 7, 17, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("src.market.calendar.is_trading_day", lambda *a, **k: True)
    monkeypatch.setattr("src.market.calendar.get_session_close", lambda *a, **k: now.replace(hour=15))
    monkeypatch.setattr("src.engine.daily_review.execute_daily_review", lambda args, **kwargs: 0)
    config = tmp_path / "email.toml"
    _write_email_config(config)
    args = make_args(tmp_path)
    args.email_config = str(config)
    calls = []

    def fake_send(*args_, **kwargs):
        assert Path(args.log_path).is_file()
        calls.append(kwargs["status"])
        return SimpleNamespace(sent=True, error=None)

    monkeypatch.setattr("src.notification.email_sender.send_review_email", fake_send)
    result = sr.run_scheduled_review(args, now=now)
    assert result.status == "completed"
    assert calls == ["completed"]


def test_completed_with_errors_sends_without_changing_exit_code(monkeypatch, tmp_path):
    from src.engine import scheduled_review as sr

    now = datetime(2026, 7, 17, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("src.market.calendar.is_trading_day", lambda *a, **k: True)
    monkeypatch.setattr("src.market.calendar.get_session_close", lambda *a, **k: now.replace(hour=15))
    monkeypatch.setattr("src.engine.daily_review.execute_daily_review", lambda args, **kwargs: 1)
    config = tmp_path / "email.toml"
    _write_email_config(config)
    args = make_args(tmp_path)
    args.email_config = str(config)
    calls = []
    monkeypatch.setattr(
        "src.notification.email_sender.send_review_email",
        lambda *a, **k: calls.append(k["status"]) or SimpleNamespace(sent=True, error=None),
    )
    result = sr.run_scheduled_review(args, now=now)
    assert result.status == "completed_with_errors"
    assert result.exit_code == 1
    assert calls == ["completed_with_errors"]


def test_failed_attempts_notification_and_email_error_is_isolated(monkeypatch, tmp_path):
    from src.engine import scheduled_review as sr

    now = datetime(2026, 7, 17, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("src.market.calendar.is_trading_day", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("calendar")))
    config = tmp_path / "email.toml"
    _write_email_config(config)
    args = make_args(tmp_path)
    args.email_config = str(config)
    calls = []

    def fail_send(*args_, **kwargs):
        calls.append(kwargs["status"])
        raise RuntimeError("safe mail failure")

    monkeypatch.setattr("src.notification.email_sender.send_review_email", fail_send)
    result = sr.run_scheduled_review(args, now=now)
    assert result.status == "failed"
    assert result.exit_code == 1
    assert calls == ["failed"]
    assert json.loads(Path(args.log_path).read_text(encoding="utf-8"))["status"] == "failed"


@pytest.mark.parametrize("trading,before", [(False, False), (True, True)])
def test_skipped_statuses_do_not_send(monkeypatch, tmp_path, trading, before):
    from src.engine import scheduled_review as sr

    now = datetime(2026, 7, 18 if not trading else 17, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("src.market.calendar.is_trading_day", lambda *a, **k: trading)
    monkeypatch.setattr("src.market.calendar.get_session_close", lambda *a, **k: now.replace(hour=15))
    monkeypatch.setattr("src.notification.email_sender.send_review_email", lambda *a, **k: pytest.fail("email called"))
    sr.run_scheduled_review(make_args(tmp_path), now=now)


def test_no_email_and_disabled_config_skip(monkeypatch, tmp_path):
    from src.engine import scheduled_review as sr

    now = datetime(2026, 7, 17, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = sr.ScheduledRunResult(now.date(), now, now, "completed", 0, False, tmp_path / "run.jsonl")
    monkeypatch.setattr("src.notification.email_sender.send_review_email", lambda *a, **k: pytest.fail("email called"))
    sr._send_email_notification(make_args(tmp_path, no_email=True), result)

    config = tmp_path / "email.toml"
    _write_email_config(config, enabled=False)
    args = make_args(tmp_path)
    args.email_config = str(config)
    calls = []
    monkeypatch.setattr(
        "src.notification.email_sender.send_review_email",
        lambda *a, **k: calls.append(1) or SimpleNamespace(sent=False, error="Email notifications disabled"),
    )
    sr._send_email_notification(args, result)
    assert calls == [1]
