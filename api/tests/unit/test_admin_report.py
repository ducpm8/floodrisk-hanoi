"""
Unit tests for FIR-36: GET /api/admin/purchases + GET /api/admin/purchases/export
All DB calls mocked via dependency_overrides.
"""

import csv
import io
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.database import get_db
from api.main import app


@pytest.fixture(autouse=True)
def mock_db_startup():
    """Prevent lifespan _cleanup_stuck_runs from hitting real DB on every TestClient startup."""
    with patch("api.main._cleanup_stuck_runs", new=AsyncMock()):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_purchase(
    id=None,
    session_id=None,
    email="user@example.com",
    ward_normalized="Phường Nam An Khanh",
    location_query="Nam An Khanh",
    location_lat=21.012345,
    location_lng=105.789012,
    payment_gateway="momo",
    payment_id="MOMO123",
    amount_vnd=35000,
    status="paid",
    failure_reason=None,
    paid_at=None,
    created_at=None,
):
    p = SimpleNamespace()
    p.id = id or uuid.uuid4()
    p.session_id = session_id or uuid.uuid4()
    p.email = email
    p.ward_normalized = ward_normalized
    p.location_query = location_query
    p.location_lat = location_lat
    p.location_lng = location_lng
    p.payment_gateway = payment_gateway
    p.payment_id = payment_id
    p.amount_vnd = amount_vnd
    p.status = status
    p.failure_reason = failure_reason
    p.paid_at = paid_at or datetime(2026, 4, 25, 10, 30, tzinfo=timezone.utc)
    p.created_at = created_at or datetime(2026, 4, 25, 10, 28, tzinfo=timezone.utc)
    return p


def _list_db(rows: list, total: int | None = None):
    """Mock DB for list endpoint: count query returns total, rows query returns rows."""
    _total = total if total is not None else len(rows)
    call_count = 0

    async def _get_db():
        nonlocal call_count

        async def mock_execute(stmt, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # count query
                result.scalar_one.return_value = _total
            else:
                # rows query
                result.scalars.return_value.all.return_value = rows
            return result

        session = AsyncMock()
        session.execute = mock_execute
        yield session

    return _get_db


def _export_db(rows: list):
    """Mock DB for export endpoint: single rows query."""
    async def _get_db():
        async def mock_execute(stmt, *args, **kwargs):
            result = MagicMock()
            result.scalars.return_value.all.return_value = rows
            return result

        session = AsyncMock()
        session.execute = mock_execute
        yield session

    return _get_db


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /api/admin/purchases — auth
# ---------------------------------------------------------------------------

def test_list_no_token_forbidden_when_token_set():
    with patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"):
        app.dependency_overrides[get_db] = _list_db([])
        with TestClient(app) as client:
            r = client.get("/api/admin/purchases")
        assert r.status_code == 403


def test_list_no_auth_allowed_in_dev_mode():
    app.dependency_overrides[get_db] = _list_db([])
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/admin/purchases — response shape
# ---------------------------------------------------------------------------

def test_list_returns_items():
    purchase = _make_purchase()
    app.dependency_overrides[get_db] = _list_db([purchase], total=1)
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["page"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["email"] == "user@example.com"
    assert item["status"] == "paid"
    assert item["amount_vnd"] == 35000


def test_list_empty_returns_zero():
    app.dependency_overrides[get_db] = _list_db([], total=0)
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases")
    data = r.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_pagination_params():
    app.dependency_overrides[get_db] = _list_db([], total=0)
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases?page=2&page_size=10")
    assert r.status_code == 200
    data = r.json()
    assert data["page"] == 2
    assert data["page_size"] == 10


def test_list_invalid_status_returns_422():
    app.dependency_overrides[get_db] = _list_db([])
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases?status=unknown")
    assert r.status_code == 422


def test_list_status_filter_accepted():
    for s in ("all", "paid", "pending", "failed"):
        app.dependency_overrides[get_db] = _list_db([])
        with TestClient(app) as client:
            r = client.get(f"/api/admin/purchases?status={s}")
        assert r.status_code == 200, f"status={s} should return 200"


def test_list_item_has_all_fields():
    purchase = _make_purchase()
    app.dependency_overrides[get_db] = _list_db([purchase], total=1)
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases")
    item = r.json()["items"][0]
    for field in ("id", "email", "session_id", "ward_normalized", "location_query",
                  "payment_gateway", "payment_id", "amount_vnd", "status",
                  "failure_reason", "paid_at", "created_at"):
        assert field in item, f"missing field: {field}"


def test_list_failed_purchase_has_failure_reason():
    purchase = _make_purchase(status="failed", failure_reason="Gateway timeout", paid_at=None)
    app.dependency_overrides[get_db] = _list_db([purchase], total=1)
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases")
    item = r.json()["items"][0]
    assert item["status"] == "failed"
    assert item["failure_reason"] == "Gateway timeout"


def test_list_page_size_exceeds_max_returns_422():
    app.dependency_overrides[get_db] = _list_db([])
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases?page_size=999")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/admin/purchases/export
# ---------------------------------------------------------------------------

def test_export_returns_csv_content_type():
    app.dependency_overrides[get_db] = _export_db([])
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]


def test_export_has_content_disposition():
    app.dependency_overrides[get_db] = _export_db([])
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases/export")
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "purchases_" in cd
    assert ".csv" in cd


def test_export_csv_parseable_with_header():
    purchase = _make_purchase()
    app.dependency_overrides[get_db] = _export_db([purchase])
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases/export")
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["email"] == "user@example.com"
    assert rows[0]["status"] == "paid"
    assert rows[0]["amount_vnd"] == "35000"


def test_export_empty_returns_header_only():
    app.dependency_overrides[get_db] = _export_db([])
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases/export")
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    assert rows == []
    assert reader.fieldnames is not None
    assert "email" in reader.fieldnames


def test_export_status_filter_accepted():
    for s in ("all", "paid", "pending", "failed"):
        app.dependency_overrides[get_db] = _export_db([])
        with TestClient(app) as client:
            r = client.get(f"/api/admin/purchases/export?status={s}")
        assert r.status_code == 200, f"export status={s} should return 200"


def test_export_invalid_status_returns_422():
    app.dependency_overrides[get_db] = _export_db([])
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases/export?status=bad")
    assert r.status_code == 422


def test_export_multiple_rows():
    purchases = [
        _make_purchase(email="a@a.com", status="paid"),
        _make_purchase(email="b@b.com", status="pending"),
        _make_purchase(email="c@c.com", status="failed", failure_reason="err"),
    ]
    app.dependency_overrides[get_db] = _export_db(purchases)
    with TestClient(app) as client:
        r = client.get("/api/admin/purchases/export")
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    assert len(rows) == 3
    emails = {row["email"] for row in rows}
    assert emails == {"a@a.com", "b@b.com", "c@c.com"}
