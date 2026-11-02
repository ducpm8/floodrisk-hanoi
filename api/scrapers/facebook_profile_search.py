"""
FIR-51: FacebookProfileSearchCrawler

Crawls flood-related posts from a Facebook profile search results page:
    /profile/{id}/search/?q=ngập

Inherits cookie loading, browser launch, and staging helpers from FacebookCrawler.
Extraction is written fresh — does NOT reuse FIR-45 JS extractors.

Strategy: one page.evaluate() call per scroll cycle extracts [{href, text}] for
all visible articles in a single browser IPC round trip.
"""

import hashlib
import logging
import queue
import re
from typing import Optional

from api.scrapers.base import contains_flood_keywords
from api.scrapers.facebook import FacebookCrawler

try:
    from playwright.sync_api import sync_playwright  # type: ignore[import]
except ImportError:
    sync_playwright = None  # type: ignore[assignment]

try:
    from cloakbrowser import launch as _cloak_launch  # type: ignore[import]
except ImportError:
    _cloak_launch = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Matches all stable Facebook post ID shapes since 2020.
# Profile search results dominant patterns: set=pcb.{ID} (photos) and /videos/pcb.{ID}/ (videos).
_POST_ID_PATTERNS = [
    re.compile(r"/posts/(pfbid[A-Za-z0-9]+)"),
    re.compile(r"/posts/(\d{10,})"),
    re.compile(r"/permalink/(\d{10,})"),
    re.compile(r"[?&]story_fbid=(\d{10,})"),
    re.compile(r"[?&]set=pcb\.(\d{10,})"),        # photo album links
    re.compile(r"/videos/pcb\.(\d{10,})/"),        # video links on profile search
    re.compile(r"[?&]fbid=(\d{10,})"),
    re.compile(r"/share/p/([A-Za-z0-9]+)"),
]

# Facebook shows end-of-results in either locale depending on account language.
_NO_MORE_RESULTS_TEXTS = [
    "Không tìm thấy thêm kết quả",
    "Không có thêm kết quả",
    "Không có kết quả",
    "No more results",
    "No results found",
]

# Clicks every "See more" / "Xem thêm" expander in the feed. Run before _BATCH_EXTRACT_JS
# so the long-form post bodies are visible in innerText.
_EXPAND_SEE_MORE_JS = """
() => {
    const feed = document.querySelector('[role="feed"]') || document.body;
    const targets = feed.querySelectorAll('[role="button"], [tabindex="0"]');
    let clicked = 0;
    targets.forEach(b => {
        const t = (b.innerText || '').trim();
        if (t === 'Xem thêm' || t === 'See more' || t === 'See More') {
            try { b.click(); clicked++; } catch (e) {}
        }
    });
    return clicked;
}
"""


# Single evaluate() call — extracts [{href, text}] for all articles in one IPC round trip.
#
# Facebook profile search innerText layout (verified via DOM dump 2026-05-11):
#   [N x "Facebook\n" decoy lines]
#   [author display name]
#   [obfuscated metadata: single-char lines spelling a timestamp]
#   "·"  <- bullet separator on its own line
#   [actual post body in clean Vietnamese]
#   [reactions/comments tail]
#
# Split on "\n·\n" and keep the body. No vision OCR needed.
_BATCH_EXTRACT_JS = """
() => {
    function getTimestamp(container) {
        // data-utime = Unix seconds on <abbr> (older FB), still appears on some posts.
        const abbr = container.querySelector('abbr[data-utime]');
        if (abbr) {
            const u = parseInt(abbr.getAttribute('data-utime'), 10);
            if (u) return u;
        }
        // time[datetime] = ISO string on newer FB layouts.
        const t = container.querySelector('time[datetime]');
        if (t) return t.getAttribute('datetime') || null;
        return null;
    }

    // Profile search uses feed children (plain divs, no role=article).
    // Fall back to role=article for group/page feeds.
    const feedChildren = Array.from(document.querySelectorAll('[role="feed"] > *'));
    const articles = Array.from(document.querySelectorAll('[role="article"]'));
    const source = feedChildren.length > 0 ? feedChildren : articles;

    const seen = new Set();
    const results = [];

    source.forEach(container => {
        // Skip virtualized placeholders (no innerText, no children).
        const rawText = (container.innerText || '').trim();
        if (!rawText || rawText.length < 20) return;

        // Prefer canonical post links; fall back to ANY photo/profile link so
        // pure text posts (no photo album) aren't dropped.
        const preferred = container.querySelector(
            'a[href*="/posts/"], a[href*="story_fbid"], a[href*="permalink"], a[href*="/share/p/"], a[href*="set=pcb."], a[href*="/videos/pcb."]'
        );
        const fallback = container.querySelector(
            'a[href*="/photo/"], a[href*="fbid="], a[href*="/videos/"]'
        );
        const linkEl = preferred || fallback;
        const href = linkEl ? (linkEl.href || '') : '';

        // Deduplicate by href prefix when we have one, else by text fingerprint.
        const key = href ? href.split('&')[0] : ('text:' + rawText.slice(0, 120));
        if (seen.has(key)) return;
        seen.add(key);

        const raw = rawText;

        // Strip "Facebook" decoy lines and the obfuscated metadata block by
        // splitting on the bullet separator. The body is everything after.
        let body = raw;
        const sepIdx = raw.indexOf('\\n·\\n');
        if (sepIdx > -1) {
            body = raw.slice(sepIdx + 3).trim();
        } else {
            // Fallback: drop "Facebook" lines and single-character lines.
            body = raw.split('\\n')
                .filter(l => l.trim() !== 'Facebook' && l.trim().length > 2)
                .join('\\n')
                .trim();
        }

        // Trim trailing "Facebook" decoy block + engagement counters tacked
        // onto the end of innerText (reactions/comments/share numbers like
        // "321\\n66\\n2" and "+4" image-count badges).
        const bodyLines = body.split('\\n');
        const cleaned = [];
        for (const line of bodyLines) {
            const t = line.trim();
            if (t === 'Facebook') break;            // hit trailing decoy block
            if (/^\\+\\d+$/.test(t)) break;          // image count badge
            if (/^[\\d,.KMkm]+$/.test(t) && t.length <= 6) break;  // reaction count
            if (t === '0:00 / 0:00' || /^\\d+:\\d+ \\/ \\d+:\\d+$/.test(t)) continue; // video timer
            cleaned.push(line);
        }
        body = cleaned.join('\\n').trim();

        // Author name often sits as a clean line *before* the bullet — pull it
        // back in as a "Posted by" prefix so the LLM has source context.
        let author = '';
        if (sepIdx > -1) {
            const preLines = raw.slice(0, sepIdx).split('\\n')
                .map(s => s.trim())
                .filter(s => s && s !== 'Facebook' && s.length > 2);
            if (preLines.length > 0) author = preLines[preLines.length - 1];
        }

        const text = (author ? author + '\\n' : '') + body;
        results.push({
            href,
            text: text.slice(0, 2000),
            author,
            body: body.slice(0, 2000),
            timestamp: getTimestamp(container),
        });
    });

    return results;
}
"""


def _extract_post_id(href: str) -> Optional[str]:
    """Return the first matching post ID from a Facebook post href, or None."""
    for pattern in _POST_ID_PATTERNS:
        m = pattern.search(href)
        if m:
            return m.group(1)
    return None


def _page_has_no_more_results(page_text: str) -> bool:
    """True if the page contains an end-of-results indicator."""
    low = page_text.lower()
    return any(phrase.lower() in low for phrase in _NO_MORE_RESULTS_TEXTS)


class FacebookProfileSearchCrawler(FacebookCrawler):
    """
    Crawls posts from a Facebook profile search results URL.

    Inherits from FacebookCrawler for:
      - _load_session_cookies / _save_session_cookies / _mark_session_expired
      - headless=False browser launch and CDP connect mode
      - _stage_item / _is_already_staged

    Does NOT use any extraction JS from FacebookCrawler (FIR-45). The
    `\\n·\\n` body split in _BATCH_EXTRACT_JS yields clean Vietnamese text
    directly from the DOM, so no screenshot / vision OCR fallback is needed.
    """

    def crawl_profile_search_streaming(
        self,
        url: str,
        max_posts: int,
        log_queue: "queue.Queue[dict | None]",
    ) -> None:
        """
        Open a visible Playwright browser, scroll a Facebook profile search
        results page, and stage up to max_posts flood-related posts.

        SSE event types emitted:
          start | navigating | browser_ready | scroll |
          post_staged | post_skipped | warn | error |
          done | extraction_start | extraction_done | extraction_error

        Always puts the None sentinel on log_queue before returning.
        Runs synchronously — call via asyncio.to_thread from async context.
        """
        staged = 0
        skipped = 0
        try:
            if sync_playwright is None:
                log_queue.put({
                    "type": "error",
                    "message": "playwright not installed — run: pip install playwright && playwright install chromium",
                })
                return

            using_cdp = bool(self.cdp_endpoint)

            if not using_cdp:
                cookies = self._load_session_cookies()
                if not cookies:
                    log_queue.put({
                        "type": "error",
                        "message": "no Facebook session in crawler_sessions — complete auth flow first, or pass cdp_url",
                    })
                    return

            log_queue.put({"type": "start", "url": url, "max_posts": max_posts, "mode": "cdp" if using_cdp else "cookies"})

            seen_ids: set[str] = set()

            with sync_playwright() as pw:
                if using_cdp:
                    log_queue.put({"type": "cdp_connecting", "endpoint": self.cdp_endpoint})
                    browser = pw.chromium.connect_over_cdp(self.cdp_endpoint)
                    ctx = browser.contexts[0]
                    page = ctx.new_page()
                else:
                    cookies = self._load_session_cookies()
                    browser = pw.chromium.launch(headless=self.headless)
                    ctx = browser.new_context(user_agent=self.session.headers["User-Agent"])
                    ctx.add_cookies(cookies)
                    page = ctx.new_page()

                log_queue.put({"type": "navigating", "url": url})
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(4000)

                # Detect login wall — expired cookies cause a redirect to /login/
                if "/login" in page.url:
                    self._mark_session_expired()
                    log_queue.put({
                        "type": "error",
                        "message": "Facebook session expired — redirected to login page. Re-authenticate via /admin/facebook-auth.",
                    })
                    page.close()
                    if not using_cdp:
                        browser.close()
                    return

                if not using_cdp:
                    self._save_session_cookies(ctx.cookies())

                log_queue.put({"type": "browser_ready"})

                # Wait for feed children to appear before starting scroll loop.
                try:
                    page.wait_for_selector('[role="feed"] > *', timeout=10_000)
                except Exception:
                    log_queue.put({"type": "warn", "message": "no feed items found after 10s — page may be empty or blocked"})

                scroll_n = 0
                stale_scrolls = 0
                _STALE_LIMIT = 2
                _MAX_SCROLLS = max(5, max_posts)  # cap scrolling — caller-controlled via max_posts

                while staged < max_posts and scroll_n < _MAX_SCROLLS:
                    # Click all "Xem thêm" / "See more" expanders so long posts are fully visible.
                    expanded = page.evaluate(_EXPAND_SEE_MORE_JS)
                    if expanded:
                        log_queue.put({"type": "expanded", "count": expanded})
                        page.wait_for_timeout(1200)

                    items: list[dict] = page.evaluate(_BATCH_EXTRACT_JS)

                    new_count = 0
                    for item in items:
                        if staged >= max_posts:
                            break

                        href = item.get("href", "")
                        post_id = _extract_post_id(href) if href else None
                        if not post_id:
                            # Pure text post or unrecognized URL shape — fingerprint by content.
                            body_text = (item.get("body") or item.get("text") or "").strip()
                            if not body_text:
                                continue
                            post_id = "txt_" + hashlib.sha1(body_text[:200].encode("utf-8")).hexdigest()[:16]
                        if post_id in seen_ids:
                            continue

                        seen_ids.add(post_id)
                        new_count += 1
                        post_text = item.get("text", "")

                        # Pre-filter before Haiku extraction (matches FIR-45 FacebookCrawler).
                        # Even though the URL is a flood query, ranking sometimes surfaces
                        # off-topic posts (promotional, replies, generic announcements).
                        # Skipping them here saves Haiku budget.
                        if post_text and not contains_flood_keywords(post_text):
                            skipped += 1
                            log_queue.put({
                                "type": "post_skipped",
                                "post_id": post_id,
                                "reason": "no_flood_keywords",
                                "text_preview": post_text[:200],
                            })
                            continue

                        if self._is_already_staged(post_id):
                            skipped += 1
                            log_queue.put({"type": "post_skipped", "post_id": post_id, "reason": "duplicate"})
                            continue

                        post_url = item.get("href") or url
                        self._stage_item(post_id, post_text, post_url, item.get("timestamp"))
                        staged += 1
                        log_queue.put({
                            "type": "post_staged",
                            "post_id": post_id,
                            "preview": post_text[:80],
                            "staged_so_far": staged,
                        })

                    if new_count == 0:
                        stale_scrolls += 1
                        if stale_scrolls >= _STALE_LIMIT:
                            log_queue.put({"type": "warn", "message": f"no new posts after {_STALE_LIMIT} consecutive scrolls — page exhausted"})
                            break
                    else:
                        stale_scrolls = 0

                    # Check for end-of-results indicator before next scroll.
                    try:
                        body_text = page.locator("body").inner_text(timeout=2000)
                        if _page_has_no_more_results(body_text):
                            log_queue.put({"type": "warn", "message": "no more search results — stopping"})
                            break
                    except Exception:
                        pass

                    if staged >= max_posts:
                        break

                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(4000)
                    scroll_n += 1
                    log_queue.put({"type": "scroll", "n": scroll_n, "staged_so_far": staged})

                page.close()
                if not using_cdp:
                    browser.close()

            log_queue.put({"type": "done", "staged": staged, "skipped": skipped})

            if self.db_conn and staged > 0:
                log_queue.put({"type": "extraction_start", "posts": staged})
                try:
                    from api.scrapers.ai_extraction_worker import run_worker
                    added, errs = run_worker(self.db_conn, batch_size=staged)
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
