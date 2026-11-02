"""
Unit tests for FIR-38: GET /api/admin/settings + PATCH /api/admin/settings
Redis and admin token are mocked — no real external state.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture(autouse=True)
def mock_db_startup():
    with patch("api.main._cleanup_stuck_runs", new=AsyncMock()):
        yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /api/admin/settings
# ---------------------------------------------------------------------------

def test_get_settings_returns_default_when_redis_miss(client):
    with patch("api.routers.admin_settings.cache_get", new=AsyncMock(return_value=None)):
        r = client.get("/api/admin/settings")
    assert r.status_code == 200
    assert r.json() == {"map_mode": "text"}


def test_get_settings_returns_stored_text_mode(client):
    with patch("api.routers.admin_settings.cache_get", new=AsyncMock(return_value="text")):
        r = client.get("/api/admin/settings")
    assert r.status_code == 200
    assert r.json()["map_mode"] == "text"


def test_get_settings_returns_stored_google_mode(client):
    with patch("api.routers.admin_settings.cache_get", new=AsyncMock(return_value="google")):
        r = client.get("/api/admin/settings")
    assert r.status_code == 200
    assert r.json()["map_mode"] == "google"


def test_get_settings_falls_back_to_default_on_invalid_stored_value(client):
    with patch("api.routers.admin_settings.cache_get", new=AsyncMock(return_value="leaflet")):
        r = client.get("/api/admin/settings")
    assert r.status_code == 200
    assert r.json()["map_mode"] == "text"


def test_get_settings_no_auth_required(client):
    with patch("api.routers.admin_settings.cache_get", new=AsyncMock(return_value=None)):
        r = client.get("/api/admin/settings")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# PATCH /api/admin/settings
# ---------------------------------------------------------------------------

def _mock_redis():
    m = MagicMock()
    m.set = AsyncMock()
    return m


def test_patch_map_mode_to_google(client):
    with (
        patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"),
        patch("api.routers.admin_settings.get_redis_client", return_value=_mock_redis()),
    ):
        r = client.patch(
            "/api/admin/settings",
            json={"map_mode": "google"},
            headers={"X-Admin-Token": "secret"},
        )
    assert r.status_code == 200
    assert r.json()["map_mode"] == "google"


def test_patch_map_mode_to_text(client):
    with (
        patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"),
        patch("api.routers.admin_settings.get_redis_client", return_value=_mock_redis()),
    ):
        r = client.patch(
            "/api/admin/settings",
            json={"map_mode": "text"},
            headers={"X-Admin-Token": "secret"},
        )
    assert r.status_code == 200
    assert r.json()["map_mode"] == "text"


def test_patch_invalid_map_mode_returns_422(client):
    with (
        patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"),
        patch("api.routers.admin_settings.get_redis_client", return_value=_mock_redis()),
    ):
        r = client.patch(
            "/api/admin/settings",
            json={"map_mode": "leaflet"},
            headers={"X-Admin-Token": "secret"},
        )
    assert r.status_code == 422


def test_patch_no_token_returns_403_when_token_set(client):
    with patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"):
        r = client.patch("/api/admin/settings", json={"map_mode": "google"})
    assert r.status_code == 403


def test_patch_wrong_token_returns_403(client):
    with patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"):
        r = client.patch(
            "/api/admin/settings",
            json={"map_mode": "google"},
            headers={"X-Admin-Token": "wrong"},
        )
    assert r.status_code == 403


def test_patch_returns_503_when_redis_offline(client):
    with (
        patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"),
        patch("api.routers.admin_settings.get_redis_client", return_value=None),
    ):
        r = client.patch(
            "/api/admin/settings",
            json={"map_mode": "google"},
            headers={"X-Admin-Token": "secret"},
        )
    assert r.status_code == 503


def test_patch_no_token_allowed_when_env_unset(client):
    with (
        patch("api.routers.admin_crawl._ADMIN_TOKEN", ""),
        patch("api.routers.admin_settings.get_redis_client", return_value=_mock_redis()),
    ):
        r = client.patch("/api/admin/settings", json={"map_mode": "google"})
    assert r.status_code == 200
