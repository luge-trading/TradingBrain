import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.analysis.sector_strength import (
    DEFAULT_SECTOR_BENCHMARK_CODE,
    SECTOR_STRENGTH_COLUMNS,
    calculate_sector_strength_snapshot,
    load_sector_strength_snapshot,
)
from src.data.database import (
    load_sector_daily_panel,
    save_index_daily_kline,
    save_sector_daily_kline,
    save_sector_registry_snapshot,
)
from src.data.sector import (
    EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    EASTMONEY_INDUSTRY_SECTOR_TYPE,
    SectorDefinition,
)


DATES = pd.bdate_range("2026-06-15", periods=25).strftime("%Y-%m-%d").tolist()
AS_OF = DATES[-1]


def benchmark(dates=DATES):
    return pd.DataFrame({"date": dates, "close": [100.0 + i for i in range(len(dates))]})


def panel_for(
    codes=("BK0001", "BK0002"),
    *,
    level=1,
    dates=DATES,
    active=True,
    rates=None,
):
    rates = rates or [0.01 + position * 0.005 for position in range(len(codes))]
    rows = []
    for code, rate in zip(codes, rates, strict=True):
        for position, date in enumerate(dates):
            rows.append({
                "sector_type": EASTMONEY_INDUSTRY_SECTOR_TYPE,
                "sector_level": level,
                "sector_code": code,
                "sector_name": f"Industry {code}",
                "is_active": active,
                "date": date,
                "close": 100.0 * (1 + rate * position),
                "amount": 1000.0 + 10 * position,
            })
    return pd.DataFrame(rows)


def calculate(panel=None, bench=None, **kwargs):
    return calculate_sector_strength_snapshot(
        panel_for() if panel is None else panel,
        benchmark() if bench is None else bench,
        sector_level=kwargs.pop("sector_level", 1),
        as_of_date=kwargs.pop("as_of_date", AS_OF),
        **kwargs,
    )


def test_constants_output_schema_and_types_are_exact():
    result = calculate()
    assert DEFAULT_SECTOR_BENCHMARK_CODE == "SH000001"
    assert result.columns.tolist() == list(SECTOR_STRENGTH_COLUMNS)
    assert str(result["sector_level"].dtype) == "int64"
    assert result["is_active"].dtype == bool
    assert str(result["sector_rank_5d"].dtype) == "Int64"
    assert str(result["sector_count_5d"].dtype) == "Int64"
    float_columns = [column for column in result if column.startswith("return_") or column.startswith("relative_")]
    assert all(str(result[column].dtype) == "float64" for column in float_columns)


@pytest.mark.parametrize("target", ["panel", "benchmark"])
def test_rejects_non_dataframe_inputs(target):
    args = {"sector_daily_panel": panel_for(), "benchmark_daily": benchmark()}
    args["sector_daily_panel" if target == "panel" else "benchmark_daily"] = []
    with pytest.raises(TypeError, match="DataFrame"):
        calculate_sector_strength_snapshot(**args, sector_level=1, as_of_date=AS_OF)


@pytest.mark.parametrize("target,column", [("panel", "amount"), ("panel", "sector_code"), ("benchmark", "close")])
def test_rejects_missing_input_columns(target, column):
    panel = panel_for().drop(columns=column) if target == "panel" else panel_for()
    bench = benchmark().drop(columns=column) if target == "benchmark" else benchmark()
    with pytest.raises(ValueError, match="missing columns"):
        calculate_sector_strength_snapshot(panel, bench, sector_level=1, as_of_date=AS_OF)


@pytest.mark.parametrize("kwargs,error", [
    ({"sector_level": True}, ValueError),
    ({"sector_level": 4}, ValueError),
    ({"as_of_date": "2026/07/17"}, ValueError),
    ({"active_only": 1}, TypeError),
])
def test_rejects_invalid_parameters(kwargs, error):
    with pytest.raises(error):
        calculate(**kwargs)


@pytest.mark.parametrize("column,value", [
    ("sector_type", "OTHER"),
    ("sector_level", 4),
    ("sector_code", "BK123"),
    ("sector_name", " "),
    ("is_active", "yes"),
    ("date", "2026/07/17"),
    ("close", 0),
    ("close", np.inf),
    ("amount", -1),
    ("amount", np.inf),
])
def test_rejects_invalid_sector_fact_values(column, value):
    panel = panel_for().astype({column: "object"})
    panel.loc[panel.index[0], column] = value
    with pytest.raises(ValueError):
        calculate(panel=panel)


@pytest.mark.parametrize("column,value", [("date", "bad"), ("close", 0), ("close", np.inf)])
def test_rejects_invalid_benchmark_values(column, value):
    bench = benchmark().astype({column: "object"})
    bench.loc[0, column] = value
    with pytest.raises(ValueError):
        calculate(bench=bench)


def test_rejects_duplicate_sector_or_benchmark_dates():
    panel = pd.concat([panel_for(), panel_for().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate sector"):
        calculate(panel=panel)
    bench = pd.concat([benchmark(), benchmark().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate benchmark"):
        calculate(bench=bench)


@pytest.mark.parametrize("column,value", [("sector_name", "Changed"), ("is_active", False)])
def test_rejects_inconsistent_current_registry_identity(column, value):
    panel = panel_for()
    panel.loc[0, column] = value
    with pytest.raises(ValueError, match=f"Inconsistent {column}"):
        calculate(panel=panel)


def test_does_not_modify_inputs_and_input_order_does_not_matter():
    panel = panel_for()
    bench = benchmark()
    panel_original = panel.copy(deep=True)
    bench_original = bench.copy(deep=True)
    expected = calculate(panel=panel, bench=bench)
    shuffled = calculate(
        panel=panel.sample(frac=1, random_state=3),
        bench=bench.sample(frac=1, random_state=4),
    )
    pd.testing.assert_frame_equal(expected, shuffled)
    pd.testing.assert_frame_equal(panel, panel_original)
    pd.testing.assert_frame_equal(bench, bench_original)


def test_exact_as_of_required_and_sector_missing_as_of_is_excluded():
    assert calculate(as_of_date="2026-07-19").empty
    panel = panel_for()
    panel = panel.loc[~((panel["sector_code"] == "BK0002") & (panel["date"] == AS_OF))]
    result = calculate(panel=panel)
    assert result["sector_code"].tolist() == ["BK0001"]


def test_future_sector_and_benchmark_rows_are_ignored_without_fallback():
    expected = calculate()
    future_panel = pd.concat([
        panel_for(),
        panel_for(dates=["2026-12-31"]),
    ], ignore_index=True)
    future_benchmark = pd.concat([
        benchmark(), pd.DataFrame([{"date": "2026-12-31", "close": 9999}])
    ], ignore_index=True)
    actual = calculate(panel=future_panel, bench=future_benchmark)
    pd.testing.assert_frame_equal(expected, actual)


@pytest.mark.parametrize("column,value", [
    ("close", -1),
    ("amount", np.inf),
    ("sector_type", "OTHER"),
    ("sector_level", 99),
    ("sector_code", "INVALID"),
    ("sector_name", ""),
    ("is_active", "not-a-bool"),
    ("sector_name", "Future Name"),
    ("is_active", False),
])
def test_future_sector_pollution_does_not_change_historical_snapshot(column, value):
    source = panel_for()
    expected = calculate(panel=source)
    future = source.iloc[[0]].copy()
    future.loc[:, "date"] = "2026-12-31"
    future[column] = future[column].astype("object")
    future.loc[:, column] = value
    actual = calculate(panel=pd.concat([source, future], ignore_index=True))
    pd.testing.assert_frame_equal(expected, actual)


def test_duplicate_future_sector_business_dates_do_not_change_historical_snapshot():
    source = panel_for()
    expected = calculate(panel=source)
    future = source.iloc[[0, 0]].copy()
    future.loc[:, "date"] = "2026-12-31"
    actual = calculate(panel=pd.concat([source, future], ignore_index=True))
    pd.testing.assert_frame_equal(expected, actual)


@pytest.mark.parametrize("mode", ["invalid_close", "duplicate_date"])
def test_future_benchmark_pollution_does_not_change_historical_snapshot(mode):
    source = benchmark()
    expected = calculate(bench=source)
    future = source.iloc[[0]].copy()
    future.loc[:, "date"] = "2026-12-31"
    if mode == "invalid_close":
        future.loc[:, "close"] = np.inf
    else:
        future = pd.concat([future, future], ignore_index=True)
    actual = calculate(bench=pd.concat([source, future], ignore_index=True))
    pd.testing.assert_frame_equal(expected, actual)


def test_unparseable_sector_date_still_fails_before_future_filtering():
    polluted = panel_for()
    bad = polluted.iloc[[0]].copy()
    bad.loc[:, "date"] = "future-but-not-a-date"
    bad.loc[:, "close"] = -1
    with pytest.raises(ValueError, match="trade date"):
        calculate(panel=pd.concat([polluted, bad], ignore_index=True))


def test_unparseable_benchmark_date_still_fails_before_future_filtering():
    polluted = benchmark()
    bad = pd.DataFrame([{"date": "future-but-not-a-date", "close": np.inf}])
    with pytest.raises(ValueError, match="trade date"):
        calculate(bench=pd.concat([polluted, bad], ignore_index=True))


def test_return_windows_benchmark_and_relative_returns_use_exact_aligned_dates():
    panel = panel_for(codes=("BK0001",), rates=[0.01])
    result = calculate(panel=panel).iloc[0]
    history = panel.set_index("date")["close"]
    bench = benchmark().set_index("date")["close"]
    for period in (1, 5, 10, 20):
        assert result[f"return_{period}d"] == pytest.approx(history.iloc[-1] / history.iloc[-(period + 1)] - 1)
    for period in (5, 20):
        expected_benchmark = bench.iloc[-1] / bench.iloc[-(period + 1)] - 1
        assert result[f"benchmark_return_{period}d"] == pytest.approx(expected_benchmark)
        assert result[f"relative_return_{period}d"] == pytest.approx(result[f"return_{period}d"] - expected_benchmark)
    assert result["benchmark_code"] == "SH000001"


def test_short_benchmark_allows_short_returns_and_keeps_long_metrics_null():
    dates = DATES[-6:]
    result = calculate(panel=panel_for(dates=dates), bench=benchmark(dates)).iloc[0]
    assert pd.notna(result["return_1d"])
    assert pd.notna(result["return_5d"])
    assert pd.isna(result["return_10d"])
    assert pd.isna(result["return_20d"])
    assert pd.isna(result["benchmark_return_20d"])
    assert pd.isna(result["relative_return_20d"])


@pytest.mark.parametrize("missing_position,missing_metrics", [
    (-6, ("return_5d", "relative_return_5d")),
    (-4, ("return_5d", "relative_return_5d")),
    (-21, ("return_20d", "relative_return_20d")),
])
def test_missing_aligned_sector_date_nulls_only_affected_window(missing_position, missing_metrics):
    panel = panel_for(codes=("BK0001",))
    panel = panel.loc[panel["date"] != DATES[missing_position]]
    result = calculate(panel=panel).iloc[0]
    for metric in missing_metrics:
        assert pd.isna(result[metric])


def test_change_pct_is_not_used_for_returns():
    panel = panel_for()
    panel["change_pct"] = 999999
    pd.testing.assert_frame_equal(calculate(panel=panel), calculate(panel=panel.drop(columns="change_pct")))


def test_amount_ratio_uses_current_over_previous_five_and_excludes_current_from_mean():
    panel = panel_for(codes=("BK0001",))
    result = calculate(panel=panel).iloc[0]
    amounts = panel.set_index("date")["amount"]
    assert result["amount_ratio_5d"] == pytest.approx(amounts.iloc[-1] / amounts.iloc[-6:-1].mean())


@pytest.mark.parametrize("mode", ["current_missing", "history_missing", "missing_date", "zero_mean"])
def test_amount_ratio_is_null_for_incomplete_or_zero_history(mode):
    panel = panel_for(codes=("BK0001",))
    if mode == "current_missing":
        panel.loc[panel["date"] == DATES[-1], "amount"] = np.nan
    elif mode == "history_missing":
        panel.loc[panel["date"] == DATES[-3], "amount"] = np.nan
    elif mode == "missing_date":
        panel = panel.loc[panel["date"] != DATES[-3]]
    else:
        panel.loc[panel["date"].isin(DATES[-6:-1]), "amount"] = 0
    assert pd.isna(calculate(panel=panel).iloc[0]["amount_ratio_5d"])


def test_amount_ratio_current_zero_is_zero_and_missing_does_not_search_earlier():
    panel = panel_for(codes=("BK0001",))
    panel.loc[panel["date"] == DATES[-1], "amount"] = 0
    assert calculate(panel=panel).iloc[0]["amount_ratio_5d"] == 0
    panel.loc[panel["date"] == DATES[-4], "amount"] = np.nan
    assert pd.isna(calculate(panel=panel).iloc[0]["amount_ratio_5d"])


def test_distance_to_20d_high_uses_close_including_current_day():
    panel = panel_for(codes=("BK0001",), rates=[0.01])
    panel["high"] = 999999
    at_high = calculate(panel=panel).iloc[0]
    assert at_high["distance_to_20d_high"] == 0
    panel.loc[panel["date"] == AS_OF, "close"] = 50
    below = calculate(panel=panel).iloc[0]
    expected = 50 / panel.loc[panel["date"].isin(DATES[-20:]), "close"].max() - 1
    assert below["distance_to_20d_high"] == pytest.approx(expected)


def test_distance_to_high_null_when_window_short_or_has_missing_date():
    short_dates = DATES[-19:]
    assert pd.isna(calculate(panel=panel_for(dates=short_dates), bench=benchmark(short_dates)).iloc[0]["distance_to_20d_high"])
    panel = panel_for(codes=("BK0001",))
    panel = panel.loc[panel["date"] != DATES[-10]]
    assert pd.isna(calculate(panel=panel).iloc[0]["distance_to_20d_high"])


@pytest.mark.parametrize("level", [1, 2, 3])
def test_rank_is_isolated_to_requested_level(level):
    requested = panel_for(level=level)
    other_level = 2 if level != 2 else 3
    combined = pd.concat([requested, panel_for(codes=("BK0099",), level=other_level, rates=[9])], ignore_index=True)
    result = calculate(panel=combined, sector_level=level)
    assert set(result["sector_level"]) == {level}
    assert "BK0099" not in set(result["sector_code"])


def test_rank_descending_min_ties_count_and_stable_code_order():
    panel = panel_for(codes=("BK0004", "BK0002", "BK0003", "BK0001"), rates=[0, 0, 0, 0])
    targets = {"BK0001": 140, "BK0002": 130, "BK0003": 130, "BK0004": 120}
    for code, close in targets.items():
        panel.loc[(panel["sector_code"] == code) & (panel["date"] == AS_OF), "close"] = close
    result = calculate(panel=panel)
    assert result["sector_code"].tolist() == ["BK0001", "BK0002", "BK0003", "BK0004"]
    assert result["sector_rank_5d"].tolist() == [1, 2, 2, 4]
    assert result["sector_count_5d"].tolist() == [4, 4, 4, 4]


def test_missing_return_rank_is_null_and_count_is_written_to_all_rows():
    panel = panel_for()
    panel = panel.loc[~((panel["sector_code"] == "BK0002") & (panel["date"] == DATES[-3]))]
    result = calculate(panel=panel)
    missing = result[result["sector_code"] == "BK0002"].iloc[0]
    assert pd.isna(missing["sector_rank_5d"])
    assert result["sector_count_5d"].tolist() == [1, 1]
    assert result.iloc[-1]["sector_code"] == "BK0002"


def test_no_valid_returns_has_zero_count_and_all_null_ranks():
    panel = panel_for()
    panel = panel.loc[panel["date"] != DATES[-3]]
    result = calculate(panel=panel)
    assert result["sector_rank_5d"].isna().all()
    assert result["sector_count_5d"].tolist() == [0, 0]


def test_active_only_filter_and_empty_active_result():
    active = panel_for(codes=("BK0001",), active=True)
    inactive = panel_for(codes=("BK0002",), active=False)
    combined = pd.concat([active, inactive], ignore_index=True)
    assert calculate(panel=combined)["sector_code"].tolist() == ["BK0001"]
    assert set(calculate(panel=combined, active_only=False)["sector_code"]) == {"BK0001", "BK0002"}
    empty = calculate(panel=inactive)
    assert empty.empty
    assert empty.columns.tolist() == list(SECTOR_STRENGTH_COLUMNS)
    assert str(empty["sector_rank_5d"].dtype) == "Int64"


def _database_fixture(database_path: Path):
    definitions = [
        SectorDefinition(EASTMONEY_INDUSTRY_SECTOR_TYPE, 1, "BK0001", "One", EASTMONEY_INDUSTRY_REGISTRY_SOURCE),
        SectorDefinition(EASTMONEY_INDUSTRY_SECTOR_TYPE, 2, "BK0002", "Two", EASTMONEY_INDUSTRY_REGISTRY_SOURCE),
    ]
    save_sector_registry_snapshot(definitions, database_path=database_path)
    for definition in definitions:
        facts = panel_for(codes=(definition.sector_code,), level=definition.sector_level)
        facts = facts.assign(
            open=facts["close"], high=facts["close"] + 1, low=facts["close"] - 1,
            volume=100, change_pct=0,
        )[["date", "open", "high", "low", "close", "volume", "amount", "change_pct"]]
        save_sector_daily_kline(definition, facts, database_path=database_path)
    index = benchmark().assign(
        open=lambda value: value["close"], high=lambda value: value["close"] + 1,
        low=lambda value: value["close"] - 1, volume=100, amount=1000,
    )[["date", "open", "high", "low", "close", "volume", "amount"]]
    save_index_daily_kline("SH000001", index, database_path=database_path)
    return definitions


def test_database_orchestration_reads_level_and_does_not_modify_facts_or_schema(tmp_path: Path):
    database_path = tmp_path / "test.db"
    _database_fixture(database_path)
    before = load_sector_daily_panel(database_path=database_path, active_only=False)
    with sqlite3.connect(database_path) as connection:
        tables_before = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    result = load_sector_strength_snapshot(database_path=database_path, sector_level=1, as_of_date=AS_OF)
    assert result["sector_code"].tolist() == ["BK0001"]
    after = load_sector_daily_panel(database_path=database_path, active_only=False)
    pd.testing.assert_frame_equal(before, after)
    with sqlite3.connect(database_path) as connection:
        tables_after = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    assert tables_after == tables_before
    assert not any("strength" in name for (name,) in tables_after)


def test_database_orchestration_applies_active_filter_in_pure_function(tmp_path: Path):
    database_path = tmp_path / "test.db"
    definitions = _database_fixture(database_path)
    save_sector_registry_snapshot([definitions[1]], database_path=database_path)
    assert load_sector_strength_snapshot(database_path=database_path, sector_level=1, as_of_date=AS_OF).empty
    inactive = load_sector_strength_snapshot(
        database_path=database_path, sector_level=1, as_of_date=AS_OF, active_only=False
    )
    assert inactive["sector_code"].tolist() == ["BK0001"]
