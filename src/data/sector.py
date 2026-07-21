"""EastMoney industry sector definitions and daily K-line validation."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math
import re
from typing import Final

import pandas as pd


EASTMONEY_INDUSTRY_SECTOR_TYPE: Final[str] = "EASTMONEY_INDUSTRY"
EASTMONEY_INDUSTRY_LEVELS: Final[tuple[int, ...]] = (1, 2, 3)
EASTMONEY_INDUSTRY_REGISTRY_SOURCE: Final[str] = "eastmoney_clist"
EASTMONEY_INDUSTRY_KLINE_SOURCE: Final[str] = "eastmoney_kline"
SECTOR_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^BK\d{4}$")

SECTOR_REGISTRY_COLUMNS: Final[tuple[str, ...]] = (
    "sector_type",
    "sector_level",
    "sector_code",
    "sector_name",
    "source",
)
SECTOR_KLINE_COLUMNS: Final[tuple[str, ...]] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "change_pct",
)


def validate_sector_level(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in EASTMONEY_INDUSTRY_LEVELS:
        raise ValueError(f"Invalid EastMoney industry sector level: {value!r}")
    return value


def validate_sector_code(value: str) -> str:
    if not isinstance(value, str) or SECTOR_CODE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"Invalid EastMoney industry sector code: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class SectorDefinition:
    sector_type: str
    sector_level: int
    sector_code: str
    sector_name: str
    source: str

    def __post_init__(self) -> None:
        if self.sector_type != EASTMONEY_INDUSTRY_SECTOR_TYPE:
            raise ValueError(f"Invalid sector type: {self.sector_type!r}")
        validate_sector_level(self.sector_level)
        validate_sector_code(self.sector_code)
        if not isinstance(self.sector_name, str) or not self.sector_name.strip():
            raise ValueError("sector_name must be a non-empty string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        object.__setattr__(self, "sector_name", self.sector_name.strip())
        object.__setattr__(self, "source", self.source.strip())


def normalize_sector_registry(
    definitions: Iterable[SectorDefinition],
) -> tuple[SectorDefinition, ...]:
    try:
        items = tuple(definitions)
    except TypeError as exc:
        raise TypeError("definitions must be iterable") from exc
    if not items:
        raise ValueError("Sector registry snapshot must not be empty")

    business_keys: set[tuple[str, int, str]] = set()
    code_levels: dict[str, int] = {}
    for item in items:
        if not isinstance(item, SectorDefinition):
            raise TypeError("Sector registry items must be SectorDefinition instances")
        key = (item.sector_type, item.sector_level, item.sector_code)
        if key in business_keys:
            raise ValueError(f"Duplicate sector registry key: {key!r}")
        business_keys.add(key)
        prior_level = code_levels.get(item.sector_code)
        if prior_level is not None and prior_level != item.sector_level:
            raise ValueError(f"Sector code appears in multiple levels: {item.sector_code}")
        code_levels[item.sector_code] = item.sector_level
    return tuple(sorted(items, key=lambda item: (item.sector_level, item.sector_code)))


def _is_missing(value: object) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, str):
        return value.strip() in {"", "-", "--"}
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _strict_float(value: object, field: str, row: int, *, positive: bool = False) -> float:
    if isinstance(value, bool) or _is_missing(value):
        raise ValueError(f"Invalid {field} at row {row}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field} at row {row}") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        requirement = "finite and positive" if positive else "finite"
        raise ValueError(f"Invalid {field} at row {row}: must be {requirement}")
    return result


def normalize_sector_daily_kline(data: pd.DataFrame) -> pd.DataFrame:
    """Return strictly validated sector daily K-lines without mutating input."""
    if not isinstance(data, pd.DataFrame):
        raise TypeError("sector daily K-line data must be a pandas DataFrame")
    missing_columns = [column for column in SECTOR_KLINE_COLUMNS if column not in data.columns]
    if missing_columns:
        raise ValueError(f"Sector daily K-line data missing columns: {missing_columns}")
    result = data.loc[:, list(SECTOR_KLINE_COLUMNS)].copy()
    if result.empty:
        result["volume"] = result["volume"].astype("Int64")
        return result

    dates: list[str] = []
    numeric: dict[str, list[float | int | None]] = {
        column: [] for column in SECTOR_KLINE_COLUMNS if column != "date"
    }
    for position, row in enumerate(result.itertuples(index=False, name=None)):
        values = dict(zip(SECTOR_KLINE_COLUMNS, row))
        date_value = values["date"]
        if not isinstance(date_value, str):
            raise ValueError(f"Invalid sector trade date at row {position}: {date_value!r}")
        try:
            parsed_date = pd.to_datetime(date_value, format="%Y-%m-%d", errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid sector trade date at row {position}: {date_value!r}") from exc
        normalized_date = parsed_date.strftime("%Y-%m-%d")
        if normalized_date != date_value:
            raise ValueError(f"Invalid sector trade date at row {position}: {date_value!r}")
        dates.append(normalized_date)

        prices = {
            column: _strict_float(values[column], column, position, positive=True)
            for column in ("open", "high", "low", "close")
        }
        if prices["high"] < max(prices.values()):
            raise ValueError(f"Invalid high at row {position}: inconsistent with OHLC prices")
        if prices["low"] > min(prices.values()):
            raise ValueError(f"Invalid low at row {position}: inconsistent with OHLC prices")
        for column, value in prices.items():
            numeric[column].append(value)

        volume_value = values["volume"]
        if _is_missing(volume_value):
            numeric["volume"].append(None)
        else:
            volume = _strict_float(volume_value, "volume", position)
            if volume < 0 or not volume.is_integer():
                raise ValueError(f"Invalid volume at row {position}: must be a non-negative integer")
            numeric["volume"].append(int(volume))

        for column in ("amount", "change_pct"):
            value = values[column]
            if _is_missing(value):
                numeric[column].append(None)
                continue
            converted = _strict_float(value, column, position)
            if column == "amount" and converted < 0:
                raise ValueError(f"Invalid amount at row {position}: must be non-negative")
            numeric[column].append(converted)

    if len(dates) != len(set(dates)):
        duplicate = next(date for date in dates if dates.count(date) > 1)
        raise ValueError(f"Duplicate sector trade date: {duplicate}")
    result["date"] = dates
    for column, values in numeric.items():
        result[column] = values
    result["volume"] = result["volume"].astype("Int64")
    return result.sort_values("date").reset_index(drop=True)
