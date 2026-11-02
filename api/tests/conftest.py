"""
Test configuration and fixtures.

Integration tests require a real PostgreSQL + PostGIS instance.
Point DATABASE_TEST_URL at a test-only database (separate from dev).

Quick start:
    docker compose -f infra/docker-compose.yml up -d db_test
    cd api/
    DATABASE_TEST_URL=postgresql+psycopg2://flood:flood@localhost:5433/flooddb_test \
        pytest tests/integration/
"""

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def get_test_url() -> str:
    return os.environ.get(
        "DATABASE_TEST_URL",
        "postgresql+psycopg2://flood:flood@localhost:5433/flooddb_test",
    )


def alembic_cfg(db_url: str) -> Config:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option(
        "script_location",
        os.path.join(os.path.dirname(__file__), "..", "migrations"),
    )
    return cfg


@pytest.fixture(scope="session")
def db_url() -> str:
    return get_test_url()


@pytest.fixture(scope="session")
def migrated_db(db_url: str):
    """
    Run alembic upgrade head once per test session.
    Tears down (downgrade base) after all tests complete.
    """
    cfg = alembic_cfg(db_url)
    command.upgrade(cfg, "head")
    yield db_url
    command.downgrade(cfg, "base")


@pytest.fixture
def db_conn(migrated_db: str):
    """Sync connection for integration test assertions."""
    engine = create_engine(migrated_db)
    with engine.connect() as conn:
        yield conn
    engine.dispose()
