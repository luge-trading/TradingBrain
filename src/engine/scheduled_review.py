"""Scheduled review CLI with trading-day and close-time gating.

This module implements a small wrapper that reuses the daily review execution
logic while enforcing market-day gates and structured JSONL run logs.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from src.engine.daily_review import build_parser
# calendar helpers are imported lazily inside run_scheduled_review to keep the
# module import lightweight and test-friendly (tests may inject a fake
# exchange_calendars or monkeypatch the calendar helpers).


@dataclass(frozen=True)
class ScheduledRunResult:
    market_date: datetime.date
    started_at: datetime
    finished_at: datetime
    status: str
    exit_code: int
    forced: bool
    log_path: Path
    message: Optional[str] = None


def _write_log(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def run_scheduled_review(args: argparse.Namespace, *, now: Optional[datetime] = None) -> ScheduledRunResult:
    # import calendar helpers here so tests can monkeypatch calendar module
    from src.market.calendar import (
        MARKET_CALENDAR_NAME,
        MARKET_TIMEZONE,
        get_market_now,
        get_session_close,
        is_trading_day,
    )

    # import the execute_daily_review function lazily so tests can patch it
    from src.engine.daily_review import execute_daily_review

    started_at = (now or get_market_now())
    started_at = started_at.astimezone(MARKET_TIMEZONE)
    market_date = started_at.date()
    forced = bool(getattr(args, "force", False))
    log_path = Path(getattr(args, "log_path", "logs/scheduled-review.jsonl"))

    try:
        trading = is_trading_day(market_date, calendar_name=MARKET_CALENDAR_NAME)
    except Exception as exc:
        # calendar error: do not treat as non-trading day
        message = str(exc)
        finished_at = get_market_now().astimezone(MARKET_TIMEZONE)
        record = {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "market_date": market_date.isoformat(),
            "calendar": MARKET_CALENDAR_NAME,
            "status": "failed",
            "exit_code": 1,
            "forced": forced,
            "message": message,
        }
        try:
            _write_log(log_path, record)
        except Exception as err:
            print(f"Error writing log: {err}", file=sys.stderr)
            return ScheduledRunResult(market_date, started_at, finished_at, "failed", 1, forced, log_path, message=message)
        print(f"Error: {message}", file=sys.stderr)
        return ScheduledRunResult(market_date, started_at, finished_at, "failed", 1, forced, log_path, message=message)

    # Non-trading day
    if not trading and not forced:
        finished_at = get_market_now().astimezone(MARKET_TIMEZONE)
        record = {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "market_date": market_date.isoformat(),
            "calendar": MARKET_CALENDAR_NAME,
            "status": "skipped_non_trading_day",
            "exit_code": 0,
            "forced": False,
            "message": None,
        }
        _write_log(log_path, record)
        print(f"跳过复盘：{market_date} 不是 A 股交易日")
        print(f"运行日志: {log_path}")
        return ScheduledRunResult(market_date, started_at, finished_at, "skipped_non_trading_day", 0, False, log_path)

    # Check close time
    try:
        close_dt = get_session_close(market_date, calendar_name=MARKET_CALENDAR_NAME)
    except Exception as exc:
        finished_at = get_market_now().astimezone(MARKET_TIMEZONE)
        message = str(exc)
        record = {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "market_date": market_date.isoformat(),
            "calendar": MARKET_CALENDAR_NAME,
            "status": "failed",
            "exit_code": 1,
            "forced": forced,
            "message": message,
        }
        try:
            _write_log(log_path, record)
        except Exception:
            pass
        print(f"Error: {message}", file=sys.stderr)
        return ScheduledRunResult(market_date, started_at, finished_at, "failed", 1, forced, log_path, message=message)

    now_dt = (now or get_market_now()).astimezone(MARKET_TIMEZONE)
    if close_dt is not None and now_dt < close_dt and not forced:
        finished_at = get_market_now().astimezone(MARKET_TIMEZONE)
        record = {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "market_date": market_date.isoformat(),
            "calendar": MARKET_CALENDAR_NAME,
            "status": "skipped_before_close",
            "exit_code": 0,
            "forced": False,
            "message": None,
        }
        _write_log(log_path, record)
        print("跳过复盘：A 股市场尚未收盘")
        print(f"运行日志: {log_path}")
        return ScheduledRunResult(market_date, started_at, finished_at, "skipped_before_close", 0, False, log_path)

    # Proceed to execute daily review
    exit_code = execute_daily_review(args)
    finished_at = get_market_now().astimezone(MARKET_TIMEZONE)
    status = "completed" if exit_code == 0 else "completed_with_errors"
    record = {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "market_date": market_date.isoformat(),
        "calendar": MARKET_CALENDAR_NAME,
        "status": status,
        "exit_code": exit_code,
        "forced": forced,
        "message": None,
    }
    _write_log(log_path, record)
    print(f"运行日志: {log_path}")
    return ScheduledRunResult(market_date, started_at, finished_at, status, exit_code, forced, log_path)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.add_argument("--force", dest="force", action="store_true", help="Force execution regardless of trading day or close time")
    parser.add_argument("--log-path", dest="log_path", default="logs/scheduled-review.jsonl", help="Path to append run logs (JSONL)")
    args = parser.parse_args(argv)

    try:
        result = run_scheduled_review(args)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
