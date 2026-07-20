from unittest.mock import Mock, call, patch

import pytest
import requests

from src.data.providers.eastmoney_market import get_market_breadth


def response(payload):
    result = Mock()
    result.raise_for_status.return_value = None
    result.json.return_value = payload
    return result


def payload(sh=(1000, 800, 100), sz=(1200, 900, 120)):
    return {"rc": 0, "data": {"diff": [
        {"f13": 1, "f12": "000001", "f104": sh[0], "f105": sh[1], "f106": sh[2]},
        {"f13": 0, "f12": "399001", "f104": sz[0], "f105": sz[1], "f106": sz[2]},
    ]}}


@patch("src.data.providers.eastmoney_market.requests.get")
def test_breadth_provider_validates_both_markets_and_sums_counts(mock_get):
    mock_get.return_value = response(payload())
    result = get_market_breadth()
    assert (result.advance_count, result.decline_count, result.flat_count) == (2200, 1700, 220)
    assert mock_get.call_args.kwargs["params"] == {
        "secids": "1.000001,0.399001",
        "fields": "f12,f13,f104,f105,f106",
    }


@pytest.mark.parametrize("bad_payload", [
    {"rc": 0, "data": {"diff": [{"f13": 1, "f12": "000001", "f104": 1, "f105": 2, "f106": 3}]}},
    {"rc": 0, "data": {"diff": [{"f13": 1, "f12": "000001", "f104": 1, "f105": 2, "f106": 3}] * 2}},
    {"rc": 0, "data": {"diff": "bad"}},
    {"rc": 1, "data": None},
])
def test_breadth_provider_rejects_missing_duplicate_or_invalid_market_records(bad_payload):
    with patch("src.data.providers.eastmoney_market.requests.get", return_value=response(bad_payload)) as mock_get:
        with pytest.raises(RuntimeError, match="after 1 attempts"):
            get_market_breadth(sleep=Mock())
    mock_get.assert_called_once()


@pytest.mark.parametrize("field,value", [("f104", None), ("f105", -1), ("f106", 1.5)])
def test_breadth_provider_requires_all_non_negative_integer_fields(field, value):
    bad = payload()
    bad["data"]["diff"][1][field] = value
    with patch("src.data.providers.eastmoney_market.requests.get", return_value=response(bad)):
        with pytest.raises(RuntimeError, match=field):
            get_market_breadth(sleep=Mock())


@pytest.mark.parametrize("error", [requests.Timeout("timeout"), requests.ConnectionError("connection")])
def test_breadth_provider_retries_network_errors_without_real_sleep(error):
    mock_get = Mock(side_effect=[error, response(payload())])
    sleep = Mock()
    with patch("src.data.providers.eastmoney_market.requests.get", mock_get):
        result = get_market_breadth(sleep=sleep)
    assert result.advance_count == 2200
    assert mock_get.call_count == 2
    assert sleep.call_args_list == [call(0.5)]


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_breadth_provider_retries_retryable_http(status):
    failure = Mock(status_code=status)
    failure.raise_for_status.side_effect = requests.HTTPError(response=failure)
    mock_get = Mock(side_effect=[failure, response(payload())])
    sleep = Mock()
    with patch("src.data.providers.eastmoney_market.requests.get", mock_get):
        get_market_breadth(sleep=sleep)
    assert mock_get.call_count == 2
    sleep.assert_called_once_with(0.5)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_breadth_provider_does_not_retry_non_retryable_http(status):
    failure = Mock(status_code=status)
    failure.raise_for_status.side_effect = requests.HTTPError(response=failure)
    sleep = Mock()
    with patch("src.data.providers.eastmoney_market.requests.get", return_value=failure) as mock_get:
        with pytest.raises(RuntimeError, match="after 1 attempts"):
            get_market_breadth(sleep=sleep)
    mock_get.assert_called_once()
    sleep.assert_not_called()


def test_breadth_provider_does_not_retry_invalid_json():
    invalid = response({})
    invalid.json.side_effect = ValueError("bad JSON")
    with patch("src.data.providers.eastmoney_market.requests.get", return_value=invalid) as mock_get:
        with pytest.raises(RuntimeError, match="after 1 attempts"):
            get_market_breadth(sleep=Mock())
    mock_get.assert_called_once()
