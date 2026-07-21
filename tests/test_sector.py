from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from src.data.sector import (
    EASTMONEY_INDUSTRY_LEVELS,
    EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    EASTMONEY_INDUSTRY_SECTOR_TYPE,
    SECTOR_KLINE_COLUMNS,
    SectorDefinition,
    normalize_sector_daily_kline,
    normalize_sector_registry,
)


def definition(level=1, code="BK0001", name="Industry"):
    return SectorDefinition(
        EASTMONEY_INDUSTRY_SECTOR_TYPE,
        level,
        code,
        name,
        EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    )


def valid_kline():
    return pd.DataFrame([
        {"date": "2026-07-18", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "amount": 1000, "change_pct": 1.2, "extra": 1},
        {"date": "2026-07-17", "open": 9, "high": 10, "low": 8, "close": 9.5, "volume": "--", "amount": None, "change_pct": pd.NA, "extra": 2},
    ])


def test_sector_constants_and_definition_immutability():
    assert EASTMONEY_INDUSTRY_LEVELS == (1, 2, 3)
    item = definition()
    with pytest.raises(FrozenInstanceError):
        item.sector_name = "changed"


@pytest.mark.parametrize("kwargs", [
    {"sector_type": "OTHER"}, {"sector_level": True}, {"sector_level": 0},
    {"sector_level": 4}, {"sector_level": 1.0}, {"sector_code": "BK123"},
    {"sector_code": "bk1234"}, {"sector_name": " "}, {"source": ""},
])
def test_sector_definition_rejects_invalid_fields(kwargs):
    values = {
        "sector_type": EASTMONEY_INDUSTRY_SECTOR_TYPE,
        "sector_level": 1,
        "sector_code": "BK0001",
        "sector_name": "Industry",
        "source": EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        SectorDefinition(**values)


def test_sector_definition_strips_name_and_source():
    item = SectorDefinition(EASTMONEY_INDUSTRY_SECTOR_TYPE, 1, "BK0001", " Name ", " source ")
    assert (item.sector_name, item.source) == ("Name", "source")


def test_registry_normalization_sorts_without_mutation_and_allows_duplicate_names():
    source = [definition(3, "BK0003", "Same"), definition(1, "BK0001", "Same"), definition(2, "BK0002")]
    result = normalize_sector_registry(source)
    assert [(item.sector_level, item.sector_code) for item in result] == [(1, "BK0001"), (2, "BK0002"), (3, "BK0003")]
    assert source[0].sector_code == "BK0003"


def test_registry_rejects_empty_non_definition_duplicate_and_cross_level_code():
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_sector_registry([])
    with pytest.raises(TypeError, match="SectorDefinition"):
        normalize_sector_registry([object()])
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_sector_registry([definition(), definition()])
    with pytest.raises(ValueError, match="multiple levels"):
        normalize_sector_registry([definition(1, "BK0001"), definition(2, "BK0001")])


def test_kline_normalizer_selects_columns_sorts_preserves_input_and_missing_values():
    source = valid_kline()
    original = source.copy(deep=True)
    result = normalize_sector_daily_kline(source)
    assert result.columns.tolist() == list(SECTOR_KLINE_COLUMNS)
    assert result["date"].tolist() == ["2026-07-17", "2026-07-18"]
    assert str(result["volume"].dtype) == "Int64"
    assert pd.isna(result.iloc[0]["volume"])
    assert pd.isna(result.iloc[0]["amount"])
    assert pd.isna(result.iloc[0]["change_pct"])
    pd.testing.assert_frame_equal(source, original)


def test_empty_kline_returns_standard_nullable_columns():
    result = normalize_sector_daily_kline(pd.DataFrame(columns=SECTOR_KLINE_COLUMNS))
    assert result.empty
    assert result.columns.tolist() == list(SECTOR_KLINE_COLUMNS)
    assert str(result["volume"].dtype) == "Int64"


@pytest.mark.parametrize("column,value", [
    ("date", "2026/07/18"), ("open", 0), ("close", float("inf")),
    ("volume", -1), ("volume", 1.5), ("volume", float("inf")),
    ("amount", -1), ("amount", float("inf")), ("change_pct", float("inf")),
])
def test_kline_normalizer_rejects_invalid_fields(column, value):
    data = valid_kline().iloc[[0]].copy()
    data[column] = data[column].astype("object")
    data.loc[data.index[0], column] = value
    with pytest.raises(ValueError):
        normalize_sector_daily_kline(data)


def test_kline_normalizer_rejects_missing_columns_duplicate_dates_and_bad_ohlc():
    with pytest.raises(ValueError, match="missing columns"):
        normalize_sector_daily_kline(pd.DataFrame([{"date": "2026-07-18"}]))
    duplicate = valid_kline()
    duplicate.loc[1, "date"] = duplicate.loc[0, "date"]
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_sector_daily_kline(duplicate)
    bad_high = valid_kline().iloc[[0]].copy()
    bad_high.loc[bad_high.index[0], "high"] = 8
    with pytest.raises(ValueError, match="high"):
        normalize_sector_daily_kline(bad_high)
    bad_low = valid_kline().iloc[[0]].copy()
    bad_low.loc[bad_low.index[0], "high"] = 14
    bad_low.loc[bad_low.index[0], "low"] = 13
    with pytest.raises(ValueError, match="low"):
        normalize_sector_daily_kline(bad_low)


@pytest.mark.parametrize("marker", [None, pd.NA, float("nan"), "", "-", "--"])
def test_nullable_fields_recognize_all_missing_markers(marker):
    data = valid_kline().iloc[[0]].copy()
    for column in ("volume", "amount", "change_pct"):
        data[column] = data[column].astype("object")
        data.loc[data.index[0], column] = marker
    result = normalize_sector_daily_kline(data)
    assert pd.isna(result.iloc[0]["volume"])
    assert pd.isna(result.iloc[0]["amount"])
    assert pd.isna(result.iloc[0]["change_pct"])
