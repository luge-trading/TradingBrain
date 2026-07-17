"""Tests for the SQLite stock data storage layer."""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src.data.database import (
    get_latest_trade_date,
    init_database,
    load_daily_kline,
    save_daily_kline,
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
