"""
Unit tests for the official thoatnuochanoi.vn scraper.

Uses an HTML fixture — no network calls, no DB.
"""

import os

import pytest

from api.scrapers.official import (
    _infer_severity,
    _parse_event_date,
    _parse_html,
)


FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "thoatnuoc_page.html"
)


@pytest.fixture
def thoatnuoc_html() -> str:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return f.read()


def test_parse_html_returns_correct_count(thoatnuoc_html: str) -> None:
    """Should parse 3 valid rows, skip the incomplete row."""
    errors: list[str] = []
    points = _parse_html(thoatnuoc_html, errors)
    assert len(points) == 3


def test_parse_html_normalizes_ward(thoatnuoc_html: str) -> None:
    """Ward names must be normalized (diacritics stripped, lowercase)."""
    errors: list[str] = []
    points = _parse_html(thoatnuoc_html, errors)
    wards = [p["ward"] for p in points]
    assert "my dinh ii" in wards
    assert "trung van" in wards
    assert "ngoc lam" in wards


def test_parse_html_source_is_official(thoatnuoc_html: str) -> None:
    """All parsed points must have source='official'."""
    errors: list[str] = []
    points = _parse_html(thoatnuoc_html, errors)
    assert all(p["source"] == "official" for p in points)


def test_parse_html_severity_inference(thoatnuoc_html: str) -> None:
    """Severity should be inferred from Vietnamese status text."""
    errors: list[str] = []
    points = _parse_html(thoatnuoc_html, errors)
    by_ward = {p["ward"]: p["severity"] for p in points}
    # "Ngập nhẹ" → minor
    assert by_ward["my dinh ii"] == "minor"
    # "Ngập vừa" → moderate
    assert by_ward["trung van"] == "moderate"
    # "Ngập nặng" → severe
    assert by_ward["ngoc lam"] == "severe"


def test_parse_html_returns_empty_for_no_table() -> None:
    """HTML with no table returns empty list, no crash."""
    errors: list[str] = []
    points = _parse_html("<html><body><p>nothing here</p></body></html>", errors)
    assert points == []


def test_infer_severity_minor() -> None:
    assert _infer_severity("Ngập nhẹ") == "minor"
    assert _infer_severity("ngập 10cm") == "minor"


def test_infer_severity_moderate() -> None:
    assert _infer_severity("Ngập vừa") == "moderate"
    assert _infer_severity("mức trung bình") == "moderate"


def test_infer_severity_severe() -> None:
    assert _infer_severity("Ngập nặng") == "severe"
    assert _infer_severity("ngập nghiêm trọng") == "severe"
    assert _infer_severity("ngập sâu 50cm") == "severe"


def test_parse_event_date_ddmmyyyy() -> None:
    from datetime import date
    result = _parse_event_date("15/08/2023")
    assert result == date(2023, 8, 15)


def test_parse_event_date_iso() -> None:
    from datetime import date
    result = _parse_event_date("2023-08-15")
    assert result == date(2023, 8, 15)


def test_parse_event_date_unparseable_returns_today() -> None:
    from datetime import date
    result = _parse_event_date("not a date")
    assert result == date.today()
