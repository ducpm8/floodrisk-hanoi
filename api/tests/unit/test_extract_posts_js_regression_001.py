"""
Regression: FIR-48 — _EXTRACT_POSTS_JS text selector extracted Facebook navbar
instead of post body on authenticated pages.
Found by /qa on 2026-05-08
Report: .gstack/qa-reports/

Requires playwright. Skipped automatically if playwright not installed.
Uses page.set_content() with a local HTML fixture — no network calls.
"""

import pytest

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

from api.scrapers.facebook import _EXTRACT_POSTS_JS

pytestmark = pytest.mark.skipif(
    not _PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed",
)

# Minimal HTML that reproduces both the bug scenario and the correct case.
_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<body>
<!-- Navbar: starts with "Facebook" — Strategy 2 must NOT capture this -->
<div id="nav" style="height:100px">
  Facebook
  <a href="https://www.facebook.com/groups/flood/posts/1234567890123">Group link in nav</a>
</div>

<!-- Strategy 1: role="article" with a post link and real text -->
<div role="article" id="article1" style="height:200px">
  <a href="https://www.facebook.com/groups/floodgroup/posts/9876543210987">Post link</a>
  <p>Ngập lụt tại Hà Nội nghiêm trọng, nhiều tuyến phố bị chìm trong nước sau cơn mưa lớn kéo dài suốt đêm qua.</p>
</div>

<!-- Strategy 2 fallback: no role="article", but valid div with text -->
<div id="fallback-post" style="height:150px">
  <a href="https://www.facebook.com/hanoinews/posts/1111111111111">Another post</a>
  <p>Cập nhật tình hình ngập úng sáng nay tại các quận nội thành Hà Nội theo báo cáo của Sở Giao thông Vận tải.</p>
</div>
</body>
</html>"""


@pytest.fixture(scope="module")
def browser_page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(_FIXTURE_HTML)
        yield page
        browser.close()


def test_strategy1_extracts_text_from_role_article(browser_page):
    """Strategy 1: top-down role='article' finds post ID + real text."""
    posts = browser_page.evaluate(_EXTRACT_POSTS_JS)
    ids = {p["id"] for p in posts}
    assert "9876543210987" in ids
    post = next(p for p in posts if p["id"] == "9876543210987")
    assert "Ngập lụt" in post["text"], f"Expected flood text, got: {post['text']!r}"


def test_nav_container_not_captured(browser_page):
    """Strategy 2 fallback must not capture the navbar (starts with 'Facebook')."""
    posts = browser_page.evaluate(_EXTRACT_POSTS_JS)
    nav_post = next((p for p in posts if p["id"] == "1234567890123"), None)
    if nav_post:
        # If the nav post IS captured, its text must NOT start with "Facebook"
        assert not nav_post["text"].strip().startswith("Facebook"), (
            f"Nav garbage captured: {nav_post['text']!r}"
        )


def test_strategy2_fallback_extracts_text(browser_page):
    """Strategy 2 fallback: post link in plain div (no role='article') still gets text."""
    posts = browser_page.evaluate(_EXTRACT_POSTS_JS)
    ids = {p["id"] for p in posts}
    assert "1111111111111" in ids
    post = next(p for p in posts if p["id"] == "1111111111111")
    assert len(post["text"]) > 20, f"Expected real text, got: {post['text']!r}"


def test_no_duplicate_ids(browser_page):
    """Each post ID appears at most once in results."""
    posts = browser_page.evaluate(_EXTRACT_POSTS_JS)
    ids = [p["id"] for p in posts]
    assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"
