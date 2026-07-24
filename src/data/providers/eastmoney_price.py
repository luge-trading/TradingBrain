"""Strict single-security EastMoney daily-price provider.

The ``fqt=0/1/2`` mapping is an operational project contract inferred from
the installed third-party implementation and legacy behavior. It is not
claimed as an official EastMoney definition and must be verified by the
controlled TASK-012E-2E network probe.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from http.client import RemoteDisconnected
from math import isfinite
from numbers import Real
import socket
import time
from types import MappingProxyType
from typing import Any, Final, Mapping
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from urllib3.exceptions import NameResolutionError, ProtocolError

from src.data.price import (
    STOCK_DAILY_PRICE_COLUMNS,
    PriceProviderError,
    PriceProviderErrorCode,
    normalize_stock_daily_prices,
    validate_price_adjustment,
    validate_price_date,
)
from src.data.security import SecurityIdentity


EASTMONEY_PRICE_URL: Final[str] = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
)
EASTMONEY_PRICE_SOURCE: Final[str] = "EASTMONEY"
EASTMONEY_PRICE_FIELDS1: Final[str] = "f1,f2,f3,f4,f5,f6"
EASTMONEY_PRICE_FIELDS2: Final[str] = (
    "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
)
EASTMONEY_PRICE_USER_AGENT: Final[str] = "Mozilla/5.0"
_EASTMONEY_MARKETS: Final[Mapping[str, int]] = MappingProxyType(
    {"XSHE": 0, "XSHG": 1}
)
_EASTMONEY_ADJUSTMENTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "UNADJUSTED": "0",
        "QFQ": "1",
        "HFQ": "2",
    }
)
RETRYABLE_HTTP_STATUS_CODES: Final[frozenset[int]] = frozenset(
    {429, 500, 502, 503, 504}
)
SHANGHAI_TIMEZONE: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")


def _provider_error(
    code: PriceProviderErrorCode,
    message: str,
    *,
    attempts: int,
    retryable: bool = False,
    batch_signal: bool = False,
    status_code: int | None = None,
) -> PriceProviderError:
    return PriceProviderError(
        message,
        provider=EASTMONEY_PRICE_SOURCE,
        code=code,
        retryable=retryable,
        batch_signal=batch_signal,
        attempts=attempts,
        status_code=status_code,
    )


def _walk_exception_graph(exc: BaseException) -> tuple[BaseException, ...]:
    """Return each cause/context node once without relying on graph order."""
    nodes: list[BaseException] = []
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        nodes.append(current)
        for linked in (current.__cause__, current.__context__):
            if linked is not None and id(linked) not in seen:
                pending.append(linked)
    return tuple(nodes)


def _classify_request_exception(
    exc: requests.RequestException,
    *,
    attempts: int,
) -> PriceProviderError:
    graph = _walk_exception_graph(exc)
    if any(isinstance(item, requests.exceptions.ProxyError) for item in graph):
        return _provider_error(
            PriceProviderErrorCode.PROXY_UNAVAILABLE,
            "EastMoney proxy is unavailable",
            attempts=attempts,
            batch_signal=True,
        )
    configuration_errors = (
        requests.exceptions.InvalidProxyURL,
        requests.exceptions.InvalidURL,
        requests.exceptions.MissingSchema,
        requests.exceptions.InvalidSchema,
    )
    if any(isinstance(item, configuration_errors) for item in graph):
        return _provider_error(
            PriceProviderErrorCode.NETWORK_CONFIGURATION,
            "EastMoney request configuration is invalid",
            attempts=attempts,
            batch_signal=True,
        )
    timeout_errors = (
        requests.exceptions.ConnectTimeout,
        requests.exceptions.ReadTimeout,
        requests.exceptions.Timeout,
    )
    if any(isinstance(item, timeout_errors) for item in graph):
        return _provider_error(
            PriceProviderErrorCode.TIMEOUT,
            "EastMoney request timed out",
            attempts=attempts,
            retryable=True,
        )

    if any(isinstance(item, (NameResolutionError, socket.gaierror)) for item in graph):
        return _provider_error(
            PriceProviderErrorCode.DNS_FAILURE,
            "EastMoney host name resolution failed",
            attempts=attempts,
            retryable=True,
            batch_signal=True,
        )
    if any(isinstance(item, (RemoteDisconnected, ProtocolError)) for item in graph):
        return _provider_error(
            PriceProviderErrorCode.CONNECTION_CLOSED,
            "EastMoney connection closed unexpectedly",
            attempts=attempts,
            retryable=True,
        )
    if any(isinstance(item, requests.ConnectionError) for item in graph):
        return _provider_error(
            PriceProviderErrorCode.CONNECTION_CLOSED,
            "EastMoney connection failed",
            attempts=attempts,
            retryable=True,
        )
    return _provider_error(
        PriceProviderErrorCode.NETWORK_CONFIGURATION,
        "EastMoney request failed",
        attempts=attempts,
        batch_signal=True,
    )


def _classify_http_error(
    exc: requests.HTTPError,
    *,
    attempts: int,
) -> PriceProviderError:
    response = exc.response
    status_code = response.status_code if response is not None else None
    if status_code in RETRYABLE_HTTP_STATUS_CODES:
        return _provider_error(
            PriceProviderErrorCode.HTTP_RETRYABLE,
            f"EastMoney returned retryable HTTP status {status_code}",
            attempts=attempts,
            retryable=True,
            batch_signal=True,
            status_code=status_code,
        )
    return _provider_error(
        PriceProviderErrorCode.HTTP_FINAL,
        (
            "EastMoney returned a non-retryable HTTP status"
            if status_code is None
            else f"EastMoney returned non-retryable HTTP status {status_code}"
        ),
        attempts=attempts,
        batch_signal=status_code in {401, 403},
        status_code=status_code,
    )


def _validate_observed_at(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("observed_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _validate_positive_finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a finite positive number")
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return result


def _validate_timeout(value: object) -> tuple[float, float]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError("timeout must be a two-item tuple")
    _validate_positive_finite(value[0], "connect timeout")
    _validate_positive_finite(value[1], "read timeout")
    return value


def _strict_float(value: str, field: str) -> float:
    if not value or value != value.strip():
        raise ValueError(f"Invalid {field}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}") from exc
    if not isfinite(result):
        raise ValueError(f"Invalid {field}")
    return result


def _strict_volume(value: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError("Invalid volume")
    return int(value)


def _parse_records(
    klines: list[Any],
    *,
    identity: SecurityIdentity,
    adjustment: str,
    provider_adjustment: str,
    effective_start: date,
    effective_end: date,
    observed_at: datetime,
    attempts: int,
) -> pd.DataFrame:
    observed_date = observed_at.astimezone(SHANGHAI_TIMEZONE).date()
    records: list[dict[str, object]] = []
    seen_dates: set[str] = set()

    try:
        for item in klines:
            if not isinstance(item, str):
                raise ValueError("K-line record must be a string")
            fields = item.split(",")
            if len(fields) < 7:
                raise ValueError("K-line record has insufficient fields")

            trade_date_text = validate_price_date(fields[0])
            trade_date_value = date.fromisoformat(trade_date_text)
            if not effective_start <= trade_date_value <= effective_end:
                raise ValueError("K-line date is outside the effective query range")
            if trade_date_text in seen_dates:
                raise ValueError("Duplicate K-line trade_date")
            seen_dates.add(trade_date_text)

            open_price = _strict_float(fields[1], "open")
            close_price = _strict_float(fields[2], "close")
            high_price = _strict_float(fields[3], "high")
            low_price = _strict_float(fields[4], "low")
            if min(open_price, close_price, high_price, low_price) <= 0:
                raise ValueError("OHLC prices must be positive")
            if not low_price <= open_price <= high_price:
                raise ValueError("Invalid OHLC relationship for open")
            if not low_price <= close_price <= high_price:
                raise ValueError("Invalid OHLC relationship for close")

            volume = _strict_volume(fields[5])
            raw_amount = fields[6]
            if raw_amount in {"", "-"}:
                amount = None
                amount_unit = None
            else:
                amount = _strict_float(raw_amount, "amount")
                if amount < 0:
                    raise ValueError("amount cannot be negative")
                amount_unit = "PROVIDER_NATIVE"

            if trade_date_value > observed_date:
                raise ValueError("K-line date is after the Shanghai observation date")
            records.append(
                {
                    "security_id": identity.security_id,
                    "trade_date": trade_date_text,
                    "adjustment": adjustment,
                    "source": EASTMONEY_PRICE_SOURCE,
                    "provider_adjustment": provider_adjustment,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": volume,
                    "volume_unit": "PROVIDER_NATIVE",
                    "amount": amount,
                    "amount_unit": amount_unit,
                    "is_final": trade_date_value < observed_date,
                    "provider_as_of_date": None,
                    "observed_at": observed_at.isoformat(),
                }
            )
    except (TypeError, ValueError, IndexError) as exc:
        raise _provider_error(
            PriceProviderErrorCode.INVALID_DATA,
            "EastMoney returned invalid daily-price data",
            attempts=attempts,
        ) from exc

    frame = pd.DataFrame(records, columns=STOCK_DAILY_PRICE_COLUMNS)
    try:
        return normalize_stock_daily_prices(frame)
    except (TypeError, ValueError) as exc:
        raise _provider_error(
            PriceProviderErrorCode.INVALID_DATA,
            "EastMoney daily-price data violates the price contract",
            attempts=attempts,
        ) from exc


def fetch_stock_daily_prices(
    identity: SecurityIdentity,
    *,
    adjustment: str,
    start_date: str,
    end_date: str,
    observed_at: datetime | None = None,
    max_attempts: int = 3,
    backoff_base: float = 0.5,
    timeout: tuple[float, float] = (5.0, 10.0),
    http_get: Callable[..., Any] = requests.get,
    sleep: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Fetch one explicit security and return normalized daily-price facts."""
    if not isinstance(identity, SecurityIdentity):
        raise TypeError("identity must be a SecurityIdentity")
    adjustment_value = validate_price_adjustment(adjustment)
    start_text = validate_price_date(start_date, field="start_date")
    end_text = validate_price_date(end_date, field="end_date")
    requested_start = date.fromisoformat(start_text)
    requested_end = date.fromisoformat(end_text)
    if requested_start > requested_end:
        raise ValueError("start_date must not be after end_date")
    observation = _validate_observed_at(observed_at)
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts <= 0
    ):
        raise ValueError("max_attempts must be a positive integer")
    if (
        isinstance(backoff_base, bool)
        or not isinstance(backoff_base, Real)
        or not isfinite(float(backoff_base))
        or backoff_base < 0
    ):
        raise ValueError("backoff_base must be finite and non-negative")
    timeout_value = _validate_timeout(timeout)
    if not callable(http_get):
        raise TypeError("http_get must be callable")
    if not callable(sleep):
        raise TypeError("sleep must be callable")

    listed_on = date.fromisoformat(identity.list_date)
    delisted_on = (
        date.fromisoformat(identity.delist_date)
        if identity.delist_date is not None
        else requested_end
    )
    effective_start = max(requested_start, listed_on)
    effective_end = min(requested_end, delisted_on)
    if effective_start > effective_end:
        raise _provider_error(
            PriceProviderErrorCode.NO_DATA,
            "Requested dates do not overlap the security listing interval",
            attempts=0,
        )

    market = _EASTMONEY_MARKETS[identity.exchange]
    fqt = _EASTMONEY_ADJUSTMENTS[adjustment_value]
    params = {
        "secid": f"{market}.{identity.local_symbol}",
        "klt": "101",
        "fqt": fqt,
        "beg": effective_start.strftime("%Y%m%d"),
        "end": effective_end.strftime("%Y%m%d"),
        "lmt": str((effective_end - effective_start).days + 1),
        "fields1": EASTMONEY_PRICE_FIELDS1,
        "fields2": EASTMONEY_PRICE_FIELDS2,
    }

    response: Any = None
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        try:
            response = http_get(
                EASTMONEY_PRICE_URL,
                params=params,
                headers={"User-Agent": EASTMONEY_PRICE_USER_AGENT},
                timeout=timeout_value,
            )
            response.raise_for_status()
            break
        except requests.HTTPError as exc:
            error = _classify_http_error(exc, attempts=attempts)
            if error.retryable and attempts < max_attempts:
                sleep(float(backoff_base) * 2 ** (attempts - 1))
                continue
            raise error from exc
        except requests.RequestException as exc:
            error = _classify_request_exception(exc, attempts=attempts)
            if error.retryable and attempts < max_attempts:
                sleep(float(backoff_base) * 2 ** (attempts - 1))
                continue
            raise error from exc

    try:
        payload = response.json()
    except (requests.JSONDecodeError, ValueError) as exc:
        raise _provider_error(
            PriceProviderErrorCode.INVALID_JSON,
            "EastMoney returned invalid JSON",
            attempts=attempts,
        ) from exc

    if not isinstance(payload, dict):
        raise _provider_error(
            PriceProviderErrorCode.INVALID_SCHEMA,
            "EastMoney response must be an object",
            attempts=attempts,
        )
    if "rc" not in payload or isinstance(payload["rc"], bool) or not isinstance(
        payload["rc"], int
    ):
        raise _provider_error(
            PriceProviderErrorCode.INVALID_SCHEMA,
            "EastMoney response has an invalid rc field",
            attempts=attempts,
        )
    if payload["rc"] != 0:
        raise _provider_error(
            PriceProviderErrorCode.PROVIDER_REJECTED,
            "EastMoney rejected the price request",
            attempts=attempts,
        )
    data = payload.get("data")
    if data is None:
        raise _provider_error(
            PriceProviderErrorCode.NO_DATA,
            "EastMoney returned no price data",
            attempts=attempts,
        )
    if not isinstance(data, dict):
        raise _provider_error(
            PriceProviderErrorCode.INVALID_SCHEMA,
            "EastMoney data must be an object",
            attempts=attempts,
        )
    if not {"code", "market", "klines"}.issubset(data):
        raise _provider_error(
            PriceProviderErrorCode.INVALID_SCHEMA,
            "EastMoney data is missing required identity or K-line fields",
            attempts=attempts,
        )
    code = data["code"]
    market_value = data["market"]
    if (
        not isinstance(code, str)
        or len(code) != 6
        or not code.isascii()
        or not code.isdecimal()
        or isinstance(market_value, bool)
        or not isinstance(market_value, int)
        or market_value not in {0, 1}
    ):
        raise _provider_error(
            PriceProviderErrorCode.INVALID_SCHEMA,
            "EastMoney response identity fields are invalid",
            attempts=attempts,
        )
    if code != identity.local_symbol or market_value != market:
        raise _provider_error(
            PriceProviderErrorCode.IDENTITY_MISMATCH,
            "EastMoney response identity does not match the request",
            attempts=attempts,
        )
    klines = data["klines"]
    if not isinstance(klines, list):
        raise _provider_error(
            PriceProviderErrorCode.INVALID_SCHEMA,
            "EastMoney K-line data must be a list",
            attempts=attempts,
        )
    if not klines:
        raise _provider_error(
            PriceProviderErrorCode.NO_DATA,
            "EastMoney returned no K-line records",
            attempts=attempts,
        )
    return _parse_records(
        klines,
        identity=identity,
        adjustment=adjustment_value,
        provider_adjustment=f"fqt={fqt}",
        effective_start=effective_start,
        effective_end=effective_end,
        observed_at=observation,
        attempts=attempts,
    )
