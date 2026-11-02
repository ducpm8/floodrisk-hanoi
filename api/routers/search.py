"""
GET /api/search?q=<ward_name>

Returns up to 10 ward matches with has_flood_data + data_year_range.
No flood counts, no source attribution — those only appear in paid /api/report.

Flow:
  1. Check Redis cache (key: search:{q_normalized}, TTL 1h)
  2. normalize_ward(q) -> strip diacritics + lowercase
  3. DB fetches up to _FETCH_LIMIT candidates from two sources:
       a. ward_centroids  — direct ward name ILIKE match
       b. location_aliases — alias ILIKE match, resolved to ward + flood data
  4. rapidfuzz WRatio re-ranks combined results -> take top _MAX_RESULTS
  5. Write result to Redis cache

SearchResult fields:
  location_name  — display name shown as primary label:
                   alias_vi for alias matches, ward_name_vi for direct hits
  location_type  — "ward" | "street" | "apartment" | "landmark" | "other"
  ward           — always ward_name_vi (used by ResultSheet detail panel)
"""

import json
import math
import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from rapidfuzz import fuzz
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.flood_event import FloodEvent
from api.models.location_alias import LocationAlias
from api.models.ward_centroid import WardCentroid
from api.cache import cache_get, cache_set
from api.utils.text import normalize_ward

router = APIRouter()

_MAX_RESULTS = 10
_FETCH_LIMIT = 30  # over-fetch so rapidfuzz can re-rank


class SearchResult(BaseModel):
    ward: str
    ward_normalized: str
    location_name: str
    location_type: str
    district: str
    lat: float
    lng: float
    has_flood_data: bool
    data_year_range: str | None


class SearchResponse(BaseModel):
    results: list[SearchResult]
    total: int
    message: str | None = None


def _build_ward_stmt(extra_where):
    flood_count = func.count(FloodEvent.id).label("flood_count")
    min_year = func.extract("year", func.min(FloodEvent.event_date)).label("min_year")
    max_year = func.extract("year", func.max(FloodEvent.event_date)).label("max_year")
    return (
        select(
            WardCentroid.ward_name_vi,
            WardCentroid.ward_name,
            WardCentroid.district,
            WardCentroid.lat,
            WardCentroid.lng,
            flood_count,
            min_year,
            max_year,
        )
        .outerjoin(
            FloodEvent,
            or_(
                FloodEvent.ward == WardCentroid.ward_name,
                FloodEvent.ward == func.replace(WardCentroid.ward_name, "phuong ", ""),
            ),
        )
        .where(extra_where)
        .group_by(
            WardCentroid.ward_name,
            WardCentroid.ward_name_vi,
            WardCentroid.district,
            WardCentroid.lat,
            WardCentroid.lng,
        )
        .order_by(WardCentroid.ward_name)
        .limit(_FETCH_LIMIT)
    )


@router.get("/search", response_model=SearchResponse)
async def search_wards(
    q: str = Query(..., min_length=1, max_length=100),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    q_normalized = normalize_ward(q)
    if not q_normalized:
        return SearchResponse(
            results=[],
            total=0,
            message="Không tìm thấy địa điểm. Thử tên phường (ví dụ: 'Đống Đa')",
        )

    # Cache hit — skip DB entirely
    cache_key = f"search:{q_normalized}"
    cached = await cache_get(cache_key)
    if cached:
        return SearchResponse(**json.loads(cached))

    # ── Query 1: direct ward name match ──────────────────────────────────────
    direct_rows = (
        await db.execute(
            _build_ward_stmt(WardCentroid.ward_name.ilike(f"%{q_normalized}%"))
        )
    ).fetchall()

    # ── Query 2: alias match → resolve to ward ────────────────────────────────
    alias_stmt = (
        select(
            LocationAlias.alias_norm,
            LocationAlias.alias_vi,
            LocationAlias.alias_type,
            LocationAlias.ward_name,
        )
        .where(LocationAlias.alias_norm.ilike(f"%{q_normalized}%"))
        .limit(_FETCH_LIMIT)
    )
    alias_rows = (await db.execute(alias_stmt)).fetchall()

    _TYPE_PRIORITY = {"street": 0, "apartment": 1, "landmark": 2, "other": 3}

    def _clean_alias_vi(raw: str) -> str:
        """Strip house-range/block parentheticals: 'Lê Trọng Tấn (từ số 16 đến 86)' → 'Lê Trọng Tấn'."""
        return re.sub(r"\s*\(.*?\)", "", raw).strip()

    # Best alias per ward: prefer by type priority, break ties by fuzzy score
    alias_best: dict[str, tuple[float, str, str]] = {}
    for a in alias_rows:
        score = fuzz.WRatio(q_normalized, a.alias_norm)
        raw_name = a.alias_vi or a.alias_norm
        display_name = _clean_alias_vi(raw_name)
        if a.ward_name not in alias_best:
            alias_best[a.ward_name] = (score, display_name, a.alias_type)
        else:
            cur_score, _, cur_type = alias_best[a.ward_name]
            cur_pri = _TYPE_PRIORITY.get(cur_type, 99)
            new_pri = _TYPE_PRIORITY.get(a.alias_type, 99)
            if new_pri < cur_pri or (new_pri == cur_pri and score > cur_score):
                alias_best[a.ward_name] = (score, display_name, a.alias_type)

    # Fetch ward + flood data for alias-matched wards not already in direct_rows
    direct_ward_names = {r.ward_name for r in direct_rows}
    alias_only_wards = [w for w in alias_best if w not in direct_ward_names]

    alias_ward_rows = []
    if alias_only_wards:
        alias_ward_rows = (
            await db.execute(
                _build_ward_stmt(WardCentroid.ward_name.in_(alias_only_wards))
            )
        ).fetchall()

    # ── Radius filter for alias results (5 km cap) ───────────────────────────
    # Anchor = centroid of the highest-scoring alias ward; drop outliers.
    _ALIAS_RADIUS_KM = 5.0

    def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    if alias_ward_rows:
        anchor = max(alias_ward_rows, key=lambda r: alias_best[r.ward_name][0])
        alias_ward_rows = [
            r for r in alias_ward_rows
            if _haversine_km(float(anchor.lat), float(anchor.lng), float(r.lat), float(r.lng)) <= _ALIAS_RADIUS_KM
        ]

    # ── Combine + score ───────────────────────────────────────────────────────
    # ward_name -> (row, score, location_name, location_type, has_data_override)
    # has_data_override=None means use row.flood_count directly
    ward_data: dict[str, tuple] = {}

    for row in direct_rows:
        score = fuzz.WRatio(q_normalized, row.ward_name)
        ward_data[row.ward_name] = (row, score, row.ward_name_vi, "ward", None)

    for ward_row in alias_ward_rows:
        score, alias_vi, alias_type = alias_best[ward_row.ward_name]
        ward_data[ward_row.ward_name] = (ward_row, score, alias_vi, alias_type, ward_row.flood_count > 0)

    if not ward_data:
        return SearchResponse(
            results=[],
            total=0,
            message="Không tìm thấy địa điểm. Thử tên phường (ví dụ: 'Đống Đa')",
        )

    top_items = sorted(ward_data.values(), key=lambda x: x[1], reverse=True)[:_MAX_RESULTS]

    results: list[SearchResult] = []
    for row, _, location_name, location_type, has_data_override in top_items:
        has_data = has_data_override if has_data_override is not None else row.flood_count > 0
        year_range: str | None = (
            f"{int(row.min_year)}–{int(row.max_year)}"  # en-dash
            if has_data and row.min_year and row.max_year
            else None
        )
        results.append(
            SearchResult(
                ward=row.ward_name_vi,
                ward_normalized=row.ward_name,
                location_name=location_name,
                location_type=location_type,
                district=row.district,
                lat=float(row.lat),
                lng=float(row.lng),
                has_flood_data=has_data,
                data_year_range=year_range,
            )
        )

    response = SearchResponse(results=results, total=len(results))
    await cache_set(cache_key, response.model_dump_json())
    return response
