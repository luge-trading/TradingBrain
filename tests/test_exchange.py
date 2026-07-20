from unittest.mock import Mock, call, patch

import pytest
import requests

from src.data.market import SSE_AMOUNT_SOURCE, SZSE_AMOUNT_SOURCE
from src.data.providers.exchange import get_sse_daily_amount, get_szse_daily_amount


def response(payload):
    result = Mock()
    result.raise_for_status.return_value = None
    result.json.return_value = payload
    return result


def sse_payload(date="20260717", amount="123.45"):
    return {"result": [{"PRODUCT_CODE": "17", "TRADE_DATE": date, "TRADE_AMT": amount}]}


def szse_payload(date="2026-07-17", amount="234.56"):
    return [{
        "metadata": {"conditions": [{"name": "txtQueryDate", "defaultValue": date}]},
        "data": [{"zbmc": "成交金额（亿元）", "gp": amount}],
    }]


@patch("src.data.providers.exchange.requests.get")
def test_sse_provider_selects_stock_total_validates_date_and_converts_amount(mock_get):
    mock_get.return_value = response(sse_payload())
    result = get_sse_daily_amount("2026-07-17")
    assert result.amount_yuan == 12_345_000_000
    assert result.source == SSE_AMOUNT_SOURCE
    assert mock_get.call_args.kwargs["params"]["SEARCH_DATE"] == "2026-07-17"


@patch("src.data.providers.exchange.requests.get")
def test_szse_provider_uses_metadata_date_and_gp_total(mock_get):
    mock_get.return_value = response(szse_payload())
    result = get_szse_daily_amount("2026-07-17")
    assert result.amount_yuan == 23_456_000_000
    assert result.source == SZSE_AMOUNT_SOURCE
    assert mock_get.call_args.kwargs["params"]["CATALOGID"] == "scsj_gprdgk_after"


@pytest.mark.parametrize("fetcher,payload", [
    (get_sse_daily_amount, sse_payload(date="20260716")),
    (get_szse_daily_amount, szse_payload(date="2026-07-16")),
])
def test_exchange_provider_rejects_response_date_mismatch(fetcher, payload):
    with patch("src.data.providers.exchange.requests.get", return_value=response(payload)) as mock_get:
        with pytest.raises(RuntimeError, match="date does not match"):
            fetcher("2026-07-17", sleep=Mock())
    mock_get.assert_called_once()


@pytest.mark.parametrize("fetcher,payload", [
    (get_sse_daily_amount, sse_payload(amount=None)),
    (get_szse_daily_amount, szse_payload(amount="--")),
])
def test_exchange_provider_rejects_missing_amount(fetcher, payload):
    with patch("src.data.providers.exchange.requests.get", return_value=response(payload)):
        with pytest.raises(RuntimeError, match="Invalid .* response"):
            fetcher("2026-07-17", sleep=Mock())


@pytest.mark.parametrize("fetcher", [get_sse_daily_amount, get_szse_daily_amount])
def test_exchange_provider_retries_timeout_without_real_sleep(fetcher):
    mock_get = Mock(side_effect=[requests.Timeout("timeout"), response(sse_payload() if fetcher is get_sse_daily_amount else szse_payload())])
    sleep = Mock()
    with patch("src.data.providers.exchange.requests.get", mock_get):
        result = fetcher("2026-07-17", sleep=sleep)
    assert result.trade_date == "2026-07-17"
    assert mock_get.call_count == 2
    assert sleep.call_args_list == [call(0.5)]


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_exchange_provider_retries_retryable_http(status):
    failure = Mock(status_code=status)
    failure.raise_for_status.side_effect = requests.HTTPError(response=failure)
    mock_get = Mock(side_effect=[failure, response(sse_payload())])
    sleep = Mock()
    with patch("src.data.providers.exchange.requests.get", mock_get):
        get_sse_daily_amount("2026-07-17", sleep=sleep)
    assert mock_get.call_count == 2
    sleep.assert_called_once_with(0.5)


def test_exchange_provider_does_not_retry_http_400_or_invalid_json():
    failure = Mock(status_code=400)
    failure.raise_for_status.side_effect = requests.HTTPError(response=failure)
    with patch("src.data.providers.exchange.requests.get", return_value=failure) as mock_get:
        with pytest.raises(RuntimeError, match="after 1 attempts"):
            get_sse_daily_amount("2026-07-17", sleep=Mock())
    mock_get.assert_called_once()
    invalid = response({})
    invalid.json.side_effect = ValueError("bad JSON")
    with patch("src.data.providers.exchange.requests.get", return_value=invalid) as mock_get:
        with pytest.raises(RuntimeError, match="invalid JSON"):
            get_szse_daily_amount("2026-07-17", sleep=Mock())
    mock_get.assert_called_once()
