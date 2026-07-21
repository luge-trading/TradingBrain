"""EastMoney industry registry and unadjusted daily K-line provider."""
from __future__ import annotations

from collections.abc import Callable
import math
import time
from typing import Final

import pandas as pd
import requests

from src.data.sector import (
    EASTMONEY_INDUSTRY_KLINE_SOURCE,
    EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    EASTMONEY_INDUSTRY_SECTOR_TYPE,
    SECTOR_KLINE_COLUMNS,
    SectorDefinition,
    normalize_sector_daily_kline,
    normalize_sector_registry,
    validate_sector_level,
)


EASTMONEY_SECTOR_LIST_URL: Final[str] = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_SECTOR_KLINE_URL: Final[str] = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_SECTOR_PAGE_SIZE: Final[int] = 100
EASTMONEY_SECTOR_FILTERS: Final[dict[int, str]] = {
    1: "m:90 s:2 f:!50",
    2: "m:90 s:4 f:!50",
    3: "m:90 s:8 f:!50",
}
RETRYABLE_HTTP_STATUS_CODES: Final[set[int]] = {429, 500, 502, 503, 504}


def _validate_request_options(retries: int, backoff_base: float) -> None:
    if isinstance(retries, bool) or not isinstance(retries, int) or retries <= 0:
        raise ValueError(f"Invalid retries: {retries!r}")
    if (
        isinstance(backoff_base, bool)
        or not isinstance(backoff_base, (int, float))
        or not math.isfinite(backoff_base)
        or backoff_base < 0
    ):
        raise ValueError(f"Invalid backoff_base: {backoff_base!r}")


def _request_json(
    url: str,
    params: dict[str, str],
    *,
    target: str,
    retries: int,
    backoff_base: float,
    sleep: Callable[[float], None],
) -> tuple[object, int]:
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in RETRYABLE_HTTP_STATUS_CODES and attempt < retries:
                sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            raise RuntimeError(
                f"Unable to retrieve eastmoney {target} after {attempt} attempts: {exc}"
            ) from exc
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt < retries:
                sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            raise RuntimeError(
                f"Unable to retrieve eastmoney {target} after {attempt} attempts: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Unable to retrieve eastmoney {target} after {attempt} attempts: {exc}"
            ) from exc
        try:
            return response.json(), attempt
        except (requests.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(
                f"Invalid eastmoney {target} response after {attempt} attempts: invalid JSON"
            ) from exc
    raise AssertionError("unreachable")


def _parse_registry_page(
    payload: object,
    *,
    sector_level: int,
    page: int,
    attempts: int,
) -> tuple[int, list[object]]:
    target = f"industry level {sector_level} registry page {page}"
    try:
        if not isinstance(payload, dict) or payload.get("rc") != 0:
            raise ValueError("invalid response code")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("data is not an object")
        total = data.get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ValueError("data.total is not a non-negative integer")
        diff = data.get("diff")
        if not isinstance(diff, list):
            raise ValueError("data.diff is not a list")
        return total, diff
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid eastmoney {target} response after {attempts} attempts: {exc}"
        ) from exc


def get_industry_sector_list(
    sector_level: int,
    *,
    retries: int = 3,
    backoff_base: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[SectorDefinition, ...]:
    """Return one complete EastMoney industry level using strict pagination."""
    level = validate_sector_level(sector_level)
    _validate_request_options(retries, backoff_base)
    definitions: list[SectorDefinition] = []
    seen_codes: set[str] = set()
    expected_total: int | None = None
    total_pages: int | None = None
    page = 1

    while total_pages is None or page <= total_pages:
        params = {
            "pn": str(page),
            "pz": str(EASTMONEY_SECTOR_PAGE_SIZE),
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": EASTMONEY_SECTOR_FILTERS[level],
            "fields": "f12,f14",
        }
        payload, attempts = _request_json(
            EASTMONEY_SECTOR_LIST_URL,
            params,
            target=f"industry level {level} registry page {page}",
            retries=retries,
            backoff_base=backoff_base,
            sleep=sleep,
        )
        total, rows = _parse_registry_page(
            payload, sector_level=level, page=page, attempts=attempts
        )
        try:
            if expected_total is None:
                if total == 0:
                    raise ValueError("data.total must be positive")
                expected_total = total
                total_pages = math.ceil(total / EASTMONEY_SECTOR_PAGE_SIZE)
            elif total != expected_total:
                raise ValueError(f"data.total changed from {expected_total} to {total}")
            if not rows and len(definitions) < expected_total:
                raise ValueError("empty page before reaching data.total")
            page_codes: set[str] = set()
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("registry item is not an object")
                definition = SectorDefinition(
                    EASTMONEY_INDUSTRY_SECTOR_TYPE,
                    level,
                    row.get("f12"),
                    row.get("f14"),
                    EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
                )
                if definition.sector_code in page_codes:
                    raise ValueError(f"duplicate code within page: {definition.sector_code}")
                if definition.sector_code in seen_codes:
                    raise ValueError(f"duplicate code across pages: {definition.sector_code}")
                page_codes.add(definition.sector_code)
                seen_codes.add(definition.sector_code)
                definitions.append(definition)
            if len(definitions) > expected_total:
                raise ValueError("unique record count exceeds data.total")
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Invalid eastmoney industry level {level} registry page {page} "
                f"response after {attempts} attempts: {exc}"
            ) from exc
        page += 1

    if expected_total is None or len(definitions) != expected_total:
        raise RuntimeError(
            f"Invalid eastmoney industry level {level} registry response after 1 attempts: "
            f"unique record count {len(definitions)} does not equal data.total {expected_total}"
        )
    return normalize_sector_registry(definitions)


def get_sector_daily_kline(
    definition: SectorDefinition,
    *,
    limit: int = 100,
    retries: int = 3,
    backoff_base: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Return validated unadjusted daily K-lines for one registered sector."""
    if not isinstance(definition, SectorDefinition):
        raise TypeError("definition must be a SectorDefinition")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError(f"Invalid K-line limit: {limit!r}")
    _validate_request_options(retries, backoff_base)
    params = {
        "secid": f"90.{definition.sector_code}",
        "klt": "101",
        "fqt": "0",
        "lmt": str(limit),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    target = f"sector {definition.sector_code} K-line"
    payload, attempts = _request_json(
        EASTMONEY_SECTOR_KLINE_URL,
        params,
        target=target,
        retries=retries,
        backoff_base=backoff_base,
        sleep=sleep,
    )
    try:
        if not isinstance(payload, dict) or payload.get("rc") != 0:
            raise ValueError("invalid response code")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("data is not an object")
        if data.get("code") != definition.sector_code:
            raise ValueError("response code does not match definition")
        name = data.get("name")
        if not isinstance(name, str) or name.strip() != definition.sector_name:
            raise ValueError("response name does not match definition")
        if "klines" not in data:
            raise ValueError("missing data.klines")
        klines = data["klines"]
        if not isinstance(klines, list):
            raise ValueError("data.klines is not a list")
        records: list[dict[str, object]] = []
        for item in klines:
            if not isinstance(item, str):
                raise ValueError("K-line record is not a string")
            fields = item.split(",")
            if len(fields) != 11:
                raise ValueError("K-line record must contain exactly 11 fields")
            records.append({
                "date": fields[0],
                "open": fields[1],
                "high": fields[3],
                "low": fields[4],
                "close": fields[2],
                "volume": fields[5],
                "amount": fields[6],
                "change_pct": fields[8],
            })
        frame = pd.DataFrame(records, columns=SECTOR_KLINE_COLUMNS)
        return normalize_sector_daily_kline(frame)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Invalid eastmoney {target} response after {attempts} attempts: {exc}"
        ) from exc
