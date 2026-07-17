import builtins
from pathlib import Path
from unittest import mock

import pandas as pd

from src.report import generate_stock_report
from src.data.update import UpdateResult


def _make_dummy_kline(days=30):
    # generate simple increasing close and constant volume
    dates = pd.date_range(end=pd.Timestamp.today(), periods=days)
    closes = list(range(1, days + 1))
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": [c - 0.5 for c in closes],
        "high": [c + 0.5 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": [1000] * days,
        "amount": [1000.0 * c for c in closes],
    })
    return df


def test_generate_report_update_called(tmp_path, monkeypatch):
    symbol = "000021"
    db = tmp_path / "data.db"

    dummy = _make_dummy_kline(30)

    # patch update_stock_daily to a fake that writes our dummy into DB to avoid network
    from src.data.database import save_daily_kline, get_latest_trade_date
    from src.data.update import UpdateResult as UR

    def fake_update(sym, *, database_path, limit, fetcher=None):
        save_daily_kline(sym, dummy, database_path=database_path)
        latest_after = get_latest_trade_date(sym, database_path=database_path)
        return UR(symbol=sym, fetched_rows=len(dummy), new_rows=len(dummy), stored_rows=len(dummy), latest_before=None, latest_after=latest_after)

    with mock.patch("src.report.stock_report.update_stock_daily", side_effect=fake_update) as mock_update:
        path = generate_stock_report(symbol, database_path=str(db), output_dir=tmp_path, update_data=True, limit=500)
        mock_update.assert_called()

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "一、数据更新" in content
    assert "最新行情" in content or "二、最新行情" in content


def test_generate_report_no_update(tmp_path):
    symbol = "000022"
    db = tmp_path / "data.db"

    dummy = _make_dummy_kline(30)

    # Save to database via save_daily_kline to simulate existing data
    from src.data.database import save_daily_kline

    save_daily_kline(symbol, dummy, database_path=str(db))

    # patch update_stock_daily to ensure it's not called
    with mock.patch("src.data.update.update_stock_daily") as mock_update:
        path = generate_stock_report(symbol, database_path=str(db), output_dir=tmp_path, update_data=False)
        mock_update.assert_not_called()

    assert path.exists()
    txt = path.read_text(encoding="utf-8")
    assert "本次未执行数据更新" in txt


def test_invalid_symbol_raises(tmp_path):
    with mock.patch("src.data.update.get_daily_kline", return_value=_make_dummy_kline()):
        try:
            generate_stock_report("ABC", database_path=str(tmp_path / "db.sqlite"), output_dir=tmp_path)
        except ValueError:
            return
    raise AssertionError("Invalid symbol did not raise")


def test_observation_content(tmp_path):
    # create temp db and save increasing data to be bullish
    symbol = "000021"
    db = tmp_path / "data.db"
    from src.data.database import save_daily_kline

    dummy = _make_dummy_kline(30)
    save_daily_kline(symbol, dummy, database_path=str(db))

    path = generate_stock_report(symbol, database_path=str(db), output_dir=tmp_path, update_data=False)
    text = path.read_text(encoding="utf-8")

    # must include MA20 numeric and current volume ratio and threshold
    assert "MA20 位置" in text
    assert "量能比为" in text
    assert "1.50" in text

    # must not contain low-quality phrases
    assert "排列变化: 可能" not in text
    assert "1.5 倍: 0.0" not in text

    # bullish case should include observe break line
    assert "观察是否跌破" in text


def test_ma20_below_contains_recover_check(tmp_path):
    # create DB where last close is well below previous values so ma20 > close
    symbol = "000021"
    db = tmp_path / "data.db"
    from src.data.database import save_daily_kline
    import pandas as pd
    from datetime import date, timedelta

    days = 30
    dates = [(date.today() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)][::-1]
    # first 29 days close 200, last day close 100
    closes = [200.0] * (days - 1) + [100.0]
    df = pd.DataFrame({
        'date': dates,
        'open': closes,
        'high': [c + 1 for c in closes],
        'low': [c - 1 for c in closes],
        'close': closes,
        'volume': [1000] * days,
        'amount': [c * 1000 for c in closes],
    })
    save_daily_kline(symbol, df, database_path=str(db))

    path = generate_stock_report(symbol, database_path=str(db), output_dir=tmp_path, update_data=False)
    text = path.read_text(encoding="utf-8")

    assert "观察是否重新站上" in text


def test_insufficient_history_observation(tmp_path):
    # fewer than needed days to compute ma20/volume ratio
    symbol = "000021"
    db = tmp_path / "data.db"
    from src.data.database import save_daily_kline

    dummy = _make_dummy_kline(5)
    save_daily_kline(symbol, dummy, database_path=str(db))

    path = generate_stock_report(symbol, database_path=str(db), output_dir=tmp_path, update_data=False)
    text = path.read_text(encoding="utf-8")

    assert "历史数据不足，暂无法形成该观察条件" in text


def test_cli_default_paths_do_not_pass_none(monkeypatch):
    # patch the generate_stock_report used by CLI
    import src.report.__main__ as cli

    called = {}

    def fake_generate(symbol, *, database_path=None, output_dir=None, update_data=True, limit=500):
        called['database_path'] = database_path
        called['output_dir'] = output_dir
        return Path('/tmp/fake.md')

    monkeypatch.setattr(cli, 'generate_stock_report', fake_generate)

    rc = cli.main(['000021', '--no-update'])
    assert rc == 0
    assert called['database_path'] is not None
    assert called['output_dir'] is not None


def test_cli_custom_paths_passed(monkeypatch, tmp_path):
    import src.report.__main__ as cli

    captured = {}

    def fake_generate(symbol, *, database_path=None, output_dir=None, update_data=True, limit=500):
        captured['database_path'] = database_path
        captured['output_dir'] = output_dir
        return Path(tmp_path / 'x.md')

    monkeypatch.setattr(cli, 'generate_stock_report', fake_generate)

    dbp = str(tmp_path / 'db.sqlite')
    outp = str(tmp_path / 'reports')
    rc = cli.main(['000021', '--database-path', dbp, '--output-dir', outp, '--no-update'])
    assert rc == 0
    assert captured['database_path'] == dbp
    assert captured['output_dir'] == outp


def test_cli_error_returns_nonzero(monkeypatch):
    import src.report.__main__ as cli

    def fake_generate(symbol, **kwargs):
        raise RuntimeError('fail')

    monkeypatch.setattr(cli, 'generate_stock_report', fake_generate)
    rc = cli.main(['000021', '--no-update'])
    assert rc != 0
