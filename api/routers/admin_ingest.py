"""
Admin manual ingest — FIR-39 / FIR-41

Endpoints:
  POST /admin/manual-ingest         accept pasted plaintext (JSON body)
  POST /admin/manual-ingest/upload  accept uploaded text file (multipart)
  POST /admin/youtube-ingest        accept YouTube URL, fetch vi transcript → staging

manual-ingest and upload run Haiku extraction synchronously and return stats.
youtube-ingest stages the transcript and returns 202 (worker processes async).
Auth: X-Admin-Token header (same pattern as admin_crawl.py).

YouTube transcript path uses yt-dlp (eng review 2026-05-18, decisions D1-D13):
caption-only flow, no FFmpeg, no cookies, vi -> a.vi fallback, threading.Lock
serializes invocations to bound RAM, 60s wall clock guard on endpoint.
"""

import asyncio
import logging
import os
import re
import sys
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.raw_flood_staging import RawFloodStaging
from api.routers.admin_crawl import require_admin

# scripts/ is at project root — add it so we can import process_content
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from scripts.seed_production import process_content as _process_content_sync  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_CONTENT_CHARS = 100_000
_MAX_FILE_BYTES = 100_000
_ALLOWED_MIME = {"text/plain", "text/html"}

# Serialize yt-dlp invocations: each extract_info peaks 50-100MB RAM.
# Concurrent admin paste-ingests would multiply that and risk OOM under uvicorn
# workers. Pattern lifted from reference impl (youtube_cast_addon/app.py).
YT_DLP_LOCK = threading.Lock()

# Wall-clock guard for the whole transcript fetch flow (D12). Per-request
# socket_timeout=15 inside ydl_opts bounds individual network calls; this
# bounds the total end-to-end including yt-dlp's internal retries.
# Bumped from 60s after FIR-83 Run 4 follow-up wired in the Whisper fallback;
# audio download + Whisper transcription for a ~5 min video runs 20-40s,
# so the wall guard must cover both yt-dlp metadata fetch AND the fallback chain.
_YT_DLP_WALL_TIMEOUT_S = 180.0

# OpenAI Whisper fallback config (FIR-83 Run 4 follow-up 2026-05-18).
# Triggered only when both caption tiers (manual vi, auto a.vi) return empty.
# Key + YouTube cookie file loaded via pydantic Settings (.env-aware);
# os.getenv as fallback for bare-env contexts (tests, scripts that don't
# import api.config).
def _get_openai_key() -> str:
    try:
        from api.config import settings  # local import to avoid cycle at module load
        return settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    except Exception:
        return os.getenv("OPENAI_API_KEY", "")


def _get_youtube_cookies_file() -> str | None:
    """Return path to Netscape cookies.txt for yt-dlp audio downloads, or None.

    YouTube anti-bot blocks anonymous audio/video format URLs for many videos;
    Whisper fallback needs authenticated downloads. Cookie file is exported
    via scripts/youtube_cookies_export.py (FIR-49 EditThisCookie pattern,
    Netscape format for yt-dlp). Env var: YOUTUBE_COOKIES_FILE.
    """
    try:
        from api.config import settings
        path = settings.youtube_cookies_file or os.getenv("YOUTUBE_COOKIES_FILE", "")
    except Exception:
        path = os.getenv("YOUTUBE_COOKIES_FILE", "")
    if not path:
        return None
    if not os.path.isfile(path):
        logger.warning("YOUTUBE_COOKIES_FILE set but file not found: %s", path)
        return None
    return path


_WHISPER_MODEL = "whisper-1"
# OpenAI rejects upload >25MB. m4a at ~96kbps fits ~35 min; cap at 30 min so
# we both stay under the size limit and bound cost ($0.18 per call at the cap).
_WHISPER_MAX_DURATION_S = 30 * 60
_WHISPER_MAX_BYTES = 25 * 1024 * 1024


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class IngestBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=_MAX_CONTENT_CHARS)
    source_url: str | None = None
    event_date: str | None = None


class YoutubeIngestBody(BaseModel):
    youtube_url: str
    event_date: str | None = None

    @field_validator("youtube_url")
    @classmethod
    def validate_youtube_url(cls, v: str) -> str:
        if "youtube.com" not in v and "youtu.be" not in v:
            raise ValueError("URL must be a YouTube link (youtube.com or youtu.be)")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_event_date(event_date: str | None) -> datetime | None:
    if not event_date:
        return None
    try:
        d = date.fromisoformat(event_date)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except ValueError:
        return None


async def _insert_staging(
    db: AsyncSession,
    content: str,
    source_url: str | None,
    event_date: str | None,
    source_type: str = "manual",
) -> str:
    row = RawFloodStaging(
        content=content,
        source_type=source_type,
        source_url=source_url,
        post_timestamp=_parse_event_date(event_date),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return str(row.id)


def _extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if "youtu.be" in hostname:
        return parsed.path.lstrip("/").split("/")[0]
    if "youtube.com" in hostname:
        if parsed.path == "/watch":
            return parse_qs(parsed.query)["v"][0]
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/")[1].split("/")[0]
        if "/embed/" in parsed.path:
            return parsed.path.split("/embed/")[1].split("/")[0]
    raise ValueError(f"Cannot extract video ID from {url!r}")


_DATE_RE_WITH_YEAR = re.compile(r'\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b')
_DATE_RE_NO_YEAR = re.compile(r'\b(\d{1,2})[/.\-](\d{1,2})\b')


def _parse_date_from_title(title: str) -> str | None:
    """Extract YYYY-MM-DD from a Vietnamese video title (DD/MM/YYYY or DD/MM). Returns None if no valid date found."""
    m = _DATE_RE_WITH_YEAR.search(title)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass
    m = _DATE_RE_NO_YEAR.search(title)
    if m:
        try:
            return date(date.today().year, int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass
    return None


# yt-dlp DownloadError substring -> (HTTP status, admin-facing message). Order
# matters: first match wins. "Sign in to confirm you're not a bot" is checked
# before the generic "Sign in to confirm" (age-gate) so bot-check routes to 503
# instead of 422.
_YTDLP_ERROR_MAP: tuple[tuple[str, int, str], ...] = (
    ("Private video", 422, "Video is unavailable or private"),
    ("Sign in to confirm you're not a bot", 503, "YouTube bot-check triggered, retry later"),
    ("HTTP Error 429", 503, "Rate-limited by YouTube, retry later"),
    ("Sign in to confirm", 422, "Sign in required (age-restricted video)"),
    ("Video unavailable", 422, "Video is unavailable or private"),
    ("Subtitles are disabled", 422, "Transcripts are disabled for this video"),
    ("No video formats found", 422, "No transcript available for this video"),
    ("is not available", 422, "Video is unavailable"),
    ("timed out", 422, "Could not fetch transcript (timeout)"),
    ("Connection timed out", 422, "Could not fetch transcript (timeout)"),
)


def _raise_for_ytdlp_error(msg: str) -> None:
    """Map yt-dlp DownloadError text to HTTPException. Default to 503."""
    lower = msg.lower()
    for needle, status, detail in _YTDLP_ERROR_MAP:
        if needle.lower() in lower:
            raise HTTPException(status_code=status, detail=detail)
    raise HTTPException(status_code=503, detail=f"Could not fetch transcript: {msg}")


def _best_track_url(tracks: list[dict]) -> str | None:
    """Pick caption URL preferring VTT > SRT > JSON3, else first with a url."""
    for ext in ("vtt", "srt", "json3"):
        for t in tracks:
            if t.get("ext") == ext and t.get("url"):
                return t["url"]
    for t in tracks:
        if t.get("url"):
            return t["url"]
    return None


def _pick_subtitle_url(info: dict) -> str | None:
    """Return URL for vi caption track. Manual (subtitles) first, auto (automatic_captions) second."""
    manual = (info.get("subtitles") or {}).get("vi")
    if manual:
        url = _best_track_url(manual)
        if url:
            return url
    auto = (info.get("automatic_captions") or {}).get("vi")
    if auto:
        url = _best_track_url(auto)
        if url:
            return url
    return None


_VTT_TAG_RE = re.compile(r"<[^>]+>")


def _fetch_and_parse_subtitle(url: str) -> str:
    """Fetch caption file via urllib + strip WEBVTT/SRT timestamps + cue numbers + tags.

    Returns plain text. Raises 422 on fetch failure.
    """
    try:
        with urlopen(url, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not fetch transcript ({exc.__class__.__name__})",
        )

    out: list[str] = []
    seen_in_block: set[str] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            seen_in_block.clear()
            continue
        if stripped.startswith("WEBVTT"):
            continue
        if "-->" in stripped:
            continue
        if stripped.isdigit():
            continue
        if stripped.startswith(("NOTE", "STYLE", "REGION", "Kind:", "Language:")):
            continue
        cleaned = _VTT_TAG_RE.sub("", stripped).strip()
        if not cleaned:
            continue
        # Auto-captions duplicate lines across overlapping cues; dedupe locally
        if cleaned in seen_in_block:
            continue
        seen_in_block.add(cleaned)
        out.append(cleaned)
    return "\n".join(out)


def _upload_date_to_iso(upload_date: str | None) -> str | None:
    """Convert yt-dlp upload_date (YYYYMMDD) to ISO YYYY-MM-DD, or None if unparseable."""
    if not upload_date or len(upload_date) != 8 or not upload_date.isdigit():
        return None
    try:
        return date(int(upload_date[0:4]), int(upload_date[4:6]), int(upload_date[6:8])).isoformat()
    except ValueError:
        return None


def _fetch_via_whisper(youtube_url: str, info: dict) -> str | None:
    """Download audio via yt-dlp and transcribe via OpenAI Whisper.

    Third tier of the D6 caption chain (manual vi -> auto a.vi -> Whisper),
    wired in after FIR-83 Run 4 surfaced a 50% no-captions rate on hand-curated
    Hà Nội flood URLs. Cost: ~$0.006/min audio (whisper-1).

    Returns transcript text on success. Returns None when:
      - OPENAI_API_KEY is unset (signal caller to surface 422)
    Raises HTTPException (422/503) when:
      - Video duration exceeds the cap
      - yt-dlp audio download fails (mapped through existing error router)
      - Whisper API errors (auth, rate-limit, generic)
    """
    api_key = _get_openai_key()
    if not api_key:
        return None

    duration = info.get("duration") or 0
    if duration and duration > _WHISPER_MAX_DURATION_S:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Video duration {int(duration)}s exceeds Whisper cap "
                f"{_WHISPER_MAX_DURATION_S}s; ingest manually via /admin/manual-ingest"
            ),
        )

    try:
        import yt_dlp  # type: ignore[import-not-found]
        from yt_dlp.utils import DownloadError  # type: ignore[import-not-found]
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="yt-dlp not installed") from exc

    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="openai SDK not installed") from exc

    cookies_file = _get_youtube_cookies_file()
    if cookies_file is None:
        # YouTube anti-bot serves zero audio formats to anonymous yt-dlp on
        # most videos. Without cookies the download will produce
        # "No video formats found"; surface a clearer error.
        raise HTTPException(
            status_code=503,
            detail=(
                "Whisper fallback needs YouTube cookies: set YOUTUBE_COOKIES_FILE "
                "in .env (see scripts/youtube_cookies_export.py)"
            ),
        )

    with tempfile.TemporaryDirectory(prefix="ytwhisper_") as tmpdir:
        outtmpl = os.path.join(tmpdir, "audio.%(ext)s")
        dl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            # Prefer m4a (Whisper-compatible, no transcoding). Filesize bound
            # leaves headroom under Whisper's 25 MB upload limit.
            "format": "bestaudio[ext=m4a][filesize<24M]/bestaudio[ext=m4a]/bestaudio",
            "outtmpl": outtmpl,
            "socket_timeout": 30,
            "ignore_no_formats_error": True,
            "cookiefile": cookies_file,
        }
        with YT_DLP_LOCK:
            try:
                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    ydl.extract_info(youtube_url, download=True)
            except DownloadError as exc:
                _raise_for_ytdlp_error(str(exc))
                raise  # unreachable

        audio_files = [
            os.path.join(tmpdir, f)
            for f in os.listdir(tmpdir)
            if os.path.isfile(os.path.join(tmpdir, f))
        ]
        if not audio_files:
            raise HTTPException(
                status_code=503,
                detail="Whisper: audio download produced no file",
            )

        audio_path = audio_files[0]
        size = os.path.getsize(audio_path)
        if size > _WHISPER_MAX_BYTES:
            raise HTTPException(
                status_code=422,
                detail=f"Audio file {size} bytes exceeds Whisper 25MB limit",
            )

        client = OpenAI(api_key=api_key)
        try:
            with open(audio_path, "rb") as f:
                resp = client.audio.transcriptions.create(
                    model=_WHISPER_MODEL,
                    file=f,
                    language="vi",
                    response_format="text",
                )
        except Exception as exc:
            msg = str(exc)
            lower = msg.lower()
            if "401" in msg or "auth" in lower or "api key" in lower:
                raise HTTPException(
                    status_code=503,
                    detail="Whisper auth failed (check OPENAI_API_KEY)",
                )
            if "429" in msg or "rate" in lower:
                raise HTTPException(
                    status_code=503,
                    detail="Whisper rate-limited, retry later",
                )
            raise HTTPException(
                status_code=503,
                detail=f"Whisper API error: {msg[:200]}",
            )

    text = resp if isinstance(resp, str) else str(resp)
    text = text.strip()
    return text or None


def _fetch_youtube_transcript(url: str) -> tuple[str, str | None]:
    """Return (transcript_text, extracted_date) using yt-dlp.

    Caption-only metadata flow: download=False keeps FFmpeg out of the codepath
    for the metadata pass, so the no_warnings opt below is sufficient to silence
    the optional-FFmpeg notice (D13). Serialized via YT_DLP_LOCK (D2).
    Caption preference: manual vi -> auto a.vi (D6) -> Whisper audio fallback
    (FIR-83 Run 4 follow-up, gated on OPENAI_API_KEY).
    Errors routed by _raise_for_ytdlp_error (D5). Date priority: title regex
    first, upload_date fallback (D8).
    """
    try:
        import yt_dlp  # type: ignore[import-not-found]
        from yt_dlp.utils import DownloadError  # type: ignore[import-not-found]
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="yt-dlp not installed") from exc

    # Fail fast on malformed URL before any network work.
    _extract_video_id(url)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["vi"],
        "socket_timeout": 15,
        # Caption-only flow: skip format selection failures. Without this,
        # YouTube videos whose available formats don't match yt-dlp's default
        # selector raise "Requested format is not available" even though we
        # only need caption tracks (which live outside the formats list).
        "ignore_no_formats_error": True,
    }

    with YT_DLP_LOCK:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as exc:
            _raise_for_ytdlp_error(str(exc))
            raise  # unreachable; _raise_for_ytdlp_error always raises

    if not info:
        raise HTTPException(status_code=503, detail="yt-dlp returned no info")

    subtitle_url = _pick_subtitle_url(info)
    transcript_text = ""
    transcript_source = "captions"
    if subtitle_url:
        transcript_text = _fetch_and_parse_subtitle(subtitle_url)

    if not transcript_text.strip():
        # Captions absent or empty → Whisper fallback. Returns None when
        # OPENAI_API_KEY is unset; raises on Whisper-side errors.
        whisper_text = _fetch_via_whisper(url, info)
        if whisper_text:
            transcript_text = whisper_text
            transcript_source = "whisper"

    if not transcript_text.strip():
        raise HTTPException(status_code=422, detail="No Vietnamese transcript available")

    title = info.get("title")
    extracted_date = _parse_date_from_title(title) if title else None
    if extracted_date is None:
        extracted_date = _upload_date_to_iso(info.get("upload_date"))

    text = transcript_text
    if title:
        text = f"[Tiêu đề video: {title}]\n\n{text}"
    if transcript_source == "whisper":
        # Mark provenance so downstream review can flag Whisper-derived rows.
        text = f"[Nguồn transcript: OpenAI Whisper (audio→text)]\n{text}"

    if len(text) > _MAX_CONTENT_CHARS:
        logger.warning("YouTube transcript truncated from %d chars", len(text))
        text = text[:_MAX_CONTENT_CHARS]

    return text, extracted_date


async def _read_file(file: UploadFile) -> str:
    raw = await file.read(_MAX_FILE_BYTES + 1)
    if len(raw) == 0:
        raise HTTPException(status_code=422, detail="File is empty")
    if len(raw) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"File exceeds {_MAX_FILE_BYTES:,} byte limit",
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1258")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=422,
            detail="File encoding not recognised (expected UTF-8 or CP1258/Vietnamese Windows)",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/admin/manual-ingest", status_code=200)
async def manual_ingest(
    body: IngestBody,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> dict:
    staging_id = await _insert_staging(db, body.content, body.source_url, body.event_date)
    stats = await asyncio.to_thread(_process_content_sync, body.content, body.event_date, body.source_url)
    status = "error" if "error" in stats else "processed"
    return {"staging_id": staging_id, "status": status, "stats": stats}


@router.post("/admin/manual-ingest/upload", status_code=200)
async def manual_ingest_upload(
    file: UploadFile = File(...),
    source_url: str | None = Form(default=None),
    event_date: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> dict:
    if file.content_type not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{file.content_type}'. Only text/plain and text/html accepted.",
        )
    content = await _read_file(file)
    staging_id = await _insert_staging(db, content, source_url, event_date)
    stats = await asyncio.to_thread(_process_content_sync, content, event_date, source_url)
    status = "error" if "error" in stats else "processed"
    return {"staging_id": staging_id, "status": status, "stats": stats}


@router.post("/admin/youtube-ingest", status_code=202)
async def youtube_ingest(
    body: YoutubeIngestBody,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> dict:
    # Duplicate URL guard
    existing = await db.execute(
        select(RawFloodStaging.id).where(RawFloodStaging.source_url == body.youtube_url).limit(1)
    )
    existing_row = existing.scalar_one_or_none()
    if existing_row is not None:
        raise HTTPException(
            status_code=409,
            detail={"error": "already_queued", "staging_id": str(existing_row)},
        )

    # Fetch transcript (sync — run in thread to avoid blocking event loop).
    # Wall-clock guard (D12) bounds total time including yt-dlp internal retries.
    try:
        transcript, extracted_date = await asyncio.wait_for(
            asyncio.to_thread(_fetch_youtube_transcript, body.youtube_url),
            timeout=_YT_DLP_WALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail=f"Transcript fetch exceeded {int(_YT_DLP_WALL_TIMEOUT_S)}s wall clock",
        )
    event_date = body.event_date or extracted_date

    staging_id = await _insert_staging(
        db, transcript, body.youtube_url, event_date, source_type="youtube"
    )
    return {"staging_id": staging_id, "status": "queued", "event_date": event_date}
