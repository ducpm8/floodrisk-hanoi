"""
FIR-44 live test — depth enrichment against QDND 65-point flood article.
Runs AC1-AC4, prints JSON result for comment generation.
"""
import io
import json
import logging
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    stream=sys.stdout,
)

import requests
from bs4 import BeautifulSoup
import sqlalchemy as sa
from sqlalchemy import create_engine, text

from api.config import settings

TEST_URL = "https://www.qdnd.vn/xa-hoi/tin-tuc/danh-sach-65-diem-ung-ngap-do-mua-lon-tai-ha-noi-848473"
EVENT_DATE = None  # let Haiku extract date from article content

# ── AC1: Fetch article ────────────────────────────────────────────────────────
print("=== AC1: Fetching article ===")
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
try:
    resp = requests.get(TEST_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    # Remove nav/header/footer noise
    for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
        tag.decompose()
    content = soup.get_text(separator="\n", strip=True)
    content = "\n".join(line for line in content.splitlines() if line.strip())
    print(f"Fetched {len(content)} chars")
    print(f"First 300 chars: {content[:300]}")
except Exception as e:
    print(f"FETCH_ERROR: {e}")
    sys.exit(1)

# ── AC1: Ingest ───────────────────────────────────────────────────────────────
print("\n=== AC1: Running manual ingest ===")
from scripts.seed_production import process_content
ingest_stats = process_content(content[:100_000], EVENT_DATE, TEST_URL)
print(f"Ingest stats: {json.dumps(ingest_stats, ensure_ascii=False)}")

# ── AC2/AC3: Check NULL-depth count BEFORE enrichment ────────────────────────
engine = create_engine(settings.database_sync_url, echo=False)
with engine.connect() as conn:
    before_null = conn.execute(text(
        "SELECT COUNT(*) FROM flood_events WHERE source_url = :u AND depth_cm IS NULL"
    ), {"u": TEST_URL}).scalar()
    before_total = conn.execute(text(
        "SELECT COUNT(*) FROM flood_events WHERE source_url = :u"
    ), {"u": TEST_URL}).scalar()
    sample_rows = conn.execute(text(
        "SELECT location_name, ward, depth_cm, severity, event_date FROM flood_events "
        "WHERE source_url = :u LIMIT 1"
    ), {"u": TEST_URL}).fetchall()

print(f"\n=== AC3: Before enrichment ===")
print(f"Total events for URL: {before_total}")
print(f"depth_cm IS NULL:     {before_null}")
print(f"Sample rows:")
for r in sample_rows:
    print(f"  {r.location_name} | {r.ward} | depth={r.depth_cm} | sev={r.severity} | date={r.event_date}")

# ── AC2: Run enrichment ───────────────────────────────────────────────────────
print(f"\n=== AC2: Running enrichment ===")
from api.scrapers.enrichment_worker import run_enrichment

with engine.connect() as enrich_conn:
    enrich_stats = run_enrichment(enrich_conn, limit=100)
    enrich_conn.commit()
print(f"Enrichment stats: {json.dumps(enrich_stats, ensure_ascii=False, default=str)}")

# ── AC3: Check NULL-depth count AFTER enrichment ─────────────────────────────
with engine.connect() as conn:
    after_null = conn.execute(text(
        "SELECT COUNT(*) FROM flood_events WHERE source_url = :u AND depth_cm IS NULL"
    ), {"u": TEST_URL}).scalar()
    after_enriched = conn.execute(text(
        "SELECT COUNT(*) FROM flood_events WHERE source_url = :u AND depth_cm IS NOT NULL"
    ), {"u": TEST_URL}).scalar()
    enriched_rows = conn.execute(text(
        "SELECT location_name, ward, depth_cm, severity FROM flood_events "
        "WHERE source_url = :u AND depth_cm IS NOT NULL LIMIT 5"
    ), {"u": TEST_URL}).fetchall()

print(f"\n=== AC3: After enrichment ===")
print(f"depth_cm IS NULL after:     {after_null}")
print(f"depth_cm IS NOT NULL after: {after_enriched}")
print(f"Enriched rows (sample):")
for r in enriched_rows:
    print(f"  {r.location_name} | {r.ward} | depth={r.depth_cm} | sev={r.severity}")

print(f"\n=== SUMMARY ===")
print(json.dumps({
    "ingest": ingest_stats,
    "before": {"total": before_total, "null_depth": before_null},
    "after": {"null_depth": after_null, "enriched": after_enriched},
    "enrichment": enrich_stats,
}, ensure_ascii=False, default=str))
