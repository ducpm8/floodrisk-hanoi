"""
FIR-45: Facebook manual crawl — visible browser + SSE streaming

POST /admin/facebook-crawl
  Launches Playwright in non-headless mode, scrolls a Facebook page or group,
  stages up to max_posts new posts per run (dedup via external_id).
  Response is text/event-stream so logs print to terminal in real time.

How to run:
    curl -N -X POST http://localhost:8000/api/admin/facebook-crawl \\
      -H "X-Admin-Token: $ADMIN_TOKEN" \\
      -H "Content-Type: application/json" \\
      -d '{"facebook_url": "https://www.facebook.com/groups/offb.hanoi", "max_posts": 10}'

    -N disables curl buffering so SSE lines appear immediately.
    Browser window opens while logs stream in the terminal.
    Re-run the same command to crawl the next batch (duplicates auto-skipped).

    After staging, Haiku extraction runs inline and emits:
      extraction_start  — {posts: N}
      extraction_done   — {flood_events_added: N, errors: [...]}
      extraction_error  — {message: ...} on unexpected failure

    Requires ANTHROPIC_API_KEY in environment.
    Set FLOOD_SCRAPER_MOCK_LLM=1 to skip real Haiku calls (dev/CI).
"""

import asyncio
import json
import logging
import queue
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from api.config import settings
from api.routers.admin_crawl import require_admin
from api.scrapers.facebook import FacebookCrawler
from api.scrapers.facebook_profile_search import FacebookProfileSearchCrawler

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class FacebookCrawlBody(BaseModel):
    facebook_url: str
    max_posts: int = Field(default=10, ge=1, le=100)
    max_comments_per_post: int = Field(
        default=10, ge=0, le=100,
        description="Max 'Xem thêm bình luận' clicks per post. 0 disables comment extraction.",
    )
    cdp_url: Optional[str] = Field(
        default=None,
        description="CDP endpoint of a running Chrome instance (e.g. http://localhost:9222). "
                    "When set, Playwright connects to your existing logged-in browser instead of "
                    "launching a new one. Launch Chrome with --remote-debugging-port=9222.",
    )

    @field_validator("facebook_url")
    @classmethod
    def validate_facebook_url(cls, v: str) -> str:
        if "facebook.com" not in v and "fb.com" not in v:
            raise ValueError("URL must be a Facebook link (facebook.com or fb.com)")
        return v


# ---------------------------------------------------------------------------
# Sync runner (called in thread via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _run_crawl_sync(
    facebook_url: str,
    max_posts: int,
    log_queue: "queue.Queue[dict | None]",
    cdp_url: Optional[str] = None,
    max_comments_per_post: int = 10,
) -> None:
    """
    Blocking Playwright crawl. Runs in a thread — NOT in the event loop.

    queue.Queue (thread-safe) bridges log events to the async SSE generator.
    The None sentinel is always sent in the finally block so the generator
    terminates even if an exception escapes the crawler.

    cdp_url: if set, connect to existing Chrome via CDP (reuses logged-in session).
    Otherwise fall back to launching a new browser + loading cookies from DB.
    """
    try:
        engine = sa.create_engine(settings.database_sync_url)
        with engine.connect() as conn:
            # Profile search URLs (/search/ or ?q=) use the dedicated crawler.
            # Group/page feeds use the original FIR-45 crawler.
            is_profile_search = "/search/" in facebook_url or "?q=" in facebook_url
            if is_profile_search:
                crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False, cdp_endpoint=cdp_url)
                crawler.crawl_profile_search_streaming(
                    url=facebook_url,
                    max_posts=max_posts,
                    log_queue=log_queue,
                )
            else:
                crawler = FacebookCrawler(db_conn=conn, headless=False, cdp_endpoint=cdp_url)
                crawler.crawl_with_streaming(
                    url=facebook_url,
                    max_posts=max_posts,
                    log_queue=log_queue,
                    max_comments_per_post=max_comments_per_post,
                )
    except Exception as exc:
        log_queue.put({"type": "error", "message": str(exc)})
    finally:
        log_queue.put(None)  # sentinel — always sent


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/admin/facebook-crawl", status_code=200)
async def facebook_crawl(
    body: FacebookCrawlBody,
    _: None = Depends(require_admin),
) -> StreamingResponse:
    """
    Stream Facebook crawl progress as Server-Sent Events.

    Event types: start | navigating | cdp_connecting | browser_ready | scroll |
                 post_staged | post_skipped | comment_staged | comment_skipped |
                 warn | error | done

    CDP mode: pass cdp_url (e.g. "http://localhost:9222") to reuse an existing
    Chrome session. Launch Chrome first:
      chrome.exe --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\\ChromeDebug"
    Log into Facebook once in that window, then all subsequent calls reuse the session.
    """
    log_queue: "queue.Queue[dict | None]" = queue.Queue()

    async def event_stream():
        task = asyncio.create_task(
            asyncio.to_thread(
                _run_crawl_sync,
                body.facebook_url,
                body.max_posts,
                log_queue,
                body.cdp_url,
                body.max_comments_per_post,
            )
        )
        while True:
            try:
                # log_queue.get(block=True, timeout=0.5) blocks the thread for up
                # to 0.5s then raises queue.Empty — keeps the event loop free.
                item = await asyncio.to_thread(log_queue.get, True, 0.5)
            except queue.Empty:
                yield "data: {\"type\":\"heartbeat\"}\n\n"
                continue
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")
