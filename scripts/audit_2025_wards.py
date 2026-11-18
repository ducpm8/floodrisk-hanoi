"""
Read-only gap audit: 2025 Hà Nội admin units vs `ward_centroids`.

Context: Nghị quyết 1656/NQ-UBTVQH15 (effective 2025-07-01) replaced Hà Nội's
579 old commune/ward units with 126 new phường/xã. `ward_centroids` was seeded
from the older OSM boundaries, so post-2025 events often have no resolvable
centroid.

This script:
  1. parses the official 126-unit list (S1) and cross-checks it against the
     verified dataset in `api/seeds/data/hanoi_wards_2025.json`;
  2. diffs the 126 units against `ward_centroids` (present / missing /
     same-name collision / inconsistent existing name);
  3. joins NULL-coordinate `flood_events` rows to the units to rank them by
     affected row count;
  4. writes a CSV for manual review.

Read-only: it never writes to the database.

Usage:
    cd <repo root>
    python scripts/audit_2025_wards.py
    python scripts/audit_2025_wards.py --s1-json /tmp/s1_names.json  # offline
    python scripts/audit_2025_wards.py --csv scripts/audit_2025_wards.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Iterable

import requests
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.utils.text import normalize_ward  # noqa: E402
from api.utils.ward_lookup import resolve_location  # noqa: E402

log = logging.getLogger("audit_2025_wards")

S1_URL = (
    "https://luatvietnam.vn/hanh-chinh/"
    "danh-sach-126-xa-phuong-moi-cua-ha-noi-sau-sap-xep-dvhc-tu-01-7-2025-"
    "570-102695-article.html"
)
DEFAULT_DATASET = REPO_ROOT / "api" / "seeds" / "data" / "hanoi_wards_2025.json"
DEFAULT_CSV = REPO_ROOT / "scripts" / "audit_2025_wards.csv"

_ADMIN_PREFIXES = ("phuong ", "xa ", "thi tran ", "quan ", "huyen ")
_UNIT_LINE = re.compile(r"^(Phường|Xã|Thị trấn)\s+(.+?)\s*$")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def bare_name(name: str) -> str:
    """Normalize and strip the admin-unit prefix ("phuong hoan kiem" → "hoan kiem")."""
    norm = normalize_ward(name)
    for prefix in _ADMIN_PREFIXES:
        if norm.startswith(prefix):
            return norm[len(prefix):]
    return norm


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _stdout_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


# ---------------------------------------------------------------------------
# S1 — official 126-unit list
# ---------------------------------------------------------------------------

def parse_s1_names(html: str) -> list[str]:
    """Extract "Phường …" / "Xã …" lines from the article HTML."""
    text_only = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    text_only = re.sub(r"<style.*?</style>", " ", text_only, flags=re.S)
    text_only = re.sub(r"<[^>]+>", "\n", text_only)
    text_only = text_only.replace("&nbsp;", " ")
    found: list[str] = []
    for line in text_only.splitlines():
        line = line.strip()
        match = _UNIT_LINE.match(line)
        if match:
            found.append(f"{match.group(1)} {match.group(2)}")
    # Keep first occurrence, preserve document order.
    return list(dict.fromkeys(found))


def fetch_s1_names(url: str = S1_URL, timeout: int = 60) -> list[str]:
    log.info("Fetching official 126-unit list from %s", url)
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; FloodRiskAudit/1.0)"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return parse_s1_names(resp.text)


# ---------------------------------------------------------------------------
# Verified dataset (S2)
# ---------------------------------------------------------------------------

def load_dataset(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    units = data.get("units") or []
    if len(units) != 126:
        log.warning("Dataset %s has %d units (expected 126)", path, len(units))
    return data


# ---------------------------------------------------------------------------
# DB readers
# ---------------------------------------------------------------------------

def load_existing_wards(conn) -> dict[str, dict]:
    rows = conn.execute(
        text(
            "SELECT ward_name, ward_name_vi, lat::float AS lat, lng::float AS lng "
            "FROM ward_centroids"
        )
    ).fetchall()
    return {row.ward_name: dict(row._mapping) for row in rows}


def null_coord_ward_rows(conn) -> list[tuple[str, int]]:
    """Ward values on coordinate-less events, most frequent first."""
    rows = conn.execute(
        text(
            "SELECT ward, COUNT(*) AS n FROM flood_events "
            "WHERE lat IS NULL AND ward IS NOT NULL AND ward <> '' "
            "GROUP BY ward ORDER BY n DESC"
        )
    ).fetchall()
    return [(row.ward, row.n) for row in rows]


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def unit_name_index(units: Iterable[dict]) -> dict[str, str]:
    """normalized name / bare name / predecessor names → new unit name."""
    index: dict[str, str] = {}
    for unit in units:
        name = unit["name_vi"]
        index.setdefault(normalize_ward(name), name)
        index.setdefault(bare_name(name), name)
        for pred in unit.get("predecessors") or []:
            pred_name = pred.get("name_vi") or ""
            if not pred_name:
                continue
            index.setdefault(normalize_ward(pred_name), name)
            index.setdefault(bare_name(pred_name), name)
    return index


def status_for(unit: dict, existing: dict[str, dict]) -> str:
    row = existing.get(normalize_ward(unit["name_vi"]))
    if row is None:
        return "missing"
    if bare_name(row["ward_name_vi"]) == bare_name(unit["name_vi"]):
        return "present_same_name"
    # Same normalized key, different Vietnamese name: the old OSM row is
    # inconsistent (e.g. "xa o dien" ↔ "Đan Phượng"). Report, never overwrite.
    return "present_name_mismatch"


def predecessor_stats(
    unit: dict, existing: dict[str, dict]
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """Mean / median of the predecessor centroids that exist in the current DB."""
    points = []
    for pred in unit.get("predecessors") or []:
        row = existing.get(normalize_ward(pred.get("name_vi") or ""))
        if row is not None:
            points.append((row["lat"], row["lng"]))
    if not points:
        return None, None
    mean = (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )
    median = (
        statistics.median(p[0] for p in points),
        statistics.median(p[1] for p in points),
    )
    return mean, median


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_audit(
    database_url: str,
    dataset_path: Path,
    csv_path: Path,
    s1_names: list[str] | None,
) -> list[dict]:
    dataset = load_dataset(dataset_path)
    units = dataset["units"]

    if s1_names:
        s1_norm = {normalize_ward(n): n for n in s1_names}
        ds_norm = {normalize_ward(u["name_vi"]): u["name_vi"] for u in units}
        only_s1 = sorted(s1_norm[k] for k in set(s1_norm) - set(ds_norm))
        only_ds = sorted(ds_norm[k] for k in set(ds_norm) - set(s1_norm))
        if only_s1 or only_ds:
            log.warning("S1/dataset name mismatch — only S1: %s | only dataset: %s",
                        only_s1, only_ds)
        else:
            log.info("S1 list matches dataset names (%d units)", len(units))
        bare_s1 = {bare_name(n) for n in s1_names}
        bare_ds = {bare_name(u["name_vi"]) for u in units}
        if bare_s1 ^ bare_ds:
            log.warning("S1/dataset mismatch ignoring type prefix: %s", sorted(bare_s1 ^ bare_ds))

    engine = create_engine(database_url)
    with engine.connect() as conn:
        existing = load_existing_wards(conn)
        null_rows = null_coord_ward_rows(conn)

        # Rank unresolved event rows by unit.
        index = unit_name_index(units)
        rows_by_unit: dict[str, int] = {}
        unresolved_total = 0
        unresolved_rows = 0
        unmatched_values: list[tuple[str, int]] = []
        for value, count in null_rows:
            # Full runtime chain: ward_centroids (4 strategies) → location_aliases.
            if resolve_location(conn, value, client=None):
                continue
            unresolved_total += 1
            unresolved_rows += count
            unit_name = index.get(normalize_ward(value)) or index.get(bare_name(value))
            if unit_name:
                rows_by_unit[unit_name] = rows_by_unit.get(unit_name, 0) + count
            else:
                unmatched_values.append((value, count))

    report: list[dict] = []
    for unit in units:
        name = unit["name_vi"]
        norm = normalize_ward(name)
        row = existing.get(norm)
        status = status_for(unit, existing)
        pred_mean, pred_median = predecessor_stats(unit, existing)
        pred_mean_distance = (
            round(haversine_km(unit["lat"], unit["lng"], *pred_mean), 3)
            if pred_mean
            else None
        )
        pred_median_distance = (
            round(haversine_km(unit["lat"], unit["lng"], *pred_median), 3)
            if pred_median
            else None
        )
        if row:
            distance = round(
                haversine_km(unit["lat"], unit["lng"], row["lat"], row["lng"]), 3
            )
        else:
            distance = None
        report.append(
            {
                "code": unit["code"],
                "name_vi": name,
                "name_norm": norm,
                "type": unit.get("type", ""),
                "status": status,
                "db_ward_name": row["ward_name"] if row else "",
                "db_ward_name_vi": row["ward_name_vi"] if row else "",
                "new_lat": unit["lat"],
                "new_lng": unit["lng"],
                "db_lat": row["lat"] if row else "",
                "db_lng": row["lng"] if row else "",
                "distance_km": "" if distance is None else distance,
                "predecessor_mean_distance_km": (
                    "" if pred_mean_distance is None else pred_mean_distance
                ),
                "predecessor_median_distance_km": (
                    "" if pred_median_distance is None else pred_median_distance
                ),
                "null_coord_rows": rows_by_unit.get(name, 0),
                "predecessors": len(unit.get("predecessors") or []),
            }
        )

    priority = {"missing": 0, "present_name_mismatch": 1, "present_same_name": 2}
    report.sort(key=lambda r: (priority[r["status"]], -r["null_coord_rows"], r["name_norm"]))

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(report[0].keys()))
        writer.writeheader()
        writer.writerows(report)

    # --- summary -----------------------------------------------------------
    by_status: dict[str, int] = {}
    for row in report:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    print(f"\n126-unit audit — {dataset_path}")
    print(f"  source: {dataset.get('source')} {dataset.get('source_version')}")
    for status, count in sorted(by_status.items()):
        print(f"  {status:24s} {count}")
    far = [r for r in report if isinstance(r["predecessor_median_distance_km"], float)
           and r["predecessor_median_distance_km"] > 5]
    if far:
        print(f"  centroid >5km from predecessor median: {len(far)} (review)")
        for row in far[:10]:
            print(f"    - {row['name_vi']} "
                  f"(median {row['predecessor_median_distance_km']} km, "
                  f"mean {row['predecessor_mean_distance_km']} km)")
    print(f"\nUnresolved NULL-coord event values: {unresolved_total} "
          f"({unresolved_rows} rows)")
    top = [r for r in report if r["null_coord_rows"]]
    if top:
        print("Affected rows per unit (top 15):")
        for row in sorted(top, key=lambda r: -r["null_coord_rows"])[:15]:
            print(f"  {row['null_coord_rows']:5d}  {row['name_vi']} [{row['status']}]")
    if unmatched_values:
        print("Unresolved values not matched to a 2025 unit (top 15):")
        for value, count in unmatched_values[:15]:
            print(f"  {count:5d}  {value}")
    print(f"\nCSV written to {csv_path}")
    return report


def main(argv: list[str] | None = None) -> int:
    _stdout_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get(
        "DATABASE_SYNC_URL",
        "postgresql+psycopg2://flood:flood@localhost:5432/flooddb",
    ))
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--s1-url", default=S1_URL)
    parser.add_argument(
        "--s1-json",
        type=Path,
        help="Cached JSON list of S1 names (skips the network fetch).",
    )
    parser.add_argument("--no-s1", action="store_true", help="Skip the S1 list check.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    s1_names: list[str] | None = None
    if not args.no_s1:
        if args.s1_json:
            s1_names = json.loads(args.s1_json.read_text(encoding="utf-8"))
        else:
            try:
                s1_names = fetch_s1_names(args.s1_url)
            except requests.RequestException as exc:
                log.warning("Could not fetch S1 list (%s); continuing with dataset names", exc)

    if s1_names is not None and len(s1_names) != 126:
        log.warning("S1 list has %d names (expected 126)", len(s1_names))

    run_audit(args.database_url, args.dataset, args.csv, s1_names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
