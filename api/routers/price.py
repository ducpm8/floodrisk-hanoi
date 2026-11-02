"""
FIR-13 backend: Price history by ward.

GET /api/price/history?ward={normalized_ward}
  Returns all price_records for the ward, grouped by property_type.
  Used by <PriceHistoryChart /> in the paid report.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.price_record import PriceRecord
from api.utils.text import normalize_ward

router = APIRouter()


class PricePoint(BaseModel):
    month: str           # "YYYY-MM"
    price_per_m2_millions: float
    last_updated: str
    property_type: str


class PriceHistoryResponse(BaseModel):
    ward: str
    records: list[PricePoint]
    last_updated: str | None


@router.get("/price/history", response_model=PriceHistoryResponse)
async def price_history(
    ward: str = Query(..., min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db),
) -> PriceHistoryResponse:
    ward_normalized = normalize_ward(ward)

    stmt = (
        select(PriceRecord)
        .where(PriceRecord.ward == ward_normalized)
        .order_by(PriceRecord.recorded_month.asc())
    )
    result = await db.execute(stmt)
    records = result.scalars().all()

    points = [
        PricePoint(
            month=str(r.recorded_month)[:7],        # "YYYY-MM"
            price_per_m2_millions=round(float(r.price_per_m2_vnd) / 1_000_000, 1),
            last_updated=str(r.last_updated),
            property_type=r.property_type or "apartment",
        )
        for r in records
    ]

    last_updated = str(records[-1].last_updated) if records else None

    return PriceHistoryResponse(
        ward=ward_normalized,
        records=points,
        last_updated=last_updated,
    )
