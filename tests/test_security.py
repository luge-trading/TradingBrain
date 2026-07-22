"""Offline tests for strict security identity and listing fact normalization."""

from __future__ import annotations

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.data.security import (
    SECURITY_LISTING_EVENT_COLUMNS,
    SECURITY_MASTER_COLUMNS,
    normalize_security_listing_events,
    normalize_security_master,
)


def master_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "local_symbol": "600000",
                "exchange": "XSHG",
                "asset_type": "COMMON_STOCK",
                "board": "SSE_MAIN",
                "current_name": "浦发银行",
                "list_date": "1999-11-10",
                "delist_date": None,
                "current_listing_status": "LISTED",
                "source": "SSE_OFFICIAL",
                "source_as_of_date": "2026-07-22",
            },
            {
                "local_symbol": "688001",
                "exchange": "XSHG",
                "asset_type": "COMMON_STOCK",
                "board": "SSE_STAR",
                "current_name": "华兴源创",
                "list_date": "2019-07-22",
                "delist_date": None,
                "current_listing_status": "LISTED",
                "source": "SSE_OFFICIAL",
                "source_as_of_date": "2026-07-22",
            },
            {
                "local_symbol": "000001",
                "exchange": "XSHE",
                "asset_type": "COMMON_STOCK",
                "board": "SZSE_MAIN",
                "current_name": "平安银行",
                "list_date": "1991-04-03",
                "delist_date": None,
                "current_listing_status": "LISTED",
                "source": "SZSE_OFFICIAL",
                "source_as_of_date": "2026-07-22",
            },
            {
                "local_symbol": "300001",
                "exchange": "XSHE",
                "asset_type": "COMMON_STOCK",
                "board": "SZSE_CHINEXT",
                "current_name": "特锐德",
                "list_date": "2009-10-30",
                "delist_date": None,
                "current_listing_status": "LISTED",
                "source": "SZSE_OFFICIAL",
                "source_as_of_date": "2026-07-22",
            },
        ]
    )


def test_normalize_security_master_accepts_four_frozen_market_combinations():
    result = normalize_security_master(master_rows().sample(frac=1, random_state=7))
    assert result.columns.tolist() == list(SECURITY_MASTER_COLUMNS)
    assert list(zip(result["exchange"], result["local_symbol"])) == sorted(
        zip(result["exchange"], result["local_symbol"])
    )
    assert set(zip(result["exchange"], result["board"])) == {
        ("XSHG", "SSE_MAIN"),
        ("XSHG", "SSE_STAR"),
        ("XSHE", "SZSE_MAIN"),
        ("XSHE", "SZSE_CHINEXT"),
    }


@pytest.mark.parametrize(
    "symbol",
    [
        "60000",
        "6000000",
        "６０００００",
        "٦٠٠٠٠٠",
        "60000A",
        "60000 ",
        600000,
        True,
        None,
    ],
)
def test_normalize_security_master_rejects_invalid_local_symbol(symbol):
    data = master_rows().iloc[[0]].copy()
    data["local_symbol"] = pd.Series([symbol], dtype="object")
    with pytest.raises(ValueError, match="local_symbol"):
        normalize_security_master(data)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("exchange", "XBSE", "exchange"),
        ("asset_type", "ETF", "asset_type"),
        ("board", "BSE_MAIN", "board"),
    ],
)
def test_normalize_security_master_rejects_invalid_enums(field, value, message):
    data = master_rows().iloc[[0]].copy()
    data.loc[:, field] = value
    with pytest.raises(ValueError, match=message):
        normalize_security_master(data)


@pytest.mark.parametrize(
    ("exchange", "board"),
    [("XSHG", "SZSE_MAIN"), ("XSHE", "SSE_STAR")],
)
def test_normalize_security_master_rejects_exchange_board_mismatch(exchange, board):
    data = master_rows().iloc[[0]].copy()
    data.loc[:, "exchange"] = exchange
    data.loc[:, "board"] = board
    with pytest.raises(ValueError, match="exchange/board"):
        normalize_security_master(data)


@pytest.mark.parametrize("name", ["", "   ", None])
def test_normalize_security_master_rejects_empty_name(name):
    data = master_rows().iloc[[0]].copy()
    data.loc[:, "current_name"] = name
    with pytest.raises(ValueError, match="current_name"):
        normalize_security_master(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("list_date", "2026-02-30"),
        ("list_date", "20260722"),
        ("source_as_of_date", "22-07-2026"),
        ("delist_date", "invalid"),
    ],
)
def test_normalize_security_master_rejects_invalid_dates(field, value):
    data = master_rows().iloc[[0]].copy()
    data.loc[:, field] = value
    if field == "delist_date":
        data.loc[:, "current_listing_status"] = "DELISTED"
    with pytest.raises(ValueError, match=field):
        normalize_security_master(data)


def test_normalize_security_master_rejects_delist_before_list():
    data = master_rows().iloc[[0]].copy()
    data.loc[:, "current_listing_status"] = "DELISTED"
    data.loc[:, "delist_date"] = "1990-01-01"
    with pytest.raises(ValueError, match="precedes"):
        normalize_security_master(data)


def test_normalize_security_master_rejects_listed_with_delist_date():
    data = master_rows().iloc[[0]].copy()
    data.loc[:, "delist_date"] = "2026-07-22"
    with pytest.raises(ValueError, match="LISTED"):
        normalize_security_master(data)


def test_normalize_security_master_rejects_delisted_without_delist_date():
    data = master_rows().iloc[[0]].copy()
    data.loc[:, "current_listing_status"] = "DELISTED"
    with pytest.raises(ValueError, match="DELISTED"):
        normalize_security_master(data)


def test_normalize_security_master_rejects_duplicate_natural_key():
    data = pd.concat([master_rows().iloc[[0]], master_rows().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate security natural key"):
        normalize_security_master(data)


def test_normalize_security_master_does_not_mutate_input_and_trims_text():
    data = master_rows().iloc[[0]].copy()
    data.loc[:, "current_name"] = "  浦发银行  "
    before = data.copy(deep=True)
    result = normalize_security_master(data)
    assert_frame_equal(data, before)
    assert result.loc[0, "current_name"] == "浦发银行"


@pytest.mark.parametrize(
    ("frame_kind", "column"),
    [
        ("master", "source_as_of_date"),
        ("master", "local_symbol"),
        ("master", "extra"),
        ("event", "local_symbol"),
        ("event", "extra"),
    ],
)
def test_security_normalizers_reject_any_duplicate_column_without_mutation(
    frame_kind: str,
    column: str,
):
    data = master_rows().iloc[[0]].copy() if frame_kind == "master" else listing_events()
    if column == "extra":
        data.insert(len(data.columns), "extra", ["first"])
        data.insert(len(data.columns), "extra", ["second"], allow_duplicates=True)
    else:
        data.insert(len(data.columns), column, ["conflict"], allow_duplicates=True)
    before = data.copy(deep=True)
    normalizer = (
        normalize_security_master
        if frame_kind == "master"
        else normalize_security_listing_events
    )
    with pytest.raises(ValueError, match="duplicate columns"):
        normalizer(data)
    assert_frame_equal(data, before)


def test_normalize_security_master_empty_has_fixed_columns():
    result = normalize_security_master(pd.DataFrame(columns=SECURITY_MASTER_COLUMNS))
    assert result.empty
    assert result.columns.tolist() == list(SECURITY_MASTER_COLUMNS)


def listing_events() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "local_symbol": "600000",
                "exchange": "XSHG",
                "asset_type": "COMMON_STOCK",
                "event_type": "LISTED",
                "event_date": "1999-11-10",
                "source": "SSE_OFFICIAL",
            }
        ]
    )


def test_normalize_security_listing_events_fixed_columns_sort_and_input_unchanged():
    data = listing_events()
    before = data.copy(deep=True)
    result = normalize_security_listing_events(data)
    assert_frame_equal(data, before)
    assert result.columns.tolist() == list(SECURITY_LISTING_EVENT_COLUMNS)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("local_symbol", "60000X"),
        ("local_symbol", "６０００００"),
        ("local_symbol", "٦٠٠٠٠٠"),
        ("exchange", "XBSE"),
        ("asset_type", "ETF"),
        ("event_type", "RELISTED"),
        ("event_date", "19991110"),
        ("source", ""),
    ],
)
def test_normalize_security_listing_events_rejects_invalid_facts(field, value):
    data = listing_events()
    data.loc[:, field] = value
    with pytest.raises(ValueError, match=field):
        normalize_security_listing_events(data)


def test_normalize_security_listing_events_rejects_duplicate_event_key():
    data = pd.concat([listing_events(), listing_events()], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate security listing event key"):
        normalize_security_listing_events(data)
