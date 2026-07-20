"""Core index definitions and standardized daily K-line validation."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

import numpy as np
import pandas as pd


INDEX_KLINE_COLUMNS: Final[tuple[str, ...]] = (
    "date", "open", "high", "low", "close", "volume", "amount",
)


@dataclass(frozen=True, slots=True)
class IndexDefinition:
    index_code: str
    name: str
    eastmoney_secid: str


INDEX_REGISTRY: Final[Mapping[str, IndexDefinition]] = MappingProxyType({
    "SH000001": IndexDefinition("SH000001", "上证指数", "1.000001"),
    "SZ399001": IndexDefinition("SZ399001", "深证成指", "0.399001"),
    "SZ399006": IndexDefinition("SZ399006", "创业板指", "0.399006"),
    "SH000688": IndexDefinition("SH000688", "科创50", "1.000688"),
})


def get_index_definition(index_code: str) -> IndexDefinition:
    if not isinstance(index_code, str) or index_code not in INDEX_REGISTRY:
        raise ValueError(f"Unsupported index code: {index_code!r}")
    return INDEX_REGISTRY[index_code]


def _missing_amount(value: object) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip() in {"-", "--"}
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def normalize_index_daily_kline(data: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize index daily K-lines without mutating input."""
    if not isinstance(data, pd.DataFrame):
        raise TypeError("index K-line data must be a pandas DataFrame")
    missing = [column for column in INDEX_KLINE_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError(f"Index K-line data missing columns: {missing}")

    result = data.loc[:, list(INDEX_KLINE_COLUMNS)].copy()
    if result.empty:
        return result

    parsed_dates = []
    for position, value in enumerate(result["date"]):
        try:
            parsed = pd.to_datetime(value, format="%Y-%m-%d", errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid index trade date at row {position}: {value!r}") from exc
        parsed_dates.append(parsed.strftime("%Y-%m-%d"))
    result["date"] = parsed_dates
    if result["date"].duplicated().any():
        duplicate = result.loc[result["date"].duplicated(), "date"].iloc[0]
        raise ValueError(f"Duplicate index trade date: {duplicate}")

    numeric: dict[str, list[float | int | None]] = {column: [] for column in ("open", "high", "low", "close", "volume", "amount")}
    for position, row in enumerate(result.itertuples(index=False, name=None)):
        values = dict(zip(INDEX_KLINE_COLUMNS, row))
        converted: dict[str, float | int | None] = {}
        for column in ("open", "high", "low", "close"):
            try:
                value = float(values[column])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid {column} at row {position}") from exc
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid {column} at row {position}: must be finite and positive")
            converted[column] = value
        if converted["high"] < max(converted["open"], converted["close"], converted["low"]):
            raise ValueError(f"Invalid high at row {position}: inconsistent with OHLC prices")
        if converted["low"] > min(converted["open"], converted["close"], converted["high"]):
            raise ValueError(f"Invalid low at row {position}: inconsistent with OHLC prices")

        try:
            volume = float(values["volume"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid volume at row {position}") from exc
        if not np.isfinite(volume) or volume < 0 or not volume.is_integer():
            raise ValueError(f"Invalid volume at row {position}: must be a non-negative integer")
        converted["volume"] = int(volume)

        amount_value = values["amount"]
        if _missing_amount(amount_value):
            converted["amount"] = None
        else:
            try:
                amount = float(amount_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid amount at row {position}") from exc
            if not np.isfinite(amount) or amount < 0:
                raise ValueError(f"Invalid amount at row {position}: must be finite and non-negative")
            converted["amount"] = amount
        for column, value in converted.items():
            numeric[column].append(value)

    for column, values in numeric.items():
        result[column] = values
    result["volume"] = result["volume"].astype("int64")
    return result.sort_values("date").reset_index(drop=True)
