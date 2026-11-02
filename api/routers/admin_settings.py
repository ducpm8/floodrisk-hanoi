"""
App-level settings store — FIR-38

Endpoints:
  GET   /api/admin/settings   public — homepage reads map_mode on load
  PATCH /api/admin/settings   admin-only — toggle map_mode

Settings are persisted in Redis (no TTL — permanent until changed).
Graceful degradation: if Redis is down, GET returns defaults, PATCH returns 503.

map_mode values: "text" | "google"
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from api.cache import cache_get, get_redis_client
from api.routers.admin_crawl import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()

_MAP_MODE_KEY = "admin:map_mode"
_DEFAULT_MAP_MODE = "google"
_VALID_MAP_MODES = {"text", "google"}


class SettingsResponse(BaseModel):
    map_mode: str


class SettingsPatch(BaseModel):
    map_mode: str


@router.get("/admin/settings", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    value = await cache_get(_MAP_MODE_KEY)
    return SettingsResponse(map_mode=value if value in _VALID_MAP_MODES else _DEFAULT_MAP_MODE)


@router.patch("/admin/settings", response_model=SettingsResponse)
async def patch_settings(
    body: SettingsPatch,
    _: None = Depends(require_admin),
) -> SettingsResponse:
    if body.map_mode not in _VALID_MAP_MODES:
        raise HTTPException(status_code=422, detail=f"map_mode must be one of {sorted(_VALID_MAP_MODES)}")

    if get_redis_client() is None:
        raise HTTPException(status_code=503, detail="Settings store unavailable (Redis offline)")

    await get_redis_client().set(_MAP_MODE_KEY, body.map_mode)  # no TTL — permanent
    logger.info("map_mode set to %s", body.map_mode)
    return SettingsResponse(map_mode=body.map_mode)
