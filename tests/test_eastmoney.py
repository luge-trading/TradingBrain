"""Tests for the EastMoney market data provider."""

from unittest.mock import Mock, patch

import pandas as pd
import pytest
import requests

from src.data.providers.eastmoney import (
    EASTMONEY_KLINE_URL,
    get_daily_kline,
)


def make_response(payload: object) -> Mock:
    """Create a mocked successful requests response."""
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


@patch("src.data.providers.eastmoney.requests.get")
def test_get_daily_kline_returns_standard_dataframe(
    mock_get: Mock,
) -> None:
    mock_get.return_value = make_response(
        {
            "rc": 0,
            "data": {
                "klines": [
                    (
                        "2026-07-16,"
                        "18.00,18.25,18.50,17.90,"
                        "123456,2250000.0,0,0,0,0"
                    )
                ]
            },
        }
    )

    result = get_daily_kline("000021")

    assert isinstance(result, pd.DataFrame)
    assert result.columns.tolist() == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ]
    assert result.shape == (1, 7)

    row = result.iloc[0]

    assert row["date"] == "2026-07-16"
    assert row["open"] == 18.00
    assert row["high"] == 18.50
    assert row["low"] == 17.90
    assert row["close"] == 18.25
    assert row["volume"] == 123456
    assert row["amount"] == 2250000.0

    mock_get.assert_called_once_with(
        EASTMONEY_KLINE_URL,
        params={
            "secid": "0.000021",
            "klt": "101",
            "fqt": "1",
            "lmt": "100",
            "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": (
                "f51,f52,f53,f54,f55,"
                "f56,f57,f58,f59,f60,f61"
            ),
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )


@patch("src.data.providers.eastmoney.requests.get")
def test_get_daily_kline_uses_shanghai_market_code(
    mock_get: Mock,
) -> None:
    mock_get.return_value = make_response(
        {
            "rc": 0,
            "data": {
                "klines": [
                    "2026-07-16,10,11,12,9,100,1000"
                ]
            },
        }
    )

    get_daily_kline("600000")

    params = mock_get.call_args.kwargs["params"]
    assert params["secid"] == "1.600000"


@pytest.mark.parametrize(
    "symbol",
    [
        "21",
        "00002A",
        123456,
        None,
        "",
    ],
)
def test_get_daily_kline_rejects_invalid_symbol(
    symbol: object,
) -> None:
    with pytest.raises(ValueError, match="Invalid stock code"):
        get_daily_kline(symbol)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "symbol",
    [
        "400001",
        "800001",
        "900001",
    ],
)
def test_get_daily_kline_rejects_unsupported_market(
    symbol: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="Unsupported A-share stock code",
    ):
        get_daily_kline(symbol)


@patch("src.data.providers.eastmoney.requests.get")
def test_get_daily_kline_converts_network_error(
    mock_get: Mock,
) -> None:
    mock_get.side_effect = requests.ConnectionError(
        "connection failed"
    )

    with pytest.raises(
        RuntimeError,
        match="Unable to retrieve EastMoney K-line data",
    ):
        get_daily_kline("000021")


@patch("src.data.providers.eastmoney.requests.get")
def test_get_daily_kline_converts_http_error(
    mock_get: Mock,
) -> None:
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError(
        "500 Server Error"
    )
    mock_get.return_value = response

    with pytest.raises(
        RuntimeError,
        match="Unable to retrieve EastMoney K-line data",
    ):
        get_daily_kline("000021")


@patch("src.data.providers.eastmoney.requests.get")
def test_get_daily_kline_rejects_invalid_json(
    mock_get: Mock,
) -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("invalid JSON")
    mock_get.return_value = response

    with pytest.raises(
        RuntimeError,
        match="EastMoney returned invalid JSON",
    ):
        get_daily_kline("000021")


@pytest.mark.parametrize(
    "payload, expected_message",
    [
        (
            {"rc": 1, "data": None},
            "EastMoney returned an invalid response",
        ),
        (
            {"rc": 0, "data": None},
            "EastMoney returned no K-line data",
        ),
        (
            {"rc": 0, "data": {}},
            "EastMoney returned no K-line records",
        ),
        (
            {"rc": 0, "data": {"klines": []}},
            "EastMoney returned no K-line records",
        ),
    ],
)
@patch("src.data.providers.eastmoney.requests.get")
def test_get_daily_kline_rejects_invalid_payload(
    mock_get: Mock,
    payload: object,
    expected_message: str,
) -> None:
    mock_get.return_value = make_response(payload)

    with pytest.raises(RuntimeError, match=expected_message):
        get_daily_kline("000021")


@patch("src.data.providers.eastmoney.requests.get")
def test_get_daily_kline_rejects_malformed_record(
    mock_get: Mock,
) -> None:
    mock_get.return_value = make_response(
        {
            "rc": 0,
            "data": {
                "klines": [
                    "2026-07-16,18.00"
                ]
            },
        }
    )

    with pytest.raises(
        RuntimeError,
        match="EastMoney returned malformed K-line data",
    ):
        get_daily_kline("000021")
