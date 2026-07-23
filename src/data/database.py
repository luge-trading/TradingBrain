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
from src.data.security import (
    SECURITY_ASSET_TYPES,
    SECURITY_BOARDS,
    SECURITY_EXCHANGES,
    SECURITY_LISTING_EVENT_TYPES,
    SECURITY_LISTING_STATUSES,
    normalize_security_listing_events,
    normalize_security_master,
    validate_local_symbol,
)
from src.data.price import (
    PRICE_FACT_FIELDS,
    PRICE_NULLABLE_FIELDS,
    StockDailyPriceSaveResult,
    normalize_stock_daily_prices,
    validate_price_adjustment,
    validate_price_date,
    validate_price_source,
    validate_security_id,
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

SECTOR_DAILY_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    "sector_type",
    "sector_level",
    "sector_code",
    "sector_name",
    "is_active",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "change_pct",
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

CREATE_SECURITY_MASTER_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS security_master (
    security_id INTEGER PRIMARY KEY,
    local_symbol TEXT NOT NULL
        CHECK (length(local_symbol) = 6 AND local_symbol NOT GLOB '*[^0-9]*'),
    exchange TEXT NOT NULL
        CHECK (exchange IN ('XSHG', 'XSHE')),
    asset_type TEXT NOT NULL
        CHECK (asset_type = 'COMMON_STOCK'),
    board TEXT NOT NULL
        CHECK (board IN ('SSE_MAIN', 'SSE_STAR', 'SZSE_MAIN', 'SZSE_CHINEXT')),
    current_name TEXT NOT NULL
        CHECK (length(trim(current_name)) > 0),
    list_date TEXT NOT NULL,
    delist_date TEXT,
    current_listing_status TEXT NOT NULL
        CHECK (current_listing_status IN ('LISTED', 'DELISTED')),
    source TEXT NOT NULL
        CHECK (length(trim(source)) > 0),
    source_as_of_date TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (exchange, asset_type, local_symbol),
    CHECK (
        (exchange = 'XSHG' AND board IN ('SSE_MAIN', 'SSE_STAR'))
        OR (exchange = 'XSHE' AND board IN ('SZSE_MAIN', 'SZSE_CHINEXT'))
    ),
    CHECK (
        (current_listing_status = 'LISTED' AND delist_date IS NULL)
        OR (
            current_listing_status = 'DELISTED'
            AND delist_date IS NOT NULL
            AND delist_date >= list_date
        )
    )
);
"""

CREATE_SECURITY_LISTING_EVENT_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS security_listing_event (
    security_id INTEGER NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('LISTED', 'DELISTED')),
    event_date TEXT NOT NULL,
    source TEXT NOT NULL
        CHECK (length(trim(source)) > 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (security_id, event_type),
    FOREIGN KEY (security_id)
        REFERENCES security_master(security_id)
        ON DELETE RESTRICT
);
"""

CREATE_STOCK_DAILY_PRICE_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS stock_daily_price (
    security_id INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    adjustment TEXT NOT NULL
        CHECK (adjustment IN ('UNADJUSTED', 'QFQ', 'HFQ')),
    source TEXT NOT NULL
        CHECK (length(trim(source)) > 0),
    provider_adjustment TEXT NOT NULL
        CHECK (length(trim(provider_adjustment)) > 0),
    open REAL NOT NULL CHECK (open > 0),
    high REAL NOT NULL CHECK (high > 0),
    low REAL NOT NULL CHECK (low > 0),
    close REAL NOT NULL CHECK (close > 0),
    volume INTEGER NOT NULL CHECK (volume >= 0),
    volume_unit TEXT NOT NULL
        CHECK (volume_unit IN ('PROVIDER_NATIVE', 'SHARE', 'LOT')),
    amount REAL CHECK (amount IS NULL OR amount >= 0),
    amount_unit TEXT,
    is_final INTEGER NOT NULL CHECK (is_final IN (0, 1)),
    provider_as_of_date TEXT,
    observed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (security_id, trade_date, adjustment, source),
    FOREIGN KEY (security_id)
        REFERENCES security_master(security_id)
        ON DELETE RESTRICT,
    CHECK (low <= open AND open <= high),
    CHECK (low <= close AND close <= high),
    CHECK (
        (amount IS NULL AND amount_unit IS NULL)
        OR (
            amount IS NOT NULL
            AND amount_unit IS NOT NULL
            AND amount_unit IN ('PROVIDER_NATIVE', 'CNY')
        )
    )
);
"""

CREATE_STOCK_DAILY_PRICE_TRADE_DATE_INDEX_SQL: Final[str] = """
CREATE INDEX IF NOT EXISTS idx_stock_daily_price_trade_date
ON stock_daily_price (trade_date, adjustment, source, security_id);
"""

CREATE_STOCK_DAILY_PRICE_SERIES_INDEX_SQL: Final[str] = """
CREATE INDEX IF NOT EXISTS idx_stock_daily_price_series
ON stock_daily_price (security_id, adjustment, source, trade_date);
"""

CREATE_STOCK_DAILY_PRICE_REVISION_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS stock_daily_price_revision (
    revision_id INTEGER PRIMARY KEY,
    security_id INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    source TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    changed_fields TEXT NOT NULL CHECK (length(changed_fields) > 0),
    old_provider_adjustment TEXT NOT NULL,
    old_open REAL NOT NULL,
    old_high REAL NOT NULL,
    old_low REAL NOT NULL,
    old_close REAL NOT NULL,
    old_volume INTEGER NOT NULL,
    old_volume_unit TEXT NOT NULL,
    old_amount REAL,
    old_amount_unit TEXT,
    old_is_final INTEGER NOT NULL,
    old_provider_as_of_date TEXT,
    old_observed_at TEXT NOT NULL,
    new_provider_adjustment TEXT NOT NULL,
    new_open REAL NOT NULL,
    new_high REAL NOT NULL,
    new_low REAL NOT NULL,
    new_close REAL NOT NULL,
    new_volume INTEGER NOT NULL,
    new_volume_unit TEXT NOT NULL,
    new_amount REAL,
    new_amount_unit TEXT,
    new_is_final INTEGER NOT NULL,
    new_provider_as_of_date TEXT,
    new_observed_at TEXT NOT NULL,
    revised_at TEXT NOT NULL,
    UNIQUE (security_id, trade_date, adjustment, source, revision_number),
    FOREIGN KEY (security_id, trade_date, adjustment, source)
        REFERENCES stock_daily_price(
            security_id, trade_date, adjustment, source
        )
        ON DELETE RESTRICT
);
"""

STOCK_DAILY_PRICE_RESULT_COLUMNS: Final[tuple[str, ...]] = (
    "security_id", "exchange", "asset_type", "local_symbol", "board",
    "trade_date", "adjustment", "source", "provider_adjustment",
    "open", "high", "low", "close", "volume", "volume_unit", "amount",
    "amount_unit", "is_final", "provider_as_of_date", "observed_at",
    "updated_at",
)

STOCK_DAILY_PRICE_REVISION_RESULT_COLUMNS: Final[tuple[str, ...]] = (
    "revision_id", "security_id", "exchange", "asset_type", "local_symbol",
    "board", "trade_date", "adjustment", "source", "revision_number",
    "changed_fields", "old_provider_adjustment", "old_open", "old_high",
    "old_low", "old_close", "old_volume", "old_volume_unit", "old_amount",
    "old_amount_unit", "old_is_final", "old_provider_as_of_date",
    "old_observed_at", "new_provider_adjustment", "new_open", "new_high",
    "new_low", "new_close", "new_volume", "new_volume_unit", "new_amount",
    "new_amount_unit", "new_is_final", "new_provider_as_of_date",
    "new_observed_at", "revised_at",
)

LATEST_STOCK_DAILY_PRICE_DATE_COLUMNS: Final[tuple[str, ...]] = (
    "security_id", "latest_trade_date",
)

SECURITY_MASTER_RESULT_COLUMNS: Final[tuple[str, ...]] = (
    "security_id", "local_symbol", "exchange", "asset_type", "board",
    "current_name", "list_date", "delist_date", "current_listing_status",
    "source", "source_as_of_date", "updated_at",
)

SECURITY_LISTING_EVENT_RESULT_COLUMNS: Final[tuple[str, ...]] = (
    "security_id", "local_symbol", "exchange", "asset_type", "event_type",
    "event_date", "source", "updated_at",
)


def _validate_symbol(symbol: object) -> None:
    """Validate a six-digit stock code."""
    try:
        validate_local_symbol(symbol)
    except ValueError as exc:
        raise ValueError(f"Invalid stock code: {symbol!r}") from exc


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
            connection.execute(CREATE_SECURITY_MASTER_TABLE_SQL)
            connection.execute(CREATE_SECURITY_LISTING_EVENT_TABLE_SQL)
            connection.execute(CREATE_STOCK_DAILY_PRICE_TABLE_SQL)
            connection.execute(CREATE_STOCK_DAILY_PRICE_TRADE_DATE_INDEX_SQL)
            connection.execute(CREATE_STOCK_DAILY_PRICE_SERIES_INDEX_SQL)
            connection.execute(CREATE_STOCK_DAILY_PRICE_REVISION_TABLE_SQL)
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


def load_sector_daily_panel(
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    sector_level: int | None = None,
    active_only: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load joined industry registry and daily facts without deriving metrics."""
    if sector_level is not None:
        sector_level = validate_sector_level(sector_level)
    if not isinstance(active_only, bool):
        raise TypeError("active_only must be a bool")
    if start_date is not None:
        start_date = validate_trade_date(start_date)
    if end_date is not None:
        end_date = validate_trade_date(end_date)
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    conditions = ["registry.sector_type = ?"]
    params: list[object] = [EASTMONEY_INDUSTRY_SECTOR_TYPE]
    if sector_level is not None:
        conditions.append("registry.sector_level = ?")
        params.append(sector_level)
    if active_only:
        conditions.append("registry.is_active = 1")
    if start_date is not None:
        conditions.append("daily.trade_date >= ?")
        params.append(start_date)
    if end_date is not None:
        conditions.append("daily.trade_date <= ?")
        params.append(end_date)

    query = f"""
    SELECT
        registry.sector_type,
        registry.sector_level,
        registry.sector_code,
        registry.sector_name,
        registry.is_active,
        daily.trade_date AS date,
        daily.open,
        daily.high,
        daily.low,
        daily.close,
        daily.volume,
        daily.amount,
        daily.change_pct
    FROM sector_daily AS daily
    INNER JOIN sector_registry AS registry
        ON daily.sector_type = registry.sector_type
       AND daily.sector_level = registry.sector_level
       AND daily.sector_code = registry.sector_code
    WHERE {' AND '.join(conditions)}
    ORDER BY registry.sector_level ASC, registry.sector_code ASC, daily.trade_date ASC;
    """
    path = _prepare_database_path(database_path)
    try:
        init_database(path)
    except RuntimeError as exc:
        raise RuntimeError("Unable to load sector daily panel") from exc
    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(query, connection, params=params)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError("Unable to load sector daily panel") from exc

    result = result.loc[:, list(SECTOR_DAILY_PANEL_COLUMNS)]
    result["sector_level"] = result["sector_level"].astype("int64")
    result["is_active"] = result["is_active"].astype(bool)
    result["volume"] = result["volume"].astype("Int64")
    for column in ("open", "high", "low", "close", "amount", "change_pct"):
        result[column] = result[column].astype("float64")
    return result


def save_security_master(
    data: pd.DataFrame,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> int:
    """Insert or advance strictly ordered current security snapshots atomically."""
    normalized = normalize_security_master(data)
    if normalized.empty:
        return 0

    path = _prepare_database_path(database_path)
    init_database(path)
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = 0

    try:
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            for record in normalized.to_dict("records"):
                key = (
                    record["exchange"],
                    record["asset_type"],
                    record["local_symbol"],
                )
                existing = connection.execute(
                    """
                    SELECT security_id, board, current_name, list_date, delist_date,
                           current_listing_status, source, source_as_of_date
                    FROM security_master
                    WHERE exchange = ? AND asset_type = ? AND local_symbol = ?
                    """,
                    key,
                ).fetchone()

                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO security_master (
                            local_symbol, exchange, asset_type, board, current_name,
                            list_date, delist_date, current_listing_status, source,
                            source_as_of_date, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record["local_symbol"], record["exchange"],
                            record["asset_type"], record["board"],
                            record["current_name"], record["list_date"],
                            record["delist_date"], record["current_listing_status"],
                            record["source"], record["source_as_of_date"], updated_at,
                        ),
                    )
                    changed += 1
                    continue

                (
                    security_id,
                    old_board,
                    old_name,
                    old_list_date,
                    old_delist_date,
                    old_status,
                    old_source,
                    old_as_of,
                ) = existing
                new_as_of = record["source_as_of_date"]
                if new_as_of < old_as_of:
                    raise ValueError(f"Older security snapshot rejected for {key!r}")

                comparable_old = (
                    old_board, old_name, old_list_date, old_delist_date,
                    old_status, old_source, old_as_of,
                )
                comparable_new = (
                    record["board"], record["current_name"], record["list_date"],
                    record["delist_date"], record["current_listing_status"],
                    record["source"], new_as_of,
                )
                if new_as_of == old_as_of:
                    if comparable_new == comparable_old:
                        continue
                    raise ValueError(f"Conflicting same-date security snapshot for {key!r}")

                if record["board"] != old_board or record["list_date"] != old_list_date:
                    raise ValueError(f"Stable security identity changed for {key!r}")
                if old_status == "DELISTED" and record["current_listing_status"] == "LISTED":
                    raise ValueError(f"Relisting is not supported for {key!r}")
                if old_status == "DELISTED" and record["delist_date"] != old_delist_date:
                    raise ValueError(f"Delisting date is immutable for {key!r}")

                delisted_event = connection.execute(
                    """
                    SELECT event_date FROM security_listing_event
                    WHERE security_id = ? AND event_type = 'DELISTED'
                    """,
                    (security_id,),
                ).fetchone()
                if (
                    delisted_event is not None
                    and record["delist_date"] != delisted_event[0]
                ):
                    raise ValueError(f"Security snapshot conflicts with DELISTED event for {key!r}")

                connection.execute(
                    """
                    UPDATE security_master
                    SET current_name = ?, delist_date = ?, current_listing_status = ?,
                        source = ?, source_as_of_date = ?, updated_at = ?
                    WHERE security_id = ?
                    """,
                    (
                        record["current_name"], record["delist_date"],
                        record["current_listing_status"], record["source"],
                        new_as_of, updated_at, security_id,
                    ),
                )
                changed += 1
    except sqlite3.Error as exc:
        raise RuntimeError("Unable to save security master") from exc
    return changed


def _filter_values(
    values: Iterable[str] | str | None,
    *,
    field: str,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...] | None:
    if values is None:
        return None
    if isinstance(values, str):
        items = (values,)
    else:
        try:
            items = tuple(values)
        except TypeError as exc:
            raise TypeError(f"{field} must be a string or iterable of strings") from exc
    for item in items:
        if not isinstance(item, str) or not item:
            raise ValueError(f"Invalid {field} filter: {item!r}")
        if allowed is not None and item not in allowed:
            raise ValueError(f"Invalid {field} filter: {item!r}")
    return tuple(dict.fromkeys(items))


def _symbol_filter_values(
    values: Iterable[str] | str | None,
) -> tuple[str, ...] | None:
    if values is None:
        return None
    if isinstance(values, str):
        items: tuple[object, ...] = (values,)
    else:
        try:
            items = tuple(values)
        except TypeError as exc:
            raise TypeError(
                "local_symbols must be a string or iterable of strings"
            ) from exc
    validated: list[str] = []
    for item in items:
        _validate_symbol(item)
        assert isinstance(item, str)
        validated.append(item)
    return tuple(dict.fromkeys(validated))


def load_security_master(
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    exchanges: Iterable[str] | str | None = None,
    boards: Iterable[str] | str | None = None,
    listing_status: Iterable[str] | str | None = None,
) -> pd.DataFrame:
    """Load current security snapshots without inferring historical state."""
    exchange_values = _filter_values(
        exchanges, field="exchange", allowed=SECURITY_EXCHANGES
    )
    board_values = _filter_values(boards, field="board", allowed=SECURITY_BOARDS)
    status_values = _filter_values(
        listing_status, field="listing_status", allowed=SECURITY_LISTING_STATUSES
    )
    conditions: list[str] = []
    params: list[object] = []
    for column, values in (
        ("exchange", exchange_values),
        ("board", board_values),
        ("current_listing_status", status_values),
    ):
        if values is not None:
            if not values:
                conditions.append("0")
            else:
                conditions.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
    SELECT security_id, local_symbol, exchange, asset_type, board, current_name,
           list_date, delist_date, current_listing_status, source,
           source_as_of_date, updated_at
    FROM security_master
    {where}
    ORDER BY exchange ASC, local_symbol ASC;
    """
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(query, connection, params=params)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError("Unable to load security master") from exc
    return result.loc[:, list(SECURITY_MASTER_RESULT_COLUMNS)]


def save_security_listing_events(
    data: pd.DataFrame,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> int:
    """Save dated listing facts atomically without overwriting event history."""
    normalized = normalize_security_listing_events(data)
    if normalized.empty:
        return 0

    path = _prepare_database_path(database_path)
    init_database(path)
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inserted = 0
    try:
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            for record in normalized.to_dict("records"):
                key = (
                    record["exchange"], record["asset_type"], record["local_symbol"]
                )
                master = connection.execute(
                    """
                    SELECT security_id, list_date, delist_date
                    FROM security_master
                    WHERE exchange = ? AND asset_type = ? AND local_symbol = ?
                    """,
                    key,
                ).fetchone()
                if master is None:
                    raise ValueError(f"Unknown security for listing event: {key!r}")
                security_id, list_date, delist_date = master
                expected_date = list_date if record["event_type"] == "LISTED" else delist_date
                if expected_date is None or record["event_date"] != expected_date:
                    raise ValueError(
                        f"Listing event does not match security master for {key!r}"
                    )
                existing = connection.execute(
                    """
                    SELECT event_date, source FROM security_listing_event
                    WHERE security_id = ? AND event_type = ?
                    """,
                    (security_id, record["event_type"]),
                ).fetchone()
                if existing is not None:
                    if existing == (record["event_date"], record["source"]):
                        continue
                    raise ValueError(
                        f"Conflicting listing event for {key!r}/{record['event_type']}"
                    )
                connection.execute(
                    """
                    INSERT INTO security_listing_event (
                        security_id, event_type, event_date, source, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        security_id, record["event_type"], record["event_date"],
                        record["source"], updated_at,
                    ),
                )
                inserted += 1
    except sqlite3.Error as exc:
        raise RuntimeError("Unable to save security listing events") from exc
    return inserted


def load_security_listing_events(
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    local_symbols: Iterable[str] | str | None = None,
) -> pd.DataFrame:
    """Load explicit LISTED/DELISTED facts with identity and provenance."""
    symbol_values = _symbol_filter_values(local_symbols)
    conditions = ""
    params: list[object] = []
    if symbol_values is not None:
        if not symbol_values:
            conditions = "WHERE 0"
        else:
            conditions = f"WHERE master.local_symbol IN ({','.join('?' for _ in symbol_values)})"
            params.extend(symbol_values)
    query = f"""
    SELECT event.security_id, master.local_symbol, master.exchange,
           master.asset_type, event.event_type, event.event_date,
           event.source, event.updated_at
    FROM security_listing_event AS event
    INNER JOIN security_master AS master
        ON master.security_id = event.security_id
    {conditions}
    ORDER BY event.security_id ASC, event.event_date ASC, event.event_type ASC;
    """
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(query, connection, params=params)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError("Unable to load security listing events") from exc
    return result.loc[:, list(SECURITY_LISTING_EVENT_RESULT_COLUMNS)]


def _price_security_id_values(
    security_ids: Iterable[int] | None,
) -> tuple[int, ...] | None:
    if security_ids is None:
        return None
    if isinstance(security_ids, (str, bytes)):
        raise TypeError("security_ids must be an iterable of positive integers")
    try:
        items = tuple(security_ids)
    except TypeError as exc:
        raise TypeError(
            "security_ids must be an iterable of positive integers"
        ) from exc
    values = tuple(validate_security_id(item) for item in items)
    if len(set(values)) != len(values):
        raise ValueError("security_ids must not contain duplicates")
    return values


def _price_query_filters(
    *,
    adjustment: object,
    source: object,
    security_ids: Iterable[int] | None,
    start_date: object | None,
    end_date: object | None,
    security_column: str,
    date_column: str,
) -> tuple[list[str], list[object]]:
    adjustment_value = validate_price_adjustment(adjustment)
    source_value = validate_price_source(source)
    id_values = _price_security_id_values(security_ids)
    if id_values is None and (start_date is None or end_date is None):
        raise ValueError(
            "start_date and end_date are required when security_ids is None"
        )
    start_value = (
        None if start_date is None else validate_price_date(start_date, field="start_date")
    )
    end_value = (
        None if end_date is None else validate_price_date(end_date, field="end_date")
    )
    if start_value is not None and end_value is not None and start_value > end_value:
        raise ValueError("start_date must not be after end_date")

    conditions = ["price.adjustment = ?", "price.source = ?"]
    params: list[object] = [adjustment_value, source_value]
    if id_values is not None:
        if not id_values:
            conditions.append("0")
        else:
            placeholders = ",".join("?" for _ in id_values)
            conditions.append(f"{security_column} IN ({placeholders})")
            params.extend(id_values)
    if start_value is not None:
        conditions.append(f"{date_column} >= ?")
        params.append(start_value)
    if end_value is not None:
        conditions.append(f"{date_column} <= ?")
        params.append(end_value)
    return conditions, params


def _normalize_nullable_price_scalar(value: object) -> object:
    if not pd.api.types.is_scalar(value):
        raise TypeError("nullable price fact values must be scalars")
    return None if bool(pd.isna(value)) else value


def _price_fact_values(values: dict[str, object]) -> tuple[object, ...]:
    return tuple(
        _normalize_nullable_price_scalar(values[field])
        if field in PRICE_NULLABLE_FIELDS
        else values[field]
        for field in PRICE_FACT_FIELDS
    )


def save_stock_daily_prices(
    frame: pd.DataFrame,
    *,
    database_path: str | PathLike[str],
) -> StockDailyPriceSaveResult:
    """Atomically insert or revise explicit stock daily price facts."""
    normalized = normalize_stock_daily_prices(frame)
    if normalized.empty:
        return StockDailyPriceSaveResult(0, 0, 0, 0)

    path = _prepare_database_path(database_path)
    init_database(path)
    saved_at = datetime.now(timezone.utc).isoformat()
    inserted = revised = unchanged = revision_rows = 0

    try:
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            for record in normalized.to_dict("records"):
                security_id = record["security_id"]
                if connection.execute(
                    "SELECT 1 FROM security_master WHERE security_id = ?",
                    (security_id,),
                ).fetchone() is None:
                    raise ValueError(f"Unknown security_id: {security_id}")

                key = (
                    security_id,
                    record["trade_date"],
                    record["adjustment"],
                    record["source"],
                )
                new_facts = _price_fact_values(record)
                existing = connection.execute(
                    """
                    SELECT provider_adjustment, open, high, low, close, volume,
                           volume_unit, amount, amount_unit, is_final,
                           provider_as_of_date, observed_at, updated_at
                    FROM stock_daily_price
                    WHERE security_id = ? AND trade_date = ?
                      AND adjustment = ? AND source = ?
                    """,
                    key,
                ).fetchone()

                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO stock_daily_price (
                            security_id, trade_date, adjustment, source,
                            provider_adjustment, open, high, low, close, volume,
                            volume_unit, amount, amount_unit, is_final,
                            provider_as_of_date, observed_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        key + new_facts + (record["observed_at"], saved_at),
                    )
                    inserted += 1
                    continue

                old_facts = tuple(
                    _normalize_nullable_price_scalar(value)
                    if field in PRICE_NULLABLE_FIELDS
                    else value
                    for field, value in zip(
                        PRICE_FACT_FIELDS,
                        existing[: len(PRICE_FACT_FIELDS)],
                    )
                )
                old_observed_at = str(existing[len(PRICE_FACT_FIELDS)])
                if old_facts == new_facts:
                    if datetime.fromisoformat(record["observed_at"]) > datetime.fromisoformat(
                        old_observed_at
                    ):
                        connection.execute(
                            """
                            UPDATE stock_daily_price
                            SET observed_at = ?
                            WHERE security_id = ? AND trade_date = ?
                              AND adjustment = ? AND source = ?
                            """,
                            (record["observed_at"],) + key,
                        )
                    unchanged += 1
                    continue

                if datetime.fromisoformat(record["observed_at"]) <= datetime.fromisoformat(
                    old_observed_at
                ):
                    raise ValueError(
                        f"Price revision observed_at must advance for key: {key!r}"
                    )

                changed_fields = ",".join(
                    field
                    for field, old_value, new_value in zip(
                        PRICE_FACT_FIELDS, old_facts, new_facts
                    )
                    if old_value != new_value
                )
                revision_number = connection.execute(
                    """
                    SELECT COALESCE(MAX(revision_number), 0) + 1
                    FROM stock_daily_price_revision
                    WHERE security_id = ? AND trade_date = ?
                      AND adjustment = ? AND source = ?
                    """,
                    key,
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO stock_daily_price_revision (
                        security_id, trade_date, adjustment, source,
                        revision_number, changed_fields,
                        old_provider_adjustment, old_open, old_high, old_low,
                        old_close, old_volume, old_volume_unit, old_amount,
                        old_amount_unit, old_is_final, old_provider_as_of_date,
                        old_observed_at,
                        new_provider_adjustment, new_open, new_high, new_low,
                        new_close, new_volume, new_volume_unit, new_amount,
                        new_amount_unit, new_is_final, new_provider_as_of_date,
                        new_observed_at, revised_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    key
                    + (revision_number, changed_fields)
                    + old_facts
                    + (old_observed_at,)
                    + new_facts
                    + (record["observed_at"], saved_at),
                )
                connection.execute(
                    """
                    UPDATE stock_daily_price
                    SET provider_adjustment = ?, open = ?, high = ?, low = ?,
                        close = ?, volume = ?, volume_unit = ?, amount = ?,
                        amount_unit = ?, is_final = ?, provider_as_of_date = ?,
                        observed_at = ?, updated_at = ?
                    WHERE security_id = ? AND trade_date = ?
                      AND adjustment = ? AND source = ?
                    """,
                    new_facts
                    + (record["observed_at"], saved_at)
                    + key,
                )
                revised += 1
                revision_rows += 1
    except sqlite3.Error as exc:
        raise RuntimeError("Unable to save stock daily prices") from exc

    return StockDailyPriceSaveResult(
        inserted=inserted,
        revised=revised,
        unchanged=unchanged,
        revision_rows=revision_rows,
    )


def load_stock_daily_prices(
    *,
    database_path: str | PathLike[str],
    adjustment: str,
    source: str,
    security_ids: Iterable[int] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load explicit price facts with security identity and provenance."""
    conditions, params = _price_query_filters(
        adjustment=adjustment,
        source=source,
        security_ids=security_ids,
        start_date=start_date,
        end_date=end_date,
        security_column="price.security_id",
        date_column="price.trade_date",
    )
    query = f"""
    SELECT price.security_id, master.exchange, master.asset_type,
           master.local_symbol, master.board, price.trade_date,
           price.adjustment, price.source, price.provider_adjustment,
           price.open, price.high, price.low, price.close, price.volume,
           price.volume_unit, price.amount, price.amount_unit, price.is_final,
           price.provider_as_of_date, price.observed_at, price.updated_at
    FROM stock_daily_price AS price
    INNER JOIN security_master AS master
        ON master.security_id = price.security_id
    WHERE {' AND '.join(conditions)}
    ORDER BY price.security_id, price.trade_date, price.adjustment, price.source
    """
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(query, connection, params=params)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError("Unable to load stock daily prices") from exc
    result = result.loc[:, list(STOCK_DAILY_PRICE_RESULT_COLUMNS)]
    if not result.empty:
        result["volume"] = result["volume"].astype("int64")
        result["is_final"] = result["is_final"].astype(bool)
    return result


def load_stock_daily_price_revisions(
    *,
    database_path: str | PathLike[str],
    adjustment: str,
    source: str,
    security_ids: Iterable[int] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load ordered price revision history with old and new facts."""
    conditions, params = _price_query_filters(
        adjustment=adjustment,
        source=source,
        security_ids=security_ids,
        start_date=start_date,
        end_date=end_date,
        security_column="price.security_id",
        date_column="price.trade_date",
    )
    query = f"""
    SELECT revision.revision_id, revision.security_id, master.exchange,
           master.asset_type, master.local_symbol, master.board,
           revision.trade_date, revision.adjustment, revision.source,
           revision.revision_number, revision.changed_fields,
           revision.old_provider_adjustment, revision.old_open,
           revision.old_high, revision.old_low, revision.old_close,
           revision.old_volume, revision.old_volume_unit, revision.old_amount,
           revision.old_amount_unit, revision.old_is_final,
           revision.old_provider_as_of_date, revision.old_observed_at,
           revision.new_provider_adjustment, revision.new_open,
           revision.new_high, revision.new_low, revision.new_close,
           revision.new_volume, revision.new_volume_unit, revision.new_amount,
           revision.new_amount_unit, revision.new_is_final,
           revision.new_provider_as_of_date, revision.new_observed_at,
           revision.revised_at
    FROM stock_daily_price_revision AS revision
    INNER JOIN stock_daily_price AS price
        ON price.security_id = revision.security_id
       AND price.trade_date = revision.trade_date
       AND price.adjustment = revision.adjustment
       AND price.source = revision.source
    INNER JOIN security_master AS master
        ON master.security_id = revision.security_id
    WHERE {' AND '.join(conditions)}
    ORDER BY revision.security_id, revision.trade_date, revision.adjustment,
             revision.source, revision.revision_number
    """
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(query, connection, params=params)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError("Unable to load stock daily price revisions") from exc
    result = result.loc[:, list(STOCK_DAILY_PRICE_REVISION_RESULT_COLUMNS)]
    if not result.empty:
        result["old_volume"] = result["old_volume"].astype("int64")
        result["new_volume"] = result["new_volume"].astype("int64")
        result["old_is_final"] = result["old_is_final"].astype(bool)
        result["new_is_final"] = result["new_is_final"].astype(bool)
    return result


def load_latest_stock_daily_price_dates(
    *,
    database_path: str | PathLike[str],
    adjustment: str,
    source: str,
    security_ids: Iterable[int] | None = None,
) -> pd.DataFrame:
    """Return latest existing dates for an explicit source and adjustment."""
    adjustment_value = validate_price_adjustment(adjustment)
    source_value = validate_price_source(source)
    id_values = _price_security_id_values(security_ids)
    conditions = ["adjustment = ?", "source = ?"]
    params: list[object] = [adjustment_value, source_value]
    if id_values is not None:
        if not id_values:
            conditions.append("0")
        else:
            placeholders = ",".join("?" for _ in id_values)
            conditions.append(f"security_id IN ({placeholders})")
            params.extend(id_values)
    query = f"""
    SELECT security_id, MAX(trade_date) AS latest_trade_date
    FROM stock_daily_price
    WHERE {' AND '.join(conditions)}
    GROUP BY security_id
    ORDER BY security_id
    """
    path = _prepare_database_path(database_path)
    init_database(path)
    try:
        with sqlite3.connect(path) as connection:
            result = pd.read_sql_query(query, connection, params=params)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise RuntimeError("Unable to load latest stock daily price dates") from exc
    return result.loc[:, list(LATEST_STOCK_DAILY_PRICE_DATE_COLUMNS)]
