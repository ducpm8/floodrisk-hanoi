"""
Seed ward_centroids from OSM Overpass API.

Fetches all admin_level=8 boundaries (wards/phường) within Hanoi and inserts
their centroids. Safe to re-run — uses ON CONFLICT DO NOTHING.

Usage:
    cd api/
    python -m seeds.seed_wards

Requirements:
    - DATABASE_SYNC_URL env var (or .env file)
    - PostgreSQL + PostGIS running
    - alembic upgrade head already run
"""

import logging
import os
import sys
import time

import requests
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from api.utils.text import normalize_ward  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Fetches all ward-level (admin_level=8) boundaries in Hanoi with their centroids.
# Hanoi's relation ID on OSM is 1972741.
# Bounding box covers the full extent of Hanoi municipality (post-2008 expansion).
# Extended west to 105.05 to include Ba Vì district.
# Extended north to 21.42 to include all of Sóc Sơn district.
# Extended south to 20.53 to include Phú Xuyên district.
# area(3601972741) is unreliable when Overpass hasn't indexed the area;
# bbox is always available and returns results immediately.
OVERPASS_QUERY = """
[out:json][timeout:120];
(
  relation["admin_level"="8"]["type"="boundary"]
    (20.53,105.05,21.42,106.05);
);
out center tags;
"""


def fetch_wards() -> list[dict]:
    log.info("Querying OSM Overpass API for Hanoi wards...")
    resp = requests.post(
        OVERPASS_URL,
        data={"data": OVERPASS_QUERY},
        headers={
            "Accept": "application/json",
            "User-Agent": "FloodRiskBot/1.0 (Hanoi ward seed; contact: tnhn@thoatnuochanoi.vn)",
        },
        timeout=180,
    )
    resp.raise_for_status()
    elements = resp.json().get("elements", [])
    log.info(f"Received {len(elements)} elements from Overpass")
    return elements


def parse_ward(element: dict) -> dict | None:
    tags = element.get("tags", {})
    center = element.get("center", {})

    lat = center.get("lat")
    lon = center.get("lon")
    if not lat or not lon:
        return None

    # Vietnamese ward name — prefer "name:vi", fall back to "name"
    name_vi = tags.get("name:vi") or tags.get("name", "")
    if not name_vi:
        return None

    # Filter: only keep wards inside Hà Nội municipality.
    # Tags checked: addr:province, is_in:province, addr:city, is_in.
    province = (
        tags.get("addr:province")
        or tags.get("is_in:province")
        or tags.get("addr:city")
        or tags.get("is_in", "")
    )
    if province and "Hà Nội" not in province and "Ha Noi" not in province:
        return None

    # District from "is_in:district" tag or parent tag if available
    district = (
        tags.get("is_in:district")
        or tags.get("addr:district")
        or tags.get("is_in")
        or "Unknown"
    )
    # Clean up district prefix (e.g. "Quận Đống Đa" → "Đống Đa")
    for prefix in ("Quận ", "Huyện ", "Thị xã "):
        if district.startswith(prefix):
            district = district[len(prefix):]
            break

    return {
        "ward_name": normalize_ward(name_vi),
        "ward_name_vi": name_vi,
        "district": district,
        "lat": float(lat),
        "lng": float(lon),
    }


def seed(database_url: str | None = None) -> int:
    url = database_url or os.environ.get(
        "DATABASE_SYNC_URL",
        "postgresql+psycopg2://flood:flood@localhost:5432/flooddb",
    )

    elements = fetch_wards()
    wards = [w for e in elements if (w := parse_ward(e)) is not None]
    log.info(f"Parsed {len(wards)} valid wards")

    if not wards:
        log.warning("No wards parsed — check Overpass query or network")
        return 0

    engine = create_engine(url)
    inserted = 0

    with engine.begin() as conn:
        for ward in wards:
            result = conn.execute(
                text("""
                    INSERT INTO ward_centroids (ward_name, ward_name_vi, district, lat, lng, geom)
                    VALUES (
                        :ward_name,
                        :ward_name_vi,
                        :district,
                        :lat,
                        :lng,
                        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                    )
                    ON CONFLICT (ward_name) DO NOTHING
                """),
                ward,
            )
            inserted += result.rowcount

    log.info(f"Inserted {inserted} new wards ({len(wards) - inserted} already existed)")
    engine.dispose()
    return inserted


if __name__ == "__main__":
    # Retry once on transient Overpass rate limit (HTTP 429)
    for attempt in range(2):
        try:
            count = seed()
            log.info(f"Done. {count} rows inserted.")
            break
        except requests.exceptions.HTTPError as exc:
            if exc.response.status_code == 429 and attempt == 0:
                log.warning("Overpass rate limited — waiting 60s and retrying...")
                time.sleep(60)
            else:
                raise
