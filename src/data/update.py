"""Stock daily market data update service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from os import PathLike

import pandas as pd

from src.data.database import (
    DEFAULT_DATABASE_PATH,
    get_latest_trade_date,
    load_daily_kline,
    save_daily_kline,
    get_latest_index_trade_date,
    load_index_daily_kline,
    save_index_daily_kline,
)
from src.data.index import get_index_definition, normalize_index_daily_kline
from src.data.providers.eastmoney import get_daily_kline, get_index_daily_kline


KlineFetcher = Callable[..., pd.DataFrame]


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """Result of one stock daily-data update."""

    symbol: str
    fetched_rows: int
    new_rows: int
    stored_rows: int
    latest_before: str | None
    latest_after: str | None


@dataclass(frozen=True, slots=True)
class IndexUpdateResult:
    index_code: str
    fetched_rows: int
    new_rows: int
    stored_rows: int
    latest_before: str | None
    latest_after: str | None


def update_stock_daily(
    symbol: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    limit: int = 500,
    fetcher: KlineFetcher = get_daily_kline,
) -> UpdateResult:
    """Fetch daily K-lines and save dates not already stored."""
    if not callable(fetcher):
        raise TypeError("fetcher must be callable")

    latest_before = get_latest_trade_date(
        symbol,
        database_path=database_path,
    )

    fetched_data = fetcher(symbol, limit=limit)

    if not isinstance(fetched_data, pd.DataFrame):
        raise TypeError("fetcher must return a pandas DataFrame")

    if "date" not in fetched_data.columns:
        raise ValueError("Fetched K-line data is missing date column")

    if fetched_data.empty:
        return UpdateResult(
            symbol=symbol,
            fetched_rows=0,
            new_rows=0,
            stored_rows=0,
            latest_before=latest_before,
            latest_after=latest_before,
        )

    normalized_data = fetched_data.copy()
    normalized_data["date"] = normalized_data["date"].astype(str)

    normalized_data = (
        normalized_data
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )

    existing_data = load_daily_kline(
        symbol,
        database_path=database_path,
    )

    if existing_data.empty:
        existing_dates: set[str] = set()
    else:
        existing_dates = set(
            existing_data["date"].astype(str)
        )

    new_data = normalized_data.loc[
        ~normalized_data["date"].isin(existing_dates)
    ].copy()

    if new_data.empty:
        stored_rows = 0
    else:
        stored_rows = save_daily_kline(
            symbol,
            new_data,
            database_path=database_path,
        )

    latest_after = get_latest_trade_date(
        symbol,
        database_path=database_path,
    )

    return UpdateResult(
        symbol=symbol,
        fetched_rows=len(normalized_data),
        new_rows=len(new_data),
        stored_rows=stored_rows,
        latest_before=latest_before,
        latest_after=latest_after,
    )


def update_index_daily(
    index_code: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    limit: int = 500,
    fetcher: KlineFetcher = get_index_daily_kline,
) -> IndexUpdateResult:
    get_index_definition(index_code)
    if not callable(fetcher):
        raise TypeError("fetcher must be callable")
    latest_before = get_latest_index_trade_date(index_code, database_path=database_path)
    try:
        fetched = fetcher(index_code, limit=limit)
    except Exception as exc:
        raise RuntimeError(f"Index update fetch failed for {index_code}: {exc}") from exc
    if not isinstance(fetched, pd.DataFrame):
        raise TypeError(f"Index update fetcher must return a DataFrame for {index_code}")
    try:
        normalized = normalize_index_daily_kline(fetched)
    except Exception as exc:
        raise ValueError(f"Index update normalization failed for {index_code}: {exc}") from exc
    if normalized.empty:
        return IndexUpdateResult(index_code, 0, 0, 0, latest_before, latest_before)
    existing = load_index_daily_kline(index_code, database_path=database_path)
    existing_dates = set(existing["date"].astype(str)) if not existing.empty else set()
    new_rows = sum(date not in existing_dates for date in normalized["date"].astype(str))
    try:
        stored_rows = save_index_daily_kline(index_code, normalized, database_path=database_path)
    except Exception as exc:
        raise RuntimeError(f"Index update save failed for {index_code}: {exc}") from exc
    latest_after = get_latest_index_trade_date(index_code, database_path=database_path)
    return IndexUpdateResult(index_code, len(normalized), new_rows, stored_rows, latest_before, latest_after)
