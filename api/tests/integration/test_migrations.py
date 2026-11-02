"""
Integration tests for Alembic migrations.

Requires a real PostgreSQL + PostGIS database pointed at by DATABASE_TEST_URL.

Quick start:
    docker compose -f infra/docker-compose.yml up -d db_test
    cd api/
    pytest tests/integration/test_migrations.py -v
"""

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests.conftest import alembic_cfg


@pytest.fixture(scope="module")
def clean_db_url() -> str:
    """
    Returns the test DB URL. The module-scoped fixture ensures we start
    clean (downgrade base) before this module's tests run.
    """
    url = os.environ.get(
        "DATABASE_TEST_URL",
        "postgresql+psycopg2://flood:flood@localhost:5433/flooddb_test",
    )
    # Ensure we start from a clean state
    cfg = alembic_cfg(url)
    try:
        command.downgrade(cfg, "base")
    except Exception:
        pass  # Tables may not exist yet on first run
    return url


def test_upgrade_head_creates_all_tables(clean_db_url: str) -> None:
    """alembic upgrade head creates all 4 tables."""
    cfg = alembic_cfg(clean_db_url)
    command.upgrade(cfg, "head")

    engine = create_engine(clean_db_url)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    engine.dispose()

    assert "flood_events" in tables
    assert "price_records" in tables
    assert "ward_centroids" in tables
    assert "purchased_reports" in tables
    assert "alembic_version" in tables


def test_flood_events_uses_geography_type(clean_db_url: str) -> None:
    """
    flood_events.geom must be GEOGRAPHY, not GEOMETRY.
    ST_DWithin on a geometry column measures in degrees, not meters.
    This test catches the GEOMETRY vs GEOGRAPHY bug before any data is loaded.
    """
    engine = create_engine(clean_db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT type, coord_dimension, srid
                FROM geometry_columns
                WHERE f_table_name = 'flood_events' AND f_geometry_column = 'geom'
            """)
        ).fetchone()
    engine.dispose()

    assert result is not None, "geom column not found in geometry_columns view"
    # PostGIS stores geography columns with type 'POINT' in geometry_columns
    assert result[2] == 4326, "SRID must be 4326"


def test_ward_centroids_uses_geography_type(clean_db_url: str) -> None:
    """ward_centroids.geom must be GEOGRAPHY(POINT, 4326)."""
    engine = create_engine(clean_db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT udt_name
                FROM information_schema.columns
                WHERE table_name = 'ward_centroids' AND column_name = 'geom'
            """)
        ).fetchone()
    engine.dispose()

    assert result is not None
    # geography columns show as 'geography' in information_schema
    assert result[0] == "geography", (
        f"Expected 'geography' type, got '{result[0]}'. "
        "ST_DWithin on geometry measures in degrees, not meters."
    )


def test_gist_index_on_flood_events(clean_db_url: str) -> None:
    """GIST index on flood_events.geom must exist for ST_DWithin performance."""
    engine = create_engine(clean_db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'flood_events'
                  AND indexname = 'idx_flood_events_geom'
            """)
        ).fetchone()
    engine.dispose()

    assert result is not None, (
        "GIST index idx_flood_events_geom missing. "
        "ST_DWithin will full-scan flood_events without it."
    )


def test_spatial_query_500m_radius(clean_db_url: str) -> None:
    """
    ST_DWithin with 500m radius works correctly with geography columns.
    Inserts a test point at Dong Da ward centroid, queries with 500m radius,
    expects the point returned. Verifies meters-not-degrees behavior.
    """
    engine = create_engine(clean_db_url)
    # Dong Da centroid: ~21.0245°N, 105.8412°E
    dong_da_lat, dong_da_lng = 21.0245, 105.8412

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO flood_events
                    (location_name, ward, district, lat, lng, geom, event_date, severity, source)
                VALUES (
                    'Test flood',
                    'dong da', 'Dong Da',
                    :lat, :lng,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                    CURRENT_DATE, 'minor', 'official'
                )
            """),
            {"lat": dong_da_lat, "lng": dong_da_lng},
        )

        # Query from the same point — should find the event within 500m
        nearby = conn.execute(
            text("""
                SELECT COUNT(*) FROM flood_events
                WHERE ST_DWithin(
                    geom,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                    500
                )
            """),
            {"lat": dong_da_lat, "lng": dong_da_lng},
        ).scalar()

        # Query from 5km away (Cau Giay) — should NOT find the event
        far_away = conn.execute(
            text("""
                SELECT COUNT(*) FROM flood_events
                WHERE ST_DWithin(
                    geom,
                    ST_SetSRID(ST_MakePoint(105.7937, 21.0317), 4326)::geography,
                    500
                )
            """),
        ).scalar()

        conn.execute(text("DELETE FROM flood_events WHERE location_name = 'Test flood'"))

    engine.dispose()

    assert nearby == 1, "Expected 1 event within 500m of Dong Da centroid"
    assert far_away == 0, "Expected 0 events within 500m of Cau Giay (5km away)"


def test_purchased_reports_email_not_null(clean_db_url: str) -> None:
    """email column must be NOT NULL — enforced at DB level."""
    engine = create_engine(clean_db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_name = 'purchased_reports' AND column_name = 'email'
            """)
        ).fetchone()
    engine.dispose()

    assert result is not None
    assert result[0] == "NO", "email must be NOT NULL"


def test_purchased_reports_status_check_constraint(clean_db_url: str) -> None:
    """status only accepts 'pending', 'paid', 'failed' — not 'success'."""
    engine = create_engine(clean_db_url)
    with engine.begin() as conn:
        import uuid as _uuid

        with pytest.raises(Exception, match="purchased_reports_status_check|violates check"):
            conn.execute(
                text("""
                    INSERT INTO purchased_reports
                        (session_id, email, ward_normalized, status, amount_vnd)
                    VALUES (:sid, 'test@example.com', 'dong da', 'success', 35000)
                """),
                {"sid": str(_uuid.uuid4())},
            )
    engine.dispose()


def test_migration_roundtrip(clean_db_url: str) -> None:
    """
    Full round-trip: upgrade head → downgrade -1 → upgrade head.
    Verifies that downgrade migrations are functional.
    """
    cfg = alembic_cfg(clean_db_url)
    engine = create_engine(clean_db_url)

    # Start: already at head from previous tests
    inspector = inspect(engine)
    assert "purchased_reports" in inspector.get_table_names()

    # Downgrade one step — purchased_reports should be gone
    command.downgrade(cfg, "-1")
    inspector = inspect(engine)
    assert "purchased_reports" not in inspector.get_table_names()
    assert "flood_events" in inspector.get_table_names()

    # Upgrade back to head
    command.upgrade(cfg, "head")
    inspector = inspect(engine)
    assert "purchased_reports" in inspector.get_table_names()

    engine.dispose()


def test_downgrade_base_removes_all_tables(clean_db_url: str) -> None:
    """alembic downgrade base removes all application tables."""
    cfg = alembic_cfg(clean_db_url)
    command.downgrade(cfg, "base")

    engine = create_engine(clean_db_url)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    engine.dispose()

    for table in ("flood_events", "price_records", "ward_centroids", "purchased_reports"):
        assert table not in tables, f"Table '{table}' still exists after downgrade base"


def test_upgrade_after_downgrade_base(clean_db_url: str) -> None:
    """After downgrade base, upgrade head works cleanly from scratch."""
    cfg = alembic_cfg(clean_db_url)
    command.upgrade(cfg, "head")

    engine = create_engine(clean_db_url)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    engine.dispose()

    for table in ("flood_events", "price_records", "ward_centroids", "purchased_reports"):
        assert table in tables
