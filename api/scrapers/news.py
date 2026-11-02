"""
News archive scraper for Vietnamese flood coverage.

Sources (10 total):
  1.  VnExpress   — https://vnexpress.net/chu-de/ngap-o-ha-noi-4591         ✅ verified
  2.  Tuổi Trẻ   — https://tuoitre.vn/ngap-lut-ha-noi.html                  🔶 inferred
  3.  Dân Trí    — https://dantri.com.vn/ha-noi-ngap.tag                    ✅ verified
  4.  Thanh Niên — https://thanhnien.vn/ha-noi-ngap-lut-tags577637.html      ✅ verified
  5.  VietnamNet — https://vietnamnet.vn/ha-noi-ngap-lut-tag14266181710453676058.html  ✅ verified
  6.  Người LĐ  — https://nld.com.vn/ha-noi-ngap-nang.html                  ✅ verified
  7.  Tiền Phong — https://tienphong.vn/tags/bmfhuq1wIMO6bmc=/ngap-ung.tpo  ✅ verified
  8.  Lao Động  — https://laodong.vn/tag/ngap-ung-ha-noi.html                ⚠ needs verification
  9.  Znews      — https://znews.vn/tag/ngap-ung-ha-noi.html                 ⚠ needs verification
  10. 24h        — https://www.24h.com.vn/tag/ngap-ung-ha-noi.html           ⚠ needs verification

Sources 8–10 have best-guess URLs. If page 1 returns 0 article URLs the scraper logs
a SELECTOR_FAILURE warning and skips that source — no crash, just a warning to fix the URL.

Flow per article:
  1. Fetch topic/tag list page → extract article URLs
  2. Paginate until max_pages or empty page
  3. Fetch each article → extract body text (capped at 3000 chars)
  4. INSERT raw text into raw_flood_staging as JSON blob
     FIR-18 AI extraction worker reads staged rows and writes to flood_events.

HTTP errors are caught per-article; one failure does not stop the batch.

Usage:
    from api.scrapers.news import ALL_SOURCES, scrape_news_source
    records_staged, errors = scrape_news_source(ALL_SOURCES[0], max_pages=5)

    # Or run all 10:
    from api.scrapers.news import scrape_all_news
    total, per_source = scrape_all_news(max_pages=5)
"""

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import requests
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text as sa_text

from api.scrapers.base import contains_flood_keywords
from api.scrapers.base import parse_article_body as _parse_article_generic
from api.scrapers.base import parse_article_list as _parse_list_generic

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (compatible; FloodRiskBot/1.0; +https://floodrisk.vn/about)"
)
SLEEP_BETWEEN_REQUESTS = 1.5  # seconds — respectful crawl rate


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

def _paginate_append(url: str, page: int) -> str:
    """Append -pN to URL for page N≥2. Works for extension-free URLs (VnExpress)."""
    return url if page == 1 else f"{url}-p{page}"


def _paginate_before_ext(url: str, page: int) -> str:
    """Insert -pN before the file extension for page N≥2.
    Works for .html, .tag, .tpo, .htm etc.
    E.g. foo.html → foo-p2.html, foo.tag → foo-p2.tag.
    Falls back to appending if no extension is detected.
    """
    if page == 1:
        return url
    last_segment = url.rsplit("/", 1)[-1]
    if "." in last_segment:
        base, ext = url.rsplit(".", 1)
        return f"{base}-p{page}.{ext}"
    return f"{url}-p{page}"


def _paginate_query(url: str, page: int) -> str:
    """Add ?page=N query parameter for page N≥2."""
    return url if page == 1 else f"{url}?page={page}"


# ---------------------------------------------------------------------------
# NewsSource config
# ---------------------------------------------------------------------------

@dataclass
class NewsSource:
    """Config for a single news site scraper."""
    name: str                         # source name, e.g. "news_vnexpress"
    list_url: str                     # topic/tag page URL (page 1)
    domain: str                       # URL prefix for relative links, e.g. "https://vnexpress.net"
    list_selectors: list[str]         # CSS selectors (tried in order) for article title <a> tags
    article_selectors: list[str]      # CSS selectors (tried in order) for article body container
    paginate: Callable[[str, int], str] = field(default=_paginate_append)


# ---------------------------------------------------------------------------
# 10 source definitions
# ---------------------------------------------------------------------------

ALL_SOURCES: list[NewsSource] = [
    # 1. VnExpress — topic page confirmed working (FIR-27 validity check 2026-04-11)
    NewsSource(
        name="news_vnexpress",
        list_url="https://vnexpress.net/chu-de/ngap-o-ha-noi-4591",
        domain="https://vnexpress.net",
        list_selectors=["article.item-news a.title-news", "h3.title-news a"],
        article_selectors=["article.fck_detail", "div.sidebar-1", "div[class*='detail']"],
        paginate=_paginate_append,
    ),
    # 2. Tuổi Trẻ — old /chu-de/ngap-ung-ha-noi.htm broken (redirects to homepage).
    #   New URL inferred from tuoitre.vn tag pattern (cf. /da-nang-ngap-lut.html).
    NewsSource(
        name="news_tuoitre",
        list_url="https://tuoitre.vn/ngap-lut-ha-noi.html",
        domain="https://tuoitre.vn",
        list_selectors=["h3.title-news a", "h2.title-news a", ".box-category-item a"],
        article_selectors=["div.detail-cmain", "div#detail-id", "div[class*='detail']"],
        paginate=_paginate_before_ext,
    ),
    # 3. Dân Trí — tag URL verified via web search
    NewsSource(
        name="news_dantri",
        list_url="https://dantri.com.vn/ha-noi-ngap.tag",
        domain="https://dantri.com.vn",
        list_selectors=[
            "h3.article-title a",
            ".article-item h3 a",
            "h2.article-title a",
            "h3 a",
        ],
        article_selectors=[
            "div.singular-content",
            "div.article-content",
            "div#news-content",
            "div[class*='content']",
        ],
        paginate=_paginate_before_ext,
    ),
    # 4. Thanh Niên — tag URL verified via web search (tags577637)
    NewsSource(
        name="news_thanhnien",
        list_url="https://thanhnien.vn/ha-noi-ngap-lut-tags577637.html",
        domain="https://thanhnien.vn",
        list_selectors=[
            "h3.story__headline a",
            "h2.story__headline a",
            ".story-list h3 a",
            "h3 a",
        ],
        article_selectors=[
            "div#abody",
            "div.detail-content",
            "div.article-body",
            "div[class*='detail']",
        ],
        paginate=_paginate_before_ext,
    ),
    # 5. VietnamNet — tag URL verified via web search (tag14266181710453676058)
    NewsSource(
        name="news_vietnamnet",
        list_url="https://vietnamnet.vn/ha-noi-ngap-lut-tag14266181710453676058.html",
        domain="https://vietnamnet.vn",
        list_selectors=[
            "h3.item__title a",
            "h2.item__title a",
            ".list-news h3 a",
            "h3 a",
        ],
        article_selectors=[
            "div.content-detail-body",
            "div#main-content",
            "div.maincontent",
            "div[class*='content']",
        ],
        paginate=_paginate_before_ext,
    ),
    # 6. Người Lao Động — tag URL verified via web search
    NewsSource(
        name="news_nld",
        list_url="https://nld.com.vn/ha-noi-ngap-nang.html",
        domain="https://nld.com.vn",
        list_selectors=[
            "h3.cate-title a",
            "h2.cate-title a",
            ".list-news h3 a",
            "h3 a",
        ],
        article_selectors=[
            "div.detail-content",
            "div#detail-content",
            "div.content",
            "div[class*='detail']",
        ],
        paginate=_paginate_before_ext,
    ),
    # 7. Tiền Phong — general ngap-ung tag (not Hanoi-specific); FIR-18 filters non-Hanoi.
    #   URL verified via web search. Pagination format uncertain; _paginate_before_ext
    #   will produce foo-p2.tpo which may or may not work — safe to fail gracefully.
    NewsSource(
        name="news_tienphong",
        list_url="https://tienphong.vn/tags/bmfhuq1wIMO6bmc=/ngap-ung.tpo",
        domain="https://tienphong.vn",
        list_selectors=[
            "h3.story-title a",
            "h2.story__title a",
            ".story-item h3 a",
            "h3 a",
        ],
        article_selectors=[
            "div.article__body",
            "div.article-detail",
            "div.content-detail",
            "div[class*='article']",
        ],
        paginate=_paginate_before_ext,
    ),
    # 8. Lao Động — ⚠ URL needs verification. If page 1 returns 0 URLs, a
    #   SELECTOR_FAILURE warning is logged; update list_url to fix.
    NewsSource(
        name="news_laodong",
        list_url="https://laodong.vn/tag/ngap-ung-ha-noi.html",
        domain="https://laodong.vn",
        list_selectors=[
            "h3.article-title a",
            "h2.article-title a",
            ".list-news h3 a",
            "h3 a",
        ],
        article_selectors=[
            "div.article-content",
            "div.content-article",
            "div.detail-content",
            "div[class*='content']",
        ],
        paginate=_paginate_before_ext,
    ),
    # 9. Znews (formerly Zing News) — ⚠ URL needs verification.
    NewsSource(
        name="news_znews",
        list_url="https://znews.vn/tag/ngap-ung-ha-noi.html",
        domain="https://znews.vn",
        list_selectors=[
            "h3.article-title a",
            "h3.headline a",
            ".article-item h3 a",
            "h3 a",
        ],
        article_selectors=[
            "div.the-article-body",
            "div.article__content",
            "div.article-body",
            "div[class*='article']",
        ],
        paginate=_paginate_before_ext,
    ),
    # 10. 24h — ⚠ URL needs verification.
    NewsSource(
        name="news_24h",
        list_url="https://www.24h.com.vn/tag/ngap-ung-ha-noi.html",
        domain="https://www.24h.com.vn",
        list_selectors=[
            "h3.box-title a",
            "h2.box-title a",
            ".news-item h3 a",
            "h3 a",
        ],
        article_selectors=[
            "div.content-news",
            "div.article-content",
            "div#detail-content",
            "div[class*='content']",
        ],
        paginate=_paginate_before_ext,
    ),
]

# Convenience lookup by source name
_SOURCE_BY_NAME: dict[str, NewsSource] = {s.name: s for s in ALL_SOURCES}


# ---------------------------------------------------------------------------
# Public scrape API
# ---------------------------------------------------------------------------

def scrape_news_source(
    source: NewsSource,
    max_pages: int = 10,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    """Scrape a single NewsSource. Returns (records_staged, errors)."""
    return _scrape_source(source=source, max_pages=max_pages, dry_run=dry_run)


def scrape_all_news(
    max_pages: int = 10,
    dry_run: bool = False,
) -> tuple[int, dict[str, tuple[int, list[str]]]]:
    """Scrape all 10 sources. Returns (total_staged, {source_name: (records, errors)})."""
    total = 0
    per_source: dict[str, tuple[int, list[str]]] = {}
    for source in ALL_SOURCES:
        records, errors = scrape_news_source(source, max_pages=max_pages, dry_run=dry_run)
        per_source[source.name] = (records, errors)
        total += records
    return total, per_source


# Backward-compatible convenience wrappers (used in run_scrapers.py v1)
def scrape_vnexpress(
    max_pages: int = 10, dry_run: bool = False
) -> tuple[int, list[str]]:
    return scrape_news_source(_SOURCE_BY_NAME["news_vnexpress"], max_pages, dry_run)


def scrape_tuoitre(
    max_pages: int = 10, dry_run: bool = False
) -> tuple[int, list[str]]:
    return scrape_news_source(_SOURCE_BY_NAME["news_tuoitre"], max_pages, dry_run)


# ---------------------------------------------------------------------------
# Core scraping engine (internal)
# ---------------------------------------------------------------------------

def _scrape_source(
    source: NewsSource,
    max_pages: int,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    errors: list[str] = []
    records_staged = 0

    article_urls = _fetch_article_list(source, max_pages, errors)
    logger.info("%s: found %d articles", source.name, len(article_urls))

    if dry_run:
        logger.info(
            "[DRY RUN] %s: would stage %d articles. No DB writes.",
            source.name, len(article_urls),
        )
        return 0, errors

    engine = _get_sync_engine()
    scraped_at = datetime.now(timezone.utc)
    stats = {"fetched": 0, "short": 0, "filtered": 0, "staged": 0, "error": 0}

    for url in article_urls:
        try:
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            logger.debug("Fetching article: %s", url)
            html = _fetch_url(url)
            article_text = _parse_article_generic(html, source.article_selectors)
            stats["fetched"] += 1

            if not article_text or len(article_text) < 100:
                stats["short"] += 1
                logger.debug("Skipping (short body, %d chars): %s", len(article_text or ""), url)
                continue

            if not contains_flood_keywords(article_text):
                stats["filtered"] += 1
                logger.debug("Skipping (no flood keywords): %s", url)
                continue

            content = json.dumps(
                {"source_name": source.name, "text": article_text},
                ensure_ascii=False,
            )
            with engine.begin() as conn:
                conn.execute(
                    sa_text("""
                        INSERT INTO raw_flood_staging
                            (id, content, source_type, source_url, post_timestamp, processed)
                        VALUES
                            (:id, :content, 'news', :source_url, :post_timestamp, false)
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "content": content,
                        "source_url": url,
                        "post_timestamp": scraped_at,
                    },
                )
            stats["staged"] += 1
            records_staged += 1

        except Exception as exc:
            stats["error"] += 1
            msg = f"Error processing {url}: {exc}"
            logger.warning(msg)
            errors.append(msg)

    logger.info(
        "%s summary: fetched=%d short=%d filtered=%d staged=%d error=%d",
        source.name, stats["fetched"], stats["short"], stats["filtered"], stats["staged"], stats["error"],
    )
    return records_staged, errors


def _get_sync_engine():
    sync_url = (
        os.environ.get("DATABASE_SYNC_URL")
        or os.environ.get("DATABASE_URL", "").replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        )
    )
    return create_engine(sync_url)


def _fetch_article_list(
    source: NewsSource,
    max_pages: int,
    errors: list[str],
) -> list[str]:
    """Fetch paginated article list, return deduplicated article URLs."""
    all_urls: list[str] = []
    page = 1

    while page <= max_pages:
        url = source.paginate(source.list_url, page)
        try:
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            logger.info("Fetching list page %d: %s", page, url)
            html = _fetch_url(url)
            urls = _parse_list_generic(html, source.domain, source.list_selectors)
            if not urls:
                if page == 1:
                    msg = (
                        f"[SELECTOR_FAILURE] {source.list_url} page 1 returned 0 article URLs. "
                        f"CSS selectors may be stale or list_url may be wrong — "
                        f"inspect the site HTML and update the NewsSource config."
                    )
                    errors.append(msg)
                    logger.error(
                        "SELECTOR_FAILURE: %s page 1 = 0 URLs. "
                        "Site HTML may have changed or URL is wrong. Halting this source.",
                        source.list_url,
                    )
                break

            new_urls = [u for u in urls if u not in all_urls]
            if len(new_urls) < len(urls):
                logger.warning(
                    "Page %d contained %d duplicate URLs — pagination may be repeating page 1",
                    page, len(urls) - len(new_urls),
                )
            all_urls.extend(new_urls)
            if not new_urls:
                break
            page += 1
        except Exception as exc:
            errors.append(f"List page {page} fetch failed: {exc}")
            break

    return all_urls


# ---------------------------------------------------------------------------
# HTTP helper (news-specific: uses FloodRiskBot User-Agent for polite crawling)
# ---------------------------------------------------------------------------

def _fetch_url(url: str) -> str:
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text
