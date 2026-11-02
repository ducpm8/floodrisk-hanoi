"""
Integration tests: scraper output → real DB insert → verify count + dedup.

Requires a real PostgreSQL + PostGIS instance with all migrations applied.

Quick start:
    docker compose -f infra/docker-compose.yml up -d db_test
    cd api/
    DATABASE_TEST_URL=postgresql+psycopg2://flood:flood@localhost:5433/flooddb_test \\
        pytest tests/integration/test_scraper_insert.py -v
"""

import os
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

from api.scrapers.news import _insert_news_event
from api.scrapers.official import scrape_official


FIXTURE_HTML = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "thoatnuoc_page.html"
)

# Normalized ward names that appear in the HTML fixture
FIXTURE_WARDS = [
    ("my dinh ii",  21.0150, 105.7800),
    ("trung van",   21.0050, 105.7700),
    ("ngoc lam",    21.0350, 105.8800),
]


@pytest.fixture(scope="module")
def seeded_db(migrated_db: str) -> str:
    """
    Seed test ward centroids for the 3 wards in the HTML fixture.
    Removed after all tests in this module complete.
    """
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        for ward, lat, lng in FIXTURE_WARDS:
            conn.execute(
                text("""
                    INSERT INTO ward_centroids (ward_name, geom)
                    VALUES (
                        :ward,
                        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                    )
                    ON CONFLICT DO NOTHING
                """),
                {"ward": ward, "lat": lat, "lng": lng},
            )
    yield migrated_db
    ward_names = tuple(w for w, _, _ in FIXTURE_WARDS)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM flood_events WHERE source = 'official'"))
        conn.execute(
            text("DELETE FROM flood_events WHERE source IN ('news', 'news_vnexpress', 'news_tuoitre')"),
        )
        for ward in ward_names:
            conn.execute(
                text("DELETE FROM ward_centroids WHERE ward_name = :ward"),
                {"ward": ward},
            )
    engine.dispose()


# ---------------------------------------------------------------------------
# Official scraper — insert + dedup
# ---------------------------------------------------------------------------

def test_scrape_official_inserts_three_records(seeded_db: str) -> None:
    """scrape_official with fixture HTML inserts exactly 3 rows."""
    engine = create_engine(seeded_db)
    with open(FIXTURE_HTML, encoding="utf-8") as f:
        html = f.read()

    with patch("api.scrapers.official._fetch_page", return_value=html):
        with engine.begin() as conn:
            records, errors = scrape_official(conn)

    engine.dispose()
    assert records == 3, f"Expected 3 inserts, got {records}. Errors: {errors}"
    assert errors == []


def test_scrape_official_dedup_on_second_run(seeded_db: str) -> None:
    """Running scrape_official twice inserts 0 on the second run (ON CONFLICT DO NOTHING)."""
    engine = create_engine(seeded_db)
    with open(FIXTURE_HTML, encoding="utf-8") as f:
        html = f.read()

    with patch("api.scrapers.official._fetch_page", return_value=html):
        with engine.begin() as conn:
            scrape_official(conn)  # first run (already run by previous test, but idempotent)
        with engine.begin() as conn:
            records, errors = scrape_official(conn)

    engine.dispose()
    assert records == 0, f"Expected 0 on second run, got {records}"


def test_scrape_official_severity_stored_correctly(seeded_db: str) -> None:
    """Severity values from fixture: minor, moderate, severe are stored correctly."""
    engine = create_engine(seeded_db)
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ward, severity FROM flood_events
                WHERE source = 'official'
                ORDER BY ward
            """)
        ).fetchall()
    engine.dispose()

    by_ward = {r[0]: r[1] for r in rows}
    assert by_ward.get("my dinh ii") == "minor"
    assert by_ward.get("trung van") == "moderate"
    assert by_ward.get("ngoc lam") == "severe"


def test_scrape_official_geom_populated_from_ward_centroids(seeded_db: str) -> None:
    """geom column is populated via JOIN on ward_centroids (not null)."""
    engine = create_engine(seeded_db)
    with engine.connect() as conn:
        null_geom_count = conn.execute(
            text("""
                SELECT COUNT(*) FROM flood_events
                WHERE source = 'official' AND geom IS NULL
            """)
        ).scalar()
    engine.dispose()
    assert null_geom_count == 0, f"{null_geom_count} official records have null geom"


def test_scrape_official_empty_table_html_returns_no_records(seeded_db: str) -> None:
    """HTML with table shell but no data rows returns 0 records and JS-render error."""
    engine = create_engine(seeded_db)
    empty_table_html = "<html><body><table><tr><th>col</th></tr></table></body></html>"

    with patch("api.scrapers.official._fetch_page", return_value=empty_table_html):
        with engine.begin() as conn:
            records, errors = scrape_official(conn)

    engine.dispose()
    assert records == 0
    assert any("JavaScript-rendered" in e or "0 data rows" in e for e in errors)


def test_scrape_official_no_table_with_flood_keywords_gives_js_render_error(seeded_db: str) -> None:
    """Page with flood keywords but no table → JS-render suspected error."""
    engine = create_engine(seeded_db)
    js_render_html = "<html><body><p>Danh sách điểm ngập úng đang tải...</p></body></html>"

    with patch("api.scrapers.official._fetch_page", return_value=js_render_html):
        with engine.begin() as conn:
            records, errors = scrape_official(conn)

    engine.dispose()
    assert records == 0
    assert any("JavaScript-rendered" in e or "JS-RENDER" in e or "probe_thoatnuoc" in e for e in errors)


# ---------------------------------------------------------------------------
# News event insert + dedup (direct, no HTTP or LLM)
# ---------------------------------------------------------------------------

def test_insert_news_event_inserts_row(seeded_db: str) -> None:
    """_insert_news_event inserts a row and returns True."""
    engine = create_engine(seeded_db)
    extracted = {
        "ward": "Mỹ Đình II",
        "district": "Nam Từ Liêm",
        "event_date": "2023-08-15",
        "severity": "moderate",
        "location_name": "Phố Lê Đức Thọ",
    }
    with engine.begin() as conn:
        inserted = _insert_news_event(conn, extracted, "https://vnexpress.net/test-1", "news_vnexpress")

    engine.dispose()
    assert inserted is True


def test_insert_news_event_dedup_same_ward_date_source(seeded_db: str) -> None:
    """Second insert with same (ward, event_date, source) returns False."""
    engine = create_engine(seeded_db)
    extracted = {
        "ward": "Mỹ Đình II",
        "district": "Nam Từ Liêm",
        "event_date": "2023-08-15",
        "severity": "minor",
        "location_name": "Different street",
    }
    with engine.begin() as conn:
        # First insert (may already exist from previous test — that's fine)
        _insert_news_event(conn, extracted, "https://vnexpress.net/test-2", "news_vnexpress")
    with engine.begin() as conn:
        # Exact same (ward, event_date) with same source → deduped
        result = _insert_news_event(conn, extracted, "https://vnexpress.net/test-3", "news_vnexpress")

    engine.dispose()
    assert result is False


def test_insert_news_event_unknown_ward_skips_insert(seeded_db: str) -> None:
    """Ward not in ward_centroids: INSERT...SELECT returns 0 rows → False."""
    engine = create_engine(seeded_db)
    extracted = {
        "ward": "Phường Không Tồn Tại",
        "district": "Quận Ảo",
        "event_date": str(date.today()),
        "severity": "minor",
        "location_name": None,
    }
    with engine.begin() as conn:
        result = _insert_news_event(conn, extracted, "https://x.com/unknown-ward", "news_vnexpress")

    engine.dispose()
    # Ward not in ward_centroids → SELECT returns nothing → RETURNING id is empty
    assert result is False
