"""
Seed production flood_events from live sources.

Usage:
    python scripts/seed_production.py --source thoatnuochanoi
    python scripts/seed_production.py --source thoatnuochanoi --max-pages 20

Outputs:
    scripts/seed_errors.log   — ward_error / db_error lines (always appended)
    scripts/seed_results.json — summary of this run (overwritten each run)

Redis pub/sub:
    Each event published to channel 'crawl:progress' as JSON.
    Schema: {ts, source, title, ward, event_date, status, detail?}
    Admin stream endpoint: GET /admin/crawl/stream (see AC4 / FIR-35)

Upsert key: (source_url, event_date, ward) via ON CONFLICT DO NOTHING.
Requires migration 010 to be applied before running.
"""

import argparse
import io
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import re

import anthropic
import redis as sync_redis
import requests
import sqlalchemy as sa
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text

from api.config import settings
from api.scrapers.ai_extraction_worker import _parse_event, call_haiku_news
from api.scrapers.thoatnuoc_com import ThoatNuocComScraper
from api.utils.text import normalize_ward
from api.utils.ward_lookup import resolve_location

# ── Logging ───────────────────────────────────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).resolve().parent
ERROR_LOG = _SCRIPTS_DIR / "seed_errors.log"
RESULTS_FILE = _SCRIPTS_DIR / "seed_results.json"

_stdout_utf8 = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[
        logging.StreamHandler(_stdout_utf8),
        logging.FileHandler(str(ERROR_LOG), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── Redis ─────────────────────────────────────────────────────────────────────

CRAWL_CHANNEL = "crawl:progress"


def _connect_redis() -> "sync_redis.Redis | None":
    try:
        r = sync_redis.from_url(settings.redis_url, decode_responses=True)
        r.ping()
        logger.info("Redis connected: %s", settings.redis_url)
        return r
    except Exception as exc:
        logger.warning("Redis unavailable — live stream disabled: %s", exc)
        return None


def _publish(r: "sync_redis.Redis | None", payload: dict) -> None:
    if not r:
        return
    try:
        r.publish(CRAWL_CHANNEL, json.dumps(payload, ensure_ascii=False))
    except Exception as exc:
        logger.debug("Redis publish failed: %s", exc)


def _build_event(
    *,
    source: str,
    title: str,
    ward: "str | None",
    event_date: str,
    status: str,
    detail: "str | None" = None,
) -> dict:
    return {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        "source": source,
        "title": title,
        "ward": ward,
        "event_date": event_date,
        "status": status,
        **({"detail": detail} if detail else {}),
    }


# ── Database ──────────────────────────────────────────────────────────────────

_UPSERT_SQL = text("""
    INSERT INTO flood_events
        (id, location_name, ward, district, lat, lng, geom,
         event_date, severity, depth_cm, source, source_url, crawl_run_id, cause)
    VALUES (
        gen_random_uuid(),
        :location_name, :ward, :district,
        :lat, :lng,
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        :event_date,
        'minor',
        :depth_cm,
        'official',
        :source_url,
        CAST(:crawl_run_id AS uuid),
        NULL
    )
    ON CONFLICT ON CONSTRAINT uidx_flood_events_upsert_key
    DO NOTHING
    RETURNING id
""")


def _upsert_event(conn: sa.Connection, params: dict) -> bool:
    """Insert event; return True if new row, False if duplicate."""
    row = conn.execute(_UPSERT_SQL, params).fetchone()
    return row is not None


_OPEN_RUN_SQL = text("""
    INSERT INTO crawl_runs (source_name, source_url, started_at, status)
    VALUES (:source, :source_url, NOW(), 'running')
    RETURNING id
""")

_CLOSE_RUN_SQL = text("""
    UPDATE crawl_runs SET finished_at = NOW(), stats = CAST(:stats AS jsonb), status = 'success'
    WHERE id = CAST(:run_id AS uuid)
""")


def _open_crawl_run(engine: sa.Engine, source: str, source_url: "str | None") -> "str | None":
    try:
        with engine.connect() as conn:
            row = conn.execute(_OPEN_RUN_SQL, {"source": source, "source_url": source_url}).fetchone()
            conn.commit()
            return str(row[0]) if row else None
    except Exception as exc:
        logger.warning("crawl_run open failed: %s", exc)
        return None


def _close_crawl_run(engine: sa.Engine, run_id: "str | None", stats: dict) -> None:
    if not run_id:
        return
    try:
        with engine.connect() as conn:
            conn.execute(_CLOSE_RUN_SQL, {"run_id": run_id, "stats": json.dumps(stats)})
            conn.commit()
    except Exception as exc:
        logger.warning("crawl_run close failed: %s", exc)


# ── Crawl runners ─────────────────────────────────────────────────────────────

def run_thoatnuoc(max_pages: int, redis_client: "sync_redis.Redis | None") -> dict:
    engine = create_engine(settings.database_sync_url, echo=False)
    scraper = ThoatNuocComScraper()
    ai_client = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
    stats: dict[str, int] = {
        "total": 0, "saved": 0, "duplicate": 0, "ward_error": 0, "db_error": 0,
        "no_depth": 0,
    }
    run_id = _open_crawl_run(engine, "thoatnuochanoi", None)

    with engine.connect() as conn:
        for raw in scraper.scrape(max_pages=max_pages):
            stats["total"] += 1
            source_url: str = raw["source_url"]
            title: str = raw.get("title", "")
            event_date = raw["event_date"]
            location_name: str = raw.get("location_name") or title

            if not raw.get("depth_cm"):
                stats["no_depth"] += 1
                logger.debug("no_depth | url=%s", source_url)
                continue

            resolved = resolve_location(conn, location_name, client=ai_client) if location_name else None

            if not resolved:
                stats["ward_error"] += 1
                logger.warning(
                    "ward_error | location=%r | url=%s",
                    location_name, source_url,
                )
                _publish(redis_client, _build_event(
                    source="thoatnuochanoi",
                    title=title,
                    ward=normalize_ward(location_name) if location_name else None,
                    event_date=str(event_date),
                    status="ward_error",
                    detail=f"no centroid for {location_name!r}",
                ))
                continue

            params = {
                "location_name": location_name,
                "ward": resolved.ward_name,
                "district": resolved.district,
                "lat": resolved.lat,
                "lng": resolved.lng,
                "event_date": event_date,
                "depth_cm": raw.get("depth_cm"),
                "source_url": source_url,
                "crawl_run_id": run_id,
            }

            try:
                is_new = _upsert_event(conn, params)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                stats["db_error"] += 1
                logger.error("db_error | url=%s | %s", source_url, exc)
                _publish(redis_client, _build_event(
                    source="thoatnuochanoi",
                    title=title,
                    ward=resolved.ward_name,
                    event_date=str(event_date),
                    status="db_error",
                    detail=str(exc),
                ))
                continue

            status = "saved" if is_new else "duplicate"
            stats[status] += 1
            logger.info(
                "%s | ward=%s [%s] date=%s | %s",
                status, resolved.ward_name, resolved.resolution_level, event_date, source_url,
            )
            _publish(redis_client, _build_event(
                source="thoatnuochanoi",
                title=title,
                ward=resolved.ward_name,
                event_date=str(event_date),
                status=status,
            ))

    _close_crawl_run(engine, run_id, stats)
    return stats


_NEWS_KEYWORDS = ["ngập lụt", "ngập úng", "ngập", "hà nội"]

_NEWS_UPSERT_SQL = text("""
    INSERT INTO flood_events
        (id, location_name, ward, district, lat, lng, geom,
         event_date, severity, depth_cm, source, source_url, crawl_run_id, cause)
    VALUES (
        gen_random_uuid(),
        :location_name, :ward, :district,
        :lat, :lng,
        CASE WHEN :lat IS NOT NULL AND :lng IS NOT NULL
             THEN ST_SetSRID(ST_MakePoint(:lng2, :lat2), 4326)::geography
             ELSE NULL
        END,
        CAST(:event_date AS date),
        :severity,
        :depth_cm,
        'news',
        :source_url,
        CAST(:crawl_run_id AS uuid),
        :cause
    )
    ON CONFLICT ON CONSTRAINT uidx_flood_events_upsert_key
    DO UPDATE SET cause = EXCLUDED.cause
    WHERE flood_events.cause IS NULL
    RETURNING id, (xmax = 0) AS inserted
""")


_COOKIE_CHALLENGE_RE = re.compile(r'document\.cookie="([^"]+)"')
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"


def _fetch_article(url: str) -> "requests.Response":
    """Fetch URL, handling JS cookie challenges (e.g. laodong.vn D1N pattern)."""
    session = requests.Session()
    session.headers["User-Agent"] = _UA
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    # Detect cookie challenge: tiny HTML with document.cookie + window.location.reload
    if len(resp.content) < 1024 and b"document.cookie" in resp.content:
        m = _COOKIE_CHALLENGE_RE.search(resp.text)
        if m:
            kv = m.group(1).split(";")[0]
            name, _, val = kv.partition("=")
            session.cookies.set(name.strip(), val.strip())
            logger.debug("Cookie challenge resolved for %s", url)
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp


def run_news_url(url: str, redis_client: "sync_redis.Redis | None") -> dict:
    """AC2: fetch one news URL, extract flood events via Haiku, upsert."""
    # --- fetch ---
    try:
        resp = _fetch_article(url)
    except Exception as exc:
        logger.error("Fetch failed: %s | %s", url, exc)
        return {"error": str(exc)}

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    # Prefer article body to skip nav boilerplate; fall back to full page
    body = soup.select_one("article, main, .article-content, .post-content, .detail-content, .cms-body")
    article_text = (body or soup).get_text(separator="\n", strip=True)

    # --- keyword filter ---
    text_lower = article_text.lower()
    if not any(kw in text_lower for kw in _NEWS_KEYWORDS):
        logger.info("Skipping %s — no flood keywords found", url)
        return {"skipped": True, "reason": "no flood keywords"}

    # --- article date: parse from URL pattern ngay-DDMMYYYY, else meta tag ---
    article_date: str | None = None
    m = re.search(r"ngay-(\d{2})(\d{2})(\d{4})", url)
    if m:
        article_date = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    else:
        meta = soup.find("meta", property="article:published_time")
        if meta and meta.get("content"):
            article_date = str(meta["content"])[:10]
    logger.info("Article date: %s | url: %s", article_date, url)

    # --- LLM extraction ---
    if not settings.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY not configured")
        return {"error": "no api key"}
    ai_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        result = call_haiku_news(ai_client, article_text[:12000], article_date)
    except Exception as exc:
        logger.error("Haiku extraction failed: %s", exc)
        return {"error": str(exc)}

    raw_events = result.get("events", [])
    source_date = result.get("source_date")
    article_cause: str | None = result.get("cause") or None
    fallback_date = article_date or (source_date[:10] if source_date else None)
    logger.info("Haiku extracted %d raw events from %s (cause=%r)", len(raw_events), url, article_cause)

    # --- DB upsert ---
    engine = create_engine(settings.database_sync_url, echo=False)
    stats: dict[str, int] = {
        "total": len(raw_events),
        "saved": 0,
        "duplicate": 0,
        "ward_error": 0,
        "db_error": 0,
        "low_confidence": 0,
        "no_depth": 0,
    }
    run_id = _open_crawl_run(engine, "news", url)

    with engine.connect() as conn:
        for raw_ev in raw_events:
            event = _parse_event(raw_ev, "news", url, fallback_date, cause=article_cause)
            if event is None:
                stats["ward_error"] += 1
                continue

            if event.depth_cm is None:
                stats["no_depth"] += 1
                logger.debug("no_depth | location=%r", raw_ev.get("location_name"))

            if event.confidence == "low":
                stats["low_confidence"] += 1

            location_name = event.location_name or event.ward or event.district
            if not location_name:
                stats["ward_error"] += 1
                continue

            resolved = resolve_location(conn, location_name, client=ai_client)
            if not resolved:
                stats["ward_error"] += 1
                logger.warning("ward_error | location=%r", location_name)
                _publish(redis_client, _build_event(
                    source="news",
                    title=location_name,
                    ward=None,
                    event_date=str(event.event_date),
                    status="ward_error",
                    detail=f"no centroid for {location_name!r}",
                ))
                continue

            params = {
                "location_name": event.location_name,
                "ward": resolved.ward_name,
                "district": resolved.district,
                "lat": resolved.lat,
                "lng": resolved.lng,
                "lat2": resolved.lat,
                "lng2": resolved.lng,
                "event_date": event.event_date,
                "severity": event.severity or "minor",
                "depth_cm": event.depth_cm,
                "source_url": url,
                "crawl_run_id": run_id,
                "cause": event.cause,
            }

            try:
                row = conn.execute(_NEWS_UPSERT_SQL, params).fetchone()
                conn.commit()
                is_new = row is not None and bool(row.inserted)
            except Exception as exc:
                conn.rollback()
                stats["db_error"] += 1
                logger.error("db_error | location=%r | %s", location_name, exc)
                _publish(redis_client, _build_event(
                    source="news",
                    title=location_name,
                    ward=resolved.ward_name,
                    event_date=str(event.event_date),
                    status="db_error",
                    detail=str(exc),
                ))
                continue

            status = "saved" if is_new else "duplicate"
            stats[status] += 1
            logger.info(
                "%s | ward=%s [%s] date=%s | location=%r",
                status, resolved.ward_name, resolved.resolution_level,
                event.event_date, location_name,
            )
            _publish(redis_client, _build_event(
                source="news",
                title=event.location_name or location_name,
                ward=resolved.ward_name,
                event_date=str(event.event_date),
                status=status,
            ))

    _close_crawl_run(engine, run_id, stats)
    return stats


# ── AC6: pasted-content extraction ───────────────────────────────────────────

def process_content(
    content: str,
    event_date: str | None,
    source_url: str | None,
) -> dict:
    """Extract flood events from pasted/uploaded text and upsert to DB."""
    if not settings.anthropic_api_key:
        return {"error": "no api key"}

    ai_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        result = call_haiku_news(ai_client, content[:12000], event_date)
    except Exception as exc:
        logger.error("Haiku extraction failed: %s", exc)
        return {"error": str(exc)}

    raw_events = result.get("events", [])
    source_date = result.get("source_date")
    article_cause: str | None = result.get("cause") or None
    fallback_date = event_date or (source_date[:10] if source_date else None)
    logger.info("Haiku extracted %d raw events (cause=%r)", len(raw_events), article_cause)

    engine = create_engine(settings.database_sync_url, echo=False)
    stats: dict[str, int] = {
        "total": len(raw_events),
        "saved": 0,
        "duplicate": 0,
        "ward_error": 0,
        "db_error": 0,
        "low_confidence": 0,
        "no_depth": 0,
    }
    run_id = _open_crawl_run(engine, "manual", source_url)

    with engine.connect() as conn:
        for raw_ev in raw_events:
            event = _parse_event(raw_ev, "news", source_url, fallback_date, cause=article_cause)
            if event is None:
                stats["ward_error"] += 1
                continue

            if event.depth_cm is None:
                stats["no_depth"] += 1

            if event.confidence == "low":
                stats["low_confidence"] += 1

            location_name = event.location_name or event.ward or event.district
            if not location_name:
                stats["ward_error"] += 1
                continue

            resolved = resolve_location(conn, location_name, client=ai_client)
            if not resolved:
                stats["ward_error"] += 1
                logger.warning("ward_error | location=%r", location_name)
                continue

            params = {
                "location_name": event.location_name,
                "ward": resolved.ward_name,
                "district": resolved.district,
                "lat": resolved.lat,
                "lng": resolved.lng,
                "lat2": resolved.lat,
                "lng2": resolved.lng,
                "event_date": event.event_date,
                "severity": event.severity or "minor",
                "depth_cm": event.depth_cm,
                "source_url": source_url,
                "crawl_run_id": run_id,
                "cause": event.cause,
            }

            try:
                row = conn.execute(_NEWS_UPSERT_SQL, params).fetchone()
                conn.commit()
                is_new = row is not None and bool(row.inserted)
            except Exception as exc:
                conn.rollback()
                stats["db_error"] += 1
                logger.error("db_error | location=%r | %s", location_name, exc)
                continue

            status = "saved" if is_new else "duplicate"
            stats[status] += 1
            logger.info(
                "%s | ward=%s [%s] date=%s | location=%r",
                status, resolved.ward_name, resolved.resolution_level,
                event.event_date, location_name,
            )

    _close_crawl_run(engine, run_id, stats)
    return stats


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed production flood_events from live sources (FIR-35)."
    )
    parser.add_argument(
        "--source",
        choices=["thoatnuochanoi", "news", "all"],
        default="all",
        help="Which source(s) to crawl (default: all)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=50,
        metavar="N",
        help="Max listing pages per source (default: 50)",
    )
    parser.add_argument(
        "--url",
        metavar="URL",
        help="News article URL to process (requires --source news)",
    )
    args = parser.parse_args()

    redis_client = _connect_redis()
    results: dict[str, dict] = {}

    if args.source in ("thoatnuochanoi", "all"):
        logger.info("=== thoatnuochanoi.com.vn (max %d pages) ===", args.max_pages)
        results["thoatnuochanoi"] = run_thoatnuoc(
            max_pages=args.max_pages,
            redis_client=redis_client,
        )

    if args.source in ("news", "all"):
        if args.url:
            logger.info("=== news: %s ===", args.url)
            results["news"] = run_news_url(args.url, redis_client)
        else:
            logger.info("=== news sources (no --url provided) ===")
            results["news"] = {"skipped": True, "reason": "pass --url to process a news article"}

    RESULTS_FILE.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("Results saved: %s", RESULTS_FILE)

    for source, stats in results.items():
        print(f"\n{'-' * 40}")
        print(f"  {source}")
        for k, v in stats.items():
            print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
