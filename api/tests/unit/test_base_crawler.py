"""Unit tests for BaseCrawler — no real HTTP calls."""

import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from api.scrapers.base import BaseCrawler, FLOOD_KEYWORDS, contains_flood_keywords


def _make_session(status_code: int, text: str = "<html/>") -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = text
    mock_session = MagicMock(spec=requests.Session)
    mock_session.headers = {"User-Agent": "test"}
    mock_session.get.return_value = mock_resp
    return mock_session


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _: None)


# ---------------------------------------------------------------------------
# 200 — happy path
# ---------------------------------------------------------------------------

def test_fetch_200_returns_html() -> None:
    session = _make_session(200, "<html>flood</html>")
    crawler = BaseCrawler(session=session)
    result = crawler.fetch_with_retry("https://example.com")
    assert result == "<html>flood</html>"
    assert session.get.call_count == 1


# ---------------------------------------------------------------------------
# 429 — exponential backoff retries
# ---------------------------------------------------------------------------

def test_fetch_429_retries_up_to_max() -> None:
    mock_resp_429 = MagicMock(status_code=429)
    mock_session = MagicMock(spec=requests.Session)
    mock_session.headers = {"User-Agent": "test"}
    mock_session.get.return_value = mock_resp_429
    crawler = BaseCrawler(session=mock_session)

    result = crawler.fetch_with_retry("https://example.com", max_retries=3)
    assert result is None
    assert mock_session.get.call_count == 3


def test_fetch_429_then_200_succeeds() -> None:
    mock_429 = MagicMock(status_code=429)
    mock_200 = MagicMock(status_code=200, text="<html>ok</html>")
    mock_session = MagicMock(spec=requests.Session)
    mock_session.headers = {"User-Agent": "test"}
    mock_session.get.side_effect = [mock_429, mock_200]
    crawler = BaseCrawler(session=mock_session)

    result = crawler.fetch_with_retry("https://example.com", max_retries=3)
    assert result == "<html>ok</html>"
    assert mock_session.get.call_count == 2


# ---------------------------------------------------------------------------
# 403 — Playwright fallback
# ---------------------------------------------------------------------------

def test_fetch_403_triggers_playwright_fallback() -> None:
    session = _make_session(403)
    crawler = BaseCrawler(session=session)
    crawler._fetch_with_playwright = MagicMock(return_value="<html>playwright</html>")

    result = crawler.fetch_with_retry("https://example.com", use_playwright_on_403=True)
    assert result == "<html>playwright</html>"
    crawler._fetch_with_playwright.assert_called_once_with("https://example.com")


def test_fetch_403_no_playwright_fallback_returns_none() -> None:
    session = _make_session(403)
    crawler = BaseCrawler(session=session)
    crawler._fetch_with_playwright = MagicMock()

    result = crawler.fetch_with_retry("https://example.com", use_playwright_on_403=False)
    assert result is None
    crawler._fetch_with_playwright.assert_not_called()


# ---------------------------------------------------------------------------
# Network errors
# ---------------------------------------------------------------------------

def test_fetch_network_error_retries_then_returns_none() -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_session.headers = {"User-Agent": "test"}
    mock_session.get.side_effect = requests.ConnectionError("timeout")
    crawler = BaseCrawler(session=mock_session)

    result = crawler.fetch_with_retry("https://example.com", max_retries=2)
    assert result is None
    assert mock_session.get.call_count == 2


def test_fetch_unexpected_status_returns_none() -> None:
    session = _make_session(500)
    crawler = BaseCrawler(session=session)
    result = crawler.fetch_with_retry("https://example.com", max_retries=1)
    assert result is None


# ---------------------------------------------------------------------------
# Playwright fallback — playwright not installed
# ---------------------------------------------------------------------------

def test_playwright_fallback_missing_import_returns_none() -> None:
    crawler = BaseCrawler(session=_make_session(200))
    with patch.dict("sys.modules", {"playwright": None, "playwright.sync_api": None}):
        result = crawler._fetch_with_playwright("https://example.com")
    assert result is None


# ---------------------------------------------------------------------------
# FIR-50: FLOOD_KEYWORDS constant
# ---------------------------------------------------------------------------

def test_flood_keywords_constant_nonempty() -> None:
    assert isinstance(FLOOD_KEYWORDS, frozenset)
    assert len(FLOOD_KEYWORDS) > 0
    assert "ngập" in FLOOD_KEYWORDS
