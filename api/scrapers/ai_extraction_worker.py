"""
FIR-18: LLM extraction pipeline — raw_flood_staging → flood_events / flood_events_review.

Pipeline per batch:
  1. Fetch unprocessed rows from raw_flood_staging (processed=False)
  2. Social media: batch 3 posts per Haiku call
     News: 1 article per Haiku call
  3. Parse JSON events array from response
  4. Validate severity ↔ depth_cm consistency
  5. Geocode via local ward_centroids (no external API)
  6. Route by confidence:
       high/medium → flood_events
       low         → flood_events_review (human review queue)
  7. Mark staging rows processed=True

Set FLOOD_SCRAPER_MOCK_LLM=1 or MOCK_LLM=true in .env to skip real Haiku calls (CI/local dev).
"""

import json
import logging
from api.config import settings as _cfg
import os
from dataclasses import dataclass
from datetime import date as _date
from typing import Any

import anthropic
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.utils.text import normalize_ward

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SOCIAL_BATCH_SIZE = 3

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_BASE_SYSTEM = """\
Bạn là chuyên gia phân tích dữ liệu ngập lụt Hà Nội. Trích xuất thông tin theo JSON schema.
Severity: <20cm hoặc "mắt cá" = minor | 20–60cm hoặc "đầu gối" = moderate | >60cm hoặc "đùi/bụng" = severe
Mapping độ sâu dân gian:
  mắt cá chân / lấp xấp → 10–15cm → minor
  bắp chân               → 20–30cm → moderate
  đầu gối                → 30–40cm → moderate
  ngang đùi              → 60–80cm → severe
  bụng xe máy            → 50–70cm → severe
  cửa ô tô               → 40–60cm → severe
  bơi được               → 100cm   → severe
confidence: "high" = rõ ràng | "medium" = suy ra được | "low" = không chắc.
Chỉ trả JSON thuần túy, không giải thích."""

SOCIAL_SYSTEM_PROMPT = _BASE_SYSTEM + """

Nguồn: Facebook/Instagram posts tiếng Việt (có thể có emoji, viết tắt).
Xử lý nhiều posts cùng lúc. Mỗi post có post_index riêng.
Extract TẤT CẢ địa điểm được nhắc đến → nhiều events riêng biệt.
Nhiều phố/ngõ/địa điểm trong một câu → tách thành events riêng.
Trả về:
{"results":[{"post_index":<int>,"events":[...]}]}

Mỗi event:
{"location_name":<str|null>,"ward":<str|null>,"district":<str|null>,
 "event_date":<YYYY-MM-DD|null>,"depth_cm":<int|null>,"depth_description":<str|null>,
 "duration_hours":<int|null>,"severity":<"minor"|"moderate"|"severe"|null>,
 "confidence":<"high"|"medium"|"low">}"""

NEWS_SYSTEM_PROMPT = _BASE_SYSTEM + """

Nguồn: Bài báo tiếng Việt. Extract TẤT CẢ địa điểm → nhiều events.
Trả về:
{"events":[...],"source_date":<ISO datetime|null>,"cause":<str|null>}

cause: nguyên nhân gây ngập được đề cập rõ ràng trong bài — ví dụ "hoàn lưu bão số 10 Bualoi", "mưa lớn ngày 30.9", "mưa rất to kéo dài". Nếu bài không đề cập nguyên nhân, trả null. Không suy đoán.

Cùng event schema như trên (cause áp dụng cho tất cả events trong bài)."""

YOUTUBE_SYSTEM_PROMPT = _BASE_SYSTEM + """

Nguồn: Transcript YouTube tiếng Việt (bản tự động — có thể chứa nhiễu âm thanh).
Hướng dẫn xử lý:
- Bỏ qua timestamp dạng [HH:MM:SS] nếu xuất hiện trong text
- Bỏ qua từ đệm: ờ, à, thì, ạ, nhỉ, mà, là, cũng
- Người nói có thể đề cập nhiều phố/ngõ liên tiếp trong một câu — tách thành nhiều events riêng biệt
- Nếu không đề cập độ sâu rõ ràng, để depth_cm = null
Trả về:
{"events":[...],"source_date":<ISO datetime|null>,"cause":<str|null>}

cause: nguyên nhân gây ngập — ví dụ "mưa lớn", "hoàn lưu bão". Nếu không đề cập, trả null.

Mỗi event:
{"location_name":<str|null>,"ward":<str|null>,"district":<str|null>,
 "event_date":<YYYY-MM-DD|null>,"depth_cm":<int|null>,"depth_description":<str|null>,
 "duration_hours":<int|null>,"severity":<"minor"|"moderate"|"severe"|null>,
 "confidence":<"high"|"medium"|"low">}"""

# ---------------------------------------------------------------------------
# Mock data (FLOOD_SCRAPER_MOCK_LLM=1)
# ---------------------------------------------------------------------------

_MOCK_SOCIAL: dict[str, Any] = {
    "results": [
        {
            "post_index": 0,
            "events": [
                {
                    "location_name": "Ngõ 76 Tây Sơn",
                    "ward": None,
                    "district": "Đống Đa",
                    "event_date": "2024-09-12",
                    "depth_cm": 80,
                    "depth_description": "đến đùi",
                    "duration_hours": None,
                    "severity": "severe",
                    "confidence": "medium",
                }
            ],
        }
    ]
}

_MOCK_NEWS: dict[str, Any] = {
    "events": [
        {
            "location_name": "Phố Minh Khai",
            "ward": None,
            "district": "Hai Bà Trưng",
            "event_date": "2024-09-12",
            "depth_cm": 30,
            "depth_description": "ngập 30cm",
            "duration_hours": None,
            "severity": "moderate",
            "confidence": "high",
        }
    ],
    "source_date": "2024-09-12T14:00:00",
    "cause": "mưa lớn",
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_VALID_SEVERITIES = {"minor", "moderate", "severe"}


def validate_severity(depth_cm: int | None, severity: str | None) -> str | None:
    """Correct severity when depth_cm contradicts the LLM's label."""
    if severity not in _VALID_SEVERITIES:
        severity = None
    if severity is None or depth_cm is None:
        return severity
    if depth_cm < 20 and severity in ("moderate", "severe"):
        return "minor"
    if 20 <= depth_cm <= 60 and severity == "severe":
        return "moderate"
    if depth_cm > 60 and severity in ("minor", "moderate"):
        return "severe"
    return severity


# ---------------------------------------------------------------------------
# Extracted event dataclass
# ---------------------------------------------------------------------------

@dataclass
class ExtractedEvent:
    location_name: str | None
    ward: str | None
    district: str | None
    event_date: str               # ISO date string YYYY-MM-DD
    depth_cm: int | None
    depth_description: str | None
    duration_hours: int | None
    severity: str | None
    confidence: str               # 'high' | 'medium' | 'low'
    source_type: str              # 'social_media' | 'news' | 'governance' | 'manual' | 'youtube'
    source_url: str | None
    cause: str | None = None      # weather trigger e.g. "hoàn lưu bão số 10 Bualoi"
    lat: float | None = None
    lng: float | None = None


def _parse_event(
    raw: dict[str, Any],
    source_type: str,
    source_url: str | None,
    fallback_date: str | None,
    cause: str | None = None,
) -> ExtractedEvent | None:
    """Parse one event dict from LLM JSON. Returns None if no usable date."""
    event_date = raw.get("event_date") or fallback_date
    if event_date is None:
        return None

    depth_cm = raw.get("depth_cm")
    severity = validate_severity(depth_cm, raw.get("severity"))

    return ExtractedEvent(
        location_name=raw.get("location_name") or raw.get("location"),
        ward=raw.get("ward"),
        district=raw.get("district"),
        event_date=event_date,
        depth_cm=depth_cm,
        depth_description=raw.get("depth_description"),
        duration_hours=raw.get("duration_hours"),
        severity=severity,
        confidence=raw.get("confidence", "low"),
        source_type=source_type,
        source_url=source_url,
        cause=cause,
    )


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

def call_haiku_social(
    client: anthropic.Anthropic,
    batch_contents: list[dict[str, str]],
) -> dict[str, Any]:
    """
    batch_contents: [{"content": str, "timestamp": str}, ...]
    Returns dict with "results" key matching SOCIAL_SYSTEM_PROMPT schema.
    """
    if os.getenv("FLOOD_SCRAPER_MOCK_LLM") == "1" or _cfg.mock_llm:
        results = [
            {"post_index": i, "events": _MOCK_SOCIAL["results"][0]["events"]}
            for i in range(len(batch_contents))
        ]
        return {"results": results}

    parts = []
    for i, item in enumerate(batch_contents):
        parts.append(f"POST {i} (timestamp: {item['timestamp']}):\n{item['content']}")
    user_text = "\n\n---\n\n".join(parts)

    resp = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=8192,
        system=[
            {
                "type": "text",
                "text": SOCIAL_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_text}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    if not raw:
        logger.warning("Haiku returned empty response for social batch")
        return {"results": [{"post_index": i, "events": []} for i in range(len(batch_contents))]}
    return json.loads(raw)


def call_haiku_news(
    client: anthropic.Anthropic,
    content: str,
    article_date: str | None,
    system_prompt: str = NEWS_SYSTEM_PROMPT,
) -> dict[str, Any]:
    """Returns dict with "events" key matching NEWS_SYSTEM_PROMPT schema."""
    if os.getenv("FLOOD_SCRAPER_MOCK_LLM") == "1" or _cfg.mock_llm:
        return _MOCK_NEWS

    date_str = article_date or "không rõ"
    user_text = f"Ngày đăng: {date_str}\n\n{content}"

    resp = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=8192,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_text}],
    )
    raw = resp.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    if not raw:
        logger.warning("Haiku returned empty response")
        return {"events": [], "source_date": None}
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def geocode_ward(
    conn: Connection,
    ward: str | None,
    district: str | None,
) -> tuple[float | None, float | None]:
    """
    Look up lat/lng from local ward_centroids table.
    Tries exact normalized ward match first, then district centroid average.
    Returns (lat, lng) or (None, None) — never calls an external API.
    """
    if ward:
        normalized = normalize_ward(ward)
        row = conn.execute(
            text("SELECT lat, lng FROM ward_centroids WHERE ward_name = :w LIMIT 1"),
            {"w": normalized},
        ).fetchone()
        if row:
            return float(row.lat), float(row.lng)

    if district:
        normalized_d = normalize_ward(district)
        row = conn.execute(
            text("""
                SELECT AVG(lat) AS lat, AVG(lng) AS lng
                FROM ward_centroids
                WHERE ward_name LIKE :d
            """),
            {"d": f"%{normalized_d}%"},
        ).fetchone()
        if row and row.lat is not None:
            return float(row.lat), float(row.lng)

    return None, None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_event(conn: Connection, event: ExtractedEvent, staging_id: str) -> int:
    """
    Route by confidence:
      high/medium → INSERT INTO flood_events
      low         → INSERT INTO flood_events_review
    Returns 1 if inserted into flood_events, 0 otherwise.
    """
    if event.confidence in ("high", "medium"):
        result = conn.execute(
            text("""
                INSERT INTO flood_events
                  (id, location_name, ward, district, lat, lng, geom,
                   event_date, severity, depth_cm, duration_hours, source, source_url, cause)
                VALUES
                  (gen_random_uuid(), :location_name, :ward, :district,
                   :lat, :lng,
                   CASE WHEN :lat IS NOT NULL AND :lng IS NOT NULL
                        THEN ST_SetSRID(ST_MakePoint(:lng2, :lat2), 4326)::geography
                        ELSE NULL
                   END,
                   CAST(:event_date AS date), :severity, :depth_cm, :duration_hours,
                   :source, :source_url, :cause)
                ON CONFLICT (source_url, event_date, COALESCE(ward, ''), COALESCE(location_name, '')) DO NOTHING
            """),
            {
                "location_name": event.location_name,
                "ward": event.ward,
                "district": event.district,
                "lat": event.lat,
                "lng": event.lng,
                "lat2": event.lat,
                "lng2": event.lng,
                "event_date": event.event_date,
                "severity": event.severity,
                "depth_cm": event.depth_cm,
                "duration_hours": event.duration_hours,
                "source": "community" if event.source_type == "social_media" else "news",
                "source_url": event.source_url,
                "cause": event.cause,
            },
        )
        return result.rowcount
    else:
        conn.execute(
            text("""
                INSERT INTO flood_events_review
                  (id, staging_id, location_name, ward, district, lat, lng,
                   event_date, depth_cm, depth_description, duration_hours,
                   severity, confidence, source_type, source_url)
                VALUES
                  (gen_random_uuid(), CAST(:staging_id AS uuid), :location_name, :ward, :district,
                   :lat, :lng, CAST(:event_date AS date), :depth_cm, :depth_description,
                   :duration_hours, :severity, :confidence, :source_type, :source_url)
            """),
            {
                "staging_id": staging_id,
                "location_name": event.location_name,
                "ward": event.ward,
                "district": event.district,
                "lat": event.lat,
                "lng": event.lng,
                "event_date": event.event_date,
                "depth_cm": event.depth_cm,
                "depth_description": event.depth_description,
                "duration_hours": event.duration_hours,
                "severity": event.severity,
                "confidence": event.confidence,
                "source_type": event.source_type,
                "source_url": event.source_url,
            },
        )
        return 0


def _mark_processed(conn: Connection, staging_id: str) -> None:
    conn.execute(
        text("UPDATE raw_flood_staging SET processed = TRUE WHERE id = CAST(:id AS uuid)"),
        {"id": staging_id},
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_worker(conn: Connection, batch_size: int = 30) -> tuple[int, list[str]]:
    """
    Process up to batch_size unprocessed raw_flood_staging rows.
    Returns (records_added_to_flood_events, error_strings).
    """
    rows = conn.execute(
        text("""
            SELECT id, content, source_type, source_url, post_timestamp, created_at
            FROM raw_flood_staging
            WHERE processed = FALSE
            ORDER BY created_at
            LIMIT :n
        """),
        {"n": batch_size},
    ).fetchall()

    if not rows:
        logger.info("FIR-18 worker: no unprocessed staging rows.")
        return 0, []

    from api.config import settings as _settings
    client = anthropic.Anthropic(api_key=_settings.anthropic_api_key or "mock")

    records_added = 0
    errors: list[str] = []

    social_rows = [r for r in rows if r.source_type == "social_media"]
    news_rows = [r for r in rows if r.source_type == "news"]
    manual_rows = [r for r in rows if r.source_type in ("manual", "governance")]
    youtube_rows = [r for r in rows if r.source_type == "youtube"]

    # --- Social media: batch SOCIAL_BATCH_SIZE posts per Haiku call ---
    for i in range(0, len(social_rows), SOCIAL_BATCH_SIZE):
        batch = social_rows[i : i + SOCIAL_BATCH_SIZE]
        try:
            batch_contents = [
                {
                    "content": r.content,
                    "timestamp": (
                        r.post_timestamp.isoformat()
                        if r.post_timestamp
                        else "không rõ"
                    ),
                }
                for r in batch
            ]
            result = call_haiku_social(client, batch_contents)

            for post_result in result.get("results", []):
                idx = post_result.get("post_index", 0)
                if idx >= len(batch):
                    continue
                row = batch[idx]
                fallback = (
                    row.post_timestamp.date().isoformat() if row.post_timestamp
                    else row.created_at.date().isoformat() if row.created_at
                    else None
                )
                for raw_ev in post_result.get("events", []):
                    event = _parse_event(raw_ev, "social_media", row.source_url, fallback)
                    if event is None:
                        continue
                    event.lat, event.lng = geocode_ward(conn, event.ward, event.district)
                    records_added += save_event(conn, event, str(row.id))

        except Exception as exc:
            logger.exception("Social batch error: %s", exc)
            errors.append(str(exc))
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            for row in batch:
                _mark_processed(conn, str(row.id))

    # --- News: one article per Haiku call ---
    for row in news_rows:
        try:
            article_date = (
                row.post_timestamp.date().isoformat() if row.post_timestamp
                else row.created_at.date().isoformat() if row.created_at
                else None
            )
            result = call_haiku_news(client, row.content, article_date)
            article_cause: str | None = result.get("cause") or None

            for raw_ev in result.get("events", []):
                event = _parse_event(raw_ev, "news", row.source_url, article_date, cause=article_cause)
                if event is None:
                    continue
                event.lat, event.lng = geocode_ward(conn, event.ward, event.district)
                records_added += save_event(conn, event, str(row.id))

        except Exception as exc:
            logger.exception("News extraction error (row %s): %s", row.id, exc)
            errors.append(str(exc))
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            _mark_processed(conn, str(row.id))

    # --- Manual / governance: route through NEWS_SYSTEM_PROMPT (P1 fix — FIR-41) ---
    for row in manual_rows:
        try:
            article_date = (
                row.post_timestamp.date().isoformat() if row.post_timestamp
                else row.created_at.date().isoformat() if row.created_at
                else None
            )
            result = call_haiku_news(client, row.content, article_date)
            article_cause: str | None = result.get("cause") or None

            for raw_ev in result.get("events", []):
                event = _parse_event(raw_ev, row.source_type, row.source_url, article_date, cause=article_cause)
                if event is None:
                    continue
                event.lat, event.lng = geocode_ward(conn, event.ward, event.district)
                records_added += save_event(conn, event, str(row.id))

        except Exception as exc:
            logger.exception("Manual/governance extraction error (row %s): %s", row.id, exc)
            errors.append(str(exc))
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            _mark_processed(conn, str(row.id))

    # --- YouTube: route through YOUTUBE_SYSTEM_PROMPT ---
    for row in youtube_rows:
        try:
            article_date = (
                row.post_timestamp.date().isoformat() if row.post_timestamp
                else row.created_at.date().isoformat() if row.created_at
                else None
            )
            result = call_haiku_news(client, row.content, article_date, system_prompt=YOUTUBE_SYSTEM_PROMPT)
            article_cause = result.get("cause") or None

            for raw_ev in result.get("events", []):
                event = _parse_event(raw_ev, "youtube", row.source_url, article_date, cause=article_cause)
                if event is None:
                    continue
                event.lat, event.lng = geocode_ward(conn, event.ward, event.district)
                records_added += save_event(conn, event, str(row.id))

        except Exception as exc:
            logger.exception("YouTube extraction error (row %s): %s", row.id, exc)
            errors.append(str(exc))
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            _mark_processed(conn, str(row.id))

    conn.commit()
    logger.info("FIR-18 worker done: %d added, %d errors.", records_added, len(errors))
    return records_added, errors
