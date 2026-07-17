"""Tests for stock market data retrieval."""

from unittest.mock import patch

import pandas as pd
import pytest

from src.data.stock import Stock, get_stock


@patch("src.data.stock.ak.stock_zh_a_spot_em")
def test_get_stock_returns_stock(mock_stock_data: object) -> None:
    """Return normalized quote data when the stock exists."""
    mock_stock_data.return_value = pd.DataFrame(
        {
            "代码": ["000021"],
            "名称": ["深科技"],
            "最新价": [18.25],
            "涨跌幅": [1.5],
            "成交量": [123456],
            "成交额": [2250000.0],
        }
    )

    result = get_stock("000021")

    assert result == Stock("深科技", 18.25, 1.5, 123456, 2250000.0)


@patch("src.data.stock.ak.stock_zh_a_spot_em")
def test_get_stock_raises_value_error_for_unknown_symbol(
    mock_stock_data: object,
) -> None:
    """Raise ValueError when no returned row matches the stock code."""
    mock_stock_data.return_value = pd.DataFrame(
        columns=["代码", "名称", "最新价", "涨跌幅", "成交量", "成交额"]
    )

    with pytest.raises(ValueError, match="does not exist"):
        get_stock("999999")


@patch("src.data.stock.ak.stock_zh_a_spot_em")
def test_get_stock_raises_runtime_error_for_network_failure(
    mock_stock_data: object,
) -> None:
    """Translate data retrieval failures into RuntimeError."""
    mock_stock_data.side_effect = ConnectionError("network unavailable")

    with pytest.raises(RuntimeError, match="Unable to retrieve"):
        get_stock("000021")


def test_get_stock_rejects_invalid_symbol() -> None:
    """Reject malformed stock codes before requesting market data."""
    with pytest.raises(ValueError, match="Invalid stock code"):
        get_stock("21")
