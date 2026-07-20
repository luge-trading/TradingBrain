"""Tests for the stock daily-data update service."""

from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from src.data.database import load_daily_kline
from src.data.database import load_index_daily_kline, save_index_daily_kline
from src.data.update import IndexUpdateResult, UpdateResult, update_index_daily, update_stock_daily


def make_kline_data(dates: list[str]) -> pd.DataFrame:
    """Create standardized K-line data."""
    records = []

    for index, trade_date in enumerate(dates):
        price = 10.0 + index

        records.append(
            {
                "date": trade_date,
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + 0.5,
                "volume": 1000 + index,
                "amount": 10000.0 + index,
            }
        )

    return pd.DataFrame(records)


def test_update_stock_daily_initial_import(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    fetcher = Mock(
        return_value=make_kline_data(
            ["2026-07-16", "2026-07-17"]
        )
    )

    result = update_stock_daily(
        "000021",
        database_path=database_path,
        limit=500,
        fetcher=fetcher,
    )

    assert isinstance(result, UpdateResult)
    assert result.symbol == "000021"
    assert result.fetched_rows == 2
    assert result.new_rows == 2
    assert result.stored_rows == 2
    assert result.latest_before is None
    assert result.latest_after == "2026-07-17"

    fetcher.assert_called_once_with(
        "000021",
        limit=500,
    )


def test_update_stock_daily_only_saves_new_dates(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(
            return_value=make_kline_data(
                ["2026-07-16", "2026-07-17"]
            )
        ),
    )

    result = update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(
            return_value=make_kline_data(
                [
                    "2026-07-16",
                    "2026-07-17",
                    "2026-07-18",
                ]
            )
        ),
    )

    stored = load_daily_kline(
        "000021",
        database_path=database_path,
    )

    assert result.fetched_rows == 3
    assert result.new_rows == 1
    assert result.stored_rows == 1
    assert result.latest_before == "2026-07-17"
    assert result.latest_after == "2026-07-18"
    assert stored["date"].tolist() == [
        "2026-07-16",
        "2026-07-17",
        "2026-07-18",
    ]


def test_update_stock_daily_writes_nothing_when_current(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    data = make_kline_data(
        ["2026-07-16", "2026-07-17"]
    )

    update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(return_value=data),
    )

    result = update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(return_value=data),
    )

    assert result.fetched_rows == 2
    assert result.new_rows == 0
    assert result.stored_rows == 0
    assert result.latest_before == "2026-07-17"
    assert result.latest_after == "2026-07-17"


def test_update_stock_daily_removes_duplicate_dates(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    duplicated_data = make_kline_data(
        [
            "2026-07-16",
            "2026-07-16",
            "2026-07-17",
        ]
    )

    result = update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(return_value=duplicated_data),
    )

    stored = load_daily_kline(
        "000021",
        database_path=database_path,
    )

    assert result.fetched_rows == 2
    assert result.new_rows == 2
    assert result.stored_rows == 2
    assert len(stored) == 2


def test_update_stock_daily_handles_empty_data(
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

    result = update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(return_value=empty_data),
    )

    assert result.fetched_rows == 0
    assert result.new_rows == 0
    assert result.stored_rows == 0
    assert result.latest_before is None
    assert result.latest_after is None


def test_update_stock_daily_rejects_non_dataframe(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    with pytest.raises(
        TypeError,
        match="fetcher must return a pandas DataFrame",
    ):
        update_stock_daily(
            "000021",
            database_path=database_path,
            fetcher=Mock(return_value=[]),
        )


def test_update_stock_daily_rejects_missing_date_column(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    invalid_data = pd.DataFrame(
        [{"open": 10.0, "close": 11.0}]
    )

    with pytest.raises(
        ValueError,
        match="missing date column",
    ):
        update_stock_daily(
            "000021",
            database_path=database_path,
            fetcher=Mock(return_value=invalid_data),
        )


def test_update_stock_daily_does_not_overwrite_existing_data_on_failure(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    from src.data.database import save_daily_kline, load_daily_kline

    initial = make_kline_data(["2026-07-16", "2026-07-17"])
    save_daily_kline("000021", initial, database_path=database_path)

    def failing_fetcher(symbol, *, limit):
        raise RuntimeError("fetch failed")

    with pytest.raises(RuntimeError, match="fetch failed"):
        update_stock_daily(
            "000021",
            database_path=database_path,
            fetcher=failing_fetcher,
        )

    stored = load_daily_kline("000021", database_path=database_path)
    assert stored["date"].tolist() == ["2026-07-16", "2026-07-17"]


def index_data(close=11.0, dates=("2026-07-16", "2026-07-17")):
    return pd.DataFrame([
        {"date": date, "open": close - 1, "high": close + 1, "low": close - 2, "close": close, "volume": 100, "amount": 1000}
        for date in dates
    ])


def test_update_index_upserts_all_rows_and_counts_only_new_dates(tmp_path: Path):
    database_path = tmp_path / "test.db"
    first = update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=index_data()))
    assert isinstance(first, IndexUpdateResult)
    assert (first.fetched_rows, first.new_rows, first.stored_rows) == (2, 2, 2)
    revised = index_data(close=12.0, dates=("2026-07-16", "2026-07-17", "2026-07-18"))
    second = update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=revised))
    assert (second.fetched_rows, second.new_rows, second.stored_rows) == (3, 1, 3)
    loaded = load_index_daily_kline("SH000001", database_path=database_path)
    assert len(loaded) == 3
    assert loaded.iloc[0]["close"] == 12.0


def test_update_index_empty_and_failure_isolated(tmp_path: Path):
    database_path = tmp_path / "test.db"
    empty = update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])))
    assert (empty.fetched_rows, empty.new_rows, empty.stored_rows) == (0, 0, 0)
    save_index_daily_kline("SZ399001", index_data(), database_path=database_path)
    with pytest.raises(RuntimeError, match="SH000001"):
        update_index_daily("SH000001", database_path=database_path, fetcher=Mock(side_effect=RuntimeError("network")))
    assert len(load_index_daily_kline("SZ399001", database_path=database_path)) == 2


def test_update_index_rejects_non_dataframe_and_preserves_data(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_index_daily_kline("SH000001", index_data(), database_path=database_path)
    with pytest.raises(TypeError, match=r"DataFrame.*SH000001"):
        update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=[]))
    assert len(load_index_daily_kline("SH000001", database_path=database_path)) == 2


def test_update_index_rejects_duplicate_dates_and_preserves_data(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_index_daily_kline("SH000001", index_data(), database_path=database_path)
    duplicated = index_data(dates=("2026-07-16", "2026-07-16"))
    with pytest.raises(ValueError, match=r"normalization.*SH000001"):
        update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=duplicated))
    assert len(load_index_daily_kline("SH000001", database_path=database_path)) == 2


def test_update_index_save_failure_preserves_data(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "test.db"
    save_index_daily_kline("SH000001", index_data(), database_path=database_path)
    monkeypatch.setattr("src.data.update.save_index_daily_kline", Mock(side_effect=RuntimeError("disk")))
    with pytest.raises(RuntimeError, match=r"save.*SH000001"):
        update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=index_data(close=13.0)))
    assert len(load_index_daily_kline("SH000001", database_path=database_path)) == 2
