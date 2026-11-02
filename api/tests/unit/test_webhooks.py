"""
Unit tests for POST /api/webhooks/sepay (FIR-81).
All DB calls mocked via dependency_overrides.

Covers:
  1. valid_signature_accepted
  2. bad_signature_returns_400
  3. stale_timestamp_returns_400
  4. outgoing_transfer_ignored
  5. missing_code_returns_success
  6. unknown_code_returns_success
  7. already_paid_is_noop
  8. amount_mismatch_returns_success_without_marking_paid
  9. happy_path_marks_paid_and_stores_transaction_id
"""

import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.database import get_db
from api.main import app

_TEST_API_KEY = "test-secret-key-abc123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_purchase(
    id=None,
    session_id=None,
    status="pending",
    payment_code="FLOOD1A2B3C4D",
    payment_gateway="sepay",
    amount_vnd=35000,
    paid_at=None,
    sepay_transaction_id=None,
):
    p = SimpleNamespace()
    p.id = id or uuid.uuid4()
    p.session_id = session_id or uuid.uuid4()
    p.status = status
    p.payment_code = payment_code
    p.payment_gateway = payment_gateway
    p.amount_vnd = amount_vnd
    p.paid_at = paid_at
    p.sepay_transaction_id = sepay_transaction_id
    return p


def _webhook_db(purchase=None):
    """Mock DB returning a single purchase on SELECT."""
    async def _get_db():
        async def mock_execute(stmt, *args, **kwargs):
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = purchase
            return mock_result

        session = AsyncMock()
        session.execute = mock_execute
        session.commit = AsyncMock()
        yield session

    return _get_db


def _make_signature(body: bytes, timestamp: str, api_key: str = _TEST_API_KEY) -> str:
    signed_string = f"{timestamp}.".encode() + body
    digest = hmac.new(api_key.encode(), signed_string, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _valid_headers(body: bytes, api_key: str = _TEST_API_KEY) -> dict:
    ts = str(int(time.time()))
    return {
        "X-SePay-Timestamp": ts,
        "X-SePay-Signature": _make_signature(body, ts, api_key),
        "Content-Type": "application/json",
    }


def _payload(**kwargs) -> bytes:
    base = {
        "id": "TXN-001",
        "gateway": "Vietcombank",
        "transactionDate": "2026-05-16 23:00:00",
        "accountNumber": "0123456789",
        "code": "FLOOD1A2B3C4D",
        "content": "FLOOD1A2B3C4D Thanh toan bao cao lu",
        "transferAmount": 35000,
        "transferType": "in",
        "referenceCode": "REF001",
    }
    base.update(kwargs)
    return json.dumps(base).encode()


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Valid signature accepted
# ---------------------------------------------------------------------------

def test_valid_signature_accepted():
    purchase = _make_purchase()
    app.dependency_overrides[get_db] = _webhook_db(purchase=purchase)
    body = _payload()
    with patch("api.routers.webhooks._SEPAY_API_KEY", _TEST_API_KEY):
        with TestClient(app) as client:
            r = client.post("/api/webhooks/sepay", content=body, headers=_valid_headers(body))
    assert r.status_code == 200
    assert r.json() == {"success": True}


# ---------------------------------------------------------------------------
# 2. Bad signature returns 400
# ---------------------------------------------------------------------------

def test_bad_signature_returns_400():
    app.dependency_overrides[get_db] = _webhook_db()
    body = _payload()
    ts = str(int(time.time()))
    headers = {
        "X-SePay-Timestamp": ts,
        "X-SePay-Signature": "sha256=deadbeefdeadbeef",
        "Content-Type": "application/json",
    }
    with patch("api.routers.webhooks._SEPAY_API_KEY", _TEST_API_KEY):
        with TestClient(app) as client:
            r = client.post("/api/webhooks/sepay", content=body, headers=headers)
    assert r.status_code == 400
    assert "signature" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. Stale timestamp returns 400
# ---------------------------------------------------------------------------

def test_stale_timestamp_returns_400():
    app.dependency_overrides[get_db] = _webhook_db()
    body = _payload()
    stale_ts = str(int(time.time()) - 400)  # 400s ago — beyond 300s window
    headers = {
        "X-SePay-Timestamp": stale_ts,
        "X-SePay-Signature": _make_signature(body, stale_ts),
        "Content-Type": "application/json",
    }
    with patch("api.routers.webhooks._SEPAY_API_KEY", _TEST_API_KEY):
        with TestClient(app) as client:
            r = client.post("/api/webhooks/sepay", content=body, headers=headers)
    assert r.status_code == 400
    assert "stale" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 4. Outgoing transfer ignored (transferType='out')
# ---------------------------------------------------------------------------

def test_outgoing_transfer_ignored():
    app.dependency_overrides[get_db] = _webhook_db()
    body = _payload(transferType="out")
    with patch("api.routers.webhooks._SEPAY_API_KEY", _TEST_API_KEY):
        with TestClient(app) as client:
            r = client.post("/api/webhooks/sepay", content=body, headers=_valid_headers(body))
    assert r.status_code == 200
    assert r.json() == {"success": True}


# ---------------------------------------------------------------------------
# 5. Missing code returns success (no retry spam)
# ---------------------------------------------------------------------------

def test_missing_code_returns_success():
    app.dependency_overrides[get_db] = _webhook_db()
    body = _payload(code=None)
    with patch("api.routers.webhooks._SEPAY_API_KEY", _TEST_API_KEY):
        with TestClient(app) as client:
            r = client.post("/api/webhooks/sepay", content=body, headers=_valid_headers(body))
    assert r.status_code == 200
    assert r.json() == {"success": True}


# ---------------------------------------------------------------------------
# 6. Unknown code returns success (SePay shouldn't retry)
# ---------------------------------------------------------------------------

def test_unknown_code_returns_success():
    app.dependency_overrides[get_db] = _webhook_db(purchase=None)
    body = _payload(code="FLOODUNKNOWN")
    with patch("api.routers.webhooks._SEPAY_API_KEY", _TEST_API_KEY):
        with TestClient(app) as client:
            r = client.post("/api/webhooks/sepay", content=body, headers=_valid_headers(body))
    assert r.status_code == 200
    assert r.json() == {"success": True}


# ---------------------------------------------------------------------------
# 7. Already paid is a no-op (idempotent duplicate webhook)
# ---------------------------------------------------------------------------

def test_already_paid_is_noop():
    from datetime import datetime
    purchase = _make_purchase(status="paid", paid_at=datetime(2026, 5, 16, 12, 0))
    app.dependency_overrides[get_db] = _webhook_db(purchase=purchase)
    body = _payload()
    original_status = purchase.status

    with patch("api.routers.webhooks._SEPAY_API_KEY", _TEST_API_KEY):
        with TestClient(app) as client:
            r = client.post("/api/webhooks/sepay", content=body, headers=_valid_headers(body))

    assert r.status_code == 200
    assert r.json() == {"success": True}
    assert purchase.status == original_status  # unchanged


# ---------------------------------------------------------------------------
# 8. Amount mismatch returns success without marking paid
# ---------------------------------------------------------------------------

def test_amount_mismatch_does_not_mark_paid():
    purchase = _make_purchase(amount_vnd=35000)
    app.dependency_overrides[get_db] = _webhook_db(purchase=purchase)
    body = _payload(transferAmount=1000)  # underpayment

    with patch("api.routers.webhooks._SEPAY_API_KEY", _TEST_API_KEY):
        with TestClient(app) as client:
            r = client.post("/api/webhooks/sepay", content=body, headers=_valid_headers(body))

    assert r.status_code == 200
    assert r.json() == {"success": True}
    assert purchase.status == "pending"  # NOT marked paid


# ---------------------------------------------------------------------------
# 9. Happy path: valid webhook marks order paid and stores transaction ID
# ---------------------------------------------------------------------------

def test_happy_path_marks_paid_and_stores_transaction_id():
    purchase = _make_purchase(status="pending", amount_vnd=35000)
    app.dependency_overrides[get_db] = _webhook_db(purchase=purchase)
    body = _payload(id="TXN-HAPPY-001", transferAmount=35000)

    with patch("api.routers.webhooks._SEPAY_API_KEY", _TEST_API_KEY):
        with TestClient(app) as client:
            r = client.post("/api/webhooks/sepay", content=body, headers=_valid_headers(body))

    assert r.status_code == 200
    assert r.json() == {"success": True}
    assert purchase.status == "paid"
    assert purchase.paid_at is not None
    assert purchase.sepay_transaction_id == "TXN-HAPPY-001"
