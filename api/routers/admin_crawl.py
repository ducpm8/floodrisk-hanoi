"""
Admin crawl management — FIR-16

Endpoints:
  GET    /admin/crawl-sources                  list all sources + last run
  POST   /admin/crawl-sources                  add source
  PATCH  /admin/crawl-sources/{name}           update is_enabled / notes
  DELETE /admin/crawl-sources/{name}           remove source
  GET    /admin/crawler-classes                available crawler class names
  POST   /admin/crawl-trigger/{name}           trigger crawl (202 or 409)

Auth: X-Admin-Token header (if ADMIN_TOKEN env var is set).
      In dev (ADMIN_TOKEN unset) all requests are allowed.
"""

import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import AsyncSessionLocal, get_db
from api.models.crawl_run import CrawlRun
from api.models.crawl_source_config import CrawlSourceConfig
from api.scrapers.registry import CRAWLER_CLASS_NAMES, run as run_crawler
from api.scrapers.enrichment_worker import run_enrichment

logger = logging.getLogger(__name__)

router = APIRouter()

_ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

_CONSECUTIVE_FAIL_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def require_admin(x_admin_token: str = Header(default="")) -> None:
    if _ADMIN_TOKEN and x_admin_token != _ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SourceOut(BaseModel):
    source_name: str
    source_type: str
    source_url: str
    crawler_class: str
    is_enabled: bool
    last_crawled_at: datetime | None
    last_run_status: str | None
    last_run_records: int | None

    class Config:
        from_attributes = True


class SourceCreate(BaseModel):
    source_name: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    source_type: str = Field(..., pattern=r"^(governance|news|social)$")
    source_url: str = Field(..., min_length=10, max_length=500)
    crawler_class: str
    is_enabled: bool = True
    notes: str | None = None


class SourcePatch(BaseModel):
    is_enabled: bool | None = None
    source_url: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _last_run(db: AsyncSession, source_name: str) -> CrawlRun | None:
    result = await db.execute(
        select(CrawlRun)
        .where(CrawlRun.source_name == source_name)
        .order_by(CrawlRun.started_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _is_running(db: AsyncSession, source_name: str) -> bool:
    result = await db.execute(
        select(CrawlRun.id)
        .where(CrawlRun.source_name == source_name, CrawlRun.status == "running")
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _check_alert(db: AsyncSession, source_name: str) -> None:
    result = await db.execute(
        select(CrawlRun.status)
        .where(CrawlRun.source_name == source_name)
        .order_by(CrawlRun.started_at.desc())
        .limit(_CONSECUTIVE_FAIL_THRESHOLD)
    )
    statuses = [r.status for r in result.fetchall()]
    if len(statuses) == _CONSECUTIVE_FAIL_THRESHOLD and all(s == "failed" for s in statuses):
        _send_alert(source_name)


def _send_alert(source_name: str) -> None:
    # Email always mocked per CLAUDE.md — replace with real SMTP in production
    logger.warning("ALERT: %s failed %d consecutive times", source_name, _CONSECUTIVE_FAIL_THRESHOLD)


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def _execute_crawl(run_id: uuid.UUID, source_name: str, source_url: str, crawler_class: str) -> None:
    async with AsyncSessionLocal() as db:
        try:
            records_fetched, records_new = await run_crawler(crawler_class, source_url)

            await db.execute(
                update(CrawlRun)
                .where(CrawlRun.id == run_id)
                .values(
                    status="success",
                    finished_at=datetime.now(timezone.utc),
                    records_fetched=records_fetched,
                    records_new=records_new,
                )
            )
            await db.execute(
                update(CrawlSourceConfig)
                .where(CrawlSourceConfig.source_name == source_name)
                .values(last_crawled_at=datetime.now(timezone.utc))
            )
            await db.commit()

        except Exception as exc:
            logger.exception("Crawl failed for %s", source_name)
            await db.execute(
                update(CrawlRun)
                .where(CrawlRun.id == run_id)
                .values(
                    status="failed",
                    finished_at=datetime.now(timezone.utc),
                    error_message=str(exc)[:500],
                )
            )
            await db.commit()
            await _check_alert(db, source_name)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/admin/crawler-classes", response_model=list[str])
async def list_crawler_classes(_: None = Depends(require_admin)) -> list[str]:
    return CRAWLER_CLASS_NAMES


@router.get("/admin/crawl-sources", response_model=list[SourceOut])
async def list_sources(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> list[SourceOut]:
    result = await db.execute(
        select(CrawlSourceConfig).order_by(CrawlSourceConfig.source_type, CrawlSourceConfig.source_name)
    )
    sources = result.scalars().all()

    out = []
    for src in sources:
        run = await _last_run(db, src.source_name)
        out.append(
            SourceOut(
                source_name=src.source_name,
                source_type=src.source_type,
                source_url=src.source_url,
                crawler_class=src.crawler_class,
                is_enabled=src.is_enabled,
                last_crawled_at=src.last_crawled_at,
                last_run_status=run.status if run else None,
                last_run_records=run.records_fetched if run else None,
            )
        )
    return out


@router.post("/admin/crawl-sources", status_code=201, response_model=SourceOut)
async def create_source(
    body: SourceCreate,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> SourceOut:
    if body.crawler_class not in CRAWLER_CLASS_NAMES:
        raise HTTPException(status_code=422, detail=f"Unknown crawler_class: {body.crawler_class}")

    existing = await db.execute(
        select(CrawlSourceConfig).where(CrawlSourceConfig.source_name == body.source_name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="source_name already exists")

    src = CrawlSourceConfig(
        source_name=body.source_name,
        source_type=body.source_type,
        source_url=body.source_url,
        crawler_class=body.crawler_class,
        is_enabled=body.is_enabled,
        notes=body.notes,
    )
    db.add(src)
    await db.commit()
    await db.refresh(src)

    return SourceOut(
        source_name=src.source_name,
        source_type=src.source_type,
        source_url=src.source_url,
        crawler_class=src.crawler_class,
        is_enabled=src.is_enabled,
        last_crawled_at=None,
        last_run_status=None,
        last_run_records=None,
    )


@router.patch("/admin/crawl-sources/{source_name}", response_model=SourceOut)
async def patch_source(
    source_name: str,
    body: SourcePatch,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> SourceOut:
    result = await db.execute(
        select(CrawlSourceConfig).where(CrawlSourceConfig.source_name == source_name)
    )
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")

    if body.is_enabled is not None:
        src.is_enabled = body.is_enabled
    if body.source_url is not None:
        src.source_url = body.source_url
    if body.notes is not None:
        src.notes = body.notes

    await db.commit()
    await db.refresh(src)

    run = await _last_run(db, src.source_name)
    return SourceOut(
        source_name=src.source_name,
        source_type=src.source_type,
        source_url=src.source_url,
        crawler_class=src.crawler_class,
        is_enabled=src.is_enabled,
        last_crawled_at=src.last_crawled_at,
        last_run_status=run.status if run else None,
        last_run_records=run.records_fetched if run else None,
    )


@router.delete("/admin/crawl-sources/{source_name}", status_code=204)
async def delete_source(
    source_name: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> None:
    result = await db.execute(
        select(CrawlSourceConfig).where(CrawlSourceConfig.source_name == source_name)
    )
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")

    if await _is_running(db, source_name):
        raise HTTPException(status_code=409, detail="Crawl is running — wait for it to finish before deleting")

    await db.delete(src)
    await db.commit()


@router.post("/admin/crawl-trigger/{source_name}", status_code=202)
async def trigger_crawl(
    source_name: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> dict:
    result = await db.execute(
        select(CrawlSourceConfig).where(CrawlSourceConfig.source_name == source_name)
    )
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    if not src.is_enabled:
        raise HTTPException(status_code=422, detail="Source is disabled")

    # 409 guard: race condition (two things happen at the same time) protection
    if await _is_running(db, source_name):
        raise HTTPException(status_code=409, detail="Crawl already running for this source")

    run_id = uuid.uuid4()
    run = CrawlRun(id=run_id, source_name=source_name, status="running")
    db.add(run)
    await db.commit()

    background_tasks.add_task(
        _execute_crawl,
        run_id=run_id,
        source_name=source_name,
        source_url=src.source_url,
        crawler_class=src.crawler_class,
    )

    return {"run_id": str(run_id), "status": "running"}


# ---------------------------------------------------------------------------
# FIR-42: Depth enrichment trigger
# ---------------------------------------------------------------------------

class EnrichDepthBody(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)


async def _execute_enrichment(run_id: uuid.UUID, limit: int) -> None:
    """Background task: run enrichment and update CrawlRun on completion."""
    from sqlalchemy import create_engine
    import os

    sync_url = (
        os.environ.get("DATABASE_SYNC_URL")
        or os.environ.get("DATABASE_URL", "").replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        )
    )
    from sqlalchemy import create_engine as _ce
    engine = _ce(sync_url)

    async with AsyncSessionLocal() as db:
        try:
            with engine.begin() as conn:
                stats = run_enrichment(conn, limit=limit)

            await db.execute(
                update(CrawlRun)
                .where(CrawlRun.id == run_id)
                .values(
                    status="success",
                    finished_at=datetime.now(timezone.utc),
                    records_fetched=stats["enriched"] + stats["unchanged"] + stats["failed"],
                    records_new=stats["enriched"],
                )
            )
            await db.commit()

        except Exception as exc:
            logger.exception("Enrichment background task failed")
            await db.execute(
                update(CrawlRun)
                .where(CrawlRun.id == run_id)
                .values(
                    status="failed",
                    finished_at=datetime.now(timezone.utc),
                    error_message=str(exc)[:500],
                )
            )
            await db.commit()


@router.post("/admin/enrich-depth", status_code=202)
async def trigger_enrich_depth(
    body: EnrichDepthBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> dict:
    """Trigger background enrichment of flood_events with depth_cm=NULL.

    Scans up to `limit` NULL-depth events, runs 3-tier search cascade
    (Google → DuckDuckGo → Haiku web_search), updates depth_cm in place.
    Returns immediately with run_id; check GET /admin/crawl-runs for status.
    """
    if await _is_running(db, "enrichment"):
        raise HTTPException(status_code=409, detail="Enrichment already running")

    run_id = uuid.uuid4()
    run = CrawlRun(id=run_id, source_name="enrichment", status="running")
    db.add(run)
    await db.commit()

    background_tasks.add_task(_execute_enrichment, run_id=run_id, limit=body.limit)

    return {"run_id": str(run_id), "status": "queued", "limit": body.limit}
