"""
Admin bulk flood-location CSV import.

Endpoints:
  GET  /admin/flood-import/template   download CSV template (UTF-8 BOM)
  POST /admin/flood-import            upload CSV (multipart), optional dry_run

Auth: X-Admin-Token header (same require_admin as admin_crawl).

Behaviour:
  - Whole-file problems (bad encoding, missing required header, too large,
    too many rows, not CSV) -> 422, nothing written.
  - Per-row problems -> that row is skipped and reported in `errors`; valid
    rows are still imported (partial import). Each row runs in its own
    SAVEPOINT via begin_nested().
  - Rows are deduplicated by the unique index
    uidx_flood_events_upsert (source_url, event_date, COALESCE(ward,''),
    COALESCE(location_name,'')). Blank source_url is stored as
    `manual://admin-csv` so re-uploading the same CSV does not duplicate.
  - Admin always supplies lat/lng; no geocoding fallback.
  - dry_run=true validates only and reports what would happen.
"""

import csv
import io
import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.cache import cache_delete_prefix
from api.database import get_db
from api.routers.admin_crawl import require_admin
from api.utils.text import clean_district, normalize_ward

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_FILE_BYTES = 1_000_000
MAX_ROWS = 2_000
MANUAL_SOURCE_URL = "manual://admin-csv"

# Hanoi sanity bounding box for admin-supplied coordinates.
_LAT_MIN, _LAT_MAX = 20.5, 21.5
_LNG_MIN, _LNG_MAX = 105.2, 106.1

_SEVERITIES = {"minor", "moderate", "severe"}
_SOURCES = {"official", "news", "community"}

_REQUIRED_HEADERS = ("location_name", "event_date", "severity", "lat", "lng")
_KNOWN_HEADERS = _REQUIRED_HEADERS + (
    "ward",
    "district",
    "depth_cm",
    "duration_hours",
    "source",
    "source_url",
    "cause",
)

_TEMPLATE_HEADER = list(_KNOWN_HEADERS)
_TEMPLATE_ROWS = [
    {
        "location_name": "Ngã tư Vành Đai 3 giao BigC Thăng Long",
        "event_date": "2025-09-15",
        "severity": "moderate",
        "lat": "21.030000",
        "lng": "105.780000",
        "ward": "Mỹ Đình I",
        "district": "Nam Từ Liêm",
        "depth_cm": "35",
        "duration_hours": "4",
        "source": "community",
        "source_url": "https://example.com/flood-report",
        "cause": "mưa lớn",
    },
    {
        "location_name": "Phố Huế, gần chợ Hôm",
        "event_date": "2025-08-20",
        "severity": "minor",
        "lat": "21.014500",
        "lng": "105.850000",
        "ward": "Phố Huế",
        "district": "Hai Bà Trưng",
        "depth_cm": "",
        "duration_hours": "",
        "source": "",
        "source_url": "",
        "cause": "",
    },
]


class RowError(BaseModel):
    row: int
    field: str
    message: str


class ImportRowResult(BaseModel):
    row: int
    location_name: str
    event_date: str
    severity: str
    ward: str | None = None
    district: str | None = None
    status: str  # "inserted" | "duplicate" | "error"
    # Raw values echoed so the UI can rebuild a re-uploadable error CSV.
    lat: str | None = None
    lng: str | None = None
    depth_cm: str | None = None
    duration_hours: str | None = None
    source: str | None = None
    source_url: str | None = None
    cause: str | None = None


class ImportResponse(BaseModel):
    inserted: int
    duplicates: int
    skipped: int
    errors: list[RowError]
    rows: list[ImportRowResult]
    dry_run: bool


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _template_bytes() -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_TEMPLATE_HEADER, lineterminator="\r\n")
    writer.writeheader()
    for row in _TEMPLATE_ROWS:
        writer.writerow(row)
    # Excel shows Vietnamese correctly only with a BOM.
    return "﻿".encode("utf-8") + buf.getvalue().encode("utf-8")


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1258")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail="File không đúng định dạng văn bản (UTF-8 hoặc CP1258).",
        ) from exc


def _parse_rows(csv_text: str) -> list[dict[str, str]]:
    """Parse CSV text after validating required headers."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise HTTPException(status_code=422, detail="File CSV không có dòng tiêu đề.")

    # Case-insensitive, whitespace-trimmed header match.
    field_map = {name.strip().lower(): name for name in reader.fieldnames if name}
    missing = [h for h in _REQUIRED_HEADERS if h not in field_map]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Thiếu cột bắt buộc: {', '.join(missing)}",
        )

    rows: list[dict[str, str]] = []
    for raw in reader:
        mapped = {
            key: (raw.get(original) or "").strip()
            for key, original in field_map.items()
        }
        # Skip completely blank lines.
        if not any(mapped.values()):
            continue
        rows.append(mapped)
    return rows


def _row_error(row_no: int, field: str, message: str) -> RowError:
    return RowError(row=row_no, field=field, message=message)


def _validate_row(row: dict[str, str], row_no: int) -> tuple[dict, list[RowError]]:
    """Validate one CSV row. Returns (clean_values, errors)."""
    errors: list[RowError] = []

    location_name = row.get("location_name", "").strip()
    if not location_name:
        errors.append(_row_error(row_no, "location_name", "Không được để trống"))
    elif len(location_name) > 255:
        errors.append(_row_error(row_no, "location_name", "Tối đa 255 ký tự"))

    event_date: date | None = None
    raw_date = row.get("event_date", "").strip()
    if not raw_date:
        errors.append(_row_error(row_no, "event_date", "Không được để trống"))
    else:
        try:
            event_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            errors.append(
                _row_error(row_no, "event_date", "Định dạng phải là YYYY-MM-DD")
            )
        else:
            if event_date > datetime.now(timezone.utc).date():
                errors.append(_row_error(row_no, "event_date", "Không được ở tương lai"))

    severity = row.get("severity", "").strip().lower()
    if not severity:
        errors.append(_row_error(row_no, "severity", "Không được để trống"))
    elif severity not in _SEVERITIES:
        errors.append(
            _row_error(row_no, "severity", "Chỉ nhận minor / moderate / severe")
        )

    lat = _parse_coord(row.get("lat", ""), -90, 90, row_no, "lat", errors)
    lng = _parse_coord(row.get("lng", ""), -180, 180, row_no, "lng", errors)
    if lat is not None and not (_LAT_MIN <= lat <= _LAT_MAX):
        errors.append(
            _row_error(row_no, "lat", f"Ngoài phạm vi Hà Nội ({_LAT_MIN}–{_LAT_MAX})")
        )
    if lng is not None and not (_LNG_MIN <= lng <= _LNG_MAX):
        errors.append(
            _row_error(row_no, "lng", f"Ngoài phạm vi Hà Nội ({_LNG_MIN}–{_LNG_MAX})")
        )

    ward_raw = row.get("ward", "").strip()
    ward = normalize_ward(ward_raw) if ward_raw else None
    district = clean_district(row.get("district", "").strip() or None)

    depth_cm = _parse_int(row.get("depth_cm", ""), row_no, "depth_cm", errors)
    duration_hours = _parse_int(
        row.get("duration_hours", ""), row_no, "duration_hours", errors
    )

    source = row.get("source", "").strip().lower() or "community"
    if source not in _SOURCES:
        errors.append(
            _row_error(row_no, "source", "Chỉ nhận official / news / community")
        )

    source_url = row.get("source_url", "").strip() or None
    if source_url and not source_url.startswith(("http://", "https://")):
        errors.append(_row_error(row_no, "source_url", "Phải bắt đầu bằng http(s)://"))
    stored_source_url = source_url or MANUAL_SOURCE_URL

    cause = row.get("cause", "").strip() or None

    return (
        {
            "location_name": location_name,
            "event_date": event_date,
            "severity": severity,
            "lat": lat,
            "lng": lng,
            "ward": ward,
            "district": district,
            "depth_cm": depth_cm,
            "duration_hours": duration_hours,
            "source": source,
            "source_url": stored_source_url,
            "cause": cause,
        },
        errors,
    )


def _parse_coord(
    raw: str,
    low: float,
    high: float,
    row_no: int,
    field: str,
    errors: list[RowError],
) -> float | None:
    if not raw:
        errors.append(_row_error(row_no, field, "Không được để trống"))
        return None
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        errors.append(_row_error(row_no, field, "Phải là số thực"))
        return None
    if not (low <= value <= high):
        errors.append(_row_error(row_no, field, f"Ngoài khoảng {low}..{high}"))
        return None
    return value


def _parse_int(
    raw: str, row_no: int, field: str, errors: list[RowError]
) -> int | None:
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        errors.append(_row_error(row_no, field, "Phải là số nguyên"))
        return None
    if value < 0:
        errors.append(_row_error(row_no, field, "Không được âm"))
        return None
    return value


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_EXISTS_SQL = text("""
    SELECT 1
    FROM flood_events
    WHERE source_url = :source_url
      AND event_date = :event_date
      AND COALESCE(ward, '') = :ward
      AND COALESCE(location_name, '') = :location_name
    LIMIT 1
""")

_INSERT_SQL = text("""
    INSERT INTO flood_events
      (id, location_name, ward, district, lat, lng, geom,
       event_date, severity, depth_cm, duration_hours, source, source_url,
       cause, is_seed, resolution_level, resolution_confidence)
    VALUES
      (gen_random_uuid(), :location_name, :ward, :district,
       :lat, :lng,
       ST_SetSRID(ST_MakePoint(:lng_pt, :lat_pt), 4326)::geography,
       CAST(:event_date AS date), :severity, :depth_cm, :duration_hours,
       :source, :source_url, :cause, false, 'manual', 1.0)
    ON CONFLICT (source_url, event_date, COALESCE(ward, ''), COALESCE(location_name, ''))
    DO NOTHING
""")


def _bind_params(values: dict) -> dict:
    return {
        **values,
        "ward": values["ward"] or "",
        # asyncpg needs a real date object (a str raises DataError).
        "event_date": values["event_date"],
        "lat": Decimal(str(values["lat"])),
        "lng": Decimal(str(values["lng"])),
        # Separate binds: one param cannot be both NUMERIC (column) and
        # DOUBLE PRECISION (ST_MakePoint) -> AmbiguousParameterError.
        "lat_pt": values["lat"],
        "lng_pt": values["lng"],
    }


def _row_result(
    idx: int, row: dict[str, str], values: dict, status: str
) -> ImportRowResult:
    event_date = values.get("event_date")
    return ImportRowResult(
        row=idx,
        location_name=values.get("location_name") or row.get("location_name", ""),
        event_date=(
            event_date.isoformat()
            if event_date is not None
            else row.get("event_date", "")
        ),
        severity=values.get("severity") or row.get("severity", ""),
        ward=values.get("ward"),
        district=values.get("district"),
        status=status,
        lat=row.get("lat") or None,
        lng=row.get("lng") or None,
        depth_cm=row.get("depth_cm") or None,
        duration_hours=row.get("duration_hours") or None,
        source=row.get("source") or None,
        source_url=row.get("source_url") or None,
        cause=row.get("cause") or None,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/admin/flood-import/template",
    dependencies=[Depends(require_admin)],
)
async def download_template() -> Response:
    return Response(
        content=_template_bytes(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                'attachment; filename="flood_locations_template.csv"'
            )
        },
    )


@router.post(
    "/admin/flood-import",
    response_model=ImportResponse,
    dependencies=[Depends(require_admin)],
)
async def import_flood_csv(
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    db: AsyncSession = Depends(get_db),
) -> ImportResponse:
    filename = (file.filename or "").lower()
    if not filename.endswith(".csv"):
        raise HTTPException(status_code=422, detail="Chỉ nhận file .csv")

    raw = await file.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"File vượt quá {MAX_FILE_BYTES // 1000} KB",
        )
    if not raw:
        raise HTTPException(status_code=422, detail="File rỗng")

    csv_text = _decode(raw)
    parsed_rows = _parse_rows(csv_text)
    if len(parsed_rows) > MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"Tối đa {MAX_ROWS} dòng dữ liệu",
        )

    errors: list[RowError] = []
    results: list[ImportRowResult] = []
    inserted = duplicates = skipped = 0

    for idx, row in enumerate(parsed_rows, start=2):  # row 1 = header
        values, row_errors = _validate_row(row, idx)
        if row_errors:
            errors.extend(row_errors)
            skipped += 1
            results.append(_row_result(idx, row, values, "error"))
            continue

        params = _bind_params(values)

        if dry_run:
            exists = (await db.execute(_EXISTS_SQL, params)).first()
            status = "duplicate" if exists else "inserted"
        else:
            try:
                async with db.begin_nested():
                    result = await db.execute(_INSERT_SQL, params)
                status = "inserted" if result.rowcount else "duplicate"
            except Exception as exc:  # noqa: BLE001 - per-row isolation
                logger.exception("admin_import: row %s failed", idx)
                errors.append(_row_error(idx, "db", str(exc)))
                skipped += 1
                results.append(_row_result(idx, row, values, "error"))
                continue

        if status == "inserted":
            inserted += 1
        else:
            duplicates += 1

        results.append(_row_result(idx, row, values, status))

    if not dry_run and inserted:
        await db.commit()
        # New pins invalidate cached search results. No-op without Redis.
        await cache_delete_prefix("search:v2:")

    return ImportResponse(
        inserted=inserted,
        duplicates=duplicates,
        skipped=skipped,
        errors=errors,
        rows=results,
        dry_run=dry_run,
    )
