"""
Crawler registry — maps crawler_class names (stored in crawl_source_config)
to async callables with signature: (source_url: str) -> tuple[int, int]
where the tuple is (records_fetched, records_new).

To add a new crawler:
  1. Implement the scraping logic (sync or async)
  2. Add an entry here
  3. Insert a row in crawl_source_config (no deploy needed after that)
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stub runner used for sources not yet fully integrated.
# Replace with real implementation per source.
# ---------------------------------------------------------------------------

async def _stub(source_url: str) -> tuple[int, int]:
    logger.info("stub crawler called for %s — wire real implementation", source_url)
    return 0, 0


# ---------------------------------------------------------------------------
# Real integrations
# official.py is async (Playwright). news.py is sync — wrapped with asyncio.to_thread.
# ---------------------------------------------------------------------------

async def _run_thoatnuoc(source_url: str) -> tuple[int, int]:
    from api.scrapers.official import scrape_official  # type: ignore[import]
    return await scrape_official(source_url)


async def _run_news(source_url: str, source_key: str) -> tuple[int, int]:
    from api.scrapers.news import ALL_SOURCES, scrape_news_source  # type: ignore[import]
    src = next((s for s in ALL_SOURCES if s.name == f"news_{source_key}"), None)
    if src is None:
        raise ValueError(f"No news source with name='news_{source_key}'")
    fetched, _ = await asyncio.to_thread(scrape_news_source, src)
    return fetched, 0


async def _run_vnexpress(source_url: str) -> tuple[int, int]:
    return await _run_news(source_url, "vnexpress")


async def _run_tuoitre(source_url: str) -> tuple[int, int]:
    return await _run_news(source_url, "tuoitre")


async def _run_dantri(source_url: str) -> tuple[int, int]:
    return await _run_news(source_url, "dantri")


async def _run_facebook_offb(source_url: str) -> tuple[int, int]:
    import json
    import os
    import uuid
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy import text as sa_text

    from api.scrapers.facebook import FacebookCrawler  # type: ignore[import]

    def _run_sync() -> tuple[int, int]:
        # Extract group id/slug from URL, e.g. .../groups/offb.hanoi → "offb.hanoi"
        group_id = source_url.rstrip("/").split("/groups/")[-1].split("/")[0]
        if not group_id:
            logger.error("_run_facebook_offb: cannot extract group_id from %s", source_url)
            return 0, 0

        sync_url = (
            os.environ.get("DATABASE_SYNC_URL")
            or os.environ.get("DATABASE_URL", "").replace(
                "postgresql+asyncpg://", "postgresql+psycopg2://"
            )
        )
        engine = create_engine(sync_url)
        scraped_at = datetime.now(timezone.utc)

        with engine.begin() as conn:
            crawler = FacebookCrawler(db_conn=conn)
            posts = crawler.fetch_group_posts(group_id, limit=50)

        if not posts:
            logger.info("_run_facebook_offb: 0 posts fetched from group %s", group_id)
            return 0, 0

        staged = 0
        for post in posts:
            content = json.dumps(
                {
                    "source_name": "facebook_offb",
                    "group_id": group_id,
                    "post_id": post.get("id"),
                    "text": post.get("message", ""),
                    "created_time": post.get("created_time"),
                },
                ensure_ascii=False,
            )
            try:
                with engine.begin() as conn:
                    conn.execute(
                        sa_text("""
                            INSERT INTO raw_flood_staging
                                (id, content, source_type, source_url, post_timestamp, processed)
                            VALUES (:id, :content, 'social_media', :source_url, :post_timestamp, false)
                        """),
                        {
                            "id": str(uuid.uuid4()),
                            "content": content,
                            "source_url": source_url,
                            "post_timestamp": scraped_at,
                        },
                    )
                staged += 1
            except Exception as exc:
                logger.warning("_run_facebook_offb: staging failed for post %s: %s", post.get("id"), exc)

        logger.info("_run_facebook_offb: staged %d posts from group %s", staged, group_id)
        return staged, 0

    return await asyncio.to_thread(_run_sync)


# ---------------------------------------------------------------------------
# Public registry
# ---------------------------------------------------------------------------

CRAWLER_REGISTRY: dict[str, tuple[str, callable]] = {
    # (display_label, async_fn)
    "ThoatNuocHanoiCrawler":    ("Thoát Nước Hà Nội (chính phủ)", _run_thoatnuoc),
    "VnExpressCrawler":         ("VnExpress (tin tức)", _run_vnexpress),
    "TuoiTreCrawler":           ("Tuổi Trẻ (tin tức)", _run_tuoitre),
    "DanTriCrawler":            ("Dân Trí (tin tức)", _run_dantri),
    "BaoMoiCrawler":            ("Báo Mới (tin tức)", _stub),
    "FacebookOFFBCrawler":      ("Facebook OFFB Group (mạng xã hội)", _run_facebook_offb),
    "FacebookLocalGroupCrawler":("Facebook Local Groups (mạng xã hội)", _stub),
    "InstagramHashtagCrawler":  ("Instagram Hashtag (mạng xã hội)", _stub),
}

CRAWLER_CLASS_NAMES: list[str] = list(CRAWLER_REGISTRY.keys())


async def run(crawler_class: str, source_url: str) -> tuple[int, int]:
    """Run a crawler by class name. Raises KeyError if unknown."""
    _label, fn = CRAWLER_REGISTRY[crawler_class]
    return await fn(source_url)
