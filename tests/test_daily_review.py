import pytest
from pathlib import Path

import src.engine.daily_review as dr


def test_validate_and_normalize_symbols_success():
    symbols = ["000021", " 600584 ", "000021"]
    out = dr.normalize_symbols(symbols)
    assert out == ("000021", "600584")


@pytest.mark.parametrize("bad", [[], (), None])
def test_empty_symbols_rejected(bad):
    with pytest.raises(ValueError):
        dr.normalize_symbols(bad)


@pytest.mark.parametrize("bad", ["abc", "123", "1234567", 123456])
def test_invalid_codes_rejected(bad):
    with pytest.raises(ValueError):
        dr.normalize_symbols([bad])


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
    assert captured[0][0] == ("000021",)
    assert captured[0][1]["database_path"] == "/tmp/db.sqlite"
    assert captured[0][1]["output_dir"] == "/tmp/reports"
    assert captured[0][1]["limit"] == 10
    assert captured[0][1]["update_data"] is False
    assert captured[1] == captured[0]


def test_cli_watchlist_source_and_custom_path(monkeypatch, capsys, tmp_path):
    toml_path = tmp_path / "watchlist.toml"
    toml_path.write_text('version = 1\nsymbols = ["000021", "600584", "000021"]\n', encoding="utf-8")

    def fake_run(symbols, **kwargs):
        assert symbols == ("000021", "600584")
        return dr.DailyReviewResult(outcomes=(
            dr.StockReviewOutcome("000021", True, Path(tmp_path / "000021.md"), None),
            dr.StockReviewOutcome("600584", True, Path(tmp_path / "600584.md"), None),
        ))

    monkeypatch.setattr(dr, 'run_daily_review', fake_run)

    rc = cli.main(["--watchlist", str(toml_path), "--no-update"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "自选股来源: " in captured.out
    assert str(toml_path) in captured.out
    assert "000021 成功" in captured.out
    assert "600584 成功" in captured.out


def test_cli_symbols_without_watchlist(monkeypatch, capsys):
    def fake_run(symbols, **kwargs):
        assert symbols == ("000021",)
        return dr.DailyReviewResult(outcomes=(
            dr.StockReviewOutcome("000021", True, Path("/tmp/000021.md"), None),
        ))

    monkeypatch.setattr(dr, 'run_daily_review', fake_run)

    rc = cli.main(["--symbols", "000021"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "自选股来源: 命令行" in captured.out
    assert "000021 成功" in captured.out


def test_watchlist_load_failure_returns_nonzero(monkeypatch, capsys):
    def fake_load(path):
        raise FileNotFoundError("missing")

    called = {"run_daily_review": False}

    def fake_run(symbols, **kwargs):
        called["run_daily_review"] = True
        return dr.DailyReviewResult(outcomes=())

    monkeypatch.setattr(dr, 'load_watchlist', fake_load)
    monkeypatch.setattr(dr, 'run_daily_review', fake_run)

    rc = cli.main(["--watchlist", "/tmp/missing.toml"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "Error: missing" in captured.err
    assert called["run_daily_review"] is False


def test_cli_default_watchlist_reads_config_and_passes_paths(monkeypatch, capsys):
    from src.config.watchlist import WatchlistConfig

    config = WatchlistConfig(
        path=Path("config/watchlist.toml"),
        version=1,
        symbols=("000021",),
    )

    def fake_load(path):
        assert Path(path) == Path("config/watchlist.toml")
        return config

    def fake_run(symbols, **kwargs):
        assert symbols == ("000021",)
        assert kwargs["database_path"] is not None
        assert kwargs["output_dir"] is not None
        return dr.DailyReviewResult(outcomes=(
            dr.StockReviewOutcome("000021", True, Path("/tmp/000021.md"), None),
        ))

    monkeypatch.setattr(dr, 'load_watchlist', fake_load)
    monkeypatch.setattr(dr, 'run_daily_review', fake_run)

    rc = cli.main([])
    captured = capsys.readouterr()

    assert rc == 0
    assert "自选股来源: config/watchlist.toml" in captured.out
    assert "000021 成功" in captured.out


def test_cli_symbols_and_watchlist_are_mutually_exclusive():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--symbols", "000021", "--watchlist", "config/watchlist.toml"])
    assert exc.value.code == 2
