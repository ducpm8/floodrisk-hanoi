"""
Scraper for thoatnuochanoi.vn/ungngap — official government flood monitoring.

Site is JavaScript-rendered (AJAX populates the flood table after page load).
Uses Playwright headless Chromium to get the rendered DOM.

First-time setup: after `pip install playwright`, run `playwright install chromium`.

Each table row is stored in raw_flood_staging as a JSON blob.
FIR-18 AI extraction worker reads staged rows and writes to flood_events.

NOTE: If thoatnuochanoi.vn ever exposes a stable JSON/XHR endpoint, replace
_fetch_rows() with a direct requests.get() call — faster and more reliable
than full browser automation. Check the Network tab on page load to verify.
"""

import json
import logging
from datetime import datetime, timezone

from api.database import AsyncSessionLocal
from api.models.raw_flood_staging import RawFloodStaging

logger = logging.getLogger(__name__)

# Selector that proves the table has rendered (at least one data row present)
_TABLE_DATA_SELECTOR = "table tr:nth-child(2)"
_NAV_TIMEOUT_MS = 60_000
_RENDER_TIMEOUT_MS = 30_000


async def scrape_official(source_url: str) -> tuple[int, int]:
    """
    Render thoatnuochanoi.vn with Playwright, insert raw table rows into
    raw_flood_staging for FIR-18 processing.

    Returns: (records_inserted, 0)
    """
    rows = await _fetch_rows(source_url)
    if not rows:
        logger.warning("scrape_official: 0 rows fetched from %s", source_url)
        return 0, 0

    async with AsyncSessionLocal() as db:
        for row in rows:
            db.add(RawFloodStaging(
                content=json.dumps(row, ensure_ascii=False),
                source_type="governance",
                source_url=source_url,
                post_timestamp=datetime.now(timezone.utc),
            ))
        await db.commit()

    logger.info("scrape_official: staged %d rows from %s", len(rows), source_url)
    return len(rows), 0


async def _fetch_rows(url: str) -> list[dict]:
    """
    Use Playwright async API to render the page and extract raw table rows.
    Returns a list of dicts: {"cells": [str, ...], "url": str}.
    """
    from playwright.async_api import TimeoutError as PWTimeout
    from playwright.async_api import async_playwright

    rows: list[dict] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")

            try:
                await page.wait_for_selector(_TABLE_DATA_SELECTOR, timeout=_RENDER_TIMEOUT_MS)
            except PWTimeout:
                logger.error(
                    "scrape_official: timed out waiting for %r at %s — "
                    "JS may not have rendered the table; check for XHR API alternative",
                    _TABLE_DATA_SELECTOR,
                    url,
                )
                return rows

            table_rows = await page.query_selector_all("table tr")
            for i, tr in enumerate(table_rows[1:], start=1):  # skip header row
                cells = await tr.query_selector_all("td")
                if not cells:
                    continue
                texts = [await c.inner_text() for c in cells]
                rows.append({"cells": texts, "url": url})

        finally:
            await browser.close()

    return rows
