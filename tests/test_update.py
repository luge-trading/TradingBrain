"""Tests for the stock daily-data update service."""

from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from src.data.database import get_market_daily, load_daily_kline, save_market_daily
from src.data.database import load_index_daily_kline, save_index_daily_kline
from src.data.database import (
    load_sector_daily_kline,
    load_sector_registry,
    save_sector_registry_snapshot,
)
from src.data.market import (
    SSE_AMOUNT_SOURCE,
    SZSE_AMOUNT_SOURCE,
    ExchangeDailyAmount,
    MarketBreadth,
    compose_market_daily,
)
from src.data.update import (
    IndexUpdateResult,
    MarketUpdateResult,
    UpdateResult,
    update_index_daily,
    update_market_daily,
    update_stock_daily,
    SectorDailyBatchUpdateResult,
    SectorDailyUpdateResult,
    SectorRegistryUpdateResult,
    update_sector_daily,
    update_sector_daily_batch,
    update_sector_registry,
)
from src.data.sector import (
    EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    EASTMONEY_INDUSTRY_SECTOR_TYPE,
    SECTOR_KLINE_COLUMNS,
    SectorDefinition,
)


def make_kline_data(dates: list[str]) -> pd.DataFrame:
    """Create standardized K-line data."""
    records = []

    for index, trade_date in enumerate(dates):
        price = 10.0 + index

        records.append(
            {
                "date": trade_date,
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + 0.5,
                "volume": 1000 + index,
                "amount": 10000.0 + index,
            }
        )

    return pd.DataFrame(records)


def test_update_stock_daily_initial_import(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    fetcher = Mock(
        return_value=make_kline_data(
            ["2026-07-16", "2026-07-17"]
        )
    )

    result = update_stock_daily(
        "000021",
        database_path=database_path,
        limit=500,
        fetcher=fetcher,
    )

    assert isinstance(result, UpdateResult)
    assert result.symbol == "000021"
    assert result.fetched_rows == 2
    assert result.new_rows == 2
    assert result.stored_rows == 2
    assert result.latest_before is None
    assert result.latest_after == "2026-07-17"

    fetcher.assert_called_once_with(
        "000021",
        limit=500,
    )


def test_update_stock_daily_only_saves_new_dates(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(
            return_value=make_kline_data(
                ["2026-07-16", "2026-07-17"]
            )
        ),
    )

    result = update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(
            return_value=make_kline_data(
                [
                    "2026-07-16",
                    "2026-07-17",
                    "2026-07-18",
                ]
            )
        ),
    )

    stored = load_daily_kline(
        "000021",
        database_path=database_path,
    )

    assert result.fetched_rows == 3
    assert result.new_rows == 1
    assert result.stored_rows == 1
    assert result.latest_before == "2026-07-17"
    assert result.latest_after == "2026-07-18"
    assert stored["date"].tolist() == [
        "2026-07-16",
        "2026-07-17",
        "2026-07-18",
    ]


def test_update_stock_daily_writes_nothing_when_current(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    data = make_kline_data(
        ["2026-07-16", "2026-07-17"]
    )

    update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(return_value=data),
    )

    result = update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(return_value=data),
    )

    assert result.fetched_rows == 2
    assert result.new_rows == 0
    assert result.stored_rows == 0
    assert result.latest_before == "2026-07-17"
    assert result.latest_after == "2026-07-17"


def test_update_stock_daily_removes_duplicate_dates(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    duplicated_data = make_kline_data(
        [
            "2026-07-16",
            "2026-07-16",
            "2026-07-17",
        ]
    )

    result = update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(return_value=duplicated_data),
    )

    stored = load_daily_kline(
        "000021",
        database_path=database_path,
    )

    assert result.fetched_rows == 2
    assert result.new_rows == 2
    assert result.stored_rows == 2
    assert len(stored) == 2


def test_update_stock_daily_handles_empty_data(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    empty_data = pd.DataFrame(
        columns=[
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ]
    )

    result = update_stock_daily(
        "000021",
        database_path=database_path,
        fetcher=Mock(return_value=empty_data),
    )

    assert result.fetched_rows == 0
    assert result.new_rows == 0
    assert result.stored_rows == 0
    assert result.latest_before is None
    assert result.latest_after is None


def test_update_stock_daily_rejects_non_dataframe(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    with pytest.raises(
        TypeError,
        match="fetcher must return a pandas DataFrame",
    ):
        update_stock_daily(
            "000021",
            database_path=database_path,
            fetcher=Mock(return_value=[]),
        )


def test_update_stock_daily_rejects_missing_date_column(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    invalid_data = pd.DataFrame(
        [{"open": 10.0, "close": 11.0}]
    )

    with pytest.raises(
        ValueError,
        match="missing date column",
    ):
        update_stock_daily(
            "000021",
            database_path=database_path,
            fetcher=Mock(return_value=invalid_data),
        )


def test_update_stock_daily_does_not_overwrite_existing_data_on_failure(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    from src.data.database import save_daily_kline, load_daily_kline

    initial = make_kline_data(["2026-07-16", "2026-07-17"])
    save_daily_kline("000021", initial, database_path=database_path)

    def failing_fetcher(symbol, *, limit):
        raise RuntimeError("fetch failed")

    with pytest.raises(RuntimeError, match="fetch failed"):
        update_stock_daily(
            "000021",
            database_path=database_path,
            fetcher=failing_fetcher,
        )

    stored = load_daily_kline("000021", database_path=database_path)
    assert stored["date"].tolist() == ["2026-07-16", "2026-07-17"]


def index_data(close=11.0, dates=("2026-07-16", "2026-07-17")):
    return pd.DataFrame([
        {"date": date, "open": close - 1, "high": close + 1, "low": close - 2, "close": close, "volume": 100, "amount": 1000}
        for date in dates
    ])


def test_update_index_upserts_all_rows_and_counts_only_new_dates(tmp_path: Path):
    database_path = tmp_path / "test.db"
    first = update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=index_data()))
    assert isinstance(first, IndexUpdateResult)
    assert (first.fetched_rows, first.new_rows, first.stored_rows) == (2, 2, 2)
    revised = index_data(close=12.0, dates=("2026-07-16", "2026-07-17", "2026-07-18"))
    second = update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=revised))
    assert (second.fetched_rows, second.new_rows, second.stored_rows) == (3, 1, 3)
    loaded = load_index_daily_kline("SH000001", database_path=database_path)
    assert len(loaded) == 3
    assert loaded.iloc[0]["close"] == 12.0


def test_update_index_empty_and_failure_isolated(tmp_path: Path):
    database_path = tmp_path / "test.db"
    empty = update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])))
    assert (empty.fetched_rows, empty.new_rows, empty.stored_rows) == (0, 0, 0)
    save_index_daily_kline("SZ399001", index_data(), database_path=database_path)
    with pytest.raises(RuntimeError, match="SH000001"):
        update_index_daily("SH000001", database_path=database_path, fetcher=Mock(side_effect=RuntimeError("network")))
    assert len(load_index_daily_kline("SZ399001", database_path=database_path)) == 2


def test_update_index_rejects_non_dataframe_and_preserves_data(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_index_daily_kline("SH000001", index_data(), database_path=database_path)
    with pytest.raises(TypeError, match=r"DataFrame.*SH000001"):
        update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=[]))
    assert len(load_index_daily_kline("SH000001", database_path=database_path)) == 2


def test_update_index_rejects_duplicate_dates_and_preserves_data(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_index_daily_kline("SH000001", index_data(), database_path=database_path)
    duplicated = index_data(dates=("2026-07-16", "2026-07-16"))
    with pytest.raises(ValueError, match=r"normalization.*SH000001"):
        update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=duplicated))
    assert len(load_index_daily_kline("SH000001", database_path=database_path)) == 2


def test_update_index_save_failure_preserves_data(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "test.db"
    save_index_daily_kline("SH000001", index_data(), database_path=database_path)
    monkeypatch.setattr("src.data.update.save_index_daily_kline", Mock(side_effect=RuntimeError("disk")))
    with pytest.raises(RuntimeError, match=r"save.*SH000001"):
        update_index_daily("SH000001", database_path=database_path, fetcher=Mock(return_value=index_data(close=13.0)))
    assert len(load_index_daily_kline("SH000001", database_path=database_path)) == 2


def market_fetchers(trade_date="2026-07-17", sh=100, sz=200, breadth=(3000, 1800, 200)):
    return (
        Mock(return_value=ExchangeDailyAmount(trade_date, sh, SSE_AMOUNT_SOURCE)),
        Mock(return_value=ExchangeDailyAmount(trade_date, sz, SZSE_AMOUNT_SOURCE)),
        Mock(return_value=MarketBreadth(*breadth)),
    )


def test_update_market_daily_stores_complete_amount_and_breadth_groups(tmp_path: Path):
    database_path = tmp_path / "test.db"
    sse, szse, breadth = market_fetchers()
    result = update_market_daily(
        "2026-07-17",
        database_path=database_path,
        sse_fetcher=sse,
        szse_fetcher=szse,
        breadth_fetcher=breadth,
    )
    assert isinstance(result, MarketUpdateResult)
    assert result.errors == ()
    assert result.attempted_record == result.stored_record
    assert result.stored_record.total_amount_yuan == 300
    assert result.stored_record.advance_count == 3000
    sse.assert_called_once_with("2026-07-17")
    szse.assert_called_once_with("2026-07-17")
    breadth.assert_called_once_with()


def test_update_market_daily_initial_partial_failures_store_null_groups(tmp_path: Path):
    database_path = tmp_path / "test.db"
    sse, _, _ = market_fetchers()
    result = update_market_daily(
        "2026-07-17",
        database_path=database_path,
        sse_fetcher=sse,
        szse_fetcher=Mock(side_effect=RuntimeError("SZSE unavailable")),
        breadth_fetcher=Mock(side_effect=RuntimeError("breadth unavailable")),
    )
    assert result.attempted_record.sh_amount_yuan is None
    assert result.attempted_record.sz_amount_yuan is None
    assert result.attempted_record.total_amount_yuan is None
    assert result.attempted_record.advance_count is None
    assert result.stored_record == result.attempted_record
    assert len(result.errors) == 2
    assert "Shenzhen amount fetch" in result.errors[0]
    assert "Market breadth fetch" in result.errors[1]


def test_update_market_daily_failed_amount_group_preserves_both_old_amounts(tmp_path: Path):
    database_path = tmp_path / "test.db"
    initial = compose_market_daily(
        "2026-07-17",
        sh_amount=ExchangeDailyAmount("2026-07-17", 100, SSE_AMOUNT_SOURCE),
        sz_amount=ExchangeDailyAmount("2026-07-17", 200, SZSE_AMOUNT_SOURCE),
        breadth=MarketBreadth(3000, 1800, 200),
    )
    save_market_daily(initial, database_path=database_path)
    result = update_market_daily(
        "2026-07-17",
        database_path=database_path,
        sse_fetcher=Mock(return_value=ExchangeDailyAmount("2026-07-17", 999, SSE_AMOUNT_SOURCE)),
        szse_fetcher=Mock(side_effect=RuntimeError("failed")),
        breadth_fetcher=Mock(return_value=MarketBreadth(3100, 1700, 200)),
    )
    assert result.attempted_record.total_amount_yuan is None
    assert result.stored_record.sh_amount_yuan == 100
    assert result.stored_record.sz_amount_yuan == 200
    assert result.stored_record.total_amount_yuan == 300
    assert result.stored_record.advance_count == 3100


def test_update_market_daily_failed_breadth_preserves_old_breadth(tmp_path: Path):
    database_path = tmp_path / "test.db"
    sse, szse, breadth = market_fetchers()
    first = update_market_daily(
        "2026-07-17", database_path=database_path,
        sse_fetcher=sse, szse_fetcher=szse, breadth_fetcher=breadth,
    )
    new_sse, new_szse, _ = market_fetchers(sh=400, sz=500)
    second = update_market_daily(
        "2026-07-17", database_path=database_path,
        sse_fetcher=new_sse, szse_fetcher=new_szse,
        breadth_fetcher=Mock(side_effect=RuntimeError("failed")),
    )
    assert second.attempted_record.advance_count is None
    assert second.stored_record.total_amount_yuan == 900
    assert second.stored_record.advance_count == first.stored_record.advance_count


@pytest.mark.parametrize("bad_fetcher", [Mock(return_value=[]), Mock(return_value=None)])
def test_update_market_daily_rejects_invalid_fetcher_values_as_group_failure(tmp_path: Path, bad_fetcher):
    sse, _, breadth = market_fetchers()
    result = update_market_daily(
        "2026-07-17", database_path=tmp_path / "test.db",
        sse_fetcher=sse, szse_fetcher=bad_fetcher, breadth_fetcher=breadth,
    )
    assert result.stored_record.total_amount_yuan is None
    assert result.stored_record.advance_count == 3000
    assert "ExchangeDailyAmount" in result.errors[0]


def test_update_market_daily_save_failure_preserves_existing_data(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "test.db"
    original = compose_market_daily(
        "2026-07-17",
        sh_amount=ExchangeDailyAmount("2026-07-17", 100, SSE_AMOUNT_SOURCE),
        sz_amount=ExchangeDailyAmount("2026-07-17", 200, SZSE_AMOUNT_SOURCE),
        breadth=MarketBreadth(3000, 1800, 200),
    )
    save_market_daily(original, database_path=database_path)
    monkeypatch.setattr("src.data.update.save_market_daily", Mock(side_effect=RuntimeError("disk")))
    sse, szse, breadth = market_fetchers(sh=999, sz=999)
    with pytest.raises(RuntimeError, match=r"save failed.*2026-07-17"):
        update_market_daily(
            "2026-07-17", database_path=database_path,
            sse_fetcher=sse, szse_fetcher=szse, breadth_fetcher=breadth,
        )
    assert get_market_daily("2026-07-17", database_path=database_path) == original


def sector_definition(level=1, code="BK0001", name="Industry"):
    return SectorDefinition(
        EASTMONEY_INDUSTRY_SECTOR_TYPE,
        level,
        code,
        name,
        EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    )


def sector_data(close=11.0, dates=("2026-07-16", "2026-07-17")):
    return pd.DataFrame([
        {"date": date, "open": close - 1, "high": close + 1, "low": close - 2, "close": close, "volume": 100, "amount": 1000, "change_pct": 1.0}
        for date in dates
    ])


def test_update_sector_registry_fetches_levels_in_order_and_atomically_saves(tmp_path: Path):
    database_path = tmp_path / "test.db"
    by_level = {
        1: (sector_definition(1, "BK0001"),),
        2: (sector_definition(2, "BK0002"), sector_definition(2, "BK0003")),
        3: (sector_definition(3, "BK0004"),),
    }
    fetcher = Mock(side_effect=lambda level: by_level[level])
    result = update_sector_registry(database_path=database_path, fetcher=fetcher)
    assert isinstance(result, SectorRegistryUpdateResult)
    assert (result.fetched_rows, result.stored_rows) == (4, 4)
    assert result.level_counts == ((1, 1), (2, 2), (3, 1))
    assert [item.args for item in fetcher.call_args_list] == [(1,), (2,), (3,)]
    assert load_sector_registry(database_path=database_path)["sector_code"].tolist() == ["BK0001", "BK0002", "BK0003", "BK0004"]


def test_update_sector_registry_fetch_or_merged_duplicate_failure_preserves_database(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_sector_registry_snapshot([sector_definition(1, "BK0009")], database_path=database_path)
    failing = Mock(side_effect=[(sector_definition(1, "BK0001"),), RuntimeError("network")])
    with pytest.raises(RuntimeError, match=r"level 2.*network"):
        update_sector_registry(database_path=database_path, fetcher=failing)
    assert load_sector_registry(database_path=database_path)["sector_code"].tolist() == ["BK0009"]
    duplicate = Mock(side_effect=[
        (sector_definition(1, "BK0001"),),
        (sector_definition(2, "BK0001"),),
        (sector_definition(3, "BK0003"),),
    ])
    with pytest.raises(ValueError, match="normalization"):
        update_sector_registry(database_path=database_path, fetcher=duplicate)
    assert load_sector_registry(database_path=database_path)["sector_code"].tolist() == ["BK0009"]


def test_update_sector_registry_save_failure_has_phase(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.data.update.save_sector_registry_snapshot", Mock(side_effect=RuntimeError("disk")))
    fetcher = Mock(side_effect=lambda level: (sector_definition(level, f"BK000{level}"),))
    with pytest.raises(RuntimeError, match=r"registry save.*disk"):
        update_sector_registry(database_path=tmp_path / "test.db", fetcher=fetcher)


def test_update_sector_daily_initial_import_and_historical_revision(tmp_path: Path):
    database_path = tmp_path / "test.db"
    current = sector_definition()
    save_sector_registry_snapshot([current], database_path=database_path)
    fetcher = Mock(return_value=sector_data())
    first = update_sector_daily(current, database_path=database_path, fetcher=fetcher)
    assert isinstance(first, SectorDailyUpdateResult)
    assert (first.fetched_rows, first.new_rows, first.stored_rows) == (2, 2, 2)
    assert (first.latest_before, first.latest_after) == (None, "2026-07-17")
    fetcher.assert_called_once_with(current, limit=500)
    revised = sector_data(close=12, dates=("2026-07-16", "2026-07-17", "2026-07-18"))
    second = update_sector_daily(current, database_path=database_path, fetcher=Mock(return_value=revised))
    assert (second.fetched_rows, second.new_rows, second.stored_rows) == (3, 1, 3)
    loaded = load_sector_daily_kline(current, database_path=database_path)
    assert loaded.iloc[0]["close"] == 12
    assert loaded["date"].tolist() == ["2026-07-16", "2026-07-17", "2026-07-18"]


def test_update_sector_daily_empty_non_dataframe_and_normalization_failures_preserve_data(tmp_path: Path):
    database_path = tmp_path / "test.db"
    current = sector_definition()
    save_sector_registry_snapshot([current], database_path=database_path)
    update_sector_daily(current, database_path=database_path, fetcher=Mock(return_value=sector_data()))
    empty = update_sector_daily(current, database_path=database_path, fetcher=Mock(return_value=pd.DataFrame(columns=SECTOR_KLINE_COLUMNS)))
    assert (empty.fetched_rows, empty.new_rows, empty.stored_rows) == (0, 0, 0)
    with pytest.raises(TypeError, match=r"fetch.*BK0001.*DataFrame"):
        update_sector_daily(current, database_path=database_path, fetcher=Mock(return_value=[]))
    duplicate = sector_data(dates=("2026-07-16", "2026-07-16"))
    with pytest.raises(ValueError, match=r"normalization.*BK0001"):
        update_sector_daily(current, database_path=database_path, fetcher=Mock(return_value=duplicate))
    assert len(load_sector_daily_kline(current, database_path=database_path)) == 2


def test_update_sector_daily_fetch_and_save_failures_preserve_existing(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "test.db"
    current = sector_definition()
    save_sector_registry_snapshot([current], database_path=database_path)
    update_sector_daily(current, database_path=database_path, fetcher=Mock(return_value=sector_data()))
    with pytest.raises(RuntimeError, match=r"fetch.*BK0001.*network"):
        update_sector_daily(current, database_path=database_path, fetcher=Mock(side_effect=RuntimeError("network")))
    monkeypatch.setattr("src.data.update.save_sector_daily_kline", Mock(side_effect=RuntimeError("disk")))
    with pytest.raises(RuntimeError, match=r"save.*BK0001.*disk"):
        update_sector_daily(current, database_path=database_path, fetcher=Mock(return_value=sector_data(close=15)))
    assert len(load_sector_daily_kline(current, database_path=database_path)) == 2


def test_update_sector_daily_rejects_inactive_or_mismatched_before_fetch(tmp_path: Path):
    database_path = tmp_path / "test.db"
    current = sector_definition()
    save_sector_registry_snapshot([current], database_path=database_path)
    fetcher = Mock(return_value=sector_data())
    with pytest.raises(ValueError, match="match current registry"):
        update_sector_daily(sector_definition(name="Old"), database_path=database_path, fetcher=fetcher)
    save_sector_registry_snapshot([sector_definition(2, "BK0002")], database_path=database_path)
    with pytest.raises(ValueError, match="not active"):
        update_sector_daily(current, database_path=database_path, fetcher=fetcher)
    fetcher.assert_not_called()


def test_update_sector_daily_batch_continues_after_failure_and_filters_level(tmp_path: Path):
    database_path = tmp_path / "test.db"
    definitions = [sector_definition(1, "BK0001", "One"), sector_definition(2, "BK0002", "Two"), sector_definition(2, "BK0003", "Three")]
    save_sector_registry_snapshot(definitions, database_path=database_path)

    def fetcher(definition, *, limit):
        if definition.sector_code == "BK0002":
            raise RuntimeError("provider down")
        return sector_data()

    result = update_sector_daily_batch(database_path=database_path, sector_level=2, fetcher=fetcher)
    assert isinstance(result, SectorDailyBatchUpdateResult)
    assert result.attempted == 2
    assert [item.sector_code for item in result.succeeded] == ["BK0003"]
    assert [item.sector_code for item in result.failed] == ["BK0002"]
    assert result.failed[0].sector_name == "Two"
    assert "RuntimeError" in result.failed[0].error and "provider down" in result.failed[0].error
    assert load_sector_daily_kline(definitions[2], database_path=database_path).shape[0] == 2
    assert load_sector_daily_kline(definitions[0], database_path=database_path).empty


def test_update_sector_daily_batch_empty_active_registry(tmp_path: Path):
    result = update_sector_daily_batch(database_path=tmp_path / "test.db", fetcher=Mock())
    assert result == SectorDailyBatchUpdateResult(0, (), ())


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_update_sector_daily_batch_rejects_invalid_limit_before_registry_read(tmp_path: Path, limit):
    with pytest.raises(ValueError, match="limit"):
        update_sector_daily_batch(database_path=tmp_path / "test.db", limit=limit, fetcher=Mock())
    assert not (tmp_path / "test.db").exists()
