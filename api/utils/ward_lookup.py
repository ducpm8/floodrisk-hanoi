"""
Shared location resolution utility for crawl pipeline.

Resolution chain (in order):
  1. ward_centroids — exact / prefix-prepend / prefix-strip / LIKE
  2. location_aliases — cached aliases (districts, landmarks, apartments, slang)
  3. Haiku disambiguation — fires only on cache miss; stores result as alias
  4. None → caller logs ward_error

ascii:
  raw_name
      │
      ├─1─► ward_centroids (4-strategy)   ──────────────► ResolvedLocation(level='ward')
      ├─2─► location_aliases              ──────────────► ResolvedLocation(level=alias.type)
      ├─3─► Haiku → store alias           ──────────────► ResolvedLocation(level='llm')
      └─4─► None
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import text

from api.utils.text import normalize_ward

log = logging.getLogger(__name__)

# ── SQL ───────────────────────────────────────────────────────────────────────

_WARD_EXACT = text("""
    SELECT ward_name, district, lat::float AS lat, lng::float AS lng
    FROM ward_centroids WHERE ward_name = :ward_name LIMIT 1
""")

_WARD_LIKE = text("""
    SELECT ward_name, district, lat::float AS lat, lng::float AS lng
    FROM ward_centroids WHERE ward_name LIKE :pattern LIMIT 1
""")

_ALIAS_LOOKUP = text("""
    SELECT la.ward_name, la.alias_type, la.confidence, la.source,
           wc.district, wc.lat::float AS lat, wc.lng::float AS lng
    FROM location_aliases la
    JOIN ward_centroids wc ON wc.ward_name = la.ward_name
    WHERE la.alias_norm = :alias_norm
    LIMIT 1
""")

# FIR-73: substring fallback — picks the longest alias_norm that appears
# inside the normalized input. Catches descriptive strings like
# "duong nguyen chi thanh cong dai truyen hinh viet nam" → alias "nguyen chi thanh".
_ALIAS_SUBSTRING_LOOKUP = text("""
    SELECT la.ward_name, la.alias_type, la.confidence, la.source,
           wc.district, wc.lat::float AS lat, wc.lng::float AS lng
    FROM location_aliases la
    JOIN ward_centroids wc ON wc.ward_name = la.ward_name
    WHERE :normalized LIKE '%' || la.alias_norm || '%'
      AND LENGTH(la.alias_norm) >= 4
    ORDER BY LENGTH(la.alias_norm) DESC
    LIMIT 1
""")

_ALIAS_INSERT = text("""
    INSERT INTO location_aliases
        (alias_norm, alias_vi, ward_name, alias_type, confidence, source)
    VALUES
        (:alias_norm, :alias_vi, :ward_name, :alias_type, :confidence, :source)
    ON CONFLICT (alias_norm) DO NOTHING
""")

_ADMIN_PREFIXES = ("phuong ", "xa ", "thi tran ", "quan ", "huyen ")

# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class ResolvedLocation:
    ward_name: str
    district: str
    lat: float
    lng: float
    resolution_level: str   # 'ward' | 'district' | 'landmark' | 'llm' | alias_type
    confidence: float = 1.0


# ── Internal ward lookup (4-strategy) ────────────────────────────────────────

def _ward_lookup(conn: sa.Connection, normalized: str) -> Optional[dict]:
    """4-strategy ward_centroids lookup. Returns row dict or None."""
    # 1. Exact
    row = conn.execute(_WARD_EXACT, {"ward_name": normalized}).fetchone()
    if row:
        return row._asdict()

    # 2. Prepend admin prefix
    for prefix in ("phuong ", "xa ", "thi tran "):
        row = conn.execute(_WARD_EXACT, {"ward_name": prefix + normalized}).fetchone()
        if row:
            return row._asdict()

    # 3. Strip admin prefix
    for prefix in _ADMIN_PREFIXES:
        if normalized.startswith(prefix):
            row = conn.execute(_WARD_EXACT, {"ward_name": normalized[len(prefix):]}).fetchone()
            if row:
                return row._asdict()

    # 4. LIKE fallback — catches "thanh xuan" → "xa thanh xuan"
    row = conn.execute(_WARD_LIKE, {"pattern": f"%{normalized}%"}).fetchone()
    if row:
        return row._asdict()

    return None


# ── Haiku disambiguation ──────────────────────────────────────────────────────

_DISAMBIG_SYSTEM = """You are a Hanoi geography expert.
Given a Vietnamese location name (which may be a street, apartment complex, landmark,
district, or neighborhood slang), identify the most specific ward (phường/xã/thị trấn)
in Hà Nội that contains or best represents that location.

Return ONLY a JSON object:
{"ward_vi": "Tên phường đầy đủ", "district_vi": "Tên quận/huyện", "confidence": 0.0}

confidence: 1.0 = certain, 0.7 = likely, <0.5 = uncertain.
If location is outside Hà Nội or completely unknown, return {"ward_vi": null, "confidence": 0.0}
Return ONLY the JSON, no explanation."""

_CONFIDENCE_AUTO_ACCEPT = 0.7   # ≥ this → source='llm'
_CONFIDENCE_PENDING = 0.0       # < _CONFIDENCE_AUTO_ACCEPT and > 0 → source='pending'


def _haiku_disambiguate(
    client,  # anthropic.Anthropic
    raw_name: str,
    alias_vi: str,
) -> Optional[tuple[str, float]]:
    """
    Ask Haiku which ward a raw location name belongs to.
    Returns (ward_vi_name, confidence) or None.
    """
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=128,
            system=_DISAMBIG_SYSTEM,
            messages=[{"role": "user", "content": f'Location: "{alias_vi}"'}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        ward_vi = data.get("ward_vi")
        confidence = float(data.get("confidence", 0.0))
        if not ward_vi:
            return None
        return ward_vi, confidence
    except Exception as exc:
        log.debug("Haiku disambiguation failed for %r: %s", raw_name, exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_location(
    conn: sa.Connection,
    raw_name: str,
    client=None,            # anthropic.Anthropic — required for Haiku fallback
    alias_type_hint: str = "other",
) -> Optional[ResolvedLocation]:
    """
    Resolve a raw Vietnamese location string to a ward centroid.

    Args:
        conn: SQLAlchemy sync connection (must have ward_centroids + location_aliases)
        raw_name: Location string as extracted from article (any granularity)
        client: Anthropic client for Haiku disambiguation (None disables step 3)
        alias_type_hint: Suggested alias_type when storing a new LLM-resolved alias

    Returns:
        ResolvedLocation or None (caller should log ward_error)
    """
    if not raw_name or not raw_name.strip():
        return None

    normalized = normalize_ward(raw_name)

    # ── Step 1: ward_centroids (4-strategy) ──────────────────────────────────
    ward_row = _ward_lookup(conn, normalized)
    if ward_row:
        return ResolvedLocation(
            ward_name=ward_row["ward_name"],
            district=ward_row["district"],
            lat=ward_row["lat"],
            lng=ward_row["lng"],
            resolution_level="ward",
            confidence=1.0,
        )

    # ── Step 2: location_aliases (exact) ─────────────────────────────────────
    alias_row = conn.execute(_ALIAS_LOOKUP, {"alias_norm": normalized}).fetchone()
    if alias_row:
        return ResolvedLocation(
            ward_name=alias_row.ward_name,
            district=alias_row.district,
            lat=alias_row.lat,
            lng=alias_row.lng,
            resolution_level=alias_row.alias_type,
            confidence=alias_row.confidence,
        )

    # ── Step 2b: location_aliases (substring, longest wins) ───────────────────
    # FIR-73: catches "duong nguyen chi thanh cong dai truyen hinh viet nam"
    # → matches alias "nguyen chi thanh" inside the string.
    sub_row = conn.execute(_ALIAS_SUBSTRING_LOOKUP, {"normalized": normalized}).fetchone()
    if sub_row:
        return ResolvedLocation(
            ward_name=sub_row.ward_name,
            district=sub_row.district,
            lat=sub_row.lat,
            lng=sub_row.lng,
            resolution_level=sub_row.alias_type,
            confidence=sub_row.confidence * 0.9,  # slight penalty for substring vs exact
        )

    # ── Step 3: Haiku disambiguation ──────────────────────────────────────────
    if client is None:
        return None

    result = _haiku_disambiguate(client, normalized, raw_name)
    if result is None:
        return None

    ward_vi, confidence = result
    if confidence == 0.0:
        return None

    # Try to resolve the Haiku-suggested ward name
    guessed_norm = normalize_ward(ward_vi)
    ward_row = _ward_lookup(conn, guessed_norm)
    if not ward_row:
        log.debug("Haiku suggested %r but not in ward_centroids", ward_vi)
        return None

    # Determine source based on confidence threshold
    source = "llm" if confidence >= _CONFIDENCE_AUTO_ACCEPT else "pending"

    try:
        conn.execute(_ALIAS_INSERT, {
            "alias_norm": normalized,
            "alias_vi": raw_name,
            "ward_name": ward_row["ward_name"],
            "alias_type": alias_type_hint,
            "confidence": confidence,
            "source": source,
        })
        conn.commit()
    except Exception as exc:
        log.debug("Failed to store alias for %r: %s", raw_name, exc)

    if source == "pending":
        log.info("alias_pending | %r → %r (confidence=%.2f)", raw_name, ward_row["ward_name"], confidence)
        return None  # don't emit event until manually reviewed

    return ResolvedLocation(
        ward_name=ward_row["ward_name"],
        district=ward_row["district"],
        lat=ward_row["lat"],
        lng=ward_row["lng"],
        resolution_level="llm",
        confidence=confidence,
    )
