"""SQLite storage for standardized stock market data."""

from __future__ import annotations

from collections.abc import Iterable
import sqlite3
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Final

import pandas as pd

from src.data.index import INDEX_KLINE_COLUMNS, get_index_definition, normalize_index_daily_kline
from src.data.market import MarketDaily, calculate_advance_ratio, validate_trade_date
from src.data.sector import (
    EASTMONEY_INDUSTRY_KLINE_SOURCE,
    EASTMONEY_INDUSTRY_SECTOR_TYPE,
    SECTOR_KLINE_COLUMNS,
    SectorDefinition,
    normalize_sector_daily_kline,
    normalize_sector_registry,
    validate_sector_code,
    validate_sector_level,
)


DEFAULT_DATABASE_PATH: Final[Path] = Path("data/trading_brain.db")

KLINE_COLUMNS: Final[tuple[str, ...]] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)

CREATE_STOCK_DAILY_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS stock_daily (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    amount REAL NOT NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);
"""

UPSERT_STOCK_DAILY_SQL: Final[str] = """
INSERT INTO stock_daily (
    symbol,
    trade_date,
    open,
    high,
    low,
    close,
    volume,
    amount,
    source,
    updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, trade_date) DO UPDATE SET
    open = excluded.open,
    high = excluded.high,
    low = excluded.low,
    close = excluded.close,
    volume = excluded.volume,
    amount = excluded.amount,
    source = excluded.source,
    updated_at = excluded.updated_at;
"""

CREATE_INDEX_DAILY_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS index_daily (
    index_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    amount REAL NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (index_code, trade_date)
);
"""

UPSERT_INDEX_DAILY_SQL: Final[str] = """
INSERT INTO index_daily (
    index_code, trade_date, open, high, low, close, volume, amount, source, updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(index_code, trade_date) DO UPDATE SET
    open = excluded.open,
    high = excluded.high,
    low = excluded.low,
    close = excluded.close,
    volume = excluded.volume,
    amount = excluded.amount,
    source = excluded.source,
    updated_at = excluded.updated_at;
"""

CREATE_MARKET_DAILY_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS market_daily (
    trade_date TEXT PRIMARY KEY,
    sh_amount_yuan INTEGER NULL,
    sz_amount_yuan INTEGER NULL,
    total_amount_yuan INTEGER NULL,
    advance_count INTEGER NULL,
    decline_count INTEGER NULL,
    flat_count INTEGER NULL,
    sh_amount_source TEXT NULL,
    sz_amount_source TEXT NULL,
    breadth_source TEXT NULL,
    updated_at TEXT NOT NULL
);
"""

UPSERT_MARKET_DAILY_SQL: Final[str] = """
INSERT INTO market_daily (
    trade_date, sh_amount_yuan, sz_amount_yuan, total_amount_yuan,
    advance_count, decline_count, flat_count,
    sh_amount_source, sz_amount_source, breadth_source, updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(trade_date) DO UPDATE SET
    sh_amount_yuan = excluded.sh_amount_yuan,
    sz_amount_yuan = excluded.sz_amount_yuan,
    total_amount_yuan = excluded.total_amount_yuan,
    advance_count = excluded.advance_count,
    decline_count = excluded.decline_count,
    flat_count = excluded.flat_count,
    sh_amount_source = excluded.sh_amount_source,
    sz_amount_source = excluded.sz_amount_source,
    breadth_source = excluded.breadth_source,
    updated_at = excluded.updated_at;
"""

CREATE_SECTOR_REGISTRY_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS sector_registry (
    sector_type TEXT NOT NULL,
    sector_level INTEGER NOT NULL
        CHECK (sector_level IN (1, 2, 3)),
    sector_code TEXT NOT NULL,
    sector_name TEXT NOT NULL,
    source TEXT NOT NULL,
    is_active INTEGER NOT NULL
        CHECK (is_active IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        sector_type,
        sector_level,
        sector_code
    )
);
"""

CREATE_SECTOR_DAILY_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS sector_daily (
    sector_type TEXT NOT NULL,
    sector_level INTEGER NOT NULL
        CHECK (sector_level IN (1, 2, 3)),
    sector_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NULL,
    amount REAL NULL,
    change_pct REAL NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (
        sector_type,
        sector_level,
        sector_code,
        trade_date
    )
);
"""

UPSERT_SECTOR_REGISTRY_SQL: Final[str] = """
INSERT INTO sector_registry (
    sector_type, sector_level, sector_code, sector_name, source, is_active, updated_at
)
VALUES (?, ?, ?, ?, ?, 1, ?)
ON CONFLICT(sector_type, sector_level, sector_code) DO UPDATE SET
    sector_name = excluded.sector_name,
    source = excluded.source,
    is_active = 1,
    updated_at = excluded.updated_at;
"""

UPSERT_SECTOR_DAILY_SQL: Final[str] = """
INSERT INTO sector_daily (
    sector_type, sector_level, sector_code, trade_date,
    open, high, low, close, volume, amount, change_pct, source, updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(sector_type, sector_level, sector_code, trade_date) DO UPDATE SET
    open = excluded.open,
    high = excluded.high,
    low = excluded.low,
    close = excluded.close,
    volume = excluded.volume,
    amount = excluded.amount,
    change_pct = excluded.change_pct,
    source = excluded.source,
    updated_at = excluded.updated_at;
"""


def _validate_symbol(symbol: str) -> None:
    """Validate a six-digit stock code."""
    if (
        not isinstance(symbol, str)
        or len(symbol) != 6
        or not symbol.isdigit()
    ):
        raise ValueError(f"Invalid stock code: {symbol!r}")


def _prepare_database_path(
    database_path: str | PathLike[str],
) -> Path:
    """Normalize the database path and create its parent directory."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def init_database(
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> None:
    """Create the SQLite database and required tables."""
    path = _prepare_database_path(database_path)

    try:
        with sqlite3.connect(path) as connection:
            connection.execute(CREATE_STOCK_DAILY_TABLE_SQL)
            connection.execute(CREATE_INDEX_DAILY_TABLE_SQL)
            connection.execute(CREATE_MARKET_DAILY_TABLE_SQL)
            connection.execute(CREATE_SECTOR_REGISTRY_TABLE_SQL)
            connection.execute(CREATE_SECTOR_DAILY_TABLE_SQL)
    except sqlite3.Error as exc:
        raise RuntimeError("Unable to initialize database") from exc


def save_daily_kline(
    symbol: str,
    data: pd.DataFrame,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    source: str = "eastmoney",
) -> int:
    """Insert or update standardized daily K-line records."""
    _validate_symbol(symbol)

    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame")

    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be a non-empty string")

    missing_columns = [
        column
        for column in KLINE_COLUMNS
        if column not in data.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required K-line columns: {missing_columns}"
        )

    if data.empty:
        return 0

    updated_at = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )

    records: list[tuple[object, ...]] = []

    try:
        selected_data = data.loc[:, list(KLINE_COLUMNS)]

        for row in selected_data.itertuples(index=False, name=None):
            (
                trade_date,
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
                amount,
            ) = row

            records.append(
                (
                    symbol,
                    str(trade_date),
                    float(open_price),
                    float(high_price),
                    float(low_price),
                    float(close_price),
                    int(volume),
                    float(amount),
                    source.strip(),
                    updated_at,
                )
            )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "K-line data contains invalid values"
        ) from exc

    path = _prepare_database_path(database_path)
    init_database(path)

    try:
        with sqlite3.connect(path) as connection:
            connection.executemany(
                UPSERT_STOCK_DAILY_SQL,
                records,
            )
    except sqlite3.Error as exc:
        raise RuntimeError("Unable to save K-line data") from exc

    return len(records)


def load_daily_kline(
    symbol: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    """Load daily K-line records ordered by trade date."""
    _validate_symbol(symbol)

    path = _prepare_database_path(database_path)
    init_database(path)

    query = """
    SELECT
        trade_date AS date,
        open,
        high,
        low,
        close,
        volume,
        amount
    FROM stock_daily
    WHERE symbol = ?
    ORDER BY trade_date ASC;
    """

    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(
                query,
                connection,
                params=(symbol,),
            )
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError("Unable to load K-line data") from exc

    if not result.empty:
        result["volume"] = result["volume"].astype("int64")

    return result


def get_latest_trade_date(
    symbol: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> str | None:
    """Return the latest stored trade date for a stock."""
    _validate_symbol(symbol)

    path = _prepare_database_path(database_path)
    init_database(path)

    query = """
    SELECT MAX(trade_date)
    FROM stock_daily
    WHERE symbol = ?;
    """

    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                query,
                (symbol,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(
            "Unable to query latest trade date"
        ) from exc

    if row is None or row[0] is None:
        return None

    return str(row[0])


def save_index_daily_kline(
    index_code: str,
    data: pd.DataFrame,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    source: str = "eastmoney",
) -> int:
    get_index_definition(index_code)
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be a non-empty string")
    normalized = normalize_index_daily_kline(data)
    if normalized.empty:
        return 0
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    for row in normalized.itertuples(index=False, name=None):
        trade_date, open_price, high_price, low_price, close_price, volume, amount = row
        records.append((
            index_code, trade_date, float(open_price), float(high_price),
            float(low_price), float(close_price), int(volume),
            None if pd.isna(amount) else float(amount), source.strip(), updated_at,
        ))
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            connection.executemany(UPSERT_INDEX_DAILY_SQL, records)
    except sqlite3.Error as exc:
        raise RuntimeError(f"Unable to save index K-line data for {index_code}") from exc
    return len(records)


def load_index_daily_kline(
    index_code: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> pd.DataFrame:
    get_index_definition(index_code)
    path = _prepare_database_path(database_path)
    init_database(path)
    query = """
    SELECT trade_date AS date, open, high, low, close, volume, amount
    FROM index_daily WHERE index_code = ? ORDER BY trade_date ASC;
    """
    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(query, connection, params=(index_code,))
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError(f"Unable to load index K-line data for {index_code}") from exc
    if not result.empty:
        result["volume"] = result["volume"].astype("int64")
    return result.loc[:, list(INDEX_KLINE_COLUMNS)]


def get_latest_index_trade_date(
    index_code: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> str | None:
    get_index_definition(index_code)
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "SELECT MAX(trade_date) FROM index_daily WHERE index_code = ?",
                (index_code,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Unable to query latest index trade date for {index_code}") from exc
    return None if row is None or row[0] is None else str(row[0])


def save_market_daily(
    record: MarketDaily,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> int:
    """Idempotently store one validated market-day record."""
    if not isinstance(record, MarketDaily):
        raise TypeError("record must be a MarketDaily")
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    values = (
        record.trade_date,
        record.sh_amount_yuan,
        record.sz_amount_yuan,
        record.total_amount_yuan,
        record.advance_count,
        record.decline_count,
        record.flat_count,
        record.sh_amount_source,
        record.sz_amount_source,
        record.breadth_source,
        updated_at,
    )
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            connection.execute(UPSERT_MARKET_DAILY_SQL, values)
    except sqlite3.Error as exc:
        raise RuntimeError(f"Unable to save market daily data for {record.trade_date}") from exc
    return 1


def get_market_daily(
    trade_date: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> MarketDaily | None:
    """Load one market-day record as a validated value object."""
    trade_date = validate_trade_date(trade_date)
    path = _prepare_database_path(database_path)
    init_database(path)
    query = """
    SELECT trade_date, sh_amount_yuan, sz_amount_yuan, total_amount_yuan,
           advance_count, decline_count, flat_count,
           sh_amount_source, sz_amount_source, breadth_source
    FROM market_daily WHERE trade_date = ?;
    """
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute(query, (trade_date,)).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Unable to load market daily data for {trade_date}") from exc
    return None if row is None else MarketDaily(*row)


def load_market_daily(
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load market facts in date order and derive advance_ratio on read."""
    if start_date is not None:
        start_date = validate_trade_date(start_date)
    if end_date is not None:
        end_date = validate_trade_date(end_date)
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    conditions: list[str] = []
    params: list[str] = []
    if start_date is not None:
        conditions.append("trade_date >= ?")
        params.append(start_date)
    if end_date is not None:
        conditions.append("trade_date <= ?")
        params.append(end_date)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
    SELECT trade_date, sh_amount_yuan, sz_amount_yuan, total_amount_yuan,
           advance_count, decline_count, flat_count,
           sh_amount_source, sz_amount_source, breadth_source, updated_at
    FROM market_daily{where} ORDER BY trade_date ASC;
    """
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(query, connection, params=params)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError("Unable to load market daily data") from exc
    for column in (
        "sh_amount_yuan", "sz_amount_yuan", "total_amount_yuan",
        "advance_count", "decline_count", "flat_count",
    ):
        result[column] = result[column].astype("Int64")
    result["advance_ratio"] = [
        calculate_advance_ratio(MarketDaily(
            trade_date=row.trade_date,
            sh_amount_yuan=None if pd.isna(row.sh_amount_yuan) else int(row.sh_amount_yuan),
            sz_amount_yuan=None if pd.isna(row.sz_amount_yuan) else int(row.sz_amount_yuan),
            total_amount_yuan=None if pd.isna(row.total_amount_yuan) else int(row.total_amount_yuan),
            advance_count=None if pd.isna(row.advance_count) else int(row.advance_count),
            decline_count=None if pd.isna(row.decline_count) else int(row.decline_count),
            flat_count=None if pd.isna(row.flat_count) else int(row.flat_count),
            sh_amount_source=row.sh_amount_source,
            sz_amount_source=row.sz_amount_source,
            breadth_source=row.breadth_source,
        ))
        for row in result.itertuples(index=False)
    ]
    return result


def get_latest_market_trade_date(
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> str | None:
    """Return the latest stored market trade date."""
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute("SELECT MAX(trade_date) FROM market_daily").fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError("Unable to query latest market trade date") from exc
    return None if row is None or row[0] is None else str(row[0])


def save_sector_registry_snapshot(
    definitions: Iterable[SectorDefinition],
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> int:
    """Atomically replace the current active industry registry snapshot."""
    normalized = normalize_sector_registry(definitions)
    sector_types = {item.sector_type for item in normalized}
    if len(sector_types) != 1:
        raise ValueError("Sector registry snapshot must contain one sector type")
    sector_type = normalized[0].sector_type
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = [
        (
            item.sector_type,
            item.sector_level,
            item.sector_code,
            item.sector_name,
            item.source,
            updated_at,
        )
        for item in normalized
    ]
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE sector_registry SET is_active = 0, updated_at = ? WHERE sector_type = ?",
                (updated_at, sector_type),
            )
            connection.executemany(UPSERT_SECTOR_REGISTRY_SQL, records)
    except sqlite3.Error as exc:
        raise RuntimeError("Unable to save sector registry snapshot") from exc
    return len(records)


def load_sector_registry(
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    sector_level: int | None = None,
    active_only: bool = True,
) -> pd.DataFrame:
    """Load current or historical registry rows in stable business-key order."""
    if sector_level is not None:
        sector_level = validate_sector_level(sector_level)
    if not isinstance(active_only, bool):
        raise TypeError("active_only must be a bool")
    conditions = ["sector_type = ?"]
    params: list[object] = [EASTMONEY_INDUSTRY_SECTOR_TYPE]
    if sector_level is not None:
        conditions.append("sector_level = ?")
        params.append(sector_level)
    if active_only:
        conditions.append("is_active = 1")
    query = f"""
    SELECT sector_type, sector_level, sector_code, sector_name, source, is_active, updated_at
    FROM sector_registry
    WHERE {' AND '.join(conditions)}
    ORDER BY sector_level ASC, sector_code ASC;
    """
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(query, connection, params=params)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError("Unable to load sector registry") from exc
    result["is_active"] = result["is_active"].astype(bool)
    return result


def get_sector_definition(
    sector_type: str,
    sector_level: int,
    sector_code: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    active_only: bool = True,
) -> SectorDefinition | None:
    """Return one database-backed sector definition, if present."""
    if sector_type != EASTMONEY_INDUSTRY_SECTOR_TYPE:
        raise ValueError(f"Invalid sector type: {sector_type!r}")
    level = validate_sector_level(sector_level)
    code = validate_sector_code(sector_code)
    if not isinstance(active_only, bool):
        raise TypeError("active_only must be a bool")
    active_clause = " AND is_active = 1" if active_only else ""
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "SELECT sector_name, source FROM sector_registry "
                "WHERE sector_type = ? AND sector_level = ? AND sector_code = ?"
                + active_clause,
                (sector_type, level, code),
            ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Unable to load sector definition for {code}") from exc
    if row is None:
        return None
    return SectorDefinition(sector_type, level, code, str(row[0]), str(row[1]))


def save_sector_daily_kline(
    definition: SectorDefinition,
    data: pd.DataFrame,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    source: str = EASTMONEY_INDUSTRY_KLINE_SOURCE,
) -> int:
    """Idempotently store a validated daily batch for an active sector."""
    if not isinstance(definition, SectorDefinition):
        raise TypeError("definition must be a SectorDefinition")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be a non-empty string")
    current = get_sector_definition(
        definition.sector_type,
        definition.sector_level,
        definition.sector_code,
        database_path=database_path,
        active_only=True,
    )
    if current is None:
        raise ValueError(f"Sector is not active in registry: {definition.sector_code}")
    if current.sector_name != definition.sector_name:
        raise ValueError(f"Sector name does not match current registry: {definition.sector_code}")
    if current.source != definition.source:
        raise ValueError(f"Sector registry source does not match: {definition.sector_code}")
    normalized = normalize_sector_daily_kline(data)
    if normalized.empty:
        return 0
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    for row in normalized.itertuples(index=False, name=None):
        trade_date, open_price, high_price, low_price, close_price, volume, amount, change_pct = row
        records.append((
            definition.sector_type,
            definition.sector_level,
            definition.sector_code,
            trade_date,
            float(open_price),
            float(high_price),
            float(low_price),
            float(close_price),
            None if pd.isna(volume) else int(volume),
            None if pd.isna(amount) else float(amount),
            None if pd.isna(change_pct) else float(change_pct),
            source.strip(),
            updated_at,
        ))
    path = _prepare_database_path(database_path)
    try:
        with sqlite3.connect(path) as connection:
            connection.executemany(UPSERT_SECTOR_DAILY_SQL, records)
    except sqlite3.Error as exc:
        raise RuntimeError(f"Unable to save sector daily data for {definition.sector_code}") from exc
    return len(records)


def load_sector_daily_kline(
    definition: SectorDefinition,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load historical sector facts, including facts for inactive definitions."""
    if not isinstance(definition, SectorDefinition):
        raise TypeError("definition must be a SectorDefinition")
    if start_date is not None:
        start_date = validate_trade_date(start_date)
    if end_date is not None:
        end_date = validate_trade_date(end_date)
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    conditions = ["sector_type = ?", "sector_level = ?", "sector_code = ?"]
    params: list[object] = [definition.sector_type, definition.sector_level, definition.sector_code]
    if start_date is not None:
        conditions.append("trade_date >= ?")
        params.append(start_date)
    if end_date is not None:
        conditions.append("trade_date <= ?")
        params.append(end_date)
    query = f"""
    SELECT trade_date AS date, open, high, low, close, volume, amount, change_pct
    FROM sector_daily WHERE {' AND '.join(conditions)} ORDER BY trade_date ASC;
    """
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(query, connection, params=params)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError(f"Unable to load sector daily data for {definition.sector_code}") from exc
    result["volume"] = result["volume"].astype("Int64")
    return result.loc[:, list(SECTOR_KLINE_COLUMNS)]


def get_latest_sector_trade_date(
    definition: SectorDefinition,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> str | None:
    """Return the latest stored trade date for the full sector business key."""
    if not isinstance(definition, SectorDefinition):
        raise TypeError("definition must be a SectorDefinition")
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "SELECT MAX(trade_date) FROM sector_daily "
                "WHERE sector_type = ? AND sector_level = ? AND sector_code = ?",
                (definition.sector_type, definition.sector_level, definition.sector_code),
            ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Unable to query latest sector trade date for {definition.sector_code}") from exc
    return None if row is None or row[0] is None else str(row[0])
