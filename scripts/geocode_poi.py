"""
Offline geocoding for named places (Tier 2, sub-group 1).

Rows whose `location_name` is a named place rather than a street — thôn, làng,
cầu, hầm, khu đô thị, chung cư, sân vận động, bệnh viện, trường, chợ... — and
whose `ward` resolves to `ward_centroids`. Street names stay with
`scripts/geocode_tier1.py`; unnamed subdivisions (tổ dân phố, khu dân cư số N)
are flagged `generic_area` and left for later.

Reuses the Tier-1 plumbing (Photon client, resumable cache, ward index).
The only differences: no `osm_tag=highway` filter, a place-oriented query/label
matcher, `coord_source='geocoded_poi'`.

    python -m scripts.geocode_poi --select
    python -m scripts.geocode_poi --geocode
    python -m scripts.geocode_poi --report
    python -m scripts.geocode_poi --apply [--dry-run]
    python -m scripts.geocode_poi --rollback [--dry-run]

Files (gitignored): .gstack/geocode/poi_cache_photon.json, poi_report_photon.csv,
poi_summary_photon.md
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
from scripts.geocode_tier1 import (  # noqa: E402
    LAT_MAX,
    LAT_MIN,
    LNG_MAX,
    LNG_MIN,
    GeocoderClient,
    NominatimCache,
    PhotonClient,
    WardInfo,
    _invalidate_search_cache,
    haversine_km,
    in_hanoi_bbox,
    is_admin_only_name,
    is_street_name,
    load_ward_index,
    resolve_ward,
)

log = logging.getLogger("geocode_poi")

CACHE_PATH = REPO_ROOT / ".gstack" / "geocode" / "poi_cache_photon.json"
REPORT_PATH = REPO_ROOT / ".gstack" / "geocode" / "poi_report_photon.csv"
SUMMARY_PATH = REPO_ROOT / ".gstack" / "geocode" / "poi_summary_photon.md"

# Named-place keywords. Ordered longest-first so "sân vận động" beats "sân".
_POI_WORDS = (
    "khu đô thị",
    "sân vận động",
    "bệnh viện",
    "chung cư",
    "bến xe",
    "công viên",
    "khu tập thể",
    "nút giao",
    "hầm chui",
    "hầm",
    "cầu vượt",
    "cầu",
    "thôn",
    "xóm",
    "làng",
    "kđt",
    "svđ",
    "ktt",
    "trường",
    "chợ",
)
_WORDS_ALT = "|".join(re.escape(word) for word in _POI_WORDS)
POI_RE = re.compile(rf"(?<!\w)(?:{_WORDS_ALT})(?!\w)", re.IGNORECASE)
GENERIC_RE = re.compile(
    r"(?<!\w)(?:tổ dân phố|tdp|khu dân cư|cụm dân cư|"
    # Descriptions of unnamed roads: nothing a geocoder can find.
    r"các\s+tuyến\s+đường|đường\s+(?:giao\s+thông\s+)?nông\s+thôn|"
    r"đường\s+nội\s+(?:bộ|đồng|khu)|đường\s+liên\s+(?:thôn|xã)|đường\s+làng|"
    r"đường\s+chính\s+(?:qua|trong))(?!\w)",
    re.IGNORECASE,
)
_STREET_CUT_RE = re.compile(
    r"\s+(?:đường|phố|ngõ|ngách|hẻm|đại lộ|quốc lộ|tỉnh lộ)\b.*$", re.IGNORECASE
)
_FAILED_FLAGS = ("geocode_failed", "too_far")
# Roads that merely mention a bridge/place ("Cao tốc Pháp Vân – Cầu Giẽ"):
# a road, not a named place, so they are left for a later road tier.
ROAD_RE = re.compile(r"(?<!\w)(?:cao tốc|vành đai|đường gom|đường dẫn)(?!\w)", re.IGNORECASE)
_PARENS_RE = re.compile(r"\([^)]*\)")
_SEGMENT_RE = re.compile(r"\s+(?:và|\+|&)\s+|\s*[,;]\s*|\s+[-–—]\s+", re.IGNORECASE)
_NOTE_RE = re.compile(
    r"\s+(?:km\s*\d|đoạn\b|đối diện\b|gần\b|giao\b|tại\b|qua\b|từ\b|đến\b|"
    r"ngoài\b|trong\b|trên\b|dưới\b|khu vực\b|phường\b|xã\b|thị trấn\b|quận\b|huyện\b).*$",
    re.IGNORECASE,
)

# Photon `osm_key` ranking: real places first, roads last.
_PLACE_KEYS = {
    "place", "amenity", "building", "leisure", "tourism", "man_made",
    "railway", "shop", "landuse", "office", "public_transport", "bridge",
}
_BANDS = ((1.0, 0.85), (2.5, 0.70))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PoiGroup:
    location_name: str
    ward: str | None
    district: str | None
    ward_info: WardInfo
    core: str
    row_count: int = 1
    queries: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PoiPick:
    lat: float
    lng: float
    distance_km: float
    display_name: str
    osm_class: str
    osm_type: str


@dataclass
class PoiOutcome:
    group: PoiGroup
    status: str            # ok | geocode_failed | too_far | not_geocoded | error
    query: str = ""
    pick: PoiPick | None = None
    confidence: float | None = None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def is_generic_area(name: str | None) -> bool:
    return bool(name and GENERIC_RE.search(name))


# Words that may precede the POI keyword without changing which place it is
# ("Khu vực cổng Bệnh viện K", "Chân cầu Tây Mỗ"). Anything else in front
# ("Nhà dân tại", "Sân Miếu bản làng", "Dãy LK 23,") names something else.
_LEAD_NOISE = {
    "khu", "vuc", "cong", "truoc", "sau", "chan", "dau", "cuoi", "gan", "canh",
    "quanh", "xung", "ngay", "tai", "doan", "cac", "tuyen", "ven", "trong",
    "ngoai", "tren", "duoi", "o", "gia", "ra", "vao", "huong", "diem", "ben",
    "chao",
}


def _lead_is_noise(value: str) -> bool:
    match = POI_RE.search(value)
    if not match:
        return False
    return all(token in _LEAD_NOISE for token in normalize_ward(value[: match.start()]).split())


def is_named_poi(name: str | None, allow_street: bool = False) -> bool:
    """Named place: POI keyword present, not a street, not admin-only, not generic.

    `allow_street` admits names that ALSO mention a street ("Cầu 72II đường 423")
    — used for rows the street pass could not place.
    """
    if not name or is_admin_only_name(name):
        return False
    if is_street_name(name) and not allow_street:
        return False
    if is_generic_area(name) or ROAD_RE.search(name):
        return False
    value = _PARENS_RE.sub(" ", name)
    return bool(POI_RE.search(value)) and _lead_is_noise(value)


def poi_core(name: str) -> str:
    """'Thôn Kim Tiên (xã Thư Lâm)' -> 'Thôn Kim Tiên'; leading noise is dropped.

    Starts at the FIRST POI keyword, then keeps that clause only.
    """
    value = _PARENS_RE.sub(" ", name)
    match = POI_RE.search(value)
    if match:
        value = value[match.start():]
    value = _SEGMENT_RE.split(value)[0]
    # Keep the clause head; cut location notes ("... qua Đại lộ", "... xã X").
    head = POI_RE.match(value)
    rest = value[head.end():] if head else value
    rest = _NOTE_RE.sub("", rest)
    # "Cầu 72II đường 423" -> the bridge is "Cầu 72II"; the road is Tier 1's job.
    rest = _STREET_CUT_RE.sub("", rest)
    value = (value[: head.end()] if head else "") + rest
    return re.sub(r"\s+", " ", value).strip(" ,;-–")


def distinctive_tokens(core: str) -> list[str]:
    """Name tokens without the leading POI keyword ('Thôn Kim Tiên' -> kim, tien)."""
    head = POI_RE.match(core)
    rest = core[head.end():] if head else core
    return [token for token in normalize_ward(rest).split() if token]


def label_matches(core: str, label: str | None) -> bool:
    """Every distinctive name token must appear in the result label."""
    tokens = distinctive_tokens(core)
    if not tokens or not label:
        return False
    norm_label = normalize_ward(label)
    label_tokens = set(norm_label.split())
    compact = norm_label.replace(" ", "")
    # "72II" in the name vs "72 II" in the label.
    return all(
        token in label_tokens or (any(ch.isdigit() for ch in token) and len(token) >= 3 and token in compact)
        for token in tokens
    )


def build_queries(core: str, ward_vi: str | None) -> list[str]:
    ward_vi = (ward_vi or "").strip()
    queries = [core]
    if ward_vi:
        queries.append(f"{core} {ward_vi}")
    bare = " ".join(distinctive_tokens_raw(core))
    if bare and bare.casefold() != core.casefold() and len(bare.split()) >= 2:
        queries.append(f"{bare} {ward_vi}".strip())
    return list(dict.fromkeys(queries))


def distinctive_tokens_raw(core: str) -> list[str]:
    head = POI_RE.match(core)
    rest = core[head.end():] if head else core
    return rest.split()


def confidence_for(distance_km: float) -> float:
    for limit, value in _BANDS:
        if distance_km <= limit:
            return value
    return 0.55


def poi_category(core: str) -> str:
    """Rough kind of place, from the leading POI keyword."""
    head = POI_RE.match(core)
    word = normalize_ward(head.group(0)) if head else ""
    if word in ("thon", "xom", "lang"):
        return "village"
    if word in ("khu do thi", "kdt", "chung cu", "khu tap the", "ktt"):
        return "estate"
    if word == "benh vien":
        return "hospital"
    if word == "truong":
        return "school"
    if word == "cho":
        return "market"
    if word == "ben xe":
        return "bus_station"
    if word == "cong vien":
        return "park"
    if word in ("san van dong", "svd"):
        return "stadium"
    if word in ("cau", "cau vuot", "ham", "ham chui", "nut giao"):
        return "crossing"
    return ""


# category -> (osm_key, allowed osm_values or None for any value of that key)
_COMPATIBLE: dict[str, tuple[tuple[str, frozenset[str] | None], ...]] = {
    "village": (("place", None), ("landuse", frozenset({"residential"}))),
    "estate": (
        ("landuse", frozenset({"residential"})),
        ("building", frozenset({"apartments", "residential", "yes", "dormitory"})),
        ("place", frozenset({"neighbourhood", "quarter", "suburb", "locality"})),
    ),
    "hospital": (("amenity", frozenset({"hospital", "clinic", "doctors"})), ("building", frozenset({"hospital"}))),
    "school": (
        ("amenity", frozenset({"school", "kindergarten", "college", "university"})),
        ("building", frozenset({"school", "kindergarten", "college", "university"})),
    ),
    "market": (("amenity", frozenset({"marketplace"})), ("shop", None), ("landuse", frozenset({"retail"}))),
    "bus_station": (("amenity", frozenset({"bus_station"})), ("public_transport", None)),
    "park": (("leisure", frozenset({"park", "garden"})), ("landuse", frozenset({"recreation_ground"}))),
    "stadium": (("leisure", frozenset({"stadium", "pitch", "sports_centre"})),),
    "crossing": (
        ("man_made", frozenset({"bridge"})),
        ("bridge", None),
        ("highway", None),
        ("junction", None),
        ("railway", None),
        ("waterway", None),
    ),
}


_TRANSIT_STOPS = frozenset({"bus_stop", "platform", "bus_guideway", "traffic_signals"})


def is_compatible(category: str, result: Mapping) -> bool:
    """Reject hits of the wrong kind (a factory for an estate, a bus stop for a village)."""
    allowed = _COMPATIBLE.get(category)
    if allowed is None:
        return True
    key, value = result.get("class"), result.get("type")
    # A bus stop "before the bridge" is not the bridge.
    if category == "crossing" and key == "highway" and value in _TRANSIT_STOPS:
        return False
    return any(key == k and (values is None or value in values) for k, values in allowed)


def _class_rank(result: Mapping) -> int:
    key = result.get("class")
    if key in _PLACE_KEYS:
        return 0
    if key == "highway":
        return 2
    return 1


def pick_result(
    results: Sequence[Mapping], ward: WardInfo, core: str
) -> PoiPick | None:
    candidates: list[tuple[int, float, Mapping]] = []
    category = poi_category(core)
    for result in results:
        if result.get("class") == "boundary" or not is_compatible(category, result):
            continue
        label = str(result.get("label") or "")
        if not label_matches(core, label):
            continue
        try:
            lat, lng = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not in_hanoi_bbox(lat, lng):
            continue
        distance = haversine_km(lat, lng, ward.lat, ward.lng)
        if distance > ward.radius_km:
            continue
        candidates.append((_class_rank(result), distance, result))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    _, distance, result = candidates[0]
    return PoiPick(
        lat=float(result["lat"]),
        lng=float(result["lon"]),
        distance_km=round(distance, 4),
        display_name=str(result.get("display_name") or ""),
        osm_class=str(result.get("class") or ""),
        osm_type=str(result.get("type") or ""),
    )


def has_far_candidate(results: Sequence[Mapping], ward: WardInfo, core: str) -> bool:
    category = poi_category(core)
    for result in results:
        if result.get("class") == "boundary" or not is_compatible(category, result):
            continue
        if not label_matches(core, str(result.get("label") or "")):
            continue
        try:
            lat, lng = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not in_hanoi_bbox(lat, lng):
            return True
        if haversine_km(lat, lng, ward.lat, ward.lng) > ward.radius_km:
            return True
    return False


def group_poi(rows: Iterable, ward_index: Mapping[str, WardInfo]) -> list[PoiGroup]:
    groups: dict[tuple, PoiGroup] = {}
    for row in rows:
        failed_before = getattr(row, "coord_flag", None) in _FAILED_FLAGS
        if not is_named_poi(row.location_name, allow_street=failed_before):
            continue
        ward_info = resolve_ward(row.ward, ward_index)
        if ward_info is None:
            continue
        core = poi_core(row.location_name)
        if not distinctive_tokens(core):
            continue
        # "Cầu Giấy" in phường Cầu Giấy is the ward's own name, not a bridge.
        ward_bare = re.sub(r"^(?:phuong|xa|thi tran)\s+", "", ward_info.ward_name)
        if normalize_ward(core) == ward_bare:
            continue
        key = (row.location_name, row.ward, row.district)
        if key in groups:
            groups[key].row_count += 1
            continue
        groups[key] = PoiGroup(
            location_name=row.location_name,
            ward=row.ward,
            district=row.district,
            ward_info=ward_info,
            core=core,
            queries=build_queries(core, ward_info.ward_name_vi),
        )
    return sorted(
        groups.values(),
        key=lambda g: (-g.row_count, g.location_name, g.ward or "", g.district or ""),
    )


def generic_rows(rows: Iterable) -> list:
    return [
        row
        for row in rows
        # No is_street_name() check: "Tổ dân phố" contains the street word "phố".
        if row.location_name
        and not is_admin_only_name(row.location_name)
        and is_generic_area(row.location_name)
    ]


# ---------------------------------------------------------------------------
# Photon without the road filter
# ---------------------------------------------------------------------------

class PhotonPoiClient(PhotonClient):
    """Same normalisation as the street client, but searches every OSM object."""

    def _params(self, query: str) -> dict:
        return {
            "q": query,
            "limit": 10,
            "bbox": f"{LNG_MIN},{LAT_MIN},{LNG_MAX},{LAT_MAX}",
        }


# ---------------------------------------------------------------------------
# Geocoding / evaluation
# ---------------------------------------------------------------------------

def _evaluate(group: PoiGroup, lookup) -> PoiOutcome:
    """`lookup(query) -> results | None` (None = no data / network error)."""
    saw_far = False
    last_query = ""
    for query in group.queries:
        last_query = query
        results = lookup(query)
        if results is None:
            return PoiOutcome(group, "not_geocoded", query=query)
        if not results:
            continue
        pick = pick_result(results, group.ward_info, group.core)
        if pick is not None:
            return PoiOutcome(
                group, "ok", query=query, pick=pick,
                confidence=confidence_for(pick.distance_km),
            )
        saw_far = saw_far or has_far_candidate(results, group.ward_info, group.core)
    return PoiOutcome(group, "too_far" if saw_far else "geocode_failed", query=last_query)


def geocode_group(client: GeocoderClient, group: PoiGroup) -> PoiOutcome:
    failed = False

    def lookup(query: str):
        nonlocal failed
        results = client.search(query)
        if results is None:
            failed = True
        return results

    outcome = _evaluate(group, lookup)
    if failed and outcome.status != "ok":
        outcome.status = "error"
    return outcome


def evaluate_groups(groups: Sequence[PoiGroup], cache: NominatimCache) -> list[PoiOutcome]:
    return [_evaluate(group, cache.get) for group in groups]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_REPORT_COLUMNS = [
    "location_name", "ward", "district", "ward_name_vi", "row_count", "core", "query",
    "status", "display_name", "lat", "lng", "distance_km", "confidence",
    "osm_class", "osm_type",
]


def write_report(outcomes: Sequence[PoiOutcome], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_REPORT_COLUMNS)
        writer.writeheader()
        for outcome in outcomes:
            group, pick = outcome.group, outcome.pick
            writer.writerow(
                {
                    "location_name": group.location_name,
                    "ward": group.ward or "",
                    "district": group.district or "",
                    "ward_name_vi": group.ward_info.ward_name_vi,
                    "row_count": group.row_count,
                    "core": group.core,
                    "query": outcome.query,
                    "status": outcome.status,
                    "display_name": pick.display_name if pick else "",
                    "lat": pick.lat if pick else "",
                    "lng": pick.lng if pick else "",
                    "distance_km": pick.distance_km if pick else "",
                    "confidence": outcome.confidence or "",
                    "osm_class": pick.osm_class if pick else "",
                    "osm_type": pick.osm_type if pick else "",
                }
            )


def format_summary(outcomes: Sequence[PoiOutcome]) -> str:
    by_status: Counter[str] = Counter()
    rows_by_status: Counter[str] = Counter()
    for outcome in outcomes:
        by_status[outcome.status] += 1
        rows_by_status[outcome.status] += outcome.group.row_count
    lines = [
        f"POI groups: {len(outcomes)}",
        f"Rows covered: {sum(o.group.row_count for o in outcomes)}",
        "",
        "Group status:",
    ]
    for status in sorted(by_status):
        lines.append(f"  {status:<16} {by_status[status]:>4} groups / {rows_by_status[status]:>4} rows")
    ok = [o for o in outcomes if o.status == "ok" and o.pick]
    lines += ["", "Distance from ward centroid (ok hits):"]
    for label, low, high in (("<=0.5 km", 0, 0.5), ("0.5-1 km", 0.5, 1), ("1-2.5 km", 1, 2.5), ("2.5-5 km", 2.5, 5.01)):
        count = sum(1 for o in ok if low < o.pick.distance_km <= high or (low == 0 and o.pick.distance_km <= high))
        lines.append(f"  {label:<10} {count}")
    lines += ["", "OSM class of hits: " + ", ".join(f"{k}={v}" for k, v in Counter(o.pick.osm_class for o in ok).most_common())]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB apply / rollback
# ---------------------------------------------------------------------------

def group_filter(group: PoiGroup):
    conditions = [
        FloodEvent.lat.is_(None),
        FloodEvent.coord_source.is_(None),
        FloodEvent.location_name == group.location_name,
        FloodEvent.ward.is_(None) if group.ward is None else FloodEvent.ward == group.ward,
        FloodEvent.district.is_(None)
        if group.district is None
        else FloodEvent.district == group.district,
    ]
    return conditions


def _count_or_run(session: Session, statement, conditions, apply: bool) -> int:
    if apply:
        return session.execute(statement).rowcount or 0
    return session.execute(
        select(func.count()).select_from(FloodEvent).where(*conditions)
    ).scalar_one()


def apply_outcomes(
    session: Session,
    outcomes: Sequence[PoiOutcome],
    generic: Sequence,
    apply: bool = True,
) -> dict:
    stats: Counter[str] = Counter()
    for outcome in outcomes:
        conditions = group_filter(outcome.group)
        if outcome.status == "ok" and outcome.pick:
            pick = outcome.pick
            statement = update(FloodEvent).where(*conditions).values(
                lat=round(pick.lat, 6),
                lng=round(pick.lng, 6),
                geom=func.ST_SetSRID(
                    func.ST_MakePoint(round(pick.lng, 6), round(pick.lat, 6)), 4326
                ).cast(Geography(geometry_type="POINT", srid=4326)),
                coord_source="geocoded_poi",
                coord_confidence=outcome.confidence,
                coord_flag=None,
            )
            stats["geocoded"] += _count_or_run(session, statement, conditions, apply)
        elif outcome.status in ("geocode_failed", "too_far"):
            statement = update(FloodEvent).where(*conditions).values(coord_flag=outcome.status)
            stats[outcome.status] += _count_or_run(session, statement, conditions, apply)
        else:
            stats["not_geocoded_groups"] += 1

    ids = [row.id for row in generic]
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        conditions = [FloodEvent.id.in_(chunk), FloodEvent.lat.is_(None)]
        statement = update(FloodEvent).where(*conditions).values(coord_flag="generic_area")
        stats["generic_area"] += _count_or_run(session, statement, conditions, apply)
    if apply:
        session.commit()
    return dict(stats)


def rollback(session: Session, apply: bool = True) -> int:
    conditions = [FloodEvent.coord_source == "geocoded_poi"]
    statement = update(FloodEvent).where(*conditions).values(
        lat=None, lng=None, geom=None, coord_source=None, coord_confidence=None
    )
    count = _count_or_run(session, statement, conditions, apply)
    if apply:
        session.commit()
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_rows(session: Session) -> list:
    return list(
        session.execute(
            select(
                FloodEvent.id, FloodEvent.location_name, FloodEvent.ward,
                FloodEvent.district, FloodEvent.coord_flag,
            ).where(FloodEvent.lat.is_(None), FloodEvent.coord_source.is_(None))
        ).all()
    )


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
    parser.add_argument(
        "--database-url", default=settings.database_sync_url,
        help="Sync SQLAlchemy URL (default: DATABASE_SYNC_URL).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    engine = create_engine(args.database_url)
    try:
        with Session(engine) as session:
            if args.rollback:
                count = rollback(session, apply=not args.dry_run)
                print(f"{'DRY RUN: would clear' if args.dry_run else 'Cleared'} {count} rows")
                return 0
            rows = load_rows(session)
            groups = group_poi(rows, load_ward_index(session))
            if args.limit:
                groups = groups[: args.limit]
            cache = NominatimCache(args.cache)

            if args.select or not (args.geocode or args.report or args.apply):
                print(f"Named-POI groups: {len(groups)} ({sum(g.row_count for g in groups)} rows)")
                print(f"Generic-area rows (flag later): {len(generic_rows(rows))}")
                for group in groups[:25]:
                    print(f"  {group.row_count:>3} | {group.location_name[:60]} | {group.ward} | q={group.queries[0]}")
                return 0

            if args.geocode:
                client = PhotonPoiClient(cache, args.min_interval, args.timeout)
                outcomes = []
                for idx, group in enumerate(groups, start=1):
                    outcomes.append(geocode_group(client, group))
                    if idx % 25 == 0 or idx == len(groups):
                        log.info("geocoded %d/%d groups (%d network calls)", idx, len(groups), client.network_calls)
                        cache.save()
                cache.save(force=True)
                print(format_summary(outcomes))
                return 0

            outcomes = evaluate_groups(groups, cache)
            if args.report:
                write_report(outcomes, REPORT_PATH)
                SUMMARY_PATH.write_text(format_summary(outcomes), encoding="utf-8")
                print(format_summary(outcomes))
                print(f"\nCSV: {REPORT_PATH}")
                return 0

            stats = apply_outcomes(session, outcomes, generic_rows(rows), apply=not args.dry_run)
            if args.dry_run:
                session.rollback()
            else:
                asyncio.run(_invalidate_search_cache())
            print(format_summary(outcomes))
            print(f"\n{'DRY RUN — nothing written' if args.dry_run else 'APPLIED'}")
            for key in ("geocoded", "geocode_failed", "too_far", "generic_area", "not_geocoded_groups"):
                print(f"{key:<20} {stats.get(key, 0)}")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
