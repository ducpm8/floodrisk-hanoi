"""
Tier 4b-1: bare street names that have a district but no ward.

Target rows have no coordinates, were never processed (`coord_flag` NULL), have
`ward` NULL and a real `district`, and their `location_name` is a bare name
("Thái Hà", "Hoa Bằng", "Lệ Mật") — no Đường/Phố/... keyword and no leading
place keyword ("Cầu Giấy", "Khu đô thị ...").

This pass combines the two earlier scripts:
  - the bare-name handling of scripts/geocode_bare.py ("Phố X", "Đường X", "X"),
  - the district-zone validation of scripts/geocode_district.py (anchors from the
    old wards, 2 km for quận / 4 km for huyện, Photon biased to the district).

Classification (each row falls in exactly one bucket):
  A  plain street name                     -> geocoded on the street
  B  house number + street ("Số 165 X")    -> parent street, lower confidence
  C  street + district in parens ("X (Y)") -> parenthesised district checked
  D  street + landmark in parens           -> placed on the street (POI: 4b-2)
  E  two streets / an intersection         -> 4b-2, counted only
  F  generic area description              -> 4b-3, counted only
  G  only restates a ward / the district   -> no point, counted only

Validation: Hanoi bbox, distance to the nearest anchor of the district within
the district zone, tone-aware name match (Thiên != Thiện), highway results only,
no plot/alley labels. A point already placed for the same street in ANOTHER
district is reused when it falls inside this district's zone.

Confidence: 0.60 only for an A/C/D name that matched "Phố X"/"Đường X" and lies
within 1 km of an anchor; 0.55 otherwise (B, bare-name-only hits, cross-district
reuse). `coord_source='geocoded_street'`, ids are saved for rollback.

    python -m scripts.geocode_bare_district --select
    python -m scripts.geocode_bare_district --geocode
    python -m scripts.geocode_bare_district --report
    python -m scripts.geocode_bare_district --apply [--dry-run]
    python -m scripts.geocode_bare_district --rollback [--dry-run]

The Tier-1 Photon cache is shared; keys are "query@@district".
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import random
import re
import sys
from collections import Counter, defaultdict
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
from api.models.ward_centroid import WardCentroid  # noqa: E402
from api.utils.text import normalize_ward  # noqa: E402
from scripts.geocode_bare import (  # noqa: E402
    _AREA_RE,
    _LEAD_RE,
    _PARENS_RE,
    clean_name,
    queries_for_segment,
    segments_of,
)
from scripts.geocode_district import (  # noqa: E402
    PhotonDistrictClient,
    _null_conditions,
    build_anchors,
    centre_of,
    confidence_for,
    cross_district_reference,
    nearest_anchor_km,
    tone_match,
    zone_km,
)
from scripts.geocode_poi import is_generic_area  # noqa: E402
from scripts.geocode_tier1 import (  # noqa: E402
    GeocoderClient,
    NominatimCache,
    _invalidate_search_cache,
    default_paths,
    haversine_km,
    in_hanoi_bbox,
    label_match_rank,
    matches_street,
    street_alternates,
)
from scripts.reuse_street_points import is_reference, street_key  # noqa: E402

log = logging.getLogger("geocode_bare_district")

DATA_PATH = REPO_ROOT / "api" / "seeds" / "data" / "hanoi_wards_2025.json"
CACHE_PATH, _, _ = default_paths("photon")
REPORT_PATH = REPO_ROOT / ".gstack" / "geocode" / "bare_district_report.csv"
SUMMARY_PATH = REPO_ROOT / ".gstack" / "geocode" / "bare_district_summary.md"
IDS_PATH = REPO_ROOT / ".gstack" / "geocode" / "bare_district_applied_ids.csv"

_FAILED = ("geocode_failed", "too_far")
_SUFFIX = "@@"
_NEAR_ANCHOR_KM = 1.0

# Flat keyword set used when the target size was estimated. Kept only to audit
# what the refined filter below excludes.
_FLAT_KEYWORD_RE = re.compile(
    r"(?:đường|phố|ngõ|ngách|hẻm|đại lộ|quốc lộ|tỉnh lộ|\bql\b|\btl\b|đê|cầu|khu|"
    r"chung cư|bệnh viện|trường|chợ|công viên|hồ|ngã|tổ dân|thôn|xóm|phường|xã|"
    r"thị trấn)",
    re.IGNORECASE,
)
# A street-type word anywhere: those rows belong to geocode_district.
_ROAD_TYPE_RE = re.compile(
    r"(?:đường|phố|ngõ|ngách|hẻm|đại lộ|quốc lộ|tỉnh lộ|\bql\b|\btl\b|đê)",
    re.IGNORECASE,
)
# Leading place/admin words: "Cầu Giấy", "Khu đô thị", "Chợ Mơ", "Hồ Tây".
# Only LEADING, so surnames ("Phan Văn Trường") and "Thụy Khuê" stay in.
_LEADING_PLACE_RE = re.compile(
    r"^\s*(?:cầu|khu|chung cư|bệnh viện|trường|chợ|công viên|hồ|ngã|tổ dân|thôn|"
    r"xóm|phường|xã|thị trấn)\b",
    re.IGNORECASE,
)
_GENERIC_RE = re.compile(
    r"(?<!\w)(?:nhiều đoạn|một số đoạn|nhiều tuyến|một số tuyến|toàn tuyến|cả tuyến|"
    r"các tuyến|tuyến đường|đoạn đường|một đoạn|khu vực ngập|ngập gần|ngập sâu|"
    r"ngập \d|mức ngập|nước ngập|ngập úng)(?!\w)",
    re.IGNORECASE,
)
_INTERSECTION_RE = re.compile(
    r"(?<!\w)(?:giao|ngã ba|ngã tư|nút giao|nút|vòng xuyến)(?!\w)", re.IGNORECASE
)
# Leading words that describe where on the road, not which road.
_EXTRA_LEAD_RE = re.compile(
    r"^\s*(?:tuyến|dốc|gầm cầu|gầm chui|xe lửa|chân cầu vượt|đầu cầu vượt|chân cầu|"
    r"đầu cầu|cầu vượt|lối vào|lối ra|lối xuống|lối ra vào|vòng xuyến|qua|"
    r"trước cổng|sau cổng|cổng)\s+",
    re.IGNORECASE,
)
_TRAILING_EXTENSION_RE = re.compile(r"\s+kéo\s+dài$", re.IGNORECASE)
_TRAILING_DIRECTION_RE = re.compile(r"\s+(?:hướng|đến)\s+.*$", re.IGNORECASE)
_LEAD_NUMBER_SEGMENT_RE = re.compile(r"^\s*\d+[a-z]?\s*[–—-]\s*", re.IGNORECASE)
_HOUSE_NUMBER_RE = re.compile(
    r"^\s*(?:số\s*(?:nhà\s*)?|nhà\s*số\s*)?\d+[a-z]?(?:\s*/\s*\d+[a-z]?)*\s+",
    re.IGNORECASE,
)
_ADMIN_PREFIX_RE = re.compile(
    r"^(?:quan|huyen|thi xa|phuong|xa|thi tran)\s+"
)
# Plots and numbered alleys are not the street itself.
_PLOT_RE = re.compile(r"^\s*(?:ô|o)\s*\d+\b|\blô\s+\d+\b", re.IGNORECASE)

_CLASS_LABELS = {
    "A": "plain street",
    "B": "house number",
    "C": "street + district in parens",
    "D": "street + landmark in parens",
    "E": "two streets / intersection",
    "F": "generic area",
    "G": "ward/district restatement",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BDGroup:
    key: tuple
    district: str
    names: list[str]
    row_ids: list
    street: str
    classes: set[str] = field(default_factory=set)
    alternates: list[str] = field(default_factory=list)

    @property
    def cls(self) -> str:
        # A group mixing numbered rows and plain rows keeps the lower band.
        return "B" if "B" in self.classes else (sorted(self.classes)[0] if self.classes else "A")


@dataclass(frozen=True)
class Classified:
    cls: str
    street: str | None = None
    note: str = ""


@dataclass(frozen=True)
class Pick:
    lat: float
    lng: float
    anchor_km: float
    display_name: str
    osm_class: str = ""
    osm_type: str = ""


@dataclass
class BDOutcome:
    group: BDGroup
    status: str               # ok | geocode_failed | too_far | not_geocoded | no_anchors | error
    pick: Pick | None = None
    confidence: float | None = None
    method: str = ""          # photon | cross_district
    query: str = ""


# ---------------------------------------------------------------------------
# Target selection / classification
# ---------------------------------------------------------------------------

def is_db_target(row) -> bool:
    return (
        row.lat is None
        and row.coord_source is None
        and row.coord_flag is None
        and row.ward is None
        and bool(row.district)
        and row.district != "Unknown"
    )


def is_candidate_name(name: str | None) -> bool:
    """Bare name: no street type, no leading place keyword."""
    if not name:
        return False
    if _ROAD_TYPE_RE.search(name):
        return False
    if _LEADING_PLACE_RE.match(name):
        return False
    return True


def is_generic_name(name: str | None) -> bool:
    return bool(
        name
        and (is_generic_area(name) or _GENERIC_RE.search(name) or _AREA_RE.match(name))
    )


def admin_bare(value: str | None) -> str:
    return _ADMIN_PREFIX_RE.sub("", normalize_ward(value or "")).strip()


def ward_names_by_district(units: Sequence[Mapping]) -> dict[str, set[str]]:
    """district -> normalized current and old ward names (for class G)."""
    names: dict[str, set[str]] = defaultdict(set)
    for unit in units:
        predecessors = unit.get("predecessors", [])
        districts = {p.get("district_vi") for p in predecessors if p.get("district_vi")}
        for district in districts:
            if unit.get("name_vi"):
                names[district].add(normalize_ward(unit["name_vi"]))
                names[district].add(admin_bare(unit["name_vi"]))
        for pred in predecessors:
            district = pred.get("district_vi")
            if district and pred.get("name_vi"):
                names[district].add(normalize_ward(pred["name_vi"]))
                names[district].add(admin_bare(pred["name_vi"]))
    return dict(names)


def district_names(units: Sequence[Mapping]) -> set[str]:
    return {
        pred["district_vi"]
        for unit in units
        for pred in unit.get("predecessors", [])
        if pred.get("district_vi")
    }


def has_house_number(name: str) -> bool:
    return bool(_HOUSE_NUMBER_RE.match(name or ""))


def street_core(name: str, district: str | None = None) -> str:
    """'Số 165 Thái Hà (gần chợ)' -> 'Thái Hà'; leading notes are dropped."""
    value = clean_name(name)
    value = _TRAILING_EXTENSION_RE.sub("", value)
    value = _TRAILING_DIRECTION_RE.sub("", value)
    previous = None
    while previous != value:
        previous = value
        value = _LEAD_RE.sub("", value)
        value = _EXTRA_LEAD_RE.sub("", value)
        value = _HOUSE_NUMBER_RE.sub("", value, count=1)
        value = _LEAD_NUMBER_SEGMENT_RE.sub("", value)
        value = value.strip(" ,;-–")
    if district:
        # "Quang Trung Hà Đông" -> "Quang Trung" (the district is a suffix note).
        bare = admin_bare(district).split()
        words = value.split()
        if bare and len(words) > len(bare) and normalize_ward(" ".join(words[-len(bare):])) == " ".join(bare):
            value = " ".join(words[:-len(bare)]).strip(" ,;-–")
    return value


def _restates_admin(core: str, district: str, ward_names: Mapping[str, set[str]]) -> bool:
    if not core:
        return False
    key = normalize_ward(core)
    bare = admin_bare(core)
    known = ward_names.get(district, set())
    district_key = admin_bare(district)
    return (
        key == district_key
        or bare == district_key
        or key in known
        or bare in known
    )


def classify(
    name: str | None,
    district: str,
    ward_names: Mapping[str, set[str]] | None = None,
    known_districts: Iterable[str] | None = None,
) -> Classified:
    """One row -> exactly one class A-G."""
    ward_names = ward_names or {}
    known = {admin_bare(value) for value in (known_districts or ()) if value}
    if not name:
        return Classified("F")

    core = street_core(name, district)
    if _INTERSECTION_RE.search(_PARENS_RE.sub(" ", name)):
        return Classified("E")
    if len(segments_of(core)) >= 2 and not is_generic_name(name):
        return Classified("E")

    notes = [note.strip() for note in _PARENS_RE.findall(name) if note.strip()]
    # G only when the name is LITERALLY the ward/district: no house number, no
    # parenthetical note and no position word. "Dốc Minh Khai" is a street even
    # though a ward is also called Minh Khai.
    raw = re.sub(r"\s+", " ", clean_name(name)).strip(" ,;-–")
    if (
        not notes
        and not has_house_number(name)
        and _restates_admin(raw, district, ward_names)
    ):
        return Classified("G", raw)

    if is_generic_name(name):
        return Classified("F")

    district_key = admin_bare(district)
    is_c = is_d = mismatch = False
    for note in notes:
        note_key = admin_bare(note)
        if note_key and note_key in known:
            if note_key == district_key:
                is_c = True
            else:
                mismatch = True
        else:
            is_d = True
    if mismatch:
        # Street named with another district: do not place it here.
        return Classified("C", None, "district_mismatch")
    if not core:
        return Classified("F")
    if has_house_number(name):
        return Classified("B", core)
    if is_d and not is_c:
        return Classified("D", core)
    if is_c:
        return Classified("C", core)
    return Classified("A", core)


@dataclass
class ClassifiedRow:
    row: object
    cls: str
    street: str | None
    note: str = ""


def classify_rows(
    rows: Iterable,
    ward_names: Mapping[str, set[str]] | None = None,
    known_districts: Iterable[str] | None = None,
) -> list[ClassifiedRow]:
    result: list[ClassifiedRow] = []
    for row in rows:
        verdict = classify(row.location_name, row.district, ward_names, known_districts)
        result.append(ClassifiedRow(row, verdict.cls, verdict.street, verdict.note))
    return result


def build_groups(classified: Iterable[ClassifiedRow]) -> list[BDGroup]:
    """Class A-D rows grouped by (street key, district)."""
    groups: dict[tuple, BDGroup] = {}
    for item in classified:
        if item.cls not in ("A", "B", "C", "D") or not item.street:
            continue
        key = street_key(item.street)
        if not key:
            continue
        slot = (key, item.row.district)
        group = groups.get(slot)
        if group is None:
            street = item.street
            group = groups[slot] = BDGroup(
                slot, item.row.district, [], [], street,
                alternates=street_alternates(item.row.location_name, street, None),
            )
        group.row_ids.append(item.row.id)
        group.classes.add(item.cls)
        if item.row.location_name not in group.names:
            group.names.append(item.row.location_name)
    return sorted(groups.values(), key=lambda g: -len(g.row_ids))


# ---------------------------------------------------------------------------
# Queries / picking
# ---------------------------------------------------------------------------

def queries_for(street: str, district: str) -> list[tuple[str, bool]]:
    """(query@@district, used_a_street_prefix) pairs, most likely first."""
    pairs: list[tuple[str, bool]] = []
    for query, _ in queries_for_segment(street):
        pairs.append((f"{query}{_SUFFIX}{district}", query != street))
    return list(dict.fromkeys(pairs))


def confidence_for_pick(anchor_km: float, cls: str, used_prefix: bool) -> float:
    if cls == "B" or not used_prefix:
        return 0.55
    return confidence_for(anchor_km)


def pick_result(
    results: Sequence[Mapping],
    anchors: Sequence[tuple[float, float]],
    zone: float,
    street: str,
) -> tuple[Pick | None, bool]:
    """-> (best highway pick inside the zone, saw_a_match_outside_the_zone)."""
    candidates: list[tuple[int, float, Mapping]] = []
    saw_far = False
    for result in results:
        if str(result.get("class") or "") != "highway":
            continue
        label = str(result.get("label") or "")
        if not matches_street(street, label) or not tone_match(street, label):
            continue
        if _PLOT_RE.search(label):
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
        candidates.append((label_match_rank(street, label), distance, result))
    if not candidates:
        return None, saw_far
    candidates.sort(key=lambda item: (item[0], item[1]))
    _, distance, result = candidates[0]
    return (
        Pick(
            float(result["lat"]), float(result["lon"]), round(distance, 4),
            str(result.get("display_name") or ""),
            str(result.get("class") or ""), str(result.get("type") or ""),
        ),
        saw_far,
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(
    group: BDGroup,
    anchors: Mapping[str, Sequence[tuple[float, float]]],
    lookup,
    references: Mapping[tuple, list] | None = None,
) -> BDOutcome:
    points = anchors.get(group.district)
    if not points:
        return BDOutcome(group, "no_anchors")
    if references:
        cross = cross_district_reference(group, references, points)
        if cross:
            return BDOutcome(
                group, "ok",
                Pick(cross.lat, cross.lng, cross.anchor_km, cross.display_name),
                0.55, "cross_district",
            )
    zone = zone_km(group.district)
    saw_far = False
    last = ""
    for query, used_prefix in queries_for(group.street, group.district):
        last = query
        results = lookup(query)
        if results is None:
            return BDOutcome(group, "not_geocoded", query=query)
        if not results:
            continue
        pick, far = pick_result(results, points, zone, group.street)
        if pick:
            return BDOutcome(
                group, "ok", pick,
                confidence_for_pick(pick.anchor_km, group.cls, used_prefix),
                "photon", query,
            )
        saw_far = saw_far or far
    return BDOutcome(group, "too_far" if saw_far else "geocode_failed", query=last)


def geocode_group(client: GeocoderClient, group: BDGroup, anchors, references) -> BDOutcome:
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
# Report
# ---------------------------------------------------------------------------

_COLUMNS = [
    "district", "names", "rows", "street", "class", "status", "method",
    "lat", "lng", "anchor_km", "confidence", "hit",
]


def write_report(outcomes: Sequence[BDOutcome], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS)
        writer.writeheader()
        for outcome in outcomes:
            pick = outcome.pick
            writer.writerow({
                "district": outcome.group.district,
                "names": " | ".join(outcome.group.names)[:200],
                "rows": len(outcome.group.row_ids),
                "street": outcome.group.street,
                "class": outcome.group.cls,
                "status": outcome.status,
                "method": outcome.method,
                "lat": pick.lat if pick else "",
                "lng": pick.lng if pick else "",
                "anchor_km": pick.anchor_km if pick else "",
                "confidence": outcome.confidence or "",
                "hit": pick.display_name[:90] if pick else "",
            })


def format_summary(
    outcomes: Sequence[BDOutcome],
    class_rows: Mapping[str, int] | None = None,
    skipped_rows: Mapping[str, int] | None = None,
) -> str:
    groups: Counter[str] = Counter()
    rows: Counter[str] = Counter()
    for outcome in outcomes:
        groups[outcome.status] += 1
        rows[outcome.status] += len(outcome.group.row_ids)
    lines = [f"Bare-district groups: {len(outcomes)} ({sum(rows.values())} rows)", ""]
    for status in sorted(groups):
        lines.append(f"  {status:<14} {groups[status]:>4} groups / {rows[status]:>5} rows")
    conf = Counter(outcome.confidence for outcome in outcomes if outcome.status == "ok")
    lines.append("Confidence: " + ", ".join(f"{k}={v}" for k, v in sorted(conf.items())))
    methods = Counter(outcome.method for outcome in outcomes if outcome.status == "ok")
    lines.append("Method: " + ", ".join(f"{k}={v}" for k, v in methods.items()))
    classes = Counter(outcome.group.cls for outcome in outcomes if outcome.status == "ok")
    lines.append("Placed classes: " + ", ".join(f"{k}={v}" for k, v in sorted(classes.items())))
    if class_rows is not None:
        lines.append(
            "Rows by class: "
            + ", ".join(f"{key}={class_rows.get(key, 0)}" for key in "ABCDEFG")
        )
    if skipped_rows is not None:
        lines.append(
            "Not processed (rows): "
            + ", ".join(f"{key}={skipped_rows.get(key, 0)}" for key in "EFG")
        )
    return "\n".join(lines)


def write_summary(outcomes: Sequence[BDOutcome], path: Path, class_rows=None, skipped_rows=None) -> None:
    text = [format_summary(outcomes, class_rows, skipped_rows), ""]
    good = [outcome for outcome in outcomes if outcome.status == "ok" and outcome.pick]
    sample = random.Random(42).sample(good, k=min(20, len(good))) if good else []
    if sample:
        text.append("20 random placed groups:")
        for outcome in sample:
            pick = outcome.pick
            text.append(
                f"- {outcome.group.street} | {outcome.group.district} | class {outcome.group.cls} "
                f"| conf {outcome.confidence} | anchor {pick.anchor_km:.2f} km | {pick.display_name[:80]}"
            )
    top = sorted(outcomes, key=lambda o: -len(o.group.row_ids))[:10]
    if top:
        text += ["", "Top 10 groups by rows:"]
        for outcome in top:
            pick = outcome.pick
            where = (
                f"({pick.lat:.5f},{pick.lng:.5f}) anchor {pick.anchor_km:.2f} km"
                if pick else outcome.status
            )
            text.append(
                f"- {len(outcome.group.row_ids):>3} rows | {outcome.group.street} "
                f"| {outcome.group.district} | {where}"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(text) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# DB apply / rollback
# ---------------------------------------------------------------------------

def write_ids(changed: Sequence[tuple]) -> None:
    IDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with IDS_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "lat", "lng", "confidence", "district"])
        writer.writerows(changed)


def read_ids() -> list[str]:
    if not IDS_PATH.exists():
        return []
    with IDS_PATH.open(encoding="utf-8", newline="") as handle:
        return [row["id"] for row in csv.DictReader(handle)]


def apply_outcomes(
    session: Session, outcomes: Sequence[BDOutcome], apply: bool = True
) -> tuple[dict, list]:
    stats: Counter[str] = Counter()
    changed: list = []
    for outcome in outcomes:
        ids = outcome.group.row_ids
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            conditions = _null_conditions(chunk)
            if outcome.status == "ok" and outcome.pick and outcome.confidence is not None:
                lat, lng = round(outcome.pick.lat, 6), round(outcome.pick.lng, 6)
                statement = update(FloodEvent).where(*conditions).values(
                    lat=lat,
                    lng=lng,
                    geom=func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326).cast(
                        Geography(geometry_type="POINT", srid=4326)
                    ),
                    coord_source="geocoded_street",
                    coord_confidence=outcome.confidence,
                    coord_flag=None,
                )
                key = "geocoded"
                changed.extend(
                    (str(row_id), lat, lng, outcome.confidence, outcome.group.district)
                    for row_id in chunk
                )
            elif outcome.status in _FAILED:
                statement = update(FloodEvent).where(*conditions).values(coord_flag=outcome.status)
                key = outcome.status
            else:
                stats[f"skipped_{outcome.status}"] += len(chunk)
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


def rollback(session: Session, apply: bool = True) -> tuple[int, int]:
    ids = read_ids()
    total = 0
    for start in range(0, len(ids), 500):
        conditions = [
            FloodEvent.id.in_(ids[start:start + 500]),
            FloodEvent.coord_source == "geocoded_street",
            FloodEvent.ward.is_(None),
        ]
        if apply:
            total += session.execute(
                update(FloodEvent).where(*conditions).values(
                    lat=None, lng=None, geom=None,
                    coord_source=None, coord_confidence=None,
                )
            ).rowcount or 0
        else:
            total += session.execute(
                select(func.count()).select_from(FloodEvent).where(*conditions)
            ).scalar_one()
    if apply:
        session.commit()
    return total, len(ids)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load(session: Session):
    rows = list(session.execute(select(
        FloodEvent.id, FloodEvent.location_name, FloodEvent.ward, FloodEvent.district,
        FloodEvent.lat, FloodEvent.lng, FloodEvent.coord_source, FloodEvent.coord_flag,
        FloodEvent.coord_confidence,
    )).all())
    points = {
        ward.ward_name: (float(ward.lat), float(ward.lng))
        for ward in session.execute(select(WardCentroid)).scalars()
    }
    units = json.loads(DATA_PATH.read_text(encoding="utf-8"))["units"]
    anchors = build_anchors(units, points, rows)
    references: dict[tuple, list] = defaultdict(list)
    for row in rows:
        if is_reference(row):
            key = street_key(row.location_name)
            if key:
                references[key].append(
                    (row.district, float(row.lat), float(row.lng), float(row.coord_confidence))
                )
    return rows, anchors, references, ward_names_by_district(units), district_names(units)


def target_rows(rows: Iterable) -> list:
    return [row for row in rows if is_db_target(row) and is_candidate_name(row.location_name)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_select(
    base: Sequence,
    candidates: Sequence,
    classified: Sequence[ClassifiedRow],
    groups: Sequence[BDGroup],
    anchors: Mapping[str, Sequence[tuple[float, float]]],
) -> None:
    class_rows = Counter(item.cls for item in classified)
    print(f"District-only rows (DB target): {len(base)}")
    print(f"Bare-name rows (refined filter): {len(candidates)} in {len(groups)} groups")
    print(
        "Rows by class: "
        + ", ".join(f"{key}={class_rows.get(key, 0)}" for key in "ABCDEFG")
    )
    excluded = [row for row in base if not is_candidate_name(row.location_name)]
    flat = [row for row in base if _FLAT_KEYWORD_RE.search(row.location_name or "")]
    rescued = [row for row in flat if is_candidate_name(row.location_name)]
    print(f"\nKeyword rows excluded by the refined filter: {len(excluded)} (flat keyword rows: {len(flat)})")
    print(f"  of those, kept by the refined filter: {len(rescued)}")
    for row in rescued[:15]:
        print(f"    kept | {row.location_name[:60]} | {row.district}")
    for row in excluded[:15]:
        print(f"    skip | {row.location_name[:60]} | {row.district}")

    for cls in "ABCDEFG":
        samples = [item for item in classified if item.cls == cls][:15]
        print(f"\nClass {cls} — {_CLASS_LABELS[cls]} ({class_rows.get(cls, 0)} rows), 15 samples:")
        for item in samples:
            street = f" -> {item.street}" if item.street and item.street != item.row.location_name else ""
            note = f" ({item.note})" if item.note else ""
            print(f"  {item.row.location_name[:52]:<52} | {item.row.district}{street}{note}")

    print("\nTop 15 groups by rows:")
    for group in groups[:15]:
        anchor = anchors.get(group.district)
        where = f"anchors={len(anchor)}" if anchor else "NO ANCHORS"
        print(f"  {len(group.row_ids):>4} | {group.street[:40]:<40} | {group.district} | {group.cls} | {where}")

    missing = sorted({group.district for group in groups if group.district not in anchors})
    print(f"\nDistricts without anchors: {missing}")
    print("Read-only: stop here and review before --geocode.")


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
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    engine = create_engine(args.database_url)
    try:
        with Session(engine) as session:
            if args.rollback:
                changed, listed = rollback(session, apply=not args.dry_run)
                print(
                    f"{'DRY RUN: would clear' if args.dry_run else 'Cleared'} "
                    f"{changed} rows ({listed} ids)"
                )
                if not args.dry_run:
                    asyncio.run(_invalidate_search_cache())
                return 0

            rows, anchors, references, ward_names, districts = load(session)
            base = [row for row in rows if is_db_target(row)]
            classified = classify_rows(target_rows(rows), ward_names, districts)
            groups = build_groups(classified)
            if args.limit:
                groups = groups[: args.limit]

            if args.select or not (args.geocode or args.report or args.apply):
                print_select(
                    base, [item.row for item in classified], classified, groups, anchors
                )
                return 0

            class_rows = Counter(item.cls for item in classified)
            skipped_rows = Counter({key: class_rows[key] for key in "EFG"})
            cache = NominatimCache(args.cache)
            centres = {d: centre_of(points) for d, points in anchors.items() if points}

            if args.geocode:
                client = PhotonDistrictClient(
                    cache, centres, min_interval=args.min_interval, timeout=args.timeout
                )
                outcomes: list[BDOutcome] = []
                for idx, group in enumerate(groups, start=1):
                    outcomes.append(geocode_group(client, group, anchors, references))
                    if idx % 25 == 0 or idx == len(groups):
                        log.info(
                            "geocoded %d/%d groups (%d network calls)",
                            idx, len(groups), client.network_calls,
                        )
                        cache.save()
                cache.save(force=True)
                print(format_summary(outcomes, class_rows))
                return 0

            outcomes = [
                evaluate(group, anchors, cache.get, references) for group in groups
            ]
            if args.report:
                write_report(outcomes, REPORT_PATH)
                write_summary(outcomes, SUMMARY_PATH, class_rows)
                print(format_summary(outcomes, class_rows))
                print(f"\nCSV: {REPORT_PATH}\nSummary: {SUMMARY_PATH}")
                return 0

            stats, changed = apply_outcomes(session, outcomes, apply=not args.dry_run)
            if args.dry_run:
                session.rollback()
            else:
                write_ids(changed)
                asyncio.run(_invalidate_search_cache())
            print(format_summary(outcomes, class_rows, skipped_rows))
            print(f"\n{'DRY RUN — nothing written' if args.dry_run else 'APPLIED'}")
            for key, value in sorted(stats.items()):
                print(f"{key:<28} {value}")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
