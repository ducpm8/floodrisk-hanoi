"""
Integration tests for the admin CSV import against a real PostGIS database.

Requires migrations applied.
    cd api/
    DATABASE_TEST_URL=postgresql+psycopg2://flood:flood@localhost:5433/flooddb_test \
        pytest tests/integration/test_admin_import.py -v
"""

import asyncio
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.database import get_db
from api.main import app

_WARD = "my dinh i"
_WARD_VI = "Mỹ Đình I"
_DISTRICT = "Nam Từ Liêm"
_LAT = 21.031
_LNG = 105.773

_HEADER = (
    "location_name,event_date,severity,lat,lng,ward,district,"
    "depth_cm,duration_hours,source,source_url,cause\n"
)


def _row(name: str = "Ngã tư Vành Đai 3", source_url: str = "") -> str:
    return (
        f"{name},2025-09-15,moderate,{_LAT},{_LNG},"
        f"{_WARD_VI},{_DISTRICT},35,4,community,{source_url},mưa lớn\n"
    )


@pytest.fixture(scope="module")
def import_db(migrated_db: str) -> str:
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ward_centroids (ward_name, ward_name_vi, district, lat, lng, geom)
                VALUES (
                    :ward, :ward_vi, :district, :lat, :lng,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                )
                ON CONFLICT DO NOTHING
            """),
            {
                "ward": _WARD,
                "ward_vi": _WARD_VI,
                "district": _DISTRICT,
                "lat": _LAT,
                "lng": _LNG,
            },
        )
        conn.execute(
            text("DELETE FROM flood_events WHERE location_name LIKE 'IT Import%'")
        )
        conn.execute(
            text("DELETE FROM purchased_reports WHERE location_query = 'it-import'")
        )
    yield migrated_db
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM flood_events WHERE location_name LIKE 'IT Import%'")
        )
        conn.execute(
            text("DELETE FROM purchased_reports WHERE location_query = 'it-import'")
        )
    engine.dispose()


@pytest.fixture(scope="module")
def client(import_db: str):
    """TestClient whose get_db is bound to the test DB, not the dev DB."""
    async_url = import_db.replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://", 1
    )
    engine = create_async_engine(async_url, poolclass=None)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        asyncio.run(engine.dispose())


def _post(client: TestClient, body: str, dry_run: bool = False):
    return client.post(
        "/api/admin/flood-import",
        files={"file": ("flood.csv", body.encode("utf-8"), "text/csv")},
        data={"dry_run": "true" if dry_run else "false"},
    )


def test_import_writes_rows_with_geom_and_source(import_db: str, client) -> None:
    engine = create_engine(import_db)
    r = _post(client, _HEADER + _row("IT Import A"))
    assert r.status_code == 200
    assert r.json()["inserted"] == 1

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT source, is_seed, resolution_level,
                       ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lng
                FROM flood_events WHERE location_name = 'IT Import A'
            """)
        ).one()
    assert row.source == "community"
    assert row.is_seed is False
    assert row.resolution_level == "manual"
    assert abs(float(row.lat) - _LAT) < 1e-6
    assert abs(float(row.lng) - _LNG) < 1e-6
    engine.dispose()


def test_reupload_reports_duplicates(client) -> None:
    _post(client, _HEADER + _row("IT Import B"))
    second = _post(client, _HEADER + _row("IT Import B"))
    assert second.json()["inserted"] == 0
    assert second.json()["duplicates"] == 1


def test_dry_run_writes_nothing(import_db: str, client) -> None:
    engine = create_engine(import_db)
    r = _post(client, _HEADER + _row("IT Import Dry"), dry_run=True)
    assert r.json()["dry_run"] is True
    assert r.json()["inserted"] == 1

    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM flood_events WHERE location_name = 'IT Import Dry'")
        ).scalar_one()
    assert count == 0
    engine.dispose()


def test_no_coordinate_row_appears_in_paid_report_only(
    import_db: str, client
) -> None:
    session_id = str(uuid.uuid4())
    engine = create_engine(import_db)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO flood_events
                  (location_name, ward, district, event_date, severity, source,
                   is_seed, resolution_level, resolution_confidence)
                VALUES
                  ('IT Import No Geom', :ward, :district, '2025-06-01', 'minor',
                   'community', false, 'manual', 1.0)
            """),
            {"ward": _WARD, "district": _DISTRICT},
        )
        conn.execute(
            text("""
                INSERT INTO purchased_reports
                  (id, session_id, email, location_lat, location_lng, status,
                   amount_vnd, location_query)
                VALUES
                  (gen_random_uuid(), :session, 'it@example.com', :lat, :lng,
                   'paid', 35000, 'it-import')
            """),
            {"session": session_id, "lat": _LAT, "lng": _LNG},
        )

    try:
        report = client.post(
            "/api/flood/report",
            json={"session_id": session_id, "lat": _LAT, "lng": _LNG},
        )
        zones = client.get("/api/flood-zones")
        teaser = client.get(f"/api/flood/teaser?lat={_LAT}&lng={_LNG}")

        assert report.status_code == 200
        names = [e["location_name"] for e in report.json()["flood_events"]]
        assert "IT Import No Geom" in names

        assert all(
            z["lat"] is not None
            for z in zones.json().get("zones", zones.json() if isinstance(zones.json(), list) else [])
        )
        assert "IT Import No Geom" not in teaser.text
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM flood_events WHERE location_name = 'IT Import No Geom'")
            )
            conn.execute(
                text("DELETE FROM purchased_reports WHERE location_query = 'it-import'")
            )
        engine.dispose()
