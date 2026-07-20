"""EastMoney market data provider."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import logging
import time

import pandas as pd
import numpy as np
import requests

from src.data.index import (
    INDEX_KLINE_COLUMNS,
    get_index_definition,
    normalize_index_daily_kline,
)

logger = logging.getLogger(__name__)

EASTMONEY_KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
)

DEFAULT_EASTMONEY_RETRIES = 3
DEFAULT_BACKOFF_BASE = 0.5
RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}

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


def _should_retry_request(exc: BaseException, response: requests.Response | None = None) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True

    if response is not None:
        status = response.status_code
        return status in RETRYABLE_HTTP_STATUS_CODES

    return False


def _sleep_backoff(attempt: int, base_delay: float, sleep_func: callable) -> None:
    delay = base_delay * (2 ** (attempt - 1))
    logger.debug(
        "EastMoney retry sleep: attempt=%s delay=%s", attempt, delay
    )
    sleep_func(delay)


def _fetch_once(
    symbol: str,
    params: dict[str, str],
) -> requests.Response:
    response = requests.get(
        EASTMONEY_KLINE_URL,
        params=params,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    response.raise_for_status()
    return response


def _raise_fetch_error(symbol: str, attempt: int, exc: BaseException) -> None:
    raise RuntimeError(
        f"Unable to retrieve EastMoney K-line data for {symbol} after {attempt} attempts: {exc}"
    ) from exc


def get_daily_kline(
    symbol: str,
    *,
    limit: int = 100,
    retries: int = DEFAULT_EASTMONEY_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    sleep: callable = time.sleep,
) -> pd.DataFrame:
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

    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit <= 0
    ):
        raise ValueError(f"Invalid K-line limit: {limit!r}")

    if (
        not isinstance(retries, int)
        or isinstance(retries, bool)
        or retries <= 0
    ):
        raise ValueError(f"Invalid retries: {retries!r}")

    if (
        not isinstance(backoff_base, (int, float))
        or isinstance(backoff_base, bool)
        or backoff_base < 0
    ):
        raise ValueError(f"Invalid backoff_base: {backoff_base!r}")

    params = {
        "secid": f"{market}.{symbol}",
        "klt": "101",
        "fqt": "1",
        "lmt": str(limit),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": (
            "f51,f52,f53,f54,f55,"
            "f56,f57,f58,f59,f60,f61"
        ),
    }

    attempt = 0
    response = None
    last_exc: BaseException | None = None
    while attempt < retries:
        attempt += 1
        try:
            response = _fetch_once(symbol, params)
            break
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            if _should_retry_request(exc, response=response) and attempt < retries:
                logger.warning(
                    "EastMoney request retrying %s/%s for %s due to HTTP %s",
                    attempt,
                    retries,
                    symbol,
                    response.status_code if response is not None else "unknown",
                )
                _sleep_backoff(attempt, backoff_base, sleep)
                last_exc = exc
                continue
            _raise_fetch_error(symbol, attempt, exc)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt < retries:
                logger.warning(
                    "EastMoney request retrying %s/%s for %s due to %s",
                    attempt,
                    retries,
                    symbol,
                    type(exc).__name__,
                )
                _sleep_backoff(attempt, backoff_base, sleep)
                last_exc = exc
                continue
            _raise_fetch_error(symbol, attempt, exc)
        except requests.RequestException as exc:
            _raise_fetch_error(symbol, attempt, exc)

    if response is None:
        _raise_fetch_error(symbol, attempt, last_exc or RuntimeError("unknown error"))

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


def _raise_index_fetch_error(index_code: str, attempt: int, exc: BaseException) -> None:
    raise RuntimeError(
        f"Unable to retrieve eastmoney index K-line data for {index_code} "
        f"after {attempt} attempts: {exc}"
    ) from exc


def get_index_daily_kline(
    index_code: str,
    *,
    limit: int = 100,
    retries: int = DEFAULT_EASTMONEY_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    sleep: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Return validated daily K-lines for one of the supported indexes."""
    definition = get_index_definition(index_code)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError(f"Invalid K-line limit: {limit!r}")
    if not isinstance(retries, int) or isinstance(retries, bool) or retries <= 0:
        raise ValueError(f"Invalid retries: {retries!r}")
    if not isinstance(backoff_base, (int, float)) or isinstance(backoff_base, bool) or not np.isfinite(backoff_base) or backoff_base < 0:
        raise ValueError(f"Invalid backoff_base: {backoff_base!r}")

    params = {
        "secid": definition.eastmoney_secid,
        "klt": "101", "fqt": "1", "lmt": str(limit), "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    attempt = 0
    response = None
    last_exc: BaseException | None = None
    while attempt < retries:
        attempt += 1
        try:
            response = _fetch_once(index_code, params)
            break
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            if _should_retry_request(exc, response=response) and attempt < retries:
                _sleep_backoff(attempt, backoff_base, sleep)
                last_exc = exc
                continue
            _raise_index_fetch_error(index_code, attempt, exc)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt < retries:
                _sleep_backoff(attempt, backoff_base, sleep)
                last_exc = exc
                continue
            _raise_index_fetch_error(index_code, attempt, exc)
        except requests.RequestException as exc:
            _raise_index_fetch_error(index_code, attempt, exc)

    if response is None:
        _raise_index_fetch_error(index_code, attempt, last_exc or RuntimeError("unknown error"))
    try:
        result = response.json()
        if not isinstance(result, dict) or result.get("rc") != 0:
            raise ValueError("invalid response")
        data = result.get("data")
        if not isinstance(data, dict) or "klines" not in data:
            raise ValueError("missing data.klines")
        klines = data["klines"]
        if not isinstance(klines, list):
            raise ValueError("data.klines is not a list")
        if not klines:
            return pd.DataFrame(columns=list(INDEX_KLINE_COLUMNS))
        records = []
        for item in klines:
            if not isinstance(item, str):
                raise ValueError("K-line record is not a string")
            fields = item.split(",")
            if len(fields) < 7:
                raise ValueError("K-line record has insufficient fields")
            records.append({
                "date": fields[0], "open": fields[1], "high": fields[3],
                "low": fields[4], "close": fields[2], "volume": fields[5],
                "amount": fields[6],
            })
        return normalize_index_daily_kline(pd.DataFrame(records, columns=INDEX_KLINE_COLUMNS))
    except Exception as exc:
        if isinstance(exc, RuntimeError) and str(exc).startswith("Unable to retrieve"):
            raise
        _raise_index_fetch_error(index_code, attempt, exc)
