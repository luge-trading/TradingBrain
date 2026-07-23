"""Strict offline normalization for versioned stock daily prices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from numbers import Integral, Real
from typing import Final

import numpy as np
import pandas as pd


STOCK_DAILY_PRICE_COLUMNS: Final[tuple[str, ...]] = (
    "security_id",
    "trade_date",
    "adjustment",
    "source",
    "provider_adjustment",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "volume_unit",
    "amount",
    "amount_unit",
    "is_final",
    "provider_as_of_date",
    "observed_at",
)

PRICE_ADJUSTMENTS: Final[frozenset[str]] = frozenset(
    {"UNADJUSTED", "QFQ", "HFQ"}
)
PRICE_VOLUME_UNITS: Final[frozenset[str]] = frozenset(
    {"PROVIDER_NATIVE", "SHARE", "LOT"}
)
PRICE_AMOUNT_UNITS: Final[frozenset[str]] = frozenset(
    {"PROVIDER_NATIVE", "CNY"}
)
PRICE_NULLABLE_FIELDS: Final[tuple[str, ...]] = (
    "amount",
    "amount_unit",
    "provider_as_of_date",
)
PRICE_FACT_FIELDS: Final[tuple[str, ...]] = (
    "provider_adjustment",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "volume_unit",
    "amount",
    "amount_unit",
    "is_final",
    "provider_as_of_date",
)


@dataclass(frozen=True, slots=True)
class StockDailyPriceSaveResult:
    """Counts produced by one atomic stock-price save."""

    inserted: int
    revised: int
    unchanged: int
    revision_rows: int


def _is_missing(value: object) -> bool:
    if value is None or value is pd.NA:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _required_text(value: object, field: str, row: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid {field} at row {row}: must be non-empty text")
    return value.strip()


def _enum_text(
    value: object,
    field: str,
    allowed: frozenset[str],
    row: int,
) -> str:
    result = _required_text(value, field, row)
    if result not in allowed:
        raise ValueError(f"Invalid {field} at row {row}: {result!r}")
    return result


def validate_price_adjustment(value: object) -> str:
    """Return one explicit project price-adjustment identifier."""
    return _enum_text(value, "adjustment", PRICE_ADJUSTMENTS, 0)


def validate_price_source(value: object) -> str:
    """Return a non-empty explicit price source."""
    return _required_text(value, "source", 0)


def validate_price_date(value: object, *, field: str = "trade_date") -> str:
    """Return a strict ISO calendar date."""
    if not isinstance(value, str):
        raise ValueError(f"Invalid {field}: {value!r}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"Invalid {field}: {value!r}")
    return value


def validate_security_id(value: object, *, field: str = "security_id") -> int:
    """Return a positive integer security id, rejecting booleans."""
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise ValueError(f"Invalid {field}: {value!r}")
    return int(value)


def _finite_number(value: object, field: str, row: int) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"Invalid {field} at row {row}: {value!r}")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"Invalid {field} at row {row}: must be finite")
    return result


def _positive_price(value: object, field: str, row: int) -> float:
    result = _finite_number(value, field, row)
    if result <= 0:
        raise ValueError(f"Invalid {field} at row {row}: must be positive")
    return result


def _volume(value: object, row: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"Invalid volume at row {row}: {value!r}")
    return int(value)


def _nullable_amount(value: object, row: int) -> float | None:
    if _is_missing(value):
        return None
    result = _finite_number(value, "amount", row)
    if result < 0:
        raise ValueError(f"Invalid amount at row {row}: cannot be negative")
    return result


def _nullable_date(value: object, field: str, row: int) -> str | None:
    if _is_missing(value):
        return None
    try:
        return validate_price_date(value, field=field)
    except ValueError as exc:
        raise ValueError(f"Invalid {field} at row {row}: {value!r}") from exc


def _observed_at(value: object, row: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Invalid observed_at at row {row}: {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid observed_at at row {row}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Invalid observed_at at row {row}: timezone is required")
    return parsed.astimezone(timezone.utc).isoformat()


def normalize_stock_daily_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """Return strictly validated, date-ordered stock daily price facts."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    if frame.columns.duplicated().any():
        duplicates = frame.columns[frame.columns.duplicated()].tolist()
        raise ValueError(f"Stock daily prices contain duplicate columns: {duplicates}")
    missing = [column for column in STOCK_DAILY_PRICE_COLUMNS if column not in frame]
    if missing:
        raise ValueError(f"Stock daily prices missing columns: {missing}")

    selected = frame.loc[:, list(STOCK_DAILY_PRICE_COLUMNS)].copy()
    normalized: list[dict[str, object]] = []
    for position, row in enumerate(selected.itertuples(index=False, name=None)):
        values = dict(zip(STOCK_DAILY_PRICE_COLUMNS, row))
        open_price = _positive_price(values["open"], "open", position)
        high_price = _positive_price(values["high"], "high", position)
        low_price = _positive_price(values["low"], "low", position)
        close_price = _positive_price(values["close"], "close", position)
        if not (low_price <= open_price <= high_price):
            raise ValueError(f"Invalid OHLC relationship at row {position}: open")
        if not (low_price <= close_price <= high_price):
            raise ValueError(f"Invalid OHLC relationship at row {position}: close")

        amount = _nullable_amount(values["amount"], position)
        raw_amount_unit = values["amount_unit"]
        if amount is None:
            if not _is_missing(raw_amount_unit):
                raise ValueError(
                    f"Invalid amount_unit at row {position}: must be NULL with amount"
                )
            amount_unit = None
        else:
            amount_unit = _enum_text(
                raw_amount_unit,
                "amount_unit",
                PRICE_AMOUNT_UNITS,
                position,
            )

        is_final = values["is_final"]
        if not isinstance(is_final, bool):
            raise ValueError(f"Invalid is_final at row {position}: must be bool")

        normalized.append(
            {
                "security_id": validate_security_id(values["security_id"]),
                "trade_date": validate_price_date(values["trade_date"]),
                "adjustment": _enum_text(
                    values["adjustment"],
                    "adjustment",
                    PRICE_ADJUSTMENTS,
                    position,
                ),
                "source": _required_text(values["source"], "source", position),
                "provider_adjustment": _required_text(
                    values["provider_adjustment"],
                    "provider_adjustment",
                    position,
                ),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": _volume(values["volume"], position),
                "volume_unit": _enum_text(
                    values["volume_unit"],
                    "volume_unit",
                    PRICE_VOLUME_UNITS,
                    position,
                ),
                "amount": amount,
                "amount_unit": amount_unit,
                "is_final": is_final,
                "provider_as_of_date": _nullable_date(
                    values["provider_as_of_date"],
                    "provider_as_of_date",
                    position,
                ),
                "observed_at": _observed_at(values["observed_at"], position),
            }
        )

    output = pd.DataFrame(normalized, columns=STOCK_DAILY_PRICE_COLUMNS)
    for field in PRICE_NULLABLE_FIELDS:
        output[field] = pd.Series(
            [record[field] for record in normalized],
            index=output.index,
            dtype="object",
        )
    duplicate = output.duplicated(
        subset=["security_id", "trade_date", "adjustment", "source"],
        keep=False,
    )
    if duplicate.any():
        key = tuple(
            output.loc[
                duplicate,
                ["security_id", "trade_date", "adjustment", "source"],
            ].iloc[0]
        )
        raise ValueError(f"Duplicate stock daily price key: {key!r}")
    return output.sort_values(
        ["security_id", "trade_date", "adjustment", "source"]
    ).reset_index(drop=True)
