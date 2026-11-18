"""
Unit tests for FIR-7: GET /api/flood/teaser + POST /api/flood/report

All DB calls are mocked via dependency_overrides — no real PostGIS required.
"""

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.database import get_db
from api.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    lat=21.024, lng=105.841, severity="moderate",
    event_date=date(2023, 9, 15), depth_cm=40,
    duration_hours=6, source="official",
    source_url="https://thoatnuochanoi.vn/test",
    ward="trung liet", location_name=None,
):
    e = SimpleNamespace()
    e.id = uuid.uuid4()
    e.lat, e.lng = lat, lng
    e.severity = severity
    e.event_date = event_date
    e.depth_cm = depth_cm
    e.duration_hours = duration_hours
    e.source = source
    e.source_url = source_url
    e.ward = ward
    e.location_name = location_name
    return e


def _make_ward(ward_name_vi="Tây Sơn", district="Đống Đa"):
    from api.utils.text import normalize_ward

    w = SimpleNamespace()
    w.ward_name_vi = ward_name_vi
    w.ward_name = normalize_ward(ward_name_vi)
    w.district = district
    return w


def _make_purchase(session_id=None, status="paid"):
    p = SimpleNamespace()
    p.session_id = session_id or uuid.uuid4()
    p.status = status
    return p


_SENTINEL = object()  # distinguishes "no purchase found" from not-provided


def _teaser_db(events=None, ward=None):
    """Mock for teaser flow: call 1=events list, call 2=nearest ward."""
    call_count = 0

    async def _get_db():
        nonlocal call_count

        async def mock_execute(stmt, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                mock_result.scalars.return_value.all.return_value = events or []
            else:
                mock_result.scalar_one_or_none.return_value = ward
            return mock_result

        session = AsyncMock()
        session.execute = mock_execute
        yield session

    return _get_db


def _report_db(purchase=_SENTINEL, events=None, ward=None, name_events=None, no_geom_events=None):
    """
    Mock for report flow. Statements are matched by SQL text so the optional
    location_name / NULL-geom branches can be reordered without breaking tests.
    """
    resolved_purchase = None if purchase is _SENTINEL else purchase

    async def _get_db():
        async def mock_execute(stmt, *args, **kwargs):
            sql = str(stmt)
            mock_result = MagicMock()
            if "purchased_reports" in sql:
                mock_result.scalars.return_value.first.return_value = resolved_purchase
            elif "ward_centroids" in sql:
                mock_result.scalar_one_or_none.return_value = ward
            elif "geom IS NULL" in sql:
                mock_result.scalars.return_value.all.return_value = no_geom_events or []
            elif "ILIKE" in sql.upper():
                mock_result.scalars.return_value.all.return_value = name_events or []
            else:
                mock_result.scalars.return_value.all.return_value = events or []
            return mock_result

        session = AsyncMock()
        session.execute = mock_execute
        yield session

    return _get_db


# Keep backwards-compatible alias used by a few tests below
def _db_override(
    events=None, ward=None, purchase=_SENTINEL, name_events=None, no_geom_events=None
):
    if purchase is _SENTINEL:
        return _teaser_db(events=events, ward=ward)
    return _report_db(
        purchase=purchase,
        events=events,
        ward=ward,
        name_events=name_events,
        no_geom_events=no_geom_events,
    )


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /api/flood/teaser — happy path
# ---------------------------------------------------------------------------

def test_teaser_returns_200():
    app.dependency_overrides[get_db] = _teaser_db(events=[_make_event()], ward=_make_ward())
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024&lng=105.841")
    assert r.status_code == 200


def test_teaser_response_shape():
    # Spec v2: only location, has_flood_data, data_year_range exposed pre-payment
    app.dependency_overrides[get_db] = _teaser_db(events=[_make_event()], ward=_make_ward())
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024&lng=105.841")
    data = r.json()
    for field in ("location", "has_flood_data", "data_year_range"):
        assert field in data, f"missing field: {field}"
    for private in ("flood_zone_count", "most_recent_event", "risk_level", "markers"):
        assert private not in data, f"private field leaked: {private}"


def test_teaser_location_uses_ward_name():
    app.dependency_overrides[get_db] = _teaser_db(
        events=[_make_event()], ward=_make_ward("Tây Sơn", "Đống Đa")
    )
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024&lng=105.841")
    assert "Tây Sơn" in r.json()["location"]
    assert "Đống Đa" in r.json()["location"]


def test_teaser_has_flood_data_true_with_events():
    events = [_make_event(), _make_event(lat=21.025), _make_event(lat=21.026)]
    app.dependency_overrides[get_db] = _teaser_db(events=events, ward=_make_ward())
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024&lng=105.841")
    assert r.json()["has_flood_data"] is True


def test_teaser_data_year_range_single_year():
    events = [_make_event(event_date=date(2023, 9, 15))]
    app.dependency_overrides[get_db] = _teaser_db(events=events, ward=_make_ward())
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024&lng=105.841")
    assert r.json()["data_year_range"] == "2023–2023"


def test_teaser_no_events_returns_false_has_flood_data():
    app.dependency_overrides[get_db] = _teaser_db(events=[], ward=_make_ward())
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024&lng=105.841")
    data = r.json()
    assert data["has_flood_data"] is False
    assert data["data_year_range"] is None


def test_teaser_data_year_range_spans_multiple_years():
    events = [
        _make_event(event_date=date(2019, 3, 1)),
        _make_event(event_date=date(2022, 7, 15)),
        _make_event(event_date=date(2024, 11, 30)),
    ]
    app.dependency_overrides[get_db] = _teaser_db(events=events, ward=_make_ward())
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024&lng=105.841")
    assert r.json()["data_year_range"] == "2019–2024"


def test_teaser_has_flood_data_true_regardless_of_severity():
    # Spec v2: severity is paid data — teaser only reveals existence, not level
    events = [_make_event(severity="severe")]
    app.dependency_overrides[get_db] = _teaser_db(events=events, ward=_make_ward())
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024&lng=105.841")
    data = r.json()
    assert data["has_flood_data"] is True
    assert "risk_level" not in data


def test_teaser_unknown_district_omitted_from_location():
    """FIR-84: legacy 'Unknown' district must not leak into the location label."""
    app.dependency_overrides[get_db] = _teaser_db(
        events=[_make_event()], ward=_make_ward("Tây Sơn", "Unknown")
    )
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024&lng=105.841")

    location = r.json()["location"]
    assert "Tây Sơn" in location
    assert "Unknown" not in location


# ---------------------------------------------------------------------------
# GET /api/flood/teaser — input validation
# ---------------------------------------------------------------------------

def test_teaser_invalid_lat_returns_400():
    app.dependency_overrides[get_db] = _teaser_db(events=[], ward=None)
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=999&lng=105.841")
    assert r.status_code == 400


def test_teaser_invalid_lng_returns_400():
    app.dependency_overrides[get_db] = _teaser_db(events=[], ward=None)
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024&lng=999")
    assert r.status_code == 400


def test_teaser_missing_lat_returns_422():
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lng=105.841")
    assert r.status_code == 422


def test_teaser_missing_lng_returns_422():
    with TestClient(app) as client:
        r = client.get("/api/flood/teaser?lat=21.024")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/flood/report — 402 gate (no payment)
# ---------------------------------------------------------------------------

def test_report_no_payment_returns_402():
    """No purchase in DB → 402 Payment Required."""
    app.dependency_overrides[get_db] = _report_db()  # sentinel → scalar_one_or_none=None
    with TestClient(app) as client:
        r = client.post("/api/flood/report", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024, "lng": 105.841,
        })
    assert r.status_code == 402


def test_report_returns_402_even_if_session_exists_but_unpaid():
    """Unpaid session → still 402."""
    app.dependency_overrides[get_db] = _report_db()  # scalar_one_or_none → None
    with TestClient(app) as client:
        r = client.post("/api/flood/report", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024, "lng": 105.841,
        })
    assert r.status_code == 402
    assert r.json()["detail"]["error"] == "Payment required"


# ---------------------------------------------------------------------------
# POST /api/flood/report — paid path
# ---------------------------------------------------------------------------

def test_report_paid_returns_200():
    purchase = _make_purchase(status="paid")
    events = [_make_event()]
    app.dependency_overrides[get_db] = _report_db(
        purchase=purchase, events=events, ward=_make_ward()
    )
    with TestClient(app) as client:
        r = client.post("/api/flood/report", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024, "lng": 105.841,
        })
    assert r.status_code == 200


def test_report_includes_source_attribution():
    purchase = _make_purchase(status="paid")
    events = [_make_event(source="official", source_url="https://thoatnuochanoi.vn/x")]
    app.dependency_overrides[get_db] = _report_db(
        purchase=purchase, events=events, ward=_make_ward()
    )
    with TestClient(app) as client:
        r = client.post("/api/flood/report", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024, "lng": 105.841,
        })
    event = r.json()["flood_events"][0]
    assert event["source"] == "official"
    assert "thoatnuochanoi" in event["source_url"]


def test_report_empty_flood_events_returns_zero_summary():
    purchase = _make_purchase(status="paid")
    app.dependency_overrides[get_db] = _report_db(
        purchase=purchase, events=[], ward=_make_ward()
    )
    with TestClient(app) as client:
        r = client.post("/api/flood/report", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024, "lng": 105.841,
        })
    data = r.json()
    assert data["flood_events"] == []
    assert data["summary"]["total_events"] == 0


def test_report_summary_most_severe():
    purchase = _make_purchase(status="paid")
    events = [
        _make_event(severity="minor"),
        _make_event(severity="severe"),
        _make_event(severity="moderate"),
    ]
    app.dependency_overrides[get_db] = _report_db(
        purchase=purchase, events=events, ward=_make_ward()
    )
    with TestClient(app) as client:
        r = client.post("/api/flood/report", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024, "lng": 105.841,
        })
    assert r.json()["summary"]["most_severe"] == "severe"


def test_report_summary_avg_depth():
    purchase = _make_purchase(status="paid")
    events = [_make_event(depth_cm=40), _make_event(depth_cm=60)]
    app.dependency_overrides[get_db] = _report_db(
        purchase=purchase, events=events, ward=_make_ward()
    )
    with TestClient(app) as client:
        r = client.post("/api/flood/report", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024, "lng": 105.841,
        })
    assert r.json()["summary"]["avg_depth_cm"] == pytest.approx(50.0)


def test_report_unknown_district_omitted_from_location():
    """FIR-84: paid report location must be 'Ward' — never 'Ward, Unknown'."""
    purchase = _make_purchase(status="paid")
    app.dependency_overrides[get_db] = _report_db(
        purchase=purchase, events=[_make_event()],
        ward=_make_ward("Tây Sơn", "Unknown"),
    )
    with TestClient(app) as client:
        r = client.post("/api/flood/report", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024, "lng": 105.841,
        })

    location = r.json()["location"]
    assert location == "Tây Sơn"
    assert "Unknown" not in location


# ---------------------------------------------------------------------------
# POST /api/flood/report — input validation
# ---------------------------------------------------------------------------

def test_report_invalid_lat_returns_422():
    with TestClient(app) as client:
        r = client.post("/api/flood/report", json={
            "session_id": str(uuid.uuid4()),
            "lat": 999, "lng": 105.841,
        })
    assert r.status_code == 422


def test_report_invalid_session_id_returns_400():
    app.dependency_overrides[get_db] = _report_db()
    with TestClient(app) as client:
        r = client.post("/api/flood/report", json={
            "session_id": "not-a-uuid",
            "lat": 21.024, "lng": 105.841,
        })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/flood/report — coordinate-less rows (3f)
# ---------------------------------------------------------------------------

def _no_geom_event(ward=None, district=None):
    e = _make_event(lat=None, lng=None, ward=ward)
    e.district = district
    e.location_name = "Điểm nhập tay"
    e.geom = None
    return e


def _paid_post():
    with TestClient(app) as client:
        return client.post("/api/flood/report", json={
            "session_id": str(uuid.uuid4()),
            "lat": 21.024, "lng": 105.841,
        })


def test_report_includes_no_coordinate_row_by_ward():
    purchase = _make_purchase(status="paid")
    row = _no_geom_event(ward="my dinh i")
    app.dependency_overrides[get_db] = _report_db(
        purchase=purchase,
        events=[],
        ward=_make_ward("Mỹ Đình I", "Nam Từ Liêm"),
        no_geom_events=[row],
    )

    r = _paid_post()

    assert r.status_code == 200
    body = r.json()
    assert len(body["flood_events"]) == 1
    assert body["flood_events"][0]["lat"] is None
    assert body["flood_events"][0]["lng"] is None


def test_report_includes_no_coordinate_row_by_district_prefix_variant():
    purchase = _make_purchase(status="paid")
    row = _no_geom_event(ward="khac", district="Nam Từ Liêm")
    app.dependency_overrides[get_db] = _report_db(
        purchase=purchase,
        events=[],
        ward=_make_ward("Mỹ Đình I", "Quận Nam Từ Liêm"),
        no_geom_events=[row],
    )

    r = _paid_post()

    assert r.status_code == 200
    assert len(r.json()["flood_events"]) == 1


def test_report_excludes_no_coordinate_row_outside_ward_and_district():
    purchase = _make_purchase(status="paid")
    row = _no_geom_event(ward="ba dinh", district="Ba Đình")
    app.dependency_overrides[get_db] = _report_db(
        purchase=purchase,
        events=[],
        ward=_make_ward("Mỹ Đình I", "Nam Từ Liêm"),
        no_geom_events=[row],
    )

    r = _paid_post()

    assert r.status_code == 200
    assert r.json()["flood_events"] == []


def test_report_no_coordinate_row_deduplicated_against_geom_branch():
    purchase = _make_purchase(status="paid")
    row = _no_geom_event(ward="my dinh i")
    app.dependency_overrides[get_db] = _report_db(
        purchase=purchase,
        events=[row],
        ward=_make_ward("Mỹ Đình I", "Nam Từ Liêm"),
        no_geom_events=[row],
    )

    r = _paid_post()

    assert r.status_code == 200
    assert len(r.json()["flood_events"]) == 1
