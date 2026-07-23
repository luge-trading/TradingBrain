"""Tests for the SQLite stock data storage layer."""

import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from src.data.database import (
    SECURITY_LISTING_EVENT_RESULT_COLUMNS,
    SECURITY_MASTER_RESULT_COLUMNS,
    SECTOR_DAILY_PANEL_COLUMNS,
    get_latest_sector_trade_date,
    get_latest_market_trade_date,
    get_market_daily,
    get_latest_trade_date,
    init_database,
    load_daily_kline,
    save_daily_kline,
    get_latest_index_trade_date,
    load_index_daily_kline,
    save_index_daily_kline,
    load_market_daily,
    save_market_daily,
    get_sector_definition,
    load_sector_daily_kline,
    load_sector_registry,
    save_sector_daily_kline,
    save_sector_registry_snapshot,
    load_sector_daily_panel,
    load_security_listing_events,
    load_security_master,
    save_security_listing_events,
    save_security_master,
    load_stock_daily_price_revisions,
    load_stock_daily_prices,
    save_stock_daily_prices,
)
from src.data.market import (
    SSE_AMOUNT_SOURCE,
    SZSE_AMOUNT_SOURCE,
    ExchangeDailyAmount,
    MarketBreadth,
    compose_market_daily,
)
from src.data.sector import (
    EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    EASTMONEY_INDUSTRY_SECTOR_TYPE,
    SectorDefinition,
)


def make_kline_data() -> pd.DataFrame:
    """Create standardized test K-line data."""
    return pd.DataFrame(
        [
            {
                "date": "2026-07-16",
                "open": 18.00,
                "high": 18.50,
                "low": 17.90,
                "close": 18.25,
                "volume": 123456,
                "amount": 2250000.0,
            },
            {
                "date": "2026-07-17",
                "open": 18.30,
                "high": 18.80,
                "low": 18.10,
                "close": 18.60,
                "volume": 150000,
                "amount": 2800000.0,
            },
        ]
    )


def test_init_database_creates_table(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    init_database(database_path)

    with sqlite3.connect(database_path) as connection:
        result = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'stock_daily';
            """
        ).fetchone()

    assert result == ("stock_daily",)


def test_save_and_load_daily_kline(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    saved_rows = save_daily_kline(
        "000021",
        make_kline_data(),
        database_path=database_path,
    )

    loaded = load_daily_kline(
        "000021",
        database_path=database_path,
    )

    assert saved_rows == 2
    assert loaded.columns.tolist() == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ]
    assert loaded.shape == (2, 7)
    assert loaded.iloc[0]["date"] == "2026-07-16"
    assert loaded.iloc[1]["close"] == 18.60
    assert loaded.iloc[1]["volume"] == 150000


def test_save_daily_kline_updates_existing_record(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    original = make_kline_data().iloc[[0]].copy()

    save_daily_kline(
        "000021",
        original,
        database_path=database_path,
    )

    updated = original.copy()
    updated.loc[:, "close"] = 19.00
    updated.loc[:, "volume"] = 200000

    save_daily_kline(
        "000021",
        updated,
        database_path=database_path,
    )

    loaded = load_daily_kline(
        "000021",
        database_path=database_path,
    )

    assert loaded.shape == (1, 7)
    assert loaded.iloc[0]["close"] == 19.00
    assert loaded.iloc[0]["volume"] == 200000


def test_load_daily_kline_orders_by_date(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    reversed_data = make_kline_data().iloc[::-1]

    save_daily_kline(
        "000021",
        reversed_data,
        database_path=database_path,
    )

    loaded = load_daily_kline(
        "000021",
        database_path=database_path,
    )

    assert loaded["date"].tolist() == [
        "2026-07-16",
        "2026-07-17",
    ]


def test_get_latest_trade_date(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    save_daily_kline(
        "000021",
        make_kline_data(),
        database_path=database_path,
    )

    result = get_latest_trade_date(
        "000021",
        database_path=database_path,
    )

    assert result == "2026-07-17"


def test_get_latest_trade_date_returns_none(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"

    result = get_latest_trade_date(
        "000021",
        database_path=database_path,
    )

    assert result is None


def test_save_daily_kline_returns_zero_for_empty_data(
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

    result = save_daily_kline(
        "000021",
        empty_data,
        database_path=database_path,
    )

    assert result == 0


def test_save_daily_kline_rejects_missing_columns(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    invalid_data = pd.DataFrame(
        [{"date": "2026-07-17", "close": 18.60}]
    )

    with pytest.raises(
        ValueError,
        match="Missing required K-line columns",
    ):
        save_daily_kline(
            "000021",
            invalid_data,
            database_path=database_path,
        )


@pytest.mark.parametrize(
    "symbol",
    ["21", "00002A", "", 123456, None],
)
def test_database_rejects_invalid_symbol(
    tmp_path: Path,
    symbol: object,
) -> None:
    database_path = tmp_path / "test.db"

    with pytest.raises(ValueError, match="Invalid stock code"):
        load_daily_kline(
            symbol,  # type: ignore[arg-type]
            database_path=database_path,
        )


def make_index_data(amount=None):
    return pd.DataFrame([
        {"date": "2026-07-17", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "amount": amount},
        {"date": "2026-07-16", "open": 9, "high": 10, "low": 8, "close": 9.5, "volume": 90, "amount": 900},
    ])


def test_index_database_schema_and_stock_isolation(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_daily_kline("000021", make_kline_data(), database_path=database_path)
    assert save_index_daily_kline("SH000001", make_index_data(), database_path=database_path) == 2
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1]: row[3] for row in connection.execute("PRAGMA table_info(index_daily)")}
    assert {"stock_daily", "index_daily"} <= tables
    assert columns["amount"] == 0


def test_index_database_upsert_null_amount_and_order(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_index_daily_kline("SH000001", make_index_data(amount="--"), database_path=database_path)
    updated = make_index_data(amount=1234).iloc[[0]].copy()
    save_index_daily_kline("SH000001", updated, database_path=database_path)
    loaded = load_index_daily_kline("SH000001", database_path=database_path)
    assert loaded["date"].tolist() == ["2026-07-16", "2026-07-17"]
    assert loaded.iloc[1]["amount"] == 1234
    assert get_latest_index_trade_date("SH000001", database_path=database_path) == "2026-07-17"


def test_index_save_rolls_back_entire_batch_on_trigger_failure(tmp_path: Path):
    database_path = tmp_path / "test.db"
    init_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("""CREATE TRIGGER reject_second BEFORE INSERT ON index_daily
            WHEN NEW.trade_date = '2026-07-17' BEGIN SELECT RAISE(ABORT, 'blocked'); END;""")
    with pytest.raises(RuntimeError, match="Unable to save index K-line data"):
        save_index_daily_kline("SH000001", make_index_data(), database_path=database_path)
    assert load_index_daily_kline("SH000001", database_path=database_path).empty


@pytest.mark.parametrize("bad", [pd.DataFrame([{"date": "2026-07-17"}]), pd.DataFrame([{"date": "2026-07-17", "open": 1, "high": 2, "low": 0, "close": 1, "volume": -1, "amount": 1}])])
def test_save_index_defensive_validation(tmp_path: Path, bad: pd.DataFrame):
    with pytest.raises((ValueError, TypeError)):
        save_index_daily_kline("SH000001", bad, database_path=tmp_path / "test.db")
    with pytest.raises(ValueError, match="Unsupported index code"):
        save_index_daily_kline("SH999999", make_index_data(), database_path=tmp_path / "other.db")


def make_market_record(trade_date="2026-07-17", sh=100, sz=200, breadth=(3000, 1800, 200)):
    return compose_market_daily(
        trade_date,
        sh_amount=ExchangeDailyAmount(trade_date, sh, SSE_AMOUNT_SOURCE),
        sz_amount=ExchangeDailyAmount(trade_date, sz, SZSE_AMOUNT_SOURCE),
        breadth=MarketBreadth(*breadth),
    )


def test_market_database_schema_is_additive_and_exact(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_daily_kline("000021", make_kline_data(), database_path=database_path)
    save_index_daily_kline("SH000001", make_index_data(), database_path=database_path)
    init_database(database_path)
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = [(row[1], row[2], row[3], row[5]) for row in connection.execute("PRAGMA table_info(market_daily)")]
    assert {"stock_daily", "index_daily", "market_daily"} <= tables
    assert columns == [
        ("trade_date", "TEXT", 0, 1),
        ("sh_amount_yuan", "INTEGER", 0, 0),
        ("sz_amount_yuan", "INTEGER", 0, 0),
        ("total_amount_yuan", "INTEGER", 0, 0),
        ("advance_count", "INTEGER", 0, 0),
        ("decline_count", "INTEGER", 0, 0),
        ("flat_count", "INTEGER", 0, 0),
        ("sh_amount_source", "TEXT", 0, 0),
        ("sz_amount_source", "TEXT", 0, 0),
        ("breadth_source", "TEXT", 0, 0),
        ("updated_at", "TEXT", 1, 0),
    ]
    assert len(load_daily_kline("000021", database_path=database_path)) == 2
    assert len(load_index_daily_kline("SH000001", database_path=database_path)) == 2


def test_market_database_insert_upsert_order_latest_and_derived_ratio(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_market_daily(make_market_record("2026-07-17"), database_path=database_path)
    save_market_daily(make_market_record("2026-07-16", breadth=(1000, 900, 100)), database_path=database_path)
    assert save_market_daily(make_market_record("2026-07-17", sh=400, sz=500), database_path=database_path) == 1
    loaded = load_market_daily(database_path=database_path)
    assert loaded["trade_date"].tolist() == ["2026-07-16", "2026-07-17"]
    assert int(loaded.iloc[1]["total_amount_yuan"]) == 900
    assert loaded.iloc[1]["advance_ratio"] == pytest.approx(0.6)
    assert get_latest_market_trade_date(database_path=database_path) == "2026-07-17"
    assert get_market_daily("2026-07-17", database_path=database_path) == make_market_record("2026-07-17", sh=400, sz=500)


def test_market_database_preserves_null_as_sqlite_null(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_market_daily(compose_market_daily("2026-07-17"), database_path=database_path)
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT sh_amount_yuan, total_amount_yuan, advance_count FROM market_daily"
        ).fetchone()
    assert row == (None, None, None)
    loaded = load_market_daily(database_path=database_path)
    assert pd.isna(loaded.iloc[0]["sh_amount_yuan"])
    assert pd.isna(loaded.iloc[0]["advance_ratio"])


def test_market_database_failed_upsert_rolls_back_and_preserves_old_record(tmp_path: Path):
    database_path = tmp_path / "test.db"
    original = make_market_record("2026-07-17")
    save_market_daily(original, database_path=database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("""CREATE TRIGGER reject_market_update BEFORE UPDATE ON market_daily
            BEGIN SELECT RAISE(ABORT, 'blocked'); END;""")
    with pytest.raises(RuntimeError, match="Unable to save market daily data"):
        save_market_daily(make_market_record("2026-07-17", sh=999, sz=999), database_path=database_path)
    assert get_market_daily("2026-07-17", database_path=database_path) == original


def test_market_database_filters_dates_and_validates_inputs(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_market_daily(make_market_record("2026-07-16"), database_path=database_path)
    save_market_daily(make_market_record("2026-07-17"), database_path=database_path)
    loaded = load_market_daily(database_path=database_path, start_date="2026-07-17", end_date="2026-07-17")
    assert loaded["trade_date"].tolist() == ["2026-07-17"]
    with pytest.raises(ValueError, match="start_date"):
        load_market_daily(database_path=database_path, start_date="2026-07-18", end_date="2026-07-17")
    with pytest.raises(TypeError, match="MarketDaily"):
        save_market_daily({"trade_date": "2026-07-17"}, database_path=database_path)


def sector_definition(level=1, code="BK0001", name="Industry"):
    return SectorDefinition(
        EASTMONEY_INDUSTRY_SECTOR_TYPE,
        level,
        code,
        name,
        EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    )


def sector_kline():
    return pd.DataFrame([
        {"date": "2026-07-18", "open": 10, "high": 12, "low": 9, "close": 11, "volume": None, "amount": "--", "change_pct": pd.NA},
        {"date": "2026-07-17", "open": 9, "high": 10, "low": 8, "close": 9.5, "volume": 90, "amount": 900, "change_pct": -1.0},
    ])


def test_sector_tables_are_additive_with_exact_schema_and_preserve_existing_data(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_daily_kline("000021", make_kline_data(), database_path=database_path)
    save_index_daily_kline("SH000001", make_index_data(), database_path=database_path)
    save_market_daily(make_market_record(), database_path=database_path)
    init_database(database_path)
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        registry = [(row[1], row[2], row[3], row[5]) for row in connection.execute("PRAGMA table_info(sector_registry)")]
        daily = [(row[1], row[2], row[3], row[5]) for row in connection.execute("PRAGMA table_info(sector_daily)")]
    assert {"stock_daily", "index_daily", "market_daily", "sector_registry", "sector_daily"} <= tables
    assert registry == [
        ("sector_type", "TEXT", 1, 1), ("sector_level", "INTEGER", 1, 2),
        ("sector_code", "TEXT", 1, 3), ("sector_name", "TEXT", 1, 0),
        ("source", "TEXT", 1, 0), ("is_active", "INTEGER", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ]
    assert daily == [
        ("sector_type", "TEXT", 1, 1), ("sector_level", "INTEGER", 1, 2),
        ("sector_code", "TEXT", 1, 3), ("trade_date", "TEXT", 1, 4),
        ("open", "REAL", 1, 0), ("high", "REAL", 1, 0),
        ("low", "REAL", 1, 0), ("close", "REAL", 1, 0),
        ("volume", "INTEGER", 0, 0), ("amount", "REAL", 0, 0),
        ("change_pct", "REAL", 0, 0), ("source", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ]
    assert len(load_daily_kline("000021", database_path=database_path)) == 2
    assert len(load_index_daily_kline("SH000001", database_path=database_path)) == 2
    assert get_market_daily("2026-07-17", database_path=database_path) is not None


def test_sector_registry_snapshot_upserts_name_marks_inactive_and_filters(tmp_path: Path):
    database_path = tmp_path / "test.db"
    first = [sector_definition(2, "BK0002", "Old"), sector_definition(1, "BK0001", "One")]
    assert save_sector_registry_snapshot(first, database_path=database_path) == 2
    assert save_sector_registry_snapshot([sector_definition(2, "BK0002", "New")], database_path=database_path) == 1
    active = load_sector_registry(database_path=database_path)
    assert active[["sector_code", "sector_name", "is_active"]].to_dict("records") == [
        {"sector_code": "BK0002", "sector_name": "New", "is_active": True}
    ]
    all_rows = load_sector_registry(database_path=database_path, active_only=False)
    assert all_rows["sector_code"].tolist() == ["BK0001", "BK0002"]
    assert all_rows["is_active"].tolist() == [False, True]
    assert load_sector_registry(database_path=database_path, sector_level=1, active_only=False)["sector_code"].tolist() == ["BK0001"]
    assert get_sector_definition(EASTMONEY_INDUSTRY_SECTOR_TYPE, 2, "BK0002", database_path=database_path).sector_name == "New"
    assert get_sector_definition(EASTMONEY_INDUSTRY_SECTOR_TYPE, 1, "BK0001", database_path=database_path) is None
    assert get_sector_definition(EASTMONEY_INDUSTRY_SECTOR_TYPE, 1, "BK0001", database_path=database_path, active_only=False).sector_name == "One"


def test_sector_registry_snapshot_failure_rolls_back_active_state(tmp_path: Path):
    database_path = tmp_path / "test.db"
    original = [sector_definition(1, "BK0001"), sector_definition(2, "BK0002")]
    save_sector_registry_snapshot(original, database_path=database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("""CREATE TRIGGER reject_registry_insert BEFORE INSERT ON sector_registry
            WHEN NEW.sector_code = 'BK0003' BEGIN SELECT RAISE(ABORT, 'blocked'); END;""")
    with pytest.raises(RuntimeError, match="Unable to save sector registry snapshot"):
        save_sector_registry_snapshot([sector_definition(3, "BK0003")], database_path=database_path)
    assert load_sector_registry(database_path=database_path)["sector_code"].tolist() == ["BK0001", "BK0002"]


def test_sector_daily_requires_active_matching_definition_and_upserts_revision(tmp_path: Path):
    database_path = tmp_path / "test.db"
    current = sector_definition()
    with pytest.raises(ValueError, match="not active"):
        save_sector_daily_kline(current, sector_kline(), database_path=database_path)
    save_sector_registry_snapshot([current], database_path=database_path)
    assert save_sector_daily_kline(current, sector_kline(), database_path=database_path) == 2
    revised = sector_kline().iloc[[0]].copy()
    revised.loc[:, "close"] = 11.5
    save_sector_daily_kline(current, revised, database_path=database_path)
    loaded = load_sector_daily_kline(current, database_path=database_path)
    assert loaded["date"].tolist() == ["2026-07-17", "2026-07-18"]
    assert loaded.iloc[1]["close"] == 11.5
    assert str(loaded["volume"].dtype) == "Int64"
    assert pd.isna(loaded.iloc[1]["volume"])
    assert pd.isna(loaded.iloc[1]["amount"])
    assert pd.isna(loaded.iloc[1]["change_pct"])
    assert get_latest_sector_trade_date(current, database_path=database_path) == "2026-07-18"
    with pytest.raises(ValueError, match="name"):
        save_sector_daily_kline(sector_definition(name="Renamed"), sector_kline(), database_path=database_path)
    wrong_source = SectorDefinition(EASTMONEY_INDUSTRY_SECTOR_TYPE, 1, "BK0001", "Industry", "other")
    with pytest.raises(ValueError, match="source"):
        save_sector_daily_kline(wrong_source, sector_kline(), database_path=database_path)


def test_sector_daily_rejects_inactive_save_but_allows_historical_read_and_filters(tmp_path: Path):
    database_path = tmp_path / "test.db"
    old = sector_definition(1, "BK0001")
    save_sector_registry_snapshot([old], database_path=database_path)
    save_sector_daily_kline(old, sector_kline(), database_path=database_path)
    save_sector_registry_snapshot([sector_definition(2, "BK0002")], database_path=database_path)
    with pytest.raises(ValueError, match="not active"):
        save_sector_daily_kline(old, sector_kline(), database_path=database_path)
    loaded = load_sector_daily_kline(old, database_path=database_path, start_date="2026-07-18", end_date="2026-07-18")
    assert loaded["date"].tolist() == ["2026-07-18"]
    with pytest.raises(ValueError, match="start_date"):
        load_sector_daily_kline(old, database_path=database_path, start_date="2026-07-19", end_date="2026-07-18")


def test_sector_daily_batch_failure_rolls_back_all_rows(tmp_path: Path):
    database_path = tmp_path / "test.db"
    current = sector_definition()
    save_sector_registry_snapshot([current], database_path=database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("""CREATE TRIGGER reject_sector_second BEFORE INSERT ON sector_daily
            WHEN NEW.trade_date = '2026-07-18' BEGIN SELECT RAISE(ABORT, 'blocked'); END;""")
    with pytest.raises(RuntimeError, match="Unable to save sector daily data"):
        save_sector_daily_kline(current, sector_kline(), database_path=database_path)
    assert load_sector_daily_kline(current, database_path=database_path).empty


def _save_panel_fixture(database_path: Path):
    definitions = [
        sector_definition(1, "BK0001", "Level One"),
        sector_definition(2, "BK0002", "Level Two"),
        sector_definition(3, "BK0003", "Level Three"),
    ]
    save_sector_registry_snapshot(definitions, database_path=database_path)
    for position, definition in enumerate(definitions):
        data = sector_kline().copy()
        data.loc[data["date"] == "2026-07-18", "volume"] = None
        data.loc[data["date"] == "2026-07-18", "amount"] = None
        data.loc[data["date"] == "2026-07-18", "change_pct"] = None
        data["close"] = data["close"] + position
        data["high"] = data["high"] + position
        save_sector_daily_kline(definition, data, database_path=database_path)
    return definitions


def test_load_sector_daily_panel_returns_fixed_columns_types_sort_and_nulls(tmp_path: Path):
    database_path = tmp_path / "test.db"
    _save_panel_fixture(database_path)
    result = load_sector_daily_panel(database_path=database_path)
    assert result.columns.tolist() == list(SECTOR_DAILY_PANEL_COLUMNS)
    assert list(zip(result["sector_level"], result["sector_code"], result["date"])) == sorted(
        zip(result["sector_level"], result["sector_code"], result["date"])
    )
    assert str(result["sector_level"].dtype) == "int64"
    assert result["is_active"].dtype == bool
    assert str(result["volume"].dtype) == "Int64"
    missing = result[result["date"] == "2026-07-18"]
    assert missing["volume"].isna().all()
    assert missing["amount"].isna().all()
    assert missing["change_pct"].isna().all()
    assert not (missing[["amount", "change_pct"]] == 0).any().any()


@pytest.mark.parametrize("level", [1, 2, 3])
def test_load_sector_daily_panel_filters_each_level_and_date_range(tmp_path: Path, level: int):
    database_path = tmp_path / "test.db"
    _save_panel_fixture(database_path)
    result = load_sector_daily_panel(
        database_path=database_path,
        sector_level=level,
        start_date="2026-07-18",
        end_date="2026-07-18",
    )
    assert result["sector_level"].tolist() == [level]
    assert result["date"].tolist() == ["2026-07-18"]


def test_load_sector_daily_panel_active_filter_current_name_and_inactive_history(tmp_path: Path):
    database_path = tmp_path / "test.db"
    definitions = _save_panel_fixture(database_path)
    renamed = sector_definition(2, "BK0002", "Current Name")
    save_sector_registry_snapshot([renamed, definitions[2]], database_path=database_path)
    active = load_sector_daily_panel(database_path=database_path)
    assert set(active["sector_code"]) == {"BK0002", "BK0003"}
    assert active.loc[active["sector_code"] == "BK0002", "sector_name"].unique().tolist() == ["Current Name"]
    all_rows = load_sector_daily_panel(database_path=database_path, active_only=False)
    old = all_rows[all_rows["sector_code"] == "BK0001"]
    assert len(old) == 2
    assert old["is_active"].tolist() == [False, False]


def test_load_sector_daily_panel_joins_on_complete_business_key(tmp_path: Path):
    database_path = tmp_path / "test.db"
    definition = sector_definition(1, "BK0001")
    save_sector_registry_snapshot([definition], database_path=database_path)
    save_sector_daily_kline(definition, sector_kline().iloc[[0]], database_path=database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO sector_daily (
                sector_type, sector_level, sector_code, trade_date,
                open, high, low, close, volume, amount, change_pct, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (EASTMONEY_INDUSTRY_SECTOR_TYPE, 2, "BK0001", "2026-07-19", 1, 2, 0.5, 1.5, 1, 2, 3, "test", "2026-07-19T00:00:00+00:00"),
        )
    result = load_sector_daily_panel(database_path=database_path, active_only=False)
    assert result[["sector_level", "sector_code", "date"]].to_dict("records") == [
        {"sector_level": 1, "sector_code": "BK0001", "date": "2026-07-18"}
    ]


def test_load_sector_daily_panel_empty_structure_and_parameter_validation(tmp_path: Path):
    database_path = tmp_path / "test.db"
    empty = load_sector_daily_panel(database_path=database_path, sector_level=None)
    assert empty.empty
    assert empty.columns.tolist() == list(SECTOR_DAILY_PANEL_COLUMNS)
    assert str(empty["sector_level"].dtype) == "int64"
    assert empty["is_active"].dtype == bool
    assert str(empty["volume"].dtype) == "Int64"
    with pytest.raises(TypeError, match="active_only"):
        load_sector_daily_panel(database_path=database_path, active_only=1)
    with pytest.raises(ValueError, match="start_date"):
        load_sector_daily_panel(database_path=database_path, start_date="2026-07-19", end_date="2026-07-18")
    with pytest.raises(ValueError):
        load_sector_daily_panel(database_path=database_path, sector_level=4)


def test_load_sector_daily_panel_wraps_initialization_runtime_error(tmp_path: Path, monkeypatch):
    original = RuntimeError("initialization failed")
    monkeypatch.setattr("src.data.database.init_database", Mock(side_effect=original))
    with pytest.raises(RuntimeError, match="^Unable to load sector daily panel$") as captured:
        load_sector_daily_panel(database_path=tmp_path / "test.db")
    assert captured.value.__cause__ is original


def security_master_frame(
    *,
    symbol: str = "600000",
    exchange: str = "XSHG",
    board: str = "SSE_MAIN",
    name: str = "浦发银行",
    list_date: str = "1999-11-10",
    delist_date=None,
    status: str = "LISTED",
    source: str = "SSE_OFFICIAL",
    as_of: str = "2026-07-22",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "local_symbol": symbol,
                "exchange": exchange,
                "asset_type": "COMMON_STOCK",
                "board": board,
                "current_name": name,
                "list_date": list_date,
                "delist_date": delist_date,
                "current_listing_status": status,
                "source": source,
                "source_as_of_date": as_of,
            }
        ]
    )


def security_event_frame(
    *,
    symbol: str = "600000",
    exchange: str = "XSHG",
    event_type: str = "LISTED",
    event_date: str = "1999-11-10",
    source: str = "SSE_OFFICIAL",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "local_symbol": symbol,
                "exchange": exchange,
                "asset_type": "COMMON_STOCK",
                "event_type": event_type,
                "event_date": event_date,
                "source": source,
            }
        ]
    )


def test_security_tables_initialize_idempotently_with_exact_schema(tmp_path: Path):
    database_path = tmp_path / "test.db"
    init_database(database_path)
    init_database(database_path)
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        master = [(row[1], row[2], row[3], row[5]) for row in connection.execute(
            "PRAGMA table_info(security_master)"
        )]
        events = [(row[1], row[2], row[3], row[5]) for row in connection.execute(
            "PRAGMA table_info(security_listing_event)"
        )]
    assert {
        "stock_daily", "index_daily", "market_daily", "sector_registry",
        "sector_daily", "security_master", "security_listing_event",
    } <= tables
    assert master == [
        ("security_id", "INTEGER", 0, 1),
        ("local_symbol", "TEXT", 1, 0),
        ("exchange", "TEXT", 1, 0),
        ("asset_type", "TEXT", 1, 0),
        ("board", "TEXT", 1, 0),
        ("current_name", "TEXT", 1, 0),
        ("list_date", "TEXT", 1, 0),
        ("delist_date", "TEXT", 0, 0),
        ("current_listing_status", "TEXT", 1, 0),
        ("source", "TEXT", 1, 0),
        ("source_as_of_date", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ]
    assert events == [
        ("security_id", "INTEGER", 1, 1),
        ("event_type", "TEXT", 1, 2),
        ("event_date", "TEXT", 1, 0),
        ("source", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ]


def test_security_master_natural_unique_key_is_enforced(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    with sqlite3.connect(database_path) as connection:
        original = connection.execute(
            "SELECT * FROM security_master WHERE local_symbol = '600000'"
        ).fetchone()
        values = list(original)
        values[0] = None
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO security_master VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )


def test_security_master_initial_save_reads_identity_and_provenance(tmp_path: Path):
    database_path = tmp_path / "test.db"
    assert save_security_master(security_master_frame(), database_path=database_path) == 1
    result = load_security_master(database_path=database_path)
    assert result.columns.tolist() == list(SECURITY_MASTER_RESULT_COLUMNS)
    assert result.loc[0, "security_id"] == 1
    assert result.loc[0, "local_symbol"] == "600000"
    assert result.loc[0, "source"] == "SSE_OFFICIAL"
    assert result.loc[0, "source_as_of_date"] == "2026-07-22"
    assert result.loc[0, "updated_at"].endswith("+00:00")


def test_security_master_identical_snapshot_is_idempotent(tmp_path: Path):
    database_path = tmp_path / "test.db"
    frame = security_master_frame()
    assert save_security_master(frame, database_path=database_path) == 1
    first = load_security_master(database_path=database_path)
    assert save_security_master(frame, database_path=database_path) == 0
    second = load_security_master(database_path=database_path)
    pd.testing.assert_frame_equal(first, second)


def test_security_master_newer_snapshot_updates_name_and_preserves_id(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    original_id = load_security_master(database_path=database_path).loc[0, "security_id"]
    updated = security_master_frame(name="浦发银行股份", as_of="2026-07-23")
    assert save_security_master(updated, database_path=database_path) == 1
    result = load_security_master(database_path=database_path)
    assert result.loc[0, "security_id"] == original_id
    assert result.loc[0, "current_name"] == "浦发银行股份"


def test_security_master_rejects_older_snapshot_without_change(tmp_path: Path):
    database_path = tmp_path / "test.db"
    current = security_master_frame()
    save_security_master(current, database_path=database_path)
    with pytest.raises(ValueError, match="Older security snapshot"):
        save_security_master(
            security_master_frame(name="Old", as_of="2026-07-21"),
            database_path=database_path,
        )
    assert load_security_master(database_path=database_path).loc[0, "current_name"] == "浦发银行"


def test_security_master_rejects_conflicting_same_date_snapshot(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    with pytest.raises(ValueError, match="same-date"):
        save_security_master(
            security_master_frame(name="Conflict"), database_path=database_path
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("board", "SSE_STAR"), ("list_date", "1999-11-11")],
)
def test_security_master_rejects_stable_fact_change(tmp_path: Path, field: str, value: str):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    changed = security_master_frame(as_of="2026-07-23")
    changed.loc[:, field] = value
    with pytest.raises(ValueError, match="Stable security identity"):
        save_security_master(changed, database_path=database_path)


def test_security_master_allows_listed_to_delisted_transition(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    delisted = security_master_frame(
        delist_date="2026-07-23", status="DELISTED", as_of="2026-07-23"
    )
    assert save_security_master(delisted, database_path=database_path) == 1
    result = load_security_master(database_path=database_path)
    assert result.loc[0, "current_listing_status"] == "DELISTED"
    assert result.loc[0, "delist_date"] == "2026-07-23"


def test_security_master_rejects_changed_delist_date_without_event(tmp_path: Path):
    database_path = tmp_path / "test.db"
    original = security_master_frame(
        delist_date="2026-07-20", status="DELISTED", as_of="2026-07-20"
    )
    save_security_master(original, database_path=database_path)
    changed = security_master_frame(
        delist_date="2026-07-21", status="DELISTED", as_of="2026-07-23"
    )
    with pytest.raises(ValueError, match="Delisting date is immutable"):
        save_security_master(changed, database_path=database_path)
    result = load_security_master(database_path=database_path)
    assert result.loc[0, "delist_date"] == "2026-07-20"
    assert result.loc[0, "source_as_of_date"] == "2026-07-20"


def test_security_master_allows_delisted_name_and_source_update(tmp_path: Path):
    database_path = tmp_path / "test.db"
    original = security_master_frame(
        delist_date="2026-07-20", status="DELISTED", as_of="2026-07-20"
    )
    save_security_master(original, database_path=database_path)
    before = load_security_master(database_path=database_path).iloc[0]
    updated = security_master_frame(
        name="退市后名称快照",
        delist_date="2026-07-20",
        status="DELISTED",
        source="SSE_DELIST_OFFICIAL",
        as_of="2026-07-23",
    )
    assert save_security_master(updated, database_path=database_path) == 1
    after = load_security_master(database_path=database_path).iloc[0]
    assert after["security_id"] == before["security_id"]
    assert after["current_name"] == "退市后名称快照"
    assert after["source"] == "SSE_DELIST_OFFICIAL"
    assert after["delist_date"] == "2026-07-20"


def test_security_master_rejects_delisted_to_listed_transition(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_security_master(
        security_master_frame(
            delist_date="2026-07-23", status="DELISTED", as_of="2026-07-23"
        ),
        database_path=database_path,
    )
    with pytest.raises(ValueError, match="Relisting"):
        save_security_master(
            security_master_frame(as_of="2026-07-24"), database_path=database_path
        )


def test_security_listing_event_initial_save_and_idempotency(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    event = security_event_frame()
    assert save_security_listing_events(event, database_path=database_path) == 1
    first = load_security_listing_events(database_path=database_path)
    assert first.columns.tolist() == list(SECURITY_LISTING_EVENT_RESULT_COLUMNS)
    assert first.loc[0, "event_type"] == "LISTED"
    assert save_security_listing_events(event, database_path=database_path) == 0
    pd.testing.assert_frame_equal(
        first, load_security_listing_events(database_path=database_path)
    )


@pytest.mark.parametrize(
    ("event_date", "source"),
    [("1999-11-11", "SSE_OFFICIAL"), ("1999-11-10", "OTHER")],
)
def test_security_listing_event_rejects_conflicts(tmp_path: Path, event_date: str, source: str):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    save_security_listing_events(security_event_frame(), database_path=database_path)
    with pytest.raises(ValueError, match="listing event|Listing event"):
        save_security_listing_events(
            security_event_frame(event_date=event_date, source=source),
            database_path=database_path,
        )


def test_security_listing_event_rejects_unknown_security(tmp_path: Path):
    with pytest.raises(ValueError, match="Unknown security"):
        save_security_listing_events(
            security_event_frame(), database_path=tmp_path / "test.db"
        )


def test_security_listing_event_must_match_master_dates(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    with pytest.raises(ValueError, match="does not match"):
        save_security_listing_events(
            security_event_frame(event_date="1999-11-11"), database_path=database_path
        )
    with pytest.raises(ValueError, match="does not match"):
        save_security_listing_events(
            security_event_frame(event_type="DELISTED", event_date="2026-07-23"),
            database_path=database_path,
        )


def test_security_listing_event_batch_failure_rolls_back(tmp_path: Path):
    database_path = tmp_path / "test.db"
    masters = pd.concat(
        [
            security_master_frame(
                symbol="000001", exchange="XSHE", board="SZSE_MAIN",
                name="平安银行", list_date="1991-04-03", source="SZSE_OFFICIAL",
            ),
            security_master_frame(),
        ],
        ignore_index=True,
    )
    save_security_master(masters, database_path=database_path)
    events = pd.concat(
        [
            security_event_frame(
                symbol="000001", exchange="XSHE", event_date="1991-04-03",
                source="SZSE_OFFICIAL",
            ),
            security_event_frame(event_date="1999-11-11"),
        ],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="does not match"):
        save_security_listing_events(events, database_path=database_path)
    assert load_security_listing_events(database_path=database_path).empty


def test_security_master_batch_failure_rolls_back(tmp_path: Path):
    database_path = tmp_path / "test.db"
    first = security_master_frame(
        symbol="000001", exchange="XSHE", board="SZSE_MAIN", name="平安银行",
        list_date="1991-04-03", source="SZSE_OFFICIAL",
    )
    second = security_master_frame(
        delist_date="2026-07-20", status="DELISTED", as_of="2026-07-20"
    )
    save_security_master(
        pd.concat([first, second], ignore_index=True), database_path=database_path
    )
    batch = pd.concat(
        [
            first.assign(current_name="合法的新名称", source_as_of_date="2026-07-23"),
            security_master_frame(
                delist_date="2026-07-21", status="DELISTED", as_of="2026-07-23"
            ),
        ],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="Delisting date is immutable"):
        save_security_master(batch, database_path=database_path)
    result = load_security_master(database_path=database_path)
    first_after = result[result["exchange"] == "XSHE"].iloc[0]
    second_after = result[result["exchange"] == "XSHG"].iloc[0]
    assert first_after["current_name"] == "平安银行"
    assert first_after["source_as_of_date"] == "2026-07-22"
    assert second_after["delist_date"] == "2026-07-20"


def test_security_loaders_filter_sort_and_return_fixed_empty_columns(tmp_path: Path):
    database_path = tmp_path / "test.db"
    frames = pd.concat(
        [
            security_master_frame(),
            security_master_frame(
                symbol="000001", exchange="XSHE", board="SZSE_MAIN",
                name="平安银行", list_date="1991-04-03", source="SZSE_OFFICIAL",
            ),
        ],
        ignore_index=True,
    )
    save_security_master(frames, database_path=database_path)
    save_security_listing_events(
        pd.concat(
            [
                security_event_frame(),
                security_event_frame(
                    symbol="000001", exchange="XSHE", event_date="1991-04-03",
                    source="SZSE_OFFICIAL",
                ),
            ],
            ignore_index=True,
        ),
        database_path=database_path,
    )
    assert load_security_master(
        database_path=database_path, exchanges="XSHE", boards=["SZSE_MAIN"],
        listing_status="LISTED",
    )["local_symbol"].tolist() == ["000001"]
    assert load_security_listing_events(
        database_path=database_path, local_symbols=["600000"]
    )["local_symbol"].tolist() == ["600000"]
    empty_master = load_security_master(database_path=database_path, exchanges=[])
    empty_events = load_security_listing_events(database_path=database_path, local_symbols=[])
    assert empty_master.empty
    assert empty_master.columns.tolist() == list(SECURITY_MASTER_RESULT_COLUMNS)
    assert empty_events.empty
    assert empty_events.columns.tolist() == list(SECURITY_LISTING_EVENT_RESULT_COLUMNS)


@pytest.mark.parametrize("symbol", ["６０００００", "٦٠٠٠٠٠", "60000A", "60000 ", 600000, True])
def test_security_listing_event_loader_rejects_non_ascii_symbol_filter(
    tmp_path: Path,
    symbol,
):
    with pytest.raises(ValueError, match="Invalid stock code"):
        load_security_listing_events(
            database_path=tmp_path / "test.db", local_symbols=[symbol]
        )


def test_security_identity_and_events_distinguish_same_symbol_across_exchanges(tmp_path: Path):
    database_path = tmp_path / "test.db"
    masters = pd.concat(
        [
            security_master_frame(),
            security_master_frame(
                symbol="600000", exchange="XSHE", board="SZSE_MAIN",
                name="同代码深市测试证券", list_date="2000-01-01",
                source="SZSE_OFFICIAL",
            ),
        ],
        ignore_index=True,
    )
    assert save_security_master(masters, database_path=database_path) == 2
    loaded_master = load_security_master(database_path=database_path)
    assert loaded_master["local_symbol"].tolist() == ["600000", "600000"]
    assert loaded_master["exchange"].tolist() == ["XSHE", "XSHG"]
    assert loaded_master["security_id"].nunique() == 2

    events = pd.concat(
        [
            security_event_frame(),
            security_event_frame(
                symbol="600000", exchange="XSHE", event_date="2000-01-01",
                source="SZSE_OFFICIAL",
            ),
        ],
        ignore_index=True,
    )
    assert save_security_listing_events(events, database_path=database_path) == 2
    loaded_events = load_security_listing_events(
        database_path=database_path, local_symbols=["600000"]
    )
    assert len(loaded_events) == 2
    assert loaded_events["security_id"].nunique() == 2
    assert loaded_events[["exchange", "event_date"]].to_dict("records") == [
        {"exchange": "XSHE", "event_date": "2000-01-01"},
        {"exchange": "XSHG", "event_date": "1999-11-10"},
    ]


def test_security_listing_event_foreign_key_restricts_master_delete(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    save_security_listing_events(security_event_frame(), database_path=database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM security_master WHERE local_symbol = '600000'")


def test_security_tables_do_not_change_existing_stock_daily_behavior(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_daily_kline("000021", make_kline_data(), database_path=database_path)
    save_security_master(security_master_frame(), database_path=database_path)
    loaded = load_daily_kline("000021", database_path=database_path)
    assert loaded["date"].tolist() == ["2026-07-16", "2026-07-17"]
    assert get_latest_trade_date("000021", database_path=database_path) == "2026-07-17"


def _database_price_frame(security_id: int, **changes) -> pd.DataFrame:
    row = {
        "security_id": security_id,
        "trade_date": "2026-07-21",
        "adjustment": "QFQ",
        "source": "EASTMONEY",
        "provider_adjustment": "fqt=1",
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 1000,
        "volume_unit": "PROVIDER_NATIVE",
        "amount": None,
        "amount_unit": None,
        "is_final": True,
        "provider_as_of_date": "2026-07-21",
        "observed_at": "2026-07-21T16:00:00+08:00",
    }
    row.update(changes)
    return pd.DataFrame([row])


def test_price_schema_index_and_legacy_stock_schema_are_stable(tmp_path: Path):
    database_path = tmp_path / "test.db"
    init_database(database_path)
    init_database(database_path)
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"stock_daily_price", "stock_daily_price_revision"} <= tables
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(stock_daily_price)"
            )
        }
        assert "idx_stock_daily_price_trade_date" in indexes
        assert "idx_stock_daily_price_series" in indexes
        series_index_columns = [
            row[2]
            for row in connection.execute(
                "PRAGMA index_info(idx_stock_daily_price_series)"
            )
        ]
        assert series_index_columns == [
            "security_id",
            "adjustment",
            "source",
            "trade_date",
        ]
        stock_columns = [
            (row[1], row[2], row[3], row[5])
            for row in connection.execute("PRAGMA table_info(stock_daily)")
        ]
        assert stock_columns == [
            ("symbol", "TEXT", 1, 1),
            ("trade_date", "TEXT", 1, 2),
            ("open", "REAL", 1, 0),
            ("high", "REAL", 1, 0),
            ("low", "REAL", 1, 0),
            ("close", "REAL", 1, 0),
            ("volume", "INTEGER", 1, 0),
            ("amount", "REAL", 1, 0),
            ("source", "TEXT", 1, 0),
            ("updated_at", "TEXT", 1, 0),
        ]
        price_pk = [
            row[1]
            for row in sorted(
                (row for row in connection.execute(
                    "PRAGMA table_info(stock_daily_price)"
                ) if row[5]),
                key=lambda row: row[5],
            )
        ]
        assert price_pk == ["security_id", "trade_date", "adjustment", "source"]
        revision_pk = next(
            row
            for row in connection.execute(
                "PRAGMA table_info(stock_daily_price_revision)"
            )
            if row[1] == "revision_id"
        )
        assert revision_pk[2] == "INTEGER" and revision_pk[5] == 1


def test_price_amount_unit_sql_check_rejects_invalid_pairs(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    security_id = int(
        load_security_master(database_path=database_path).loc[0, "security_id"]
    )
    insert_sql = """
        INSERT INTO stock_daily_price (
            security_id, trade_date, adjustment, source,
            provider_adjustment, open, high, low, close, volume,
            volume_unit, amount, amount_unit, is_final,
            provider_as_of_date, observed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def values(
        trade_date: str,
        source: str,
        amount: float | None,
        amount_unit: str | None,
    ) -> tuple[object, ...]:
        return (
            security_id,
            trade_date,
            "QFQ",
            source,
            "fqt=1",
            10.0,
            12.0,
            9.0,
            11.0,
            1000,
            "PROVIDER_NATIVE",
            amount,
            amount_unit,
            1,
            trade_date,
            f"{trade_date}T16:00:00+00:00",
            f"{trade_date}T16:00:00+00:00",
        )

    with sqlite3.connect(database_path) as connection:
        accepted = (
            values("2026-07-21", "NULL_PAIR", None, None),
            values("2026-07-22", "ZERO_CNY", 0.0, "CNY"),
            values(
                "2026-07-23",
                "NATIVE_AMOUNT",
                10000.0,
                "PROVIDER_NATIVE",
            ),
        )
        connection.executemany(insert_sql, accepted)
        connection.commit()

        rejected = (
            values("2026-07-24", "MISSING_UNIT", 10000.0, None),
            values("2026-07-25", "UNIT_WITHOUT_AMOUNT", None, "CNY"),
            values("2026-07-26", "INVALID_UNIT", 10000.0, "RMB"),
            values("2026-07-27", "NEGATIVE_AMOUNT", -1.0, "CNY"),
        )
        for row in rejected:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert_sql, row)
            connection.rollback()

        stored_sources = {
            row[0]
            for row in connection.execute(
                "SELECT source FROM stock_daily_price ORDER BY source"
            )
        }
    assert stored_sources == {"NULL_PAIR", "ZERO_CNY", "NATIVE_AMOUNT"}


def test_price_series_query_plan_uses_full_prefix_index(tmp_path: Path):
    database_path = tmp_path / "test.db"
    init_database(database_path)
    with sqlite3.connect(database_path) as connection:
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT *
            FROM stock_daily_price
            WHERE security_id = ?
              AND adjustment = ?
              AND source = ?
              AND trade_date BETWEEN ? AND ?
            ORDER BY security_id, trade_date, adjustment, source
            """,
            (1, "QFQ", "EASTMONEY", "2026-01-01", "2026-12-31"),
        ).fetchall()
    details = "\n".join(str(row[3]) for row in plan)
    assert "USING INDEX idx_stock_daily_price_series" in details
    assert (
        "security_id=? AND adjustment=? AND source=? "
        "AND trade_date>? AND trade_date<?"
    ) in details


def test_price_and_revision_foreign_keys_restrict_deletes(tmp_path: Path):
    database_path = tmp_path / "test.db"
    save_security_master(security_master_frame(), database_path=database_path)
    security_id = int(load_security_master(database_path=database_path).loc[0, "security_id"])
    save_stock_daily_prices(
        _database_price_frame(security_id), database_path=database_path
    )
    save_stock_daily_prices(
        _database_price_frame(
            security_id,
            close=11.5,
            observed_at="2026-07-21T17:00:00+08:00",
        ),
        database_path=database_path,
    )
    assert len(load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )) == 1
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM stock_daily_price WHERE security_id = ?",
                (security_id,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM security_master WHERE security_id = ?",
                (security_id,),
            )
    current = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert len(current) == 1 and current.loc[0, "close"] == 11.5
