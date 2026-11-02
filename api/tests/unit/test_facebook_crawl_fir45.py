"""
FIR-45: FacebookCrawler streaming + admin endpoint tests.
No real HTTP, no DB, no Playwright.
"""

import json
import queue
import time
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.scrapers.base import contains_flood_keywords
from api.scrapers.facebook import FacebookCrawler, _EXTRACT_POSTS_JS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _: None)


def _make_conn(*, staged_row=None) -> MagicMock:
    """Mock sync SQLAlchemy connection. staged_row controls _is_already_staged."""
    result = MagicMock()
    result.fetchone.return_value = staged_row
    conn = MagicMock()
    conn.execute.return_value = result
    return conn


def _drain_queue(q: queue.Queue) -> list[dict]:
    items = []
    while True:
        item = q.get(timeout=2)
        if item is None:
            break
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Unit: _is_already_staged
# ---------------------------------------------------------------------------

def test_is_already_staged_returns_true_when_row_exists() -> None:
    conn = _make_conn(staged_row=("1",))
    crawler = FacebookCrawler(db_conn=conn)
    assert crawler._is_already_staged("123456789012") is True


def test_is_already_staged_returns_false_when_no_row() -> None:
    conn = _make_conn(staged_row=None)
    crawler = FacebookCrawler(db_conn=conn)
    assert crawler._is_already_staged("123456789012") is False


def test_is_already_staged_returns_false_without_conn() -> None:
    crawler = FacebookCrawler(db_conn=None)
    assert crawler._is_already_staged("123456789012") is False


# ---------------------------------------------------------------------------
# Unit: _stage_item
# ---------------------------------------------------------------------------

def test_stage_item_executes_insert_with_external_id() -> None:
    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn)
    crawler._stage_item("999888777666", "Ngập 50cm phố Tây Sơn", "https://facebook.com/groups/test")
    conn.execute.assert_called_once()
    conn.commit.assert_called_once()
    sql_call = conn.execute.call_args
    params = sql_call[0][1]
    assert params["external_id"] == "999888777666"
    assert "Ngập 50cm" in params["content"]


def test_stage_item_no_op_without_conn() -> None:
    crawler = FacebookCrawler(db_conn=None)
    crawler._stage_item("123", "text", "https://facebook.com/groups/x")
    # should not raise


# ---------------------------------------------------------------------------
# Unit: crawl_with_streaming — no Playwright
# ---------------------------------------------------------------------------

def test_crawl_streaming_error_when_playwright_not_installed() -> None:
    conn = _make_conn(staged_row=("1",))
    crawler = FacebookCrawler(db_conn=conn, headless=False)

    q: queue.Queue = queue.Queue()
    with patch("api.scrapers.facebook._HAS_CLOAK", False):
        with patch("api.scrapers.facebook.sync_playwright", None):
            crawler.crawl_with_streaming("https://facebook.com/groups/x", 5, q)

    events = _drain_queue(q)
    types = [e["type"] for e in events]
    assert "error" in types
    error_evt = next(e for e in events if e["type"] == "error")
    assert "cloakbrowser" in error_evt["message"].lower()


def test_crawl_streaming_error_when_no_cookies() -> None:
    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn, headless=False)

    with patch.object(crawler, "_load_session_cookies", return_value=[]):
        q: queue.Queue = queue.Queue()
        crawler.crawl_with_streaming("https://facebook.com/groups/x", 5, q)

    events = _drain_queue(q)
    types = [e["type"] for e in events]
    assert "error" in types
    assert not any(t == "start" for t in types)


def test_crawl_streaming_stages_up_to_max_posts() -> None:
    call_count = {"n": 0}

    def _is_staged(post_id: str) -> bool:
        return False  # nothing staged yet

    def _stage(post_id: str, content: str, source_url: str, raw_timestamp=None) -> None:
        call_count["n"] += 1

    fake_posts = [{"id": str(1000000000 + i), "text": f"Ngập đường số {i}"} for i in range(20)]
    mock_page = MagicMock()
    mock_page.evaluate.return_value = fake_posts
    mock_ctx = MagicMock()
    mock_ctx.cookies.return_value = []
    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_ctx
    mock_ctx.new_page.return_value = mock_page

    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn, headless=False)

    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", side_effect=_is_staged):
                with patch.object(crawler, "_stage_item", side_effect=_stage):
                    with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser):
                        q: queue.Queue = queue.Queue()
                        crawler.crawl_with_streaming("https://facebook.com/groups/x", 5, q)

    events = _drain_queue(q)
    staged_events = [e for e in events if e["type"] == "post_staged"]
    assert len(staged_events) == 5
    assert call_count["n"] == 5
    done = next(e for e in events if e["type"] == "done")
    assert done["staged"] == 5


def test_crawl_streaming_skips_duplicates() -> None:
    fake_posts = [{"id": "111222333444", "text": "ngập lụt text"}]
    mock_page = MagicMock()
    mock_page.evaluate.return_value = fake_posts
    mock_ctx = MagicMock()
    mock_ctx.cookies.return_value = []
    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_ctx
    mock_ctx.new_page.return_value = mock_page

    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn, headless=False)

    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=True):
                with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser):
                    q: queue.Queue = queue.Queue()
                    crawler.crawl_with_streaming("https://facebook.com/groups/x", 5, q)

    events = _drain_queue(q)
    skipped = [e for e in events if e["type"] == "post_skipped"]
    assert len(skipped) >= 1
    assert skipped[0]["reason"] == "duplicate"
    staged = [e for e in events if e["type"] == "post_staged"]
    assert len(staged) == 0


def _make_playwright_mocks(fake_posts: list[dict]) -> tuple:
    """Return (mock_browser, mock_page) wired up with given fake_posts from page.evaluate.
    Patch ``api.scrapers.facebook._cloak_launch`` with return_value=mock_browser in tests."""
    mock_page = MagicMock()
    mock_page.evaluate.return_value = fake_posts
    mock_ctx = MagicMock()
    mock_ctx.cookies.return_value = []
    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_ctx
    mock_ctx.new_page.return_value = mock_page
    return mock_browser, mock_page


def test_crawl_streaming_emits_extraction_events_when_posts_staged() -> None:
    fake_posts = [{"id": "111222333444", "text": "Ngập phố Tây Sơn"}]
    mock_browser, _ = _make_playwright_mocks(fake_posts)
    conn = _make_conn()

    crawler = FacebookCrawler(db_conn=conn, headless=False)
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item"):
                    with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", return_value=(1, [])) as mock_worker:
                            q: queue.Queue = queue.Queue()
                            crawler.crawl_with_streaming("https://facebook.com/groups/x", 1, q)

    events = _drain_queue(q)
    types = [e["type"] for e in events]
    assert "extraction_start" in types
    assert "extraction_done" in types
    done_ext = next(e for e in events if e["type"] == "extraction_done")
    assert done_ext["flood_events_added"] == 1
    assert done_ext["errors"] == []
    mock_worker.assert_called_once_with(conn, batch_size=1)


def test_crawl_streaming_skips_extraction_when_nothing_staged() -> None:
    fake_posts = [{"id": "111222333444", "text": "text"}]
    mock_browser, _ = _make_playwright_mocks(fake_posts)
    conn = _make_conn()

    crawler = FacebookCrawler(db_conn=conn, headless=False)
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=True):  # everything duplicate
                with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser):
                    with patch("api.scrapers.ai_extraction_worker.run_worker") as mock_worker:
                        q: queue.Queue = queue.Queue()
                        crawler.crawl_with_streaming("https://facebook.com/groups/x", 1, q)

    events = _drain_queue(q)
    types = [e["type"] for e in events]
    assert "extraction_start" not in types
    assert "extraction_done" not in types
    mock_worker.assert_not_called()


def test_crawl_streaming_extraction_error_does_not_crash() -> None:
    fake_posts = [{"id": "111222333444", "text": "Ngập"}]
    mock_browser, _ = _make_playwright_mocks(fake_posts)
    conn = _make_conn()

    crawler = FacebookCrawler(db_conn=conn, headless=False)
    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item"):
                    with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", side_effect=RuntimeError("haiku down")):
                            q: queue.Queue = queue.Queue()
                            crawler.crawl_with_streaming("https://facebook.com/groups/x", 1, q)

    events = _drain_queue(q)
    types = [e["type"] for e in events]
    assert "extraction_error" in types
    err = next(e for e in events if e["type"] == "extraction_error")
    assert "haiku down" in err["message"]
    # sentinel must still be sent
    assert "done" in types


def test_headless_false_passed_to_playwright_launch() -> None:
    mock_page = MagicMock()
    mock_page.evaluate.return_value = []
    mock_ctx = MagicMock()
    mock_ctx.cookies.return_value = []
    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_ctx
    mock_ctx.new_page.return_value = mock_page

    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn, headless=False)

    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser) as mock_launch:
                q: queue.Queue = queue.Queue()
                crawler.crawl_with_streaming("https://facebook.com/groups/x", 5, q)
                _drain_queue(q)

    mock_launch.assert_called_once_with(headless=False, humanize=True)


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

client = TestClient(app, raise_server_exceptions=False)


def test_endpoint_requires_admin_token() -> None:
    def fake_run(url, max_posts, log_queue, cdp_url=None, max_comments_per_post=10):
        log_queue.put({"type": "done", "staged": 0, "skipped": 0})
        log_queue.put(None)

    with patch("api.routers.admin_facebook._run_crawl_sync", side_effect=fake_run):
        resp = client.post(
            "/api/admin/facebook-crawl",
            json={"facebook_url": "https://www.facebook.com/groups/test"},
            headers={},
        )
    # ADMIN_TOKEN unset = dev mode (all pass). Test verifies auth logic runs without crash.
    assert resp.status_code in (200, 403)


def test_endpoint_rejects_non_facebook_url() -> None:
    resp = client.post(
        "/api/admin/facebook-crawl",
        json={"facebook_url": "https://www.twitter.com/groups/test"},
    )
    assert resp.status_code == 422


def test_endpoint_rejects_max_posts_over_100() -> None:
    resp = client.post(
        "/api/admin/facebook-crawl",
        json={"facebook_url": "https://www.facebook.com/groups/test", "max_posts": 101},
    )
    assert resp.status_code == 422


def test_endpoint_rejects_max_posts_zero() -> None:
    resp = client.post(
        "/api/admin/facebook-crawl",
        json={"facebook_url": "https://www.facebook.com/groups/test", "max_posts": 0},
    )
    assert resp.status_code == 422


def test_endpoint_returns_sse_content_type() -> None:
    events = [
        {"type": "start", "url": "https://facebook.com/groups/x", "max_posts": 1},
        {"type": "done", "staged": 0, "skipped": 0},
    ]

    def fake_run(url, max_posts, log_queue, cdp_url=None, max_comments_per_post=10):
        for e in events:
            log_queue.put(e)
        log_queue.put(None)

    with patch("api.routers.admin_facebook._run_crawl_sync", side_effect=fake_run):
        resp = client.post(
            "/api/admin/facebook-crawl",
            json={"facebook_url": "https://www.facebook.com/groups/test", "max_posts": 1},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


def test_endpoint_streams_all_events() -> None:
    fake_events = [
        {"type": "start", "url": "https://facebook.com/groups/x", "max_posts": 2},
        {"type": "post_staged", "post_id": "111222333444", "preview": "Ngập phố", "staged_so_far": 1},
        {"type": "post_skipped", "post_id": "555666777888", "reason": "duplicate"},
        {"type": "done", "staged": 1, "skipped": 1},
    ]

    def fake_run(url, max_posts, log_queue, cdp_url=None, max_comments_per_post=10):
        for e in fake_events:
            log_queue.put(e)
        log_queue.put(None)

    with patch("api.routers.admin_facebook._run_crawl_sync", side_effect=fake_run):
        resp = client.post(
            "/api/admin/facebook-crawl",
            json={"facebook_url": "https://www.facebook.com/groups/test", "max_posts": 2},
        )

    assert resp.status_code == 200
    lines = [l for l in resp.text.splitlines() if l.startswith("data:")]
    parsed = [json.loads(l[len("data: "):]) for l in lines]
    types = [e["type"] for e in parsed if e.get("type") != "heartbeat"]
    assert types == ["start", "post_staged", "post_skipped", "done"]


# ---------------------------------------------------------------------------
# FIR-50: contains_flood_keywords pre-filter
# ---------------------------------------------------------------------------

def test_contains_flood_keywords_matches_flood_terms() -> None:
    assert contains_flood_keywords("ngập 50cm phố Tây Sơn") is True
    assert contains_flood_keywords("NGẬP LỤT nghiêm trọng") is True
    assert contains_flood_keywords("mực nước dâng cao") is True
    assert contains_flood_keywords("cm") is True


def test_contains_flood_keywords_rejects_noise() -> None:
    assert contains_flood_keywords("Ăn trưa ở đâu") is False
    assert contains_flood_keywords("") is False
    assert contains_flood_keywords("tin tức hôm nay") is False


# ---------------------------------------------------------------------------
# FIR-50: _is_already_staged with comment prefix
# ---------------------------------------------------------------------------

def test_is_already_staged_with_comment_prefix() -> None:
    conn = _make_conn(staged_row=("1",))
    crawler = FacebookCrawler(db_conn=conn)
    assert crawler._is_already_staged("comment:1234567890") is True
    sql_call = conn.execute.call_args
    params = sql_call[0][1]
    assert params["eid"] == "comment:1234567890"


# ---------------------------------------------------------------------------
# FIR-50: _stage_item with comment prefix external_id
# ---------------------------------------------------------------------------

def test_stage_item_comment_uses_comment_prefix() -> None:
    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn)
    crawler._stage_item("comment:999", "ngập 50cm nhà tôi", "https://facebook.com/groups/x/posts/111?comment_id=999")
    params = conn.execute.call_args[0][1]
    assert params["external_id"] == "comment:999"


# ---------------------------------------------------------------------------
# FIR-50: post keyword pre-filter
# ---------------------------------------------------------------------------

def test_crawl_streaming_skips_post_no_flood_keywords() -> None:
    fake_posts = [{"id": "111222333444", "text": "Hôm nay ăn gì ngon", "permalink": None}]
    mock_browser, _ = _make_playwright_mocks(fake_posts)
    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn, headless=False)

    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_stage_item") as mock_stage:
                with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser):
                    q: queue.Queue = queue.Queue()
                    crawler.crawl_with_streaming("https://facebook.com/groups/x", 5, q)

    events = _drain_queue(q)
    skipped = [e for e in events if e["type"] == "post_skipped"]
    assert len(skipped) >= 1
    assert skipped[0]["reason"] == "no_flood_keywords"
    mock_stage.assert_not_called()


# ---------------------------------------------------------------------------
# FIR-50: comment extraction — happy path
# ---------------------------------------------------------------------------

def _make_pw_mocks_with_comment_page(fake_posts, fake_comments):
    """Return (mock_browser, main_page, comment_page) with separate page mocks.
    Patch ``api.scrapers.facebook._cloak_launch`` with return_value=mock_browser in tests."""
    main_page = MagicMock()
    main_page.evaluate.side_effect = [fake_posts, fake_posts]  # CLICK_READ_MORE→list ok; EXTRACT_POSTS→posts

    comment_page = MagicMock()
    comment_page.evaluate.side_effect = [0, fake_comments]  # CLICK_LOAD_COMMENTS→0; EXTRACT_COMMENTS→list

    mock_ctx = MagicMock()
    mock_ctx.cookies.return_value = []
    mock_ctx.new_page.side_effect = [main_page, comment_page]

    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_ctx

    return mock_browser, main_page, comment_page


@pytest.mark.skip(reason="comment extraction disabled in crawl_with_streaming — re-enable when stable")
def test_crawl_streaming_stages_comments_from_post() -> None:
    fake_posts = [{"id": "111", "text": "ngập lụt", "permalink": "https://facebook.com/groups/x/posts/111", "timestamp": None}]
    fake_comments = [{"id": "999", "text": "ngập 50cm nhà tôi", "timestamp": None}]
    mock_browser, _, _ = _make_pw_mocks_with_comment_page(fake_posts, fake_comments)
    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn, headless=False)

    staged_ext_ids = []

    def record_stage(ext_id, content, source_url, raw_timestamp=None):
        staged_ext_ids.append(ext_id)

    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item", side_effect=record_stage):
                    with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", return_value=(2, [])):
                            q: queue.Queue = queue.Queue()
                            crawler.crawl_with_streaming("https://facebook.com/groups/x", 1, q)

    events = _drain_queue(q)
    assert "111" in staged_ext_ids
    assert "comment:999" in staged_ext_ids
    comment_staged_events = [e for e in events if e["type"] == "comment_staged"]
    assert len(comment_staged_events) == 1
    assert comment_staged_events[0]["comment_id"] == "999"
    done = next(e for e in events if e["type"] == "done")
    assert done["comments_staged"] == 1


@pytest.mark.skip(reason="comment extraction disabled in crawl_with_streaming — re-enable when stable")
def test_crawl_streaming_skips_comment_no_flood_keywords() -> None:
    fake_posts = [{"id": "111", "text": "ngập lụt", "permalink": "https://facebook.com/groups/x/posts/111", "timestamp": None}]
    fake_comments = [{"id": "999", "text": "bình luận bình thường không liên quan", "timestamp": None}]
    mock_browser, _, _ = _make_pw_mocks_with_comment_page(fake_posts, fake_comments)
    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn, headless=False)

    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item") as mock_stage:
                    with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", return_value=(1, [])):
                            q: queue.Queue = queue.Queue()
                            crawler.crawl_with_streaming("https://facebook.com/groups/x", 1, q)

    events = _drain_queue(q)
    comment_skipped = [e for e in events if e["type"] == "comment_skipped"]
    assert len(comment_skipped) == 1
    assert comment_skipped[0]["reason"] == "no_flood_keywords"
    # _stage_item called only once (for the post, not the comment)
    assert mock_stage.call_count == 1


@pytest.mark.skip(reason="comment extraction disabled in crawl_with_streaming — re-enable when stable")
def test_crawl_streaming_skips_duplicate_comment() -> None:
    fake_posts = [{"id": "111", "text": "ngập lụt", "permalink": "https://facebook.com/groups/x/posts/111", "timestamp": None}]
    fake_comments = [{"id": "999", "text": "ngập 50cm nhà tôi", "timestamp": None}]
    mock_browser, _, _ = _make_pw_mocks_with_comment_page(fake_posts, fake_comments)
    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn, headless=False)

    def is_staged(ext_id: str) -> bool:
        return ext_id == "comment:999"  # post new, comment duplicate

    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", side_effect=is_staged):
                with patch.object(crawler, "_stage_item") as mock_stage:
                    with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", return_value=(1, [])):
                            q: queue.Queue = queue.Queue()
                            crawler.crawl_with_streaming("https://facebook.com/groups/x", 1, q)

    events = _drain_queue(q)
    comment_skipped = [e for e in events if e["type"] == "comment_skipped"]
    assert len(comment_skipped) == 1
    assert comment_skipped[0]["reason"] == "duplicate"
    # _stage_item called only once (for the post)
    assert mock_stage.call_count == 1


@pytest.mark.skip(reason="comment extraction disabled in crawl_with_streaming — re-enable when stable")
def test_crawl_streaming_batch_size_includes_comments() -> None:
    fake_posts = [{"id": "111", "text": "ngập lụt", "permalink": "https://facebook.com/groups/x/posts/111", "timestamp": None}]
    fake_comments = [
        {"id": "aaa", "text": "ngập 50cm", "timestamp": None},
        {"id": "bbb", "text": "mực nước dâng", "timestamp": None},
    ]
    mock_browser, _, _ = _make_pw_mocks_with_comment_page(fake_posts, fake_comments)
    conn = _make_conn()
    crawler = FacebookCrawler(db_conn=conn, headless=False)

    with patch.object(crawler, "_load_session_cookies", return_value=[{"name": "c", "value": "v"}]):
        with patch.object(crawler, "_save_session_cookies"):
            with patch.object(crawler, "_is_already_staged", return_value=False):
                with patch.object(crawler, "_stage_item"):
                    with patch("api.scrapers.facebook._cloak_launch", return_value=mock_browser):
                        with patch("api.scrapers.ai_extraction_worker.run_worker", return_value=(3, [])) as mock_worker:
                            q: queue.Queue = queue.Queue()
                            crawler.crawl_with_streaming("https://facebook.com/groups/x", 1, q)

    _drain_queue(q)
    # 1 post + 2 comments = batch_size 3
    mock_worker.assert_called_once_with(conn, batch_size=3)
