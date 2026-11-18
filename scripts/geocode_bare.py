"""
Offline geocoding for bare names (Tier 4a): the event has a `ward` that resolves
to `ward_centroids`, but `location_name` has no Đường/Phố/Thôn/... prefix
("Triều Khúc", "Hoa Bằng", "Phan Bội Châu – Lý Thường Kiệt").

Most are streets whose prefix was dropped. Strategy:
  - names that only restate the ward ("Tây Mỗ" in phường Tây Mỗ, "Khu vực Xuân
    Phương") are exceptions: coord_flag='ward_only', no coordinates;
  - one name  -> try "Phố X", "Đường X", "X" against Photon (road objects only);
  - "A – B"   -> geocode both; midpoint when both land close, else the one
                 nearer the ward; confidence is capped at 0.55;
  - same validation as Tier 1 (Hanoi bbox, ward radius, street-name match).

Provenance: coord_source='geocoded_street' with a LOW confidence (<= 0.70),
because the street type was inferred.

    python -m scripts.geocode_bare --select
    python -m scripts.geocode_bare --geocode
    python -m scripts.geocode_bare --report
    python -m scripts.geocode_bare --apply [--dry-run]

The Tier-1 Photon cache is reused (queries are keyed by text), so identical
"Phố X" lookups are free.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from geoalchemy2 import Geography
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.config import settings  # noqa: E402
from api.models.flood_event import FloodEvent  # noqa: E402
from api.utils.text import normalize_ward  # noqa: E402
from scripts.geocode_poi import POI_RE, is_generic_area  # noqa: E402
from scripts.geocode_tier1 import (  # noqa: E402
    GeocoderClient,
    NominatimCache,
    PhotonClient,
    WardInfo,
    _invalidate_search_cache,
    default_paths,
    has_candidate_outside_radius,
    haversine_km,
    is_admin_only_name,
    is_street_name,
    load_ward_index,
    pick_result,
    resolve_ward,
)

log = logging.getLogger("geocode_bare")

CACHE_PATH, _, _ = default_paths("photon")  # share the Tier-1 Photon cache
REPORT_PATH = REPO_ROOT / ".gstack" / "geocode" / "bare_report_photon.csv"
SUMMARY_PATH = REPO_ROOT / ".gstack" / "geocode" / "bare_summary_photon.md"

_PARENS_RE = re.compile(r"\([^)]*\)")
_SPLIT_RE = re.compile(r"\s+[–—-]\s+|\s+(?:và|&|\+)\s+|\s*[/,;]\s*", re.IGNORECASE)
_LEAD_RE = re.compile(
    r"^\s*(?:khu vực|cổng chào|đoạn|quanh|gần|trước|sau|đầu|cuối|ngã tư|ngã ba|nút giao)\s+",
    re.IGNORECASE,
)
# Areas, water bodies and groups are not streets: leave them for a later pass.
_AREA_RE = re.compile(
    r"^\s*(?:khu|hồ|sông|kênh|mương|các|cổng|cụm|công viên|vườn|bãi|đồng|nhà)\b", re.IGNORECASE
)
_ADMIN_SEGMENT_RE = re.compile(r"^\s*(?:phường|xã|thị trấn|quận|huyện)\b", re.IGNORECASE)
_MAX_WORDS = 5
_MAX_COMPOUND_KM = 2.5
_BANDS = ((1.0, 0.70), (2.5, 0.60))
_COMPOUND_CAP = 0.55


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BareGroup:
    location_name: str
    ward: str | None
    district: str | None
    ward_info: WardInfo
    segments: list[str]
    row_count: int = 1


@dataclass(frozen=True)
class SegmentPick:
    segment: str
    lat: float
    lng: float
    distance_km: float
    display_name: str
    osm_class: str = ""
    osm_type: str = ""


@dataclass
class BareOutcome:
    group: BareGroup
    status: str            # ok | geocode_failed | too_far | not_geocoded | error
    picks: list[SegmentPick] = field(default_factory=list)
    lat: float | None = None
    lng: float | None = None
    confidence: float | None = None
    compound: bool = False
    query: str = ""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def ward_bare(ward_name: str) -> str:
    return re.sub(r"^(?:phuong|xa|thi tran)\s+", "", ward_name)


def clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", _PARENS_RE.sub(" ", name)).strip(" ,;-–")


def segments_of(name: str) -> list[str]:
    """'Phan Bội Châu – Lý Thường Kiệt' -> ['Phan Bội Châu', 'Lý Thường Kiệt']."""
    result: list[str] = []
    for part in _SPLIT_RE.split(clean_name(name)):
        previous = None
        while previous != part:  # "Khu vực cổng chào X" -> "X"
            previous = part
            part = _LEAD_RE.sub("", part).strip(" ,;-–")
        if _AREA_RE.match(part):
            return []  # the whole name describes an area / group, not a street
        if not part or _ADMIN_SEGMENT_RE.match(part):
            continue
        words = part.split()
        if len(words) > _MAX_WORDS or any(any(ch.isdigit() for ch in w) for w in words):
            continue
        if part not in result:
            result.append(part)
    return result


def is_ward_restatement(name: str, ward_name: str) -> bool:
    """True when the name only repeats the ward ('Tây Mỗ' in phường Tây Mỗ)."""
    segments = [normalize_ward(s) for s in segments_of(name)]
    return bool(segments) and all(s == ward_bare(ward_name) for s in segments)


def is_bare_name(name: str | None) -> bool:
    """No street/POI keyword, not admin-only, not a generic area."""
    if not name or is_street_name(name) or is_admin_only_name(name):
        return False
    value = _PARENS_RE.sub(" ", name)
    return not POI_RE.search(value) and not is_generic_area(name)


def queries_for_segment(segment: str) -> list[tuple[str, str]]:
    """(query, street-for-matching) pairs, most likely first."""
    return [
        (f"Phố {segment}", f"Phố {segment}"),
        (f"Đường {segment}", f"Đường {segment}"),
        (segment, f"Phố {segment}"),
    ]


def confidence_for(distance_km: float, compound: bool = False) -> float:
    value = 0.55
    for limit, band in _BANDS:
        if distance_km <= limit:
            value = band
            break
    return min(value, _COMPOUND_CAP) if compound else value


def combine(picks: Sequence[SegmentPick], ward: WardInfo) -> tuple[float, float, float] | None:
    """Collapse per-segment picks into one point -> (lat, lng, distance_to_ward)."""
    if not picks:
        return None
    if len(picks) == 1:
        pick = picks[0]
        return pick.lat, pick.lng, pick.distance_km
    first, second = picks[0], picks[1]
    if haversine_km(first.lat, first.lng, second.lat, second.lng) <= _MAX_COMPOUND_KM:
        lat, lng = (first.lat + second.lat) / 2, (first.lng + second.lng) / 2
        return lat, lng, haversine_km(lat, lng, ward.lat, ward.lng)
    nearer = min(picks[:2], key=lambda p: p.distance_km)
    return nearer.lat, nearer.lng, nearer.distance_km


def group_bare(
    rows: Iterable, ward_index: Mapping[str, WardInfo]
) -> tuple[list[BareGroup], list]:
    """-> (geocodable groups, rows that only restate their ward)."""
    groups: dict[tuple, BareGroup] = {}
    restating: list = []
    for row in rows:
        if not is_bare_name(row.location_name):
            continue
        ward_info = resolve_ward(row.ward, ward_index)
        if ward_info is None:
            continue
        if is_ward_restatement(row.location_name, ward_info.ward_name):
            restating.append(row)
            continue
        # A segment that is the ward's own name is not a street ("Doãn Kế Thiện – Phú Diễn").
        segments = [
            s for s in segments_of(row.location_name)
            if normalize_ward(s) != ward_bare(ward_info.ward_name)
        ]
        if not segments:
            continue
        key = (row.location_name, row.ward, row.district)
        if key in groups:
            groups[key].row_count += 1
            continue
        groups[key] = BareGroup(
            row.location_name, row.ward, row.district, ward_info, segments[:2]
        )
    ordered = sorted(
        groups.values(),
        key=lambda g: (-g.row_count, g.location_name, g.ward or "", g.district or ""),
    )
    return ordered, restating


# ---------------------------------------------------------------------------
# Geocoding / evaluation
# ---------------------------------------------------------------------------

def _resolve_segment(segment: str, ward: WardInfo, lookup) -> tuple[str, SegmentPick | None, str]:
    """-> (state, pick, last_query); state in ok | none | far | missing."""
    saw_far = False
    last = ""
    for query, street in queries_for_segment(segment):
        last = query
        results = lookup(query)
        if results is None:
            return "missing", None, query
        if not results:
            continue
        pick = pick_result(results, ward.lat, ward.lng, ward.radius_km, street=street)
        if pick is not None:
            return (
                "ok",
                SegmentPick(
                    segment, pick.lat, pick.lng, pick.distance_km,
                    pick.display_name, pick.osm_class, pick.osm_type,
                ),
                query,
            )
        saw_far = saw_far or has_candidate_outside_radius(
            results, ward.lat, ward.lng, ward.radius_km, street=street
        )
    return ("far" if saw_far else "none"), None, last


def evaluate(group: BareGroup, lookup) -> BareOutcome:
    picks: list[SegmentPick] = []
    states: list[str] = []
    last = ""
    for segment in group.segments:
        state, pick, last = _resolve_segment(segment, group.ward_info, lookup)
        states.append(state)
        if pick:
            picks.append(pick)
    compound = len(group.segments) > 1
    if "missing" in states and not picks:
        return BareOutcome(group, "not_geocoded", compound=compound, query=last)
    point = combine(picks, group.ward_info)
    if point is None:
        status = "too_far" if "far" in states else "geocode_failed"
        return BareOutcome(group, status, compound=compound, query=last)
    lat, lng, distance = point
    return BareOutcome(
        group, "ok", picks=picks, lat=lat, lng=lng,
        confidence=confidence_for(distance, compound=compound),
        compound=compound, query=last,
    )


def geocode_group(client: GeocoderClient, group: BareGroup) -> BareOutcome:
    failed = False

    def lookup(query: str):
        nonlocal failed
        results = client.search(query)
        if results is None:
            failed = True
        return results

    outcome = evaluate(group, lookup)
    if failed and outcome.status != "ok":
        outcome.status = "error"
    return outcome


def evaluate_groups(groups: Sequence[BareGroup], cache: NominatimCache) -> list[BareOutcome]:
    return [evaluate(group, cache.get) for group in groups]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_COLUMNS = [
    "location_name", "ward", "district", "row_count", "segments", "status",
    "compound", "lat", "lng", "confidence", "hits",
]


def write_report(outcomes: Sequence[BareOutcome], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS)
        writer.writeheader()
        for o in outcomes:
            writer.writerow(
                {
                    "location_name": o.group.location_name,
                    "ward": o.group.ward or "",
                    "district": o.group.district or "",
                    "row_count": o.group.row_count,
                    "segments": " | ".join(o.group.segments),
                    "status": o.status,
                    "compound": int(o.compound),
                    "lat": round(o.lat, 6) if o.lat is not None else "",
                    "lng": round(o.lng, 6) if o.lng is not None else "",
                    "confidence": o.confidence or "",
                    "hits": " || ".join(f"{p.segment}: {p.display_name[:60]}" for p in o.picks),
                }
            )


def format_summary(outcomes: Sequence[BareOutcome], restating: int = 0) -> str:
    groups: Counter[str] = Counter()
    rows: Counter[str] = Counter()
    for o in outcomes:
        groups[o.status] += 1
        rows[o.status] += o.group.row_count
    lines = [f"Bare-name groups: {len(outcomes)}  (ward-restatement rows flagged: {restating})", ""]
    for status in sorted(groups):
        lines.append(f"  {status:<16} {groups[status]:>4} groups / {rows[status]:>4} rows")
    conf = Counter(o.confidence for o in outcomes if o.status == "ok")
    lines += ["", "Confidence: " + ", ".join(f"{k}={v}" for k, v in sorted(conf.items()))]
    lines.append(f"Compound hits: {sum(1 for o in outcomes if o.status == 'ok' and o.compound)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB apply
# ---------------------------------------------------------------------------

def group_conditions(group: BareGroup) -> list:
    return [
        FloodEvent.lat.is_(None),
        FloodEvent.coord_source.is_(None),
        FloodEvent.coord_flag.is_(None),
        FloodEvent.location_name == group.location_name,
        FloodEvent.ward.is_(None) if group.ward is None else FloodEvent.ward == group.ward,
        FloodEvent.district.is_(None)
        if group.district is None
        else FloodEvent.district == group.district,
    ]


def _run(session: Session, statement, conditions, apply: bool) -> int:
    if apply:
        return session.execute(statement).rowcount or 0
    return session.execute(
        select(func.count()).select_from(FloodEvent).where(*conditions)
    ).scalar_one()


def apply_outcomes(
    session: Session,
    outcomes: Sequence[BareOutcome],
    restating: Sequence,
    apply: bool = True,
) -> dict:
    stats: Counter[str] = Counter()
    for o in outcomes:
        conditions = group_conditions(o.group)
        if o.status == "ok" and o.lat is not None and o.lng is not None:
            lat, lng = round(o.lat, 6), round(o.lng, 6)
            statement = update(FloodEvent).where(*conditions).values(
                lat=lat,
                lng=lng,
                geom=func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326).cast(
                    Geography(geometry_type="POINT", srid=4326)
                ),
                coord_source="geocoded_street",
                coord_confidence=o.confidence,
                coord_flag=None,
            )
            stats["geocoded"] += _run(session, statement, conditions, apply)
        elif o.status in ("geocode_failed", "too_far"):
            statement = update(FloodEvent).where(*conditions).values(coord_flag=o.status)
            stats[o.status] += _run(session, statement, conditions, apply)
        else:
            stats["not_geocoded_groups"] += 1

    ids = [row.id for row in restating]
    for start in range(0, len(ids), 500):
        conditions = [
            FloodEvent.id.in_(ids[start:start + 500]),
            FloodEvent.lat.is_(None),
            FloodEvent.coord_flag.is_(None),
        ]
        statement = update(FloodEvent).where(*conditions).values(coord_flag="ward_only")
        stats["ward_only"] += _run(session, statement, conditions, apply)
    if apply:
        session.commit()
    return dict(stats)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_rows(session: Session) -> list:
    return list(
        session.execute(
            select(
                FloodEvent.id, FloodEvent.location_name, FloodEvent.ward, FloodEvent.district
            ).where(
                FloodEvent.lat.is_(None),
                FloodEvent.coord_source.is_(None),
                FloodEvent.coord_flag.is_(None),
            )
        ).all()
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    for flag in ("select", "geocode", "report", "apply"):
        mode.add_argument(f"--{flag}", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-interval", type=float, default=1.1)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument("--database-url", default=settings.database_sync_url)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    engine = create_engine(args.database_url)
    try:
        with Session(engine) as session:
            groups, restating = group_bare(load_rows(session), load_ward_index(session))
            if args.limit:
                groups = groups[: args.limit]
            cache = NominatimCache(args.cache)

            if args.select or not (args.geocode or args.report or args.apply):
                print(f"Bare-name groups: {len(groups)} ({sum(g.row_count for g in groups)} rows)")
                print(f"Ward-restatement rows (-> ward_only): {len(restating)}")
                for g in groups[:25]:
                    print(f"  {g.row_count:>3} | {g.location_name[:50]} | {g.ward} | {g.segments}")
                return 0

            if args.geocode:
                client = PhotonClient(cache, args.min_interval, args.timeout)
                outcomes: list[BareOutcome] = []
                for idx, group in enumerate(groups, start=1):
                    outcomes.append(geocode_group(client, group))
                    if idx % 25 == 0 or idx == len(groups):
                        log.info("geocoded %d/%d (%d network calls)", idx, len(groups), client.network_calls)
                        cache.save()
                cache.save(force=True)
                print(format_summary(outcomes, len(restating)))
                return 0

            outcomes = evaluate_groups(groups, cache)
            if args.report:
                write_report(outcomes, REPORT_PATH)
                SUMMARY_PATH.write_text(format_summary(outcomes, len(restating)), encoding="utf-8")
                print(format_summary(outcomes, len(restating)))
                print(f"\nCSV: {REPORT_PATH}")
                return 0

            stats = apply_outcomes(session, outcomes, restating, apply=not args.dry_run)
            if args.dry_run:
                session.rollback()
            else:
                asyncio.run(_invalidate_search_cache())
            print(format_summary(outcomes, len(restating)))
            print(f"\n{'DRY RUN — nothing written' if args.dry_run else 'APPLIED'}")
            for key in ("geocoded", "geocode_failed", "too_far", "ward_only", "not_geocoded_groups"):
                print(f"{key:<20} {stats.get(key, 0)}")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
