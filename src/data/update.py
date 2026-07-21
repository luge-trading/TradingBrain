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
    get_market_daily,
    get_latest_sector_trade_date,
    get_sector_definition,
    load_sector_daily_kline,
    load_sector_registry,
    save_sector_daily_kline,
    save_sector_registry_snapshot,
    save_market_daily,
)
from src.data.index import get_index_definition, normalize_index_daily_kline
from src.data.market import (
    SSE_AMOUNT_SOURCE,
    SZSE_AMOUNT_SOURCE,
    ExchangeDailyAmount,
    MarketBreadth,
    MarketDaily,
    compose_market_daily,
    validate_trade_date,
)
from src.data.providers.eastmoney import get_daily_kline, get_index_daily_kline
from src.data.providers.eastmoney_market import get_market_breadth
from src.data.providers.exchange import get_sse_daily_amount, get_szse_daily_amount
from src.data.providers.eastmoney_sector import (
    get_industry_sector_list,
    get_sector_daily_kline,
)
from src.data.sector import (
    EASTMONEY_INDUSTRY_LEVELS,
    SectorDefinition,
    normalize_sector_daily_kline,
    normalize_sector_registry,
    validate_sector_level,
)


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


@dataclass(frozen=True, slots=True)
class MarketUpdateResult:
    """Attempted and stored facts from one independent market update."""

    trade_date: str
    attempted_record: MarketDaily
    stored_record: MarketDaily
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SectorRegistryUpdateResult:
    fetched_rows: int
    stored_rows: int
    level_counts: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class SectorDailyUpdateResult:
    sector_type: str
    sector_level: int
    sector_code: str
    fetched_rows: int
    new_rows: int
    stored_rows: int
    latest_before: str | None
    latest_after: str | None


@dataclass(frozen=True, slots=True)
class SectorDailyUpdateFailure:
    sector_type: str
    sector_level: int
    sector_code: str
    sector_name: str
    error: str


@dataclass(frozen=True, slots=True)
class SectorDailyBatchUpdateResult:
    attempted: int
    succeeded: tuple[SectorDailyUpdateResult, ...]
    failed: tuple[SectorDailyUpdateFailure, ...]


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


def update_market_daily(
    trade_date: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    sse_fetcher: Callable[..., ExchangeDailyAmount] = get_sse_daily_amount,
    szse_fetcher: Callable[..., ExchangeDailyAmount] = get_szse_daily_amount,
    breadth_fetcher: Callable[..., MarketBreadth] = get_market_breadth,
) -> MarketUpdateResult:
    """Fetch and store one market day without changing other update flows.

    The two exchange amounts form one atomic group. Breadth forms another.
    A failed group is represented as missing in ``attempted_record`` and an
    already stored valid group is retained in ``stored_record``.
    """
    trade_date = validate_trade_date(trade_date)
    for name, fetcher in (
        ("sse_fetcher", sse_fetcher),
        ("szse_fetcher", szse_fetcher),
        ("breadth_fetcher", breadth_fetcher),
    ):
        if not callable(fetcher):
            raise TypeError(f"{name} must be callable")

    errors: list[str] = []
    sh_amount: ExchangeDailyAmount | None = None
    sz_amount: ExchangeDailyAmount | None = None
    breadth: MarketBreadth | None = None

    try:
        value = sse_fetcher(trade_date)
        if not isinstance(value, ExchangeDailyAmount):
            raise TypeError("fetcher must return ExchangeDailyAmount")
        if value.source != SSE_AMOUNT_SOURCE or value.trade_date != trade_date:
            raise ValueError("returned source/date does not match Shanghai request")
        sh_amount = value
    except Exception as exc:
        errors.append(f"Shanghai amount fetch failed for {trade_date}: {exc}")

    try:
        value = szse_fetcher(trade_date)
        if not isinstance(value, ExchangeDailyAmount):
            raise TypeError("fetcher must return ExchangeDailyAmount")
        if value.source != SZSE_AMOUNT_SOURCE or value.trade_date != trade_date:
            raise ValueError("returned source/date does not match Shenzhen request")
        sz_amount = value
    except Exception as exc:
        errors.append(f"Shenzhen amount fetch failed for {trade_date}: {exc}")

    amount_group_succeeded = sh_amount is not None and sz_amount is not None
    if not amount_group_succeeded:
        sh_amount = sz_amount = None

    try:
        value = breadth_fetcher()
        if not isinstance(value, MarketBreadth):
            raise TypeError("fetcher must return MarketBreadth")
        breadth = value
    except Exception as exc:
        errors.append(f"Market breadth fetch failed for {trade_date}: {exc}")

    attempted = compose_market_daily(
        trade_date,
        sh_amount=sh_amount,
        sz_amount=sz_amount,
        breadth=breadth,
    )
    existing = get_market_daily(trade_date, database_path=database_path)
    if existing is None:
        stored_candidate = attempted
    else:
        use_new_amount = amount_group_succeeded
        use_new_breadth = breadth is not None
        stored_candidate = MarketDaily(
            trade_date=trade_date,
            sh_amount_yuan=attempted.sh_amount_yuan if use_new_amount else existing.sh_amount_yuan,
            sz_amount_yuan=attempted.sz_amount_yuan if use_new_amount else existing.sz_amount_yuan,
            total_amount_yuan=attempted.total_amount_yuan if use_new_amount else existing.total_amount_yuan,
            advance_count=attempted.advance_count if use_new_breadth else existing.advance_count,
            decline_count=attempted.decline_count if use_new_breadth else existing.decline_count,
            flat_count=attempted.flat_count if use_new_breadth else existing.flat_count,
            sh_amount_source=attempted.sh_amount_source if use_new_amount else existing.sh_amount_source,
            sz_amount_source=attempted.sz_amount_source if use_new_amount else existing.sz_amount_source,
            breadth_source=attempted.breadth_source if use_new_breadth else existing.breadth_source,
        )
    try:
        save_market_daily(stored_candidate, database_path=database_path)
    except Exception as exc:
        raise RuntimeError(f"Market update save failed for {trade_date}: {exc}") from exc
    stored = get_market_daily(trade_date, database_path=database_path)
    if stored is None:
        raise RuntimeError(f"Market update verification failed for {trade_date}")
    return MarketUpdateResult(trade_date, attempted, stored, tuple(errors))


def update_sector_registry(
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    fetcher: Callable[[int], object] = get_industry_sector_list,
) -> SectorRegistryUpdateResult:
    """Fetch all three industry levels before atomically saving one snapshot."""
    if not callable(fetcher):
        raise TypeError("fetcher must be callable")
    combined: list[SectorDefinition] = []
    level_counts: list[tuple[int, int]] = []
    for level in EASTMONEY_INDUSTRY_LEVELS:
        try:
            fetched = tuple(fetcher(level))
            if not fetched:
                raise ValueError("empty industry level snapshot")
            if any(
                not isinstance(item, SectorDefinition) or item.sector_level != level
                for item in fetched
            ):
                raise ValueError("returned definitions do not match requested level")
        except Exception as exc:
            raise RuntimeError(
                f"Sector registry fetch failed for level {level}: {exc}"
            ) from exc
        combined.extend(fetched)
        level_counts.append((level, len(fetched)))
    try:
        normalized = normalize_sector_registry(combined)
    except Exception as exc:
        raise ValueError(f"Sector registry normalization failed: {exc}") from exc
    try:
        stored_rows = save_sector_registry_snapshot(
            normalized, database_path=database_path
        )
    except Exception as exc:
        raise RuntimeError(f"Sector registry save failed: {exc}") from exc
    return SectorRegistryUpdateResult(len(normalized), stored_rows, tuple(level_counts))


def _require_current_sector_definition(
    definition: SectorDefinition,
    database_path: str | PathLike[str],
) -> None:
    if not isinstance(definition, SectorDefinition):
        raise TypeError("definition must be a SectorDefinition")
    current = get_sector_definition(
        definition.sector_type,
        definition.sector_level,
        definition.sector_code,
        database_path=database_path,
        active_only=True,
    )
    if current is None:
        raise ValueError(f"Sector is not active in registry: {definition.sector_code}")
    if current != definition:
        raise ValueError(f"Sector definition does not match current registry: {definition.sector_code}")


def update_sector_daily(
    definition: SectorDefinition,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    limit: int = 500,
    fetcher: Callable[..., pd.DataFrame] = get_sector_daily_kline,
) -> SectorDailyUpdateResult:
    """Fetch and upsert all valid records for one active industry sector."""
    if not callable(fetcher):
        raise TypeError("fetcher must be callable")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError(f"Invalid K-line limit: {limit!r}")
    _require_current_sector_definition(definition, database_path)
    latest_before = get_latest_sector_trade_date(
        definition, database_path=database_path
    )
    try:
        fetched = fetcher(definition, limit=limit)
    except Exception as exc:
        raise RuntimeError(
            f"Sector daily fetch failed for {definition.sector_code}: {exc}"
        ) from exc
    if not isinstance(fetched, pd.DataFrame):
        raise TypeError(
            f"Sector daily fetch failed for {definition.sector_code}: "
            "fetcher must return a pandas DataFrame"
        )
    try:
        normalized = normalize_sector_daily_kline(fetched)
    except Exception as exc:
        raise ValueError(
            f"Sector daily normalization failed for {definition.sector_code}: {exc}"
        ) from exc
    if normalized.empty:
        return SectorDailyUpdateResult(
            definition.sector_type,
            definition.sector_level,
            definition.sector_code,
            0,
            0,
            0,
            latest_before,
            latest_before,
        )
    existing = load_sector_daily_kline(definition, database_path=database_path)
    existing_dates = set(existing["date"].astype(str)) if not existing.empty else set()
    new_rows = sum(date not in existing_dates for date in normalized["date"].astype(str))
    try:
        stored_rows = save_sector_daily_kline(
            definition, normalized, database_path=database_path
        )
    except Exception as exc:
        raise RuntimeError(
            f"Sector daily save failed for {definition.sector_code}: {exc}"
        ) from exc
    latest_after = get_latest_sector_trade_date(
        definition, database_path=database_path
    )
    return SectorDailyUpdateResult(
        definition.sector_type,
        definition.sector_level,
        definition.sector_code,
        len(normalized),
        new_rows,
        stored_rows,
        latest_before,
        latest_after,
    )


def update_sector_daily_batch(
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    sector_level: int | None = None,
    limit: int = 500,
    fetcher: Callable[..., pd.DataFrame] = get_sector_daily_kline,
) -> SectorDailyBatchUpdateResult:
    """Serially update active industries while isolating per-sector failures."""
    if sector_level is not None:
        sector_level = validate_sector_level(sector_level)
    if not callable(fetcher):
        raise TypeError("fetcher must be callable")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError(f"Invalid K-line limit: {limit!r}")
    registry = load_sector_registry(
        database_path=database_path,
        sector_level=sector_level,
        active_only=True,
    )
    definitions = tuple(
        SectorDefinition(
            row.sector_type,
            int(row.sector_level),
            row.sector_code,
            row.sector_name,
            row.source,
        )
        for row in registry.itertuples(index=False)
    )
    succeeded: list[SectorDailyUpdateResult] = []
    failed: list[SectorDailyUpdateFailure] = []
    for definition in definitions:
        try:
            succeeded.append(update_sector_daily(
                definition,
                database_path=database_path,
                limit=limit,
                fetcher=fetcher,
            ))
        except Exception as exc:
            failed.append(SectorDailyUpdateFailure(
                definition.sector_type,
                definition.sector_level,
                definition.sector_code,
                definition.sector_name,
                f"{type(exc).__name__}: {exc}",
            ))
    return SectorDailyBatchUpdateResult(len(definitions), tuple(succeeded), tuple(failed))
