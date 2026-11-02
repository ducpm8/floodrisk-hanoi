"""
Unit tests for GET /api/flood-zones

DB is mocked via dependency_overrides.
Verifies response shape, severity values, radius, and empty states.
"""

import uuid
from collections import namedtuple
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.database import get_db
from api.main import app

FakeZoneRow = namedtuple(
    "FakeZoneRow",
    ["id", "lat", "lng", "severity", "ward", "location_name", "district", "event_date"],
)


def _override_db(rows: list[FakeZoneRow]):
    async def _get_db():
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows
        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        yield session

    return _get_db


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_flood_zones_returns_200():
    rows = [
        FakeZoneRow(uuid.uuid4(), 21.0245, 105.8412, "severe", "dong da",
                    "Ngõ Xã Đàn", "Đống Đa", date(2023, 7, 15)),
    ]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/flood-zones")

    assert r.status_code == 200


def test_flood_zones_response_shape():
    rows = [
        FakeZoneRow(uuid.uuid4(), 21.0245, 105.8412, "severe", "dong da",
                    "Ngõ Xã Đàn", "Đống Đa", date(2023, 7, 15)),
    ]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/flood-zones")

    data = r.json()
    assert "zones" in data
    assert "total" in data
    zone = data["zones"][0]
    for field in ("id", "lat", "lng", "severity", "ward", "radius_m", "event_date"):
        assert field in zone, f"missing field: {field}"


def test_flood_zones_severity_values():
    """All returned severities must be one of: severe, moderate, minor."""
    rows = [
        FakeZoneRow(uuid.uuid4(), 21.0, 105.8, "severe",  "dong da",  None, None, date(2023, 1, 1)),
        FakeZoneRow(uuid.uuid4(), 21.1, 105.9, "moderate","cau giay",  None, None, date(2022, 1, 1)),
        FakeZoneRow(uuid.uuid4(), 21.2, 106.0, "minor",   "yen hoa",  None, None, date(2021, 1, 1)),
    ]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/flood-zones")

    severities = {z["severity"] for z in r.json()["zones"]}
    assert severities <= {"severe", "moderate", "minor"}


def test_flood_zones_radius_is_200():
    rows = [
        FakeZoneRow(uuid.uuid4(), 21.0, 105.8, "minor", "ward", None, None, date(2023, 1, 1)),
    ]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/flood-zones")

    assert r.json()["zones"][0]["radius_m"] == 200


def test_flood_zones_total_matches_zones_count():
    rows = [
        FakeZoneRow(uuid.uuid4(), 21.0, 105.8, "severe",   "a", None, None, date(2023, 1, 1)),
        FakeZoneRow(uuid.uuid4(), 21.1, 105.9, "moderate", "b", None, None, date(2022, 1, 1)),
        FakeZoneRow(uuid.uuid4(), 21.2, 106.0, "minor",    "c", None, None, date(2021, 1, 1)),
    ]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/flood-zones")

    data = r.json()
    assert data["total"] == len(data["zones"]) == 3


def test_flood_zones_empty_returns_empty_list():
    app.dependency_overrides[get_db] = _override_db([])

    with TestClient(app) as client:
        r = client.get("/api/flood-zones")

    data = r.json()
    assert data == {"zones": [], "total": 0}


def test_flood_zones_null_severity_defaults_to_minor():
    """If severity is NULL in DB, fallback to 'minor'."""
    rows = [
        FakeZoneRow(uuid.uuid4(), 21.0, 105.8, None, "ward", None, None, date(2023, 1, 1)),
    ]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/flood-zones")

    assert r.json()["zones"][0]["severity"] == "minor"


def test_flood_zones_event_date_as_string():
    rows = [
        FakeZoneRow(uuid.uuid4(), 21.0, 105.8, "severe", "dong da", None, None, date(2023, 7, 15)),
    ]
    app.dependency_overrides[get_db] = _override_db(rows)

    with TestClient(app) as client:
        r = client.get("/api/flood-zones")

    event_date = r.json()["zones"][0]["event_date"]
    assert event_date == "2023-07-15"
