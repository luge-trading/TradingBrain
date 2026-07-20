"""Tests for the SQLite stock data storage layer."""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src.data.database import (
    get_latest_market_trade_date,
    get_market_daily,
    get_latest_trade_date,
    init_database,
    load_daily_kline,
    save_daily_kline,
    get_latest_index_trade_date,
    load_index_daily_kline,
    save_index_daily_kline,
    load_market_daily,
    save_market_daily,
)
from src.data.market import (
    SSE_AMOUNT_SOURCE,
    SZSE_AMOUNT_SOURCE,
    ExchangeDailyAmount,
    MarketBreadth,
    compose_market_daily,
)


def make_kline_data() -> pd.DataFrame:
    """Create standardized test K-line data."""
    return pd.DataFrame(
        [
            {
                "date": "2026-07-16",
                "open": 18.00,
                "high": 18.50,
                "low": 17.90,
                "close": 18.25,
                "volume": 123456,
                "amount": 2250000.0,
            },
            {
                "date": "2026-07-17",
                "open": 18.30,
                "high": 18.80,
                "low": 18.10,
                "close": 18.60,
                "volume": 150000,
                "amount": 2800000.0,
            },
        ]
    )


def test_init_database_creates_table(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    init_database(database_path)

    with sqlite3.connect(database_path) as connection:
        result = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'stock_daily';
            """
        ).fetchone()

    assert result == ("stock_daily",)


def test_save_and_load_daily_kline(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    saved_rows = save_daily_kline(
        "000021",
        make_kline_data(),
        database_path=database_path,
    )

    loaded = load_daily_kline(
        "000021",
        database_path=database_path,
    )

    assert saved_rows == 2
    assert loaded.columns.tolist() == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ]
    assert loaded.shape == (2, 7)
    assert loaded.iloc[0]["date"] == "2026-07-16"
    assert loaded.iloc[1]["close"] == 18.60
    assert loaded.iloc[1]["volume"] == 150000


def test_save_daily_kline_updates_existing_record(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    original = make_kline_data().iloc[[0]].copy()

    save_daily_kline(
        "000021",
        original,
        database_path=database_path,
    )

    updated = original.copy()
    updated.loc[:, "close"] = 19.00
    updated.loc[:, "volume"] = 200000

    save_daily_kline(
        "000021",
        updated,
        database_path=database_path,
    )

    loaded = load_daily_kline(
        "000021",
        database_path=database_path,
    )

    assert loaded.shape == (1, 7)
    assert loaded.iloc[0]["close"] == 19.00
    assert loaded.iloc[0]["volume"] == 200000


def test_load_daily_kline_orders_by_date(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    reversed_data = make_kline_data().iloc[::-1]

    save_daily_kline(
        "000021",
        reversed_data,
        database_path=database_path,
    )

    loaded = load_daily_kline(
        "000021",
        database_path=database_path,
    )

    assert loaded["date"].tolist() == [
        "2026-07-16",
        "2026-07-17",
    ]


def test_get_latest_trade_date(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    save_daily_kline(
        "000021",
        make_kline_data(),
        database_path=database_path,
    )

    result = get_latest_trade_date(
        "000021",
        database_path=database_path,
    )

    assert result == "2026-07-17"


def test_get_latest_trade_date_returns_none(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    result = get_latest_trade_date(
        "000021",
        database_path=database_path,
    )

    assert result is None


def test_save_daily_kline_returns_zero_for_empty_data(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    empty_data = pd.DataFrame(
        columns=[
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ]
    )

    result = save_daily_kline(
        "000021",
        empty_data,
        database_path=database_path,
    )

    assert result == 0


def test_save_daily_kline_rejects_missing_columns(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    invalid_data = pd.DataFrame(
        [{"date": "2026-07-17", "close": 18.60}]
    )

    with pytest.raises(
        ValueError,
        match="Missing required K-line columns",
    ):
        save_daily_kline(
            "000021",
            invalid_data,
            database_path=database_path,
        )


@pytest.mark.parametrize(
    "symbol",
    ["21", "00002A", "", 123456, None],
)
def test_database_rejects_invalid_symbol(
    tmp_path: Path,
    symbol: object,
) -> None:
    database_path = tmp_path / "test.db"

    with pytest.raises(ValueError, match="Invalid stock code"):
        load_daily_kline(
            symbol,  # type: ignore[arg-type]
            database_path=database_path,
        )


def make_index_data(amount=None):
    return pd.DataFrame([
        {"date": "2026-07-17", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "amount": amount},
        {"date": "2026-07-16", "open": 9, "high": 10, "low": 8, "close": 9.5, "volume": 90, "amount": 900},
    ])


def test_index_database_schema_and_stock_isolation(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_daily_kline("000021", make_kline_data(), database_path=database_path)
    assert save_index_daily_kline("SH000001", make_index_data(), database_path=database_path) == 2
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1]: row[3] for row in connection.execute("PRAGMA table_info(index_daily)")}
    assert {"stock_daily", "index_daily"} <= tables
    assert columns["amount"] == 0


def test_index_database_upsert_null_amount_and_order(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_index_daily_kline("SH000001", make_index_data(amount="--"), database_path=database_path)
    updated = make_index_data(amount=1234).iloc[[0]].copy()
    save_index_daily_kline("SH000001", updated, database_path=database_path)
    loaded = load_index_daily_kline("SH000001", database_path=database_path)
    assert loaded["date"].tolist() == ["2026-07-16", "2026-07-17"]
    assert loaded.iloc[1]["amount"] == 1234
    assert get_latest_index_trade_date("SH000001", database_path=database_path) == "2026-07-17"


def test_index_save_rolls_back_entire_batch_on_trigger_failure(tmp_path: Path):
    database_path = tmp_path / "test.db"
    init_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("""CREATE TRIGGER reject_second BEFORE INSERT ON index_daily
            WHEN NEW.trade_date = '2026-07-17' BEGIN SELECT RAISE(ABORT, 'blocked'); END;""")
    with pytest.raises(RuntimeError, match="Unable to save index K-line data"):
        save_index_daily_kline("SH000001", make_index_data(), database_path=database_path)
    assert load_index_daily_kline("SH000001", database_path=database_path).empty


@pytest.mark.parametrize("bad", [pd.DataFrame([{"date": "2026-07-17"}]), pd.DataFrame([{"date": "2026-07-17", "open": 1, "high": 2, "low": 0, "close": 1, "volume": -1, "amount": 1}])])
def test_save_index_defensive_validation(tmp_path: Path, bad: pd.DataFrame):
    with pytest.raises((ValueError, TypeError)):
        save_index_daily_kline("SH000001", bad, database_path=tmp_path / "test.db")
    with pytest.raises(ValueError, match="Unsupported index code"):
        save_index_daily_kline("SH999999", make_index_data(), database_path=tmp_path / "other.db")


def make_market_record(trade_date="2026-07-17", sh=100, sz=200, breadth=(3000, 1800, 200)):
    return compose_market_daily(
        trade_date,
        sh_amount=ExchangeDailyAmount(trade_date, sh, SSE_AMOUNT_SOURCE),
        sz_amount=ExchangeDailyAmount(trade_date, sz, SZSE_AMOUNT_SOURCE),
        breadth=MarketBreadth(*breadth),
    )


def test_market_database_schema_is_additive_and_exact(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_daily_kline("000021", make_kline_data(), database_path=database_path)
    save_index_daily_kline("SH000001", make_index_data(), database_path=database_path)
    init_database(database_path)
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = [(row[1], row[2], row[3], row[5]) for row in connection.execute("PRAGMA table_info(market_daily)")]
    assert {"stock_daily", "index_daily", "market_daily"} <= tables
    assert columns == [
        ("trade_date", "TEXT", 0, 1),
        ("sh_amount_yuan", "INTEGER", 0, 0),
        ("sz_amount_yuan", "INTEGER", 0, 0),
        ("total_amount_yuan", "INTEGER", 0, 0),
        ("advance_count", "INTEGER", 0, 0),
        ("decline_count", "INTEGER", 0, 0),
        ("flat_count", "INTEGER", 0, 0),
        ("sh_amount_source", "TEXT", 0, 0),
        ("sz_amount_source", "TEXT", 0, 0),
        ("breadth_source", "TEXT", 0, 0),
        ("updated_at", "TEXT", 1, 0),
    ]
    assert len(load_daily_kline("000021", database_path=database_path)) == 2
    assert len(load_index_daily_kline("SH000001", database_path=database_path)) == 2


def test_market_database_insert_upsert_order_latest_and_derived_ratio(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_market_daily(make_market_record("2026-07-17"), database_path=database_path)
    save_market_daily(make_market_record("2026-07-16", breadth=(1000, 900, 100)), database_path=database_path)
    assert save_market_daily(make_market_record("2026-07-17", sh=400, sz=500), database_path=database_path) == 1
    loaded = load_market_daily(database_path=database_path)
    assert loaded["trade_date"].tolist() == ["2026-07-16", "2026-07-17"]
    assert int(loaded.iloc[1]["total_amount_yuan"]) == 900
    assert loaded.iloc[1]["advance_ratio"] == pytest.approx(0.6)
    assert get_latest_market_trade_date(database_path=database_path) == "2026-07-17"
    assert get_market_daily("2026-07-17", database_path=database_path) == make_market_record("2026-07-17", sh=400, sz=500)


def test_market_database_preserves_null_as_sqlite_null(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_market_daily(compose_market_daily("2026-07-17"), database_path=database_path)
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT sh_amount_yuan, total_amount_yuan, advance_count FROM market_daily"
        ).fetchone()
    assert row == (None, None, None)
    loaded = load_market_daily(database_path=database_path)
    assert pd.isna(loaded.iloc[0]["sh_amount_yuan"])
    assert pd.isna(loaded.iloc[0]["advance_ratio"])


def test_market_database_failed_upsert_rolls_back_and_preserves_old_record(tmp_path: Path):
    database_path = tmp_path / "test.db"
    original = make_market_record("2026-07-17")
    save_market_daily(original, database_path=database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("""CREATE TRIGGER reject_market_update BEFORE UPDATE ON market_daily
            BEGIN SELECT RAISE(ABORT, 'blocked'); END;""")
    with pytest.raises(RuntimeError, match="Unable to save market daily data"):
        save_market_daily(make_market_record("2026-07-17", sh=999, sz=999), database_path=database_path)
    assert get_market_daily("2026-07-17", database_path=database_path) == original


def test_market_database_filters_dates_and_validates_inputs(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_market_daily(make_market_record("2026-07-16"), database_path=database_path)
    save_market_daily(make_market_record("2026-07-17"), database_path=database_path)
    loaded = load_market_daily(database_path=database_path, start_date="2026-07-17", end_date="2026-07-17")
    assert loaded["trade_date"].tolist() == ["2026-07-17"]
    with pytest.raises(ValueError, match="start_date"):
        load_market_daily(database_path=database_path, start_date="2026-07-18", end_date="2026-07-17")
    with pytest.raises(TypeError, match="MarketDaily"):
        save_market_daily({"trade_date": "2026-07-17"}, database_path=database_path)
