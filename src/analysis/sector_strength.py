"""Real-time industry strength calculations over stored database facts.

Derived metrics are not persisted and use no future observations.  The first
version uses SH000001 as one reproducible observation calendar and comparison
baseline; it does not claim that this index fully represents all A shares.
"""
from __future__ import annotations

import math
from os import PathLike
from typing import Final

import numpy as np
import pandas as pd

from src.data.database import (
    DEFAULT_DATABASE_PATH,
    load_index_daily_kline,
    load_sector_daily_panel,
)
from src.data.market import validate_trade_date
from src.data.sector import (
    EASTMONEY_INDUSTRY_SECTOR_TYPE,
    validate_sector_code,
    validate_sector_level,
)


DEFAULT_SECTOR_BENCHMARK_CODE: Final[str] = "SH000001"
SECTOR_STRENGTH_COLUMNS: Final[tuple[str, ...]] = (
    "sector_type",
    "sector_level",
    "sector_code",
    "sector_name",
    "is_active",
    "trade_date",
    "close",
    "return_1d",
    "return_5d",
    "return_10d",
    "return_20d",
    "benchmark_code",
    "benchmark_return_5d",
    "benchmark_return_20d",
    "relative_return_5d",
    "relative_return_20d",
    "amount_ratio_5d",
    "distance_to_20d_high",
    "sector_rank_5d",
    "sector_count_5d",
)

_PANEL_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "sector_type",
    "sector_level",
    "sector_code",
    "sector_name",
    "is_active",
    "date",
    "close",
    "amount",
)
_BENCHMARK_REQUIRED_COLUMNS: Final[tuple[str, ...]] = ("date", "close")
_FLOAT_OUTPUT_COLUMNS: Final[tuple[str, ...]] = (
    "close",
    "return_1d",
    "return_5d",
    "return_10d",
    "return_20d",
    "benchmark_return_5d",
    "benchmark_return_20d",
    "relative_return_5d",
    "relative_return_20d",
    "amount_ratio_5d",
    "distance_to_20d_high",
)


def _empty_sector_strength() -> pd.DataFrame:
    result = pd.DataFrame({column: pd.Series(dtype="object") for column in SECTOR_STRENGTH_COLUMNS})
    result["sector_level"] = result["sector_level"].astype("int64")
    result["is_active"] = result["is_active"].astype(bool)
    for column in _FLOAT_OUTPUT_COLUMNS:
        result[column] = result[column].astype("float64")
    result["sector_rank_5d"] = result["sector_rank_5d"].astype("Int64")
    result["sector_count_5d"] = result["sector_count_5d"].astype("Int64")
    return result.loc[:, list(SECTOR_STRENGTH_COLUMNS)]


def _missing(value: object) -> bool:
    if value is None or value is pd.NA:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _finite_float(value: object, field: str, row: int, *, positive: bool = False) -> float:
    if isinstance(value, (bool, np.bool_)) or _missing(value):
        raise ValueError(f"Invalid {field} at row {row}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field} at row {row}") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"Invalid {field} at row {row}")
    return result


def _explicit_bool(value: object, row: int) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    raise ValueError(f"Invalid is_active at row {row}")


def _copy_and_validate_dates(
    data: pd.DataFrame,
    required_columns: tuple[str, ...],
    input_name: str,
) -> pd.DataFrame:
    """Copy required fields and strictly validate dates before cutoff filtering."""
    missing = [column for column in required_columns if column not in data.columns]
    if missing:
        raise ValueError(f"{input_name} missing columns: {missing}")
    result = data.loc[:, list(required_columns)].copy().reset_index(drop=True)
    result["date"] = [validate_trade_date(value) for value in result["date"]]
    return result


def _validate_sector_panel_facts(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy().reset_index(drop=True)
    closes: list[float] = []
    amounts: list[float] = []
    active_values: list[bool] = []
    for position, row in enumerate(result.itertuples(index=False, name=None)):
        values = dict(zip(_PANEL_REQUIRED_COLUMNS, row))
        if values["sector_type"] != EASTMONEY_INDUSTRY_SECTOR_TYPE:
            raise ValueError(f"Invalid sector_type at row {position}")
        validate_sector_level(values["sector_level"])
        validate_sector_code(values["sector_code"])
        if not isinstance(values["sector_name"], str) or not values["sector_name"].strip():
            raise ValueError(f"Invalid sector_name at row {position}")
        active_values.append(_explicit_bool(values["is_active"], position))
        closes.append(_finite_float(values["close"], "close", position, positive=True))
        amount = values["amount"]
        if _missing(amount):
            amounts.append(float("nan"))
        else:
            converted = _finite_float(amount, "amount", position)
            if converted < 0:
                raise ValueError(f"Invalid amount at row {position}")
            amounts.append(converted)
    result["close"] = pd.Series(closes, dtype="float64")
    result["amount"] = pd.Series(amounts, dtype="float64")
    result["is_active"] = pd.Series(active_values, dtype=bool)
    result["sector_level"] = result["sector_level"].astype("int64")

    key_columns = ["sector_type", "sector_level", "sector_code"]
    if result.duplicated(subset=[*key_columns, "date"]).any():
        raise ValueError("Duplicate sector business date")
    for _, group in result.groupby(key_columns, sort=False):
        if group["sector_name"].nunique(dropna=False) != 1:
            raise ValueError("Inconsistent sector_name for sector business key")
        if group["is_active"].nunique(dropna=False) != 1:
            raise ValueError("Inconsistent is_active for sector business key")
    return result


def _validate_benchmark_facts(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy().reset_index(drop=True)
    closes: list[float] = []
    for position, row in enumerate(result.itertuples(index=False, name=None)):
        _, close = row
        closes.append(_finite_float(close, "benchmark close", position, positive=True))
    result["close"] = pd.Series(closes, dtype="float64")
    if result["date"].duplicated().any():
        raise ValueError("Duplicate benchmark trade date")
    return result.sort_values("date").reset_index(drop=True)


def _aligned_values(group: pd.DataFrame, dates: list[str], column: str) -> pd.Series | None:
    indexed = group.set_index("date")
    if not set(dates).issubset(indexed.index):
        return None
    return indexed.loc[dates, column]


def calculate_sector_strength_snapshot(
    sector_daily_panel: pd.DataFrame,
    benchmark_daily: pd.DataFrame,
    *,
    sector_level: int,
    as_of_date: str,
    active_only: bool = True,
) -> pd.DataFrame:
    """Calculate one same-level industry cross-section on an exact trade date."""
    if not isinstance(sector_daily_panel, pd.DataFrame):
        raise TypeError("sector_daily_panel must be a pandas DataFrame")
    if not isinstance(benchmark_daily, pd.DataFrame):
        raise TypeError("benchmark_daily must be a pandas DataFrame")
    level = validate_sector_level(sector_level)
    date = validate_trade_date(as_of_date)
    if not isinstance(active_only, bool):
        raise TypeError("active_only must be a bool")

    panel = _copy_and_validate_dates(
        sector_daily_panel,
        _PANEL_REQUIRED_COLUMNS,
        "Sector daily panel",
    )
    benchmark = _copy_and_validate_dates(
        benchmark_daily,
        _BENCHMARK_REQUIRED_COLUMNS,
        "Benchmark daily data",
    )
    panel = panel.loc[panel["date"] <= date].copy()
    benchmark = benchmark.loc[benchmark["date"] <= date].copy()
    panel = _validate_sector_panel_facts(panel)
    benchmark = _validate_benchmark_facts(benchmark)
    if date not in set(benchmark["date"]):
        return _empty_sector_strength()

    calendar = benchmark["date"].tolist()
    benchmark_close = benchmark.set_index("date")["close"]
    benchmark_returns: dict[int, float] = {}
    for period in (5, 20):
        benchmark_returns[period] = (
            float(benchmark_close.iloc[-1] / benchmark_close.iloc[-(period + 1)] - 1)
            if len(calendar) >= period + 1
            else float("nan")
        )

    # is_active is today's registry state.  Without classification validity
    # history, past snapshots are not survivorship-bias-free point-in-time sets.
    panel = panel.loc[panel["sector_level"] == level].copy()
    if active_only:
        panel = panel.loc[panel["is_active"]].copy()
    current = panel.loc[panel["date"] == date].copy()
    if current.empty:
        return _empty_sector_strength()

    records: list[dict[str, object]] = []
    key_columns = ["sector_type", "sector_level", "sector_code"]
    for key, current_group in current.groupby(key_columns, sort=False):
        sector_type, row_level, sector_code = key
        current_row = current_group.iloc[0]
        history = panel.loc[
            (panel["sector_type"] == sector_type)
            & (panel["sector_level"] == row_level)
            & (panel["sector_code"] == sector_code)
        ].sort_values("date")
        returns: dict[int, float] = {}
        for period in (1, 5, 10, 20):
            if len(calendar) < period + 1:
                returns[period] = float("nan")
                continue
            window = calendar[-(period + 1):]
            values = _aligned_values(history, window, "close")
            returns[period] = (
                float(values.iloc[-1] / values.iloc[0] - 1)
                if values is not None
                else float("nan")
            )

        amount_ratio = float("nan")
        if len(calendar) >= 6:
            amount_dates = calendar[-6:]
            amounts = _aligned_values(history, amount_dates, "amount")
            if amounts is not None and not amounts.isna().any():
                previous_mean = float(amounts.iloc[:-1].mean())
                if previous_mean > 0:
                    amount_ratio = float(amounts.iloc[-1] / previous_mean)

        distance = float("nan")
        if len(calendar) >= 20:
            high_dates = calendar[-20:]
            closes = _aligned_values(history, high_dates, "close")
            if closes is not None:
                distance = float(closes.iloc[-1] / closes.max() - 1)

        relative_5d = (
            returns[5] - benchmark_returns[5]
            if not math.isnan(returns[5]) and not math.isnan(benchmark_returns[5])
            else float("nan")
        )
        relative_20d = (
            returns[20] - benchmark_returns[20]
            if not math.isnan(returns[20]) and not math.isnan(benchmark_returns[20])
            else float("nan")
        )
        records.append({
            "sector_type": sector_type,
            "sector_level": int(row_level),
            "sector_code": sector_code,
            "sector_name": current_row["sector_name"],
            "is_active": bool(current_row["is_active"]),
            "trade_date": date,
            "close": float(current_row["close"]),
            "return_1d": returns[1],
            "return_5d": returns[5],
            "return_10d": returns[10],
            "return_20d": returns[20],
            "benchmark_code": DEFAULT_SECTOR_BENCHMARK_CODE,
            "benchmark_return_5d": benchmark_returns[5],
            "benchmark_return_20d": benchmark_returns[20],
            "relative_return_5d": relative_5d,
            "relative_return_20d": relative_20d,
            "amount_ratio_5d": amount_ratio,
            "distance_to_20d_high": distance,
        })

    result = pd.DataFrame(records)
    valid_count = int(result["return_5d"].notna().sum())
    result["sector_rank_5d"] = result["return_5d"].rank(
        method="min", ascending=False, na_option="keep"
    ).astype("Int64")
    result["sector_count_5d"] = pd.Series(
        [valid_count] * len(result), dtype="Int64"
    )
    result["sector_level"] = result["sector_level"].astype("int64")
    result["is_active"] = result["is_active"].astype(bool)
    for column in _FLOAT_OUTPUT_COLUMNS:
        result[column] = result[column].astype("float64")
    result = result.sort_values(
        ["sector_rank_5d", "sector_code"], na_position="last"
    ).reset_index(drop=True)
    return result.loc[:, list(SECTOR_STRENGTH_COLUMNS)]


def load_sector_strength_snapshot(
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    sector_level: int,
    as_of_date: str,
    active_only: bool = True,
) -> pd.DataFrame:
    """Load stored facts and delegate all calculations to the pure function."""
    level = validate_sector_level(sector_level)
    date = validate_trade_date(as_of_date)
    if not isinstance(active_only, bool):
        raise TypeError("active_only must be a bool")
    panel = load_sector_daily_panel(
        database_path=database_path,
        sector_level=level,
        active_only=False,
        end_date=date,
    )
    benchmark = load_index_daily_kline(
        DEFAULT_SECTOR_BENCHMARK_CODE,
        database_path=database_path,
    )
    return calculate_sector_strength_snapshot(
        panel,
        benchmark,
        sector_level=level,
        as_of_date=date,
        active_only=active_only,
    )
