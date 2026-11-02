"""
Unit tests for FIR-39 + FIR-41:
  POST /admin/manual-ingest
  POST /admin/manual-ingest/upload
  POST /admin/youtube-ingest
All DB calls mocked via dependency_overrides.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.database import get_db
from api.main import app

_MOCK_STATS = {
    "total": 1, "saved": 1, "duplicate": 0,
    "ward_error": 0, "db_error": 0, "low_confidence": 0, "no_depth": 0,
}

_TRANSCRIPT = "Ngập nặng tại phố Huế, mực nước 30cm."


@pytest.fixture(autouse=True)
def mock_db_startup():
    with patch("api.main._cleanup_stuck_runs", new=AsyncMock()):
        yield


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# DB mocks
# ---------------------------------------------------------------------------

def _make_ingest_db():
    """Mock DB for manual-ingest (add/commit/refresh only)."""
    async def _get_db():
        session = AsyncMock()
        session.add = MagicMock()

        async def _refresh(obj):
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

        session.refresh = _refresh
        yield session

    return _get_db


def _make_youtube_db(existing_id=None):
    """Mock DB for youtube-ingest (includes execute for duplicate URL check)."""
    async def _get_db():
        session = AsyncMock()
        session.add = MagicMock()

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = existing_id
        session.execute = AsyncMock(return_value=exec_result)

        async def _refresh(obj):
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

        session.refresh = _refresh
        yield session

    return _get_db


# ---------------------------------------------------------------------------
# POST /admin/manual-ingest — paste path
# ---------------------------------------------------------------------------

def test_paste_happy_path():
    app.dependency_overrides[get_db] = _make_ingest_db()
    with patch("api.routers.admin_ingest._process_content_sync", return_value=_MOCK_STATS):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/manual-ingest",
                json={"content": "Flooding on Tran Hung Dao", "source_url": "https://example.com"},
            )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "processed"
    assert "staging_id" in data
    assert "stats" in data


def test_paste_error_status_when_process_returns_error():
    app.dependency_overrides[get_db] = _make_ingest_db()
    error_stats = {**_MOCK_STATS, "error": "LLM failed"}
    with patch("api.routers.admin_ingest._process_content_sync", return_value=error_stats):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/manual-ingest",
                json={"content": "Flooding"},
            )
    assert r.status_code == 200
    assert r.json()["status"] == "error"


def test_paste_forbidden_when_token_set():
    with patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"):
        app.dependency_overrides[get_db] = _make_ingest_db()
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/manual-ingest",
                json={"content": "Flooding"},
                headers={"X-Admin-Token": "wrong"},
            )
    assert r.status_code == 403


def test_paste_oversized_body():
    app.dependency_overrides[get_db] = _make_ingest_db()
    with TestClient(app) as client:
        r = client.post(
            "/api/admin/manual-ingest",
            json={"content": "x" * 100_001},
        )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /admin/manual-ingest/upload — file upload path
# ---------------------------------------------------------------------------

def test_upload_txt_happy_path():
    app.dependency_overrides[get_db] = _make_ingest_db()
    with patch("api.routers.admin_ingest._process_content_sync", return_value=_MOCK_STATS):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/manual-ingest/upload",
                files={"file": ("article.txt", b"Ngap lut tai Ha Noi", "text/plain")},
            )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "processed"
    assert "staging_id" in data


def test_upload_html_happy_path():
    app.dependency_overrides[get_db] = _make_ingest_db()
    with patch("api.routers.admin_ingest._process_content_sync", return_value=_MOCK_STATS):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/manual-ingest/upload",
                files={"file": ("article.html", b"<p>Flooding</p>", "text/html")},
            )
    assert r.status_code == 200


def test_upload_forbidden_when_token_set():
    with patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"):
        app.dependency_overrides[get_db] = _make_ingest_db()
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/manual-ingest/upload",
                files={"file": ("article.txt", b"content", "text/plain")},
                headers={"X-Admin-Token": "wrong"},
            )
    assert r.status_code == 403


def test_upload_too_large():
    app.dependency_overrides[get_db] = _make_ingest_db()
    with TestClient(app) as client:
        r = client.post(
            "/api/admin/manual-ingest/upload",
            files={"file": ("big.txt", b"x" * 100_001, "text/plain")},
        )
    assert r.status_code == 422


def test_upload_wrong_mime():
    app.dependency_overrides[get_db] = _make_ingest_db()
    with TestClient(app) as client:
        r = client.post(
            "/api/admin/manual-ingest/upload",
            files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
    assert r.status_code == 422


def test_upload_empty_file():
    app.dependency_overrides[get_db] = _make_ingest_db()
    with TestClient(app) as client:
        r = client.post(
            "/api/admin/manual-ingest/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
    assert r.status_code == 422


def test_upload_cp1258_fallback():
    # bytes([0xe0, 0xe1, 0xe2]) are invalid UTF-8 but valid CP1258 (Vietnamese Windows encoding)
    non_utf8_bytes = b"Ha Noi flooding " + bytes([0xe0, 0xe1, 0xe2])
    app.dependency_overrides[get_db] = _make_ingest_db()
    with patch("api.routers.admin_ingest._process_content_sync", return_value=_MOCK_STATS):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/manual-ingest/upload",
                files={"file": ("article.txt", non_utf8_bytes, "text/plain")},
            )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# POST /admin/youtube-ingest (FIR-41)
# ---------------------------------------------------------------------------

def test_youtube_happy_path():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch("api.routers.admin_ingest._fetch_youtube_transcript", return_value=(_TRANSCRIPT, None)):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 202
    data = r.json()
    assert data["status"] == "queued"
    assert "staging_id" in data


def test_youtube_duplicate_url_409():
    existing = uuid.uuid4()
    app.dependency_overrides[get_db] = _make_youtube_db(existing_id=existing)
    with TestClient(app) as client:
        r = client.post(
            "/api/admin/youtube-ingest",
            json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
        )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "already_queued"
    assert detail["staging_id"] == str(existing)


def test_youtube_no_transcript_available_422():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch(
        "api.routers.admin_ingest._fetch_youtube_transcript",
        side_effect=HTTPException(status_code=422, detail="No Vietnamese transcript available"),
    ):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 422
    assert "No Vietnamese transcript" in r.json()["detail"]


def test_youtube_transcripts_disabled_422():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch(
        "api.routers.admin_ingest._fetch_youtube_transcript",
        side_effect=HTTPException(status_code=422, detail="Transcripts are disabled for this video"),
    ):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 422


def test_youtube_private_video_422():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch(
        "api.routers.admin_ingest._fetch_youtube_transcript",
        side_effect=HTTPException(status_code=422, detail="Video is unavailable or private"),
    ):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 422


def test_youtube_empty_transcript_422():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch(
        "api.routers.admin_ingest._fetch_youtube_transcript",
        side_effect=HTTPException(status_code=422, detail="Transcript is empty"),
    ):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 422
    assert "empty" in r.json()["detail"]


def test_youtube_library_not_installed_503():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch(
        "api.routers.admin_ingest._fetch_youtube_transcript",
        side_effect=HTTPException(status_code=503, detail="yt-dlp not installed"),
    ):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 503
    assert "yt-dlp" in r.json()["detail"]


def test_youtube_timeout_422():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch(
        "api.routers.admin_ingest._fetch_youtube_transcript",
        side_effect=HTTPException(status_code=422, detail="Could not fetch transcript (timeout)"),
    ):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 422
    assert "timeout" in r.json()["detail"]


def test_youtube_invalid_url_rejected():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with TestClient(app) as client:
        r = client.post(
            "/api/admin/youtube-ingest",
            json={"youtube_url": "https://vimeo.com/12345"},
        )
    assert r.status_code == 422


def test_youtube_admin_required():
    with patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"):
        app.dependency_overrides[get_db] = _make_youtube_db()
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
                headers={"X-Admin-Token": "wrong"},
            )
    assert r.status_code == 403


def test_youtube_youtu_be_url_accepted():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch("api.routers.admin_ingest._fetch_youtube_transcript", return_value=(_TRANSCRIPT, None)):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://youtu.be/abc123"},
            )
    assert r.status_code == 202


def test_youtube_shorts_url_accepted():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch("api.routers.admin_ingest._fetch_youtube_transcript", return_value=(_TRANSCRIPT, None)):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/shorts/abc123"},
            )
    assert r.status_code == 202


def test_youtube_list_param_in_url_accepted():
    # URLs shared from playlists have &list=PL... — video ID extraction must still work
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch("api.routers.admin_ingest._fetch_youtube_transcript", return_value=(_TRANSCRIPT, None)):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123&list=PLxyz"},
            )
    assert r.status_code == 202


def test_youtube_with_event_date():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch("api.routers.admin_ingest._fetch_youtube_transcript", return_value=(_TRANSCRIPT, None)):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={
                    "youtube_url": "https://www.youtube.com/watch?v=abc123",
                    "event_date": "2024-09-12",
                },
            )
    assert r.status_code == 202
    assert r.json()["status"] == "queued"


# ---------------------------------------------------------------------------
# _parse_date_from_title — unit tests
# ---------------------------------------------------------------------------

from api.routers.admin_ingest import _parse_date_from_title  # noqa: E402


def test_parse_date_with_year():
    assert _parse_date_from_title("Hà Nội ngập nặng 4/5/2026") == "2026-05-04"


def test_parse_date_with_dashes():
    assert _parse_date_from_title("ngập 04-05-2026 Hoàn Kiếm") == "2026-05-04"


def test_parse_date_no_year_uses_current_year():
    from datetime import date
    result = _parse_date_from_title("ngập 4/5")
    assert result == date(date.today().year, 5, 4).isoformat()


def test_parse_date_invalid_returns_none():
    assert _parse_date_from_title("không có ngày tháng gì") is None


def test_parse_date_invalid_month_returns_none():
    assert _parse_date_from_title("version 5/13/2026") is None


# ---------------------------------------------------------------------------
# extracted_date fallback behaviour
# ---------------------------------------------------------------------------

def test_youtube_uses_extracted_date_when_no_event_date():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch(
        "api.routers.admin_ingest._fetch_youtube_transcript",
        return_value=(_TRANSCRIPT, "2026-05-04"),
    ):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 202
    assert r.json()["event_date"] == "2026-05-04"


def test_youtube_body_event_date_overrides_extracted():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with patch(
        "api.routers.admin_ingest._fetch_youtube_transcript",
        return_value=(_TRANSCRIPT, "2026-05-04"),
    ):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={
                    "youtube_url": "https://www.youtube.com/watch?v=abc123",
                    "event_date": "2024-09-12",
                },
            )
    assert r.status_code == 202
    assert r.json()["event_date"] == "2024-09-12"


# ---------------------------------------------------------------------------
# yt-dlp boundary tests (N1–N14) — eng review 2026-05-18 swap
#
# These patch yt_dlp.YoutubeDL directly to exercise the new
# _fetch_youtube_transcript implementation end-to-end inside the same process.
# Tests above stay at the _fetch_youtube_transcript boundary — they continue
# to validate the endpoint contract regardless of underlying library.
# ---------------------------------------------------------------------------

import io
from contextlib import contextmanager

from api.routers import admin_ingest as _ai_module


_FAKE_VTT_VI = b"""WEBVTT

00:00:00.000 --> 00:00:02.500
Ngap nang tai pho Hue

00:00:02.500 --> 00:00:05.000
muc nuoc 30cm
"""

_FAKE_VTT_EMPTY = b"WEBVTT\n\n"


@contextmanager
def _patch_ytdlp(info_return=None, raise_download_error=None):
    """Patch yt_dlp.YoutubeDL inside admin_ingest. Optionally mock subtitle fetch."""
    fake_ydl_ctx = MagicMock()
    fake_ydl_ctx.__enter__ = MagicMock(return_value=fake_ydl_ctx)
    fake_ydl_ctx.__exit__ = MagicMock(return_value=False)
    if raise_download_error is not None:
        # Build a DownloadError instance the real way
        from yt_dlp.utils import DownloadError  # imported only inside test
        fake_ydl_ctx.extract_info = MagicMock(side_effect=DownloadError(raise_download_error))
    else:
        fake_ydl_ctx.extract_info = MagicMock(return_value=info_return)

    fake_module = MagicMock()
    fake_module.YoutubeDL = MagicMock(return_value=fake_ydl_ctx)

    with patch.dict("sys.modules", {"yt_dlp": fake_module}):
        # Also expose DownloadError on fake module for the function's import
        try:
            from yt_dlp.utils import DownloadError  # real import path
        except ImportError:
            class DownloadError(Exception):
                pass
        fake_utils = MagicMock()
        fake_utils.DownloadError = DownloadError
        with patch.dict("sys.modules", {"yt_dlp.utils": fake_utils}):
            yield fake_ydl_ctx


def _info_with_manual_vi(title="Ngap Ha Noi 12/9/2024", upload_date="20240913"):
    return {
        "title": title,
        "upload_date": upload_date,
        "subtitles": {"vi": [{"ext": "vtt", "url": "https://fake.example/manual.vtt"}]},
        "automatic_captions": {},
    }


def _info_with_auto_vi(title="No date in title", upload_date="20240913"):
    return {
        "title": title,
        "upload_date": upload_date,
        "subtitles": {},
        "automatic_captions": {"vi": [{"ext": "vtt", "url": "https://fake.example/auto.vtt"}]},
    }


def _patch_subtitle_fetch(body: bytes = _FAKE_VTT_VI):
    """Patch urlopen used by _fetch_and_parse_subtitle."""
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.read = MagicMock(return_value=body)
    return patch("api.routers.admin_ingest.urlopen", return_value=resp)


# N1 — subtitle falls back to automatic_captions when manual vi absent
def test_n1_youtube_subtitle_falls_back_to_auto_vi():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with _patch_ytdlp(info_return=_info_with_auto_vi()):
        with _patch_subtitle_fetch():
            with TestClient(app) as client:
                r = client.post(
                    "/api/admin/youtube-ingest",
                    json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
                )
    assert r.status_code == 202


# N2 — extracted_date falls back to upload_date when title has no DD/MM
def test_n2_extracted_date_from_upload_date_when_title_undated():
    app.dependency_overrides[get_db] = _make_youtube_db()
    info = _info_with_manual_vi(title="Ha Noi flooding update", upload_date="20240913")
    with _patch_ytdlp(info_return=info):
        with _patch_subtitle_fetch():
            with TestClient(app) as client:
                r = client.post(
                    "/api/admin/youtube-ingest",
                    json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
                )
    assert r.status_code == 202
    assert r.json()["event_date"] == "2024-09-13"


# N3 — title regex wins over upload_date when both present
def test_n3_title_regex_wins_over_upload_date():
    app.dependency_overrides[get_db] = _make_youtube_db()
    info = _info_with_manual_vi(title="Ngap 12/9/2024 Hoan Kiem", upload_date="20240920")
    with _patch_ytdlp(info_return=info):
        with _patch_subtitle_fetch():
            with TestClient(app) as client:
                r = client.post(
                    "/api/admin/youtube-ingest",
                    json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
                )
    assert r.status_code == 202
    assert r.json()["event_date"] == "2024-09-12"


# N4 — "Sign in to confirm" → 422 age-gated
def test_n4_signin_required_422():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with _patch_ytdlp(raise_download_error="ERROR: Sign in to confirm your age"):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 422
    assert "age" in r.json()["detail"].lower() or "sign in" in r.json()["detail"].lower()


# N5 — HTTP 429 → 503 rate limited
def test_n5_http_429_503():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with _patch_ytdlp(raise_download_error="ERROR: HTTP Error 429: Too Many Requests"):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 503
    assert "rate" in r.json()["detail"].lower() or "retry" in r.json()["detail"].lower()


# N6 — "No video formats found" → 422
def test_n6_no_formats_found_422():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with _patch_ytdlp(raise_download_error="ERROR: No video formats found"):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 422


# N7 — unmapped DownloadError → 503 default
def test_n7_default_downloaderror_503():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with _patch_ytdlp(raise_download_error="ERROR: something truly unexpected"):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 503


# N8 — transcript >100k chars is truncated, warning logged
def test_n8_truncates_at_100k(caplog):
    app.dependency_overrides[get_db] = _make_youtube_db()
    # Build a fake VTT yielding >100k chars after parse
    big_lines = "\n\n".join(
        f"00:00:{i:02d}.000 --> 00:00:{i+1:02d}.000\n" + ("x" * 200)
        for i in range(600)
    )
    big_vtt = ("WEBVTT\n\n" + big_lines).encode("utf-8")
    with caplog.at_level(logging.WARNING, logger="api.routers.admin_ingest"):
        with _patch_ytdlp(info_return=_info_with_manual_vi()):
            with _patch_subtitle_fetch(body=big_vtt):
                with TestClient(app) as client:
                    r = client.post(
                        "/api/admin/youtube-ingest",
                        json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
                    )
    assert r.status_code == 202
    assert any("truncated" in rec.message for rec in caplog.records)


# N9 — title comes from yt-dlp info, NOT oEmbed (no urllib call to oembed endpoint)
def test_n9_uses_yt_dlp_title_not_oembed():
    app.dependency_overrides[get_db] = _make_youtube_db()
    # If oEmbed path were still alive, removing urlopen would break the test.
    # Module no longer imports the oembed helper; ensure no AttributeError.
    assert not hasattr(_ai_module, "_fetch_video_title")
    with _patch_ytdlp(info_return=_info_with_manual_vi(title="My YT title 12/9/2024")):
        with _patch_subtitle_fetch():
            with TestClient(app) as client:
                r = client.post(
                    "/api/admin/youtube-ingest",
                    json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
                )
    assert r.status_code == 202


# N10 — YT_DLP_LOCK acquired before extract_info per D10
def test_n10_ytdlp_lock_acquired_before_extract_info():
    app.dependency_overrides[get_db] = _make_youtube_db()
    fake_lock = MagicMock()
    fake_lock.__enter__ = MagicMock(return_value=fake_lock)
    fake_lock.__exit__ = MagicMock(return_value=False)

    with patch.object(_ai_module, "YT_DLP_LOCK", fake_lock):
        with _patch_ytdlp(info_return=_info_with_manual_vi()):
            with _patch_subtitle_fetch():
                with TestClient(app) as client:
                    r = client.post(
                        "/api/admin/youtube-ingest",
                        json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
                    )
    assert r.status_code == 202
    fake_lock.__enter__.assert_called()
    fake_lock.__exit__.assert_called()


# N11 — no captions at all → 422 "No Vietnamese transcript available"
def test_n11_no_captions_at_all_422():
    app.dependency_overrides[get_db] = _make_youtube_db()
    info = {
        "title": "x",
        "upload_date": "20240913",
        "subtitles": {},
        "automatic_captions": {},
    }
    with _patch_ytdlp(info_return=info):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 422
    assert "Vietnamese" in r.json()["detail"]


# N12 — socket timeout text → 422 via error map
def test_n12_socket_timeout_422():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with _patch_ytdlp(raise_download_error="ERROR: Connection timed out"):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/youtube-ingest",
                json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            )
    assert r.status_code == 422
    assert "timeout" in r.json()["detail"].lower()


# N13 — subtitle URL fetched but text empty → 422
def test_n13_empty_caption_text_422():
    app.dependency_overrides[get_db] = _make_youtube_db()
    with _patch_ytdlp(info_return=_info_with_manual_vi()):
        with _patch_subtitle_fetch(body=_FAKE_VTT_EMPTY):
            with TestClient(app) as client:
                r = client.post(
                    "/api/admin/youtube-ingest",
                    json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
                )
    assert r.status_code == 422
    assert "empty" in r.json()["detail"]


# N14 — wall-clock guard fires when _fetch_youtube_transcript blocks >60s → 503
def test_n14_wall_clock_60s_returns_503(monkeypatch):
    app.dependency_overrides[get_db] = _make_youtube_db()

    async def fake_wait_for(coro, timeout):
        # Cancel the inner coro to avoid leaving a task running
        if hasattr(coro, "close"):
            coro.close()
        raise __import__("asyncio").TimeoutError()

    monkeypatch.setattr(
        "api.routers.admin_ingest.asyncio.wait_for",
        fake_wait_for,
    )
    with TestClient(app) as client:
        r = client.post(
            "/api/admin/youtube-ingest",
            json={"youtube_url": "https://www.youtube.com/watch?v=abc123"},
        )
    assert r.status_code == 503
    assert "wall clock" in r.json()["detail"]


# Need logging available for caplog assertion in N8
import logging  # noqa: E402
