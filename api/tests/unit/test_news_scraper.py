"""
Unit tests for the news scraper (VnExpress + Tuổi Trẻ + generic parsers).

No network calls, no DB, no real LLM. All external dependencies are mocked.
"""

import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from api.scrapers.news import (
    _MOCK_EXTRACTION,
    _extract_with_haiku,
    _insert_news_event,
    _parse_article_generic,
    _parse_iso_date,
    _parse_list_generic,
)

# Selectors from ALL_SOURCES — keep in sync with news.py NewsSource definitions
VNEXPRESS_LIST_SELECTORS = ["article.item-news a.title-news", "h3.title-news a"]
VNEXPRESS_ARTICLE_SELECTORS = ["article.fck_detail", "div.sidebar-1", "div[class*='detail']"]
VNEXPRESS_DOMAIN = "https://vnexpress.net"

TUOITRE_LIST_SELECTORS = ["h3.title-news a", "h2.title-news a", ".box-category-item a"]
TUOITRE_ARTICLE_SELECTORS = ["div.detail-cmain", "div#detail-id", "div[class*='detail']"]
TUOITRE_DOMAIN = "https://tuoitre.vn"


VNEXPRESS_ARTICLE_FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "vnexpress_flood_article.html"
)


@pytest.fixture
def vnexpress_article_html() -> str:
    with open(VNEXPRESS_ARTICLE_FIXTURE, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# _parse_list_generic — VnExpress selectors
# ---------------------------------------------------------------------------

VNEXPRESS_LIST_HTML = """
<html><body>
  <h3 class="title-news">
    <a href="https://vnexpress.net/ha-noi-ngap-sau-mua-4600001.html">Hà Nội ngập sau mưa</a>
  </h3>
  <h3 class="title-news">
    <a href="https://vnexpress.net/ngap-ung-my-dinh-4600002.html">Ngập úng Mỹ Đình</a>
  </h3>
  <a href="https://otherdomain.com/ignored">should be ignored</a>
</body></html>
"""


def test_parse_vnexpress_list_extracts_urls() -> None:
    urls = _parse_list_generic(VNEXPRESS_LIST_HTML, VNEXPRESS_DOMAIN, VNEXPRESS_LIST_SELECTORS)
    assert len(urls) == 2
    assert all(u.startswith("https://vnexpress.net") for u in urls)


def test_parse_vnexpress_list_deduplicates() -> None:
    html = """
    <html><body>
      <h3 class="title-news"><a href="https://vnexpress.net/article-1.html">A</a></h3>
      <h3 class="title-news"><a href="https://vnexpress.net/article-1.html">A dup</a></h3>
    </body></html>
    """
    urls = _parse_list_generic(html, VNEXPRESS_DOMAIN, VNEXPRESS_LIST_SELECTORS)
    assert len(urls) == 1


def test_parse_vnexpress_list_empty_on_no_match() -> None:
    urls = _parse_list_generic("<html><body><p>no articles</p></body></html>", VNEXPRESS_DOMAIN, VNEXPRESS_LIST_SELECTORS)
    assert urls == []


# ---------------------------------------------------------------------------
# _parse_article_generic — VnExpress selectors
# ---------------------------------------------------------------------------

def test_parse_vnexpress_article_extracts_body(vnexpress_article_html: str) -> None:
    text = _parse_article_generic(vnexpress_article_html, VNEXPRESS_ARTICLE_SELECTORS)
    assert len(text) > 50
    assert "Mỹ Đình II" in text


def test_parse_vnexpress_article_caps_at_3000_chars(vnexpress_article_html: str) -> None:
    text = _parse_article_generic(vnexpress_article_html, VNEXPRESS_ARTICLE_SELECTORS)
    assert len(text) <= 3000


def test_parse_vnexpress_article_returns_empty_for_no_body() -> None:
    text = _parse_article_generic("<html><body><p>no article element</p></body></html>", VNEXPRESS_ARTICLE_SELECTORS)
    assert text == ""


# ---------------------------------------------------------------------------
# _parse_list_generic — Tuổi Trẻ selectors
# ---------------------------------------------------------------------------

TUOITRE_LIST_HTML = """
<html><body>
  <h3 class="title-news">
    <a href="https://tuoitre.vn/ha-noi-ngap-nang-20230815.htm">Hà Nội ngập nặng</a>
  </h3>
  <h3 class="title-news">
    <a href="https://tuoitre.vn/ngap-o-cau-giay-20230816.htm">Ngập ở Cầu Giấy</a>
  </h3>
  <a href="https://other.com/nope">ignore</a>
</body></html>
"""


def test_parse_tuoitre_list_extracts_urls() -> None:
    urls = _parse_list_generic(TUOITRE_LIST_HTML, TUOITRE_DOMAIN, TUOITRE_LIST_SELECTORS)
    assert len(urls) == 2
    assert all(u.startswith("https://tuoitre.vn") for u in urls)


def test_parse_tuoitre_list_empty_on_no_match() -> None:
    urls = _parse_list_generic("<html><body><p>nothing</p></body></html>", TUOITRE_DOMAIN, TUOITRE_LIST_SELECTORS)
    assert urls == []


# ---------------------------------------------------------------------------
# _parse_article_generic — Tuổi Trẻ selectors
# ---------------------------------------------------------------------------

TUOITRE_ARTICLE_HTML = """
<html><body>
  <div class="detail-cmain">
    <p>Sáng 15/8/2023, phường Mỹ Đình II, quận Nam Từ Liêm ngập sâu 40cm.</p>
    <p>Người dân khốn đốn vì nước dâng nhanh.</p>
  </div>
</body></html>
"""


def test_parse_tuoitre_article_extracts_body() -> None:
    text = _parse_article_generic(TUOITRE_ARTICLE_HTML, TUOITRE_ARTICLE_SELECTORS)
    assert "Mỹ Đình II" in text
    assert len(text) > 20


def test_parse_tuoitre_article_returns_empty_for_no_body() -> None:
    text = _parse_article_generic("<html><body><p>no detail div</p></body></html>", TUOITRE_ARTICLE_SELECTORS)
    assert text == ""


# ---------------------------------------------------------------------------
# _extract_with_haiku — mock LLM mode
# ---------------------------------------------------------------------------

def test_extract_with_haiku_mock_mode_returns_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """FLOOD_SCRAPER_MOCK_LLM=1 returns the fixed fixture without any API call."""
    monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
    errors: list[str] = []
    result = _extract_with_haiku(None, "any text", "https://example.com", errors)
    assert result == _MOCK_EXTRACTION
    assert errors == []


def test_extract_with_haiku_mock_mode_no_client_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock mode should work with client=None (no API key required)."""
    monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
    result = _extract_with_haiku(None, "text", "https://x.com", [])
    assert result is not None
    assert result.get("ward") is not None


# ---------------------------------------------------------------------------
# _extract_with_haiku — real mode with mocked client
# ---------------------------------------------------------------------------

def _make_mock_client(response_text: str) -> MagicMock:
    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=response_text)]
    mock_client.messages.create.return_value = mock_msg
    return mock_client


def test_extract_with_haiku_parses_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOOD_SCRAPER_MOCK_LLM", raising=False)
    payload = (
        '{"ward": "Mỹ Đình II", "district": "Nam Từ Liêm", '
        '"event_date": "2023-08-15", "severity": "moderate", '
        '"location_name": "Phố Lê Đức Thọ"}'
    )
    client = _make_mock_client(payload)
    errors: list[str] = []
    result = _extract_with_haiku(client, "article text", "https://vnexpress.net/x", errors)
    assert result is not None
    assert result["ward"] == "Mỹ Đình II"
    assert result["severity"] == "moderate"
    assert result["event_date"] == "2023-08-15"
    assert errors == []


def test_extract_with_haiku_strips_json_code_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOOD_SCRAPER_MOCK_LLM", raising=False)
    payload = '```json\n{"ward": "Trung Văn", "district": "Nam Từ Liêm", "event_date": null, "severity": "minor", "location_name": null}\n```'
    client = _make_mock_client(payload)
    errors: list[str] = []
    result = _extract_with_haiku(client, "text", "https://vnexpress.net/y", errors)
    assert result is not None
    assert result["ward"] == "Trung Văn"
    assert errors == []


def test_extract_with_haiku_strips_plain_code_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOOD_SCRAPER_MOCK_LLM", raising=False)
    payload = '```\n{"ward": "Ngọc Lâm", "district": "Long Biên", "event_date": "2023-08-16", "severity": "severe", "location_name": null}\n```'
    client = _make_mock_client(payload)
    errors: list[str] = []
    result = _extract_with_haiku(client, "text", "https://vnexpress.net/z", errors)
    assert result is not None
    assert result["ward"] == "Ngọc Lâm"


def test_extract_with_haiku_json_error_appends_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOOD_SCRAPER_MOCK_LLM", raising=False)
    client = _make_mock_client("this is not json at all")
    errors: list[str] = []
    result = _extract_with_haiku(client, "text", "https://vnexpress.net/bad", errors)
    assert result is None
    assert len(errors) == 1
    assert "Haiku JSON parse error" in errors[0]


def test_extract_with_haiku_api_error_appends_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOOD_SCRAPER_MOCK_LLM", raising=False)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("connection refused")
    errors: list[str] = []
    result = _extract_with_haiku(mock_client, "text", "https://x.com/fail", errors)
    assert result is None
    assert len(errors) == 1
    assert "Haiku API error" in errors[0]


# ---------------------------------------------------------------------------
# _insert_news_event — mocked connection
# ---------------------------------------------------------------------------

def _make_conn(returns_row: bool) -> MagicMock:
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (1,) if returns_row else None
    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_result
    return mock_conn


def test_insert_news_event_returns_true_on_insert() -> None:
    conn = _make_conn(returns_row=True)
    extracted = {
        "ward": "Mỹ Đình II",
        "district": "Nam Từ Liêm",
        "event_date": "2023-08-15",
        "severity": "moderate",
        "location_name": "Phố Lê Đức Thọ",
    }
    result = _insert_news_event(conn, extracted, "https://vnexpress.net/x", "news_vnexpress")
    assert result is True
    assert conn.execute.called


def test_insert_news_event_returns_false_on_dedup() -> None:
    conn = _make_conn(returns_row=False)
    extracted = {
        "ward": "Mỹ Đình II",
        "district": "Nam Từ Liêm",
        "event_date": "2023-08-15",
        "severity": "moderate",
        "location_name": None,
    }
    result = _insert_news_event(conn, extracted, "https://vnexpress.net/x", "news_vnexpress")
    assert result is False


def test_insert_news_event_invalid_severity_defaults_to_minor() -> None:
    conn = _make_conn(returns_row=True)
    extracted = {
        "ward": "Mỹ Đình II",
        "district": "",
        "event_date": "2023-08-15",
        "severity": "catastrophic",  # invalid
        "location_name": None,
    }
    _insert_news_event(conn, extracted, "https://x.com", "news_vnexpress")
    call_kwargs = conn.execute.call_args[0][1]
    assert call_kwargs["severity"] == "minor"


def test_insert_news_event_empty_ward_returns_false() -> None:
    conn = _make_conn(returns_row=True)
    extracted = {
        "ward": "",
        "district": "Nam Từ Liêm",
        "event_date": "2023-08-15",
        "severity": "minor",
        "location_name": None,
    }
    result = _insert_news_event(conn, extracted, "https://x.com", "news_vnexpress")
    assert result is False
    assert not conn.execute.called


def test_insert_news_event_null_ward_returns_false() -> None:
    conn = _make_conn(returns_row=True)
    extracted = {"ward": None, "district": None, "event_date": None, "severity": None, "location_name": None}
    result = _insert_news_event(conn, extracted, "https://x.com", "news_vnexpress")
    assert result is False


def test_insert_news_event_missing_date_defaults_to_today() -> None:
    conn = _make_conn(returns_row=True)
    extracted = {
        "ward": "Mỹ Đình II",
        "district": "",
        "event_date": None,
        "severity": "minor",
        "location_name": None,
    }
    _insert_news_event(conn, extracted, "https://x.com", "news_vnexpress")
    call_kwargs = conn.execute.call_args[0][1]
    assert call_kwargs["event_date"] == date.today()


# ---------------------------------------------------------------------------
# _parse_iso_date
# ---------------------------------------------------------------------------

def test_parse_iso_date_valid() -> None:
    assert _parse_iso_date("2023-08-15") == date(2023, 8, 15)


def test_parse_iso_date_none_returns_none() -> None:
    assert _parse_iso_date(None) is None


def test_parse_iso_date_invalid_returns_none() -> None:
    assert _parse_iso_date("not-a-date") is None
    assert _parse_iso_date("15/08/2023") is None  # wrong format for fromisoformat
    assert _parse_iso_date("") is None
