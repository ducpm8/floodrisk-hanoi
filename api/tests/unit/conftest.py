"""
Unit test fixtures.

Patches the app lifespan's real-DB call (_cleanup_stuck_runs) so that
TestClient(app) can be started/stopped per-test without hitting a live
asyncpg connection. Without this patch, the ProactorEventLoop on Windows
corrupts asyncpg's protocol state after the first TestClient exits, causing
all subsequent unit tests to fail with InterfaceError or NoneType.send errors.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def patch_lifespan_db():
    """No-op the startup DB cleanup so unit tests don't need a real Postgres."""
    with patch("api.main._cleanup_stuck_runs", new_callable=AsyncMock):
        yield
