"""Offline contract tests for the strict EastMoney stock-price provider."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from http.client import RemoteDisconnected
import inspect
import socket
from unittest.mock import Mock, call

import numpy as np
import pandas as pd
import pytest
import requests
from pandas.testing import assert_frame_equal
from urllib3.exceptions import ProtocolError

from src.data.price import (
    STOCK_DAILY_PRICE_COLUMNS,
    PriceProviderError,
    PriceProviderErrorCode,
    normalize_stock_daily_prices,
)
from src.data.providers.eastmoney_price import (
    EASTMONEY_PRICE_FIELDS1,
    EASTMONEY_PRICE_FIELDS2,
    EASTMONEY_PRICE_URL,
    _EASTMONEY_ADJUSTMENTS,
    _EASTMONEY_MARKETS,
    fetch_stock_daily_prices,
)
from src.data.security import SecurityIdentity


OBSERVED_AT = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)


def identity(**changes) -> SecurityIdentity:
    values = {
        "security_id": 7,
        "exchange": "XSHE",
        "asset_type": "COMMON_STOCK",
        "local_symbol": "000021",
        "board": "SZSE_MAIN",
        "current_listing_status": "LISTED",
        "list_date": "1994-02-02",
        "delist_date": None,
    }
    values.update(changes)
    return SecurityIdentity(**values)


def response(
    *,
    code: str = "000021",
    market: int = 0,
    klines=None,
    payload=None,
):
    if klines is None:
        klines = ["2026-07-21,10,11,12,9,100,1000"]
    value = (
        {"rc": 0, "data": {"code": code, "market": market, "klines": klines}}
        if payload is None
        else payload
    )
    result = Mock(status_code=200)
    result.raise_for_status.return_value = None
    result.json.return_value = value
    return result


def fetch(http_get=None, **kwargs):
    return fetch_stock_daily_prices(
        kwargs.pop("identity", identity()),
        adjustment=kwargs.pop("adjustment", "UNADJUSTED"),
        start_date=kwargs.pop("start_date", "2026-07-20"),
        end_date=kwargs.pop("end_date", "2026-07-22"),
        observed_at=kwargs.pop("observed_at", OBSERVED_AT),
        http_get=http_get or Mock(return_value=response()),
        sleep=kwargs.pop("sleep", Mock()),
        **kwargs,
    )


def assert_provider_error(
    error: pytest.ExceptionInfo[PriceProviderError],
    code: PriceProviderErrorCode,
    *,
    retryable: bool = False,
    batch_signal: bool = False,
    attempts: int = 1,
    status_code=None,
):
    value = error.value
    assert value.provider == "EASTMONEY"
    assert value.code is code
    assert value.retryable is retryable
    assert value.batch_signal is batch_signal
    assert value.attempts == attempts
    assert value.status_code == status_code


def chained_connection(cause: BaseException) -> requests.ConnectionError:
    try:
        raise cause
    except BaseException as inner:
        try:
            raise requests.ConnectionError("connection") from inner
        except requests.ConnectionError as outer:
            return outer


def branched_exception(
    *,
    cause: BaseException,
    context: BaseException,
    root: requests.RequestException | None = None,
) -> requests.RequestException:
    result = root or requests.ConnectionError("root")
    result.__cause__ = cause
    result.__context__ = context
    return result


def test_signature_requires_identity_adjustment_and_dates_without_database_path():
    signature = inspect.signature(fetch_stock_daily_prices)
    assert signature.parameters["adjustment"].default is inspect.Parameter.empty
    assert signature.parameters["start_date"].default is inspect.Parameter.empty
    assert signature.parameters["end_date"].default is inspect.Parameter.empty
    assert "database_path" not in signature.parameters


def test_rejects_symbol_instead_of_identity_without_http():
    http_get = Mock()
    with pytest.raises(TypeError, match="SecurityIdentity"):
        fetch_stock_daily_prices(
            "000021",
            adjustment="UNADJUSTED",
            start_date="2026-07-20",
            end_date="2026-07-21",
            http_get=http_get,
        )
    http_get.assert_not_called()


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("BAD", "adjustment"),
        ("20260720", "start_date"),
        ("2026-02-30", "start_date"),
    ],
)
def test_rejects_invalid_adjustment_or_start_date(value, message):
    kwargs = (
        {"adjustment": value}
        if message == "adjustment"
        else {"start_date": value}
    )
    with pytest.raises(ValueError, match=message):
        fetch(**kwargs)


def test_rejects_invalid_end_or_reversed_dates():
    with pytest.raises(ValueError, match="end_date"):
        fetch(end_date="20260722")
    with pytest.raises(ValueError, match="after"):
        fetch(start_date="2026-07-23", end_date="2026-07-22")


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_rejects_invalid_max_attempts(value):
    with pytest.raises(ValueError, match="max_attempts"):
        fetch(max_attempts=value)


@pytest.mark.parametrize("value", [-1, True, np.nan, np.inf])
def test_rejects_invalid_backoff(value):
    with pytest.raises(ValueError, match="backoff_base"):
        fetch(backoff_base=value)


@pytest.mark.parametrize(
    "value",
    [(5,), [5, 10], (0, 10), (5, True), (np.nan, 10), (5, np.inf)],
)
def test_rejects_invalid_timeout(value):
    with pytest.raises(ValueError, match="timeout"):
        fetch(timeout=value)


def test_rejects_naive_or_non_datetime_observed_at():
    with pytest.raises(ValueError, match="timezone-aware"):
        fetch(observed_at=datetime(2026, 7, 22, 16, 0))
    with pytest.raises(TypeError, match="datetime"):
        fetch(observed_at="2026-07-22")


@pytest.mark.parametrize(
    ("identity_value", "start", "end"),
    [
        (identity(list_date="2026-07-20"), "2026-07-01", "2026-07-19"),
        (
            identity(
                current_listing_status="DELISTED",
                delist_date="2026-07-10",
            ),
            "2026-07-11",
            "2026-07-20",
        ),
    ],
)
def test_listing_interval_without_overlap_is_no_data_without_http(
    identity_value,
    start,
    end,
):
    http_get = Mock()
    with pytest.raises(PriceProviderError) as error:
        fetch(
            identity=identity_value,
            start_date=start,
            end_date=end,
            http_get=http_get,
        )
    assert_provider_error(error, PriceProviderErrorCode.NO_DATA, attempts=0)
    http_get.assert_not_called()


def test_partial_listing_overlap_clips_closed_dates_and_lmt():
    http_get = Mock(
        return_value=response(
            klines=["2026-07-20,10,11,12,9,100,1000"]
        )
    )
    fetch(
        identity=identity(list_date="2026-07-20"),
        start_date="2026-07-01",
        end_date="2026-07-22",
        http_get=http_get,
    )
    params = http_get.call_args.kwargs["params"]
    assert params["beg"] == "20260720"
    assert params["end"] == "20260722"
    assert params["lmt"] == "3"


def test_partial_delisting_overlap_clips_closed_dates_and_lmt():
    http_get = Mock(
        return_value=response(
            klines=["2026-07-20,10,11,12,9,100,1000"]
        )
    )
    fetch(
        identity=identity(
            current_listing_status="DELISTED",
            delist_date="2026-07-20",
        ),
        start_date="2026-07-19",
        end_date="2026-07-22",
        http_get=http_get,
    )
    params = http_get.call_args.kwargs["params"]
    assert params["beg"] == "20260719"
    assert params["end"] == "20260720"
    assert params["lmt"] == "2"


@pytest.mark.parametrize(
    ("exchange", "board", "symbol", "market"),
    [
        ("XSHE", "SZSE_MAIN", "600000", 0),
        ("XSHG", "SSE_MAIN", "000021", 1),
    ],
)
def test_market_comes_from_identity_not_symbol_prefix(
    exchange,
    board,
    symbol,
    market,
):
    http_get = Mock(
        return_value=response(code=symbol, market=market)
    )
    fetch(
        identity=identity(
            exchange=exchange,
            board=board,
            local_symbol=symbol,
        ),
        http_get=http_get,
    )
    assert http_get.call_args.kwargs["params"]["secid"] == f"{market}.{symbol}"


@pytest.mark.parametrize(
    ("adjustment", "fqt"),
    [("UNADJUSTED", "0"), ("QFQ", "1"), ("HFQ", "2")],
)
def test_adjustment_request_and_provider_value(adjustment, fqt):
    http_get = Mock(return_value=response())
    result = fetch(adjustment=adjustment, http_get=http_get)
    params = http_get.call_args.kwargs["params"]
    assert params["fqt"] == fqt
    assert result.loc[0, "provider_adjustment"] == f"fqt={fqt}"


def test_frozen_request_mappings_reject_in_place_changes():
    with pytest.raises(TypeError):
        _EASTMONEY_MARKETS["XSHE"] = 1
    with pytest.raises(TypeError):
        _EASTMONEY_ADJUSTMENTS["QFQ"] = "0"
    assert dict(_EASTMONEY_MARKETS) == {"XSHE": 0, "XSHG": 1}
    assert dict(_EASTMONEY_ADJUSTMENTS) == {
        "UNADJUSTED": "0",
        "QFQ": "1",
        "HFQ": "2",
    }


def test_request_contract_timeout_headers_fields_and_closed_range():
    http_get = Mock(return_value=response())
    fetch(http_get=http_get, timeout=(2.0, 7.0))
    http_get.assert_called_once_with(
        EASTMONEY_PRICE_URL,
        params={
            "secid": "0.000021",
            "klt": "101",
            "fqt": "0",
            "beg": "20260720",
            "end": "20260722",
            "lmt": "3",
            "fields1": EASTMONEY_PRICE_FIELDS1,
            "fields2": EASTMONEY_PRICE_FIELDS2,
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=(2.0, 7.0),
    )


@pytest.mark.parametrize(
    ("exc", "code", "retryable", "batch_signal"),
    [
        (
            requests.exceptions.ProxyError("proxy"),
            PriceProviderErrorCode.PROXY_UNAVAILABLE,
            False,
            True,
        ),
        (
            requests.exceptions.InvalidProxyURL("proxy"),
            PriceProviderErrorCode.NETWORK_CONFIGURATION,
            False,
            True,
        ),
        (
            requests.exceptions.InvalidURL("url"),
            PriceProviderErrorCode.NETWORK_CONFIGURATION,
            False,
            True,
        ),
        (
            requests.exceptions.MissingSchema("schema"),
            PriceProviderErrorCode.NETWORK_CONFIGURATION,
            False,
            True,
        ),
        (
            requests.exceptions.InvalidSchema("schema"),
            PriceProviderErrorCode.NETWORK_CONFIGURATION,
            False,
            True,
        ),
        (
            requests.Timeout("timeout"),
            PriceProviderErrorCode.TIMEOUT,
            True,
            False,
        ),
        (
            requests.exceptions.ConnectTimeout("connect timeout"),
            PriceProviderErrorCode.TIMEOUT,
            True,
            False,
        ),
        (
            requests.exceptions.ReadTimeout("read timeout"),
            PriceProviderErrorCode.TIMEOUT,
            True,
            False,
        ),
        (
            requests.ConnectionError("connection"),
            PriceProviderErrorCode.CONNECTION_CLOSED,
            True,
            False,
        ),
        (
            requests.RequestException("other"),
            PriceProviderErrorCode.NETWORK_CONFIGURATION,
            False,
            True,
        ),
    ],
)
def test_request_exception_classification(
    exc,
    code,
    retryable,
    batch_signal,
):
    http_get = Mock(side_effect=exc)
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=http_get, max_attempts=1)
    assert_provider_error(
        error,
        code,
        retryable=retryable,
        batch_signal=batch_signal,
    )
    assert error.value.__cause__ is exc


@pytest.mark.parametrize(
    ("cause", "code", "batch_signal"),
    [
        (
            socket.gaierror(socket.EAI_NONAME, "name"),
            PriceProviderErrorCode.DNS_FAILURE,
            True,
        ),
        (
            RemoteDisconnected("closed"),
            PriceProviderErrorCode.CONNECTION_CLOSED,
            False,
        ),
        (
            ProtocolError("closed"),
            PriceProviderErrorCode.CONNECTION_CLOSED,
            False,
        ),
    ],
)
def test_request_exception_chain_classification(cause, code, batch_signal):
    http_get = Mock(side_effect=chained_connection(cause))
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=http_get, max_attempts=1)
    assert_provider_error(
        error,
        code,
        retryable=True,
        batch_signal=batch_signal,
    )


@pytest.mark.parametrize("dns_branch", ["cause", "context"])
def test_dual_branch_dns_classification_is_order_independent(dns_branch):
    dns = socket.gaierror(socket.EAI_NONAME, "dns")
    protocol = ProtocolError("closed")
    root = branched_exception(
        cause=dns if dns_branch == "cause" else protocol,
        context=protocol if dns_branch == "cause" else dns,
    )
    http_get = Mock(side_effect=root)
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=http_get, max_attempts=1)
    assert_provider_error(
        error,
        PriceProviderErrorCode.DNS_FAILURE,
        retryable=True,
        batch_signal=True,
    )
    http_get.assert_called_once()


@pytest.mark.parametrize("protocol_branch", ["cause", "context"])
def test_dual_branch_protocol_classification_is_order_independent(
    protocol_branch,
):
    protocol = RemoteDisconnected("closed")
    connection = requests.ConnectionError("ordinary")
    root = branched_exception(
        cause=protocol if protocol_branch == "cause" else connection,
        context=connection if protocol_branch == "cause" else protocol,
    )
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=Mock(side_effect=root), max_attempts=1)
    assert_provider_error(
        error,
        PriceProviderErrorCode.CONNECTION_CLOSED,
        retryable=True,
    )


@pytest.mark.parametrize(
    ("root", "nested", "code", "retryable", "batch_signal"),
    [
        (
            requests.exceptions.ProxyError("proxy"),
            socket.gaierror(socket.EAI_NONAME, "dns"),
            PriceProviderErrorCode.PROXY_UNAVAILABLE,
            False,
            True,
        ),
        (
            requests.exceptions.ConnectTimeout("timeout"),
            requests.ConnectionError("connection"),
            PriceProviderErrorCode.TIMEOUT,
            True,
            False,
        ),
        (
            requests.exceptions.InvalidURL("url"),
            requests.ConnectionError("connection"),
            PriceProviderErrorCode.NETWORK_CONFIGURATION,
            False,
            True,
        ),
    ],
)
def test_exception_graph_uses_frozen_classification_priority(
    root,
    nested,
    code,
    retryable,
    batch_signal,
):
    root.__context__ = nested
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=Mock(side_effect=root), max_attempts=1)
    assert_provider_error(
        error,
        code,
        retryable=retryable,
        batch_signal=batch_signal,
    )


def test_exception_graph_cycle_terminates_and_classifies_once():
    root = requests.ConnectionError("root")
    protocol = ProtocolError("closed")
    root.__cause__ = protocol
    protocol.__context__ = root
    http_get = Mock(side_effect=root)
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=http_get, max_attempts=1)
    assert_provider_error(
        error,
        PriceProviderErrorCode.CONNECTION_CLOSED,
        retryable=True,
    )
    http_get.assert_called_once()


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_http_status_retries_with_exponential_backoff(status):
    failed_response = Mock(status_code=status)
    failed_response.raise_for_status.side_effect = requests.HTTPError(
        response=failed_response
    )
    http_get = Mock(side_effect=[failed_response, failed_response, response()])
    sleep = Mock()
    result = fetch(http_get=http_get, sleep=sleep)
    assert not result.empty
    assert http_get.call_count == 3
    assert sleep.call_args_list == [call(0.5), call(1.0)]


@pytest.mark.parametrize(
    ("status", "batch_signal"),
    [(400, False), (401, True), (403, True), (404, False)],
)
def test_final_http_status_is_not_retried(status, batch_signal):
    failed_response = Mock(status_code=status)
    failed_response.raise_for_status.side_effect = requests.HTTPError(
        response=failed_response
    )
    http_get = Mock(return_value=failed_response)
    sleep = Mock()
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=http_get, sleep=sleep)
    assert_provider_error(
        error,
        PriceProviderErrorCode.HTTP_FINAL,
        batch_signal=batch_signal,
        status_code=status,
    )
    http_get.assert_called_once()
    sleep.assert_not_called()


def test_last_retryable_failure_does_not_sleep():
    http_get = Mock(side_effect=requests.Timeout("timeout"))
    sleep = Mock()
    with pytest.raises(PriceProviderError) as error:
        fetch(
            http_get=http_get,
            sleep=sleep,
            max_attempts=3,
            backoff_base=0.25,
        )
    assert_provider_error(
        error,
        PriceProviderErrorCode.TIMEOUT,
        retryable=True,
        attempts=3,
    )
    assert sleep.call_args_list == [call(0.25), call(0.5)]


@pytest.mark.parametrize("max_attempts", [1, 3])
def test_retryable_http_exhaustion_reports_final_attempt(max_attempts):
    failed_response = Mock(status_code=503)
    failed_response.raise_for_status.side_effect = requests.HTTPError(
        response=failed_response
    )
    http_get = Mock(return_value=failed_response)
    sleep = Mock()
    with pytest.raises(PriceProviderError) as error:
        fetch(
            http_get=http_get,
            sleep=sleep,
            max_attempts=max_attempts,
        )
    assert_provider_error(
        error,
        PriceProviderErrorCode.HTTP_RETRYABLE,
        retryable=True,
        batch_signal=True,
        attempts=max_attempts,
        status_code=503,
    )
    assert http_get.call_count == max_attempts
    assert sleep.call_args_list == [
        call(0.5 * 2 ** attempt)
        for attempt in range(max_attempts - 1)
    ]


def test_success_stops_retrying():
    http_get = Mock(side_effect=[requests.Timeout("timeout"), response()])
    sleep = Mock()
    fetch(http_get=http_get, sleep=sleep)
    assert http_get.call_count == 2
    sleep.assert_called_once_with(0.5)


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ([], PriceProviderErrorCode.INVALID_SCHEMA),
        ({}, PriceProviderErrorCode.INVALID_SCHEMA),
        ({"rc": "0"}, PriceProviderErrorCode.INVALID_SCHEMA),
        ({"rc": True}, PriceProviderErrorCode.INVALID_SCHEMA),
        ({"rc": 1}, PriceProviderErrorCode.PROVIDER_REJECTED),
        ({"rc": 0, "data": None}, PriceProviderErrorCode.NO_DATA),
        ({"rc": 0, "data": []}, PriceProviderErrorCode.INVALID_SCHEMA),
        (
            {"rc": 0, "data": {"market": 0, "klines": []}},
            PriceProviderErrorCode.INVALID_SCHEMA,
        ),
        (
            {"rc": 0, "data": {"code": "000021", "klines": []}},
            PriceProviderErrorCode.INVALID_SCHEMA,
        ),
        (
            {"rc": 0, "data": {"code": "000021", "market": 0}},
            PriceProviderErrorCode.INVALID_SCHEMA,
        ),
        (
            {"rc": 0, "data": {"code": 21, "market": 0, "klines": []}},
            PriceProviderErrorCode.INVALID_SCHEMA,
        ),
        (
            {"rc": 0, "data": {"code": "00002A", "market": 0, "klines": []}},
            PriceProviderErrorCode.INVALID_SCHEMA,
        ),
        (
            {"rc": 0, "data": {"code": "００００２１", "market": 0, "klines": []}},
            PriceProviderErrorCode.INVALID_SCHEMA,
        ),
        (
            {"rc": 0, "data": {"code": "000021", "market": "0", "klines": []}},
            PriceProviderErrorCode.INVALID_SCHEMA,
        ),
        (
            {"rc": 0, "data": {"code": "000021", "market": True, "klines": []}},
            PriceProviderErrorCode.INVALID_SCHEMA,
        ),
        (
            {"rc": 0, "data": {"code": "000021", "market": 2, "klines": []}},
            PriceProviderErrorCode.INVALID_SCHEMA,
        ),
        (
            {"rc": 0, "data": {"code": "000021", "market": 0, "klines": "bad"}},
            PriceProviderErrorCode.INVALID_SCHEMA,
        ),
        (
            {"rc": 0, "data": {"code": "000021", "market": 0, "klines": []}},
            PriceProviderErrorCode.NO_DATA,
        ),
    ],
)
def test_response_schema_and_business_outcomes(payload, code):
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=Mock(return_value=response(payload=payload)))
    assert_provider_error(error, code)


@pytest.mark.parametrize("response_code", ["٠٠٠٠٢١", "00002١"])
def test_response_unicode_code_is_invalid_schema(response_code):
    http_get = Mock(return_value=response(code=response_code))
    sleep = Mock()
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=http_get, sleep=sleep)
    assert_provider_error(
        error,
        PriceProviderErrorCode.INVALID_SCHEMA,
        retryable=False,
        batch_signal=False,
        attempts=1,
        status_code=None,
    )
    http_get.assert_called_once()
    sleep.assert_not_called()


def test_invalid_json_is_not_retried_and_preserves_cause():
    value = response()
    cause = ValueError("bad json")
    value.json.side_effect = cause
    http_get = Mock(return_value=value)
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=http_get)
    assert_provider_error(error, PriceProviderErrorCode.INVALID_JSON)
    assert error.value.__cause__ is cause
    http_get.assert_called_once()


@pytest.mark.parametrize(
    ("code", "market"),
    [("000022", 0), ("000021", 1)],
)
def test_response_identity_mismatch(code, market):
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=Mock(return_value=response(code=code, market=market)))
    assert_provider_error(error, PriceProviderErrorCode.IDENTITY_MISMATCH)


def test_valid_rows_sort_and_return_complete_normalized_contract():
    http_get = Mock(
        return_value=response(
            klines=[
                "2026-07-22,11,12,13,10,200,2000,extra",
                "2026-07-20,9,10,11,8,100,1000",
            ]
        )
    )
    source_identity = identity()
    before = asdict(source_identity)
    result = fetch(identity=source_identity, http_get=http_get)
    assert result.columns.tolist() == list(STOCK_DAILY_PRICE_COLUMNS)
    assert result["trade_date"].tolist() == ["2026-07-20", "2026-07-22"]
    assert result["security_id"].tolist() == [7, 7]
    assert result["source"].tolist() == ["EASTMONEY", "EASTMONEY"]
    assert result["volume_unit"].tolist() == [
        "PROVIDER_NATIVE",
        "PROVIDER_NATIVE",
    ]
    assert result["provider_as_of_date"].isna().all()
    assert result["is_final"].tolist() == [True, False]
    assert_frame_equal(result, normalize_stock_daily_prices(result))
    assert asdict(source_identity) == before


@pytest.mark.parametrize(
    "kline",
    [
        123,
        "2026-07-21,10",
        "bad-date,10,11,12,9,100,1000",
        "2026-07-19,10,11,12,9,100,1000",
        "2026-07-23,10,11,12,9,100,1000",
        "2026-07-21,bad,11,12,9,100,1000",
        "2026-07-21,nan,11,12,9,100,1000",
        "2026-07-21,inf,11,12,9,100,1000",
        "2026-07-21,0,11,12,9,100,1000",
        "2026-07-21,13,11,12,9,100,1000",
        "2026-07-21,10,8,12,9,100,1000",
        "2026-07-21,10,11,12,9,1.5,1000",
        "2026-07-21,10,11,12,9,-1,1000",
        "2026-07-21,10,11,12,9,100,-1",
        "2026-07-21,10,11,12,9,100,nan",
        "2026-07-21,10,11,12,9,100,inf",
        "2026-07-21,10,11,12,9,+1,1000",
        "2026-07-21,10,11,12,9,1e3,1000",
        "2026-07-21,10,11,12,9, 1,1000",
        "2026-07-21,10,11,12,9,1 ,1000",
        "2026-07-21,10,11,12,9,１００,1000",
        "2026-07-21,10,11,12,9,,1000",
    ],
)
def test_invalid_kline_fails_entire_response(kline):
    klines = ["2026-07-20,9,10,11,8,100,1000", kline]
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=Mock(return_value=response(klines=klines)))
    assert_provider_error(error, PriceProviderErrorCode.INVALID_DATA)


def test_decimal_volume_text_is_invalid_data_without_retry():
    http_get = Mock(
        return_value=response(
            klines=["2026-07-21,10,11,12,9,1.0,1000"]
        )
    )
    sleep = Mock()
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=http_get, sleep=sleep)
    assert_provider_error(
        error,
        PriceProviderErrorCode.INVALID_DATA,
        retryable=False,
        batch_signal=False,
        attempts=1,
        status_code=None,
    )
    http_get.assert_called_once()
    sleep.assert_not_called()


def test_duplicate_trade_date_is_invalid():
    line = "2026-07-21,10,11,12,9,100,1000"
    with pytest.raises(PriceProviderError) as error:
        fetch(http_get=Mock(return_value=response(klines=[line, line])))
    assert_provider_error(error, PriceProviderErrorCode.INVALID_DATA)


@pytest.mark.parametrize("raw_amount", ["", "-"])
def test_missing_amount_pairs_null_unit(raw_amount):
    result = fetch(
        http_get=Mock(
            return_value=response(
                klines=[f"2026-07-21,10,11,12,9,100,{raw_amount}"]
            )
        )
    )
    assert pd.isna(result.loc[0, "amount"])
    assert pd.isna(result.loc[0, "amount_unit"])


def test_zero_amount_is_valid_provider_native():
    result = fetch(
        http_get=Mock(
            return_value=response(
                klines=["2026-07-21,10,11,12,9,0,0"]
            )
        )
    )
    assert result.loc[0, "amount"] == 0
    assert result.loc[0, "amount_unit"] == "PROVIDER_NATIVE"
    assert result.loc[0, "volume"] == 0


def test_mixed_nullable_amount_preserves_each_row_semantics_and_sorting():
    result = fetch(
        http_get=Mock(
            return_value=response(
                klines=[
                    "2026-07-22,11,12,13,10,200,0",
                    "2026-07-20,9,10,11,8,100,",
                    "2026-07-21,10,11,12,9,150,1500",
                ]
            )
        )
    )
    assert result.columns.tolist() == list(STOCK_DAILY_PRICE_COLUMNS)
    assert result["trade_date"].tolist() == [
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
    ]
    assert pd.isna(result.loc[0, "amount"])
    assert pd.isna(result.loc[0, "amount_unit"])
    assert result.loc[1, "amount"] == 1500
    assert result.loc[1, "amount_unit"] == "PROVIDER_NATIVE"
    assert result.loc[2, "amount"] == 0
    assert result.loc[2, "amount_unit"] == "PROVIDER_NATIVE"


def test_normalizer_failure_is_typed_invalid_data(monkeypatch):
    failure = ValueError("strict contract failed")
    monkeypatch.setattr(
        "src.data.providers.eastmoney_price.normalize_stock_daily_prices",
        Mock(side_effect=failure),
    )
    with pytest.raises(PriceProviderError) as error:
        fetch()
    assert_provider_error(error, PriceProviderErrorCode.INVALID_DATA)
    assert error.value.__cause__ is failure


def test_explicit_observation_is_utc_and_shared_with_conservative_finality():
    result = fetch(
        observed_at=datetime(2026, 7, 22, 16, 30, tzinfo=timezone.utc),
        end_date="2026-07-23",
        http_get=Mock(
            return_value=response(
                klines=[
                    "2026-07-22,10,11,12,9,100,1000",
                    "2026-07-23,11,12,13,10,200,2000",
                ]
            )
        ),
    )
    assert result["observed_at"].nunique() == 1
    assert result.loc[0, "observed_at"] == "2026-07-22T16:30:00+00:00"
    assert result["is_final"].tolist() == [True, False]


def test_future_relative_to_shanghai_observation_is_invalid():
    with pytest.raises(PriceProviderError) as error:
        fetch(
            observed_at=datetime(2026, 7, 22, 1, 0, tzinfo=timezone.utc),
            end_date="2026-07-23",
            http_get=Mock(
                return_value=response(
                    klines=["2026-07-23,10,11,12,9,100,1000"]
                )
            ),
        )
    assert_provider_error(error, PriceProviderErrorCode.INVALID_DATA)


def test_none_observation_generates_aware_utc_timestamp():
    result = fetch(observed_at=None)
    parsed = datetime.fromisoformat(result.loc[0, "observed_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)
