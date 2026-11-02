"""
Integration tests: scraper run → scrape_log row written with correct data.

Requires a real PostgreSQL + PostGIS instance with all migrations applied.

Quick start:
    docker compose -f infra/docker-compose.yml up -d db_test
    cd api/
    DATABASE_TEST_URL=postgresql+psycopg2://flood:flood@localhost:5433/flooddb_test \\
        pytest tests/integration/test_scrape_log.py -v
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from api.scrapers.run_scrapers import _log_run


@pytest.fixture(autouse=True)
def clean_scrape_log(migrated_db: str):
    """Truncate scrape_log before and after each test for isolation."""
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM scrape_log"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM scrape_log"))
    engine.dispose()


def _fetch_latest_log(migrated_db: str) -> dict:
    engine = create_engine(migrated_db)
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT source, records_added, errors, status,
                       started_at, finished_at
                FROM scrape_log
                ORDER BY id DESC
                LIMIT 1
            """)
        ).fetchone()
    engine.dispose()
    assert row is not None, "No scrape_log row found"
    return dict(row._mapping)


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------

def test_log_run_success_status(migrated_db: str) -> None:
    """No errors → status='success'."""
    started_at = datetime(2026, 4, 6, 10, 0, 0, tzinfo=timezone.utc)
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        _log_run(conn, "official", records_added=5, errors=[], started_at=started_at)
    engine.dispose()

    row = _fetch_latest_log(migrated_db)
    assert row["source"] == "official"
    assert row["records_added"] == 5
    assert row["status"] == "success"
    assert row["errors"] is None


def test_log_run_partial_status(migrated_db: str) -> None:
    """Some records + errors → status='partial'."""
    started_at = datetime(2026, 4, 6, 11, 0, 0, tzinfo=timezone.utc)
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        _log_run(
            conn, "news_vnexpress",
            records_added=3,
            errors=["Row parse error: something failed"],
            started_at=started_at,
        )
    engine.dispose()

    row = _fetch_latest_log(migrated_db)
    assert row["status"] == "partial"
    assert row["records_added"] == 3
    assert "Row parse error" in row["errors"]


def test_log_run_failed_status(migrated_db: str) -> None:
    """Errors but 0 records → status='failed'."""
    started_at = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        _log_run(
            conn, "news_tuoitre",
            records_added=0,
            errors=["SELECTOR_FAILURE: page 1 returned 0 article URLs"],
            started_at=started_at,
        )
    engine.dispose()

    row = _fetch_latest_log(migrated_db)
    assert row["status"] == "failed"
    assert row["records_added"] == 0
    assert "SELECTOR_FAILURE" in row["errors"]


# ---------------------------------------------------------------------------
# Fields and serialization
# ---------------------------------------------------------------------------

def test_log_run_stores_started_at(migrated_db: str) -> None:
    """started_at is stored and matches what was passed."""
    started_at = datetime(2026, 4, 7, 9, 30, 0, tzinfo=timezone.utc)
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        _log_run(conn, "official", 1, [], started_at)
    engine.dispose()

    row = _fetch_latest_log(migrated_db)
    # Postgres stores TIMESTAMPTZ; compare as UTC-aware datetimes
    stored = row["started_at"]
    if stored.tzinfo is None:
        import pytz
        stored = pytz.utc.localize(stored)
    assert stored.replace(microsecond=0) == started_at.replace(microsecond=0)


def test_log_run_finished_at_is_set(migrated_db: str) -> None:
    """finished_at is set by the DB (NOW()) and is not null."""
    started_at = datetime(2026, 4, 7, 10, 0, 0, tzinfo=timezone.utc)
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        _log_run(conn, "official", 0, [], started_at)
    engine.dispose()

    row = _fetch_latest_log(migrated_db)
    assert row["finished_at"] is not None


def test_log_run_errors_stored_as_json_string(migrated_db: str) -> None:
    """Errors list is stored as a JSON string in the TEXT column."""
    import json

    errors = ["error one", "error two: details"]
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        _log_run(conn, "news_vnexpress", 0, errors, datetime.now(tz=timezone.utc))
    engine.dispose()

    row = _fetch_latest_log(migrated_db)
    stored_errors = json.loads(row["errors"])
    assert stored_errors == errors


def test_log_run_multiple_sources_each_get_own_row(migrated_db: str) -> None:
    """Each source run creates its own row in scrape_log."""
    started = datetime(2026, 4, 8, 8, 0, 0, tzinfo=timezone.utc)
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        _log_run(conn, "official", 10, [], started)
        _log_run(conn, "news_vnexpress", 20, [], started)
        _log_run(conn, "news_tuoitre", 15, [], started)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM scrape_log")).scalar()
        sources = conn.execute(text("SELECT source FROM scrape_log ORDER BY id")).scalars().all()
    engine.dispose()

    assert count == 3
    assert set(sources) == {"official", "news_vnexpress", "news_tuoitre"}


# ---------------------------------------------------------------------------
# Status CHECK constraint
# ---------------------------------------------------------------------------

def test_scrape_log_rejects_invalid_status(migrated_db: str) -> None:
    """status column has CHECK constraint — only running/success/partial/failed allowed."""
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        with pytest.raises(Exception, match="scrape_log_status_check|violates check"):
            conn.execute(
                text("""
                    INSERT INTO scrape_log (source, started_at, records_added, status)
                    VALUES ('test', NOW(), 0, 'unknown_status')
                """)
            )
    engine.dispose()
