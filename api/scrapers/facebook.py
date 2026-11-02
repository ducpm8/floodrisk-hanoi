"""
FacebookCrawler: Layer 1 = Graph API (public groups, 200 req/hour),
                 Layer 2 = Playwright with persistent cookies.

Cookie sessions are stored in the crawler_sessions table (encrypted).
Admin alert fires when session expires or account is blocked.

FIR-45 additions:
  crawl_with_streaming() — visible-browser scroll + SSE log queue
  Post ID extraction uses JS page.evaluate() on live DOM (href patterns),
  not CSS class selectors which break whenever Facebook redeploys.
  After staging each post, navigates to its permalink in a new tab to
  extract comments (dedup via comment:{id} prefix in external_id).
  Both posts and comments are pre-filtered by contains_flood_keywords()
  before staging to avoid wasting Haiku budget on off-topic content.
  After staging completes, calls run_worker() inline (same thread, same DB
  connection) to run Haiku extraction on all newly staged rows.
  Emits extraction_start / extraction_done (or extraction_error) SSE events.
  Requires ANTHROPIC_API_KEY env var; set FLOOD_SCRAPER_MOCK_LLM=1 to skip.
"""

import json
import logging
import os
import queue
import re
from datetime import datetime, timezone
from typing import Any, Optional

import requests
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.scrapers.base import BaseCrawler, contains_flood_keywords

try:
    from cloakbrowser import launch as _cloak_launch  # type: ignore[import]
    _HAS_CLOAK = True
except ImportError:
    _cloak_launch = None  # type: ignore[assignment]
    _HAS_CLOAK = False

try:
    from playwright.sync_api import sync_playwright  # type: ignore[import]
except ImportError:
    sync_playwright = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"
GRAPH_API_RATE_LIMIT = 200  # requests/hour per token
SOURCE_NAME = "facebook"

# ---------------------------------------------------------------------------
# Shared JS timestamp helper — injected into both post and comment extractors.
#
# data-utime = Unix seconds set by Facebook on <abbr> elements (most stable).
# time[datetime] = ISO string fallback for newer FB layouts.
# ---------------------------------------------------------------------------

_JS_TIMESTAMP_FN = r"""
function getTimestamp(container) {
    const abbr = container.querySelector('abbr[data-utime]');
    if (abbr) return parseInt(abbr.getAttribute('data-utime'), 10) || null;
    const time = container.querySelector('time[datetime]');
    if (time) return time.getAttribute('datetime') || null;
    return null;
}
"""

# ---------------------------------------------------------------------------
# Post ID extraction — JS evaluated in live browser DOM (FIR-45)
#
# Why JS not BeautifulSoup: Facebook's HTML is server-side-rendered then
# hydrated; the DOM seen by page.content() may lag. page.evaluate() runs
# in the live rendered context and sees the actual post links.
#
# Patterns cover the four stable URL shapes Facebook has used since 2020:
#   /groups/{id}/posts/{post_id}
#   /groups/{id}/permalink/{post_id}
#   /permalink/{post_id}
#   ?story_fbid={post_id}  or  ?fbid={post_id}
#   /{username}/posts/{post_id}
#
# getPostText: prefers [data-ad-preview="message"] (desktop web) then
# [dir="auto"] divs with real text, falls back to cleaned innerText.
#
# permalink: clean post URL (origin+pathname) used for comment navigation.
# ---------------------------------------------------------------------------

_CLICK_READ_MORE_JS = r"""
() => {
    let clicked = 0;
    document.querySelectorAll('[role="button"], a, span').forEach(el => {
        const t = (el.innerText || el.textContent || '').trim();
        if (t === 'Xem thêm' || t === 'See more' || t === 'See More') {
            try { el.click(); clicked++; } catch(e) {}
        }
    });
    return clicked;
}
"""

_EXTRACT_POSTS_JS = "() => {\n" + _JS_TIMESTAMP_FN + r"""
    const patterns = [
        /\/groups\/[^/]+\/posts\/(\d{10,})/,
        /\/groups\/[^/]+\/permalink\/(\d{10,})/,
        /\/permalink\/(\d{10,})/,
        /[?&]story_fbid=(\d{10,})/,
        /[?&]set=pcb\.(\d{10,})/,   // photo album link → use post ID, not photo ID
        /[?&]fbid=(\d{10,})/,
        /\/[^/?]+\/posts\/(pfbid[A-Za-z0-9]+)/,  // modern obfuscated pfbid post URLs
        /\/[^/?]+\/posts\/(\d{10,})/,
        /\/share\/p\/([A-Za-z0-9]+)/,             // share URLs e.g. /share/p/14g927TGiJe/
    ];

    const results = [];
    const seenIds = new Set();
    const seenContainers = new WeakSet();

    function matchPostId(href) {
        for (const p of patterns) {
            const m = href.match(p);
            if (m) return m[1];
        }
        return null;
    }

    function getText(el) {
        return (el.innerText || el.textContent || '').trim();
    }

    function getPostText(article) {
        // Priority 1: desktop web post body element
        const msgEl = article.querySelector('[data-ad-preview="message"]');
        if (msgEl) {
            const t = (msgEl.innerText || '').trim();
            if (t.length > 10) return t.slice(0, 2000);
        }
        // Priority 2: longest [dir="auto"] element with real text.
        // First-match is wrong — short scrambled labels (author name, "Sponsored") appear
        // before the post body. Longest candidate is almost always the actual post body.
        let best = '';
        for (const el of article.querySelectorAll('[dir="auto"]')) {
            const t = (el.innerText || '').trim();
            if (t.length > best.length
                && !t.startsWith('Ảnh từ bài viết')
                && !/^[A-Za-z0-9+/\-_]{30,}$/.test(t.split('\n')[0].trim())) {
                best = t;
            }
        }
        if (best.length > 20) return best.slice(0, 2000);
        // Fallback: full innerText, strip junk lines
        const lines = (article.innerText || '').split('\n').filter(line => {
            const l = line.trim();
            if (l.length < 3) return false;
            if (l.startsWith('Ảnh từ bài viết')) return false;
            if (/^[A-Za-z0-9+/\-_]{30,}$/.test(l)) return false;
            if (l === 'Facebook') return false;
            if (/^(m\.me|fb\.com|facebook\.com)/.test(l)) return false;
            return true;
        });
        return lines.join('\n').slice(0, 2000);
    }

    // Prefer clean /posts/ or /permalink/ URLs over photo/fbid links.
    // Photo links strip to /photo/ when query params are removed — useless for
    // comment navigation. Walk ALL links in the container first.
    function getPermalink(container, fallbackHref) {
        const cleanPatterns = [
            /\/groups\/[^/?#]+\/posts\/[\dA-Za-z]{10,}/,
            /\/groups\/[^/?#]+\/permalink\/\d{10,}/,
            /\/[^/?#]+\/posts\/[\dA-Za-z]{10,}/,
        ];
        for (const a of container.querySelectorAll('a[href]')) {
            const href = a.href || '';
            for (const p of cleanPatterns) {
                if (p.test(href)) {
                    try { const u = new URL(href); return u.origin + u.pathname; } catch(e) {}
                }
            }
        }
        // Fallback: use full href of the matched link (keeps query params for fbid URLs)
        return fallbackHref || '';
    }

    // Strategy 1 (preferred): top-down from role="article".
    // Facebook wraps each post in a role="article" element — find those first,
    // then look for post links inside. This avoids the bottom-up nav collision.
    document.querySelectorAll('[role="article"]').forEach(article => {
        if (seenContainers.has(article)) return;
        for (const a of article.querySelectorAll('a[href]')) {
            const id = matchPostId(a.href || '');
            if (id && !seenIds.has(id)) {
                seenIds.add(id);
                seenContainers.add(article);
                results.push({ id, text: getPostText(article), timestamp: getTimestamp(article), permalink: getPermalink(article, a.href) });
                return;
            }
        }
    });

    // Strategy 2 (fallback): bottom-up walk for pages without role="article".
    // Skip links whose IDs were already captured by Strategy 1.
    document.querySelectorAll('a[href]').forEach(a => {
        const id = matchPostId(a.href || '');
        if (!id || seenIds.has(id)) return;
        seenIds.add(id);

        // Walk up: find a div with substantial text that doesn't open with "Facebook"
        // (the navbar repeats "Facebook" as its first word on every link).
        let el = a;
        let container = a;
        for (let i = 0; i < 20; i++) {
            if (!el.parentElement) break;
            el = el.parentElement;
            if (el.getAttribute && el.getAttribute('role') === 'article') {
                container = el;
                break;
            }
            if (el.tagName === 'DIV' && el.offsetHeight > 80) {
                const t = getText(el);
                const firstLine = t.split('\n')[0].trim();
                if (t.length > 50 && firstLine !== 'Facebook') {
                    container = el;
                    break;
                }
            }
        }
        if (seenContainers.has(container)) return;
        seenContainers.add(container);
        results.push({ id, text: getPostText(container), timestamp: getTimestamp(container), permalink: getPermalink(container, a.href) });
    });

    return results;
}
"""

# ---------------------------------------------------------------------------
# Comment extraction JS — runs on a post permalink page (new tab).
#
# Finds all <a href="...comment_id=N"> links, deduplicates by comment ID,
# walks up to the nearest role="article" for the comment text container.
# Returns [{id, text, timestamp}, ...].
# ---------------------------------------------------------------------------

_CLICK_LOAD_COMMENTS_JS = r"""
() => {
    let clicked = 0;
    document.querySelectorAll('[role="button"], a, span').forEach(el => {
        const t = (el.innerText || el.textContent || '').trim();
        if (
            t === 'Xem thêm bình luận' ||
            t === 'View more comments' ||
            (t.startsWith('Xem') && t.includes('bình luận'))
        ) {
            try { el.click(); clicked++; } catch(e) {}
        }
    });
    return clicked;
}
"""

_EXTRACT_COMMENTS_JS = "() => {\n" + _JS_TIMESTAMP_FN + r"""
    const results = [];
    const seenIds = new Set();

    document.querySelectorAll('a[href*="comment_id="]').forEach(a => {
        const match = (a.href || '').match(/[?&]comment_id=(\d+)/);
        if (!match) return;
        const id = match[1];
        if (seenIds.has(id)) return;
        seenIds.add(id);

        // Walk up to the nearest role="article" — the comment's own container.
        let el = a;
        let container = a;
        for (let i = 0; i < 15; i++) {
            if (!el.parentElement) break;
            el = el.parentElement;
            if (el.getAttribute && el.getAttribute('role') === 'article') {
                container = el;
                break;
            }
        }

        const text = (container.innerText || container.textContent || '').trim().slice(0, 1000);
        if (text.length < 10) return;

        results.push({ id, text, timestamp: getTimestamp(container) });
    });

    return results;
}
"""


class FacebookCrawler(BaseCrawler):
    """
    Fetches posts from Facebook groups for flood event extraction.

    Priority order:
      1. Graph API (public groups with page token)
      2. Playwright with persistent cookie session

    FIR-45: headless=False for admin visible-browser crawl.
    """

    def __init__(
        self,
        db_conn: Optional[Connection] = None,
        access_token: Optional[str] = None,
        headless: bool = True,
        cdp_endpoint: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.db_conn = db_conn
        self.access_token = access_token or os.environ.get("FACEBOOK_ACCESS_TOKEN", "")
        self.headless = headless
        self.cdp_endpoint = cdp_endpoint  # e.g. "http://localhost:9222"

    # ------------------------------------------------------------------
    # Original API (FIR-28) — unchanged
    # ------------------------------------------------------------------

    def fetch_group_posts(
        self,
        url: str,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """
        Fetch recent posts from a Facebook page/group URL.
        Returns list of {id, message, created_time} dicts.
        """
        if self.access_token:
            posts = self._fetch_via_graph_api(url, limit)
            if posts is not None:
                return posts
            logger.warning("Graph API failed for %s — trying Playwright", url)

        return self._fetch_via_playwright(url, limit)

    def _fetch_via_graph_api(
        self, group_id: str, limit: int
    ) -> Optional[list[dict[str, Any]]]:
        url = f"{GRAPH_API_BASE}/{group_id}/feed"
        try:
            resp = requests.get(
                url,
                params={
                    "access_token": self.access_token,
                    "fields": "id,message,created_time",
                    "limit": limit,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", [])
            if resp.status_code == 429:
                logger.warning("Graph API rate limit hit for group %s", group_id)
            else:
                logger.warning("Graph API HTTP %d for group %s", resp.status_code, group_id)
            return None
        except Exception as exc:
            logger.error("Graph API error for group %s: %s", group_id, exc)
            return None

    def _fetch_via_playwright(
        self, url: str, limit: int
    ) -> list[dict[str, Any]]:
        cookies = self._load_session_cookies()

        html = self._playwright_fetch_with_cookies(url, cookies)
        if html is None:
            logger.error("Playwright fetch failed for %s", url)
            return []

        posts = self._parse_group_html(html, limit)
        if not posts and cookies:
            self._mark_session_expired()
            self._alert_admin(f"Facebook session expired for {url}")

        return posts

    def _playwright_fetch_with_cookies(
        self, url: str, cookies: list[dict]
    ) -> Optional[str]:
        if not _HAS_CLOAK and sync_playwright is None:
            logger.error("neither cloakbrowser nor playwright installed")
            return None

        _pw_instance = None
        try:
            if _HAS_CLOAK:
                browser = _cloak_launch(headless=self.headless, humanize=True)
            else:
                _pw_instance = sync_playwright().start()
                browser = _pw_instance.chromium.launch(headless=self.headless)

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
            logger.error("browser Facebook fetch failed: %s", exc)
            return None
        finally:
            if _pw_instance:
                _pw_instance.stop()

    def _parse_group_html(self, html: str, limit: int) -> list[dict[str, Any]]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        posts: list[dict[str, Any]] = []

        for el in soup.select("[data-testid='post_message']")[:limit]:
            text_content = el.get_text(separator=" ", strip=True)
            if text_content:
                posts.append({"id": None, "message": text_content, "created_time": None})

        return posts

    # ------------------------------------------------------------------
    # FIR-45: visible-browser streaming crawl
    # ------------------------------------------------------------------

    def crawl_with_streaming(
        self,
        url: str,
        max_posts: int,
        log_queue: "queue.Queue[dict | None]",
        max_comments_per_post: int = 10,
    ) -> None:
        """
        Open a visible Playwright browser, scroll the Facebook page, stage
        up to max_posts new posts (dedup via raw_flood_staging.external_id).
        For each staged post, open a new tab to extract comments (dedup via
        comment:{id} prefix). Both posts and comments are pre-filtered by
        contains_flood_keywords() before staging.
        Always puts the None sentinel on log_queue before returning.

        max_comments_per_post: max 'Xem thêm bình luận' clicks per post.
          0 disables comment extraction entirely.

        Runs synchronously — call via asyncio.to_thread from async context.
        """
        staged = 0
        comments_staged = 0
        skipped = 0
        try:
            using_cdp = bool(self.cdp_endpoint)

            if using_cdp and sync_playwright is None:
                log_queue.put({"type": "error", "message": "playwright not installed (required for CDP mode) — run: pip install playwright && playwright install chromium"})
                return
            if not using_cdp and not _HAS_CLOAK:
                log_queue.put({"type": "error", "message": "cloakbrowser not installed — run: pip install cloakbrowser"})
                return

            if not using_cdp:
                cookies = self._load_session_cookies()
                if not cookies:
                    log_queue.put({
                        "type": "error",
                        "message": "no Facebook session in crawler_sessions — complete FIR-28 auth flow first, or pass cdp_url to reuse existing Chrome session",
                    })
                    return

            log_queue.put({"type": "start", "url": url, "max_posts": max_posts, "mode": "cdp" if using_cdp else "cookies"})

            seen_in_session: set[str] = set()

            _pw_instance = None
            if using_cdp:
                log_queue.put({"type": "cdp_connecting", "endpoint": self.cdp_endpoint})
                _pw_instance = sync_playwright().start()
                browser = _pw_instance.chromium.connect_over_cdp(self.cdp_endpoint)
                ctx = browser.contexts[0]  # reuse existing context — has live Facebook session
                page = ctx.new_page()
            else:
                cookies = self._load_session_cookies()
                browser = _cloak_launch(headless=self.headless, humanize=True)
                ctx = browser.new_context(user_agent=self.session.headers["User-Agent"])
                ctx.add_cookies(cookies)
                page = ctx.new_page()

            log_queue.put({"type": "navigating", "url": url})
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(6000)
            if not using_cdp:
                self._save_session_cookies(ctx.cookies())
            log_queue.put({"type": "browser_ready"})

            scroll_n = 0
            stale_scrolls = 0  # consecutive scrolls with zero new DOM posts
            _STALE_LIMIT = 3   # give up after 3 dry scrolls
            _MAX_SCROLLS = max(8, max_posts * 3)
            while True:
                if staged >= max_posts:
                    break
                if scroll_n >= _MAX_SCROLLS:
                    log_queue.put({"type": "warn", "message": f"scroll cap {_MAX_SCROLLS} reached — stopping"})
                    break

                clicked = page.evaluate(_CLICK_READ_MORE_JS)
                if clicked:
                    page.wait_for_timeout(800)

                posts: list[dict] = page.evaluate(_EXTRACT_POSTS_JS)
                new_posts = [p for p in posts if p["id"] not in seen_in_session]
                seen_in_session.update(p["id"] for p in posts)

                if not new_posts:
                    stale_scrolls += 1
                    if stale_scrolls >= _STALE_LIMIT:
                        log_queue.put({"type": "warn", "message": f"no new posts in DOM after {_STALE_LIMIT} scrolls — page exhausted"})
                        break
                else:
                    stale_scrolls = 0

                for post in new_posts:
                    if staged >= max_posts:
                        break
                    post_id = post["id"]
                    post_text = post.get("text", "")

                    # DOM text empty or scrambled — screenshot the post element and
                    # use Haiku vision to read the rendered text (bypasses obfuscation).
                    if not post_text or self._is_scrambled(post_text):
                        post_text = self._vision_read_post(page, post_id, log_queue)

                    if post_text and not contains_flood_keywords(post_text):
                        skipped += 1
                        log_queue.put({"type": "post_skipped", "post_id": post_id, "reason": "no_flood_keywords", "text_preview": post_text[:500]})
                        continue

                    if self._is_already_staged(post_id):
                        skipped += 1
                        log_queue.put({"type": "post_skipped", "post_id": post_id, "reason": "duplicate"})
                        continue

                    self._stage_item(post_id, post_text, url, post.get("timestamp"))
                    staged += 1
                    log_queue.put({
                        "type": "post_staged",
                        "post_id": post_id,
                        "preview": post_text[:80],
                        "staged_so_far": staged,
                    })

                    # Comment extraction disabled — opens post permalink in new tab per post,
                    # causes browser instability and slow crawls; re-enable when stable.
                    # permalink = post.get("permalink")
                    # if permalink and max_comments_per_post > 0:
                    #     comment_page = None
                    #     try:
                    #         comment_page = ctx.new_page()
                    #         try:
                    #             comment_page.goto(permalink, wait_until="domcontentloaded", timeout=20_000)
                    #         except Exception as nav_exc:
                    #             log_queue.put({"type": "warn", "message": f"comment nav failed for {post_id}: {nav_exc}"})
                    #             raise
                    #         comment_page.wait_for_timeout(2000)
                    #         for _ in range(max_comments_per_post):
                    #             clicked_c = comment_page.evaluate(_CLICK_LOAD_COMMENTS_JS)
                    #             if not clicked_c:
                    #                 break
                    #             comment_page.wait_for_timeout(800)
                    #         raw_comments: list[dict] = comment_page.evaluate(_EXTRACT_COMMENTS_JS)
                    #         for comment in raw_comments:
                    #             comment_id = comment.get("id")
                    #             if not comment_id:
                    #                 continue
                    #             comment_text = comment.get("text", "")
                    #             if not contains_flood_keywords(comment_text):
                    #                 log_queue.put({"type": "comment_skipped", "comment_id": comment_id, "reason": "no_flood_keywords"})
                    #                 continue
                    #             comment_ext_id = f"comment:{comment_id}"
                    #             if self._is_already_staged(comment_ext_id):
                    #                 log_queue.put({"type": "comment_skipped", "comment_id": comment_id, "reason": "duplicate"})
                    #                 continue
                    #             comment_url = f"{permalink}?comment_id={comment_id}"
                    #             self._stage_item(comment_ext_id, comment_text, comment_url, comment.get("timestamp"))
                    #             comments_staged += 1
                    #             log_queue.put({"type": "comment_staged", "comment_id": comment_id, "post_id": post_id})
                    #     except Exception as comment_exc:
                    #         log_queue.put({"type": "warn", "message": f"comment extraction error for {post_id}: {comment_exc}"})
                    #     finally:
                    #         if comment_page:
                    #             try:
                    #                 comment_page.close()
                    #             except Exception:
                    #                 pass

                if staged >= max_posts:
                    break

                # Scroll to absolute bottom so Facebook's infinite-scroll threshold
                # triggers reliably. scrollBy(one viewport) often misses it.
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(6000)
                scroll_n += 1
                log_queue.put({"type": "scroll", "n": scroll_n, "staged_so_far": staged})

            page.close()
            if not using_cdp:
                browser.close()  # don't close user's browser in CDP mode
            if _pw_instance:
                _pw_instance.stop()

            log_queue.put({"type": "done", "staged": staged, "comments_staged": comments_staged, "skipped": skipped})

            total_staged = staged + comments_staged
            if self.db_conn and total_staged > 0:
                log_queue.put({"type": "extraction_start", "posts": total_staged})
                try:
                    from api.scrapers.ai_extraction_worker import run_worker
                    added, errs = run_worker(self.db_conn, batch_size=total_staged)
                    log_queue.put({
                        "type": "extraction_done",
                        "flood_events_added": added,
                        "errors": errs[:3] if errs else [],
                    })
                except Exception as extraction_exc:
                    log_queue.put({"type": "extraction_error", "message": str(extraction_exc)})

        except Exception as exc:
            log_queue.put({"type": "error", "message": str(exc)})
        finally:
            log_queue.put(None)  # sentinel — always sent so SSE generator terminates

    @staticmethod
    def _is_scrambled(text: str) -> bool:
        """True when Facebook DOM obfuscation is detected (high digit ratio or no Vietnamese/CJK chars in short text)."""
        if not text:
            return True
        digit_count = sum(1 for c in text if c.isdigit())
        if digit_count / len(text) > 0.15:
            return True
        # Short text with no Vietnamese diacritics and no spaces — likely scrambled
        if len(text) < 80 and ' ' not in text.strip():
            return True
        return False

    def _vision_read_post(self, page: Any, post_id: str, log_queue: "queue.Queue") -> str:
        """Screenshot the rendered post element and use Haiku vision to read its text.
        Called when DOM text extraction returns empty (Facebook obfuscation).
        Returns extracted text, or empty string on failure.
        """
        import base64
        try:
            bb = page.evaluate(f"""
                () => {{
                    for (const a of document.querySelectorAll('a[href]')) {{
                        if ((a.href || '').includes('{post_id}')) {{
                            let el = a;
                            for (let i = 0; i < 20; i++) {{
                                if (!el.parentElement) break;
                                el = el.parentElement;
                                const r = el.getBoundingClientRect();
                                if (r.height > 80 && r.width > 200) {{
                                    return {{ x: r.left, y: r.top, width: r.width, height: Math.min(r.height, 500) }};
                                }}
                            }}
                        }}
                    }}
                    return null;
                }}
            """)
            if not bb or bb["width"] < 10:
                return ""

            vp = page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
            vw, vh = vp.get("w", 1280), vp.get("h", 800)
            x = max(0, min(bb["x"], vw - 1))
            y = max(0, min(bb["y"], vh - 1))
            w = max(1, min(bb["width"], vw - x, 1200))
            h = max(1, min(bb["height"], vh - y, 500))
            if w < 10 or h < 10:
                return ""
            screenshot = page.screenshot(clip={"x": x, "y": y, "width": w, "height": h})

            from anthropic import Anthropic
            from api.config import settings
            client = Anthropic(api_key=settings.anthropic_api_key or None)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(screenshot).decode()},
                        },
                        {
                            "type": "text",
                            "text": "Return the text content of this Facebook post verbatim in its original language. Return only the post text, nothing else. If no clear post text is visible, return empty string.",
                        },
                    ],
                }],
            )
            extracted = resp.content[0].text.strip()
            if extracted:
                log_queue.put({"type": "vision_extracted", "post_id": post_id, "text_preview": extracted[:120]})
            return extracted
        except Exception as exc:
            log_queue.put({"type": "warn", "message": f"vision fallback failed for {post_id}: {exc}"})
            return ""

    def _is_already_staged(self, external_id: str) -> bool:
        if not self.db_conn:
            return False
        row = self.db_conn.execute(
            text("SELECT 1 FROM raw_flood_staging WHERE external_id = :eid LIMIT 1"),
            {"eid": external_id},
        ).fetchone()
        return row is not None

    def _stage_item(self, external_id: str, content: str, source_url: str, raw_timestamp: int | str | None = None) -> None:
        if not self.db_conn:
            return
        post_ts = None
        if isinstance(raw_timestamp, int):
            post_ts = datetime.fromtimestamp(raw_timestamp, tz=timezone.utc)
        elif isinstance(raw_timestamp, str):
            try:
                post_ts = datetime.fromisoformat(raw_timestamp)
            except ValueError:
                pass
        self.db_conn.execute(
            text("""
                INSERT INTO raw_flood_staging
                    (id, content, source_type, source_url, external_id, processed, post_timestamp, created_at)
                VALUES
                    (gen_random_uuid(), :content, 'social_media', :source_url,
                     :external_id, FALSE, :post_timestamp, NOW())
            """),
            {"content": content, "source_url": source_url, "external_id": external_id, "post_timestamp": post_ts},
        )
        self.db_conn.commit()

    # ------------------------------------------------------------------
    # Session helpers (shared by both paths)
    # ------------------------------------------------------------------

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
            logger.warning("Failed to load Facebook session: %s", exc)
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
            logger.warning("Failed to save Facebook session: %s", exc)

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
            logger.warning("Failed to mark Facebook session expired: %s", exc)

    def _alert_admin(self, message: str) -> None:
        logger.error("ADMIN ALERT: %s", message)
        # TODO: wire to email/Slack notification when alert system is in place (FIR-19+)
