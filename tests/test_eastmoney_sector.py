from unittest.mock import Mock, call, patch

import pandas as pd
import pytest
import requests

from src.data.providers.eastmoney_sector import (
    EASTMONEY_SECTOR_FILTERS,
    get_industry_sector_list,
    get_sector_daily_kline,
)
from src.data.sector import (
    EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    EASTMONEY_INDUSTRY_SECTOR_TYPE,
    SECTOR_KLINE_COLUMNS,
    SectorDefinition,
)


def definition(level=1, code="BK0001", name="Industry"):
    return SectorDefinition(
        EASTMONEY_INDUSTRY_SECTOR_TYPE,
        level,
        code,
        name,
        EASTMONEY_INDUSTRY_REGISTRY_SOURCE,
    )


def response(payload):
    result = Mock()
    result.raise_for_status.return_value = None
    result.json.return_value = payload
    return result


def registry_payload(total, rows):
    return {"rc": 0, "data": {"total": total, "diff": rows}}


def rows(start, count):
    return [
        {"f12": f"BK{number:04d}", "f14": f"Industry {number}"}
        for number in range(start, start + count)
    ]


def kline_payload(klines=None, code="BK0001", name="Industry"):
    if klines is None:
        klines = ["2026-07-18,10,11,12,9,100,1000,3,1.2,0.1,2"]
    return {"rc": 0, "data": {"code": code, "name": name, "klines": klines}}


@pytest.mark.parametrize("level,filter_value", [(1, "m:90 s:2 f:!50"), (2, "m:90 s:4 f:!50"), (3, "m:90 s:8 f:!50")])
def test_registry_uses_exact_level_filter_and_request_parameters(level, filter_value):
    with patch("src.data.providers.eastmoney_sector.requests.get", return_value=response(registry_payload(1, rows(1, 1)))) as mock_get:
        result = get_industry_sector_list(level)
    assert result[0].sector_level == level
    params = mock_get.call_args.kwargs["params"]
    assert params == {"pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f3", "fs": filter_value, "fields": "f12,f14"}
    assert "t:2" not in params["fs"]
    assert EASTMONEY_SECTOR_FILTERS[level] == filter_value


def test_registry_paginates_total_201_and_returns_exact_sorted_count():
    mock_get = Mock(side_effect=[
        response(registry_payload(201, rows(101, 100))),
        response(registry_payload(201, rows(1, 100))),
        response(registry_payload(201, rows(201, 1))),
    ])
    with patch("src.data.providers.eastmoney_sector.requests.get", mock_get):
        result = get_industry_sector_list(1)
    assert len(result) == 201
    assert [request.kwargs["params"]["pn"] for request in mock_get.call_args_list] == ["1", "2", "3"]
    assert result[0].sector_code == "BK0001"


@pytest.mark.parametrize("payload", [
    {}, {"rc": 1, "data": {}}, {"rc": 0}, {"rc": 0, "data": []},
    {"rc": 0, "data": {"total": -1, "diff": []}},
    {"rc": 0, "data": {"total": True, "diff": []}},
    {"rc": 0, "data": {"total": 1, "diff": "bad"}},
    registry_payload(0, []), registry_payload(1, [None]),
    registry_payload(1, [{"f12": "", "f14": "Name"}]),
    registry_payload(1, [{"f12": "BK123", "f14": "Name"}]),
    registry_payload(1, [{"f12": "BK0001", "f14": " "}]),
])
def test_registry_rejects_invalid_response_shapes_without_retry(payload):
    with patch("src.data.providers.eastmoney_sector.requests.get", return_value=response(payload)) as mock_get:
        with pytest.raises(RuntimeError, match=r"eastmoney.*level 1.*after 1 attempts"):
            get_industry_sector_list(1, sleep=Mock())
    mock_get.assert_called_once()


def test_registry_rejects_changed_total_empty_middle_and_duplicate_codes():
    cases = [
        [response(registry_payload(101, rows(1, 100))), response(registry_payload(100, rows(101, 1)))],
        [response(registry_payload(101, rows(1, 100))), response(registry_payload(101, []))],
        [response(registry_payload(2, [{"f12": "BK0001", "f14": "A"}, {"f12": "BK0001", "f14": "B"}]))],
        [response(registry_payload(101, rows(1, 100))), response(registry_payload(101, [{"f12": "BK0001", "f14": "Again"}]))],
    ]
    for side_effect in cases:
        with patch("src.data.providers.eastmoney_sector.requests.get", side_effect=side_effect):
            with pytest.raises(RuntimeError):
                get_industry_sector_list(1)


def test_registry_rejects_short_final_page_without_requesting_extra_page():
    mock_get = Mock(side_effect=[
        response(registry_payload(102, rows(1, 100))),
        response(registry_payload(102, rows(101, 1))),
    ])
    with patch("src.data.providers.eastmoney_sector.requests.get", mock_get):
        with pytest.raises(RuntimeError, match=r"unique record count 101.*data.total 102"):
            get_industry_sector_list(1)
    assert mock_get.call_count == 2


def test_registry_invalid_json_is_not_retried():
    invalid = response({})
    invalid.json.side_effect = ValueError("bad JSON")
    with patch("src.data.providers.eastmoney_sector.requests.get", return_value=invalid) as mock_get:
        with pytest.raises(RuntimeError, match="invalid JSON"):
            get_industry_sector_list(2, sleep=Mock())
    mock_get.assert_called_once()


def http_failure(status):
    failure = Mock(status_code=status)
    failure.raise_for_status.side_effect = requests.HTTPError(response=failure)
    return failure


def test_registry_timeout_then_success_and_exact_backoff():
    sleep = Mock()
    mock_get = Mock(side_effect=[requests.Timeout("one"), requests.Timeout("two"), response(registry_payload(1, rows(1, 1)))])
    with patch("src.data.providers.eastmoney_sector.requests.get", mock_get):
        get_industry_sector_list(1, sleep=sleep)
    assert mock_get.call_count == 3
    assert sleep.call_args_list == [call(0.5), call(1.0)]


def test_registry_connection_error_exhaustion_reports_actual_attempts():
    with patch("src.data.providers.eastmoney_sector.requests.get", side_effect=requests.ConnectionError("down")) as mock_get:
        with pytest.raises(RuntimeError, match=r"eastmoney.*level 3.*after 3 attempts"):
            get_industry_sector_list(3, sleep=Mock())
    assert mock_get.call_count == 3


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_registry_retries_retryable_http(status):
    sleep = Mock()
    with patch("src.data.providers.eastmoney_sector.requests.get", side_effect=[http_failure(status), response(registry_payload(1, rows(1, 1)))]) as mock_get:
        get_industry_sector_list(1, sleep=sleep)
    assert mock_get.call_count == 2
    sleep.assert_called_once_with(0.5)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 418])
def test_registry_does_not_retry_non_retryable_http(status):
    sleep = Mock()
    with patch("src.data.providers.eastmoney_sector.requests.get", return_value=http_failure(status)) as mock_get:
        with pytest.raises(RuntimeError, match="after 1 attempts"):
            get_industry_sector_list(1, sleep=sleep)
    mock_get.assert_called_once()
    sleep.assert_not_called()


def test_kline_uses_exact_unadjusted_parameters_and_maps_11_fields():
    with patch("src.data.providers.eastmoney_sector.requests.get", return_value=response(kline_payload())) as mock_get:
        result = get_sector_daily_kline(definition(), limit=20)
    assert result.iloc[0].to_dict() == {"date": "2026-07-18", "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 100, "amount": 1000.0, "change_pct": 1.2}
    assert mock_get.call_args.kwargs["params"] == {
        "secid": "90.BK0001", "klt": "101", "fqt": "0", "lmt": "20", "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }


def test_kline_accepts_trimmed_matching_name_and_empty_records():
    with patch("src.data.providers.eastmoney_sector.requests.get", return_value=response(kline_payload([], name=" Industry "))):
        result = get_sector_daily_kline(definition())
    assert result.empty
    assert result.columns.tolist() == list(SECTOR_KLINE_COLUMNS)


@pytest.mark.parametrize("payload", [
    {}, {"rc": 1}, {"rc": 0, "data": None},
    {"rc": 0, "data": {"code": "BK0001", "name": "Industry"}},
    kline_payload(code="BK0002"), kline_payload(name="Other"),
    kline_payload("bad"), kline_payload([None]),
    kline_payload(["2026-07-18,1,1"]),
    kline_payload(["2026-07-18,10,11,12,9,100,1000,3,1.2,0.1,2,extra"]),
])
def test_kline_rejects_invalid_response_identity_and_records_without_retry(payload):
    with patch("src.data.providers.eastmoney_sector.requests.get", return_value=response(payload)) as mock_get:
        with pytest.raises(RuntimeError, match=r"eastmoney.*BK0001.*after 1 attempts"):
            get_sector_daily_kline(definition(), sleep=Mock())
    mock_get.assert_called_once()


@pytest.mark.parametrize("record", [
    "2026/07/18,10,11,12,9,100,1000,3,1.2,0.1,2",
    "2026-07-18,0,11,12,9,100,1000,3,1.2,0.1,2",
    "2026-07-18,10,11,8,9,100,1000,3,1.2,0.1,2",
    "2026-07-18,10,11,12,9,1.5,1000,3,1.2,0.1,2",
    "2026-07-18,10,11,12,9,100,-1,3,1.2,0.1,2",
    "2026-07-18,10,11,12,9,100,1000,3,inf,0.1,2",
])
def test_kline_provider_exposes_normalizer_validation(record):
    with patch("src.data.providers.eastmoney_sector.requests.get", return_value=response(kline_payload([record]))):
        with pytest.raises(RuntimeError, match="after 1 attempts"):
            get_sector_daily_kline(definition())


def test_kline_preserves_nullable_provider_native_values():
    record = "2026-07-18,10,11,12,9,--,-,3,,0.1,2"
    with patch("src.data.providers.eastmoney_sector.requests.get", return_value=response(kline_payload([record]))):
        result = get_sector_daily_kline(definition())
    assert pd.isna(result.iloc[0]["volume"])
    assert pd.isna(result.iloc[0]["amount"])
    assert pd.isna(result.iloc[0]["change_pct"])


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_kline_rejects_invalid_limit_before_request(limit):
    with patch("src.data.providers.eastmoney_sector.requests.get") as mock_get:
        with pytest.raises(ValueError):
            get_sector_daily_kline(definition(), limit=limit)
    mock_get.assert_not_called()


def test_kline_retries_network_error_and_retryable_http_but_not_bad_http():
    sleep = Mock()
    with patch("src.data.providers.eastmoney_sector.requests.get", side_effect=[requests.Timeout("slow"), http_failure(503), response(kline_payload())]) as mock_get:
        get_sector_daily_kline(definition(), sleep=sleep)
    assert mock_get.call_count == 3
    assert sleep.call_args_list == [call(0.5), call(1.0)]
    with patch("src.data.providers.eastmoney_sector.requests.get", return_value=http_failure(400)) as mock_get:
        with pytest.raises(RuntimeError, match="after 1 attempts"):
            get_sector_daily_kline(definition(), sleep=Mock())
    mock_get.assert_called_once()
