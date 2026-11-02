"""
Spike: LLM extraction of flood locations from Vietnamese news articles.

Tests whether Claude Haiku can reliably extract structured flood event data
from a Vietnamese newspaper article, and whether extracted ward names resolve
to real entries in ward_centroids.

Usage:
    ANTHROPIC_API_KEY=sk-... python scripts/spike_nlp_extraction.py

Output:
    scripts/spike_nlp_extraction.md  — results report
    stdout                           — live per-article results

FIR-37
"""

import json
import os
import sys
import time
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import anthropic
import requests
import sqlalchemy as sa
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text

from api.config import settings
from api.utils.text import normalize_ward
from api.utils.ward_lookup import resolve_location

# ── Config ────────────────────────────────────────────────────────────────────

MODEL = "claude-haiku-4-5-20251001"
RESULTS_FILE = Path(__file__).parent / "spike_nlp_extraction.md"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# Test corpus: (url, description)
TEST_ARTICLES = [
    (
        "https://vietnamnet.vn/danh-sach-65-diem-ung-ngap-tren-dia-ban-ha-noi-2447657.html",
        "VietnamNet — 65 flood points in Hanoi (30/09/2025)",
    ),
]

# ── Extraction prompt ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Vietnamese flood event data extractor.
Given a Vietnamese news article about flooding in Hanoi, extract every distinct flood location mentioned.

For each location return a JSON object with:
- "ward": the ward/commune name exactly as written in the article (phường/xã/thị trấn/quận/huyện name, NOT the full address)
- "district": the district/quận/huyện if mentioned, else null
- "event_date": ISO date YYYY-MM-DD if determinable from article, else null
- "depth_cm": water/flood depth in centimetres if explicitly mentioned (e.g. "ngập 40cm", "sâu 0.5m" → 50), else null
- "rainfall_mm": rainfall amount in mm if mentioned for this location (e.g. "319,6mm"), else null
- "severity": one of "minor" / "moderate" / "severe" based on context:
    minor = light flooding, passable, short duration
    moderate = vehicles affected, roads blocked, depth 20-50cm
    severe = houses flooded, people evacuated, depth >50cm or >150mm rainfall
- "confidence": "high" if location is explicit, "medium" if inferred, "low" if uncertain
- "evidence": the exact Vietnamese phrase from the article that supports this extraction (max 100 chars)

Return a JSON array. If the article does not describe a specific flood event, return an empty array [].
Return ONLY the JSON array, no explanation."""

USER_TEMPLATE = """Article text:

{text}

Extract all Hanoi ward-level flood locations from the above."""

# ── Fetcher ───────────────────────────────────────────────────────────────────

def fetch_article(url: str) -> Optional[str]:
    """Fetch article text via requests + BeautifulSoup."""
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  FETCH ERROR: {exc}")
        return None

    soup = BeautifulSoup(resp.content.decode("utf-8", "replace"), "html.parser")

    # Remove nav/footer/script noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try article body selectors
    for sel in [
        "article .content",
        ".article-content",
        ".content-detail",
        ".article-body",
        "article",
        ".maincontent",
        "main",
    ]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            if len(text) > 200:
                return text

    # Fallback: full page text
    return soup.get_text(" ", strip=True)


# ── LLM extraction ────────────────────────────────────────────────────────────

def extract_events(client: anthropic.Anthropic, article_text: str) -> list[dict]:
    """Call Claude Haiku and parse the JSON response."""
    # Cap at 6000 chars to stay within token budget for a spike
    text_chunk = article_text[:6000]

    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": USER_TEMPLATE.format(text=text_chunk),
            }
        ],
        system=SYSTEM_PROMPT,
    )

    raw = message.content[0].text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"  JSON PARSE ERROR: {exc}\n  Raw: {raw[:300]}")
        return []


# ── Runner ────────────────────────────────────────────────────────────────────

def run_spike() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    engine = create_engine(settings.database_sync_url, echo=False)

    rows: list[dict] = []          # per-extraction result rows
    article_stats: list[dict] = []

    with engine.connect() as conn:
        for url, description in TEST_ARTICLES:
            print(f"\n{'─'*60}")
            print(f"Article: {description}")
            print(f"URL: {url}")

            # 1. Fetch
            text_body = fetch_article(url)
            if not text_body:
                print("  SKIP: fetch failed")
                article_stats.append({
                    "description": description,
                    "url": url,
                    "fetch_ok": False,
                    "extractions": 0,
                    "resolved": 0,
                })
                continue

            print(f"  Fetched {len(text_body)} chars")

            # 2. Extract
            time.sleep(0.5)  # rate limit
            extractions = extract_events(client, text_body)
            print(f"  Haiku extracted {len(extractions)} locations")

            if not extractions:
                article_stats.append({
                    "description": description,
                    "url": url,
                    "fetch_ok": True,
                    "extractions": 0,
                    "resolved": 0,
                })
                continue

            # 3. Resolve each extracted ward via shared resolve_location()
            resolved_count = 0
            for ext in extractions:
                ward_raw = ext.get("ward", "")
                resolution = resolve_location(conn, ward_raw, client=client) if ward_raw else None
                in_db = resolution is not None
                if in_db:
                    resolved_count += 1

                row = {
                    "article": description,
                    "ward_raw": ward_raw,
                    "district_raw": ext.get("district"),
                    "event_date": ext.get("event_date"),
                    "depth_cm": ext.get("depth_cm"),
                    "rainfall_mm": ext.get("rainfall_mm"),
                    "confidence": ext.get("confidence"),
                    "evidence": (ext.get("evidence") or "")[:120],
                    "normalized": normalize_ward(ward_raw) if ward_raw else "",
                    "in_ward_centroids": in_db,
                    "matched_as": resolution.ward_name if resolution else None,
                    "resolution_level": resolution.resolution_level if resolution else None,
                }
                rows.append(row)
                status = "✓" if in_db else "✗"
                severity = ext.get("severity") or "-"
                depth = f"{ext.get('depth_cm')}cm" if ext.get("depth_cm") else "-"
                rain = f"{ext.get('rainfall_mm')}mm" if ext.get("rainfall_mm") else "-"
                level = f"[{resolution.resolution_level}]" if resolution else ""
                matched = f"{resolution.ward_name} {level}" if resolution else "NOT FOUND"
                print(
                    f"  {status} {ward_raw!r:40s} → {matched:45s} "
                    f"| {severity:8s} | depth={depth:6s} | rain={rain}"
                )

            article_stats.append({
                "description": description,
                "url": url,
                "fetch_ok": True,
                "extractions": len(extractions),
                "resolved": resolved_count,
            })

    # ── Report ────────────────────────────────────────────────────────────────

    total_extracted = sum(s["extractions"] for s in article_stats)
    total_resolved = sum(s["resolved"] for s in article_stats)
    precision = (total_resolved / total_extracted * 100) if total_extracted else 0

    lines = [
        "# Spike: LLM flood location extraction (FIR-37)",
        "",
        f"Model: `{MODEL}`  |  Articles tested: {len(TEST_ARTICLES)}  |  Date: 2026-04-27",
        "",
        "## Results by article",
        "",
    ]

    for s in article_stats:
        fetch_str = "✓ fetched" if s["fetch_ok"] else "✗ fetch failed"
        lines.append(f"### {s['description']}")
        lines.append(f"`{s['url']}`")
        lines.append(f"- Fetch: {fetch_str}")
        lines.append(f"- Extractions: {s['extractions']}")
        lines.append(f"- Resolved in ward_centroids: {s['resolved']}")
        lines.append("")

    lines += [
        "## Extraction detail",
        "",
        "| Ward (raw) | Normalized | In DB | Date | Depth | Rainfall | Confidence | Evidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        in_db = "✓" if r["in_ward_centroids"] else "✗"
        lines.append(
            f"| {r['ward_raw']} | {r['normalized']} | {in_db} "
            f"| {r['event_date'] or '-'} | {r['depth_cm'] or '-'} cm "
            f"| {r['rainfall_mm'] or '-'} mm | {r['confidence']} | {r['evidence'][:60]} |"
        )

    lines += [
        "",
        "## Summary",
        "",
        f"- Total locations extracted: **{total_extracted}**",
        f"- Resolved to ward_centroids: **{total_resolved}** ({precision:.0f}%)",
        f"- Unresolved: **{total_extracted - total_resolved}**",
        "",
        "## Go / No-go",
        "",
    ]

    if precision >= 60:
        verdict = "**GO** — extraction quality sufficient for AC2."
        lines.append(verdict)
        lines.append("")
        lines.append(
            "Haiku correctly resolves ≥60% of extracted wards to ward_centroids. "
            "Proceed with AC2 news crawler. "
            "Unresolved wards should be logged as `ward_error` in seed_errors.log."
        )
    else:
        verdict = "**NO-GO** — precision too low for production seeding."
        lines.append(verdict)
        lines.append("")
        lines.append(
            f"Only {precision:.0f}% of extracted wards resolved. "
            "Consider: (1) prompt refinement, (2) fuzzy ward matching, "
            "or (3) supplementing with a Hanoi ward name dictionary before AC2."
        )

    report = "\n".join(lines) + "\n"
    RESULTS_FILE.write_text(report, encoding="utf-8")

    print(f"\n{'═'*60}")
    print(f"Extracted: {total_extracted}  |  Resolved: {total_resolved}  |  Precision: {precision:.0f}%")
    print(f"Verdict: {verdict}")
    print(f"Report → {RESULTS_FILE}")


if __name__ == "__main__":
    run_spike()
