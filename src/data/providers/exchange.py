"""Official exchange providers for daily stock turnover."""
from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any, Final

import requests

from src.data.market import (
    SSE_AMOUNT_SOURCE,
    SZSE_AMOUNT_SOURCE,
    ExchangeDailyAmount,
    validate_trade_date,
    yi_yuan_to_yuan,
)


SSE_DAILY_OVERVIEW_URL: Final[str] = "https://query.sse.com.cn/commonQuery.do"
SZSE_DAILY_OVERVIEW_URL: Final[str] = "https://www.szse.cn/api/report/ShowReport/data"
RETRYABLE_HTTP_STATUS_CODES: Final[set[int]] = {429, 500, 502, 503, 504}


def _validate_retry_options(retries: int, backoff_base: float) -> None:
    if isinstance(retries, bool) or not isinstance(retries, int) or retries <= 0:
        raise ValueError(f"Invalid retries: {retries!r}")
    if (
        isinstance(backoff_base, bool)
        or not isinstance(backoff_base, (int, float))
        or backoff_base < 0
    ):
        raise ValueError(f"Invalid backoff_base: {backoff_base!r}")


def _request_json(
    source: str,
    trade_date: str,
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str],
    retries: int,
    backoff_base: float,
    sleep: Callable[[float], None],
) -> tuple[object, int]:
    _validate_retry_options(retries, backoff_base)
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in RETRYABLE_HTTP_STATUS_CODES and attempt < retries:
                sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            raise RuntimeError(
                f"Unable to retrieve {source} for {trade_date} after {attempt} attempts: {exc}"
            ) from exc
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt < retries:
                sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            raise RuntimeError(
                f"Unable to retrieve {source} for {trade_date} after {attempt} attempts: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Unable to retrieve {source} for {trade_date} after {attempt} attempts: {exc}"
            ) from exc
        try:
            return response.json(), attempt
        except (requests.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(
                f"Invalid {source} response for {trade_date} after {attempt} attempts: invalid JSON"
            ) from exc
    raise AssertionError("unreachable")


def _response_date(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("response date is missing")
    compact = value.strip().replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"invalid response date: {value!r}")
    return validate_trade_date(f"{compact[:4]}-{compact[4:6]}-{compact[6:]}")


def get_sse_daily_amount(
    trade_date: str,
    *,
    retries: int = 3,
    backoff_base: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> ExchangeDailyAmount:
    """Return official Shanghai stock turnover for one trade date."""
    trade_date = validate_trade_date(trade_date)
    payload, attempt = _request_json(
        SSE_AMOUNT_SOURCE,
        trade_date,
        SSE_DAILY_OVERVIEW_URL,
        params={
            "sqlId": "COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C",
            "PRODUCT_CODE": "01,02,03,11,17",
            "type": "inParams",
            "SEARCH_DATE": trade_date,
        },
        headers={
            "Referer": "https://www.sse.com.cn/market/stockdata/overview/day/",
            "User-Agent": "Mozilla/5.0",
        },
        retries=retries,
        backoff_base=backoff_base,
        sleep=sleep,
    )
    try:
        rows = payload["result"] if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("result is not a list")
        matches = [row for row in rows if isinstance(row, dict) and str(row.get("PRODUCT_CODE")) == "17"]
        if len(matches) != 1:
            raise ValueError("expected exactly one SSE stock-market total row")
        row = matches[0]
        if _response_date(row.get("TRADE_DATE")) != trade_date:
            raise ValueError("SSE response date does not match requested trade date")
        amount_yuan = yi_yuan_to_yuan(row.get("TRADE_AMT"))
        return ExchangeDailyAmount(trade_date, amount_yuan, SSE_AMOUNT_SOURCE)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Invalid {SSE_AMOUNT_SOURCE} response for {trade_date} after {attempt} attempts: {exc}"
        ) from exc


def _find_szse_condition(payload: dict[str, Any]) -> object:
    candidates = payload.get("conditions")
    if candidates is None and isinstance(payload.get("metadata"), dict):
        candidates = payload["metadata"].get("conditions")
    if not isinstance(candidates, list):
        raise ValueError("SZSE conditions are missing")
    matches = [item for item in candidates if isinstance(item, dict) and item.get("name") == "txtQueryDate"]
    if len(matches) != 1:
        raise ValueError("SZSE txtQueryDate condition is missing or duplicated")
    return matches[0].get("defaultValue")


def get_szse_daily_amount(
    trade_date: str,
    *,
    retries: int = 3,
    backoff_base: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> ExchangeDailyAmount:
    """Return official Shenzhen aggregate stock turnover for one trade date."""
    trade_date = validate_trade_date(trade_date)
    payload, attempt = _request_json(
        SZSE_AMOUNT_SOURCE,
        trade_date,
        SZSE_DAILY_OVERVIEW_URL,
        params={
            "SHOWTYPE": "JSON",
            "CATALOGID": "scsj_gprdgk_after",
            "TABKEY": "tab1",
            "txtQueryDate": trade_date,
        },
        headers={
            "Referer": "https://www.szse.cn/market/overview/index.html",
            "User-Agent": "Mozilla/5.0",
        },
        retries=retries,
        backoff_base=backoff_base,
        sleep=sleep,
    )
    try:
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise ValueError("expected one SZSE report result")
        report = payload[0]
        if _response_date(_find_szse_condition(report)) != trade_date:
            raise ValueError("SZSE metadata date does not match requested trade date")
        rows = report.get("data")
        if not isinstance(rows, list):
            raise ValueError("SZSE report data is not a list")
        matches = [row for row in rows if isinstance(row, dict) and row.get("zbmc") == "成交金额（亿元）"]
        if len(matches) != 1:
            raise ValueError("expected exactly one SZSE turnover row")
        amount_yuan = yi_yuan_to_yuan(matches[0].get("gp"))
        return ExchangeDailyAmount(trade_date, amount_yuan, SZSE_AMOUNT_SOURCE)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Invalid {SZSE_AMOUNT_SOURCE} response for {trade_date} after {attempt} attempts: {exc}"
        ) from exc
