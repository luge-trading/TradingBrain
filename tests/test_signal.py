"""Tests for explainable technical signal rules."""

from pathlib import Path

import pandas as pd
import pytest

from src.analysis.signal import (
    SignalResult,
    analyze_stock_signal,
    classify_latest_signal,
)
from src.data.database import save_daily_kline


def make_signal_data(
    *,
    previous_close: float = 10.0,
    previous_ma20: float = 9.5,
    close: float = 12.0,
    daily_return: float = 3.0,
    ma5: float = 11.0,
    ma10: float = 10.0,
    ma20: float = 9.0,
    volume_ratio: float = 1.6,
) -> pd.DataFrame:
    """Create two rows of pre-calculated indicator data."""
    return pd.DataFrame(
        [
            {
                "date": "2026-07-16",
                "close": previous_close,
                "return_pct": 1.0,
                "ma5": 9.8,
                "ma10": 9.7,
                "ma20": previous_ma20,
                "volume_ratio_5": 1.0,
            },
            {
                "date": "2026-07-17",
                "close": close,
                "return_pct": daily_return,
                "ma5": ma5,
                "ma10": ma10,
                "ma20": ma20,
                "volume_ratio_5": volume_ratio,
            },
        ]
    )


def make_database_data(
    rows: int = 25,
) -> pd.DataFrame:
    """Create rising standardized K-line data."""
    dates = pd.date_range(
        "2026-06-01",
        periods=rows,
        freq="D",
    )

    closes = pd.Series(
        [float(index) for index in range(1, rows + 1)]
    )

    volumes = pd.Series(
        [1000 + index for index in range(rows)]
    )

    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": closes - 0.2,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": volumes,
            "amount": closes * volumes,
        }
    )


def test_classify_bullish_alignment(
) -> None:
    result = classify_latest_signal(
        "000021",
        make_signal_data(),
    )

    assert isinstance(result, SignalResult)
    assert result.trend_state == "bullish_alignment"
    assert result.price_vs_ma20 == "above_ma20"
    assert result.volume_state == "expanded"
    assert result.volume_price_state == "expanded_rise"
    assert result.risk_score == 0
    assert result.risk_level == "low"
    assert result.risk_flags == ()


def test_classify_bearish_high_volume_decline(
) -> None:
    result = classify_latest_signal(
        "000021",
        make_signal_data(
            previous_close=10.0,
            previous_ma20=10.0,
            close=8.0,
            daily_return=-5.0,
            ma5=8.5,
            ma10=9.0,
            ma20=9.5,
            volume_ratio=1.8,
        ),
    )

    assert result.trend_state == "bearish_alignment"
    assert result.volume_price_state == "expanded_decline"
    assert result.risk_level == "high"
    assert "bearish_alignment" in result.risk_flags
    assert "high_volume_decline" in result.risk_flags
    assert "break_below_ma20" in result.risk_flags


def test_classify_break_below_ma20(
) -> None:
    result = classify_latest_signal(
        "000021",
        make_signal_data(
            previous_close=10.5,
            previous_ma20=10.0,
            close=9.8,
            daily_return=-1.0,
            ma5=10.2,
            ma10=10.1,
            ma20=10.0,
            volume_ratio=1.0,
        ),
    )

    assert result.trend_state == "mixed"
    assert result.price_vs_ma20 == "below_ma20"
    assert result.risk_score == 2
    assert result.risk_level == "medium"
    assert result.risk_flags == ("break_below_ma20",)


def test_classify_contracting_volume_rebound(
) -> None:
    result = classify_latest_signal(
        "000021",
        make_signal_data(
            close=10.2,
            daily_return=1.0,
            ma5=10.1,
            ma10=10.0,
            ma20=9.9,
            volume_ratio=0.6,
        ),
    )

    assert result.volume_state == "contracted"
    assert result.volume_price_state == "contracted_rise"
    assert result.risk_score == 1
    assert result.risk_level == "medium"
    assert result.risk_flags == (
        "contracting_volume_rebound",
    )


def test_classify_insufficient_history(
) -> None:
    data = make_signal_data().iloc[[0]].copy()
    data.loc[:, "ma20"] = float("nan")

    result = classify_latest_signal(
        "000021",
        data,
    )

    assert result.trend_state == "insufficient_data"
    assert result.risk_level == "unknown"
    assert result.risk_flags == (
        "insufficient_history",
    )


def test_classify_rejects_missing_columns(
) -> None:
    data = pd.DataFrame(
        [{"date": "2026-07-17", "close": 10.0}]
    )

    with pytest.raises(
        ValueError,
        match="Missing required signal columns",
    ):
        classify_latest_signal(
            "000021",
            data,
        )


def test_analyze_stock_signal_reads_database(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    save_daily_kline(
        "000021",
        make_database_data(),
        database_path=database_path,
    )

    result = analyze_stock_signal(
        "000021",
        database_path=database_path,
    )

    assert result.symbol == "000021"
    assert result.trend_state == "bullish_alignment"
    assert result.price_vs_ma20 == "above_ma20"
    assert result.risk_level == "low"
