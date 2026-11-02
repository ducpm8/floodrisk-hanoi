"""
FIR-14 backend: Payment initiation + status check.

POST /api/payment/initiate
  Body: {session_id, lat, lng, idempotency_key}
  Creates a pending PurchasedReport and returns a payment URL.
  In dev/staging: payment_url = /payment/return?order_id=...&demo=1
  In production: MoMo/VNPay real URL (wired by FIR-8).

GET /api/payment/status?order_id=&session_id=
  Returns {status, paid_at} for a given order.

SECURITY: payment_url is generated server-side. Client never constructs it.
HMAC webhook validation (FIR-8) will flip status to 'paid'.
"""

import os
import uuid as _uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.purchased_report import PurchasedReport

router = APIRouter()

_APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:3000")
_DEMO_MODE = os.getenv("PAYMENT_DEMO_MODE", "false").lower() == "true"
_SEPAY_BANK_CODE = os.getenv("SEPAY_BANK_CODE", "")
_SEPAY_ACCOUNT_NUMBER = os.getenv("SEPAY_ACCOUNT_NUMBER", "")
_SEPAY_CODE_PREFIX = os.getenv("SEPAY_PAYMENT_CODE_PREFIX", "FLOOD")


# ---------------------------------------------------------------------------
# Initiate payment
# ---------------------------------------------------------------------------

class InitiateRequest(BaseModel):
    session_id: str = Field(..., description="UUID from localStorage")
    lat: float
    lng: float
    idempotency_key: str = Field(..., description="UUID — prevents double charges")
    email: str = Field(default="guest@example.com")


class InitiateResponse(BaseModel):
    order_id: str
    payment_url: str
    amount_vnd: int


@router.post("/payment/initiate", response_model=InitiateResponse)
async def initiate_payment(
    body: InitiateRequest,
    db: AsyncSession = Depends(get_db),
) -> InitiateResponse:
    try:
        session_uuid = _uuid.UUID(body.session_id)
        idempotency_uuid = _uuid.UUID(body.idempotency_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id and idempotency_key must be valid UUIDs")

    if not -90 <= body.lat <= 90 or not -180 <= body.lng <= 180:
        raise HTTPException(status_code=400, detail="Invalid lat/lng")

    # Idempotency: return existing pending/paid order if same key
    existing_stmt = select(PurchasedReport).where(
        PurchasedReport.payment_id == str(idempotency_uuid)
    )
    existing_result = await db.execute(existing_stmt)
    existing = existing_result.scalar_one_or_none()
    if existing:
        existing_code = existing.payment_code or _generate_payment_code(str(existing.id))
        payment_url = _build_payment_url(str(existing.id), existing_code)
        return InitiateResponse(
            order_id=str(existing.id),
            payment_url=payment_url,
            amount_vnd=existing.amount_vnd or 35000,
        )

    # Create new pending purchase
    purchase = PurchasedReport(
        session_id=session_uuid,
        email=body.email,
        location_lat=body.lat,
        location_lng=body.lng,
        payment_id=str(idempotency_uuid),
        payment_gateway="sepay",
        amount_vnd=35000,
        status="pending",
    )
    db.add(purchase)
    await db.commit()
    await db.refresh(purchase)

    payment_code = _generate_payment_code(str(purchase.id))
    purchase.payment_code = payment_code
    await db.commit()

    payment_url = _build_payment_url(str(purchase.id), payment_code)

    return InitiateResponse(
        order_id=str(purchase.id),
        payment_url=payment_url,
        amount_vnd=35000,
    )


def _generate_payment_code(order_id: str) -> str:
    """Short code embedded in VietQR memo. SePay extracts this and sends in webhook 'code' field."""
    return f"{_SEPAY_CODE_PREFIX}{order_id[:8].upper()}"


def _build_payment_url(order_id: str, payment_code: str) -> str:
    """
    In demo mode: redirect to return URL with payment_code for local testing.
    In production: VietQR image URL — customer scans and transfers via banking app.
    """
    if _DEMO_MODE:
        return f"{_APP_BASE_URL}/payment/return?order_id={order_id}&demo=1"
    return (
        f"https://img.vietqr.io/image/{_SEPAY_BANK_CODE}-{_SEPAY_ACCOUNT_NUMBER}-qr_only.jpg"
        f"?amount=35000&addInfo={payment_code}&accountName=Flood+Report"
    )


# ---------------------------------------------------------------------------
# Payment status check
# ---------------------------------------------------------------------------

class PaymentStatusResponse(BaseModel):
    order_id: str
    status: str          # pending | paid | failed
    paid_at: str | None


@router.get("/payment/status", response_model=PaymentStatusResponse)
async def payment_status(
    order_id: str = Query(...),
    session_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> PaymentStatusResponse:
    try:
        order_uuid = _uuid.UUID(order_id)
        session_uuid = _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="order_id and session_id must be valid UUIDs")

    stmt = select(PurchasedReport).where(
        PurchasedReport.id == order_uuid,
        PurchasedReport.session_id == session_uuid,
    )
    result = await db.execute(stmt)
    purchase = result.scalar_one_or_none()

    if purchase is None:
        raise HTTPException(status_code=404, detail="Order not found")

    return PaymentStatusResponse(
        order_id=str(purchase.id),
        status=purchase.status,
        paid_at=str(purchase.paid_at) if purchase.paid_at else None,
    )


# ---------------------------------------------------------------------------
# Dev-only: confirm pending payment without real transfer
# ---------------------------------------------------------------------------

@router.post("/payment/dev-confirm", response_model=PaymentStatusResponse)
async def dev_confirm_payment(
    order_id: str = Query(...),
    session_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> PaymentStatusResponse:
    """Local dev only — flip pending → paid. Returns 404 in production (PAYMENT_DEMO_MODE != true)."""
    if not _DEMO_MODE:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        order_uuid = _uuid.UUID(order_id)
        session_uuid = _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="order_id and session_id must be valid UUIDs")

    stmt = select(PurchasedReport).where(
        PurchasedReport.id == order_uuid,
        PurchasedReport.session_id == session_uuid,
    )
    result = await db.execute(stmt)
    purchase = result.scalar_one_or_none()

    if purchase is None:
        raise HTTPException(status_code=404, detail="Order not found")

    if purchase.status == "pending":
        purchase.status = "paid"
        purchase.paid_at = datetime.now()
        await db.commit()
        await db.refresh(purchase)

    return PaymentStatusResponse(
        order_id=str(purchase.id),
        status=purchase.status,
        paid_at=str(purchase.paid_at) if purchase.paid_at else None,
    )


# ---------------------------------------------------------------------------
# Check if session already has a paid report for a location
# ---------------------------------------------------------------------------

class SessionReportCheckResponse(BaseModel):
    has_paid_report: bool
    order_id: str | None


@router.get("/payment/check", response_model=SessionReportCheckResponse)
async def check_session_report(
    session_id: str = Query(...),
    lat: float = Query(...),
    lng: float = Query(...),
    db: AsyncSession = Depends(get_db),
) -> SessionReportCheckResponse:
    try:
        session_uuid = _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id must be a valid UUID")

    # Find any paid purchase for this session near the location (500m)
    from geoalchemy2.functions import ST_DWithin, ST_MakePoint, ST_SetSRID
    from sqlalchemy import cast
    from api.models.flood_event import FloodEvent

    pt = cast(
        ST_SetSRID(ST_MakePoint(lng, lat), 4326),
        type_=FloodEvent.geom.type,
    )

    stmt = select(PurchasedReport).where(
        PurchasedReport.session_id == session_uuid,
        PurchasedReport.status == "paid",
        ST_DWithin(
            cast(
                ST_SetSRID(
                    ST_MakePoint(
                        PurchasedReport.location_lng,
                        PurchasedReport.location_lat,
                    ),
                    4326,
                ),
                type_=FloodEvent.geom.type,
            ),
            pt,
            500,
        ),
    )
    result = await db.execute(stmt)
    purchase = result.scalar_one_or_none()

    return SessionReportCheckResponse(
        has_paid_report=purchase is not None,
        order_id=str(purchase.id) if purchase else None,
    )
