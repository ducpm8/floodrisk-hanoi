"""
Tier 3 step 3: geocode street names that have a district but no ward.

Targets are the same rows as scripts/reuse_street_points.py (street name,
district known, ward NULL, no coordinates, coord_flag NULL) that still have no
reusable reference point.

Validation uses a DISTRICT ZONE instead of a ward radius. A point is accepted
when it lies within ZONE_KM of an anchor of the row's district:
  - anchors = centroids of the old wards that belong to the district, taken from
    the `predecessors` of api/seeds/data/hanoi_wards_2025.json (old ward ->
    old district) matched to `ward_centroids`, plus wards already seen in that
    district in the events table;
  - zone = 2.0 km around an anchor for quận, 4.0 km for huyện.
Photon is biased towards the district centre so that "Đường Quang Trung" does
not resolve to another district.

Cross-district reuse (no network): a street already placed in ANOTHER district
is reused when that point also falls inside this district's zone (streets that
run along a border).

Confidence: 0.60 when within 1 km of an anchor, else 0.55.
coord_source = 'geocoded_street'; ids are saved for rollback.

    python -m scripts.geocode_district --select
    python -m scripts.geocode_district --geocode
    python -m scripts.geocode_district --report
    python -m scripts.geocode_district --apply [--dry-run]
    python -m scripts.geocode_district --rollback [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from geoalchemy2 import Geography
from sqlalchemy import create_engine, func, or_, select, update
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.config import settings  # noqa: E402
from api.models.flood_event import FloodEvent  # noqa: E402
from api.models.ward_centroid import WardCentroid  # noqa: E402
from api.utils.text import normalize_ward  # noqa: E402
from scripts.geocode_tier1 import (  # noqa: E402
    _STREET_TYPE_WORDS,
    LAT_MAX,
    LAT_MIN,
    LNG_MAX,
    LNG_MIN,
    GeocoderClient,
    NominatimCache,
    PhotonClient,
    _invalidate_search_cache,
    bare_street,
    clean_street,
    default_paths,
    haversine_km,
    in_hanoi_bbox,
    label_match_rank,
    matches_street,
    parent_street,
    street_alternates,
    street_tokens,
)
from scripts.reuse_street_points import (  # noqa: E402
    is_reference,
    is_target,
    street_key,
)

log = logging.getLogger("geocode_district")

DATA_PATH = REPO_ROOT / "api" / "seeds" / "data" / "hanoi_wards_2025.json"
CACHE_PATH, _, _ = default_paths("photon")
REPORT_PATH = REPO_ROOT / ".gstack" / "geocode" / "district_report_photon.csv"
SUMMARY_PATH = REPO_ROOT / ".gstack" / "geocode" / "district_summary_photon.md"
IDS_PATH = REPO_ROOT / ".gstack" / "geocode" / "district_applied_ids.csv"

ZONE_KM_QUAN = 2.0
ZONE_KM_HUYEN = 4.0
NEAR_ANCHOR_KM = 1.0
_FAILED = ("geocode_failed", "too_far")
_SUFFIX = "@@"


# ---------------------------------------------------------------------------
# Anchors / zones
# ---------------------------------------------------------------------------

def zone_km(district: str) -> float:
    return ZONE_KM_HUYEN if district.startswith(("Huyện", "Thị xã")) else ZONE_KM_QUAN


def build_anchors(
    units: Sequence[Mapping],
    ward_points: Mapping[str, tuple[float, float]],
    event_rows: Iterable,
) -> dict[str, list[tuple[float, float]]]:
    """district -> [(lat, lng)] of old-ward centroids belonging to it."""
    anchors: dict[str, set[tuple[float, float]]] = defaultdict(set)
    for unit in units:
        for pred in unit.get("predecessors", []):
            key = normalize_ward(pred["name_vi"])
            point = ward_points.get(key) or ward_points.get(key + " cu")
            if point and pred.get("district_vi"):
                anchors[pred["district_vi"]].add(point)
    for row in event_rows:
        # 'Unknown' is a placeholder from an early import, not a district.
        if row.ward in ward_points and row.district and row.district != "Unknown":
            anchors[row.district].add(ward_points[row.ward])
    return {district: sorted(points) for district, points in anchors.items()}


def nearest_anchor_km(lat: float, lng: float, anchors: Sequence[tuple[float, float]]) -> float:
    return min(haversine_km(lat, lng, a, b) for a, b in anchors)


def centre_of(anchors: Sequence[tuple[float, float]]) -> tuple[float, float]:
    lats = sorted(a for a, _ in anchors)
    lngs = sorted(b for _, b in anchors)
    return lats[len(lats) // 2], lngs[len(lngs) // 2]


def confidence_for(anchor_km: float) -> float:
    return 0.60 if anchor_km <= NEAR_ANCHOR_KM else 0.55


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DGroup:
    key: tuple
    district: str
    names: list[str]
    row_ids: list
    street: str
    parent: str | None
    alternates: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DPick:
    lat: float
    lng: float
    anchor_km: float
    display_name: str
    osm_class: str = ""
    osm_type: str = ""


@dataclass
class DOutcome:
    group: DGroup
    status: str               # ok | geocode_failed | too_far | not_geocoded | no_anchors | error
    pick: DPick | None = None
    confidence: float | None = None
    method: str = ""          # photon | cross_district
    query: str = ""


def attempts(group: DGroup) -> list[tuple[str, bool]]:
    order: list[tuple[str, bool]] = [(group.street, False)]
    if group.parent:
        order.append((group.parent, True))
    order.extend((alt, True) for alt in group.alternates)
    return order


def queries_for(street: str, district: str) -> list[str]:
    """Photon queries; the district rides in the cache key after '@@'."""
    queries = [street]
    bare = bare_street(street)
    tokens = street_tokens(bare)
    if bare and tokens and not all(t.isdigit() for t in tokens):
        queries.append(bare)
    return [f"{q}{_SUFFIX}{district}" for q in dict.fromkeys(queries) if q]


def build_groups(rows: Iterable) -> list[DGroup]:
    groups: dict[tuple, DGroup] = {}
    for row in rows:
        if not is_target(row):
            continue
        key = street_key(row.location_name)
        if not key:
            continue
        slot = (key, row.district)
        group = groups.get(slot)
        if group is None:
            street = clean_street(row.location_name)
            parent = parent_street(row.location_name)
            group = groups[slot] = DGroup(
                slot, row.district, [], [], street, parent,
                street_alternates(row.location_name, street, parent),
            )
        group.row_ids.append(row.id)
        if row.location_name not in group.names:
            group.names.append(row.location_name)
    return sorted(groups.values(), key=lambda g: -len(g.row_ids))


# ---------------------------------------------------------------------------
# Picking
# ---------------------------------------------------------------------------

_TONE_MARKS = {"̀", "́", "̃", "̉", "̣"}


def viet_word_key(word: str) -> tuple[str, str]:
    """(base letters, tone) so 'Hòe' == 'Hoè' but 'Thiên' != 'Thiện'.

    normalize_ward() drops every diacritic, which made 'Phố Thiên Đức' match
    'Phố Đinh Đức Thiện'. Tone placement variants of one word stay equal.
    """
    decomposed = unicodedata.normalize("NFD", word.casefold())
    tone = next((c for c in decomposed if c in _TONE_MARKS), "")
    base = "".join(c for c in decomposed if c not in _TONE_MARKS)
    return unicodedata.normalize("NFC", base), tone


def _distinctive_words(text: str) -> list[tuple[str, str]]:
    return [
        viet_word_key(word)
        for word in re.findall(r"\w+", text)
        if normalize_ward(word) not in _STREET_TYPE_WORDS
    ]


def tone_match(street: str, label: str) -> bool:
    """Every distinctive street word appears in the label's street part, tones included."""
    wanted = _distinctive_words(street)
    if not wanted:
        return True
    head = re.sub(r"\([^)]*\)", " ", label.split(",")[0])
    have = set(_distinctive_words(head))
    return all(key in have for key in wanted)


def pick_result(
    results: Sequence[Mapping],
    anchors: Sequence[tuple[float, float]],
    zone: float,
    street: str,
) -> tuple[DPick | None, bool]:
    """-> (best pick inside the district zone, saw_a_match_outside_the_zone)."""
    candidates: list[tuple[int, int, float, Mapping]] = []
    saw_far = False
    for result in results:
        label = str(result.get("label") or "")
        if not matches_street(street, label) or not tone_match(street, label):
            continue
        try:
            lat, lng = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not in_hanoi_bbox(lat, lng):
            saw_far = True
            continue
        distance = nearest_anchor_km(lat, lng, anchors)
        if distance > zone:
            saw_far = True
            continue
        preferred = 0 if result.get("class") in ("highway", "place") else 1
        candidates.append((preferred, label_match_rank(street, label), distance, result))
    if not candidates:
        return None, saw_far
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    _, _, distance, result = candidates[0]
    return (
        DPick(
            float(result["lat"]), float(result["lon"]), round(distance, 4),
            str(result.get("display_name") or ""),
            str(result.get("class") or ""), str(result.get("type") or ""),
        ),
        saw_far,
    )


def cross_district_reference(
    group: DGroup,
    references: Mapping[tuple, list[tuple[str, float, float, float]]],
    anchors: Sequence[tuple[float, float]],
) -> DPick | None:
    """Best placed point of the same street in ANOTHER district that falls in this zone."""
    best: tuple[float, float, float] | None = None
    for district, lat, lng, confidence in references.get(group.key[0], []):
        if district == group.district:
            continue
        distance = nearest_anchor_km(lat, lng, anchors)
        if distance <= zone_km(group.district) and (best is None or confidence > best[0]):
            best = (confidence, lat, lng)
    if best is None:
        return None
    _, lat, lng = best
    return DPick(lat, lng, round(nearest_anchor_km(lat, lng, anchors), 4), "reused from another district")


# ---------------------------------------------------------------------------
# Photon with district bias
# ---------------------------------------------------------------------------

class PhotonDistrictClient(PhotonClient):
    """Photon biased towards the district centre; cache key is 'query@@district'."""

    def __init__(self, cache, centres: Mapping[str, tuple[float, float]], **kwargs):
        super().__init__(cache, **kwargs)
        self.centres = centres

    def _params(self, query: str) -> dict:
        text, _, district = query.partition(_SUFFIX)
        params = {
            "q": text,
            "limit": 20,
            "bbox": f"{LNG_MIN},{LAT_MIN},{LNG_MAX},{LAT_MAX}",
            "osm_tag": "highway",
        }
        centre = self.centres.get(district)
        if centre:
            params.update(lat=centre[0], lon=centre[1], location_bias_scale=0.3)
        return params


def evaluate(
    group: DGroup,
    anchors: Mapping[str, Sequence[tuple[float, float]]],
    lookup,
    references: Mapping[tuple, list] | None = None,
) -> DOutcome:
    pts = anchors.get(group.district)
    if not pts:
        return DOutcome(group, "no_anchors")
    if references:
        cross = cross_district_reference(group, references, pts)
        if cross:
            # Same street, other district: a border street, so keep the lower band.
            return DOutcome(group, "ok", cross, 0.55, "cross_district")
    zone = zone_km(group.district)
    saw_far = False
    last = ""
    for street, fallback in attempts(group):
        for query in queries_for(street, group.district):
            last = query
            results = lookup(query)
            if results is None:
                return DOutcome(group, "not_geocoded", query=query)
            if not results:
                continue
            pick, far = pick_result(results, pts, zone, street)
            if pick:
                return DOutcome(group, "ok", pick, confidence_for(pick.anchor_km), "photon", query)
            saw_far = saw_far or far
    return DOutcome(group, "too_far" if saw_far else "geocode_failed", query=last)


def geocode_group(client: GeocoderClient, group: DGroup, anchors, references) -> DOutcome:
    failed = False

    def lookup(query: str):
        nonlocal failed
        results = client.search(query)
        if results is None:
            failed = True
        return results

    outcome = evaluate(group, anchors, lookup, references)
    if failed and outcome.status != "ok":
        outcome.status = "error"
    return outcome


# ---------------------------------------------------------------------------
# Report / apply
# ---------------------------------------------------------------------------

_COLUMNS = [
    "district", "names", "rows", "street", "status", "method", "lat", "lng",
    "anchor_km", "confidence", "hit",
]


def write_report(outcomes: Sequence[DOutcome], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS)
        writer.writeheader()
        for o in outcomes:
            p = o.pick
            writer.writerow({
                "district": o.group.district,
                "names": " | ".join(o.group.names)[:200],
                "rows": len(o.group.row_ids),
                "street": o.group.street,
                "status": o.status,
                "method": o.method,
                "lat": p.lat if p else "",
                "lng": p.lng if p else "",
                "anchor_km": p.anchor_km if p else "",
                "confidence": o.confidence or "",
                "hit": p.display_name[:90] if p else "",
            })


def format_summary(outcomes: Sequence[DOutcome]) -> str:
    groups: Counter[str] = Counter()
    rows: Counter[str] = Counter()
    for o in outcomes:
        groups[o.status] += 1
        rows[o.status] += len(o.group.row_ids)
    lines = [f"District groups: {len(outcomes)} ({sum(rows.values())} rows)", ""]
    for status in sorted(groups):
        lines.append(f"  {status:<14} {groups[status]:>4} groups / {rows[status]:>5} rows")
    methods = Counter(o.method for o in outcomes if o.status == "ok")
    lines.append("\nMethod: " + ", ".join(f"{k}={v}" for k, v in methods.items()))
    conf = Counter(o.confidence for o in outcomes if o.status == "ok")
    lines.append("Confidence: " + ", ".join(f"{k}={v}" for k, v in sorted(conf.items())))
    return "\n".join(lines)


def _null_conditions(ids: Sequence) -> list:
    return [
        FloodEvent.id.in_(ids),
        FloodEvent.lat.is_(None),
        FloodEvent.coord_source.is_(None),
        FloodEvent.ward.is_(None),
        or_(FloodEvent.coord_flag.is_(None), FloodEvent.coord_flag.in_(_FAILED)),
    ]


def apply_outcomes(session: Session, outcomes: Sequence[DOutcome], apply: bool = True) -> tuple[dict, list]:
    stats: Counter[str] = Counter()
    changed: list = []
    for o in outcomes:
        ids = o.group.row_ids
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            conditions = _null_conditions(chunk)
            if o.status == "ok" and o.pick and o.confidence is not None:
                lat, lng = round(o.pick.lat, 6), round(o.pick.lng, 6)
                statement = update(FloodEvent).where(*conditions).values(
                    lat=lat, lng=lng,
                    geom=func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326).cast(
                        Geography(geometry_type="POINT", srid=4326)),
                    coord_source="geocoded_street",
                    coord_confidence=o.confidence,
                    coord_flag=None,
                )
                key = "geocoded"
                changed.extend((str(i), lat, lng, o.confidence, o.group.district) for i in chunk)
            elif o.status in _FAILED:
                statement = update(FloodEvent).where(*conditions).values(coord_flag=o.status)
                key = o.status
            else:
                stats[f"skipped_{o.status}"] += len(chunk)
                continue
            if apply:
                stats[key] += session.execute(statement).rowcount or 0
            else:
                stats[key] += session.execute(
                    select(func.count()).select_from(FloodEvent).where(*conditions)
                ).scalar_one()
    if apply:
        session.commit()
    return dict(stats), changed


def write_ids(changed: Sequence[tuple]) -> None:
    IDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with IDS_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "lat", "lng", "confidence", "district"])
        writer.writerows(changed)


def rollback(session: Session, apply: bool = True) -> tuple[int, int]:
    if not IDS_PATH.exists():
        return 0, 0
    with IDS_PATH.open(encoding="utf-8", newline="") as handle:
        ids = [row["id"] for row in csv.DictReader(handle)]
    total = 0
    for start in range(0, len(ids), 500):
        conditions = [
            FloodEvent.id.in_(ids[start:start + 500]),
            FloodEvent.coord_source == "geocoded_street",
            FloodEvent.ward.is_(None),
        ]
        if apply:
            total += session.execute(update(FloodEvent).where(*conditions).values(
                lat=None, lng=None, geom=None, coord_source=None, coord_confidence=None)
            ).rowcount or 0
        else:
            total += session.execute(
                select(func.count()).select_from(FloodEvent).where(*conditions)
            ).scalar_one()
    if apply:
        session.commit()
    return total, len(ids)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load(session: Session):
    rows = list(session.execute(select(
        FloodEvent.id, FloodEvent.location_name, FloodEvent.ward, FloodEvent.district,
        FloodEvent.lat, FloodEvent.lng, FloodEvent.coord_source, FloodEvent.coord_flag,
        FloodEvent.coord_confidence,
    )).all())
    points = {
        w.ward_name: (float(w.lat), float(w.lng))
        for w in session.execute(select(WardCentroid)).scalars()
    }
    units = json.loads(DATA_PATH.read_text(encoding="utf-8"))["units"]
    anchors = build_anchors(units, points, rows)
    references: dict[tuple, list] = defaultdict(list)
    for row in rows:
        if is_reference(row):
            key = street_key(row.location_name)
            if key:
                references[key].append((row.district, float(row.lat), float(row.lng), float(row.coord_confidence)))
    return rows, anchors, references


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    for flag in ("select", "geocode", "report", "apply", "rollback"):
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
            if args.rollback:
                changed, listed = rollback(session, apply=not args.dry_run)
                print(f"{'DRY RUN: would clear' if args.dry_run else 'Cleared'} {changed} rows ({listed} ids)")
                if not args.dry_run:
                    asyncio.run(_invalidate_search_cache())
                return 0

            rows, anchors, references = load(session)
            groups = build_groups(rows)
            if args.limit:
                groups = groups[: args.limit]
            cache = NominatimCache(args.cache)
            centres = {d: centre_of(p) for d, p in anchors.items() if p}

            if args.select or not (args.geocode or args.report or args.apply):
                print(f"District groups: {len(groups)} ({sum(len(g.row_ids) for g in groups)} rows)")
                print("Anchors per district: " + ", ".join(f"{d}={len(p)}" for d, p in sorted(anchors.items(), key=lambda x: -len(x[1]))[:12]))
                missing = sorted({g.district for g in groups if g.district not in anchors})
                print(f"Districts without anchors: {missing}")
                return 0

            if args.geocode:
                client = PhotonDistrictClient(cache, centres, min_interval=args.min_interval, timeout=args.timeout)
                outcomes = []
                for idx, group in enumerate(groups, start=1):
                    outcomes.append(geocode_group(client, group, anchors, references))
                    if idx % 25 == 0 or idx == len(groups):
                        log.info("geocoded %d/%d groups (%d network calls)", idx, len(groups), client.network_calls)
                        cache.save()
                cache.save(force=True)
                print(format_summary(outcomes))
                return 0

            outcomes = [evaluate(g, anchors, cache.get, references) for g in groups]
            if args.report:
                write_report(outcomes, REPORT_PATH)
                SUMMARY_PATH.write_text(format_summary(outcomes), encoding="utf-8")
                print(format_summary(outcomes))
                print(f"\nCSV: {REPORT_PATH}")
                return 0

            stats, changed = apply_outcomes(session, outcomes, apply=not args.dry_run)
            if args.dry_run:
                session.rollback()
            else:
                write_ids(changed)
                asyncio.run(_invalidate_search_cache())
            print(format_summary(outcomes))
            print(f"\n{'DRY RUN — nothing written' if args.dry_run else 'APPLIED'}")
            for key, value in sorted(stats.items()):
                print(f"{key:<28} {value}")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
