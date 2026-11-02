"""
Unit tests for FIR-18: api/scrapers/ai_extraction_worker.py
All DB interactions are mocked; LLM calls use FLOOD_SCRAPER_MOCK_LLM=1.
"""

import os
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, call, patch

import pytest

from api.scrapers.ai_extraction_worker import (
    ExtractedEvent,
    _parse_event,
    call_haiku_news,
    call_haiku_social,
    geocode_ward,
    run_worker,
    save_event,
    validate_severity,
)


# ---------------------------------------------------------------------------
# validate_severity
# ---------------------------------------------------------------------------

class TestValidateSeverity:
    def test_none_depth_returns_severity_unchanged(self):
        assert validate_severity(None, "moderate") == "moderate"

    def test_none_severity_returns_none(self):
        assert validate_severity(30, None) is None

    def test_both_none_returns_none(self):
        assert validate_severity(None, None) is None

    def test_shallow_moderate_corrected_to_minor(self):
        assert validate_severity(10, "moderate") == "minor"

    def test_shallow_severe_corrected_to_minor(self):
        assert validate_severity(15, "severe") == "minor"

    def test_shallow_minor_unchanged(self):
        assert validate_severity(19, "minor") == "minor"

    def test_medium_severe_corrected_to_moderate(self):
        assert validate_severity(40, "severe") == "moderate"

    def test_medium_moderate_unchanged(self):
        assert validate_severity(50, "moderate") == "moderate"

    def test_medium_minor_unchanged(self):
        assert validate_severity(20, "minor") == "minor"

    def test_deep_minor_corrected_to_severe(self):
        assert validate_severity(80, "minor") == "severe"

    def test_deep_moderate_corrected_to_severe(self):
        assert validate_severity(70, "moderate") == "severe"

    def test_deep_severe_unchanged(self):
        assert validate_severity(100, "severe") == "severe"

    def test_boundary_20_moderate_unchanged(self):
        assert validate_severity(20, "moderate") == "moderate"

    def test_boundary_60_moderate_unchanged(self):
        assert validate_severity(60, "moderate") == "moderate"

    def test_boundary_61_severe_unchanged(self):
        assert validate_severity(61, "severe") == "severe"


# ---------------------------------------------------------------------------
# _parse_event
# ---------------------------------------------------------------------------

class TestParseEvent:
    def _raw(self, **kwargs):
        base = {
            "location_name": "Phố Huế",
            "ward": "Phường Bùi Thị Xuân",
            "district": "Hai Bà Trưng",
            "event_date": "2024-09-12",
            "depth_cm": 30,
            "depth_description": "ngang bắp chân",
            "duration_hours": 2,
            "severity": "moderate",
            "confidence": "high",
        }
        base.update(kwargs)
        return base

    def test_returns_extracted_event(self):
        ev = _parse_event(self._raw(), "news", "http://example.com", None)
        assert isinstance(ev, ExtractedEvent)
        assert ev.event_date == "2024-09-12"
        assert ev.confidence == "high"
        assert ev.source_type == "news"
        assert ev.source_url == "http://example.com"

    def test_no_date_and_no_fallback_returns_none(self):
        raw = self._raw(event_date=None)
        assert _parse_event(raw, "news", None, None) is None

    def test_uses_fallback_date_when_event_date_missing(self):
        raw = self._raw(event_date=None)
        ev = _parse_event(raw, "social_media", None, "2024-09-10")
        assert ev is not None
        assert ev.event_date == "2024-09-10"

    def test_severity_corrected_by_depth(self):
        raw = self._raw(depth_cm=10, severity="moderate")
        ev = _parse_event(raw, "news", None, None)
        assert ev.severity == "minor"

    def test_missing_confidence_defaults_to_low(self):
        raw = self._raw()
        raw.pop("confidence")
        ev = _parse_event(raw, "news", None, None)
        assert ev.confidence == "low"

    def test_lat_lng_default_none(self):
        ev = _parse_event(self._raw(), "news", None, None)
        assert ev.lat is None
        assert ev.lng is None


# ---------------------------------------------------------------------------
# call_haiku_social (mock mode)
# ---------------------------------------------------------------------------

class TestCallHaikuSocial:
    def test_mock_returns_results_for_each_post(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        import anthropic
        client = anthropic.Anthropic(api_key="mock")
        batch = [
            {"content": "post A", "timestamp": "2024-09-12T08:00:00"},
            {"content": "post B", "timestamp": "2024-09-12T09:00:00"},
        ]
        result = call_haiku_social(client, batch)
        assert "results" in result
        assert len(result["results"]) == 2
        assert result["results"][0]["post_index"] == 0
        assert result["results"][1]["post_index"] == 1

    def test_mock_single_post(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        import anthropic
        client = anthropic.Anthropic(api_key="mock")
        result = call_haiku_social(client, [{"content": "x", "timestamp": "2024-09-01"}])
        assert len(result["results"]) == 1


# ---------------------------------------------------------------------------
# call_haiku_news (mock mode)
# ---------------------------------------------------------------------------

class TestCallHaikuNews:
    def test_mock_returns_events(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        import anthropic
        client = anthropic.Anthropic(api_key="mock")
        result = call_haiku_news(client, "bài báo ngập lụt", "2024-09-12")
        assert "events" in result
        assert len(result["events"]) > 0
        assert result["events"][0]["confidence"] == "high"

    def test_mock_ignores_content_and_date(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        import anthropic
        client = anthropic.Anthropic(api_key="mock")
        r1 = call_haiku_news(client, "abc", "2024-01-01")
        r2 = call_haiku_news(client, "xyz", None)
        assert r1["events"][0]["district"] == r2["events"][0]["district"]


# ---------------------------------------------------------------------------
# geocode_ward
# ---------------------------------------------------------------------------

class TestGeocodeWard:
    def _conn(self, ward_row=None, district_row=None):
        conn = MagicMock()
        def execute_side_effect(stmt, params):
            result = MagicMock()
            q = str(stmt)
            if "ward_name = :w" in q:
                result.fetchone.return_value = ward_row
            else:
                result.fetchone.return_value = district_row
            return result
        conn.execute.side_effect = execute_side_effect
        return conn

    def test_exact_ward_match(self):
        conn = self._conn(ward_row=MagicMock(lat=21.02, lng=105.83))
        lat, lng = geocode_ward(conn, "Bùi Thị Xuân", "Hai Bà Trưng")
        assert lat == pytest.approx(21.02)
        assert lng == pytest.approx(105.83)

    def test_falls_back_to_district_centroid(self):
        conn = self._conn(ward_row=None, district_row=MagicMock(lat=21.03, lng=105.84))
        lat, lng = geocode_ward(conn, "Unknown Ward", "Hai Bà Trưng")
        assert lat == pytest.approx(21.03)
        assert lng == pytest.approx(105.84)

    def test_no_ward_skips_ward_lookup(self):
        conn = self._conn(district_row=MagicMock(lat=21.0, lng=105.8))
        lat, lng = geocode_ward(conn, None, "Đống Đa")
        assert lat is not None

    def test_no_match_returns_none_none(self):
        conn = self._conn(ward_row=None, district_row=MagicMock(lat=None, lng=None))
        lat, lng = geocode_ward(conn, "X", "Y")
        assert lat is None
        assert lng is None

    def test_no_ward_no_district_returns_none_none(self):
        conn = MagicMock()
        lat, lng = geocode_ward(conn, None, None)
        assert lat is None
        assert lng is None
        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# save_event
# ---------------------------------------------------------------------------

class TestSaveEvent:
    def _event(self, confidence="high", lat=21.0, lng=105.8):
        return ExtractedEvent(
            location_name="Test",
            ward="Phường X",
            district="Đống Đa",
            event_date="2024-09-12",
            depth_cm=40,
            depth_description="ngang đùi",
            duration_hours=3,
            severity="moderate",
            confidence=confidence,
            source_type="social_media",
            source_url="http://fb.com/post/1",
            lat=lat,
            lng=lng,
        )

    def test_high_confidence_inserts_flood_events(self):
        conn = MagicMock()
        result = save_event(conn, self._event("high"), "staging-uuid")
        assert result == 1
        sql = conn.execute.call_args[0][0].text
        assert "flood_events" in sql
        assert "flood_events_review" not in sql

    def test_medium_confidence_inserts_flood_events(self):
        conn = MagicMock()
        result = save_event(conn, self._event("medium"), "staging-uuid")
        assert result == 1

    def test_low_confidence_inserts_review(self):
        conn = MagicMock()
        result = save_event(conn, self._event("low"), "staging-uuid")
        assert result == 0
        sql = conn.execute.call_args[0][0].text
        assert "flood_events_review" in sql

    def test_social_media_maps_to_community_source(self):
        conn = MagicMock()
        save_event(conn, self._event("high"), "staging-uuid")
        params = conn.execute.call_args[0][1]
        assert params["source"] == "community"

    def test_news_source_type_maps_to_news(self):
        conn = MagicMock()
        ev = self._event("high")
        ev.source_type = "news"
        save_event(conn, ev, "staging-uuid")
        params = conn.execute.call_args[0][1]
        assert params["source"] == "news"

    def test_null_lat_lng_included_in_params(self):
        conn = MagicMock()
        ev = self._event("high", lat=None, lng=None)
        save_event(conn, ev, "staging-uuid")
        params = conn.execute.call_args[0][1]
        assert params["lat"] is None
        assert params["lng"] is None


# ---------------------------------------------------------------------------
# run_worker integration (FLOOD_SCRAPER_MOCK_LLM=1, mocked DB)
# ---------------------------------------------------------------------------

class TestRunWorker:
    def _make_row(self, source_type="social_media", has_timestamp=True):
        row = MagicMock()
        row.id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        row.content = "Ngập nặng tại Đống Đa"
        row.source_type = source_type
        row.source_url = "http://example.com"
        if has_timestamp:
            ts = MagicMock()
            ts.isoformat.return_value = "2024-09-12T08:00:00"
            ts.date.return_value.isoformat.return_value = "2024-09-12"
            row.post_timestamp = ts
        else:
            row.post_timestamp = None
        return row

    def _mock_conn(self, rows):
        conn = MagicMock()

        fetch_result = MagicMock()
        fetch_result.fetchall.return_value = rows

        ward_result = MagicMock()
        ward_result.fetchone.return_value = MagicMock(lat=21.02, lng=105.83)

        def execute_side_effect(stmt, params=None):
            sql = str(stmt)
            if "SELECT" in sql and "raw_flood_staging" in sql:
                return fetch_result
            if "ward_centroids" in sql:
                return ward_result
            return MagicMock()

        conn.execute.side_effect = execute_side_effect
        return conn

    def test_no_rows_returns_zero(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        conn = MagicMock()
        fetch_result = MagicMock()
        fetch_result.fetchall.return_value = []
        conn.execute.return_value = fetch_result
        added, errors = run_worker(conn, batch_size=10)
        assert added == 0
        assert errors == []

    def test_social_row_adds_event(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("social_media")]
        conn = self._mock_conn(rows)
        added, errors = run_worker(conn, batch_size=10)
        assert added >= 1
        assert errors == []

    def test_news_row_adds_event(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("news")]
        conn = self._mock_conn(rows)
        added, errors = run_worker(conn, batch_size=10)
        assert added >= 1
        assert errors == []

    def test_marks_processed_after_social(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("social_media")]
        conn = self._mock_conn(rows)
        run_worker(conn, batch_size=10)
        calls_sql = [str(c[0][0]) for c in conn.execute.call_args_list]
        assert any("processed" in s and "TRUE" in s for s in calls_sql)

    def test_marks_processed_after_news(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("news")]
        conn = self._mock_conn(rows)
        run_worker(conn, batch_size=10)
        calls_sql = [str(c[0][0]) for c in conn.execute.call_args_list]
        assert any("processed" in s and "TRUE" in s for s in calls_sql)

    def test_commit_called(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("social_media")]
        conn = self._mock_conn(rows)
        run_worker(conn, batch_size=10)
        conn.commit.assert_called_once()

    def test_mixed_rows_both_processed(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("social_media"), self._make_row("news")]
        conn = self._mock_conn(rows)
        added, errors = run_worker(conn, batch_size=10)
        assert added >= 2
        assert errors == []


# ---------------------------------------------------------------------------
# run_worker — FIR-41: manual / governance / youtube processing
# ---------------------------------------------------------------------------

class TestRunWorkerFIR41:
    def _make_row(self, source_type, content="Ngập nặng tại Đống Đa"):
        row = MagicMock()
        row.id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        row.content = content
        row.source_type = source_type
        row.source_url = "http://example.com"
        ts = MagicMock()
        ts.isoformat.return_value = "2024-09-12T08:00:00"
        ts.date.return_value.isoformat.return_value = "2024-09-12"
        row.post_timestamp = ts
        return row

    def _mock_conn(self, rows):
        conn = MagicMock()
        fetch_result = MagicMock()
        fetch_result.fetchall.return_value = rows
        ward_result = MagicMock()
        ward_result.fetchone.return_value = MagicMock(lat=21.02, lng=105.83)

        def execute_side_effect(stmt, params=None):
            sql = str(stmt)
            if "SELECT" in sql and "raw_flood_staging" in sql:
                return fetch_result
            if "ward_centroids" in sql:
                return ward_result
            return MagicMock()

        conn.execute.side_effect = execute_side_effect
        return conn

    def test_manual_row_processed(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("manual")]
        conn = self._mock_conn(rows)
        added, errors = run_worker(conn, batch_size=10)
        assert added >= 1
        assert errors == []

    def test_governance_row_processed(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("governance")]
        conn = self._mock_conn(rows)
        added, errors = run_worker(conn, batch_size=10)
        assert added >= 1
        assert errors == []

    def test_youtube_row_processed(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("youtube")]
        conn = self._mock_conn(rows)
        added, errors = run_worker(conn, batch_size=10)
        assert added >= 1
        assert errors == []

    def test_manual_row_marked_processed(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("manual")]
        conn = self._mock_conn(rows)
        run_worker(conn, batch_size=10)
        calls_sql = [str(c[0][0]) for c in conn.execute.call_args_list]
        assert any("processed" in s and "TRUE" in s for s in calls_sql)

    def test_governance_row_marked_processed(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("governance")]
        conn = self._mock_conn(rows)
        run_worker(conn, batch_size=10)
        calls_sql = [str(c[0][0]) for c in conn.execute.call_args_list]
        assert any("processed" in s and "TRUE" in s for s in calls_sql)

    def test_youtube_row_marked_processed(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        rows = [self._make_row("youtube")]
        conn = self._mock_conn(rows)
        run_worker(conn, batch_size=10)
        calls_sql = [str(c[0][0]) for c in conn.execute.call_args_list]
        assert any("processed" in s and "TRUE" in s for s in calls_sql)

    def test_youtube_row_uses_youtube_system_prompt(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        from api.scrapers.ai_extraction_worker import YOUTUBE_SYSTEM_PROMPT
        rows = [self._make_row("youtube")]
        conn = self._mock_conn(rows)
        with patch("api.scrapers.ai_extraction_worker.call_haiku_news") as mock_haiku:
            mock_haiku.return_value = {"events": [], "cause": None}
            run_worker(conn, batch_size=10)
        assert mock_haiku.called
        assert mock_haiku.call_args.kwargs.get("system_prompt") == YOUTUBE_SYSTEM_PROMPT

    def test_news_row_still_uses_default_system_prompt(self, monkeypatch):
        monkeypatch.setenv("FLOOD_SCRAPER_MOCK_LLM", "1")
        from api.scrapers.ai_extraction_worker import NEWS_SYSTEM_PROMPT, YOUTUBE_SYSTEM_PROMPT
        rows = [self._make_row("news")]
        conn = self._mock_conn(rows)
        with patch("api.scrapers.ai_extraction_worker.call_haiku_news") as mock_haiku:
            mock_haiku.return_value = {"events": [], "cause": None}
            run_worker(conn, batch_size=10)
        assert mock_haiku.called
        passed = mock_haiku.call_args.kwargs.get("system_prompt", NEWS_SYSTEM_PROMPT)
        assert passed != YOUTUBE_SYSTEM_PROMPT
