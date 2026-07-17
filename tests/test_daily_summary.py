import pytest
from datetime import date
from pathlib import Path

import pandas as pd
from src.data.database import save_daily_kline
from src.report.daily_summary import generate_daily_summary
from src.analysis.signal import SignalResult
from src.engine.daily_review import DailyReviewResult, StockReviewOutcome


def make_review_result(success_symbols, failed_symbols, reports_dir):
    outcomes = []
    for symbol in success_symbols:
        outcomes.append(StockReviewOutcome(symbol=symbol, success=True, report_path=Path(reports_dir / f"{symbol}.md"), error=None))
    for symbol, error in failed_symbols:
        outcomes.append(StockReviewOutcome(symbol=symbol, success=False, report_path=None, error=error))
    return DailyReviewResult(outcomes=tuple(outcomes))


def test_generate_daily_summary_success(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    reports_path = tmp_path / "reports"
    reports_path.mkdir()

    def fake_analyze(symbol, database_path=None):
        return SignalResult(
            symbol=symbol,
            trade_date="2026-07-01",
            close=10.0,
            daily_return_pct=1.0,
            trend_state="bullish_alignment",
            price_vs_ma20="above_ma20",
            volume_state="normal",
            volume_price_state="normal_rise",
            risk_score=3,
            risk_level="high" if symbol == "000021" else "low",
            risk_flags=("bearish_alignment",) if symbol == "000021" else (),
            evidence=("test",),
        )

    monkeypatch.setattr("src.report.daily_summary.analyze_stock_signal", fake_analyze)

    review_result = make_review_result(["000021", "600584"], [], reports_path)
    Path(reports_path / "000021.md").write_text("ok")
    Path(reports_path / "600584.md").write_text("ok")

    summary_path = generate_daily_summary(
        review_result,
        database_path=str(db_path),
        output_dir=str(reports_path),
        watchlist_source="config/watchlist.toml",
        report_date=date(2026, 7, 17),
    )

    assert summary_path == reports_path / "2026-07-17-daily-summary.md"
    content = summary_path.read_text(encoding="utf-8")
    assert "运行日期: 2026-07-17" in content
    assert "自选股来源: config/watchlist.toml" in content
    assert "2026-07-01" in content
    assert "high 数量: 1" in content
    assert "low 数量: 1" in content
    assert "无高风险股票" not in content
    assert "000021" in content
    assert "600584" in content
    assert "[查看报告](000021.md)" in content
    assert "[查看报告](600584.md)" in content


def test_generate_daily_summary_high_medium_low_counts_and_high_risk_list(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    reports_path = tmp_path / "reports"
    reports_path.mkdir()

    def fake_analyze(symbol, database_path=None):
        if symbol == "000021":
            risk_level = "high"
            risk_flags = ("bearish_alignment",)
        elif symbol == "600584":
            risk_level = "medium"
            risk_flags = ()
        else:
            risk_level = "low"
            risk_flags = ()
        return SignalResult(
            symbol=symbol,
            trade_date="2026-07-01",
            close=10.0,
            daily_return_pct=1.0,
            trend_state="bullish_alignment",
            price_vs_ma20="above_ma20",
            volume_state="normal",
            volume_price_state="normal_rise",
            risk_score=3,
            risk_level=risk_level,
            risk_flags=risk_flags,
            evidence=("test",),
        )

    monkeypatch.setattr("src.report.daily_summary.analyze_stock_signal", fake_analyze)

    review_result = make_review_result(["000021", "600584", "000100"], [], reports_path)
    Path(reports_path / "000021.md").write_text("ok")
    Path(reports_path / "600584.md").write_text("ok")
    Path(reports_path / "000100.md").write_text("ok")

    summary_path = generate_daily_summary(
        review_result,
        database_path=str(db_path),
        output_dir=str(reports_path),
        watchlist_source="config/watchlist.toml",
        report_date=date(2026, 7, 17),
    )

    content = summary_path.read_text(encoding="utf-8")
    assert "high 数量: 1" in content
    assert "medium 数量: 1" in content
    assert "low 数量: 1" in content
    assert "- 高风险股票: 000021" in content
    assert "[查看报告](000021.md)" in content
    assert "[查看报告](600584.md)" in content
    assert "[查看报告](000100.md)" in content


def test_generate_daily_summary_failure_and_unknown(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    reports_path = tmp_path / "reports"
    reports_path.mkdir()

    def fake_analyze(symbol, database_path=None):
        if symbol == "000021":
            raise RuntimeError("analysis broken")
        return SignalResult(
            symbol=symbol,
            trade_date="2026-07-01",
            close=10.0,
            daily_return_pct=1.0,
            trend_state="bullish_alignment",
            price_vs_ma20="above_ma20",
            volume_state="normal",
            volume_price_state="normal_rise",
            risk_score=0,
            risk_level="low",
            risk_flags=(),
            evidence=("test",),
        )

    monkeypatch.setattr("src.report.daily_summary.analyze_stock_signal", fake_analyze)

    review_result = make_review_result(["000021"], [("600584", "no data")], reports_path)
    Path(reports_path / "000021.md").write_text("ok")

    summary_path = generate_daily_summary(
        review_result,
        database_path=str(db_path),
        output_dir=str(reports_path),
        watchlist_source="config/watchlist.toml",
        report_date=date(2026, 7, 17),
    )

    content = summary_path.read_text(encoding="utf-8")
    assert "high 数量: 0" in content
    assert "low 数量: 0" in content
    assert "无法分析数量: 2" in content
    assert "高风险股票:" not in content
    assert "无高风险股票。" in content
    assert "分析失败" in content
    assert "600584: no data" in content


def test_generate_daily_summary_escapes_markdown_table_cells(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    reports_path = tmp_path / "reports"
    reports_path.mkdir()

    def fake_analyze(symbol, database_path=None):
        return SignalResult(
            symbol=symbol,
            trade_date="2026-07-01",
            close=10.0,
            daily_return_pct=1.0,
            trend_state="bearish|alignment\nnext",
            price_vs_ma20="above_ma20",
            volume_state="normal",
            volume_price_state="normal_rise",
            risk_score=3,
            risk_level="high",
            risk_flags=("bearish|alignment",),
            evidence=("test",),
        )

    monkeypatch.setattr("src.report.daily_summary.analyze_stock_signal", fake_analyze)

    review_result = make_review_result(["000021"], [("600584", "a\\b|error|with|pipes\nline")], reports_path)
    Path(reports_path / "000021.md").write_text("ok")

    summary_path = generate_daily_summary(
        review_result,
        database_path=str(db_path),
        output_dir=str(reports_path),
        watchlist_source="config/watchlist.toml",
        report_date=date(2026, 7, 17),
    )

    content = summary_path.read_text(encoding="utf-8")
    assert "bearish\\|alignment\\nnext" in content
    assert "a\\\\b\\|error\\|with\\|pipes\\nline" in content
    assert "error\\|with\\|pipes\\nline" in content
    assert content.count("| 000021 |") == 1
    assert content.count("| 600584 |") == 1
    assert content.count("\\n") >= 2


def test_generate_daily_summary_creates_output_dir_and_overwrites(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    reports_path = tmp_path / "reports"

    def fake_analyze(symbol, database_path=None):
        return SignalResult(
            symbol=symbol,
            trade_date="2026-07-01",
            close=10.0,
            daily_return_pct=1.0,
            trend_state="bullish_alignment",
            price_vs_ma20="above_ma20",
            volume_state="normal",
            volume_price_state="normal_rise",
            risk_score=0,
            risk_level="low",
            risk_flags=(),
            evidence=("test",),
        )

    monkeypatch.setattr("src.report.daily_summary.analyze_stock_signal", fake_analyze)

    review_result = make_review_result(["000021"], [], reports_path)

    summary_path = generate_daily_summary(
        review_result,
        database_path=str(db_path),
        output_dir=str(reports_path),
        watchlist_source="config/watchlist.toml",
        report_date=date(2026, 7, 17),
    )

    assert summary_path.exists()
    assert reports_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "无高风险股票。" in content

    Path(summary_path).write_text("old")
    summary_path2 = generate_daily_summary(
        review_result,
        database_path=str(db_path),
        output_dir=str(reports_path),
        watchlist_source="config/watchlist.toml",
        report_date=date(2026, 7, 17),
    )

    assert summary_path2 == summary_path
    assert summary_path.read_text(encoding="utf-8") != "old"


def test_generate_daily_summary_all_failures_still_generates_summary(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    reports_path = tmp_path / "reports"
    reports_path.mkdir()

    review_result = make_review_result([], [("000021", "no data"), ("600584", "no data")], reports_path)

    summary_path = generate_daily_summary(
        review_result,
        database_path=str(db_path),
        output_dir=str(reports_path),
        watchlist_source="config/watchlist.toml",
        report_date=date(2026, 7, 17),
    )

    assert summary_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "股票总数: 2" in content
    assert "成功数量: 0" in content
    assert "失败数量: 2" in content
    assert "无高风险股票。" in content
    assert "600584: no data" in content
    assert "000021: no data" in content


def test_generate_daily_summary_no_high_risk_shows_no_high_risk_text(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    reports_path = tmp_path / "reports"
    reports_path.mkdir()

    def fake_analyze(symbol, database_path=None):
        return SignalResult(
            symbol=symbol,
            trade_date="2026-07-01",
            close=10.0,
            daily_return_pct=1.0,
            trend_state="bullish_alignment",
            price_vs_ma20="above_ma20",
            volume_state="normal",
            volume_price_state="normal_rise",
            risk_score=1,
            risk_level="medium",
            risk_flags=(),
            evidence=("test",),
        )

    monkeypatch.setattr("src.report.daily_summary.analyze_stock_signal", fake_analyze)

    review_result = make_review_result(["000021"], [], reports_path)
    Path(reports_path / "000021.md").write_text("ok")

    summary_path = generate_daily_summary(
        review_result,
        database_path=str(db_path),
        output_dir=str(reports_path),
        watchlist_source="config/watchlist.toml",
        report_date=date(2026, 7, 17),
    )

    content = summary_path.read_text(encoding="utf-8")
    assert "无高风险股票。" in content
