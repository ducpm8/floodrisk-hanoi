"""
Unit tests for FIR-42: api/scrapers/enrichment_worker.py
All HTTP calls and LLM calls are mocked.
"""

import json
import os
import unittest
from unittest.mock import MagicMock, patch

import pytest

from api.scrapers.enrichment_worker import (
    _build_search_queries,
    _clean_location_for_search,
    _format_date_vi,
    _update_flood_event,
    call_haiku_enrich,
    enrich_event,
    parse_ddg_results,
    parse_google_results,
    run_enrichment,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

GOOGLE_HTML_WITH_RESULTS = """
<html><body>
  <a href="/url?q=https://vnexpress.net/article-flood-1.html&sa=U&ved=abc">Article 1</a>
  <a href="/url?q=https://dantri.com.vn/article-flood-2.html&sa=U&ved=def">Article 2</a>
  <a href="/url?q=https://vnexpress.net/article-flood-3.html&sa=U">Article 3</a>
</body></html>
"""

GOOGLE_HTML_EMPTY = "<html><body><p>No results found.</p></body></html>"

GOOGLE_HTML_MODERN = """
<html><body>
  <a jsname="UWckNb" href="https://vnexpress.net/article-modern-1.html">VnExpress</a>
  <a jsname="UWckNb" href="https://dantri.com.vn/article-modern-2.html">Dân Trí</a>
  <a href="https://google.com/search?q=other">Google nav</a>
</body></html>
"""

DDG_HTML_WITH_RESULTS = """
<html><body>
  <a class="result__a" href="https://vnexpress.net/noi-ngu-ngap-30cm.html">VnExpress flood</a>
  <a class="result__a" href="https://dantri.com.vn/ha-noi-ngap-sau.html">Dân Trí flood</a>
</body></html>
"""

DDG_HTML_EMPTY = "<html><body><p>No results.</p></body></html>"

HAIKU_EXTRACTION_RESULT = {
    "events": [{"location_name": "Phố X", "depth_cm": 30, "severity": "moderate", "confidence": "medium",
                "event_date": "2024-09-12", "ward": None, "district": "Đống Đa",
                "depth_description": "ngập 30cm", "duration_hours": None}],
    "source_date": "2024-09-12T14:00:00",
    "cause": None,
}


# ---------------------------------------------------------------------------
# 1. parse_google_results — 3-pattern: jsname, yuRUbf, legacy /url?q=
# ---------------------------------------------------------------------------

class TestParseGoogleResults:
    def test_extracts_urls_from_jsname_hrefs(self):
        urls = parse_google_results(GOOGLE_HTML_MODERN)
        assert "https://vnexpress.net/article-modern-1.html" in urls
        assert "https://dantri.com.vn/article-modern-2.html" in urls
        assert "https://google.com/search?q=other" not in urls

    def test_extracts_urls_from_redirect_hrefs(self):
        urls = parse_google_results(GOOGLE_HTML_WITH_RESULTS)
        assert "https://vnexpress.net/article-flood-1.html" in urls
        assert "https://dantri.com.vn/article-flood-2.html" in urls
        assert len(urls) <= 3

    def test_empty_page_returns_empty_list(self):
        assert parse_google_results(GOOGLE_HTML_EMPTY) == []

    def test_plain_href_without_jsname_ignored(self):
        html = '<html><body><a href="https://direct.com/page">direct</a></body></html>'
        assert parse_google_results(html) == []

    def test_deduplicates_urls(self):
        html = """<html><body>
          <a href="/url?q=https://vnexpress.net/same.html">1</a>
          <a href="/url?q=https://vnexpress.net/same.html">2</a>
        </body></html>"""
        urls = parse_google_results(html)
        assert urls.count("https://vnexpress.net/same.html") == 1


# ---------------------------------------------------------------------------
# 2. parse_ddg_results — extracts URLs from result__a hrefs
# ---------------------------------------------------------------------------

class TestParseDdgResults:
    def test_extracts_direct_hrefs(self):
        urls = parse_ddg_results(DDG_HTML_WITH_RESULTS)
        assert "https://vnexpress.net/noi-ngu-ngap-30cm.html" in urls
        assert "https://dantri.com.vn/ha-noi-ngap-sau.html" in urls

    def test_empty_page_returns_empty_list(self):
        assert parse_ddg_results(DDG_HTML_EMPTY) == []

    def test_non_result__a_links_ignored(self):
        html = '<html><body><a href="https://example.com">other link</a></body></html>'
        assert parse_ddg_results(html) == []


# ---------------------------------------------------------------------------
# 3. enrich_event — Google tier succeeds → UPDATE called
# ---------------------------------------------------------------------------

class TestEnrichEventGoogleSuccess:
    @patch("api.scrapers.enrichment_worker._fetch_search_page")
    @patch("api.scrapers.enrichment_worker._fetch_article_and_extract")
    @patch("api.scrapers.enrichment_worker._update_flood_event")
    def test_google_success_updates_event(
        self, mock_update, mock_extract, mock_search
    ):
        mock_search.return_value = GOOGLE_HTML_WITH_RESULTS
        mock_extract.return_value = HAIKU_EXTRACTION_RESULT
        mock_conn = MagicMock()
        mock_client = MagicMock()

        ok, source = enrich_event(mock_conn, "uuid-1", "Phố X", "2024-09-12", mock_client)

        assert ok is True
        assert source == "google"
        mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# 4. enrich_event — Google 0 URLs → falls through to DDG
# ---------------------------------------------------------------------------

class TestEnrichEventGoogleFallthrough:
    @patch("api.scrapers.enrichment_worker._fetch_search_page")
    @patch("api.scrapers.enrichment_worker._fetch_article_and_extract")
    @patch("api.scrapers.enrichment_worker._update_flood_event")
    def test_google_zero_results_uses_ddg(
        self, mock_update, mock_extract, mock_search
    ):
        def search_side_effect(url):
            if "google.com" in url:
                return GOOGLE_HTML_EMPTY
            return DDG_HTML_WITH_RESULTS

        mock_search.side_effect = search_side_effect
        mock_extract.return_value = HAIKU_EXTRACTION_RESULT
        mock_conn = MagicMock()
        mock_client = MagicMock()

        ok, source = enrich_event(mock_conn, "uuid-2", "Phố X", "2024-09-12", mock_client)

        assert ok is True
        assert source == "ddg"
        mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# 5. enrich_event — all tiers fail → returns False
# ---------------------------------------------------------------------------

class TestEnrichEventAllTiersFail:
    @patch("api.scrapers.enrichment_worker._fetch_search_page", return_value=GOOGLE_HTML_EMPTY)
    @patch("api.scrapers.enrichment_worker.call_haiku_enrich", return_value={"depth_cm": None, "severity": None})
    @patch("api.scrapers.enrichment_worker._update_flood_event")
    def test_all_tiers_fail_returns_false(self, mock_update, mock_haiku, mock_search):
        mock_conn = MagicMock()
        mock_client = MagicMock()

        ok, source = enrich_event(mock_conn, "uuid-3", "Phố X", "2024-09-12", mock_client)

        assert ok is False
        assert source == "none"
        mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# 6. enrich_event — location_name=NULL → skipped, no HTTP call
# ---------------------------------------------------------------------------

class TestEnrichEventNullLocation:
    @patch("api.scrapers.enrichment_worker._fetch_search_page")
    def test_null_location_skipped(self, mock_search):
        mock_conn = MagicMock()
        mock_client = MagicMock()

        ok, source = enrich_event(mock_conn, "uuid-4", None, "2024-09-12", mock_client)

        assert ok is False
        assert source == "none"
        mock_search.assert_not_called()


# ---------------------------------------------------------------------------
# 7. enrich_event — Google+DDG fail → Haiku web_search finds depth
# ---------------------------------------------------------------------------

class TestEnrichEventHaikuFallback:
    @patch("api.scrapers.enrichment_worker._fetch_search_page", return_value=GOOGLE_HTML_EMPTY)
    @patch("api.scrapers.enrichment_worker.call_haiku_enrich")
    @patch("api.scrapers.enrichment_worker._update_flood_event")
    def test_haiku_fallback_updates_event(self, mock_update, mock_haiku, mock_search):
        mock_haiku.return_value = {
            "depth_cm": 45, "severity": "moderate", "confidence": "medium", "source_url": None
        }
        mock_conn = MagicMock()
        mock_client = MagicMock()

        ok, source = enrich_event(mock_conn, "uuid-5", "Phố X", "2024-09-12", mock_client)

        assert ok is True
        assert source == "haiku"
        mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# 8. run_enrichment — no NULL events → returns zeros
# ---------------------------------------------------------------------------

class TestRunEnrichmentNoNullEvents:
    def test_empty_db_returns_zero_counts(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []

        result = run_enrichment(mock_conn, limit=50)

        assert result["enriched"] == 0
        assert result["unchanged"] == 0
        assert result["failed"] == 0
        assert result["search_sources"] == {}


# ---------------------------------------------------------------------------
# 9. run_enrichment — correct counts across 3 events
# ---------------------------------------------------------------------------

class TestRunEnrichmentCounts:
    @patch("api.scrapers.enrichment_worker.enrich_event")
    @patch("api.scrapers.enrichment_worker.anthropic.Anthropic")
    def test_counts_enriched_unchanged_failed(self, mock_anthropic, mock_enrich):
        mock_row = MagicMock()
        mock_row.id = "uuid-1"
        mock_row.location_name = "Phố X"
        mock_row.event_date.isoformat.return_value = "2024-09-12"

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [mock_row, mock_row, mock_row]

        # Event 1: enriched via google, Event 2: unchanged, Event 3: raises
        mock_enrich.side_effect = [
            (True, "google"),
            (False, "none"),
            Exception("network error"),
        ]

        result = run_enrichment(mock_conn, limit=50)

        assert result["enriched"] == 1
        assert result["unchanged"] == 1
        assert result["failed"] == 1
        assert result["search_sources"] == {"google": 1}


# ---------------------------------------------------------------------------
# 10. run_enrichment — limit respected
# ---------------------------------------------------------------------------

class TestRunEnrichmentLimit:
    def test_limit_passed_to_query(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []

        run_enrichment(mock_conn, limit=10)

        call_args = mock_conn.execute.call_args
        assert call_args[0][1]["lim"] == 10


# ---------------------------------------------------------------------------
# 11. call_haiku_enrich — mock LLM env returns null result
# ---------------------------------------------------------------------------

class TestCallHaikuEnrich:
    def test_mock_llm_returns_null_depth(self):
        os.environ["FLOOD_SCRAPER_MOCK_LLM"] = "1"
        try:
            client = MagicMock()
            result = call_haiku_enrich(client, "Phố X")
            assert result is not None
            assert result["depth_cm"] is None
        finally:
            del os.environ["FLOOD_SCRAPER_MOCK_LLM"]


# ---------------------------------------------------------------------------
# 12. Integration: POST /admin/enrich-depth → 202
# ---------------------------------------------------------------------------

class TestAdminEnrichDepthEndpoint:
    def test_returns_202_with_run_id(self):
        from fastapi.testclient import TestClient
        from api.main import app
        import os
        os.environ.pop("ADMIN_TOKEN", None)

        client = TestClient(app)
        with patch("api.routers.admin_crawl._is_running", return_value=False), \
             patch("api.routers.admin_crawl._execute_enrichment"):
            resp = client.post("/api/admin/enrich-depth", json={"limit": 10})

        assert resp.status_code == 202
        data = resp.json()
        assert "run_id" in data
        assert data["status"] == "queued"
        assert data["limit"] == 10


# ---------------------------------------------------------------------------
# 13. Integration: POST /admin/enrich-depth → 403 without token
# ---------------------------------------------------------------------------

class TestAdminEnrichDepthRequiresToken:
    def test_returns_403_without_admin_token(self):
        from fastapi.testclient import TestClient
        from api.main import app

        client = TestClient(app)
        with patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"):
            resp = client.post("/api/admin/enrich-depth", json={"limit": 10})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 14. enrich_event — DDG HTML empty → falls through to Haiku
# ---------------------------------------------------------------------------

class TestEnrichEventDdgEmptyFallsToHaiku:
    @patch("api.scrapers.enrichment_worker._fetch_search_page")
    @patch("api.scrapers.enrichment_worker.call_haiku_enrich")
    @patch("api.scrapers.enrichment_worker._update_flood_event")
    def test_ddg_zero_results_uses_haiku(self, mock_update, mock_haiku, mock_search):
        mock_search.return_value = DDG_HTML_EMPTY  # Both Google and DDG return empty
        mock_haiku.return_value = {
            "depth_cm": 20, "severity": "moderate", "confidence": "medium", "source_url": None
        }
        mock_conn = MagicMock()
        mock_client = MagicMock()

        ok, source = enrich_event(mock_conn, "uuid-6", "Phố X", "2024-09-12", mock_client)

        assert ok is True
        assert source == "haiku"
        mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# 15. _clean_location_for_search — strips parenthetical qualifiers
# ---------------------------------------------------------------------------

class TestCleanLocationForSearch:
    def test_strips_single_parens(self):
        result = _clean_location_for_search("Hầm chui số 5 (Hầm chui số 5, 6)")
        assert "(" not in result
        assert ")" not in result
        assert "Hầm chui số 5" in result

    def test_strips_multiple_parens(self):
        result = _clean_location_for_search("Phố X (alias A) (alias B)")
        assert "(" not in result
        assert "alias A" not in result
        assert "alias B" not in result

    def test_no_parens_unchanged(self):
        assert _clean_location_for_search("Ngã tư Sở") == "Ngã tư Sở"


# ---------------------------------------------------------------------------
# 16. _format_date_vi — ISO → Vietnamese date format
# ---------------------------------------------------------------------------

class TestFormatDateVi:
    def test_standard_date(self):
        assert _format_date_vi("2025-09-30") == "30/9/2025"

    def test_no_leading_zero_on_day_month(self):
        result = _format_date_vi("2025-01-05")
        assert result == "5/1/2025"

    def test_invalid_input_returned_as_is(self):
        assert _format_date_vi("not-a-date") == "not-a-date"


# ---------------------------------------------------------------------------
# 17. enrich_event — primary query 0 results → tries fallback query
# ---------------------------------------------------------------------------

class TestEnrichEventFallbackQuery:
    @patch("api.scrapers.enrichment_worker._fetch_search_page")
    @patch("api.scrapers.enrichment_worker._fetch_article_and_extract")
    @patch("api.scrapers.enrichment_worker._update_flood_event")
    def test_tries_fallback_query_when_primary_zero_results(
        self, mock_update, mock_extract, mock_search
    ):
        call_count = {"n": 0}

        def search_side_effect(url):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return GOOGLE_HTML_EMPTY  # primary → 0 results
            return GOOGLE_HTML_WITH_RESULTS  # fallback → has results

        mock_search.side_effect = search_side_effect
        mock_extract.return_value = HAIKU_EXTRACTION_RESULT
        mock_conn = MagicMock()
        mock_client = MagicMock()

        ok, source = enrich_event(mock_conn, "uuid-7", "Phố X", "2024-09-12", mock_client)

        assert ok is True
        assert source == "google"
        assert call_count["n"] == 2  # called twice: primary + fallback


# ---------------------------------------------------------------------------
# 18. run_enrichment — uses settings.anthropic_api_key not os.getenv
# ---------------------------------------------------------------------------

class TestRunEnrichmentUsesSettings:
    @patch("api.scrapers.enrichment_worker.enrich_event")
    @patch("api.scrapers.enrichment_worker.anthropic.Anthropic")
    @patch("api.scrapers.enrichment_worker.settings")
    def test_uses_settings_api_key(self, mock_settings, mock_anthropic, mock_enrich):
        mock_settings.anthropic_api_key = "settings-key-123"
        mock_row = MagicMock()
        mock_row.id = "uuid-1"
        mock_row.location_name = "Phố X"
        mock_row.event_date.isoformat.return_value = "2024-09-12"

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]
        mock_enrich.return_value = (False, "none")

        run_enrichment(mock_conn, limit=1)

        mock_anthropic.assert_called_once_with(api_key="settings-key-123")
