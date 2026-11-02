"""
BaseCrawler: shared retry logic, polite delays, and Playwright fallback.

All source-specific crawlers (Facebook, Instagram, news) inherit from this.
No duplicate retry logic in subclasses.

Module-level helpers (no class needed):
  fetch_url(url)              — simple GET, raises on non-2xx
  parse_article_list(...)     — extract article URLs from listing/search pages
  parse_article_body(...)     — extract body text from article pages
"""

import logging
import random
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
MIN_DELAY = 3.0
MAX_DELAY = 8.0

_REQUEST_TIMEOUT = 15
_ARTICLE_BODY_CAP = 3000

# ---------------------------------------------------------------------------
# Flood keyword pre-filter (used by facebook.py and news.py)
# ---------------------------------------------------------------------------

FLOOD_KEYWORDS: frozenset[str] = frozenset({
    "ngập", "lụt", "ngập lụt", "ngập úng",
    "ngập nước", "nước ngập", "thoát nước",
    "mực nước", "mưa lớn", "nước dâng", "ngập sâu","di chuyển", "khó khăn",
    "cm",
    "mét nước",
})


def contains_flood_keywords(text: str) -> bool:
    """Return True if text contains at least one flood-related keyword (case-insensitive)."""
    t = text.lower()
    return any(kw in t for kw in FLOOD_KEYWORDS)


# ---------------------------------------------------------------------------
# Module-level HTTP + HTML helpers (used by news.py and enrichment_worker.py)
# ---------------------------------------------------------------------------

def fetch_url(url: str, timeout: int = _REQUEST_TIMEOUT, user_agent: str = USER_AGENT) -> str:
    """Simple GET. Raises requests.HTTPError on non-2xx."""
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": user_agent})
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_article_list(html: str, domain: str, selectors: list[str]) -> list[str]:
    """Extract article URLs from a topic/tag/listing page.

    Tries each CSS selector in order; stops at the first that yields links.
    Relative hrefs are prepended with domain.
    """
    soup = BeautifulSoup(html, "html.parser")
    for sel in selectors:
        urls: list[str] = []
        for a in soup.select(sel):
            href = a.get("href", "")
            if not href or href == "#":
                continue
            if href.startswith("/"):
                href = domain.rstrip("/") + href
            if href.startswith(domain) and href not in urls:
                urls.append(href)
        if urls:
            return urls
    return []


def parse_article_body(html: str, selectors: list[str]) -> str:
    """Extract article body text from an article page.

    Tries each CSS selector in order; returns text from the first match,
    capped at _ARTICLE_BODY_CAP chars.
    """
    soup = BeautifulSoup(html, "html.parser")
    for sel in selectors:
        body = soup.select_one(sel)
        if body:
            return body.get_text(separator=" ", strip=True)[:_ARTICLE_BODY_CAP]
    return ""


class BaseCrawler:
    """
    Fetch URLs with polite delays, retry on 429, Playwright fallback on 403.
    Subclasses override `_fetch_with_playwright` for JS-rendered pages.
    """

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def fetch_with_retry(
        self,
        url: str,
        max_retries: int = 3,
        use_playwright_on_403: bool = True,
    ) -> Optional[str]:
        """
        Fetch URL with exponential backoff on 429 and Playwright fallback on 403.
        Returns HTML text or None on unrecoverable failure.
        """
        delay = MIN_DELAY
        for attempt in range(max_retries):
            self._polite_delay()
            try:
                resp = self.session.get(url, timeout=20)

                if resp.status_code == 200:
                    return resp.text

                if resp.status_code == 429:
                    wait = delay * (2 ** attempt) + random.uniform(0, 2)
                    logger.warning("429 on %s — waiting %.1fs (attempt %d)", url, wait, attempt + 1)
                    time.sleep(wait)
                    continue

                if resp.status_code == 403 and use_playwright_on_403:
                    logger.info("403 on %s — falling back to Playwright", url)
                    return self._fetch_with_playwright(url)

                logger.warning("HTTP %d on %s", resp.status_code, url)
                return None

            except requests.RequestException as exc:
                logger.warning("Request error on %s (attempt %d): %s", url, attempt + 1, exc)
                if attempt < max_retries - 1:
                    time.sleep(delay * (2 ** attempt))

        logger.error("All %d attempts failed for %s", max_retries, url)
        return None

    def _polite_delay(self) -> None:
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    def _fetch_with_playwright(self, url: str) -> Optional[str]:
        """
        Stealth browser fallback — override in subclasses that maintain persistent sessions.
        Uses CloakBrowser (source-level Chromium patches) when available; falls back to plain Playwright.
        """
        _launch = None
        try:
            from cloakbrowser import launch as _launch  # type: ignore[import]
        except ImportError:
            pass

        if _launch is None:
            try:
                from playwright.sync_api import sync_playwright  # type: ignore[import]
            except ImportError:
                logger.error("neither cloakbrowser nor playwright installed — cannot fallback for %s", url)
                return None

            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    ctx = browser.new_context(user_agent=USER_AGENT)
                    page = ctx.new_page()
                    self._polite_delay()
                    page.goto(url, wait_until="networkidle", timeout=30_000)
                    html = page.content()
                    browser.close()
                    return html
            except Exception as exc:
                logger.error("Playwright fetch failed for %s: %s", url, exc)
                return None

        try:
            browser = _launch(headless=True, humanize=True)
            ctx = browser.new_context(user_agent=USER_AGENT)
            page = ctx.new_page()
            self._polite_delay()
            page.goto(url, wait_until="networkidle", timeout=30_000)
            html = page.content()
            browser.close()
            return html
        except Exception as exc:
            logger.error("CloakBrowser fetch failed for %s: %s", url, exc)
            return None
