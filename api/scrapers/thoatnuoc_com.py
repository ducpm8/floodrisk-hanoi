"""
Scraper for thoatnuochanoi.com.vn/tin-tuc — Hanoi drainage authority news.

Fetches article listings, extracts flood event data (location, date, depth),
and yields structured dicts. Caller must call normalize_ward() and resolve
ward centroid before DB insert.

Rate: 1 req/s (overrides BaseCrawler's 3–8s default).
Pagination: ?page=N until empty page or max_pages reached.
"""

import logging
import re
import time
from datetime import date
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from api.scrapers.base import BaseCrawler

logger = logging.getLogger(__name__)

BASE_URL = "https://thoatnuochanoi.com.vn"
LISTING_URL = f"{BASE_URL}/tin-tuc"

_DEPTH_RE = re.compile(
    r"(?:ngập|sâu)\s+(?:khoảng\s+)?(\d+)(?:\s*[-–]\s*\d+)?\s*cm",
    re.IGNORECASE,
)

_DATE_RE_DMY = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})")

_VI_MONTHS: dict[str, int] = {
    f"tháng {i}": i for i in range(1, 13)
}
_DATE_RE_VI = re.compile(
    r"(\d{1,2})\s+(tháng\s+\d{1,2})\s+(\d{4})",
    re.IGNORECASE,
)

# Admin prefixes that may appear before the ward name in Vietnamese text
_ADMIN_PREFIXES = ("phường ", "xã ", "thị trấn ", "quận ", "huyện ", "thành phố ")

# Patterns that hint at a flood location in article text
_LOCATION_PATTERNS = [
    re.compile(
        r"(?:tại|ở|khu vực|địa bàn|trên địa bàn)\s+"
        r"(?:phường|xã|thị trấn|quận|huyện)?\s*"
        r"([A-ZĐÀÁẢÃẠĂẮẶẴẲÂẤẦẨẪẬÈÉẸẺẼÊẾỀỆỂỄÌÍỊỈĨÒÓỌỎÕÔỐỒỘỔỖƠỚỜỢỞỠÙÚỤỦŨƯỨỪỰỬỮỲÝỴỶỸ]"
        r"[^,.\n\r]{2,60})",
        re.UNICODE,
    ),
    re.compile(
        r"(?:phường|xã|thị trấn)\s+"
        r"([A-ZĐÀÁẢÃẠĂẮẶẴẲÂẤẦẨẪẬ][^,.\n\r]{2,50})",
        re.UNICODE,
    ),
]

# Selectors tried in order when parsing article listings
_LISTING_SELECTORS = [
    "article a[href]",
    ".item-title a[href]",
    ".post-title a[href]",
    ".news-title a[href]",
    ".article-title a[href]",
    "h2 a[href]",
    "h3 a[href]",
    ".title a[href]",
]

# Selectors tried in order when looking for a date on an article page
_DATE_SELECTORS = [
    "time[datetime]",
    ".date",
    ".post-date",
    ".article-date",
    ".published",
    ".entry-date",
    ".news-date",
]


class ThoatNuocComScraper(BaseCrawler):
    """Scraper for thoatnuochanoi.com.vn flood news articles."""

    RATE_LIMIT_SECS = 1.0

    def _polite_delay(self) -> None:
        time.sleep(self.RATE_LIMIT_SECS)

    def scrape(self, max_pages: int = 50) -> Iterator[dict]:
        """
        Yield raw flood event dicts.

        Each dict: source_url, title, location_name, event_date (date), depth_cm (int|None).
        Caller must normalize_ward(location_name) before DB insert.
        """
        seen_urls: set[str] = set()

        for page_num in range(1, max_pages + 1):
            listing_url = f"{LISTING_URL}?page={page_num}"
            logger.info("listing page %d: %s", page_num, listing_url)

            html = self.fetch_with_retry(listing_url)
            if not html:
                logger.warning("fetch failed for listing page %d — stopping", page_num)
                break

            articles = self._parse_listing(html)
            if not articles:
                logger.info("no articles on page %d — done", page_num)
                break

            new_articles = [(u, t, d) for u, t, d in articles if u not in seen_urls]
            if not new_articles:
                logger.info("all articles on page %d already seen — done", page_num)
                break

            for art_url, title, listing_date in new_articles:
                seen_urls.add(art_url)
                event = self._fetch_and_parse_article(art_url, title, listing_date)
                if event:
                    yield event

    # ── Listing page ──────────────────────────────────────────────────────────

    def _parse_listing(self, html: str) -> list[tuple[str, str, Optional[str]]]:
        """Return [(url, title, raw_date_str)] from a listing page."""
        soup = BeautifulSoup(html, "html.parser")
        results: list[tuple[str, str, Optional[str]]] = []

        links: list = []
        for sel in _LISTING_SELECTORS:
            links = soup.select(sel)
            if links:
                break

        if not links:
            main = soup.find("main") or soup.find(
                "div", class_=re.compile(r"content|main|list", re.I)
            )
            if main:
                links = main.find_all("a", href=True)

        for a in links:
            href = a.get("href", "").strip()
            if not href or href in ("#", "/"):
                continue
            if not href.startswith("http"):
                href = BASE_URL + (href if href.startswith("/") else f"/{href}")

            title = a.get_text(strip=True)
            if len(title) < 10:
                continue

            date_str = self._date_near_element(a)
            results.append((href, title, date_str))

        return results

    def _date_near_element(self, el) -> Optional[str]:
        """Walk up the DOM looking for a D/M/Y date string near the link."""
        node = el.parent
        for _ in range(4):
            if node is None:
                break
            m = _DATE_RE_DMY.search(node.get_text(" ", strip=True))
            if m:
                return m.group(0)
            node = node.parent
        return None

    # ── Article page ─────────────────────────────────────────────────────────

    def _fetch_and_parse_article(
        self, url: str, title: str, listing_date: Optional[str]
    ) -> Optional[dict]:
        html = self.fetch_with_retry(url)
        if not html:
            logger.warning("fetch failed for article: %s", url)
            return None

        soup = BeautifulSoup(html, "html.parser")
        raw_text = soup.get_text(" ", strip=True)

        event_date = self._parse_event_date(soup, listing_date)
        if not event_date:
            logger.debug("no event date found — skipping %s", url)
            return None

        return {
            "source_url": url,
            "title": title,
            "location_name": self._extract_location(title, raw_text),
            "event_date": event_date,
            "depth_cm": self._extract_depth(raw_text),
        }

    def _parse_event_date(
        self, soup: BeautifulSoup, fallback: Optional[str]
    ) -> Optional[date]:
        for sel in _DATE_SELECTORS:
            el = soup.select_one(sel)
            if el:
                text = el.get("datetime") or el.get_text(strip=True)
                parsed = self._parse_date_str(text)
                if parsed:
                    return parsed

        # Search first 500 chars of body text
        parsed = self._parse_date_str(soup.get_text(" ", strip=True)[:500])
        if parsed:
            return parsed

        return self._parse_date_str(fallback) if fallback else None

    def _parse_date_str(self, text: Optional[str]) -> Optional[date]:
        if not text:
            return None

        m = _DATE_RE_DMY.search(text)
        if m:
            try:
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 2015 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                    return date(y, mo, d)
            except ValueError:
                pass

        m = _DATE_RE_VI.search(text)
        if m:
            try:
                d = int(m.group(1))
                mo = _VI_MONTHS.get(re.sub(r"\s+", " ", m.group(2).strip().lower()))
                y = int(m.group(3))
                if mo and 2015 <= y <= 2030 and 1 <= d <= 31:
                    return date(y, mo, d)
            except ValueError:
                pass

        return None

    def _extract_location(self, title: str, raw_text: str) -> Optional[str]:
        """
        Return raw Vietnamese location name from title then article body.
        Caller is responsible for normalize_ward() before DB lookup.
        """
        for source in (title, raw_text[:1000]):
            for pat in _LOCATION_PATTERNS:
                m = pat.search(source)
                if m:
                    loc = m.group(1).strip()
                    # Strip trailing admin noise ("ngập", "bị", etc.)
                    loc = re.split(r"\s+(?:ngập|bị|có|đã|xảy)", loc)[0].strip()
                    if loc:
                        return loc

        # Fallback: title carries the location most of the time
        return title[:100] if title else None

    def _extract_depth(self, text: str) -> Optional[int]:
        m = _DEPTH_RE.search(text)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        return None
