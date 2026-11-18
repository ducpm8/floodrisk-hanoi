"""
FIR-7: Flood history report API

Two endpoints:

  GET  /api/flood/teaser?lat=&lng=
    Free, no auth. Returns teaser data for the 500m radius around a point.

  POST /api/flood/report
    Body: {"session_id": "<uuid>", "lat": <float>, "lng": <float>}
    Server-side payment gate: returns HTTP 402 unless the session has a paid
    purchase whose stored location is within 500m of the requested point.
    Never trusts the client — data is only released after DB verification.

PostGIS query:
  ST_DWithin(geom, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, 500)
  Uses GIST index on flood_events.geom (migration 003).
"""

import uuid as _uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from geoalchemy2.functions import ST_DWithin, ST_MakePoint, ST_SetSRID
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import cast, func, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.flood_event import FloodEvent
from api.models.purchased_report import PurchasedReport
from api.models.ward_centroid import WardCentroid
from api.utils.text import clean_district, normalize_ward

router = APIRouter()

_RADIUS_M = 2000
_LAST_N_YEARS = 5

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _validate_lat(v: float) -> float:
    if not -90 <= v <= 90:
        raise ValueError("lat must be between -90 and 90")
    return v


def _validate_lng(v: float) -> float:
    if not -180 <= v <= 180:
        raise ValueError("lng must be between -180 and 180")
    return v


def _geography_point(lng: float, lat: float):
    """SQLAlchemy expression: ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography"""
    return cast(
        ST_SetSRID(ST_MakePoint(lng, lat), 4326),
        type_=WardCentroid.geom.type,
    )


async def _fetch_events_near(
    db: AsyncSession, lat: float, lng: float
) -> list:
    """Return all flood events within 500m of the given point, newest first."""
    pt = _geography_point(lng, lat)
    stmt = (
        select(FloodEvent)
        .where(ST_DWithin(FloodEvent.geom, pt, _RADIUS_M))
        .order_by(FloodEvent.event_date.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def _fetch_events_combined(
    db: AsyncSession,
    lat: float,
    lng: float,
    location_name: str | None,
    nearest_ward: WardCentroid | None = None,
) -> list:
    """
    Union of:
      1. geom-based (500m radius) events,
      2. location_name ILIKE matches,
      3. coordinate-less rows (geom IS NULL) whose ward or district matches
         the nearest ward.
    Deduplicates by event ID, sorted newest-first.
    Used for paid report when searching a named street/landmark.
    """
    if nearest_ward is None:
        nearest_ward = await _nearest_ward(db, lat, lng)

    branches: list[list] = [list(await _fetch_events_near(db, lat, lng))]

    if location_name:
        name_stmt = (
            select(FloodEvent)
            .where(FloodEvent.location_name.ilike(f"%{location_name}%"))
            .order_by(FloodEvent.event_date.desc())
        )
        name_result = await db.execute(name_stmt)
        branches.append(list(name_result.scalars().all()))

    if nearest_ward is not None:
        # Rows injected directly into the DB may have no coordinates. They
        # still belong to the report of their ward / district, just without
        # a map pin. NULL-geom rows are the exception, so filtering the
        # district variants in Python is cheaper than SQL normalisation.
        no_geom_result = await db.execute(
            select(FloodEvent)
            .where(FloodEvent.geom.is_(None))
            .order_by(FloodEvent.event_date.desc())
        )
        # Stored wards come in both forms ("phuong cau giay" and "cau giay");
        # /api/search joins on both, so match both here too.
        ward_keys = {nearest_ward.ward_name, nearest_ward.ward_name.removeprefix("phuong ")}
        district_key = _district_key(nearest_ward.district)
        branches.append(
            [
                e
                for e in no_geom_result.scalars().all()
                if e.ward in ward_keys
                or (
                    district_key is not None
                    and _district_key(e.district) == district_key
                )
            ]
        )

    seen: set = set()
    combined = []
    for events in branches:
        for e in events:
            if e.id not in seen:
                seen.add(e.id)
                combined.append(e)
    combined.sort(key=lambda e: e.event_date, reverse=True)
    return combined


def _district_key(value: str | None) -> str | None:
    """
    Canonical key for district comparison: "Quận Ba Đình" and "Ba Đình" match.
    """
    cleaned = clean_district(value)
    if not cleaned:
        return None
    key = normalize_ward(cleaned)
    for prefix in ("quan ", "huyen ", "thi xa ", "thanh pho "):
        if key.startswith(prefix):
            return key[len(prefix):]
    return key


async def _nearest_ward(
    db: AsyncSession, lat: float, lng: float
) -> WardCentroid | None:
    """Return the nearest ward centroid to the given point."""
    pt = _geography_point(lng, lat)
    stmt = (
        select(WardCentroid)
        .order_by(WardCentroid.geom.distance_centroid(pt))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Teaser endpoint — free, no auth
# ---------------------------------------------------------------------------

class TeaserResponse(BaseModel):
    location: str
    has_flood_data: bool
    data_year_range: str | None  # "2019–2024" or None


@router.get("/flood/teaser", response_model=TeaserResponse)
async def flood_teaser(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    db: AsyncSession = Depends(get_db),
) -> TeaserResponse:
    if not -90 <= lat <= 90:
        raise HTTPException(status_code=400, detail="lat must be between -90 and 90")
    if not -180 <= lng <= 180:
        raise HTTPException(status_code=400, detail="lng must be between -180 and 180")

    events = await _fetch_events_near(db, lat, lng)
    ward = await _nearest_ward(db, lat, lng)

    district = clean_district(ward.district) if ward else None
    location = (
        f"{ward.ward_name_vi}, {district}" if ward and district
        else ward.ward_name_vi if ward
        else f"{lat:.4f}, {lng:.4f}"
    )

    has_data = len(events) > 0
    year_range: str | None = None
    if has_data:
        years = sorted({e.event_date.year for e in events})
        year_range = f"{years[0]}–{years[-1]}"  # en-dash

    return TeaserResponse(
        location=location,
        has_flood_data=has_data,
        data_year_range=year_range,
    )


# ---------------------------------------------------------------------------
# Full report endpoint — payment-gated
# ---------------------------------------------------------------------------

class ReportRequest(BaseModel):
    session_id: str = Field(..., description="UUID stored in client localStorage")
    lat: float
    lng: float
    location_name: str | None = Field(
        None,
        description="Optional searched location name (street/landmark alias_vi). "
                    "When provided, the report unions geom-radius events with "
                    "events matching location_name ILIKE.",
    )

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, v: float) -> float:
        return _validate_lat(v)

    @field_validator("lng")
    @classmethod
    def validate_lng(cls, v: float) -> float:
        return _validate_lng(v)


class FloodEventDetail(BaseModel):
    event_date: str
    severity: str
    depth_cm: int | None
    duration_hours: int | None
    source: str | None
    source_url: str | None
    ward: str | None
    location_name: str | None
    lat: float | None
    lng: float | None


class ReportSummary(BaseModel):
    total_events: int
    events_last_5_years: int
    most_severe: str | None
    avg_depth_cm: float | None


class ReportResponse(BaseModel):
    location: str
    flood_events: list[FloodEventDetail]
    summary: ReportSummary


@router.post("/flood/report", response_model=ReportResponse)
async def flood_report(
    body: ReportRequest,
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    # Parse and validate session_id
    try:
        session_uuid = _uuid.UUID(body.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id must be a valid UUID")

    # Server-side payment gate:
    # Find a paid purchase for this session whose stored location is within 500m
    # of the requested point.  Both conditions must hold — session AND location.
    pt = _geography_point(body.lng, body.lat)

    purchase_stmt = (
        select(PurchasedReport)
        .where(
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
                _RADIUS_M,
            ),
        )
        # A session can hold several paid purchases inside the radius
        # (repeat purchases, overlapping wards). Any paid one unlocks the
        # report — take the most recent instead of failing on multiple rows.
        .order_by(PurchasedReport.created_at.desc())
        .limit(1)
    )
    purchase_result = await db.execute(purchase_stmt)
    purchase = purchase_result.scalars().first()

    if purchase is None:
        raise HTTPException(
            status_code=402,
            detail={"error": "Payment required"},
        )

    # Payment verified — fetch full flood data (geom + optional location_name match)
    ward = await _nearest_ward(db, body.lat, body.lng)
    events = await _fetch_events_combined(
        db, body.lat, body.lng, body.location_name, nearest_ward=ward
    )
    if body.location_name:
        location = body.location_name
    elif ward:
        district = clean_district(ward.district)
        location = (
            f"{ward.ward_name_vi}, {district}" if district else ward.ward_name_vi
        )
    else:
        location = f"{body.lat:.4f}, {body.lng:.4f}"

    cutoff_year = datetime.now().year - _LAST_N_YEARS
    events_recent = [e for e in events if e.event_date.year >= cutoff_year]

    severity_order = {"severe": 3, "moderate": 2, "minor": 1}
    most_severe: str | None = None
    if events:
        most_severe = max(
            (e.severity for e in events if e.severity),
            key=lambda s: severity_order.get(s, 0),
            default=None,
        )

    depths = [e.depth_cm for e in events if e.depth_cm is not None]
    avg_depth = round(sum(depths) / len(depths), 1) if depths else None

    flood_details = [
        FloodEventDetail(
            event_date=str(e.event_date),
            severity=e.severity or "minor",
            depth_cm=e.depth_cm,
            duration_hours=e.duration_hours,
            source=e.source,
            source_url=e.source_url,
            ward=e.ward,
            location_name=e.location_name,
            lat=float(e.lat) if e.lat is not None else None,
            lng=float(e.lng) if e.lng is not None else None,
        )
        for e in events
    ]

    return ReportResponse(
        location=location,
        flood_events=flood_details,
        summary=ReportSummary(
            total_events=len(events),
            events_last_5_years=len(events_recent),
            most_severe=most_severe,
            avg_depth_cm=avg_depth,
        ),
    )
