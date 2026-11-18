import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import update

from api.cache import close_redis, init_redis
from api.database import AsyncSessionLocal
from api.models.crawl_run import CrawlRun
from api.routers import admin_crawl, admin_facebook, admin_import, admin_ingest, admin_report, admin_settings, flood, flood_zones, payment, price, search, webhooks

logger = logging.getLogger(__name__)


async def _cleanup_stuck_runs() -> None:
    """On startup: reset any 'running' rows older than 2 hours to 'failed'.
    Prevents permanent 409 lockout after a server crash mid-crawl."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(CrawlRun)
            .where(CrawlRun.status == "running", CrawlRun.started_at < cutoff)
            .values(status="failed", error_message="interrupted: server restarted")
        )
        await db.commit()
    logger.info("Startup cleanup: stuck crawl runs reset")


def _enforce_admin_token_in_staging_prod() -> None:
    """FIR-74: refuse to boot in staging/prod without ADMIN_TOKEN.
    In dev (ENVIRONMENT unset or 'dev'/'local'/'test') admin endpoints
    short-circuit auth — fine locally, fatal in shared envs."""
    env = os.getenv("ENVIRONMENT", "").lower()
    if env in {"staging", "prod", "production"} and not os.getenv("ADMIN_TOKEN"):
        raise RuntimeError(
            f"ADMIN_TOKEN must be set when ENVIRONMENT={env!r}. "
            "Admin endpoints would otherwise accept any request. Refusing to start."
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _enforce_admin_token_in_staging_prod()
    await init_redis()
    await _cleanup_stuck_runs()
    yield
    await close_redis()


app = FastAPI(title="Hanoi Flood Risk API", version="1.0.0", lifespan=lifespan)

_allowed_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


def _cors_headers_for_origin(origin: str | None) -> dict[str, str]:
    """CORS headers for responses produced outside CORSMiddleware.

    Starlette's ServerErrorMiddleware sits *outside* all user middleware, so its
    responses (including the 500 from the handler below) never pass through
    CORSMiddleware. Without these headers a crash surfaces in the browser as a
    CORS error instead of the real 500, hiding the actual failure.
    """
    if not origin:
        return {}
    if "*" in _allowed_origins:
        return {"Access-Control-Allow-Origin": "*"}
    if origin in _allowed_origins:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Always answer with JSON 500 (no stack trace) and CORS headers when applicable."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=_cors_headers_for_origin(request.headers.get("origin")),
    )


app.include_router(search.router, prefix="/api")
app.include_router(flood_zones.router, prefix="/api")
app.include_router(flood.router, prefix="/api")
app.include_router(payment.router, prefix="/api")
app.include_router(price.router, prefix="/api")
# Crawl/ingest code is retained but unmounted: flood data is seeded manually.
if os.getenv("ENABLE_CRAWL_ROUTERS", "false").lower() == "true":
    app.include_router(admin_crawl.router, prefix="/api")
    app.include_router(admin_facebook.router, prefix="/api")
    app.include_router(admin_ingest.router, prefix="/api")
app.include_router(admin_import.router, prefix="/api")
app.include_router(admin_report.router, prefix="/api")
app.include_router(admin_settings.router, prefix="/api")
app.include_router(webhooks.router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
