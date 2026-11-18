"""
Tier 4b-2 trial: intersections and two connected streets (class E).

This pass is READ-ONLY. It samples class-E groups and tries to place a point AT
THE JUNCTION instead of somewhere along one road:

  step 1  OSM geometry via Overpass: shared node, segment intersection, or a
          midpoint when the two roads pass within 150 m;
  step 2  Photon queries ("Ngã tư A B", "Nút giao A B", "A B", "Ngã ba A B"),
          accepted only when the label contains BOTH street names.

There is no --apply yet. Results go to a CSV/Markdown/GeoJSON trial report for
review; the database is not written.

    python -m scripts.geocode_intersection --check-network
    python -m scripts.geocode_intersection --select
    python -m scripts.geocode_intersection --sample 30 --seed 42 --fetch
    python -m scripts.geocode_intersection --sample 30 --seed 42 --report

Overpass is often blocked from this network (see LOCAL_DEV_ISSUES.md). When no
mirror answers, the trial runs step 2 only and says so in the summary.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.config import settings  # noqa: E402
from api.utils.text import normalize_ward  # noqa: E402
from scripts.geocode_bare import _PARENS_RE, clean_name, is_generic_area  # noqa: E402
from scripts.geocode_bare_district import (  # noqa: E402
    _INTERSECTION_RE,
    classify_rows,
    load,
    street_core,
    target_rows,
)
from scripts.geocode_district import (  # noqa: E402
    PhotonDistrictClient,
    centre_of,
    nearest_anchor_km,
    tone_match,
    viet_word_key,
    zone_km,
)
from scripts.geocode_tier1 import (  # noqa: E402
    _STREET_TYPE_WORDS,
    LAT_MAX,
    LAT_MIN,
    LNG_MAX,
    LNG_MIN,
    NominatimCache,
    bare_street,
    default_paths,
    haversine_km,
    in_hanoi_bbox,
    matches_street,
    street_tokens,
)
from scripts.reuse_street_points import street_key  # noqa: E402

log = logging.getLogger("geocode_intersection")

OVERPASS_CACHE_PATH = REPO_ROOT / ".gstack" / "geocode" / "overpass_cache.json"
CSV_PATH = REPO_ROOT / ".gstack" / "geocode" / "intersection_trial.csv"
SUMMARY_PATH = REPO_ROOT / ".gstack" / "geocode" / "intersection_trial_summary.md"
GEOJSON_PATH = REPO_ROOT / ".gstack" / "geocode" / "intersection_trial.geojson"
CACHE_PATH, _, _ = default_paths("photon")

OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
USER_AGENT = (
    "FloodRiskHanoi/1.0 (offline batch geocoding; "
    "+https://github.com/ducpm8/floodrisk-hanoi)"
)
_PROBE_QUERY = (
    "[out:json][timeout:10];"
    "way(21.030,105.790,21.036,105.798)[highway][name];out tags 1;"
)

_SUFFIX = "@@"
_NEAR_MIDPOINT_KM = 0.15
_AMBIGUOUS_KM = 1.0
_CONFIDENCE = {
    "osm_intersection": 0.70,
    "osm_near_midpoint": 0.60,
    "photon_junction": 0.60,
}

_PREFIX_RE = re.compile(
    r"^\s*(?:đoạn(?:\s+giao)?|trục|nút\s+giao(?:\s+thông)?|nút|"
    r"ngã\s+(?:tư|ba|năm|sáu|bảy)|giao\s+lộ|giao|vòng\s+xuyến)\s+",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(
    r"\s*[–—]\s*|\s+-\s+|\s+(?:và|với|giao)\s+|\s*/\s*", re.IGNORECASE
)
_NOT_STREET_RE = re.compile(
    r"^\s*(?:cầu|khu|chợ|trường|bệnh viện|công viên|sông|kênh|mương|vườn|bãi|đồng|"
    r"sân|bến|ga|hầm|tòa|toà|chung cư|kđt|khu đô thị|nhà|thôn|xã|phường|quận|huyện|"
    r"ngõ|ngách|hẻm)\b",
    re.IGNORECASE,
)
_JUNCTION_LABEL_RE = re.compile(
    r"(?<!\w)(?:ngã\s+tư|ngã\s+ba|ngã\s+năm|nút\s+giao|giao\s+lộ|cầu\s+vượt|"
    r"vòng\s+xuyến|junction|crossing)(?!\w)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class EphGroup:
    key: tuple
    district: str
    street_a: str
    street_b: str
    key_a: tuple
    key_b: tuple
    names: list[str] = field(default_factory=list)
    row_ids: list = field(default_factory=list)
    note: str = ""

    @property
    def rows(self) -> int:
        return len(self.row_ids)


@dataclass
class InvalidE:
    row: object
    status: str
    detail: str = ""


@dataclass(frozen=True)
class EPick:
    lat: float
    lng: float
    anchor_km: float
    method: str
    confidence: float
    detail: str = ""


@dataclass
class EOutcome:
    group: EphGroup
    status: str          # ok | not_found | no_intersection | ambiguous | too_far |
                         # not_geocoded | overpass_error | not_fetched | no_anchors
    pick: EPick | None = None
    note: str = ""


@dataclass
class ParsedPair:
    street_a: str | None
    street_b: str | None
    key_a: tuple | None
    key_b: tuple | None
    status: str = "ok"   # ok | not_pair | multi_segment | segment_not_street | same_street
    detail: str = ""
    note: str = ""


# ---------------------------------------------------------------------------
# Splitting A / B
# ---------------------------------------------------------------------------

def _strip_prefixes(value: str) -> str:
    previous = None
    while previous != value:
        previous = value
        value = _PREFIX_RE.sub("", value).strip(" ,;-–—")
    return value


def _is_street_segment(segment: str) -> bool:
    if not segment:
        return False
    if _NOT_STREET_RE.match(segment) or is_generic_area(segment):
        return False
    return bool(street_tokens(segment))


def parse_pair(name: str | None, district: str) -> ParsedPair:
    """'Nút giao A – B (note)' -> ('A', 'B', note)."""
    if not name:
        return ParsedPair(None, None, None, None, "not_pair", "empty")
    notes = [note.strip("() ").strip() for note in _PARENS_RE.findall(name) if note.strip("() ")]
    note = "; ".join(notes)

    value = _strip_prefixes(clean_name(name))
    segments = [part.strip(" ,;-–—") for part in _SPLIT_RE.split(value)]
    segments = [part for part in segments if part]
    if len(segments) < 2:
        return ParsedPair(None, None, None, None, "not_pair", value, note)
    if len(segments) > 2:
        return ParsedPair(
            None, None, None, None, "multi_segment", " | ".join(segments), note
        )

    left, right = (street_core(part, district) for part in segments)
    key_a, key_b = street_key(left), street_key(right)
    for segment in (left, right):
        if not _is_street_segment(segment):
            return ParsedPair(
                None, None, None, None, "segment_not_street", segment, note
            )
    if not key_a or not key_b:
        return ParsedPair(None, None, None, None, "segment_not_street", value, note)
    if key_a == key_b:
        return ParsedPair(left, right, key_a, key_b, "same_street", left, note)
    # Canonical order so "A – B" and "B – A" are one group.
    if key_b < key_a:
        left, right = right, left
        key_a, key_b = key_b, key_a
    return ParsedPair(left, right, key_a, key_b, "ok", "", note)


def build_groups(e_rows: Sequence) -> tuple[list[EphGroup], list[InvalidE]]:
    groups: dict[tuple, EphGroup] = {}
    invalid: list[InvalidE] = []
    for item in e_rows:
        row = item.row
        pair = parse_pair(row.location_name, row.district)
        if pair.status != "ok":
            invalid.append(InvalidE(row, pair.status, pair.detail))
            continue
        slot = (frozenset((pair.key_a, pair.key_b)), row.district)
        group = groups.get(slot)
        if group is None:
            group = groups[slot] = EphGroup(
                slot, row.district, pair.street_a, pair.street_b,
                pair.key_a, pair.key_b, note=pair.note,
            )
        group.row_ids.append(row.id)
        if row.location_name not in group.names:
            group.names.append(row.location_name)
        if pair.note and pair.note not in group.note:
            group.note = "; ".join(filter(None, (group.note, pair.note)))
    ordered = sorted(groups.values(), key=lambda g: (-g.rows, g.district, g.street_a))
    return ordered, invalid


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def sample_groups(
    groups: Sequence[EphGroup], count: int = 30, seed: int = 42, per_district: int = 4
) -> list[EphGroup]:
    rng = random.Random(seed)
    buckets: dict[str, list[EphGroup]] = defaultdict(list)
    for group in groups:
        buckets[group.district].append(group)
    districts = sorted(buckets)
    rng.shuffle(districts)
    for district in districts:
        rng.shuffle(buckets[district])

    picked: list[EphGroup] = []
    used: Counter[str] = Counter()
    progress = True
    while progress and len(picked) < count:
        progress = False
        for district in districts:
            if len(picked) >= count:
                break
            if used[district] >= per_district or not buckets[district]:
                continue
            picked.append(buckets[district].pop())
            used[district] += 1
            progress = True
    return sorted(picked, key=lambda g: (g.district, g.street_a, g.street_b))


# ---------------------------------------------------------------------------
# Overpass probe / client
# ---------------------------------------------------------------------------

def probe_overpass(timeout: int = 10) -> tuple[str | None, list[tuple[str, str]]]:
    """-> (working mirror or None, [(mirror, result)])."""
    results: list[tuple[str, str]] = []
    for url in OVERPASS_MIRRORS:
        try:
            response = requests.post(
                url,
                data={"data": _PROBE_QUERY},
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            detail = f"HTTP {response.status_code}"
            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, dict) and "elements" in payload:
                    results.append((url, "ok"))
                    return url, results
                    # (return keeps the first working mirror)
            results.append((url, detail))
        except (requests.RequestException, ValueError) as exc:
            results.append((url, f"{type(exc).__name__}: {exc}"))
        time.sleep(1.1)
    return None, results


class JsonCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, object] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    self.data = payload.get("queries", payload)
            except (OSError, ValueError) as exc:
                log.warning("Ignoring unreadable cache %s: %s", path, exc)

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "queries": self.data}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)


def parse_ways(payload) -> list[dict]:
    """Keep only named highway ways with nodes + geometry."""
    ways: list[dict] = []
    if not isinstance(payload, dict):
        return ways
    for element in payload.get("elements", []):
        if not isinstance(element, dict) or element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        name = tags.get("name")
        geometry = element.get("geometry") or []
        if not name or tags.get("highway") is None or len(geometry) < 2:
            continue
        ways.append({
            "id": element.get("id"),
            "name": str(name),
            "nodes": element.get("nodes") or [],
            "geometry": [
                {"lat": float(point["lat"]), "lon": float(point["lon"])}
                for point in geometry
                if "lat" in point and "lon" in point
            ],
        })
    return ways


def district_bbox(
    anchors: Sequence[tuple[float, float]], district: str
) -> tuple[float, float, float, float]:
    zone = zone_km(district)
    lats = [lat for lat, _ in anchors]
    lngs = [lng for _, lng in anchors]
    mean_lat = sum(lats) / len(lats)
    pad_lat = zone / 111.0
    pad_lng = zone / (111.0 * max(0.2, math.cos(math.radians(mean_lat))))
    return (
        round(min(lats) - pad_lat, 5), round(min(lngs) - pad_lng, 5),
        round(max(lats) + pad_lat, 5), round(max(lngs) + pad_lng, 5),
    )


def bbox_key(bbox: tuple[float, float, float, float]) -> str:
    return "ways:" + ",".join(f"{value:.5f}" for value in bbox)


class OverpassClient:
    def __init__(self, cache: JsonCache, url: str, min_interval: float = 1.1,
                 timeout: int = 60) -> None:
        self.cache = cache
        self.url = url
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0
        self.network_calls = 0

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def ways_for(self, bbox: tuple[float, float, float, float]) -> list[dict] | None:
        key = bbox_key(bbox)
        cached = self.cache.get(key)
        if isinstance(cached, list):
            return cached
        south, west, north, east = bbox
        query = (
            f"[out:json][timeout:{self.timeout}];"
            f"way[highway][name]({south},{west},{north},{east});"
            "out body geom;"
        )
        self._throttle()
        try:
            response = requests.post(
                self.url,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            log.warning("Overpass failed for %s: %s", key, exc)
            return None
        self.network_calls += 1
        if response.status_code != 200:
            log.warning("Overpass HTTP %s for %s", response.status_code, key)
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        ways = parse_ways(payload)
        self.cache.put(key, ways)
        return ways


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _project(lat: float, lng: float, lat0: float) -> tuple[float, float]:
    return lng * math.cos(math.radians(lat0)), lat


def _unproject(x: float, y: float, lat0: float) -> tuple[float, float]:
    return y, x / math.cos(math.radians(lat0))


def segment_intersection(p1, p2, p3, p4):
    """Proper crossing of two 2D segments -> (x, y), else None."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-15:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / denom
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return x1 + t * (x2 - x1), y1 + t * (y2 - y1)
    return None


def _closest_on_segment(point, start, end):
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    if length <= 1e-18:
        return start
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return ax + t * dx, ay + t * dy


def closest_pair(way_a: Mapping, way_b: Mapping) -> tuple[tuple[float, float], float] | None:
    """Closest point between two polylines -> ((lat, lng), km)."""
    a = [(float(p["lat"]), float(p["lon"])) for p in way_a["geometry"]]
    b = [(float(p["lat"]), float(p["lon"])) for p in way_b["geometry"]]
    if len(a) < 2 or len(b) < 2:
        return None
    lat0 = (a[0][0] + b[0][0]) / 2
    a2 = [_project(lat, lng, lat0) for lat, lng in a]
    b2 = [_project(lat, lng, lat0) for lat, lng in b]
    best: tuple[float, tuple[float, float]] | None = None
    for points, others in ((a2, b2), (b2, a2)):
        for point in points:
            for start, end in zip(others, others[1:]):
                candidate = _closest_on_segment(point, start, end)
                dx, dy = point[0] - candidate[0], point[1] - candidate[1]
                distance = dx * dx + dy * dy
                if best is None or distance < best[0]:
                    best = (distance, ((point[0] + candidate[0]) / 2,
                                       (point[1] + candidate[1]) / 2))
    if best is None:
        return None
    lat, lng = _unproject(best[1][0], best[1][1], lat0)
    return (lat, lng), math.sqrt(best[0]) * 111.32


def way_name_matches(street: str, name: str) -> bool:
    return matches_street(street, name) and tone_match(street, name)


def matching_ways(street: str, ways: Sequence[Mapping]) -> list[Mapping]:
    return [way for way in ways if way_name_matches(street, str(way.get("name") or ""))]


def shared_nodes(way_a: Mapping, way_b: Mapping) -> list[tuple[float, float]]:
    nodes_a = way_a.get("nodes") or []
    nodes_b = way_b.get("nodes") or []
    geometry_a = way_a.get("geometry") or []
    geometry_b = way_b.get("geometry") or []
    index_a = {node: i for i, node in enumerate(nodes_a)}
    points: list[tuple[float, float]] = []
    for i, node in enumerate(nodes_b):
        if node not in index_a:
            continue
        ai = index_a[node]
        if ai < len(geometry_a) and i < len(geometry_b):
            points.append((float(geometry_a[ai]["lat"]), float(geometry_a[ai]["lon"])))
    return points


def intersection_points(way_a: Mapping, way_b: Mapping) -> list[tuple[float, float]]:
    points = shared_nodes(way_a, way_b)
    geometry_a = way_a.get("geometry") or []
    geometry_b = way_b.get("geometry") or []
    if len(geometry_a) < 2 or len(geometry_b) < 2:
        return points
    lat0 = (float(geometry_a[0]["lat"]) + float(geometry_b[0]["lat"])) / 2
    a2 = [_project(float(p["lat"]), float(p["lon"]), lat0) for p in geometry_a]
    b2 = [_project(float(p["lat"]), float(p["lon"]), lat0) for p in geometry_b]
    for a_start, a_end in zip(a2, a2[1:]):
        for b_start, b_end in zip(b2, b2[1:]):
            hit = segment_intersection(a_start, a_end, b_start, b_end)
            if hit:
                points.append(_unproject(hit[0], hit[1], lat0))
    return points


def _dedupe(points: Sequence[tuple[float, float]], metres: float = 10.0) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in points:
        if all(haversine_km(point[0], point[1], other[0], other[1]) * 1000 > metres for other in result):
            result.append(point)
    return result


def osm_intersection(
    group: EphGroup,
    ways: Sequence[Mapping],
    anchors: Sequence[tuple[float, float]],
) -> tuple[EPick | None, str]:
    matched_a = matching_ways(group.street_a, ways)
    matched_b = matching_ways(group.street_b, ways)
    if not matched_a or not matched_b:
        return None, "not_found"

    zone = zone_km(group.district)
    candidates: list[tuple[float, float, float]] = []
    for way_a in matched_a:
        for way_b in matched_b:
            for lat, lng in intersection_points(way_a, way_b):
                if not in_hanoi_bbox(lat, lng):
                    continue
                distance = nearest_anchor_km(lat, lng, anchors)
                if distance <= zone:
                    candidates.append((lat, lng, distance))
    candidates_points = _dedupe([(lat, lng) for lat, lng, _ in candidates])
    if candidates_points:
        ranked = sorted(
            ((nearest_anchor_km(lat, lng, anchors), lat, lng) for lat, lng in candidates_points)
        )
        best_km, lat, lng = ranked[0]
        if len(ranked) > 1 and haversine_km(lat, lng, ranked[1][1], ranked[1][2]) > _AMBIGUOUS_KM:
            return None, "ambiguous"
        return EPick(lat, lng, round(best_km, 4), "osm_intersection", 0.70), "ok"

    # No crossing: accept a near miss (missing OSM node) at <= 150 m.
    nearest: tuple[float, float, float, str] | None = None
    for way_a in matched_a:
        for way_b in matched_b:
            pair = closest_pair(way_a, way_b)
            if pair is None:
                continue
            (lat, lng), km = pair
            if nearest is None or km < nearest[0]:
                nearest = (km, lat, lng, "osm_near_midpoint")
    if nearest and nearest[0] <= _NEAR_MIDPOINT_KM:
        km, lat, lng, method = nearest
        if in_hanoi_bbox(lat, lng) and nearest_anchor_km(lat, lng, anchors) <= zone:
            return EPick(lat, lng, round(nearest_anchor_km(lat, lng, anchors), 4), method, 0.60), "ok"
    return None, "no_intersection"


# ---------------------------------------------------------------------------
# Photon junction fallback
# ---------------------------------------------------------------------------

class PhotonJunctionClient(PhotonDistrictClient):
    """Photon without the highway-only filter, still biased to the district."""

    def _params(self, query: str) -> dict:
        params = super()._params(query)
        params.pop("osm_tag", None)
        params["limit"] = 20
        return params


def photon_queries(group: EphGroup, district: str) -> list[str]:
    a, b = bare_street(group.street_a), bare_street(group.street_b)
    forms = [f"Ngã tư {a} {b}", f"Nút giao {a} {b}", f"{a} {b}", f"Ngã ba {a} {b}"]
    return [f"{form}{_SUFFIX}{district}" for form in dict.fromkeys(forms)]


def _words(text: str) -> list[tuple[str, str]]:
    return [
        viet_word_key(word)
        for word in re.findall(r"\w+", text)
        if normalize_ward(word) not in _STREET_TYPE_WORDS
    ]


def _span(words: Sequence[tuple[str, str]], street: str) -> tuple[int, int] | None:
    """First ordered occurrence of the street's words in the label."""
    wanted = _words(street)
    if not wanted:
        return None
    indices: list[int] = []
    cursor = 0
    for word in wanted:
        try:
            index = words.index(word, cursor)
        except ValueError:
            return None
        indices.append(index)
        cursor = index + 1
    return indices[0], indices[-1]


def names_adjacent(street_a: str, street_b: str, label: str, max_gap: int = 1) -> bool:
    """True when the two street names sit next to each other in the label.

    Photon happily returns a long way label that mentions a third road between
    them ("Cầu vượt Láng Hạ - Huỳnh Thúc Kháng Ngõ Thái Hà" for Láng Hạ–Thái Hà),
    which is not the junction we asked for.
    """
    words = [viet_word_key(word) for word in re.findall(r"\w+", label)]
    span_a, span_b = _span(words, street_a), _span(words, street_b)
    if span_a is None or span_b is None:
        return False
    if span_a[1] < span_b[0]:
        gap = words[span_a[1] + 1:span_b[0]]
    elif span_b[1] < span_a[0]:
        gap = words[span_b[1] + 1:span_a[0]]
    else:
        return True  # names overlap
    return sum(1 for word in gap if word[0] not in _STREET_TYPE_WORDS) <= max_gap


def label_has_street(street: str, label: str) -> bool:
    wanted = _words(street)
    return bool(wanted) and all(word in set(_words(label)) for word in wanted)


def is_junction_label(label: str, osm_class: str = "", osm_type: str = "") -> bool:
    """A bus shelter named after two roads is not the junction of those roads."""
    return bool(
        _JUNCTION_LABEL_RE.search(label)
        or osm_class in ("junction", "crossing")
        or osm_type in ("junction", "crossing")
    )


def photon_junction(group: EphGroup, lookup, anchors) -> tuple[EPick | None, str]:
    zone = zone_km(group.district)
    saw_far = False
    missing = False
    last = ""
    for query in photon_queries(group, group.district):
        last = query
        results = lookup(query)
        if results is None:
            missing = True
            continue
        for result in results:
            label = " ".join(filter(None, (
                str(result.get("label") or ""),
                str(result.get("display_name") or ""),
            )))
            if not (label_has_street(group.street_a, label)
                    and label_has_street(group.street_b, label)
                    and names_adjacent(group.street_a, group.street_b, label)
                    and is_junction_label(
                        label,
                        str(result.get("class") or ""),
                        str(result.get("type") or ""),
                    )):
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
            detail = f"{label[:100]} [{result.get('class') or ''}/{result.get('type') or ''}]"
            return EPick(lat, lng, round(distance, 4), "photon_junction", 0.60, detail), "ok"
    if missing:
        return None, "not_geocoded"
    return None, "too_far" if saw_far else "not_found"


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------

def evaluate(
    group: EphGroup,
    anchors: Mapping[str, Sequence[tuple[float, float]]],
    ways: Sequence[Mapping] | None,
    lookup,
    osm_available: bool,
) -> EOutcome:
    points = anchors.get(group.district)
    if not points:
        return EOutcome(group, "no_anchors")
    osm_status = ""
    if osm_available:
        if ways is None:
            osm_status = "overpass_error"
        else:
            pick, osm_status = osm_intersection(group, ways, points)
            if pick:
                return EOutcome(group, "ok", pick, note=pick.detail)
            if osm_status in ("ambiguous", "too_far"):
                return EOutcome(group, osm_status)
    pick, photon_status = photon_junction(group, lookup, points)
    if pick:
        note = f"{pick.detail} | {osm_status}" if osm_status else pick.detail
        return EOutcome(group, "ok", pick, note=note)
    return EOutcome(group, photon_status if photon_status != "not_found" else (osm_status or "not_found"))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_COLUMNS = [
    "street_a", "street_b", "district", "rows", "status", "method", "lat", "lng",
    "anchor_km", "confidence", "link_osm", "note",
]


def _link(lat: float, lng: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lng:.6f}#map=18/{lat:.6f}/{lng:.6f}"


def write_csv(outcomes: Sequence[EOutcome], path: Path) -> None:
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS)
        writer.writeheader()
        for outcome in outcomes:
            pick = outcome.pick
            writer.writerow({
                "street_a": outcome.group.street_a,
                "street_b": outcome.group.street_b,
                "district": outcome.group.district,
                "rows": outcome.group.rows,
                "status": outcome.status,
                "method": pick.method if pick else "",
                "lat": f"{pick.lat:.6f}" if pick else "",
                "lng": f"{pick.lng:.6f}" if pick else "",
                "anchor_km": f"{pick.anchor_km:.3f}" if pick else "",
                "confidence": f"{pick.confidence:.2f}" if pick else "",
                "link_osm": _link(pick.lat, pick.lng) if pick else "",
                "note": outcome.note or outcome.group.note,
            })


def write_geojson(outcomes: Sequence[EOutcome], path: Path) -> None:
    features = []
    for outcome in outcomes:
        pick = outcome.pick
        if not pick:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [pick.lng, pick.lat]},
            "properties": {
                "street_a": outcome.group.street_a,
                "street_b": outcome.group.street_b,
                "district": outcome.group.district,
                "rows": outcome.group.rows,
                "method": pick.method,
                "confidence": pick.confidence,
            },
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=1
    ), encoding="utf-8")


def format_summary(
    outcomes: Sequence[EOutcome],
    groups: Sequence[EphGroup],
    invalid: Sequence[InvalidE],
    e_rows: int,
    seed: int,
    probe: tuple[str | None, list[tuple[str, str]]] | None,
    old_distances: Sequence[float] = (),
    include_table: bool = True,
) -> str:
    statuses = Counter(outcome.status for outcome in outcomes)
    methods = Counter(outcome.pick.method for outcome in outcomes if outcome.pick)
    invalid_counts = Counter(item.status for item in invalid)
    lines = [
        "4b-2 intersection trial (read-only)",
        f"Class-E rows: {e_rows}; valid groups: {len(groups)}; sampled: {len(outcomes)} (seed {seed})",
        "Invalid E: " + ", ".join(f"{k}={v}" for k, v in sorted(invalid_counts.items())),
        f"Rows in sample: {sum(outcome.group.rows for outcome in outcomes)}",
        "",
        "Status: " + ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())),
        "Methods: " + (", ".join(f"{k}={v}" for k, v in sorted(methods.items())) or "none"),
    ]
    if probe is not None:
        working, details = probe
        lines.append("Overpass: " + ("OK @ " + working if working else "UNAVAILABLE"))
        if not working:
            lines.append("  " + "; ".join(
                f"{url.split('/')[2]}={detail.split(':')[0]}"
                for url, detail in details
            ))
    if old_distances:
        lines.append(
            f"New junction vs old single-street point: avg {sum(old_distances)/len(old_distances):.2f} km "
            f"(n={len(old_distances)})"
        )
    if include_table:
        lines += ["", "| A | B | district | rows | status | method | anchor km |", "|---|---|---|---|---|---|---|"]
        for outcome in outcomes:
            pick = outcome.pick
            lines.append(
                f"| {outcome.group.street_a} | {outcome.group.street_b} | {outcome.group.district} "
                f"| {outcome.group.rows} | {outcome.status} | {pick.method if pick else ''} "
                f"| {f'{pick.anchor_km:.2f}' if pick else ''} |"
            )
    good = [outcome for outcome in outcomes if outcome.pick]
    bad = [outcome for outcome in outcomes if not outcome.pick]
    if good:
        lines += ["", "Best examples:"]
        for outcome in sorted(good, key=lambda o: o.pick.anchor_km)[:5]:
            pick = outcome.pick
            lines.append(
                f"- {outcome.group.street_a} – {outcome.group.street_b} | {pick.method} "
                f"| anchor {pick.anchor_km:.2f} km | {_link(pick.lat, pick.lng)}"
            )
    if bad:
        lines += ["", "Worst / unresolved examples:"]
        for outcome in bad[:5]:
            lines.append(
                f"- {outcome.group.street_a} – {outcome.group.street_b} | {outcome.status} "
                f"| {outcome.group.district} | {outcome.note or outcome.group.note}"
            )
    lines += ["", "Map data © OpenStreetMap contributors (ODbL); geocoding by Photon."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_class_e(session: Session):
    rows, anchors, references, ward_names, districts = load(session)
    classified = classify_rows(target_rows(rows), ward_names, districts)
    e_rows = [item for item in classified if item.cls == "E"]
    groups, invalid = build_groups(e_rows)
    return e_rows, groups, invalid, anchors, references


def cmd_select(session: Session) -> int:
    e_rows, groups, invalid, anchors, _ = load_class_e(session)
    counts = Counter(item.status for item in invalid)
    print(f"Class-E rows: {len(e_rows)}")
    print(f"Valid intersection groups: {len(groups)} ({sum(g.rows for g in groups)} rows)")
    print("Invalid E: " + ", ".join(f"{key}={counts.get(key, 0)}" for key in
                                    ("not_pair", "multi_segment", "segment_not_street", "same_street")))
    print("\n10 valid groups:")
    for group in groups[:10]:
        print(f"  {group.rows:>3} | {group.street_a} – {group.street_b} | {group.district}")
    print("\n10 segment_not_street:")
    for item in [row for row in invalid if row.status == "segment_not_street"][:10]:
        print(f"  {item.row.location_name[:60]:<60} | {item.detail}")
    print("\n10 multi_segment:")
    for item in [row for row in invalid if row.status == "multi_segment"][:10]:
        print(f"  {item.row.location_name[:60]:<60} | {item.detail}")
    print("\nDistricts without anchors: "
          + str(sorted({g.district for g in groups if g.district not in anchors})))
    print("Read-only: no network, no DB writes.")
    return 0


def cmd_check_network() -> int:
    print("Probing Overpass mirrors (timeout 10 s, 1 request/s)...")
    working, details = probe_overpass()
    for url, detail in details:
        print(f"  {url} -> {detail}")
    print(f"\nResult: {'OK @ ' + working if working else 'NO MIRROR REACHABLE'}")
    return 0 if working else 1


def _district_ways(
    client: OverpassClient | None,
    groups: Sequence[EphGroup],
    anchors: Mapping[str, Sequence[tuple[float, float]]],
) -> dict[str, list[dict] | None]:
    result: dict[str, list[dict] | None] = {}
    if client is None:
        return result
    for district in sorted({group.district for group in groups}):
        points = anchors.get(district)
        if not points:
            result[district] = None
            continue
        bbox = district_bbox(points, district)
        result[district] = client.ways_for(bbox)
        log.info("Overpass %s: %s ways", district,
                 len(result[district]) if result[district] is not None else "error")
    return result


def cmd_fetch(session: Session, sample: int, seed: int) -> int:
    e_rows, groups, invalid, anchors, _ = load_class_e(session)
    picked = sample_groups(groups, sample, seed)
    print(f"Class-E: {len(e_rows)} rows, {len(groups)} valid groups; sampling {len(picked)} (seed {seed})")

    probe = probe_overpass()
    working_url, details = probe
    for url, detail in details:
        print(f"  overpass {url} -> {detail.split(':')[0]}")
    if not working_url:
        print("No Overpass mirror reachable — running Photon step only.")

    overpass_cache = JsonCache(OVERPASS_CACHE_PATH)
    overpass_cache.put("probe", {
        "url": working_url,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "details": details,
    })
    client = OverpassClient(overpass_cache, working_url) if working_url else None
    ways_by_district = _district_ways(client, picked, anchors)

    photon_cache = NominatimCache(CACHE_PATH)
    centres = {d: centre_of(points) for d, points in anchors.items() if points}
    junction = PhotonJunctionClient(photon_cache, centres, min_interval=1.1, timeout=30)

    outcomes = []
    for index, group in enumerate(picked, start=1):
        ways = ways_by_district.get(group.district)
        outcome = evaluate(group, anchors, ways, junction.search, bool(working_url))
        outcomes.append(outcome)
        if index % 5 == 0 or index == len(picked):
            log.info("evaluated %d/%d (photon calls: %d)", index, len(picked), junction.network_calls)
            photon_cache.save()
    photon_cache.save(force=True)
    print(format_summary(outcomes, groups, invalid, len(e_rows), seed, probe, include_table=False))
    return 0


def cmd_report(session: Session, sample: int, seed: int) -> int:
    e_rows, groups, invalid, anchors, references = load_class_e(session)
    picked = sample_groups(groups, sample, seed)
    overpass_cache = JsonCache(OVERPASS_CACHE_PATH)
    probe_data = overpass_cache.get("probe")
    probe = None
    working_url = None
    if isinstance(probe_data, dict):
        working_url = probe_data.get("url")
        probe = (working_url, [tuple(item) for item in probe_data.get("details", [])])
    ways_by_district: dict[str, list[dict] | None] = {}
    if working_url:
        for district in sorted({group.district for group in picked}):
            points = anchors.get(district)
            if points:
                ways_by_district[district] = overpass_cache.get(bbox_key(district_bbox(points, district)))
    photon_cache = NominatimCache(CACHE_PATH)
    outcomes = []
    old_distances: list[float] = []
    for group in picked:
        ways = ways_by_district.get(group.district)
        outcome = evaluate(group, anchors, ways, photon_cache.get, bool(working_url))
        outcomes.append(outcome)
        if outcome.pick and references:
            key = group.key_a
            refs = references.get(key, []) or references.get(group.key_b, [])
            if refs:
                old_distances.append(min(
                    haversine_km(outcome.pick.lat, outcome.pick.lng, lat, lng)
                    for _, lat, lng, _ in refs
                ))
    write_csv(outcomes, CSV_PATH)
    write_geojson(outcomes, GEOJSON_PATH)
    summary = format_summary(
        outcomes, groups, invalid, len(e_rows), seed, probe, old_distances,
        include_table=True,
    )
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(summary + "\n", encoding="utf-8")
    print(format_summary(
        outcomes, groups, invalid, len(e_rows), seed, probe, old_distances,
        include_table=False,
    ))
    print(f"\nCSV: {CSV_PATH}\nGeoJSON: {GEOJSON_PATH}\nSummary: {SUMMARY_PATH}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-network", action="store_true")
    mode.add_argument("--select", action="store_true")
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--report", action="store_true")
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--database-url", default=settings.database_sync_url)
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    if args.check_network:
        return cmd_check_network()
    engine = create_engine(args.database_url)
    try:
        with Session(engine) as session:
            if args.fetch:
                return cmd_fetch(session, args.sample, args.seed)
            if args.report:
                return cmd_report(session, args.sample, args.seed)
            return cmd_select(session)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
