"""
Integration tests for GET /api/search against a real PostGIS database.

Requires migrations applied + ward_centroids seeded. Seeds a small set of
flood_events for the test ward and tears them down after.

Quick start:
    docker compose -f infra/docker-compose.yml up -d db_test
    cd api/
    DATABASE_TEST_URL=postgresql+psycopg2://flood:flood@localhost:5433/flooddb_test \\
        pytest tests/integration/test_search_api.py -v
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from api.main import app

# Ward used across all tests in this module
TEST_WARD = "trung hoa"
TEST_WARD_VI = "Trung Hòa"
TEST_DISTRICT = "Cầu Giấy"
TEST_LAT = 21.0120
TEST_LNG = 105.7980


@pytest.fixture(scope="module")
def search_db(migrated_db: str) -> str:
    """
    Seed: 1 ward centroid + 3 flood events (2020, 2022, 2023).
    Teardown: remove seeded rows.
    """
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
            {"ward": TEST_WARD, "ward_vi": TEST_WARD_VI, "district": TEST_DISTRICT,
             "lat": TEST_LAT, "lng": TEST_LNG},
        )
        for yr, src in [(2020, "official"), (2022, "news"), (2023, "official")]:
            conn.execute(
                text("""
                    INSERT INTO flood_events (ward, district, event_date, severity, source,
                        lat, lng, geom)
                    SELECT
                        :ward, :district, :event_date, 'minor', :source,
                        :lat, :lng,
                        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                    ON CONFLICT DO NOTHING
                """),
                {"ward": TEST_WARD, "district": TEST_DISTRICT,
                 "event_date": date(yr, 8, 15),
                 "source": src, "lat": TEST_LAT, "lng": TEST_LNG},
            )

    yield migrated_db

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM flood_events WHERE ward = :ward"), {"ward": TEST_WARD}
        )
        conn.execute(
            text("DELETE FROM ward_centroids WHERE ward_name = :ward"), {"ward": TEST_WARD}
        )
    engine.dispose()


def test_search_ward_with_flood_data(search_db):
    with TestClient(app) as client:
        r = client.get("/api/search?q=trung+hoa")

    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) >= 1
    item = next(x for x in results if x["ward_normalized"] == TEST_WARD)
    assert item["has_flood_data"] is True
    assert item["data_year_range"] == "2020\u20132023"


def test_search_ward_no_flood_data_returns_false(search_db):
    """Insert a ward with NO flood events — has_flood_data must be false."""
    engine = create_engine(search_db)
    empty_ward = "yen_hoa_test"
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ward_centroids (ward_name, ward_name_vi, district, lat, lng, geom)
                VALUES (:ward, 'Yên Hòa Test', 'Cầu Giấy', 21.01, 105.80,
                    ST_SetSRID(ST_MakePoint(105.80, 21.01), 4326)::geography)
                ON CONFLICT DO NOTHING
            """),
            {"ward": empty_ward},
        )

    try:
        with TestClient(app) as client:
            r = client.get(f"/api/search?q={empty_ward}")
        assert r.status_code == 200
        results = r.json()["results"]
        matches = [x for x in results if x["ward_normalized"] == empty_ward]
        assert len(matches) == 1
        assert matches[0]["has_flood_data"] is False
        assert matches[0]["data_year_range"] is None
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM ward_centroids WHERE ward_name = :ward"),
                {"ward": empty_ward},
            )
        engine.dispose()


def test_search_response_no_banned_fields(search_db):
    with TestClient(app) as client:
        r = client.get("/api/search?q=trung+hoa")

    item = r.json()["results"][0]
    for banned in ("flood_zone_count", "teaser_events", "source", "source_url"):
        assert banned not in item


def test_search_limited_to_10_results(search_db):
    """Results must never exceed 10 even if more wards match."""
    with TestClient(app) as client:
        r = client.get("/api/search?q=phuong")  # many wards contain "phuong"

    assert r.status_code == 200
    assert len(r.json()["results"]) <= 10


def test_search_nonexistent_ward_returns_empty(search_db):
    with TestClient(app) as client:
        r = client.get("/api/search?q=khong+ton+tai+xyz")

    assert r.status_code == 200
    assert r.json()["results"] == []
    assert r.json()["total"] == 0


def test_search_vietnamese_input_normalized(search_db):
    """Typing 'Trung Hòa' (with diacritics) should match the same ward."""
    with TestClient(app) as client:
        r = client.get("/api/search?q=Trung+H%C3%B2a")

    assert r.status_code == 200
    results = r.json()["results"]
    assert any(x["ward_normalized"] == TEST_WARD for x in results)
