import pandas as pd
import pytest

from src.data.index import (
    INDEX_KLINE_COLUMNS,
    INDEX_REGISTRY,
    get_index_definition,
    normalize_index_daily_kline,
)


def valid_data():
    return pd.DataFrame([
        {"date": "2026-07-17", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "amount": "--", "extra": 1},
        {"date": "2026-07-16", "open": 9, "high": 10, "low": 8, "close": 9.5, "volume": 90, "amount": 1000},
    ])


def test_registry_contains_only_first_four_indexes():
    assert set(INDEX_REGISTRY) == {"SH000001", "SZ399001", "SZ399006", "SH000688"}
    assert get_index_definition("SH000001").eastmoney_secid == "1.000001"
    assert get_index_definition("SZ399001").eastmoney_secid == "0.399001"
    assert get_index_definition("SZ399006").eastmoney_secid == "0.399006"
    assert get_index_definition("SH000688").eastmoney_secid == "1.000688"


@pytest.mark.parametrize("code", ["sh000001", "SH000300", "", None, 1])
def test_rejects_unknown_index_codes(code):
    with pytest.raises(ValueError, match="Unsupported index code"):
        get_index_definition(code)


def test_registry_definition_is_immutable():
    definition = get_index_definition("SH000001")
    with pytest.raises((AttributeError, TypeError)):
        definition.name = "changed"


def test_normalizer_selects_columns_sorts_and_preserves_missing_amount():
    source = valid_data()
    result = normalize_index_daily_kline(source)
    assert result.columns.tolist() == list(INDEX_KLINE_COLUMNS)
    assert result["date"].tolist() == ["2026-07-16", "2026-07-17"]
    assert pd.isna(result.iloc[1]["amount"])
    assert "extra" in source.columns


@pytest.mark.parametrize("column,value", [
    ("date", "2026/07/17"), ("open", 0), ("close", float("inf")),
    ("volume", 1.5), ("amount", "invalid"),
])
def test_normalizer_rejects_invalid_fields(column, value):
    source = valid_data()
    source[column] = source[column].astype("object")
    source.loc[0, column] = value
    with pytest.raises(ValueError):
        normalize_index_daily_kline(source)


def test_normalizer_rejects_duplicate_dates_and_bad_price_relationship():
    duplicate = valid_data()
    duplicate.loc[1, "date"] = duplicate.loc[0, "date"]
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_index_daily_kline(duplicate)
    bad = valid_data()
    bad.loc[0, "high"] = 8
    with pytest.raises(ValueError, match="high"):
        normalize_index_daily_kline(bad)


def test_normalizer_does_not_modify_input():
    source = valid_data()
    original = source.copy(deep=True)
    normalize_index_daily_kline(source)
    pd.testing.assert_frame_equal(source, original)
