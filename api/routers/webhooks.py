"""
SePay webhook handler (FIR-81).

POST /api/webhooks/sepay
  Receives bank-transfer notifications from SePay. Validates HMAC-SHA256
  signature, matches payment_code to a pending order, marks it paid.

Security contract:
  - Raw body MUST be read before any parsing (Pydantic consumes the stream).
  - Signature validated before any DB read/write.
  - Timestamp checked within 5 minutes (anti-replay).
  - Amount validated >= purchase amount before marking paid.
  - sepay_transaction_id UNIQUE constraint prevents duplicate processing.

SePay requires HTTP 200 + {"success": true} within 30 seconds.
Return success even for no-ops (unknown code, already paid) so SePay
doesn't retry indefinitely.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.purchased_report import PurchasedReport

router = APIRouter()
logger = logging.getLogger(__name__)

_SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "")


async def _validate_sepay_signature(request: Request, raw_body: bytes) -> None:
    """Verify X-SePay-Signature header using HMAC-SHA256.

    Signed string format: "{timestamp}.{raw_body_bytes}"
    Header format:        "sha256={hex_digest}"
    """
    sig_header = request.headers.get("X-SePay-Signature", "")
    timestamp_str = request.headers.get("X-SePay-Timestamp", "")

    if not timestamp_str:
        raise HTTPException(status_code=400, detail="Missing X-SePay-Timestamp")

    try:
        ts = int(timestamp_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-SePay-Timestamp")

    if abs(time.time() - ts) > 300:
        raise HTTPException(status_code=400, detail="Stale timestamp")

    if not _SEPAY_API_KEY:
        logger.warning("SEPAY_API_KEY not set — skipping signature validation")
        return

    signed_string = f"{timestamp_str}.".encode() + raw_body
    expected_digest = hmac.new(
        _SEPAY_API_KEY.encode(),
        signed_string,
        hashlib.sha256,
    ).hexdigest()
    expected_header = f"sha256={expected_digest}"

    if not hmac.compare_digest(sig_header, expected_header):
        raise HTTPException(status_code=400, detail="Invalid signature")


@router.post("/webhooks/sepay")
async def sepay_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Read raw bytes BEFORE any parsing — Pydantic would consume the stream.
    raw_body = await request.body()

    await _validate_sepay_signature(request, raw_body)

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Only process incoming transfers
    if payload.get("transferType") != "in":
        return {"success": True}

    code = payload.get("code")
    if not code:
        return {"success": True}

    stmt = select(PurchasedReport).where(PurchasedReport.payment_code == code)
    result = await db.execute(stmt)
    purchase = result.scalar_one_or_none()

    if purchase is None:
        logger.info("SePay webhook: unknown payment_code=%s", code)
        return {"success": True}

    if purchase.status == "paid":
        return {"success": True}

    transfer_amount = int(payload.get("transferAmount", 0))
    if transfer_amount < (purchase.amount_vnd or 0):
        logger.warning(
            "SePay webhook: amount mismatch order=%s expected=%s got=%s",
            purchase.id,
            purchase.amount_vnd,
            transfer_amount,
        )
        return {"success": True}

    purchase.status = "paid"
    purchase.paid_at = datetime.now()
    purchase.payment_gateway = "sepay"
    purchase.sepay_transaction_id = str(payload.get("id", ""))
    await db.commit()

    logger.info("SePay payment confirmed: order=%s amount=%s", purchase.id, transfer_amount)
    return {"success": True}
