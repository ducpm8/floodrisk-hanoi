"""
Unit tests for FIR-14: POST /api/payment/initiate + GET /api/payment/status
All DB calls mocked via dependency_overrides.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.database import get_db
from api.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_purchase(
    id=None,
    session_id=None,
    status="pending",
    payment_id=None,
    payment_code=None,
    amount_vnd=35000,
    paid_at=None,
    location_lat=21.024,
    location_lng=105.841,
):
    p = SimpleNamespace()
    p.id = id or uuid.uuid4()
    p.session_id = session_id or uuid.uuid4()
    p.status = status
    p.payment_id = payment_id or str(uuid.uuid4())
    p.payment_code = payment_code
    p.amount_vnd = amount_vnd
    p.paid_at = paid_at
    p.location_lat = location_lat
    p.location_lng = location_lng
    return p


def _initiate_db(existing_purchase=None, new_purchase_id=None):
    """
    Mock for initiate flow:
      call 1 → idempotency check (scalar_one_or_none)
      then db.add/commit/refresh
    """
    call_count = 0
    _new_id = new_purchase_id or uuid.uuid4()

    async def _get_db():
        nonlocal call_count

        async def mock_execute(stmt, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = existing_purchase
            return mock_result

        async def mock_refresh(obj):
            obj.id = _new_id

        session = AsyncMock()
        session.execute = mock_execute
        session.refresh = mock_refresh
        session.add = MagicMock()
        session.commit = AsyncMock()
        yield session

    return _get_db


def _status_db(purchase=None):
    """Mock for status check: single scalar_one_or_none."""
    async def _get_db():
        async def mock_execute(stmt, *args, **kwargs):
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = purchase
            return mock_result

        async def mock_refresh(obj):
            pass

        session = AsyncMock()
        session.execute = mock_execute
        session.refresh = mock_refresh
        session.commit = AsyncMock()
        yield session

    return _get_db


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/payment/initiate
# ---------------------------------------------------------------------------

def test_initiate_creates_pending_order():
    app.dependency_overrides[get_db] = _initiate_db()
    with TestClient(app) as client:
        r = client.post("/api/payment/initiate", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024,
            "lng": 105.841,
            "idempotency_key": str(uuid.uuid4()),
        })
    assert r.status_code == 200
    data = r.json()
    assert "order_id" in data
    assert "payment_url" in data
    assert data["amount_vnd"] == 35000


def test_initiate_returns_payment_url():
    app.dependency_overrides[get_db] = _initiate_db()
    with TestClient(app) as client:
        r = client.post("/api/payment/initiate", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024,
            "lng": 105.841,
            "idempotency_key": str(uuid.uuid4()),
        })
    url = r.json()["payment_url"]
    # Demo mode returns ?demo=1 URL; production returns VietQR img.vietqr.io URL
    assert "vietqr.io" in url or "demo" in url


def test_initiate_idempotency_returns_existing_order():
    existing_id = uuid.uuid4()
    purchase = _make_purchase(id=existing_id, status="pending")
    app.dependency_overrides[get_db] = _initiate_db(existing_purchase=purchase)
    with TestClient(app) as client:
        r = client.post("/api/payment/initiate", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024,
            "lng": 105.841,
            "idempotency_key": str(uuid.uuid4()),
        })
    assert r.status_code == 200
    assert r.json()["order_id"] == str(existing_id)


def test_initiate_invalid_session_id_returns_400():
    app.dependency_overrides[get_db] = _initiate_db()
    with TestClient(app) as client:
        r = client.post("/api/payment/initiate", json={
            "session_id": "not-a-uuid",
            "lat": 21.024,
            "lng": 105.841,
            "idempotency_key": str(uuid.uuid4()),
        })
    assert r.status_code == 400


def test_initiate_invalid_idempotency_key_returns_400():
    app.dependency_overrides[get_db] = _initiate_db()
    with TestClient(app) as client:
        r = client.post("/api/payment/initiate", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024,
            "lng": 105.841,
            "idempotency_key": "bad-key",
        })
    assert r.status_code == 400


def test_initiate_invalid_lat_returns_400():
    app.dependency_overrides[get_db] = _initiate_db()
    with TestClient(app) as client:
        r = client.post("/api/payment/initiate", json={
            "session_id": str(uuid.uuid4()),
            "lat": 999,
            "lng": 105.841,
            "idempotency_key": str(uuid.uuid4()),
        })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/payment/status
# ---------------------------------------------------------------------------

def test_status_pending_order_returns_pending():
    purchase = _make_purchase(status="pending")
    app.dependency_overrides[get_db] = _status_db(purchase=purchase)
    with TestClient(app) as client:
        r = client.get(f"/api/payment/status?order_id={purchase.id}&session_id={purchase.session_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_dev_confirm_returns_404_when_demo_mode_off():
    """PAYMENT_DEMO_MODE defaults false — dev-confirm must be invisible in production."""
    purchase = _make_purchase(status="pending")
    app.dependency_overrides[get_db] = _status_db(purchase=purchase)
    with TestClient(app) as client:
        r = client.post(f"/api/payment/dev-confirm?order_id={purchase.id}&session_id={purchase.session_id}")
    assert r.status_code == 404


def test_status_paid_order_returns_paid():
    from datetime import datetime
    purchase = _make_purchase(status="paid", paid_at=datetime(2024, 1, 15, 12, 0))
    app.dependency_overrides[get_db] = _status_db(purchase=purchase)
    with TestClient(app) as client:
        r = client.get(f"/api/payment/status?order_id={purchase.id}&session_id={purchase.session_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "paid"
    assert r.json()["paid_at"] is not None


def test_status_not_found_returns_404():
    app.dependency_overrides[get_db] = _status_db(purchase=None)
    with TestClient(app) as client:
        r = client.get(f"/api/payment/status?order_id={uuid.uuid4()}&session_id={uuid.uuid4()}")
    assert r.status_code == 404


def test_status_invalid_order_id_returns_400():
    with TestClient(app) as client:
        r = client.get("/api/payment/status?order_id=bad&session_id=bad")
    assert r.status_code == 400


def test_status_response_has_all_fields():
    purchase = _make_purchase(status="paid")
    app.dependency_overrides[get_db] = _status_db(purchase=purchase)
    with TestClient(app) as client:
        r = client.get(f"/api/payment/status?order_id={purchase.id}&session_id={purchase.session_id}")
    data = r.json()
    assert "order_id" in data
    assert "status" in data
    assert "paid_at" in data
