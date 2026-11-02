"""
GET /api/flood-zones

Returns all flood events as renderable map circles.
Used by <FloodMap /> to draw severity-colored circles on the Google Maps canvas.

Response shape:
  {
    "zones": [
      {
        "id": "uuid",
        "lat": 21.0245,
        "lng": 105.8412,
        "severity": "severe",
        "ward": "dong da",
        "ward_vi": "Đống Đa",
        "district": "Đống Đa",
        "radius_m": 200,
        "event_date": "2023-07-15"
      }
    ],
    "total": 1
  }

Severity → color mapping (applied client-side):
  severe:   #ef4444  (red-500)
  moderate: #f97316  (orange-500)
  minor:    #eab308  (yellow-500)
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.flood_event import FloodEvent

router = APIRouter()

_CIRCLE_RADIUS_M = 200


class FloodZone(BaseModel):
    id: str
    lat: float
    lng: float
    severity: str
    ward: str
    ward_vi: str | None
    district: str | None
    radius_m: int
    event_date: str
    cause: str | None


class FloodZonesResponse(BaseModel):
    zones: list[FloodZone]
    total: int


@router.get("/flood-zones", response_model=FloodZonesResponse)
async def get_flood_zones(
    db: AsyncSession = Depends(get_db),
) -> FloodZonesResponse:
    stmt = (
        select(
            FloodEvent.id,
            FloodEvent.lat,
            FloodEvent.lng,
            FloodEvent.severity,
            FloodEvent.ward,
            FloodEvent.location_name,
            FloodEvent.district,
            FloodEvent.event_date,
            FloodEvent.cause,
        )
        .where(FloodEvent.lat.isnot(None))
        .where(FloodEvent.lng.isnot(None))
        .order_by(FloodEvent.event_date.desc())
    )

    result = await db.execute(stmt)
    rows = result.fetchall()

    zones = [
        FloodZone(
            id=str(row.id),
            lat=float(row.lat),
            lng=float(row.lng),
            severity=row.severity or "minor",
            ward=row.ward or "",
            ward_vi=row.location_name,
            district=row.district,
            radius_m=_CIRCLE_RADIUS_M,
            event_date=str(row.event_date),
            cause=row.cause,
        )
        for row in rows
    ]

    return FloodZonesResponse(zones=zones, total=len(zones))
