"""
Unit tests for GET /api/search

DB is mocked via dependency_overrides — no real database required.
Verifies: v2 response shape, has_flood_data bool, data_year_range format,
location_name/location_type fields, absence of banned fields.
"""

from collections import namedtuple
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.database import get_db
from api.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FakeRow = namedtuple(
    "FakeRow",
    ["ward_name_vi", "ward_name", "district", "lat", "lng", "flood_count", "min_year", "max_year"],
)

AliasRow = namedtuple("AliasRow", ["alias_norm", "alias_vi", "alias_type", "ward_name"])


def _override_db(rows: list[FakeRow], alias_rows=None, alias_ward_rows=None):
    """Return a get_db override that handles sequential DB calls in search_wards:
    call 0 — direct ward ILIKE query  → returns `rows`
    call 1 — alias ILIKE query        → returns `alias_rows` (default: empty)
    call 2 — alias ward data query    → returns `alias_ward_rows` (default: empty)
    """
    if alias_rows is None:
        alias_rows = []
    if alias_ward_rows is None:
        alias_ward_rows = []

    async def _get_db():
        call_count = 0

        async def _execute(stmt, *args, **kwargs):
            nonlocal call_count
            result = MagicMock()
            if call_count == 0:
                result.fetchall.return_value = rows
            elif call_count == 1:
                result.fetchall.return_value = alias_rows
            else:
                result.fetchall.return_value = alias_ward_rows
            call_count += 1
            return result

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_execute)
        yield session

    return _get_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Happy path — direct ward matches
# ---------------------------------------------------------------------------

def test_search_returns_has_flood_data_true():
    rows = [FakeRow("Mỹ Đình II", "my dinh ii", "Nam Từ Liêm", 21.0285, 105.7767, 5, 2019, 2024)]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=my+dinh")

    assert r.status_code == 200
    item = r.json()["results"][0]
    assert item["has_flood_data"] is True
    assert item["data_year_range"] == "2019–2024"


def test_search_returns_has_flood_data_false():
    rows = [FakeRow("Phú Đô", "phu do", "Nam Từ Liêm", 21.01, 105.77, 0, None, None)]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=phu+do")

    assert r.status_code == 200
    item = r.json()["results"][0]
    assert item["has_flood_data"] is False
    assert item["data_year_range"] is None


def test_search_response_shape_v2_no_banned_fields():
    """flood_zone_count, teaser_events, source, source_url must NOT appear."""
    rows = [FakeRow("Mỹ Đình II", "my dinh ii", "Nam Từ Liêm", 21.0285, 105.7767, 3, 2020, 2023)]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=my+dinh")

    item = r.json()["results"][0]
    for banned in ("flood_zone_count", "teaser_events", "source", "source_url"):
        assert banned not in item, f"field '{banned}' must not appear in v2 response"


def test_search_response_required_fields_present():
    rows = [FakeRow("Mỹ Đình II", "my dinh ii", "Nam Từ Liêm", 21.0285, 105.7767, 5, 2019, 2024)]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=my+dinh")

    item = r.json()["results"][0]
    for field in (
        "ward", "ward_normalized", "location_name", "location_type",
        "district", "lat", "lng", "has_flood_data", "data_year_range",
    ):
        assert field in item, f"required field '{field}' missing"


def test_search_direct_ward_location_name_equals_ward():
    """Direct ward match: location_name = ward_name_vi, location_type = 'ward'."""
    rows = [FakeRow("Mỹ Đình II", "my dinh ii", "Nam Từ Liêm", 21.0285, 105.7767, 0, None, None)]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=my+dinh")

    item = r.json()["results"][0]
    assert item["location_name"] == "Mỹ Đình II"
    assert item["location_type"] == "ward"


def test_search_empty_results_returns_message():
    """Zero results returns Vietnamese guidance message (FIR-6 AC)."""
    app.dependency_overrides[get_db] = _override_db([])

    with TestClient(app) as client:
        r = client.get("/api/search?q=khong+ton+tai")

    assert r.status_code == 200
    data = r.json()
    assert data["results"] == []
    assert data["total"] == 0
    assert data["message"] is not None
    assert "Không tìm thấy" in data["message"]


def test_search_total_matches_results_count():
    rows = [
        FakeRow("Mỹ Đình I", "my dinh i", "Nam Từ Liêm", 21.028, 105.776, 2, 2021, 2022),
        FakeRow("Mỹ Đình II", "my dinh ii", "Nam Từ Liêm", 21.029, 105.777, 4, 2019, 2024),
    ]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=my+dinh")

    data = r.json()
    assert data["total"] == len(data["results"]) == 2


def test_search_year_range_format():
    """data_year_range must be 'YYYY–YYYY' (en-dash, not hyphen)."""
    rows = [FakeRow("Đống Đa", "dong da", "Đống Đa", 21.03, 105.84, 10, 2018, 2023)]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=dong+da")

    yr = r.json()["results"][0]["data_year_range"]
    assert yr == "2018–2023"
    assert "-" not in yr, "year range must use en-dash (–), not hyphen (-)"


# ---------------------------------------------------------------------------
# Alias match — location_name/type
# ---------------------------------------------------------------------------

def test_search_alias_match_location_name_is_alias_vi():
    """Alias match: location_name = alias_vi, location_type = alias_type."""
    alias_rows = [
        AliasRow("royal city", "Royal City", "apartment", "phuong trung hoa"),
    ]
    alias_ward_rows = [
        FakeRow("Phường Trung Hòa", "phuong trung hoa", "Cầu Giấy", 21.01, 105.79, 3, 2020, 2023),
    ]
    app.dependency_overrides[get_db] = _override_db([], alias_rows, alias_ward_rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=royal+city")

    assert r.status_code == 200
    assert len(r.json()["results"]) == 1
    item = r.json()["results"][0]
    assert item["location_name"] == "Royal City"
    assert item["location_type"] == "apartment"
    assert item["ward"] == "Phường Trung Hòa"


def test_search_alias_match_street_type():
    """Street alias: location_type = 'street'."""
    alias_rows = [
        AliasRow("nguyen trai", "Nguyễn Trãi", "street", "phuong thuong dinh"),
    ]
    alias_ward_rows = [
        FakeRow("Phường Thượng Đình", "phuong thuong dinh", "Thanh Xuân", 21.0, 105.8, 0, None, None),
    ]
    app.dependency_overrides[get_db] = _override_db([], alias_rows, alias_ward_rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=nguyen+trai")

    item = r.json()["results"][0]
    assert item["location_name"] == "Nguyễn Trãi"
    assert item["location_type"] == "street"


def test_search_alias_multiple_wards_all_returned():
    """Multiple alias-matched wards each appear as separate results with their own has_flood_data."""
    alias_rows = [
        AliasRow("nguyen trai", "Nguyễn Trãi", "street", "phuong thuong dinh"),
        AliasRow("nguyen trai specific", "Nguyễn Trãi (chi tiết)", "other", "phuong thanh nhan"),
    ]
    alias_ward_rows = [
        FakeRow("Phường Thượng Đình", "phuong thuong dinh", "Thanh Xuân", 21.0, 105.8, 0, None, None),
        FakeRow("Phường Thanh Nhân", "phuong thanh nhan", "Hai Bà Trưng", 21.0, 105.8, 5, 2021, 2024),
    ]
    app.dependency_overrides[get_db] = _override_db([], alias_rows, alias_ward_rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=nguyen+trai")

    results = r.json()["results"]
    assert len(results) == 2
    by_ward = {item["ward_normalized"]: item for item in results}
    assert by_ward["phuong thuong dinh"]["has_flood_data"] is False
    assert by_ward["phuong thanh nhan"]["has_flood_data"] is True
    assert by_ward["phuong thanh nhan"]["data_year_range"] == "2021–2024"


def test_search_alias_type_priority_street_beats_landmark():
    """When same ward has street + landmark aliases, street wins regardless of score."""
    alias_rows = [
        AliasRow("le trong tan landmark detail block 5", "Lê Trọng Tấn, Block 5", "landmark", "phuong khuong mai"),
        AliasRow("le trong tan", "Đường Lê Trọng Tấn", "street", "phuong khuong mai"),
    ]
    alias_ward_rows = [
        FakeRow("Phường Khương Mai", "phuong khuong mai", "Thanh Xuân", 21.0, 105.8, 2, 2020, 2023),
    ]
    app.dependency_overrides[get_db] = _override_db([], alias_rows, alias_ward_rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=le+trong+tan")

    assert r.status_code == 200
    item = r.json()["results"][0]
    assert item["location_name"] == "Đường Lê Trọng Tấn"
    assert item["location_type"] == "street"


def test_search_alias_fallback_to_alias_norm_when_vi_missing():
    """alias_vi=None falls back to alias_norm as location_name."""
    alias_rows = [
        AliasRow("some place", None, "landmark", "phuong abc"),
    ]
    alias_ward_rows = [
        FakeRow("Phường ABC", "phuong abc", "Hoàn Kiếm", 21.0, 105.8, 0, None, None),
    ]
    app.dependency_overrides[get_db] = _override_db([], alias_rows, alias_ward_rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=some+place")

    item = r.json()["results"][0]
    assert item["location_name"] == "some place"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_search_empty_q_returns_422():
    """q is required — missing param → 422."""
    with TestClient(app) as client:
        r = client.get("/api/search")
    assert r.status_code == 422


def test_search_q_too_long_returns_422():
    with TestClient(app) as client:
        r = client.get(f"/api/search?q={'a' * 101}")
    assert r.status_code == 422


def test_search_normalizes_vietnamese_input():
    """'Mỹ Đình' should be normalized and still hit the DB (query runs without error)."""
    rows = [FakeRow("Mỹ Đình II", "my dinh ii", "Nam Từ Liêm", 21.0285, 105.7767, 5, 2019, 2024)]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/search?q=M%E1%BB%B9+%C4%90%C3%ACnh")  # URL-encoded "Mỹ Đình"

    assert r.status_code == 200
    assert r.json()["results"][0]["ward_normalized"] == "my dinh ii"


def test_health_endpoint():
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
