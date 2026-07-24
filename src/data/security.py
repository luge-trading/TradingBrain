"""Strict offline normalization for security identity and listing facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from numbers import Integral
from typing import Final

import pandas as pd


SECURITY_MASTER_COLUMNS: Final[tuple[str, ...]] = (
    "local_symbol",
    "exchange",
    "asset_type",
    "board",
    "current_name",
    "list_date",
    "delist_date",
    "current_listing_status",
    "source",
    "source_as_of_date",
)

SECURITY_LISTING_EVENT_COLUMNS: Final[tuple[str, ...]] = (
    "local_symbol",
    "exchange",
    "asset_type",
    "event_type",
    "event_date",
    "source",
)

SECURITY_EXCHANGES: Final[frozenset[str]] = frozenset({"XSHG", "XSHE"})
SECURITY_ASSET_TYPES: Final[frozenset[str]] = frozenset({"COMMON_STOCK"})
SECURITY_BOARDS: Final[frozenset[str]] = frozenset(
    {"SSE_MAIN", "SSE_STAR", "SZSE_MAIN", "SZSE_CHINEXT"}
)
SECURITY_LISTING_STATUSES: Final[frozenset[str]] = frozenset(
    {"LISTED", "DELISTED"}
)
SECURITY_LISTING_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"LISTED", "DELISTED"}
)
EXCHANGE_BOARD_PAIRS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("XSHG", "SSE_MAIN"),
        ("XSHG", "SSE_STAR"),
        ("XSHE", "SZSE_MAIN"),
        ("XSHE", "SZSE_CHINEXT"),
    }
)


@dataclass(frozen=True, slots=True)
class SecurityIdentity:
    """Immutable, strictly validated identity required by market-data providers."""

    security_id: int
    exchange: str
    asset_type: str
    local_symbol: str
    board: str
    current_listing_status: str
    list_date: str
    delist_date: str | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.security_id, bool)
            or not isinstance(self.security_id, Integral)
            or self.security_id <= 0
        ):
            raise ValueError(f"Invalid security_id: {self.security_id!r}")
        if self.exchange not in SECURITY_EXCHANGES:
            raise ValueError(f"Invalid exchange: {self.exchange!r}")
        if self.asset_type not in SECURITY_ASSET_TYPES:
            raise ValueError(f"Invalid asset_type: {self.asset_type!r}")
        validate_local_symbol(self.local_symbol)
        if self.board not in SECURITY_BOARDS:
            raise ValueError(f"Invalid board: {self.board!r}")
        if (self.exchange, self.board) not in EXCHANGE_BOARD_PAIRS:
            raise ValueError(
                "Invalid exchange/board combination: "
                f"{self.exchange}/{self.board}"
            )
        if self.current_listing_status not in SECURITY_LISTING_STATUSES:
            raise ValueError(
                "Invalid current_listing_status: "
                f"{self.current_listing_status!r}"
            )
        _iso_date(self.list_date, "list_date", 0)
        if self.delist_date is not None:
            _iso_date(self.delist_date, "delist_date", 0)
            if self.delist_date < self.list_date:
                raise ValueError("delist_date precedes list_date")
        if self.current_listing_status == "LISTED" and self.delist_date is not None:
            raise ValueError("LISTED security must not have delist_date")
        if self.current_listing_status == "DELISTED" and self.delist_date is None:
            raise ValueError("DELISTED security must have delist_date")
        object.__setattr__(self, "security_id", int(self.security_id))


def _is_missing(value: object) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _required_text(value: object, field: str, row: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid {field} at row {row}: must be a non-empty string")
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


def validate_local_symbol(value: object, *, row: int | None = None) -> str:
    """Return a six-character symbol containing ASCII decimal digits only."""
    valid = (
        isinstance(value, str)
        and len(value) == 6
        and all("0" <= character <= "9" for character in value)
    )
    if not valid:
        location = "" if row is None else f" at row {row}"
        raise ValueError(f"Invalid local_symbol{location}: {value!r}")
    return value


def _iso_date(value: object, field: str, row: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Invalid {field} at row {row}: {value!r}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field} at row {row}: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"Invalid {field} at row {row}: {value!r}")
    return value


def _nullable_iso_date(value: object, field: str, row: int) -> str | None:
    if _is_missing(value):
        return None
    return _iso_date(value, field, row)


def _require_columns(data: pd.DataFrame, columns: tuple[str, ...], label: str) -> pd.DataFrame:
    if not isinstance(data, pd.DataFrame):
        raise TypeError(f"{label} must be a pandas DataFrame")
    if data.columns.duplicated().any():
        duplicates = data.columns[data.columns.duplicated()].tolist()
        raise ValueError(f"{label} contains duplicate columns: {duplicates}")
    missing = [column for column in columns if column not in data.columns]
    if missing:
        raise ValueError(f"{label} missing columns: {missing}")
    return data.loc[:, list(columns)].copy()


def normalize_security_master(data: pd.DataFrame) -> pd.DataFrame:
    """Return strictly validated current security identity snapshots."""
    result = _require_columns(data, SECURITY_MASTER_COLUMNS, "Security master data")
    normalized: list[dict[str, object]] = []

    for position, row in enumerate(result.itertuples(index=False, name=None)):
        values = dict(zip(SECURITY_MASTER_COLUMNS, row))
        local_symbol = validate_local_symbol(values["local_symbol"], row=position)
        exchange = _enum_text(values["exchange"], "exchange", SECURITY_EXCHANGES, position)
        asset_type = _enum_text(
            values["asset_type"], "asset_type", SECURITY_ASSET_TYPES, position
        )
        board = _enum_text(values["board"], "board", SECURITY_BOARDS, position)
        if (exchange, board) not in EXCHANGE_BOARD_PAIRS:
            raise ValueError(
                f"Invalid exchange/board combination at row {position}: "
                f"{exchange}/{board}"
            )
        current_name = _required_text(values["current_name"], "current_name", position)
        list_date = _iso_date(values["list_date"], "list_date", position)
        delist_date = _nullable_iso_date(values["delist_date"], "delist_date", position)
        status = _enum_text(
            values["current_listing_status"],
            "current_listing_status",
            SECURITY_LISTING_STATUSES,
            position,
        )
        if status == "LISTED" and delist_date is not None:
            raise ValueError(f"LISTED security has delist_date at row {position}")
        if status == "DELISTED" and delist_date is None:
            raise ValueError(f"DELISTED security missing delist_date at row {position}")
        if delist_date is not None and delist_date < list_date:
            raise ValueError(f"delist_date precedes list_date at row {position}")
        normalized.append(
            {
                "local_symbol": local_symbol,
                "exchange": exchange,
                "asset_type": asset_type,
                "board": board,
                "current_name": current_name,
                "list_date": list_date,
                "delist_date": delist_date,
                "current_listing_status": status,
                "source": _required_text(values["source"], "source", position),
                "source_as_of_date": _iso_date(
                    values["source_as_of_date"], "source_as_of_date", position
                ),
            }
        )

    output = pd.DataFrame(normalized, columns=SECURITY_MASTER_COLUMNS)
    duplicate = output.duplicated(
        subset=["exchange", "asset_type", "local_symbol"], keep=False
    )
    if duplicate.any():
        key = tuple(
            output.loc[duplicate, ["exchange", "asset_type", "local_symbol"]].iloc[0]
        )
        raise ValueError(f"Duplicate security natural key: {key!r}")
    return output.sort_values(
        ["exchange", "asset_type", "local_symbol"]
    ).reset_index(drop=True)


def normalize_security_listing_events(data: pd.DataFrame) -> pd.DataFrame:
    """Return strictly validated dated LISTED and DELISTED facts."""
    result = _require_columns(
        data, SECURITY_LISTING_EVENT_COLUMNS, "Security listing event data"
    )
    normalized: list[dict[str, str]] = []

    for position, row in enumerate(result.itertuples(index=False, name=None)):
        values = dict(zip(SECURITY_LISTING_EVENT_COLUMNS, row))
        normalized.append(
            {
                "local_symbol": validate_local_symbol(
                    values["local_symbol"], row=position
                ),
                "exchange": _enum_text(
                    values["exchange"], "exchange", SECURITY_EXCHANGES, position
                ),
                "asset_type": _enum_text(
                    values["asset_type"],
                    "asset_type",
                    SECURITY_ASSET_TYPES,
                    position,
                ),
                "event_type": _enum_text(
                    values["event_type"],
                    "event_type",
                    SECURITY_LISTING_EVENT_TYPES,
                    position,
                ),
                "event_date": _iso_date(values["event_date"], "event_date", position),
                "source": _required_text(values["source"], "source", position),
            }
        )

    output = pd.DataFrame(normalized, columns=SECURITY_LISTING_EVENT_COLUMNS)
    duplicate = output.duplicated(
        subset=["exchange", "asset_type", "local_symbol", "event_type"], keep=False
    )
    if duplicate.any():
        key = tuple(
            output.loc[
                duplicate,
                ["exchange", "asset_type", "local_symbol", "event_type"],
            ].iloc[0]
        )
        raise ValueError(f"Duplicate security listing event key: {key!r}")
    return output.sort_values(
        ["exchange", "asset_type", "local_symbol", "event_date", "event_type"]
    ).reset_index(drop=True)
