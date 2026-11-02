"""
FIR-51: FacebookProfileSearchCrawler tests.
No real HTTP, no DB, no Playwright.
"""

import queue
import time
from unittest.mock import MagicMock, patch

import pytest

from api.scrapers.facebook_profile_search import (
    FacebookProfileSearchCrawler,
    _extract_post_id,
    _page_has_no_more_results,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _: None)


def _make_conn(*, staged_row=None) -> MagicMock:
    result = MagicMock()
    result.fetchone.return_value = staged_row
    conn = MagicMock()
    conn.execute.return_value = result
    return conn


def _drain(q: queue.Queue) -> list[dict]:
    items = []
    while True:
        item = q.get(timeout=2)
        if item is None:
            break
        items.append(item)
    return items


def _make_pw_mocks(evaluate_return=None, current_url="https://www.facebook.com/profile/100069422482221/search/") -> tuple:
    """Return (mock_pw_cm, mock_page) wired for crawl_profile_search_streaming."""
    mock_page = MagicMock()
    mock_page.url = current_url
    mock_page.evaluate.return_value = evaluate_return or []
    mock_page.locator.return_value.inner_text.return_value = ""

    mock_ctx = MagicMock()
    mock_ctx.cookies.return_value = []
    mock_ctx.new_page.return_value = mock_page

    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_ctx

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    mock_pw_cm = MagicMock()
    mock_pw_cm.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw_cm.__exit__ = MagicMock(return_value=False)
    return mock_pw_cm, mock_page


# ---------------------------------------------------------------------------
# Unit: _extract_post_id
# ---------------------------------------------------------------------------

def test_extract_post_id_from_posts_numeric() -> None:
    assert _extract_post_id("https://www.facebook.com/groups/123/posts/9876543210") == "9876543210"


def test_extract_post_id_from_pfbid() -> None:
    assert _extract_post_id("https://www.facebook.com/username/posts/pfbidABC123xyz") == "pfbidABC123xyz"


def test_extract_post_id_from_story_fbid() -> None:
    assert _extract_post_id("https://www.facebook.com/?story_fbid=1234567890&id=100") == "1234567890"


def test_extract_post_id_returns_none_for_unknown() -> None:
    assert _extract_post_id("https://www.facebook.com/") is None


# ---------------------------------------------------------------------------
# Unit: _page_has_no_more_results
# ---------------------------------------------------------------------------

def test_no_more_results_detects_vietnamese() -> None:
    assert _page_has_no_more_results("Không tìm thấy thêm kết quả trên trang này") is True


def test_no_more_results_detects_english() -> None:
    assert _page_has_no_more_results("No more results available") is True


def test_no_more_results_false_for_normal_page() -> None:
    assert _page_has_no_more_results("Ngập 50cm phố Tây Sơn, xe máy chết máy hàng loạt") is False


# ---------------------------------------------------------------------------
# crawl_profile_search_streaming — entry gates
# ---------------------------------------------------------------------------

def test_error_when_playwright_not_installed() -> None:
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch("api.scrapers.facebook_profile_search.sync_playwright", None):
        crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 5, q)
    events = _drain(q)
    assert any(e["type"] == "error" and "playwright" in e["message"].lower() for e in events)


def test_error_when_no_cookies() -> None:
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[]):
        crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 5, q)
    events = _drain(q)
    assert any(e["type"] == "error" for e in events)
    assert not any(e["type"] == "start" for e in events)


def test_error_and_session_expired_on_login_redirect() -> None:
    mock_pw_cm, mock_page = _make_pw_mocks(current_url="https://www.facebook.com/login/?next=...")
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_mark_session_expired") as mock_expire:
                with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                    crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 5, q)
    events = _drain(q)
    mock_expire.assert_called_once()
    assert any(e["type"] == "error" and "expired" in e["message"].lower() for e in events)


# ---------------------------------------------------------------------------
# crawl_profile_search_streaming — scroll loop
# ---------------------------------------------------------------------------

def test_happy_path_stages_post() -> None:
    fake_items = [{"href": "https://www.facebook.com/username/posts/9876543210", "text": "Ngập 50cm phố Tây Sơn"}]
    mock_pw_cm, _ = _make_pw_mocks(evaluate_return=fake_items)
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item") as mock_stage:
                    with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", return_value=(1, [])):
                            crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 1, q)
    events = _drain(q)
    assert any(e["type"] == "post_staged" and e["post_id"] == "9876543210" for e in events)
    mock_stage.assert_called_once()
    done = next(e for e in events if e["type"] == "done")
    assert done["staged"] == 1


def test_skips_duplicate_post() -> None:
    fake_items = [{"href": "https://www.facebook.com/username/posts/9876543210", "text": "ngập lụt"}]
    mock_pw_cm, _ = _make_pw_mocks(evaluate_return=fake_items)
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=True):
                with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                    crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 5, q)
    events = _drain(q)
    skipped = [e for e in events if e["type"] == "post_skipped"]
    assert len(skipped) >= 1
    assert skipped[0]["reason"] == "duplicate"
    assert not any(e["type"] == "post_staged" for e in events)


def test_skips_post_without_flood_keywords() -> None:
    fake_items = [{"href": "https://www.facebook.com/username/posts/9876543210", "text": "Hôm nay ăn gì ngon nhỉ"}]
    mock_pw_cm, _ = _make_pw_mocks(evaluate_return=fake_items)
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_stage_item") as mock_stage:
                with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                    crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 5, q)
    events = _drain(q)
    skipped = [e for e in events if e["type"] == "post_skipped"]
    assert any(e["reason"] == "no_flood_keywords" for e in skipped)
    mock_stage.assert_not_called()


def test_prefilter_active_even_on_flood_search_url() -> None:
    """Regression: earlier code bypassed the keyword filter when the URL itself
    contained a flood keyword. Bypass dropped — non-flood text must always be
    skipped to save Haiku budget. Flood URL + non-flood post → skipped."""
    fake_items = [
        {"href": "https://www.facebook.com/u/posts/1111111111", "text": "Ad ngẫu nhiên về điện thoại mới"},
        {"href": "https://www.facebook.com/u/posts/2222222222", "text": "Đường Cầu Giấy ngập sâu 30cm sáng nay"},
    ]
    mock_pw_cm, _ = _make_pw_mocks(evaluate_return=fake_items)
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item") as mock_stage:
                    with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", return_value=(1, [])):
                            crawler.crawl_profile_search_streaming(
                                "https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 5, q,
                            )
    events = _drain(q)
    staged = [e for e in events if e["type"] == "post_staged"]
    skipped = [e for e in events if e["type"] == "post_skipped" and e.get("reason") == "no_flood_keywords"]
    assert len(staged) == 1 and staged[0]["post_id"] == "2222222222"
    assert len(skipped) == 1 and skipped[0]["post_id"] == "1111111111"
    mock_stage.assert_called_once()


def test_timestamp_passed_to_stage_item() -> None:
    """JS extractor returns `timestamp`; ensure it flows through to _stage_item."""
    fake_items = [{
        "href": "https://www.facebook.com/u/posts/9876543210",
        "text": "Ngập 30cm phố Tây Sơn",
        "timestamp": 1728345600,
    }]
    mock_pw_cm, _ = _make_pw_mocks(evaluate_return=fake_items)
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item") as mock_stage:
                    with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", return_value=(1, [])):
                            crawler.crawl_profile_search_streaming(
                                "https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 1, q,
                            )
    _drain(q)
    args, _kwargs = mock_stage.call_args
    # _stage_item(post_id, content, url, raw_timestamp)
    assert args[0] == "9876543210"
    assert args[3] == 1728345600


def test_respects_max_posts_cap() -> None:
    fake_items = [
        {"href": f"https://www.facebook.com/username/posts/{1000000000 + i}", "text": f"Ngập đường số {i}"}
        for i in range(20)
    ]
    mock_pw_cm, _ = _make_pw_mocks(evaluate_return=fake_items)
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    staged_count = {"n": 0}

    def _stage(*a, **kw):
        staged_count["n"] += 1

    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item", side_effect=_stage):
                    with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", return_value=(3, [])):
                            crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 3, q)
    events = _drain(q)
    assert staged_count["n"] == 3
    done = next(e for e in events if e["type"] == "done")
    assert done["staged"] == 3


def test_stale_scroll_limit_emits_warn_and_done() -> None:
    # evaluate() always returns empty list → 3 stale scrolls → stop
    mock_pw_cm, _ = _make_pw_mocks(evaluate_return=[])
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 5, q)
    events = _drain(q)
    warn_msgs = [e["message"].lower() for e in events if e["type"] == "warn" and "message" in e]
    assert any("exhausted" in m or "stale" in m for m in warn_msgs)
    assert any(e["type"] == "done" for e in events)


# ---------------------------------------------------------------------------
# Extraction inline
# ---------------------------------------------------------------------------

def test_extraction_start_and_done_emitted_when_posts_staged() -> None:
    fake_items = [{"href": "https://www.facebook.com/username/posts/9876543210", "text": "Ngập phố Tây Sơn"}]
    mock_pw_cm, _ = _make_pw_mocks(evaluate_return=fake_items)
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item"):
                    with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", return_value=(1, [])) as mock_worker:
                            crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 1, q)
    events = _drain(q)
    assert any(e["type"] == "extraction_start" for e in events)
    assert any(e["type"] == "extraction_done" for e in events)
    mock_worker.assert_called_once_with(conn, batch_size=1)


def test_no_extraction_when_nothing_staged() -> None:
    fake_items = [{"href": "https://www.facebook.com/username/posts/9876543210", "text": "ngập lụt"}]
    mock_pw_cm, _ = _make_pw_mocks(evaluate_return=fake_items)
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=True):
                with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                    with patch("api.scrapers.ai_extraction_worker.run_worker") as mock_worker:
                        crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 5, q)
    events = _drain(q)
    assert not any(e["type"] == "extraction_start" for e in events)
    mock_worker.assert_not_called()


def test_extraction_error_does_not_crash() -> None:
    fake_items = [{"href": "https://www.facebook.com/username/posts/9876543210", "text": "Ngập 50cm"}]
    mock_pw_cm, _ = _make_pw_mocks(evaluate_return=fake_items)
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item"):
                    with patch("api.scrapers.facebook_profile_search.sync_playwright", return_value=mock_pw_cm):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", side_effect=RuntimeError("haiku down")):
                            crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 1, q)
    events = _drain(q)
    assert any(e["type"] == "extraction_error" and "haiku down" in e["message"] for e in events)
    assert any(e["type"] == "done" for e in events)


def test_sentinel_always_sent() -> None:
    """None sentinel must always reach the queue, even on unexpected crash."""
    conn = _make_conn()
    crawler = FacebookProfileSearchCrawler(db_conn=conn, headless=False)
    q: queue.Queue = queue.Queue()
    with patch.object(crawler, "_load_session_cookies", side_effect=RuntimeError("db down")):
        crawler.crawl_profile_search_streaming("https://www.facebook.com/profile/123/search/?q=ng%E1%BA%ADp", 5, q)
    # _drain() will hang forever if sentinel is missing — timeout=2 guards that
    events = _drain(q)
    assert any(e["type"] == "error" for e in events)
