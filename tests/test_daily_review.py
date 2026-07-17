import pytest
from pathlib import Path

import src.engine.daily_review as dr


def test_validate_and_normalize_symbols_success():
    symbols = ["000021", " 600584 ", "000021"]
    out = dr._validate_and_normalize_symbols(symbols)
    assert out == ["000021", "600584"]


@pytest.mark.parametrize("bad", [[], (), None])
def test_empty_symbols_rejected(bad):
    with pytest.raises(ValueError):
        dr._validate_and_normalize_symbols(bad)


@pytest.mark.parametrize("bad", ["abc", "123", "1234567", 123456])
def test_invalid_codes_rejected(bad):
    with pytest.raises(ValueError):
        dr._validate_and_normalize_symbols([bad])


def test_limit_validation():
    with pytest.raises(ValueError):
        dr.run_daily_review(["000021"], limit=0)
    with pytest.raises(ValueError):
        dr.run_daily_review(["000021"], limit=-1)
    with pytest.raises(ValueError):
        dr.run_daily_review(["000021"], limit=1.5)
    with pytest.raises(ValueError):
        dr.run_daily_review(["000021"], limit=True)


def test_run_all_success(monkeypatch, tmp_path):
    calls = []

    def fake_generate(symbol, *, database_path=None, output_dir=None, update_data=True, limit=500):
        calls.append((symbol, database_path, output_dir, update_data, limit))
        return Path(tmp_path / f"{symbol}.md")

    monkeypatch.setattr(dr, 'generate_stock_report', fake_generate)

    result = dr.run_daily_review(["000021", "600584"], database_path=str(tmp_path / 'db.sqlite'), output_dir=str(tmp_path / 'reports'), update_data=True, limit=100)

    assert result.total_count == 2
    assert result.success_count == 2
    assert result.failure_count == 0
    assert all(o.success for o in result.outcomes)
    assert calls[0][0] == "000021"
    assert calls[0][3] is True
    assert calls[0][4] == 100


def test_one_failure_other_continue(monkeypatch, tmp_path):
    def fake_generate(symbol, **kwargs):
        if symbol == "600420":
            raise RuntimeError("no data")
        return Path(tmp_path / f"{symbol}.md")

    monkeypatch.setattr(dr, 'generate_stock_report', fake_generate)

    result = dr.run_daily_review(["000021", "600420", "600584"], database_path=str(tmp_path / 'db.sqlite'), output_dir=str(tmp_path / 'reports'))

    assert result.total_count == 3
    assert result.success_count == 2
    assert result.failure_count == 1
    outcomes = [ (o.symbol, o.success) for o in result.outcomes]
    assert outcomes == [("000021", True), ("600420", False), ("600584", True)]


def test_all_failures(monkeypatch):
    def fake_generate(symbol, **kwargs):
        raise RuntimeError(f"fail {symbol}")

    monkeypatch.setattr(dr, 'generate_stock_report', fake_generate)

    result = dr.run_daily_review(["000021", "600584"])

    assert result.total_count == 2
    assert result.success_count == 0
    assert result.failure_count == 2


def test_duplicates_preserved_order(monkeypatch, tmp_path):
    calls = []

    def fake_generate(symbol, **kwargs):
        calls.append(symbol)
        return Path(tmp_path / f"{symbol}.md")

    monkeypatch.setattr(dr, 'generate_stock_report', fake_generate)

    result = dr.run_daily_review(["000021", "600584", "000021", "600584"]) 
    assert calls == ["000021", "600584"]
    assert result.total_count == 2


def test_database_and_output_defaults_not_none(monkeypatch):
    captured = {}

    def fake_generate(symbol, *, database_path=None, output_dir=None, **kwargs):
        captured['db'] = database_path
        captured['out'] = output_dir
        return Path('/tmp/x.md')

    monkeypatch.setattr(dr, 'generate_stock_report', fake_generate)

    res = dr.run_daily_review(["000021"]) 
    assert captured['db'] is not None
    assert captured['out'] is not None


# CLI tests
import src.engine.__main__ as cli


def test_cli_main_shares_daily_review_main():
    import src.engine.daily_review as dr
    assert cli.main is dr.main


def test_cli_all_success(monkeypatch, capsys, tmp_path):
    def fake_run(symbols, **kwargs):
        # simulate three successes
        outcomes = tuple(dr.StockReviewOutcome(s, True, Path(tmp_path / f"{s}.md"), None) for s in symbols)
        return dr.DailyReviewResult(outcomes=outcomes)

    monkeypatch.setattr(dr, 'run_daily_review', fake_run)

    rc = cli.main(["--symbols", "000021", "600584", "600420", "--no-update"]) 
    captured = capsys.readouterr()
    assert rc == 0
    assert "总数: 3" in captured.out
    assert "成功: 3" in captured.out
    assert "失败: 0" in captured.out
    assert "000021 成功" in captured.out


def test_cli_partial_failure(monkeypatch, capsys, tmp_path):
    def fake_run(symbols, **kwargs):
        outcomes = (
            dr.StockReviewOutcome("000021", True, Path(tmp_path / "000021.md"), None),
            dr.StockReviewOutcome("600584", False, None, "no data"),
        )
        return dr.DailyReviewResult(outcomes=outcomes)

    monkeypatch.setattr(dr, 'run_daily_review', fake_run)

    rc = cli.main(["--symbols", "000021", "600584"]) 
    captured = capsys.readouterr()
    assert rc == 1
    assert "总数: 2" in captured.out
    assert "成功: 1" in captured.out
    assert "失败: 1" in captured.out
    assert "600584 失败 no data" in captured.out


def test_cli_and_daily_review_consistent_arguments(monkeypatch):
    captured = []

    def fake_run(symbols, **kwargs):
        captured.append((symbols, kwargs))
        return dr.DailyReviewResult(outcomes=())

    monkeypatch.setattr(dr, 'run_daily_review', fake_run)

    cli_rc = cli.main(["--symbols", "000021", "--database-path", "/tmp/db.sqlite", "--output-dir", "/tmp/reports", "--limit", "10", "--no-update"])
    dr_rc = dr.main(["--symbols", "000021", "--database-path", "/tmp/db.sqlite", "--output-dir", "/tmp/reports", "--limit", "10", "--no-update"])

    assert cli_rc == dr_rc
    assert cli_rc == 0
    assert len(captured) == 2
    assert captured[0][0] == ["000021"]
    assert captured[0][1]["database_path"] == "/tmp/db.sqlite"
    assert captured[0][1]["output_dir"] == "/tmp/reports"
    assert captured[0][1]["limit"] == 10
    assert captured[0][1]["update_data"] is False
    assert captured[1] == captured[0]
