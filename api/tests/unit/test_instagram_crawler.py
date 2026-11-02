"""Unit tests for InstagramCrawler — no real HTTP, no DB, no Playwright."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from api.scrapers.instagram import InstagramCrawler


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
# Basic Display API path
# ---------------------------------------------------------------------------

def test_api_success_returns_posts() -> None:
    crawler = InstagramCrawler(access_token="tok_test")
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "data": [
            {"id": "1", "caption": "Ngập Mỹ Đình 40cm", "timestamp": "2023-08-15T10:00:00"},
        ]
    }
    with patch("requests.get", return_value=mock_resp):
        posts = crawler.fetch_user_media("me", limit=5)

    assert len(posts) == 1
    assert posts[0]["caption"] == "Ngập Mỹ Đình 40cm"


def test_api_error_falls_to_playwright() -> None:
    crawler = InstagramCrawler(access_token="tok_test")
    mock_resp = MagicMock(status_code=403)
    crawler._fetch_via_playwright = MagicMock(return_value=[])
    with patch("requests.get", return_value=mock_resp):
        crawler.fetch_user_media("me")
    crawler._fetch_via_playwright.assert_called_once()


def test_api_exception_falls_to_playwright() -> None:
    crawler = InstagramCrawler(access_token="tok_test")
    crawler._fetch_via_playwright = MagicMock(return_value=[])
    with patch("requests.get", side_effect=requests.ConnectionError("fail")):
        crawler.fetch_user_media("me")
    crawler._fetch_via_playwright.assert_called_once()


def test_no_token_skips_api() -> None:
    crawler = InstagramCrawler(access_token="")
    crawler._fetch_via_playwright = MagicMock(return_value=[])
    crawler.fetch_user_media("me")
    crawler._fetch_via_playwright.assert_called_once()


# ---------------------------------------------------------------------------
# Session cookie persistence
# ---------------------------------------------------------------------------

def test_load_cookies_returns_list() -> None:
    cookies = [{"name": "sessionid", "value": "abc123", "domain": ".instagram.com"}]
    conn = _make_conn((json.dumps(cookies),))
    crawler = InstagramCrawler(db_conn=conn)
    assert crawler._load_session_cookies() == cookies


def test_load_cookies_no_row_returns_empty() -> None:
    conn = _make_conn(None)
    crawler = InstagramCrawler(db_conn=conn)
    assert crawler._load_session_cookies() == []


def test_load_cookies_no_db_returns_empty() -> None:
    crawler = InstagramCrawler(db_conn=None)
    assert crawler._load_session_cookies() == []


def test_save_cookies_executes_upsert() -> None:
    conn = _make_conn(None)
    crawler = InstagramCrawler(db_conn=conn)
    crawler._save_session_cookies([{"name": "sessionid", "value": "xyz"}])
    assert conn.execute.called
    call_str = str(conn.execute.call_args[0][0])
    assert "INSERT INTO crawler_sessions" in call_str


def test_save_cookies_no_db_is_noop() -> None:
    crawler = InstagramCrawler(db_conn=None)
    crawler._save_session_cookies([{"name": "x"}])


# ---------------------------------------------------------------------------
# Session expiry + admin alert
# ---------------------------------------------------------------------------

def test_mark_expired_updates_status() -> None:
    conn = _make_conn(None)
    crawler = InstagramCrawler(db_conn=conn)
    crawler._mark_session_expired()
    assert conn.execute.called


def test_alert_admin_logs_error(caplog: pytest.LogCaptureFixture) -> None:
    crawler = InstagramCrawler()
    with caplog.at_level("ERROR"):
        crawler._alert_admin("Instagram session expired for user floods_hn")
    assert "Instagram session expired" in caplog.text


# ---------------------------------------------------------------------------
# HTML parsing (og:description meta fallback)
# ---------------------------------------------------------------------------

def test_parse_profile_html_extracts_captions() -> None:
    html = """
    <html><head>
      <meta property="og:description" content="Ngập 30cm Hoàng Mai sau mưa lớn" />
    </head><body></body></html>
    """
    crawler = InstagramCrawler()
    posts = crawler._parse_profile_html(html, limit=5)
    assert len(posts) == 1
    assert "Hoàng Mai" in posts[0]["caption"]


def test_parse_profile_html_respects_limit() -> None:
    meta = '<meta property="og:description" content="flood post" />'
    html = f"<html><head>{''.join([meta] * 10)}</head><body></body></html>"
    crawler = InstagramCrawler()
    posts = crawler._parse_profile_html(html, limit=3)
    assert len(posts) == 3


def test_parse_profile_html_empty_returns_empty() -> None:
    crawler = InstagramCrawler()
    assert crawler._parse_profile_html("<html><body></body></html>", limit=5) == []
