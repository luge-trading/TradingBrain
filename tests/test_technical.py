"""Tests for technical indicator calculations."""

from pathlib import Path

import pandas as pd
import pytest

from src.analysis.technical import (
    analyze_index_daily,
    analyze_stock_daily,
    calculate_technical_indicators,
)
from src.data.database import save_daily_kline
from src.data.database import save_index_daily_kline


def make_indicator_data(
    rows: int = 20,
) -> pd.DataFrame:
    """Create deterministic indicator test data."""
    dates = pd.date_range(
        "2026-06-01",
        periods=rows,
        freq="D",
    )

    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": [
                float(index)
                for index in range(1, rows + 1)
            ],
            "volume": [
                1000 + index
                for index in range(rows)
            ],
        }
    )


def make_database_data(
    rows: int = 20,
) -> pd.DataFrame:
    """Create standardized K-line data for database tests."""
    base_data = make_indicator_data(rows)

    return pd.DataFrame(
        {
            "date": base_data["date"],
            "open": base_data["close"] - 0.2,
            "high": base_data["close"] + 0.5,
            "low": base_data["close"] - 0.5,
            "close": base_data["close"],
            "volume": base_data["volume"],
            "amount": base_data["volume"]
            * base_data["close"],
        }
    )


def test_calculate_technical_indicators(
) -> None:
    source = make_indicator_data()

    result = calculate_technical_indicators(source)

    assert result.columns.tolist() == [
        "date",
        "close",
        "volume",
        "return_pct",
        "ma5",
        "ma10",
        "ma20",
        "volume_ma5",
        "volume_ratio_5",
    ]

    last = result.iloc[-1]

    assert last["ma5"] == pytest.approx(18.0)
    assert last["ma10"] == pytest.approx(15.5)
    assert last["ma20"] == pytest.approx(10.5)
    assert last["return_pct"] == pytest.approx(
        ((20.0 / 19.0) - 1.0) * 100
    )
    assert last["volume_ma5"] == pytest.approx(1017.0)
    assert last["volume_ratio_5"] == pytest.approx(
        1019.0 / 1017.0
    )


def test_calculate_technical_indicators_uses_full_windows(
) -> None:
    result = calculate_technical_indicators(
        make_indicator_data()
    )

    assert pd.isna(result.iloc[3]["ma5"])
    assert result.iloc[4]["ma5"] == pytest.approx(3.0)

    assert pd.isna(result.iloc[8]["ma10"])
    assert result.iloc[9]["ma10"] == pytest.approx(5.5)

    assert pd.isna(result.iloc[18]["ma20"])
    assert result.iloc[19]["ma20"] == pytest.approx(10.5)


def test_calculate_technical_indicators_does_not_modify_input(
) -> None:
    source = make_indicator_data()
    original = source.copy(deep=True)

    calculate_technical_indicators(source)

    pd.testing.assert_frame_equal(source, original)


def test_calculate_technical_indicators_orders_dates(
) -> None:
    source = make_indicator_data().iloc[::-1]

    result = calculate_technical_indicators(source)

    assert result["date"].is_monotonic_increasing
    assert result.iloc[0]["date"] == "2026-06-01"


def test_calculate_technical_indicators_handles_empty_data(
) -> None:
    source = pd.DataFrame(
        columns=["date", "close", "volume"]
    )

    result = calculate_technical_indicators(source)

    assert result.empty
    assert "ma20" in result.columns
    assert "volume_ratio_5" in result.columns


def test_calculate_technical_indicators_rejects_missing_columns(
) -> None:
    source = pd.DataFrame(
        [{"date": "2026-07-17", "close": 10.0}]
    )

    with pytest.raises(
        ValueError,
        match="Missing required columns",
    ):
        calculate_technical_indicators(source)


@pytest.mark.parametrize(
    "column, value, expected_message",
    [
        ("close", "invalid", "invalid values"),
        ("close", 0, "close must contain positive"),
        ("close", -1, "close must contain positive"),
        ("volume", -1, "volume cannot contain negative"),
    ],
)
def test_calculate_technical_indicators_rejects_invalid_values(
    column: str,
    value: object,
    expected_message: str,
) -> None:
    source = make_indicator_data()

    if isinstance(value, str):
        source[column] = source[column].astype("object")

    source.loc[0, column] = value

    with pytest.raises(
        ValueError,
        match=expected_message,
    ):
        calculate_technical_indicators(source)


def test_calculate_technical_indicators_rejects_duplicate_dates(
) -> None:
    source = make_indicator_data()
    source.loc[1, "date"] = source.loc[0, "date"]

    with pytest.raises(
        ValueError,
        match="Duplicate trade dates",
    ):
        calculate_technical_indicators(source)


def test_analyze_stock_daily_reads_database(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    save_daily_kline(
        "000021",
        make_database_data(),
        database_path=database_path,
    )

    result = analyze_stock_daily(
        "000021",
        database_path=database_path,
    )

    assert len(result) == 20
    assert result.iloc[-1]["ma20"] == pytest.approx(10.5)


def test_analyze_stock_daily_rejects_missing_data(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    with pytest.raises(
        ValueError,
        match="No stored daily K-line data",
    ):
        analyze_stock_daily(
            "000021",
            database_path=database_path,
        )


def test_analyze_index_daily_reuses_indicators_and_accepts_null_amount(tmp_path: Path):
    database_path = tmp_path / "test.db"
    data = make_database_data()
    data["amount"] = pd.Series([None] * len(data), dtype="object")
    save_index_daily_kline("SH000001", data, database_path=database_path)
    result = analyze_index_daily("SH000001", database_path=database_path)
    assert len(result) == 20
    assert "ma20" in result.columns
    assert result["amount"].isna().all()


def test_analyze_index_daily_rejects_missing_data(tmp_path: Path):
    with pytest.raises(ValueError, match="No stored daily K-line data for index"):
        analyze_index_daily("SH000001", database_path=tmp_path / "test.db")
