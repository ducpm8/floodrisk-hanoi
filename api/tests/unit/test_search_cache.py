"""Unit tests for Redis caching layer in /api/search."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import api.cache as redis_module


# ---------------------------------------------------------------------------
# cache_get / cache_set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_get_returns_value_when_client_set() -> None:
    mock_client = AsyncMock()
    mock_client.get.return_value = '{"results":[],"total":0}'
    redis_module._client = mock_client
    result = await redis_module.cache_get("search:tay son")
    assert result == '{"results":[],"total":0}'
    mock_client.get.assert_called_once_with("search:tay son")


@pytest.mark.asyncio
async def test_cache_get_returns_none_when_no_client() -> None:
    redis_module._client = None
    result = await redis_module.cache_get("search:tay son")
    assert result is None


@pytest.mark.asyncio
async def test_cache_get_returns_none_on_redis_error() -> None:
    mock_client = AsyncMock()
    mock_client.get.side_effect = Exception("connection refused")
    redis_module._client = mock_client
    result = await redis_module.cache_get("search:bad")
    assert result is None


@pytest.mark.asyncio
async def test_cache_set_calls_setex() -> None:
    mock_client = AsyncMock()
    redis_module._client = mock_client
    await redis_module.cache_set("search:tay son", '{"results":[],"total":0}', ttl=3600)
    mock_client.setex.assert_called_once_with(
        "search:tay son", 3600, '{"results":[],"total":0}'
    )


@pytest.mark.asyncio
async def test_cache_set_noop_when_no_client() -> None:
    redis_module._client = None
    await redis_module.cache_set("search:x", "value")  # must not raise


@pytest.mark.asyncio
async def test_cache_set_silent_on_redis_error() -> None:
    mock_client = AsyncMock()
    mock_client.setex.side_effect = Exception("timeout")
    redis_module._client = mock_client
    await redis_module.cache_set("search:x", "value")  # must not raise


# ---------------------------------------------------------------------------
# init_redis — success and failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_redis_sets_client_on_success() -> None:
    redis_module._client = None
    mock_redis = AsyncMock()
    mock_redis.ping.return_value = True
    with patch("api.cache.aioredis.from_url", return_value=mock_redis):
        await redis_module.init_redis()
    assert redis_module._client is mock_redis


@pytest.mark.asyncio
async def test_init_redis_sets_none_on_failure() -> None:
    redis_module._client = None
    with patch("api.cache.aioredis.from_url", side_effect=Exception("refused")):
        await redis_module.init_redis()
    assert redis_module._client is None


# ---------------------------------------------------------------------------
# close_redis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_redis_calls_aclose() -> None:
    mock_client = AsyncMock()
    redis_module._client = mock_client
    await redis_module.close_redis()
    mock_client.aclose.assert_called_once()
    assert redis_module._client is None


@pytest.mark.asyncio
async def test_close_redis_noop_when_no_client() -> None:
    redis_module._client = None
    await redis_module.close_redis()  # must not raise


# ---------------------------------------------------------------------------
# Search router — cache hit path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_cached_response_without_db() -> None:
    from httpx import ASGITransport, AsyncClient

    from api.main import app

    cached_payload = json.dumps({
        "results": [
            {
                "ward": "Trung Liệt",
                "ward_normalized": "trung liet",
                "district": "Đống Đa",
                "lat": 21.02,
                "lng": 105.84,
                "has_flood_data": True,
                "data_year_range": "2023–2024",
            }
        ],
        "total": 1,
        "message": None,
    })

    with patch("api.routers.search.cache_get", new=AsyncMock(return_value=cached_payload)):
        with patch("api.routers.search.cache_set", new=AsyncMock()):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/search", params={"q": "trung liet"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["ward"] == "Trung Liệt"
