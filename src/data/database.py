"""SQLite storage for standardized stock market data."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Final

import pandas as pd

from src.data.index import INDEX_KLINE_COLUMNS, get_index_definition, normalize_index_daily_kline
from src.data.market import MarketDaily, calculate_advance_ratio, validate_trade_date


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
