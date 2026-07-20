import pytest

from src.data.market import (
    EASTMONEY_BREADTH_SOURCE,
    SSE_AMOUNT_SOURCE,
    SZSE_AMOUNT_SOURCE,
    ExchangeDailyAmount,
    MarketBreadth,
    MarketDaily,
    calculate_advance_ratio,
    compose_market_daily,
    validate_trade_date,
    yi_yuan_to_yuan,
)


def test_yi_yuan_to_yuan_uses_exact_decimal_conversion():
    assert yi_yuan_to_yuan("1,234.56") == 123_456_000_000
    assert yi_yuan_to_yuan("0.01") == 1_000_000


@pytest.mark.parametrize("value", [None, True, "", "-1", "NaN", "1.001"])
def test_yi_yuan_to_yuan_rejects_invalid_or_excess_precision(value):
    with pytest.raises(ValueError):
        yi_yuan_to_yuan(value)


@pytest.mark.parametrize("value", ["2026/07/17", "2026-02-30", "2026-7-1", None])
def test_trade_date_is_strict_iso(value):
    with pytest.raises(ValueError, match="trade date"):
        validate_trade_date(value)


def test_compose_market_daily_calculates_total_and_preserves_sources():
    record = compose_market_daily(
        "2026-07-17",
        sh_amount=ExchangeDailyAmount("2026-07-17", 100, SSE_AMOUNT_SOURCE),
        sz_amount=ExchangeDailyAmount("2026-07-17", 200, SZSE_AMOUNT_SOURCE),
        breadth=MarketBreadth(3000, 1800, 200),
    )
    assert record.total_amount_yuan == 300
    assert record.sh_amount_source == SSE_AMOUNT_SOURCE
    assert record.sz_amount_source == SZSE_AMOUNT_SOURCE
    assert record.breadth_source == EASTMONEY_BREADTH_SOURCE
    assert calculate_advance_ratio(record) == pytest.approx(0.6)


def test_market_groups_are_all_present_or_all_missing():
    empty = compose_market_daily("2026-07-17")
    assert empty.total_amount_yuan is None
    assert calculate_advance_ratio(empty) is None
    with pytest.raises(ValueError, match="supplied together"):
        compose_market_daily(
            "2026-07-17",
            sh_amount=ExchangeDailyAmount("2026-07-17", 100, SSE_AMOUNT_SOURCE),
        )
    with pytest.raises(ValueError, match="breadth fields"):
        MarketDaily("2026-07-17", advance_count=1, decline_count=None, flat_count=1)


def test_market_amount_dates_and_sources_must_match():
    with pytest.raises(ValueError, match="dates must match"):
        compose_market_daily(
            "2026-07-17",
            sh_amount=ExchangeDailyAmount("2026-07-16", 100, SSE_AMOUNT_SOURCE),
            sz_amount=ExchangeDailyAmount("2026-07-17", 200, SZSE_AMOUNT_SOURCE),
        )
    with pytest.raises(ValueError, match="wrong market"):
        compose_market_daily(
            "2026-07-17",
            sh_amount=ExchangeDailyAmount("2026-07-17", 100, SZSE_AMOUNT_SOURCE),
            sz_amount=ExchangeDailyAmount("2026-07-17", 200, SSE_AMOUNT_SOURCE),
        )


@pytest.mark.parametrize("counts", [(-1, 1, 1), (1.5, 1, 1), (True, 1, 1)])
def test_market_breadth_rejects_invalid_counts(counts):
    with pytest.raises(ValueError):
        MarketBreadth(*counts)


def test_advance_ratio_is_missing_for_zero_denominator():
    assert calculate_advance_ratio(
        compose_market_daily("2026-07-17", breadth=MarketBreadth(0, 0, 0))
    ) is None
