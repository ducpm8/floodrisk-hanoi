"""
Redis client singleton for search result caching (FIR-19).

Graceful degradation: if REDIS_URL is unset or Redis is unreachable,
all cache operations return None / no-op — never raise to callers.
"""

import logging
import os
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_client: Optional[aioredis.Redis] = None

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_TTL = 3600  # 1 hour


async def init_redis() -> None:
    global _client
    try:
        _client = aioredis.from_url(REDIS_URL, decode_responses=True)
        await _client.ping()
        logger.info("Redis connected: %s", REDIS_URL)
    except Exception as exc:
        logger.warning("Redis unavailable — caching disabled: %s", exc)
        _client = None


async def close_redis() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None


async def cache_get(key: str) -> Optional[str]:
    if not _client:
        return None
    try:
        return await _client.get(key)
    except Exception as exc:
        logger.debug("Redis get error for %s: %s", key, exc)
        return None


async def cache_set(key: str, value: str, ttl: int = CACHE_TTL) -> None:
    if not _client:
        return
    try:
        await _client.setex(key, ttl, value)
    except Exception as exc:
        logger.debug("Redis set error for %s: %s", key, exc)


def get_redis_client() -> Optional[aioredis.Redis]:
    return _client
