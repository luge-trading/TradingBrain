"""EastMoney provider for Shanghai plus Shenzhen market breadth."""
from __future__ import annotations

from collections.abc import Callable
import time
from typing import Final

import requests

from src.data.market import EASTMONEY_BREADTH_SOURCE, MarketBreadth


EASTMONEY_MARKET_BREADTH_URL: Final[str] = "https://push2.eastmoney.com/api/qt/ulist.np/get"
RETRYABLE_HTTP_STATUS_CODES: Final[set[int]] = {429, 500, 502, 503, 504}
TARGETS: Final[dict[tuple[int, str], str]] = {
    (1, "000001"): "Shanghai",
    (0, "399001"): "Shenzhen",
}


def _parse_count(value: object, field: str, market: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"invalid {field} for {market}")
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            raise ValueError(f"invalid {field} for {market}")
        result = int(value)
    elif isinstance(value, int):
        result = value
    else:
        raise ValueError(f"invalid {field} for {market}")
    if result < 0:
        raise ValueError(f"invalid {field} for {market}")
    return result


def get_market_breadth(
    *,
    retries: int = 3,
    backoff_base: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> MarketBreadth:
    """Return the sum of independently validated Shanghai and Shenzhen counts."""
    if isinstance(retries, bool) or not isinstance(retries, int) or retries <= 0:
        raise ValueError(f"Invalid retries: {retries!r}")
    if isinstance(backoff_base, bool) or not isinstance(backoff_base, (int, float)) or backoff_base < 0:
        raise ValueError(f"Invalid backoff_base: {backoff_base!r}")

    response = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                EASTMONEY_MARKET_BREADTH_URL,
                params={
                    "secids": "1.000001,0.399001",
                    "fields": "f12,f13,f104,f105,f106",
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()
            break
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in RETRYABLE_HTTP_STATUS_CODES and attempt < retries:
                sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            raise RuntimeError(
                f"Unable to retrieve {EASTMONEY_BREADTH_SOURCE} after {attempt} attempts: {exc}"
            ) from exc
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt < retries:
                sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            raise RuntimeError(
                f"Unable to retrieve {EASTMONEY_BREADTH_SOURCE} after {attempt} attempts: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Unable to retrieve {EASTMONEY_BREADTH_SOURCE} after {attempt} attempts: {exc}"
            ) from exc

    try:
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("rc") != 0:
            raise ValueError("invalid EastMoney response")
        data = payload.get("data")
        rows = data.get("diff") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError("data.diff is not a list")
        parsed: dict[tuple[int, str], tuple[int, int, int]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("breadth item is not an object")
            key = (row.get("f13"), str(row.get("f12")))
            if key not in TARGETS:
                continue
            if key in parsed:
                raise ValueError(f"duplicate {TARGETS[key]} breadth record")
            market = TARGETS[key]
            parsed[key] = tuple(_parse_count(row.get(field), field, market) for field in ("f104", "f105", "f106"))
        missing = [name for key, name in TARGETS.items() if key not in parsed]
        if missing:
            raise ValueError(f"missing breadth records: {', '.join(missing)}")
        totals = tuple(sum(parsed[key][position] for key in TARGETS) for position in range(3))
        return MarketBreadth(*totals)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Invalid {EASTMONEY_BREADTH_SOURCE} response after {attempt} attempts: {exc}"
        ) from exc
