"""
FIR-42: Flood depth enrichment — secondary search pipeline.

For flood_events rows where depth_cm IS NULL, runs a 3-tier search cascade
to find depth/severity information and UPDATE the original row in place.

Tier 1 — Google HTML:   google.com/search?q={loc}+ngập (no date — cross-year articles)
Tier 2 — DuckDuckGo:   html.duckduckgo.com/html/?q={loc}+ngập
Tier 3 — Haiku search:  claude-haiku-4-5-20251001 with web_search tool

Triggered via POST /admin/enrich-depth (202 + BackgroundTasks, FIR-16 pattern).
"""

import json
import logging
import os
import re
import time
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse

import anthropic
import requests
from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.config import settings
from api.scrapers.ai_extraction_worker import call_haiku_news, validate_severity
from api.scrapers.base import USER_AGENT, parse_article_body

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Search query helpers
# ---------------------------------------------------------------------------

def _clean_location_for_search(location: str) -> str:
    """Strip parenthetical qualifiers that help DB matching but hurt search engines."""
    return re.sub(r'\s*\([^)]*\)', '', location).strip()


def _format_date_vi(iso_date: str) -> str:
    """Convert ISO date to Vietnamese format: '2025-09-30' → '30/9/2025'."""
    try:
        y, m, d = iso_date[:10].split('-')
        return f"{int(d)}/{int(m)}/{y}"
    except Exception:
        return iso_date


def _build_search_queries(location_name: str) -> list[str]:
    """Return [primary, fallback] queries without date constraint.

    No date — chronic flood streets appear in articles from prior years, so
    date-constrained queries miss them. Primary targets depth ('ngập sâu'),
    fallback broadens to any flood coverage ('ngập').
    """
    loc = _clean_location_for_search(location_name)
    return [
        f'"{loc}" ngập sâu',
        f'"{loc}" ngập',
    ]
_SLEEP_BETWEEN_REQUESTS = 1.5
_MAX_ARTICLES_PER_SEARCH = 3
_REQUEST_TIMEOUT = 15

# Article selectors that cover VnExpress, Dân Trí, Thanh Niên, and generic sites
_ARTICLE_SELECTORS = [
    "article.fck_detail",
    "div.singular-content",
    "div.article-content",
    "div#abody",
    "div.detail-content",
    "div[class*='detail']",
    "div[class*='content']",
    "article",
]

ENRICH_SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích ngập lụt Hà Nội.
Tìm kiếm thông tin độ sâu ngập tại địa điểm và ngày cụ thể được cung cấp.
Chỉ trả JSON thuần túy, không giải thích:
{"depth_cm": <int|null>, "severity": <"minor"|"moderate"|"severe"|null>,
 "confidence": <"high"|"medium"|"low">, "source_url": <str|null>}
Nếu không tìm thấy thông tin cụ thể, trả: {"depth_cm": null, "severity": null, "confidence": "low", "source_url": null}
Severity: <20cm = minor | 20-60cm = moderate | >60cm = severe"""


# ---------------------------------------------------------------------------
# HTML parsers for search result pages
# ---------------------------------------------------------------------------

_GOOGLE_OWNED = {"google.com", "webcache.googleusercontent.com", "support.google.com", "accounts.google.com"}


def _is_external(url: str) -> bool:
    """Return True if URL is not a Google-owned domain."""
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        return not any(host == g or host.endswith("." + g) for g in _GOOGLE_OWNED)
    except Exception:
        return False


def parse_google_results(html: str) -> list[str]:
    """Extract article URLs from Google search result HTML.

    Tries multiple patterns to handle Google HTML format changes:
    1. jsname="UWckNb" — modern direct-href result links
    2. div.yuRUbf > a — alternative modern layout container
    3. /url?q= redirect hrefs — legacy pre-2020 format
    Returns up to _MAX_ARTICLES_PER_SEARCH URLs.
    """
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []

    def _collect(href: str) -> bool:
        if href.startswith("http") and _is_external(href) and href not in urls:
            urls.append(href)
        return len(urls) >= _MAX_ARTICLES_PER_SEARCH

    # Pattern 1: modern Google HTML — jsname="UWckNb" on result <a> tags
    for a in soup.find_all("a", {"jsname": "UWckNb"}, href=True):
        if _collect(a["href"]):
            return urls
    if urls:
        return urls

    # Pattern 2: yuRUbf container (alternative modern layout)
    for a in soup.select("div.yuRUbf > a[href]"):
        if _collect(a.get("href", "")):
            return urls
    if urls:
        return urls

    # Pattern 3: legacy /url?q= redirects (pre-2020 Google HTML)
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if not href.startswith("/url?"):
            continue
        qs = parse_qs(urlparse(href).query)
        real = qs.get("q", [None])[0]
        if real and _collect(real):
            return urls

    return urls


def parse_ddg_results(html: str) -> list[str]:
    """Extract article URLs from DuckDuckGo HTML search result page.

    DDG HTML (html.duckduckgo.com) returns direct hrefs on result__a elements.
    Returns up to _MAX_ARTICLES_PER_SEARCH URLs.
    """
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for a in soup.select("a.result__a"):
        href: str = a.get("href", "")
        if href.startswith("http") and href not in urls:
            urls.append(href)
        if len(urls) >= _MAX_ARTICLES_PER_SEARCH:
            break
    return urls


# ---------------------------------------------------------------------------
# Tier 3: Haiku with web_search tool
# ---------------------------------------------------------------------------

def call_haiku_enrich(
    client: anthropic.Anthropic,
    location_name: str,
) -> dict | None:
    """Ask Haiku to search the web for flood depth at location.

    Uses the built-in web_search tool (requires claude-haiku-4-5-20251001+).
    No date constraint — matches cross-year articles for chronic flood streets.
    Returns parsed dict {depth_cm, severity, confidence, source_url} or None.
    """
    if os.getenv("FLOOD_SCRAPER_MOCK_LLM") == "1":
        return {"depth_cm": None, "severity": None, "confidence": "low", "source_url": None}

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=512,
            tools=[{"type": "web_search_20260209", "name": "web_search", "allowed_callers": ["direct"]}],
            system=[{
                "type": "text",
                "text": ENRICH_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"Địa điểm: {location_name}",
            }],
        )
        # Find the final text block (after any tool_use blocks)
        text = next((b.text for b in response.content if b.type == "text"), None)
        logger.debug(
            "Haiku enrich raw response for %s — stop_reason=%s blocks=%s text=%r",
            location_name,
            response.stop_reason,
            [b.type for b in response.content],
            text,
        )
        if not text:
            logger.warning("Haiku enrich no text block for %s (stop_reason=%s)", location_name, response.stop_reason)
            return None
        raw = text.strip()
        if not raw:
            logger.warning("Haiku enrich empty text block for %s", location_name)
            return None
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0].strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as json_exc:
            logger.warning("Haiku enrich JSON parse failed for %s — raw=%r — %s", location_name, raw[:300], json_exc)
            return None
    except Exception as exc:
        logger.warning("Haiku enrich call failed for %s: %s", location_name, exc)
        return None


# ---------------------------------------------------------------------------
# Per-event enrichment (3-tier cascade)
# ---------------------------------------------------------------------------

def _fetch_search_page(url: str) -> str | None:
    """Fetch a search result page. Returns HTML or None on error."""
    try:
        time.sleep(_SLEEP_BETWEEN_REQUESTS)
        resp = requests.get(
            url,
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return resp.text
    except Exception as exc:
        logger.debug("Search page fetch failed (%s): %s", url, exc)
        return None


def _fetch_article_and_extract(url: str, event_date: str, client: anthropic.Anthropic) -> dict | None:
    """Fetch one article URL and run Haiku news extraction on it."""
    try:
        time.sleep(_SLEEP_BETWEEN_REQUESTS)
        resp = requests.get(
            url,
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        resp.encoding = "utf-8"
        body = parse_article_body(resp.text, _ARTICLE_SELECTORS)
        if not body or len(body) < 50:
            return None
        result = call_haiku_news(client, body, event_date)
        return result
    except Exception as exc:
        logger.debug("Article fetch/extract failed (%s): %s", url, exc)
        return None


def _depth_from_extraction(result: dict | None) -> tuple[int | None, str | None]:
    """Pull depth_cm + corrected severity from a call_haiku_news result dict."""
    if not result:
        return None, None
    events = result.get("events", [])
    if not events:
        return None, None
    # Take first event with depth
    for ev in events:
        depth = ev.get("depth_cm")
        if depth is not None:
            severity = validate_severity(depth, ev.get("severity"))
            return depth, severity
    return None, None


def enrich_event(
    conn: Connection,
    event_id: str,
    location_name: str | None,
    event_date: str,
    client: anthropic.Anthropic,
) -> tuple[bool, str]:
    """Run 3-tier search cascade for one flood_event.

    Returns (enriched: bool, search_source: str).
    search_source is 'google' | 'ddg' | 'haiku' | 'none'.
    """
    if not location_name:
        return False, "none"

    queries = _build_search_queries(location_name)

    # ---- Tier 1: Google HTML (primary query, then fallback) ----
    for query_str in queries:
        query = quote_plus(query_str)
        html = _fetch_search_page(f"https://www.google.com/search?q={query}&hl=vi&lr=lang_vi")
        if not html:
            continue
        article_urls = parse_google_results(html)
        if not article_urls:
            continue  # no results for this query variant → try fallback
        for url in article_urls:
            result = _fetch_article_and_extract(url, event_date, client)
            depth, severity = _depth_from_extraction(result)
            if depth is not None:
                _update_flood_event(conn, event_id, depth, severity)
                return True, "google"
        break  # had URLs but none yielded depth — don't retry fallback query

    # ---- Tier 2: DuckDuckGo HTML (primary query, then fallback) ----
    for query_str in queries:
        html = _fetch_search_page(
            f"https://html.duckduckgo.com/html/?{urlencode({'q': query_str})}"
        )
        if not html:
            continue
        article_urls = parse_ddg_results(html)
        if not article_urls:
            continue
        for url in article_urls:
            result = _fetch_article_and_extract(url, event_date, client)
            depth, severity = _depth_from_extraction(result)
            if depth is not None:
                _update_flood_event(conn, event_id, depth, severity)
                return True, "ddg"
        break

    # ---- Tier 3: Haiku web_search ----
    enrich_result = call_haiku_enrich(client, location_name)
    if enrich_result:
        depth = enrich_result.get("depth_cm")
        if depth is not None:
            severity = validate_severity(depth, enrich_result.get("severity"))
            _update_flood_event(conn, event_id, depth, severity)
            return True, "haiku"

    return False, "none"


def _update_flood_event(
    conn: Connection,
    event_id: str,
    depth_cm: int,
    severity: str | None,
) -> None:
    conn.execute(
        text("""
            UPDATE flood_events
            SET depth_cm = :depth_cm, severity = COALESCE(:severity, severity)
            WHERE id = CAST(:id AS uuid) AND depth_cm IS NULL
        """),
        {"depth_cm": depth_cm, "severity": severity, "id": event_id},
    )


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_enrichment(conn: Connection, limit: int = 50) -> dict:
    """Scan flood_events WHERE depth_cm IS NULL and enrich each via 3-tier cascade.

    Returns {enriched, unchanged, failed, search_sources}.
    search_sources: dict counting how many events were enriched per tier.
    """
    rows = conn.execute(
        text("""
            SELECT id, location_name, event_date
            FROM flood_events
            WHERE depth_cm IS NULL
            ORDER BY event_date DESC
            LIMIT :lim
        """),
        {"lim": limit},
    ).fetchall()

    if not rows:
        return {"enriched": 0, "unchanged": 0, "failed": 0, "search_sources": {}}

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    enriched = 0
    unchanged = 0
    failed = 0
    search_sources: dict[str, int] = {}

    for row in rows:
        try:
            ok, source = enrich_event(
                conn,
                str(row.id),
                row.location_name,
                row.event_date.isoformat() if hasattr(row.event_date, "isoformat") else str(row.event_date),
                client,
            )
            if ok:
                enriched += 1
                search_sources[source] = search_sources.get(source, 0) + 1
            else:
                unchanged += 1
        except Exception as exc:
            logger.exception("Enrichment failed for event %s: %s", row.id, exc)
            failed += 1

    conn.commit()
    logger.info(
        "FIR-42 enrichment done: enriched=%d unchanged=%d failed=%d sources=%s",
        enriched, unchanged, failed, search_sources,
    )
    return {
        "enriched": enriched,
        "unchanged": unchanged,
        "failed": failed,
        "search_sources": search_sources,
    }
