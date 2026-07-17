"""EastMoney market data provider."""

from __future__ import annotations

from typing import Any

import pandas as pd
import requests


EASTMONEY_KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
)

KLINE_COLUMNS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
]


def _get_market_code(symbol: str) -> str:
    """Return the EastMoney market code for an A-share symbol."""
    if (
        not isinstance(symbol, str)
        or len(symbol) != 6
        or not symbol.isdigit()
    ):
        raise ValueError(f"Invalid stock code: {symbol!r}")

    if symbol.startswith(("0", "3")):
        return "0"

    if symbol.startswith("6"):
        return "1"

    raise ValueError(f"Unsupported A-share stock code: {symbol!r}")


def _parse_kline_records(klines: list[Any]) -> pd.DataFrame:
    """Parse EastMoney K-line strings into a standard DataFrame."""
    records: list[dict[str, object]] = []

    try:
        for item in klines:
            if not isinstance(item, str):
                raise ValueError("K-line record is not a string")

            fields = item.split(",")

            if len(fields) < 7:
                raise ValueError("K-line record has insufficient fields")

            records.append(
                {
                    "date": fields[0],
                    "open": float(fields[1]),
                    "high": float(fields[3]),
                    "low": float(fields[4]),
                    "close": float(fields[2]),
                    "volume": int(fields[5]),
                    "amount": float(fields[6]),
                }
            )
    except (TypeError, ValueError, IndexError) as exc:
        raise RuntimeError(
            "EastMoney returned malformed K-line data"
        ) from exc

    return pd.DataFrame(records, columns=KLINE_COLUMNS)


def get_daily_kline(symbol: str) -> pd.DataFrame:
    """Return recent daily K-line data for an A-share stock.

    Args:
        symbol: Six-digit A-share stock code, for example ``"000021"``.

    Returns:
        A DataFrame with the columns:
        date, open, high, low, close, volume, amount.

    Raises:
        ValueError: If the stock code format or market is unsupported.
        RuntimeError: If EastMoney cannot be reached or returns invalid data.
    """
    market = _get_market_code(symbol)

    params = {
        "secid": f"{market}.{symbol}",
        "klt": "101",
        "fqt": "1",
        "lmt": "100",
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": (
            "f51,f52,f53,f54,f55,"
            "f56,f57,f58,f59,f60,f61"
        ),
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
    }

    try:
        response = requests.get(
            EASTMONEY_KLINE_URL,
            params=params,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            "Unable to retrieve EastMoney K-line data"
        ) from exc

    try:
        result = response.json()
    except (requests.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            "EastMoney returned invalid JSON"
        ) from exc

    if not isinstance(result, dict) or result.get("rc") != 0:
        raise RuntimeError("EastMoney returned an invalid response")

    data = result.get("data")

    if not isinstance(data, dict):
        raise RuntimeError("EastMoney returned no K-line data")

    klines = data.get("klines")

    if not isinstance(klines, list) or not klines:
        raise RuntimeError("EastMoney returned no K-line records")

    return _parse_kline_records(klines)
