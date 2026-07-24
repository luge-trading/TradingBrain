"""Offline tests for strict, versioned stock daily prices."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.data.database import (
    LATEST_STOCK_DAILY_PRICE_DATE_COLUMNS,
    STOCK_DAILY_PRICE_RESULT_COLUMNS,
    STOCK_DAILY_PRICE_REVISION_RESULT_COLUMNS,
    load_latest_stock_daily_price_dates,
    load_security_master,
    load_stock_daily_price_revisions,
    load_stock_daily_prices,
    save_security_master,
    save_stock_daily_prices,
)
from src.data.price import (
    PriceProviderError,
    PriceProviderErrorCode,
    STOCK_DAILY_PRICE_COLUMNS,
    StockDailyPriceSaveResult,
    normalize_stock_daily_prices,
)


def master_frame(
    *,
    symbol: str = "600000",
    exchange: str = "XSHG",
    board: str = "SSE_MAIN",
    name: str = "浦发银行",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "local_symbol": symbol,
                "exchange": exchange,
                "asset_type": "COMMON_STOCK",
                "board": board,
                "current_name": name,
                "list_date": "1999-11-10",
                "delist_date": None,
                "current_listing_status": "LISTED",
                "source": "OFFICIAL",
                "source_as_of_date": "2026-07-22",
            }
        ]
    )


def seed_security(database_path: Path, **kwargs) -> int:
    save_security_master(master_frame(**kwargs), database_path=database_path)
    result = load_security_master(database_path=database_path)
    symbol = kwargs.get("symbol", "600000")
    exchange = kwargs.get("exchange", "XSHG")
    row = result.loc[
        (result["local_symbol"] == symbol) & (result["exchange"] == exchange)
    ].iloc[0]
    return int(row["security_id"])


def price_frame(
    security_id: int = 1,
    *,
    trade_date: str = "2026-07-21",
    adjustment: str = "QFQ",
    source: str = "EASTMONEY",
    observed_at: str = "2026-07-21T16:00:00+08:00",
    **changes,
) -> pd.DataFrame:
    row = {
        "security_id": security_id,
        "trade_date": trade_date,
        "adjustment": adjustment,
        "source": source,
        "provider_adjustment": "fqt=1",
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 1000,
        "volume_unit": "PROVIDER_NATIVE",
        "amount": 10000.0,
        "amount_unit": "PROVIDER_NATIVE",
        "is_final": True,
        "provider_as_of_date": "2026-07-21",
        "observed_at": observed_at,
    }
    row.update(changes)
    return pd.DataFrame([row], columns=STOCK_DAILY_PRICE_COLUMNS)


@pytest.mark.parametrize("adjustment", ["UNADJUSTED", "QFQ", "HFQ"])
def test_normalizer_accepts_frozen_adjustments(adjustment: str):
    result = normalize_stock_daily_prices(price_frame(adjustment=adjustment))
    assert result.loc[0, "adjustment"] == adjustment


@pytest.mark.parametrize("adjustment", ["", "NONE", "RAW", "qfq", None])
def test_normalizer_rejects_invalid_adjustment(adjustment):
    with pytest.raises(ValueError, match="adjustment"):
        normalize_stock_daily_prices(price_frame(adjustment=adjustment))


@pytest.mark.parametrize("field", ["source", "provider_adjustment"])
@pytest.mark.parametrize("value", ["", "   ", None])
def test_normalizer_rejects_empty_required_text(field: str, value):
    with pytest.raises(ValueError, match=field):
        normalize_stock_daily_prices(price_frame(**{field: value}))


def test_normalizer_rejects_duplicate_columns_without_mutation():
    frame = price_frame()
    frame.insert(len(frame.columns), "source", "OTHER", allow_duplicates=True)
    before = frame.copy(deep=True)
    with pytest.raises(ValueError, match="duplicate columns"):
        normalize_stock_daily_prices(frame)
    assert_frame_equal(frame, before)


def test_normalizer_rejects_missing_column():
    with pytest.raises(ValueError, match="missing columns"):
        normalize_stock_daily_prices(price_frame().drop(columns="close"))


def test_normalizer_ignores_unique_extra_columns_and_does_not_mutate_input():
    frame = price_frame().assign(extra="ignored")
    before = frame.copy(deep=True)
    result = normalize_stock_daily_prices(frame)
    assert_frame_equal(frame, before)
    assert result.columns.tolist() == list(STOCK_DAILY_PRICE_COLUMNS)
    assert "extra" not in result


def test_normalizer_rejects_duplicate_fact_key():
    frame = pd.concat([price_frame(), price_frame()], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate stock daily price key"):
        normalize_stock_daily_prices(frame)


def test_normalizer_sorts_by_frozen_key():
    frame = pd.concat(
        [
            price_frame(2, trade_date="2026-07-22"),
            price_frame(1, trade_date="2026-07-22", source="TUSHARE"),
            price_frame(1, trade_date="2026-07-21"),
        ],
        ignore_index=True,
    )
    result = normalize_stock_daily_prices(frame)
    assert list(zip(result.security_id, result.trade_date, result.source)) == [
        (1, "2026-07-21", "EASTMONEY"),
        (1, "2026-07-22", "TUSHARE"),
        (2, "2026-07-22", "EASTMONEY"),
    ]


@pytest.mark.parametrize("security_id", [0, -1, True, 1.0, "1", None])
def test_normalizer_rejects_invalid_security_id(security_id):
    with pytest.raises(ValueError, match="security_id"):
        normalize_stock_daily_prices(price_frame(security_id))


@pytest.mark.parametrize("value", ["2026-02-30", "20260721", "21-07-2026", None])
def test_normalizer_rejects_invalid_trade_date(value):
    with pytest.raises(ValueError, match="trade_date"):
        normalize_stock_daily_prices(price_frame(trade_date=value))


@pytest.mark.parametrize("value", ["invalid", "2026-07-21T16:00:00", None])
def test_normalizer_rejects_invalid_or_naive_observed_at(value):
    with pytest.raises(ValueError, match="observed_at"):
        normalize_stock_daily_prices(price_frame(observed_at=value))


def test_normalizer_converts_observed_at_to_utc():
    result = normalize_stock_daily_prices(price_frame())
    assert result.loc[0, "observed_at"] == "2026-07-21T08:00:00+00:00"


@pytest.mark.parametrize("value", ["2026-02-30", "20260721", 20260721])
def test_normalizer_rejects_invalid_provider_as_of_date(value):
    with pytest.raises(ValueError, match="provider_as_of_date"):
        normalize_stock_daily_prices(price_frame(provider_as_of_date=value))


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
@pytest.mark.parametrize("value", [0, -1, np.nan, np.inf, -np.inf, True, "10"])
def test_normalizer_rejects_invalid_prices(field: str, value):
    with pytest.raises(ValueError, match=field):
        normalize_stock_daily_prices(price_frame(**{field: value}))


@pytest.mark.parametrize(
    "changes",
    [
        {"open": 13.0},
        {"open": 8.0},
        {"close": 13.0},
        {"close": 8.0},
        {"low": 12.0},
        {"high": 8.0},
    ],
)
def test_normalizer_rejects_invalid_ohlc_relationship(changes):
    with pytest.raises(ValueError, match="OHLC relationship"):
        normalize_stock_daily_prices(price_frame(**changes))


@pytest.mark.parametrize("volume", [-1, 1.0, True, np.nan, "100"])
def test_normalizer_rejects_invalid_volume(volume):
    with pytest.raises(ValueError, match="volume"):
        normalize_stock_daily_prices(price_frame(volume=volume))


def test_price_provider_error_codes_are_complete_and_stable():
    assert {item.value for item in PriceProviderErrorCode} == {
        "NETWORK_CONFIGURATION",
        "PROXY_UNAVAILABLE",
        "DNS_FAILURE",
        "CONNECTION_CLOSED",
        "TIMEOUT",
        "HTTP_RETRYABLE",
        "HTTP_FINAL",
        "PROVIDER_REJECTED",
        "INVALID_JSON",
        "INVALID_SCHEMA",
        "IDENTITY_MISMATCH",
        "INVALID_DATA",
        "NO_DATA",
    }


def test_price_provider_error_exposes_machine_readable_safe_attributes():
    error = PriceProviderError(
        "safe provider failure",
        provider="EASTMONEY",
        code=PriceProviderErrorCode.TIMEOUT,
        retryable=True,
        batch_signal=False,
        attempts=2,
    )
    assert str(error) == "safe provider failure"
    assert error.provider == "EASTMONEY"
    assert error.code is PriceProviderErrorCode.TIMEOUT
    assert error.retryable
    assert not error.batch_signal
    assert error.attempts == 2
    assert error.status_code is None
    assert "password" not in str(error).lower()
    assert "proxy" not in str(error).lower()


@pytest.mark.parametrize("unit", ["", "HAND", "share", None])
def test_normalizer_rejects_invalid_volume_unit(unit):
    with pytest.raises(ValueError, match="volume_unit"):
        normalize_stock_daily_prices(price_frame(volume_unit=unit))


@pytest.mark.parametrize("unit", ["PROVIDER_NATIVE", "SHARE", "LOT"])
def test_normalizer_accepts_frozen_volume_units(unit: str):
    assert normalize_stock_daily_prices(price_frame(volume_unit=unit)).loc[
        0, "volume_unit"
    ] == unit


def test_normalizer_accepts_null_amount_and_unit():
    result = normalize_stock_daily_prices(price_frame(amount=None, amount_unit=None))
    assert result.loc[0, "amount"] is None
    assert result.loc[0, "amount_unit"] is None


def test_normalizer_rejects_amount_unit_pairing_errors():
    with pytest.raises(ValueError, match="amount_unit"):
        normalize_stock_daily_prices(price_frame(amount=None, amount_unit="CNY"))
    with pytest.raises(ValueError, match="amount_unit"):
        normalize_stock_daily_prices(price_frame(amount=1.0, amount_unit=None))


@pytest.mark.parametrize("amount", [-1, np.inf, -np.inf, True, "1"])
def test_normalizer_rejects_invalid_amount(amount):
    with pytest.raises(ValueError, match="amount"):
        normalize_stock_daily_prices(price_frame(amount=amount))


@pytest.mark.parametrize("unit", ["", "RMB", "cny"])
def test_normalizer_rejects_invalid_amount_unit(unit):
    with pytest.raises(ValueError, match="amount_unit"):
        normalize_stock_daily_prices(price_frame(amount_unit=unit))


@pytest.mark.parametrize("unit", ["PROVIDER_NATIVE", "CNY"])
def test_normalizer_accepts_frozen_amount_units(unit: str):
    assert normalize_stock_daily_prices(price_frame(amount_unit=unit)).loc[
        0, "amount_unit"
    ] == unit


def test_normalizer_accepts_null_provider_as_of_date_and_false_finality():
    result = normalize_stock_daily_prices(
        price_frame(provider_as_of_date=None, is_final=False)
    )
    assert result.loc[0, "provider_as_of_date"] is None
    assert not result.loc[0, "is_final"]


def test_normalizer_preserves_none_scalars_in_mixed_nullable_batch():
    frame = pd.concat(
        [
            price_frame(
                1,
                amount=None,
                amount_unit=None,
                provider_as_of_date=None,
            ),
            price_frame(
                2,
                trade_date="2026-07-22",
                amount=0.0,
                amount_unit="CNY",
                provider_as_of_date="2026-07-22",
            ),
        ],
        ignore_index=True,
    )
    before = frame.copy(deep=True)
    result = normalize_stock_daily_prices(frame)
    assert_frame_equal(frame, before)
    assert result.columns.tolist() == list(STOCK_DAILY_PRICE_COLUMNS)
    assert result["security_id"].tolist() == [1, 2]
    assert result.loc[0, "amount"] is None
    assert result.loc[0, "amount_unit"] is None
    assert result.loc[0, "provider_as_of_date"] is None
    assert result.loc[1, "amount"] == 0
    assert result.loc[1, "amount_unit"] == "CNY"
    assert result.loc[1, "provider_as_of_date"] == "2026-07-22"


@pytest.mark.parametrize("is_final", [0, 1, "true", None])
def test_normalizer_rejects_non_boolean_is_final(is_final):
    with pytest.raises(ValueError, match="is_final"):
        normalize_stock_daily_prices(price_frame(is_final=is_final))


def test_normalizer_returns_fixed_empty_structure():
    result = normalize_stock_daily_prices(pd.DataFrame(columns=STOCK_DAILY_PRICE_COLUMNS))
    assert result.empty
    assert result.columns.tolist() == list(STOCK_DAILY_PRICE_COLUMNS)


def test_mixed_nullable_batch_is_idempotent_and_advances_watermark(
    tmp_path: Path,
):
    database_path = tmp_path / "test.db"
    first_id = seed_security(database_path)
    second_id = seed_security(
        database_path,
        symbol="000021",
        exchange="XSHE",
        board="SZSE_MAIN",
        name="深科技",
    )
    mixed = pd.concat(
        [
            price_frame(
                first_id,
                amount=None,
                amount_unit=None,
                provider_as_of_date=None,
            ),
            price_frame(
                second_id,
                trade_date="2026-07-22",
                amount=10000.0,
                amount_unit="CNY",
                provider_as_of_date="2026-07-22",
            ),
        ],
        ignore_index=True,
    )
    assert save_stock_daily_prices(mixed, database_path=database_path) == (
        StockDailyPriceSaveResult(2, 0, 0, 0)
    )
    before = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[first_id, second_id],
    )
    assert pd.isna(before.loc[before["security_id"] == first_id, "amount"]).all()
    assert save_stock_daily_prices(
        mixed.copy(deep=True), database_path=database_path
    ) == StockDailyPriceSaveResult(0, 0, 2, 0)
    after_idempotent = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[first_id, second_id],
    )
    assert_frame_equal(before, after_idempotent)
    assert load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[first_id, second_id],
    ).empty

    later_null_fact = price_frame(
        first_id,
        amount=None,
        amount_unit=None,
        provider_as_of_date=None,
        observed_at="2026-07-21T17:00:00+08:00",
    )
    assert save_stock_daily_prices(
        later_null_fact, database_path=database_path
    ) == StockDailyPriceSaveResult(0, 0, 1, 0)
    after_watermark = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[first_id],
    )
    first_before = before.loc[before["security_id"] == first_id].reset_index(drop=True)
    assert after_watermark.loc[0, "observed_at"] == "2026-07-21T09:00:00+00:00"
    assert after_watermark.loc[0, "updated_at"] == first_before.loc[0, "updated_at"]
    comparable_columns = [
        column
        for column in STOCK_DAILY_PRICE_RESULT_COLUMNS
        if column
        not in {
            "amount",
            "amount_unit",
            "provider_as_of_date",
            "observed_at",
        }
    ]
    assert_frame_equal(
        first_before.loc[:, comparable_columns],
        after_watermark.loc[:, comparable_columns],
    )
    for column in ("amount", "amount_unit", "provider_as_of_date"):
        assert pd.isna(first_before.loc[0, column])
        assert pd.isna(after_watermark.loc[0, column])
    assert load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[first_id],
    ).empty


def test_nullable_facts_create_bidirectional_revisions(tmp_path: Path):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    save_stock_daily_prices(
        price_frame(
            security_id,
            amount=None,
            amount_unit=None,
            provider_as_of_date=None,
        ),
        database_path=database_path,
    )
    assert save_stock_daily_prices(
        price_frame(
            security_id,
            amount=10000.0,
            amount_unit="CNY",
            provider_as_of_date="2026-07-21",
            observed_at="2026-07-21T17:00:00+08:00",
        ),
        database_path=database_path,
    ) == StockDailyPriceSaveResult(0, 1, 0, 1)
    assert save_stock_daily_prices(
        price_frame(
            security_id,
            amount=None,
            amount_unit=None,
            provider_as_of_date=None,
            observed_at="2026-07-21T18:00:00+08:00",
        ),
        database_path=database_path,
    ) == StockDailyPriceSaveResult(0, 1, 0, 1)
    revisions = load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert revisions["revision_number"].tolist() == [1, 2]
    assert revisions["changed_fields"].tolist() == [
        "amount,amount_unit,provider_as_of_date",
        "amount,amount_unit,provider_as_of_date",
    ]
    assert pd.isna(revisions.loc[0, "old_amount"])
    assert pd.isna(revisions.loc[0, "old_amount_unit"])
    assert pd.isna(revisions.loc[0, "old_provider_as_of_date"])
    assert revisions.loc[0, "new_amount"] == 10000.0
    assert revisions.loc[0, "new_amount_unit"] == "CNY"
    assert revisions.loc[0, "new_provider_as_of_date"] == "2026-07-21"
    assert revisions.loc[1, "old_amount"] == 10000.0
    assert revisions.loc[1, "old_amount_unit"] == "CNY"
    assert revisions.loc[1, "old_provider_as_of_date"] == "2026-07-21"
    assert pd.isna(revisions.loc[1, "new_amount"])
    assert pd.isna(revisions.loc[1, "new_amount_unit"])
    assert pd.isna(revisions.loc[1, "new_provider_as_of_date"])
    current = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert pd.isna(current.loc[0, "amount"])
    assert pd.isna(current.loc[0, "amount_unit"])
    assert pd.isna(current.loc[0, "provider_as_of_date"])


def test_save_tracks_observation_watermark_and_rejects_stale_revision(
    tmp_path: Path,
):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    t1 = price_frame(
        security_id,
        observed_at="2026-07-21T10:00:00+00:00",
    )
    assert save_stock_daily_prices(t1, database_path=database_path) == (
        StockDailyPriceSaveResult(1, 0, 0, 0)
    )
    after_t1 = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    t2 = price_frame(
        security_id,
        observed_at="2026-07-21T12:00:00+00:00",
    )
    assert save_stock_daily_prices(t2, database_path=database_path) == (
        StockDailyPriceSaveResult(0, 0, 1, 0)
    )
    after_t2 = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert after_t2.loc[0, "observed_at"] == "2026-07-21T12:00:00+00:00"
    assert after_t2.loc[0, "updated_at"] == after_t1.loc[0, "updated_at"]
    assert load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    ).empty

    for observed_at in (
        "2026-07-21T12:00:00+00:00",
        "2026-07-21T11:00:00+00:00",
    ):
        assert save_stock_daily_prices(
            price_frame(security_id, observed_at=observed_at),
            database_path=database_path,
        ) == StockDailyPriceSaveResult(0, 0, 1, 0)
    after_nonadvancing_observations = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert_frame_equal(after_t2, after_nonadvancing_observations)

    with pytest.raises(ValueError, match="observed_at must advance"):
        save_stock_daily_prices(
            price_frame(
                security_id,
                close=11.5,
                observed_at="2026-07-21T11:00:00+00:00",
            ),
            database_path=database_path,
        )
    after_t3 = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert_frame_equal(after_t2, after_t3)
    assert load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    ).empty

    assert save_stock_daily_prices(
        price_frame(
            security_id,
            close=11.5,
            observed_at="2026-07-21T13:00:00+00:00",
        ),
        database_path=database_path,
    ) == StockDailyPriceSaveResult(0, 1, 0, 1)
    after_t4 = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert after_t4.loc[0, "close"] == 11.5
    assert after_t4.loc[0, "observed_at"] == "2026-07-21T13:00:00+00:00"
    revisions = load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert len(revisions) == 1
    assert revisions.loc[0, "old_observed_at"] == "2026-07-21T12:00:00+00:00"
    assert revisions.loc[0, "new_observed_at"] == "2026-07-21T13:00:00+00:00"


def test_save_isolates_adjustment_and_source(tmp_path: Path):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    frames = pd.concat(
        [
            price_frame(security_id, adjustment="QFQ", source="EASTMONEY"),
            price_frame(
                security_id,
                adjustment="UNADJUSTED",
                source="EASTMONEY",
                provider_adjustment="fqt=0",
            ),
            price_frame(security_id, adjustment="QFQ", source="TUSHARE"),
        ],
        ignore_index=True,
    )
    result = save_stock_daily_prices(frames, database_path=database_path)
    assert result.inserted == 3
    filters = (
        ("QFQ", "EASTMONEY"),
        ("UNADJUSTED", "EASTMONEY"),
        ("QFQ", "TUSHARE"),
    )
    before = {
        key: load_stock_daily_prices(
            database_path=database_path,
            adjustment=key[0],
            source=key[1],
            security_ids=[security_id],
        )
        for key in filters
    }
    assert all(len(frame) == 1 for frame in before.values())

    assert save_stock_daily_prices(
        price_frame(
            security_id,
            adjustment="QFQ",
            source="EASTMONEY",
            close=11.5,
            observed_at="2026-07-21T17:00:00+08:00",
        ),
        database_path=database_path,
    ) == StockDailyPriceSaveResult(0, 1, 0, 1)
    after = {
        key: load_stock_daily_prices(
            database_path=database_path,
            adjustment=key[0],
            source=key[1],
            security_ids=[security_id],
        )
        for key in filters
    }
    assert after[("QFQ", "EASTMONEY")].loc[0, "close"] == 11.5
    assert_frame_equal(
        before[("UNADJUSTED", "EASTMONEY")],
        after[("UNADJUSTED", "EASTMONEY")],
    )
    assert_frame_equal(before[("QFQ", "TUSHARE")], after[("QFQ", "TUSHARE")])
    assert len(load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )) == 1
    for adjustment, source in filters[1:]:
        assert load_stock_daily_price_revisions(
            database_path=database_path,
            adjustment=adjustment,
            source=source,
            security_ids=[security_id],
        ).empty


def test_save_revision_records_old_new_values_and_stable_fields(tmp_path: Path):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    original = price_frame(security_id)
    save_stock_daily_prices(original, database_path=database_path)
    before = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    revised = price_frame(
        security_id,
        observed_at="2026-07-21T17:00:00+08:00",
        close=11.5,
        is_final=False,
        provider_as_of_date="2026-07-22",
    )
    result = save_stock_daily_prices(revised, database_path=database_path)
    assert result == StockDailyPriceSaveResult(0, 1, 0, 1)
    revisions = load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert revisions.columns.tolist() == list(STOCK_DAILY_PRICE_REVISION_RESULT_COLUMNS)
    assert revisions.loc[0, "revision_number"] == 1
    assert revisions.loc[0, "changed_fields"] == "close,is_final,provider_as_of_date"
    assert revisions.loc[0, "old_close"] == 11.0
    assert revisions.loc[0, "new_close"] == 11.5
    assert revisions.loc[0, "old_is_final"]
    assert not revisions.loc[0, "new_is_final"]
    current = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert current.loc[0, "close"] == 11.5
    assert current.loc[0, "observed_at"] == "2026-07-21T09:00:00+00:00"
    assert current.loc[0, "updated_at"] != before.loc[0, "updated_at"]


def test_provider_as_of_date_only_revision_tracks_exact_changed_field(
    tmp_path: Path,
):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    initial = price_frame(
        security_id,
        trade_date="2026-07-20",
        observed_at="2026-07-20T10:00:00+00:00",
        provider_as_of_date=None,
    )
    assert save_stock_daily_prices(
        initial, database_path=database_path
    ) == StockDailyPriceSaveResult(1, 0, 0, 0)

    revised = initial.copy(deep=True)
    revised.loc[0, "provider_as_of_date"] = "2026-07-20"
    revised.loc[0, "observed_at"] = "2026-07-20T11:00:00+00:00"
    assert save_stock_daily_prices(
        revised, database_path=database_path
    ) == StockDailyPriceSaveResult(0, 1, 0, 1)

    current = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert len(current) == 1
    assert current.loc[0, "provider_as_of_date"] == "2026-07-20"
    assert current.loc[0, "observed_at"] == "2026-07-20T11:00:00+00:00"

    stable_facts = {
        "provider_adjustment": "fqt=1",
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 1000,
        "volume_unit": "PROVIDER_NATIVE",
        "amount": 10000.0,
        "amount_unit": "PROVIDER_NATIVE",
        "is_final": True,
    }
    for field, expected in stable_facts.items():
        assert current.loc[0, field] == expected

    revisions = load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert len(revisions) == 1
    assert revisions.loc[0, "revision_number"] == 1
    assert revisions.loc[0, "changed_fields"] == "provider_as_of_date"
    assert pd.isna(revisions.loc[0, "old_provider_as_of_date"])
    assert revisions.loc[0, "new_provider_as_of_date"] == "2026-07-20"
    assert revisions.loc[0, "old_observed_at"] == "2026-07-20T10:00:00+00:00"
    assert revisions.loc[0, "new_observed_at"] == "2026-07-20T11:00:00+00:00"
    for field, expected in stable_facts.items():
        assert revisions.loc[0, f"old_{field}"] == expected
        assert revisions.loc[0, f"new_{field}"] == expected


def test_revision_number_increments(tmp_path: Path):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    save_stock_daily_prices(price_frame(security_id), database_path=database_path)
    for hour, close in [(17, 11.25), (18, 11.5)]:
        save_stock_daily_prices(
            price_frame(
                security_id,
                close=close,
                observed_at=f"2026-07-21T{hour}:00:00+08:00",
            ),
            database_path=database_path,
        )
    revisions = load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert revisions["revision_number"].tolist() == [1, 2]


@pytest.mark.parametrize(
    "observed_at", ["2026-07-21T15:00:00+08:00", "2026-07-21T16:00:00+08:00"]
)
def test_conflicting_revision_requires_later_observed_at(tmp_path: Path, observed_at: str):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    save_stock_daily_prices(price_frame(security_id), database_path=database_path)
    with pytest.raises(ValueError, match="observed_at must advance"):
        save_stock_daily_prices(
            price_frame(security_id, close=11.5, observed_at=observed_at),
            database_path=database_path,
        )


def test_save_rejects_unknown_security_id(tmp_path: Path):
    with pytest.raises(ValueError, match="Unknown security_id"):
        save_stock_daily_prices(price_frame(999), database_path=tmp_path / "test.db")


def test_batch_failure_rolls_back_prior_insert(tmp_path: Path):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    batch = pd.concat(
        [
            price_frame(
                security_id,
                amount=None,
                amount_unit=None,
                provider_as_of_date=None,
            ),
            price_frame(999, trade_date="2026-07-22"),
        ],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="Unknown security_id"):
        save_stock_daily_prices(batch, database_path=database_path)
    assert load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    ).empty


def test_batch_failure_rolls_back_prior_revision(tmp_path: Path):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    save_stock_daily_prices(
        price_frame(
            security_id,
            amount=None,
            amount_unit=None,
            provider_as_of_date=None,
        ),
        database_path=database_path,
    )
    before = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    batch = pd.concat(
        [
            price_frame(
                security_id,
                close=11.5,
                amount=10000.0,
                amount_unit="CNY",
                provider_as_of_date="2026-07-21",
                observed_at="2026-07-21T17:00:00+08:00",
            ),
            price_frame(999, trade_date="2026-07-22"),
        ],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="Unknown security_id"):
        save_stock_daily_prices(batch, database_path=database_path)
    after = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert_frame_equal(before, after)
    assert load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    ).empty
    assert load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[999],
    ).empty


def test_batch_failure_rolls_back_observation_watermark_update(tmp_path: Path):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    save_stock_daily_prices(
        price_frame(
            security_id,
            amount=None,
            amount_unit=None,
            provider_as_of_date=None,
        ),
        database_path=database_path,
    )
    before = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    batch = pd.concat(
        [
            price_frame(
                security_id,
                amount=None,
                amount_unit=None,
                provider_as_of_date=None,
                observed_at="2026-07-21T17:00:00+08:00",
            ),
            price_frame(999, trade_date="2026-07-22"),
        ],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="Unknown security_id"):
        save_stock_daily_prices(batch, database_path=database_path)
    after = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert_frame_equal(before, after)
    assert load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    ).empty


def test_revision_insert_failure_rolls_back_current_update(tmp_path: Path):
    database_path = tmp_path / "test.db"
    security_id = seed_security(database_path)
    save_stock_daily_prices(price_frame(security_id), database_path=database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_price_revision
            BEFORE INSERT ON stock_daily_price_revision
            BEGIN SELECT RAISE(ABORT, 'revision rejected'); END;
            """
        )
    with pytest.raises(RuntimeError, match="Unable to save stock daily prices"):
        save_stock_daily_prices(
            price_frame(
                security_id,
                close=11.5,
                observed_at="2026-07-21T17:00:00+08:00",
            ),
            database_path=database_path,
        )
    current = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[security_id],
    )
    assert current.loc[0, "close"] == 11.0


def test_loaders_filter_boundaries_sort_identity_and_latest_dates(tmp_path: Path):
    database_path = tmp_path / "test.db"
    sh_id = seed_security(database_path)
    sz_id = seed_security(
        database_path,
        symbol="600000",
        exchange="XSHE",
        board="SZSE_MAIN",
        name="同代码深市样本",
    )
    frames = pd.concat(
        [
            price_frame(sz_id, trade_date="2026-07-22"),
            price_frame(sh_id, trade_date="2026-07-22"),
            price_frame(sh_id, trade_date="2026-07-21"),
        ],
        ignore_index=True,
    )
    save_stock_daily_prices(frames, database_path=database_path)
    loaded = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        start_date="2026-07-21",
        end_date="2026-07-22",
    )
    assert loaded.columns.tolist() == list(STOCK_DAILY_PRICE_RESULT_COLUMNS)
    assert list(zip(loaded.security_id, loaded.trade_date)) == sorted(
        zip(loaded.security_id, loaded.trade_date)
    )
    same_symbol = loaded.loc[loaded["local_symbol"] == "600000"]
    assert set(same_symbol["exchange"]) == {"XSHG", "XSHE"}
    assert same_symbol["security_id"].nunique() == 2
    bounded = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[sh_id],
        start_date="2026-07-22",
        end_date="2026-07-22",
    )
    assert bounded["trade_date"].tolist() == ["2026-07-22"]
    latest = load_latest_stock_daily_price_dates(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[sz_id, sh_id],
    )
    assert latest.columns.tolist() == list(LATEST_STOCK_DAILY_PRICE_DATE_COLUMNS)
    assert latest["security_id"].tolist() == sorted([sh_id, sz_id])
    assert set(latest["latest_trade_date"]) == {"2026-07-22"}


def test_loaders_return_fixed_empty_structures(tmp_path: Path):
    database_path = tmp_path / "test.db"
    prices = load_stock_daily_prices(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[],
    )
    revisions = load_stock_daily_price_revisions(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[],
    )
    latest = load_latest_stock_daily_price_dates(
        database_path=database_path,
        adjustment="QFQ",
        source="EASTMONEY",
        security_ids=[],
    )
    assert prices.empty and prices.columns.tolist() == list(STOCK_DAILY_PRICE_RESULT_COLUMNS)
    assert revisions.empty and revisions.columns.tolist() == list(
        STOCK_DAILY_PRICE_REVISION_RESULT_COLUMNS
    )
    assert latest.empty and latest.columns.tolist() == list(
        LATEST_STOCK_DAILY_PRICE_DATE_COLUMNS
    )


def test_unbounded_all_security_load_requires_both_dates(tmp_path: Path):
    for kwargs in ({}, {"start_date": "2026-07-21"}, {"end_date": "2026-07-21"}):
        with pytest.raises(ValueError, match="start_date and end_date"):
            load_stock_daily_prices(
                database_path=tmp_path / "test.db",
                adjustment="QFQ",
                source="EASTMONEY",
                **kwargs,
            )


def test_price_loaders_require_explicit_adjustment_and_source(tmp_path: Path):
    with pytest.raises(TypeError):
        load_stock_daily_prices(  # type: ignore[call-arg]
            database_path=tmp_path / "test.db",
            security_ids=[],
        )
    with pytest.raises(TypeError):
        load_stock_daily_price_revisions(  # type: ignore[call-arg]
            database_path=tmp_path / "test.db",
            security_ids=[],
        )
    with pytest.raises(TypeError):
        load_latest_stock_daily_price_dates(  # type: ignore[call-arg]
            database_path=tmp_path / "test.db",
            security_ids=[],
        )


def test_loader_rejects_invalid_ranges_and_security_ids(tmp_path: Path):
    base = {
        "database_path": tmp_path / "test.db",
        "adjustment": "QFQ",
        "source": "EASTMONEY",
    }
    with pytest.raises(ValueError, match="start_date must not be after"):
        load_stock_daily_prices(
            **base,
            start_date="2026-07-22",
            end_date="2026-07-21",
        )
    for values in ([True], [0], [1, 1], [1.0], ["1"]):
        with pytest.raises((TypeError, ValueError)):
            load_stock_daily_prices(**base, security_ids=values)
