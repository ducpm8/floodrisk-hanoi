"""
FIR-45 live acceptance test.
Navigates to a Facebook URL and evaluates _EXTRACT_POSTS_JS in the live DOM.

Two modes:
  --cdp-url   Connect to an existing logged-in Chrome (recommended).
              Launch Chrome first:
                chrome.exe --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\\ChromeDebug"
              Log into Facebook once, then run this script.
  (default)   Launch a fresh Chromium (will hit Facebook login wall).

Usage:
    python scripts/fir45_live_test.py <facebook_url> [--cdp-url http://localhost:9222]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.scrapers.facebook import _EXTRACT_POSTS_JS, sync_playwright


def run_live_test(url: str, max_scrolls: int = 3, cdp_url: str | None = None) -> dict:
    if sync_playwright is None:
        return {"error": "playwright not installed — pip install playwright && playwright install chromium"}

    result = {
        "url": url,
        "mode": "cdp" if cdp_url else "fresh",
        "posts_found": [],
        "login_wall": False,
        "page_title": "",
        "scrolls_done": 0,
        "error": None,
    }

    try:
        with sync_playwright() as pw:
            if cdp_url:
                print(f"[browser] connecting via CDP to {cdp_url} ...")
                browser = pw.chromium.connect_over_cdp(cdp_url)
                ctx = browser.contexts[0]
                page = ctx.new_page()
            else:
                print("[browser] launching fresh chromium (headless=False) ...")
                browser = pw.chromium.launch(headless=False)
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                )
                page = ctx.new_page()

            print(f"[browser] navigating to {url}")
            try:
                page.goto(url, wait_until="networkidle", timeout=30_000)
            except Exception:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)

            result["page_title"] = page.title()
            print(f"[browser] page title: {result['page_title']}")

            title_lower = result["page_title"].lower()
            login_indicators = ["log in", "login", "sign in", "đăng nhập"]
            if any(kw in title_lower for kw in login_indicators) or result["page_title"] in ("Facebook", ""):
                result["login_wall"] = True
                if not cdp_url:
                    print("[browser] LOGIN WALL — Facebook requires auth. Re-run with --cdp-url to use your existing session.")
                else:
                    print("[browser] WARNING: possible login wall even with CDP. Check if you're logged in in the Chrome window.")

            seen_ids: set[str] = set()

            for scroll_n in range(max_scrolls):
                print(f"[scroll {scroll_n + 1}/{max_scrolls}] evaluating _EXTRACT_POSTS_JS ...")
                posts: list[dict] = page.evaluate(_EXTRACT_POSTS_JS)
                new_posts = [p for p in posts if p["id"] not in seen_ids]
                seen_ids.update(p["id"] for p in posts)

                for post in new_posts:
                    result["posts_found"].append({
                        "id": post["id"],
                        "preview": (post.get("text") or "")[:120],
                    })
                    print(f"  POST FOUND: id={post['id']}  preview={repr((post.get('text') or '')[:80])}")

                print(f"  total unique posts so far: {len(result['posts_found'])}")

                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(2000)
                result["scrolls_done"] = scroll_n + 1

            page.close()
            if not cdp_url:
                browser.close()
            else:
                print("[browser] CDP session left open (your Chrome is still running)")

    except Exception as exc:
        result["error"] = str(exc)
        print(f"[ERROR] {exc}")

    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    url = None
    cdp_url = None

    i = 0
    while i < len(args):
        if args[i] == "--cdp-url" and i + 1 < len(args):
            cdp_url = args[i + 1]
            i += 2
        else:
            url = args[i]
            i += 1

    if not url:
        url = "https://www.facebook.com/profile/100069422482221/search/?q=ng%E1%BA%ADp"

    result = run_live_test(url, max_scrolls=3, cdp_url=cdp_url)
    print("\n=== RESULT ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
