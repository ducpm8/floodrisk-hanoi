"""Unit tests for FacebookCrawler — no real HTTP, no DB, no Playwright."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from api.scrapers.facebook import FacebookCrawler


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _: None)


def _make_conn(row: tuple | None) -> MagicMock:
    mock_result = MagicMock()
    mock_result.fetchone.return_value = row
    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_result
    return mock_conn


# ---------------------------------------------------------------------------
# Graph API path
# ---------------------------------------------------------------------------

def test_graph_api_success_returns_posts() -> None:
    crawler = FacebookCrawler(access_token="tok_test")
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "data": [
            {"id": "1", "message": "Ngập 50cm Mỹ Đình", "created_time": "2023-08-15T10:00:00"},
        ]
    }
    crawler.session = MagicMock()
    with patch("requests.get", return_value=mock_resp):
        posts = crawler.fetch_group_posts("123456", limit=5)

    assert len(posts) == 1
    assert posts[0]["message"] == "Ngập 50cm Mỹ Đình"


def test_graph_api_429_falls_through_to_playwright() -> None:
    crawler = FacebookCrawler(access_token="tok_test")
    mock_resp = MagicMock(status_code=429)
    crawler._fetch_via_playwright = MagicMock(return_value=[])
    with patch("requests.get", return_value=mock_resp):
        posts = crawler.fetch_group_posts("123456")
    crawler._fetch_via_playwright.assert_called_once()


def test_graph_api_error_falls_through_to_playwright() -> None:
    crawler = FacebookCrawler(access_token="tok_test")
    crawler._fetch_via_playwright = MagicMock(return_value=[])
    with patch("requests.get", side_effect=requests.ConnectionError("fail")):
        posts = crawler.fetch_group_posts("123456")
    crawler._fetch_via_playwright.assert_called_once()


def test_no_token_skips_graph_api() -> None:
    crawler = FacebookCrawler(access_token="")
    crawler._fetch_via_playwright = MagicMock(return_value=[])
    posts = crawler.fetch_group_posts("123456")
    crawler._fetch_via_playwright.assert_called_once()


# ---------------------------------------------------------------------------
# Session cookie persistence
# ---------------------------------------------------------------------------

def test_load_session_cookies_returns_list() -> None:
    cookies = [{"name": "c_user", "value": "12345", "domain": ".facebook.com"}]
    conn = _make_conn((json.dumps(cookies),))
    crawler = FacebookCrawler(db_conn=conn)
    result = crawler._load_session_cookies()
    assert result == cookies


def test_load_session_cookies_no_row_returns_empty() -> None:
    conn = _make_conn(None)
    crawler = FacebookCrawler(db_conn=conn)
    assert crawler._load_session_cookies() == []


def test_load_session_cookies_no_db_returns_empty() -> None:
    crawler = FacebookCrawler(db_conn=None)
    assert crawler._load_session_cookies() == []


def test_save_session_cookies_executes_upsert() -> None:
    conn = _make_conn(None)
    crawler = FacebookCrawler(db_conn=conn)
    cookies = [{"name": "xs", "value": "abc"}]
    crawler._save_session_cookies(cookies)
    assert conn.execute.called
    call_args = conn.execute.call_args[0]
    assert "INSERT INTO crawler_sessions" in str(call_args[0])


def test_save_session_no_db_is_noop() -> None:
    crawler = FacebookCrawler(db_conn=None)
    crawler._save_session_cookies([{"name": "x"}])


# ---------------------------------------------------------------------------
# Session expiry + admin alert
# ---------------------------------------------------------------------------

def test_mark_session_expired_updates_status() -> None:
    conn = _make_conn(None)
    crawler = FacebookCrawler(db_conn=conn)
    crawler._mark_session_expired()
    assert conn.execute.called


def test_alert_admin_logs_error(caplog: pytest.LogCaptureFixture) -> None:
    crawler = FacebookCrawler()
    with caplog.at_level("ERROR"):
        crawler._alert_admin("session expired for group 999")
    assert "session expired for group 999" in caplog.text


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def test_parse_group_html_extracts_messages() -> None:
    html = """
    <html><body>
      <div data-testid="post_message"><p>Ngập 40cm tại Mỹ Đình</p></div>
      <div data-testid="post_message"><p>Nước dâng ở Cầu Giấy</p></div>
    </body></html>
    """
    crawler = FacebookCrawler()
    posts = crawler._parse_group_html(html, limit=10)
    assert len(posts) == 2
    assert any("Mỹ Đình" in p["message"] for p in posts)


def test_parse_group_html_respects_limit() -> None:
    post = '<div data-testid="post_message"><p>flood</p></div>'
    html = f"<html><body>{''.join([post] * 10)}</body></html>"
    crawler = FacebookCrawler()
    posts = crawler._parse_group_html(html, limit=3)
    assert len(posts) == 3


def test_parse_group_html_empty_returns_empty() -> None:
    crawler = FacebookCrawler()
    assert crawler._parse_group_html("<html><body></body></html>", limit=5) == []
