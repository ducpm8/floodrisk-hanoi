"""
Admin purchased-report listing and CSV export — FIR-36

Endpoints:
  GET /admin/purchases        paginated list, filterable by status + search
  GET /admin/purchases/export full CSV download

Auth: X-Admin-Token header (if ADMIN_TOKEN env var is set).
      In dev (ADMIN_TOKEN unset) all requests are allowed.
"""

import csv
import io
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.purchased_report import PurchasedReport
from api.routers.admin_crawl import require_admin

router = APIRouter()

_EXPORT_COLUMNS = [
    "id", "email", "session_id", "ward_normalized", "location_query",
    "location_lat", "location_lng", "payment_gateway", "payment_id",
    "amount_vnd", "status", "failure_reason", "paid_at", "created_at",
]


class PurchaseItem(BaseModel):
    id: str
    email: str
    session_id: str
    ward_normalized: str | None
    location_query: str | None
    location_lat: str | None
    location_lng: str | None
    payment_gateway: str | None
    payment_id: str | None
    amount_vnd: int
    status: str
    failure_reason: str | None
    paid_at: str | None
    created_at: str | None

    model_config = ConfigDict(from_attributes=True)


class PurchasesResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PurchaseItem]


def _build_query(status: str, q: str):
    stmt = select(PurchasedReport)
    if status != "all":
        stmt = stmt.where(PurchasedReport.status == status)
    if q:
        term = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(PurchasedReport.email).like(term),
                func.lower(PurchasedReport.ward_normalized).like(term),
            )
        )
    return stmt


def _to_item(row: PurchasedReport) -> PurchaseItem:
    return PurchaseItem(
        id=str(row.id),
        email=row.email,
        session_id=str(row.session_id),
        ward_normalized=row.ward_normalized,
        location_query=row.location_query,
        location_lat=str(row.location_lat) if row.location_lat is not None else None,
        location_lng=str(row.location_lng) if row.location_lng is not None else None,
        payment_gateway=row.payment_gateway,
        payment_id=row.payment_id,
        amount_vnd=row.amount_vnd or 35000,
        status=row.status,
        failure_reason=row.failure_reason,
        paid_at=row.paid_at.isoformat() if row.paid_at else None,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


@router.get("/admin/purchases", response_model=PurchasesResponse)
async def list_purchases(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str = Query(default="all", pattern="^(all|paid|pending|failed)$"),
    q: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> PurchasesResponse:
    base = _build_query(status, q)

    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    rows_result = await db.execute(
        base.order_by(PurchasedReport.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = rows_result.scalars().all()

    return PurchasesResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[_to_item(r) for r in rows],
    )


@router.get("/admin/purchases/export")
async def export_purchases_csv(
    status: str = Query(default="all", pattern="^(all|paid|pending|failed)$"),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> StreamingResponse:
    stmt = _build_query(status, "").order_by(PurchasedReport.created_at.desc())
    result = await db.execute(stmt)
    rows = result.scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([
            str(row.id),
            row.email,
            str(row.session_id),
            row.ward_normalized or "",
            row.location_query or "",
            str(row.location_lat) if row.location_lat is not None else "",
            str(row.location_lng) if row.location_lng is not None else "",
            row.payment_gateway or "",
            row.payment_id or "",
            row.amount_vnd or 35000,
            row.status,
            row.failure_reason or "",
            row.paid_at.isoformat() if row.paid_at else "",
            row.created_at.isoformat() if row.created_at else "",
        ])

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"purchases_{date_str}.csv"
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
