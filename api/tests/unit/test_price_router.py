"""
Unit tests for FIR-13: GET /api/price/history?ward=
All DB calls mocked via dependency_overrides.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api.database import get_db
from api.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    recorded_month="2024-01-01",
    price_per_m2_vnd=50_000_000,
    last_updated="2024-01-31",
    property_type="apartment",
    ward="ba-dinh",
):
    r = SimpleNamespace()
    r.recorded_month = recorded_month
    r.price_per_m2_vnd = price_per_m2_vnd
    r.last_updated = last_updated
    r.property_type = property_type
    r.ward = ward
    return r


def _price_db(records=None):
    async def _get_db():
        async def mock_execute(stmt, *args, **kwargs):
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = records or []
            return mock_result

        session = AsyncMock()
        session.execute = mock_execute
        yield session

    return _get_db


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /api/price/history
# ---------------------------------------------------------------------------

def test_price_history_returns_200():
    app.dependency_overrides[get_db] = _price_db(records=[_make_record()])
    with TestClient(app) as client:
        r = client.get("/api/price/history?ward=ba-dinh")
    assert r.status_code == 200


def test_price_history_response_shape():
    app.dependency_overrides[get_db] = _price_db(records=[_make_record()])
    with TestClient(app) as client:
        r = client.get("/api/price/history?ward=ba-dinh")
    data = r.json()
    assert "ward" in data
    assert "records" in data
    assert "last_updated" in data


def test_price_history_record_fields():
    app.dependency_overrides[get_db] = _price_db(records=[_make_record()])
    with TestClient(app) as client:
        r = client.get("/api/price/history?ward=ba-dinh")
    record = r.json()["records"][0]
    assert "month" in record
    assert "price_per_m2_millions" in record
    assert "property_type" in record


def test_price_history_converts_to_millions():
    app.dependency_overrides[get_db] = _price_db(records=[_make_record(price_per_m2_vnd=50_000_000)])
    with TestClient(app) as client:
        r = client.get("/api/price/history?ward=ba-dinh")
    record = r.json()["records"][0]
    assert record["price_per_m2_millions"] == pytest.approx(50.0)


def test_price_history_month_format():
    app.dependency_overrides[get_db] = _price_db(records=[_make_record(recorded_month="2024-03-01")])
    with TestClient(app) as client:
        r = client.get("/api/price/history?ward=ba-dinh")
    record = r.json()["records"][0]
    assert record["month"] == "2024-03"


def test_price_history_empty_ward_returns_empty_records():
    app.dependency_overrides[get_db] = _price_db(records=[])
    with TestClient(app) as client:
        r = client.get("/api/price/history?ward=unknown-ward")
    data = r.json()
    assert data["records"] == []
    assert data["last_updated"] is None


def test_price_history_multiple_records():
    records = [
        _make_record(recorded_month="2024-01-01", price_per_m2_vnd=50_000_000),
        _make_record(recorded_month="2024-02-01", price_per_m2_vnd=52_000_000),
        _make_record(recorded_month="2024-03-01", price_per_m2_vnd=54_000_000),
    ]
    app.dependency_overrides[get_db] = _price_db(records=records)
    with TestClient(app) as client:
        r = client.get("/api/price/history?ward=ba-dinh")
    assert len(r.json()["records"]) == 3


def test_price_history_property_type_preserved():
    records = [
        _make_record(property_type="apartment"),
        _make_record(property_type="house"),
    ]
    app.dependency_overrides[get_db] = _price_db(records=records)
    with TestClient(app) as client:
        r = client.get("/api/price/history?ward=ba-dinh")
    types = {rec["property_type"] for rec in r.json()["records"]}
    assert "apartment" in types
    assert "house" in types


def test_price_history_missing_ward_returns_422():
    with TestClient(app) as client:
        r = client.get("/api/price/history")
    assert r.status_code == 422


def test_price_history_last_updated_from_last_record():
    records = [
        _make_record(last_updated="2024-02-28"),
        _make_record(last_updated="2024-03-31"),
    ]
    app.dependency_overrides[get_db] = _price_db(records=records)
    with TestClient(app) as client:
        r = client.get("/api/price/history?ward=ba-dinh")
    # last_updated comes from records[-1]
    assert "2024-03-31" in r.json()["last_updated"]
