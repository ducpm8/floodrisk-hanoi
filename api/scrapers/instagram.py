"""
InstagramCrawler: Basic Display API → Playwright fallback.

Session cookies stored in crawler_sessions table.
"""

import json
import logging
import os
from typing import Any, Optional

import requests
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.scrapers.base import BaseCrawler

logger = logging.getLogger(__name__)

BASIC_DISPLAY_API_BASE = "https://graph.instagram.com"
SOURCE_NAME = "instagram"


class InstagramCrawler(BaseCrawler):
    """
    Fetches posts from Instagram accounts/hashtags for flood event extraction.

    Priority order:
      1. Basic Display API (with user access token)
      2. Playwright with persistent cookie session
    """

    def __init__(
        self,
        db_conn: Optional[Connection] = None,
        access_token: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.db_conn = db_conn
        self.access_token = access_token or os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")

    def fetch_user_media(
        self,
        user_id: str = "me",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Fetch recent media from an Instagram user.
        Returns list of {id, caption, timestamp} dicts.
        """
        if self.access_token:
            posts = self._fetch_via_api(user_id, limit)
            if posts is not None:
                return posts
            logger.warning("Basic Display API failed for user %s — trying Playwright", user_id)

        return self._fetch_via_playwright(user_id, limit)

    def _fetch_via_api(
        self, user_id: str, limit: int
    ) -> Optional[list[dict[str, Any]]]:
        url = f"{BASIC_DISPLAY_API_BASE}/{user_id}/media"
        try:
            resp = requests.get(
                url,
                params={
                    "access_token": self.access_token,
                    "fields": "id,caption,timestamp",
                    "limit": limit,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", [])
            logger.warning("Basic Display API HTTP %d for user %s", resp.status_code, user_id)
            return None
        except Exception as exc:
            logger.error("Basic Display API error: %s", exc)
            return None

    def _fetch_via_playwright(
        self, user_id: str, limit: int
    ) -> list[dict[str, Any]]:
        url = f"https://www.instagram.com/{user_id}/"
        cookies = self._load_session_cookies()

        html = self._playwright_fetch_with_cookies(url, cookies)
        if html is None:
            logger.error("Playwright fetch failed for Instagram user %s", user_id)
            return []

        posts = self._parse_profile_html(html, limit)
        if not posts and cookies:
            self._mark_session_expired()
            self._alert_admin(f"Instagram session expired for user {user_id}")

        return posts

    def _playwright_fetch_with_cookies(
        self, url: str, cookies: list[dict]
    ) -> Optional[str]:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import]
        except ImportError:
            logger.error("playwright not installed")
            return None

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(user_agent=self.session.headers["User-Agent"])
                if cookies:
                    ctx.add_cookies(cookies)
                page = ctx.new_page()
                self._polite_delay()
                page.goto(url, wait_until="networkidle", timeout=30_000)
                html = page.content()

                new_cookies = ctx.cookies()
                self._save_session_cookies(new_cookies)

                browser.close()
                return html
        except Exception as exc:
            logger.error("Playwright Instagram fetch failed: %s", exc)
            return None

    def _parse_profile_html(self, html: str, limit: int) -> list[dict[str, Any]]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        posts: list[dict[str, Any]] = []

        for meta in soup.select("meta[property='og:description']")[:limit]:
            content = meta.get("content", "")
            if content:
                posts.append({"id": None, "caption": content, "timestamp": None})

        return posts

    def _load_session_cookies(self) -> list[dict]:
        if not self.db_conn:
            return []
        try:
            row = self.db_conn.execute(
                text(
                    "SELECT cookies_encrypted FROM crawler_sessions "
                    "WHERE source_name = :src AND status = 'active'"
                ),
                {"src": SOURCE_NAME},
            ).fetchone()
            if row and row[0]:
                return json.loads(row[0])
        except Exception as exc:
            logger.warning("Failed to load Instagram session: %s", exc)
        return []

    def _save_session_cookies(self, cookies: list[dict]) -> None:
        if not self.db_conn:
            return
        try:
            self.db_conn.execute(
                text("""
                    INSERT INTO crawler_sessions (source_name, cookies_encrypted, last_valid_at, status)
                    VALUES (:src, :cookies, NOW(), 'active')
                    ON CONFLICT (source_name) DO UPDATE SET
                        cookies_encrypted = EXCLUDED.cookies_encrypted,
                        last_valid_at = NOW(),
                        status = 'active',
                        updated_at = NOW()
                """),
                {"src": SOURCE_NAME, "cookies": json.dumps(cookies)},
            )
        except Exception as exc:
            logger.warning("Failed to save Instagram session: %s", exc)

    def _mark_session_expired(self) -> None:
        if not self.db_conn:
            return
        try:
            self.db_conn.execute(
                text(
                    "UPDATE crawler_sessions SET status = 'expired', updated_at = NOW() "
                    "WHERE source_name = :src"
                ),
                {"src": SOURCE_NAME},
            )
        except Exception as exc:
            logger.warning("Failed to mark Instagram session expired: %s", exc)

    def _alert_admin(self, message: str) -> None:
        logger.error("ADMIN ALERT: %s", message)
