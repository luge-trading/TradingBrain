"""SQLite storage for standardized stock market data."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Final

import pandas as pd


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
