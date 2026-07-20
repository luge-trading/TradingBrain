"""Technical indicators for standardized daily K-line data."""

from __future__ import annotations

from os import PathLike

import numpy as np
import pandas as pd

from src.data.database import (
    DEFAULT_DATABASE_PATH,
    load_daily_kline,
    load_index_daily_kline,
)
from src.data.index import get_index_definition


REQUIRED_COLUMNS = (
    "date",
    "close",
    "volume",
)

INDICATOR_COLUMNS = (
    "return_pct",
    "ma5",
    "ma10",
    "ma20",
    "volume_ma5",
    "volume_ratio_5",
)


def calculate_technical_indicators(
    data: pd.DataFrame,
) -> pd.DataFrame:
    """Calculate basic trend and volume indicators.

    The input DataFrame is not modified.

    Indicators:
        return_pct: Daily close-to-close percentage return.
        ma5: Five-day closing-price moving average.
        ma10: Ten-day closing-price moving average.
        ma20: Twenty-day closing-price moving average.
        volume_ma5: Five-day volume moving average.
        volume_ratio_5: Current volume divided by volume_ma5.

    Args:
        data: Daily K-line DataFrame containing date, close and volume.

    Returns:
        A date-ordered copy containing the original data and indicators.

    Raises:
        TypeError: If data is not a DataFrame.
        ValueError: If required columns or values are invalid.
    """
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame")

    missing_columns = [
        column
        for column in REQUIRED_COLUMNS
        if column not in data.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required columns: {missing_columns}"
        )

    result = data.copy()

    if result.empty:
        for column in INDICATOR_COLUMNS:
            result[column] = pd.Series(dtype="float64")

        return result

    try:
        parsed_dates = pd.to_datetime(
            result["date"],
            format="%Y-%m-%d",
            errors="raise",
        )

        result["close"] = pd.to_numeric(
            result["close"],
            errors="raise",
        ).astype("float64")

        result["volume"] = pd.to_numeric(
            result["volume"],
            errors="raise",
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Technical indicator data contains invalid values"
        ) from exc

    if not np.isfinite(result["close"]).all():
        raise ValueError("close contains non-finite values")

    if not np.isfinite(result["volume"]).all():
        raise ValueError("volume contains non-finite values")

    if (result["close"] <= 0).any():
        raise ValueError("close must contain positive values")

    if (result["volume"] < 0).any():
        raise ValueError("volume cannot contain negative values")

    result["date"] = parsed_dates.dt.strftime("%Y-%m-%d")

    if result["date"].duplicated().any():
        raise ValueError("Duplicate trade dates are not allowed")

    result = (
        result
        .sort_values("date")
        .reset_index(drop=True)
    )

    result["return_pct"] = (
        result["close"]
        .pct_change(fill_method=None)
        .mul(100)
    )

    result["ma5"] = (
        result["close"]
        .rolling(window=5, min_periods=5)
        .mean()
    )

    result["ma10"] = (
        result["close"]
        .rolling(window=10, min_periods=10)
        .mean()
    )

    result["ma20"] = (
        result["close"]
        .rolling(window=20, min_periods=20)
        .mean()
    )

    result["volume_ma5"] = (
        result["volume"]
        .rolling(window=5, min_periods=5)
        .mean()
    )

    valid_volume_ma5 = result["volume_ma5"].where(
        result["volume_ma5"] != 0
    )

    result["volume_ratio_5"] = (
        result["volume"] / valid_volume_ma5
    )

    return result


def analyze_stock_daily(
    symbol: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    """Load stored daily data and calculate technical indicators."""
    stored_data = load_daily_kline(
        symbol,
        database_path=database_path,
    )

    if stored_data.empty:
        raise ValueError(
            f"No stored daily K-line data for stock: {symbol}"
        )

    return calculate_technical_indicators(stored_data)


def analyze_index_daily(
    index_code: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    get_index_definition(index_code)
    stored_data = load_index_daily_kline(index_code, database_path=database_path)
    if stored_data.empty:
        raise ValueError(f"No stored daily K-line data for index: {index_code}")
    return calculate_technical_indicators(stored_data)
