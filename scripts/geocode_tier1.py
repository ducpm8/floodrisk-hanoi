"""
Offline Tier-1 street geocoding for coordinate-less flood events.

Tier 1 = `location_name` contains a street-type token (Đường/Phố/Ngõ/Ngách/
Hẻm/Đại lộ/Quốc lộ/Tỉnh lộ/Đê/QL/TL) AND `ward` resolves to a row in
`ward_centroids`. About 1.2k rows / 790 distinct (location_name, ward,
district) places today.

Workflow (each step is a separate mode):

    # 1. read-only overview
    python -m scripts.geocode_tier1 --select

    # 2. OSM geocoder, 1 request/s, resumable cache
    python -m scripts.geocode_tier1 --geocode
    #    If nominatim.openstreetmap.org is blocked on your network, use the
    #    OSM-based Photon fallback (separate cache/report files):
    python -m scripts.geocode_tier1 --geocode --provider photon

    # 3. CSV + summary for review (no DB writes)
    python -m scripts.geocode_tier1 --report

    # 4. write coords/flags (one transaction)
    python -m scripts.geocode_tier1 --apply

    # rollback everything written by --apply
    python -m scripts.geocode_tier1 --rollback

Files (gitignored):
    .gstack/geocode/tier1_cache.json    query -> Nominatim results (resumable)
    .gstack/geocode/tier1_report.csv    review sheet
    .gstack/geocode/tier1_summary.md    hit rate / distance histogram

Rules implemented from the plan:
  - query order: street+ward+district -> street+district -> parent street
  - Nominatim: 1 req/s, generic UA, countrycodes=vn, Hanoi viewbox
  - validate: inside Hanoi bbox and within 2.5 km (phường) / 5 km (xã, thị trấn)
  - confidence: <=1 km 0.90, <=2.5 km 0.75, xã up to 5 km / parent fallback 0.60
  - apply only `WHERE lat IS NULL`; never overwrites existing coordinates
  - failed places get coord_flag=geocode_failed|too_far
  - ward-only (admin) names get coord_flag=ward_only and keep NULL coords
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import math
import os
import random
import re
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import requests
from geoalchemy2 import Geography
from sqlalchemy import create_engine, func, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.cache import (  # noqa: E402
    cache_delete_prefix,
    close_redis,
    init_redis,
)
from api.config import settings  # noqa: E402
from api.models.flood_event import FloodEvent  # noqa: E402
from api.models.ward_centroid import WardCentroid  # noqa: E402
from api.utils.text import normalize_ward  # noqa: E402

log = logging.getLogger("geocode_tier1")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
USER_AGENT = (
    "FloodRiskHanoi/1.0 (offline batch geocoding; "
    "+https://github.com/ducpm8/floodrisk-hanoi)"
)

# Same Hà Nội sanity bounding box as the admin CSV import
# (api/routers/admin_import.py:50-51).
LAT_MIN, LAT_MAX = 20.5, 21.5
LNG_MIN, LNG_MAX = 105.2, 106.1

DEFAULT_CACHE = REPO_ROOT / ".gstack" / "geocode" / "tier1_cache.json"
DEFAULT_REPORT = REPO_ROOT / ".gstack" / "geocode" / "tier1_report.csv"
DEFAULT_SUMMARY = REPO_ROOT / ".gstack" / "geocode" / "tier1_summary.md"

STREET_RE = re.compile(
    r"(?:đường|phố|ngõ|ngách|hẻm|đại lộ|quốc lộ|tỉnh lộ|đê|ql\s*\.?\s*\d|tl\s*\.?\s*\d)",
    re.IGNORECASE,
)
ADMIN_PREFIX_RE = re.compile(
    r"^\s*(?:phường|xã|thị trấn|quận|huyện|thị xã)\b", re.IGNORECASE
)
_PARENS_RE = re.compile(r"\([^)]*\)")
_DASH_SPLIT_RE = re.compile(r"\s+[-–—]\s+")
_SLASH_TYPE_RE = re.compile(r"\b(?:đường|phố)\s*/\s*(?:đường|phố)\b", re.IGNORECASE)
_ALLEY_RE = re.compile(
    r"^\s*(?:ngõ|ngách|hẻm)\s+(?:số\s+)?([0-9]+(?:/[0-9]+)*)\s+(.+)$",
    re.IGNORECASE,
)

# Trailing clauses that describe the segment, not the street itself.
_NOTE_PREFIXES = (
    "đoạn",
    "chân cầu",
    "hướng",
    "gần",
    "qua ",
    "khu vực",
    "ngã ba",
    "ngã tư",
    "bến xe",
    "trước ",
    "sau ",
)

_CONFIDENCE_BANDS = ((1.0, 0.90), (2.5, 0.75))
_WARD_ADMIN_PREFIXES = ("phuong ", "xa ", "thi tran ", "quan ", "huyen ")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WardInfo:
    ward_name: str
    ward_name_vi: str
    lat: float
    lng: float

    @property
    def radius_km(self) -> float:
        return 5.0 if self.ward_name.startswith(("xa ", "thi tran ")) else 2.5


@dataclass
class Tier1Group:
    location_name: str
    ward: str | None
    district: str | None
    ward_info: WardInfo
    street: str
    parent: str | None
    row_count: int = 1
    # Extra street names extracted from compound/noisy location names
    # ("Phố Đội Cấn và ngõ 100 Đội Cấn", "Cuối ngõ 42 Giang Văn Minh").
    # Tried after `street` and `parent`, always at fallback confidence.
    alternates: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Pick:
    lat: float
    lng: float
    distance_km: float
    display_name: str
    osm_class: str
    osm_type: str


@dataclass
class Outcome:
    group: Tier1Group
    status: str                   # ok | geocode_failed | too_far | not_geocoded | error
    query: str = ""
    pick: Pick | None = None
    confidence: float | None = None
    fallback: bool = False


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def is_street_name(name: str | None) -> bool:
    return bool(name and STREET_RE.search(name))


def is_admin_only_name(name: str | None) -> bool:
    return bool(name and ADMIN_PREFIX_RE.match(name))


def clean_street(name: str) -> str:
    """Drop parenthetical notes and segment clauses, keep the street + number."""
    value = _PARENS_RE.sub(" ", name)
    value = _SLASH_TYPE_RE.sub("Đường", value)
    value = re.sub(r"^\s*đường\s*,", "Đường", value, flags=re.IGNORECASE)
    value = re.sub(r"^\s*đê\s*,", "Đê", value, flags=re.IGNORECASE)

    kept: list[str] = []
    for part in _DASH_SPLIT_RE.split(value):
        part = part.strip()
        low = part.lower()
        if any(low.startswith(prefix) for prefix in _NOTE_PREFIXES):
            continue
        kept.append(part)
    value = " - ".join(kept) if kept else value
    value = re.sub(r"\s+", " ", value).strip(" ,;")
    return value


def parent_street(name: str) -> str | None:
    """'Ngõ 100 Đội Cấn' -> 'Đội Cấn'; None when the parent is not derivable."""
    match = _ALLEY_RE.match(clean_street(name))
    if not match:
        return None
    parent = re.sub(r"\s+", " ", match.group(2)).strip(" ,;")
    return parent or None


_SEGMENT_SPLIT_RE = re.compile(r"\s+(?:và|\+|&)\s+|\s*[,;]\s*", re.IGNORECASE)
_STREET_KEYWORD_RE = re.compile(
    r"(?<!dân )(?:đại lộ|quốc lộ|tỉnh lộ|đường|phố|ngõ|ngách|hẻm|đê)\b", re.IGNORECASE
)
# Clauses that describe where on the street, not which street.
_TRAILING_NOTE_RE = re.compile(
    r"\s+(?:km\s*\d|đoạn\b|đối diện\b|gần\b|giao\b|tại\b|qua\b|từ\b|đến\b|"
    r"hầm\s+\d|cầu vượt\b|khu vực\b).*$",
    re.IGNORECASE,
)


def street_alternates(name: str, primary: str, parent: str | None) -> list[str]:
    """Other street names hidden in a compound or noisy location name.

    'Phố Đội Cấn và ngõ 100 Đội Cấn' -> ['Phố Đội Cấn', 'ngõ 100 Đội Cấn', 'Đội Cấn']
    'Cuối ngõ 42 Giang Văn Minh'     -> ['ngõ 42 Giang Văn Minh', 'Giang Văn Minh']
    'Đường gom Đại lộ Thăng Long (hầm 4)' -> ['Đại lộ Thăng Long']
    """
    # Main text first, then parenthetical notes: the street is often only named
    # there ("Khu đô thị Nam An Khánh (đường Lê Trọng Tấn)").
    texts = [_PARENS_RE.sub(" ", name)] + [m[1:-1] for m in _PARENS_RE.findall(name)]
    candidates: list[str] = []
    for text in texts:
        for segment in _SEGMENT_SPLIT_RE.split(text):
            segment = _TRAILING_NOTE_RE.sub("", segment).strip(" ,;-–")
            if not segment:
                continue
            starts = [m.start() for m in _STREET_KEYWORD_RE.finditer(segment)]
            # Later keywords first: 'Chân cầu HH2 đường Nguyễn Trác' -> 'đường Nguyễn Trác'.
            for start in sorted(set(starts), reverse=True):
                piece = re.sub(r"\s+", " ", segment[start:]).strip(" ,;-–")
                candidates.append(piece)
                alley = _ALLEY_RE.match(piece)
                if alley:
                    candidates.append(re.sub(r"\s+", " ", alley.group(2)).strip(" ,;-–"))
    skip = {primary.casefold(), (parent or "").casefold()}
    result: list[str] = []
    for candidate in candidates:
        if not candidate or candidate.casefold() in skip or candidate in result:
            continue
        tokens = street_tokens(candidate)
        if not tokens:
            continue
        # A bare alley number ("Ngõ 1") matches an unrelated alley anywhere.
        if all(token.isdigit() for token in tokens) and re.match(
            r"^\s*(?:ngõ|ngách|hẻm)\b", candidate, re.IGNORECASE
        ):
            continue
        result.append(candidate)
    return result


def attempts(group: "Tier1Group") -> list[tuple[str, bool]]:
    """Ordered (street, is_fallback) lookups for one group."""
    order: list[tuple[str, bool]] = [(group.street, False)]
    if group.parent:
        order.append((group.parent, True))
    order.extend((alt, True) for alt in group.alternates)
    return order


def build_queries(street: str, ward_vi: str | None, district: str | None) -> list[str]:
    """Query order: street+ward+district, then street+district."""
    street = street.strip()
    ward_vi = (ward_vi or "").strip()
    district = (district or "").strip()
    queries: list[str] = []
    first = [street, ward_vi, district, "Hà Nội", "Việt Nam"]
    queries.append(", ".join(part for part in first if part))
    second = [street, district, "Hà Nội", "Việt Nam"]
    queries.append(", ".join(part for part in second if part))
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(queries))


def in_hanoi_bbox(lat: float, lng: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def confidence_for(distance_km: float, fallback: bool = False) -> float:
    if fallback:
        return 0.60
    for limit, value in _CONFIDENCE_BANDS:
        if distance_km <= limit:
            return value
    return 0.60  # xã/thị trấn between 2.5 km and the 5 km gate


_STREET_TYPE_WORDS = {
    "duong",
    "pho",
    "ngo",
    "ngach",
    "hem",
    "dai",
    "lo",
    "quoc",
    "tinh",
    "de",
    "ql",
    "tl",
}


def street_tokens(street: str | None) -> list[str]:
    """Distinctive tokens of a street name ('Phố Đội Cấn' -> ['doi', 'can'])."""
    if not street:
        return []
    norm = normalize_ward(street)
    return [token for token in norm.split() if token not in _STREET_TYPE_WORDS]


def matches_street(street: str | None, label: str | None) -> bool:
    """True when every distinctive street token appears in the result label."""
    tokens = street_tokens(street)
    if not tokens or not label:
        return True
    if all(token.isdigit() for token in tokens):
        # Number-only streets ("Đường 32") are too generic for token matching:
        # require the full normalized street in the label so an unrelated
        # "Ngõ 32 ..." cannot win.
        return normalize_ward(street) in normalize_ward(label)
    # Judge the street part of the label only: Photon appends POI names and
    # notes ("Gần ngõ 378 đường Bưởi tránh (bên giao Đội Cấn) Ngõ 376 ...",
    # "Dự án KĐT mới Phú Lương ...") that merely MENTION the street.
    head = _PARENS_RE.sub(" ", label.split(",")[0])
    label_tokens = set(normalize_ward(head).split())
    wanted = set(tokens)
    if not wanted.issubset(label_tokens):
        return False
    # Extra distinctive words mean a different street / place ("Phố Thanh Yên"
    # for "Đường Thành", "Đường TT15 Khu Đô Thị Văn Quán" for "Đường Văn Quán").
    # Digits are alley numbers and street-type words are not distinctive.
    extra = {
        token
        for token in label_tokens - wanted
        if not token.isdigit() and token not in _STREET_TYPE_WORDS
    }
    allowed = 0 if len(wanted) == 1 else 1
    return len(extra) <= allowed


def label_match_rank(street: str | None, label: str | None) -> int:
    """0 = exact street name, 1 = contains it as a prefix/suffix, 2 = other."""
    if not street or not label:
        return 2
    norm_street = normalize_ward(street)
    norm_label = normalize_ward(label)
    if norm_label == norm_street:
        return 0
    if norm_label.startswith(norm_street + " ") or norm_label.endswith(" " + norm_street):
        return 1
    return 2


def bare_street(street: str) -> str:
    """'Đường Vĩnh Hưng' -> 'Vĩnh Hưng' (used as a Photon retry query)."""
    return re.sub(
        r"^\s*(?:đường|phố|ngõ|ngách|hẻm|đại lộ|quốc lộ|tỉnh lộ|đê)\b\s*",
        "",
        street,
        flags=re.IGNORECASE,
    ).strip(" ,")


def queries_for(
    provider: str,
    street: str,
    ward_vi: str | None,
    district: str | None,
) -> list[str]:
    """Provider-specific query order (Photon handles bare street names best)."""
    if provider == "photon":
        queries = [street]
        bare = bare_street(street)
        tokens = street_tokens(bare)
        if bare and tokens and not all(token.isdigit() for token in tokens):
            queries.append(bare)
        return list(dict.fromkeys(query for query in queries if query))
    return build_queries(street, ward_vi, district)


def pick_result(
    results: Sequence[Mapping],
    ward_lat: float,
    ward_lng: float,
    radius_km: float,
    street: str | None = None,
) -> Pick | None:
    """Pick the best in-bbox, in-radius result; prefer highway/place classes."""
    candidates: list[tuple[int, int, float, Mapping]] = []
    for result in results:
        label = str(result.get("label") or "")
        if street and not matches_street(street, label):
            continue
        try:
            lat, lng = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not in_hanoi_bbox(lat, lng):
            continue
        distance = haversine_km(lat, lng, ward_lat, ward_lng)
        if distance > radius_km:
            continue
        preferred = 0 if result.get("class") in ("highway", "place") else 1
        candidates.append(
            (preferred, label_match_rank(street, label), distance, result)
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    _, _, distance, result = candidates[0]
    return Pick(
        lat=float(result["lat"]),
        lng=float(result["lon"]),
        distance_km=round(distance, 4),
        display_name=str(result.get("display_name") or ""),
        osm_class=str(result.get("class") or ""),
        osm_type=str(result.get("type") or ""),
    )


def has_candidate_outside_radius(
    results: Sequence[Mapping],
    ward_lat: float,
    ward_lng: float,
    radius_km: float,
    street: str | None = None,
) -> bool:
    for result in results:
        label = str(result.get("label") or "")
        if street and not matches_street(street, label):
            continue
        try:
            lat, lng = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if in_hanoi_bbox(lat, lng):
            if haversine_km(lat, lng, ward_lat, ward_lng) > radius_km:
                return True
        else:
            return True
    return False


# ---------------------------------------------------------------------------
# Row selection
# ---------------------------------------------------------------------------

def build_ward_index(ward_rows: Iterable) -> dict[str, WardInfo]:
    index: dict[str, WardInfo] = {}
    for row in ward_rows:
        index[row.ward_name] = WardInfo(
            ward_name=row.ward_name,
            ward_name_vi=row.ward_name_vi,
            lat=float(row.lat),
            lng=float(row.lng),
        )
    return index


def resolve_ward(value: str | None, index: Mapping[str, WardInfo]) -> WardInfo | None:
    """Exact + admin-prefix variants against ward_centroids (no LIKE)."""
    if not value:
        return None
    norm = normalize_ward(value)
    if norm in index:
        return index[norm]
    for prefix in ("phuong ", "xa ", "thi tran "):
        if prefix + norm in index:
            return index[prefix + norm]
    for prefix in _WARD_ADMIN_PREFIXES:
        if norm.startswith(prefix) and norm[len(prefix):] in index:
            return index[norm[len(prefix):]]
    return None


def group_tier1(
    rows: Iterable,
    ward_index: Mapping[str, WardInfo],
) -> list[Tier1Group]:
    groups: dict[tuple[str, str | None, str | None], Tier1Group] = {}
    for row in rows:
        name = row.location_name
        if not is_street_name(name) or is_admin_only_name(name):
            # "Xã Xuân Mai (tràn đê Bùi 2)" is a ward-only exception, not a
            # street: the street token is only a parenthetical note.
            continue
        ward_info = resolve_ward(row.ward, ward_index)
        if ward_info is None:
            continue
        key = (name, row.ward, row.district)
        if key in groups:
            groups[key].row_count += 1
            continue
        street = clean_street(name)
        parent = parent_street(name)
        groups[key] = Tier1Group(
            location_name=name,
            ward=row.ward,
            district=row.district,
            ward_info=ward_info,
            street=street,
            parent=parent,
            alternates=street_alternates(name, street, parent),
        )
    return sorted(
        groups.values(),
        key=lambda g: (-g.row_count, g.location_name, g.ward or "", g.district or ""),
    )


def admin_only_rows(rows: Iterable) -> list:
    return [row for row in rows if is_admin_only_name(row.location_name)]


# ---------------------------------------------------------------------------
# Nominatim client + resumable cache
# ---------------------------------------------------------------------------

class NominatimCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, list[dict]] = {}
        self._unsaved = 0
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                queries = payload.get("queries", payload)
                if isinstance(queries, dict):
                    self.data = {
                        str(k): list(v) for k, v in queries.items() if isinstance(v, list)
                    }
                log.info("Loaded %d cached queries from %s", len(self.data), path)
            except (OSError, ValueError) as exc:
                log.warning("Ignoring unreadable cache %s: %s", path, exc)

    def get(self, query: str) -> list[dict] | None:
        return self.data.get(query)

    def put(self, query: str, results: Sequence[Mapping]) -> None:
        self.data[query] = [
            {
                "lat": str(item.get("lat", "")),
                "lon": str(item.get("lon", "")),
                "display_name": item.get("display_name") or "",
                "class": item.get("class") or "",
                "type": item.get("type") or "",
                "importance": item.get("importance"),
                "label": item.get("label") or item.get("display_name") or "",
            }
            for item in results
        ]
        self._unsaved += 1
        if self._unsaved >= 5:
            self.save()

    def save(self, force: bool = False) -> None:
        if not self._unsaved and not force:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "queries": self.data}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(tmp, self.path)
        self._unsaved = 0


class GeocoderClient:
    """Shared retry/throttle/cache loop; subclasses provide URL + normalizer."""

    name = "base"
    url = ""

    def __init__(
        self,
        cache: NominatimCache,
        min_interval: float = 1.1,
        timeout: int = 30,
    ) -> None:
        self.cache = cache
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request = 0.0
        self.network_calls = 0

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _params(self, query: str) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def _normalize(self, payload) -> list[dict] | None:  # pragma: no cover
        raise NotImplementedError

    def search(self, query: str) -> list[dict] | None:
        cached = self.cache.get(query)
        if cached is not None:
            return cached

        for attempt in range(4):
            self._throttle()
            try:
                response = requests.get(
                    self.url,
                    params=self._params(query),
                    headers={"User-Agent": USER_AGENT},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                log.warning("%s request failed (%s): %s", self.name, query, exc)
                if attempt == 3:
                    return None
                time.sleep(2**attempt)
                continue

            self.network_calls += 1
            if response.status_code in (429, 500, 502, 503, 504):
                backoff = 2 ** (attempt + 1)
                log.warning(
                    "%s HTTP %s for %r — retrying in %ss",
                    self.name,
                    response.status_code,
                    query,
                    backoff,
                )
                time.sleep(backoff)
                continue
            try:
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                log.warning("%s bad response for %r: %s", self.name, query, exc)
                if attempt == 3:
                    return None
                time.sleep(2**attempt)
                continue

            results = self._normalize(payload)
            if results is None:
                log.warning("%s invalid payload for %r", self.name, query)
                return None
            self.cache.put(query, results)
            return list(self.cache.get(query) or [])
        return None


class NominatimClient(GeocoderClient):
    name = "nominatim"
    url = NOMINATIM_URL

    def _params(self, query: str) -> dict:
        return {
            "q": query,
            "format": "jsonv2",
            "limit": 5,
            "countrycodes": "vn",
            "viewbox": f"{LNG_MIN},{LAT_MAX},{LNG_MAX},{LAT_MIN}",
            "bounded": 1,
            "addressdetails": 0,
            "accept-language": "vi",
        }

    def _normalize(self, payload) -> list[dict] | None:
        if not isinstance(payload, list):
            return None
        results: list[dict] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "lat": str(item.get("lat", "")),
                    "lon": str(item.get("lon", "")),
                    "display_name": item.get("display_name") or "",
                    "class": item.get("class") or "",
                    "type": item.get("type") or "",
                    "importance": item.get("importance"),
                    "label": item.get("name") or item.get("display_name") or "",
                }
            )
        return results


class PhotonClient(GeocoderClient):
    """Fallback provider for networks where nominatim.openstreetmap.org is blocked.

    Photon is OSM-based (komoot) and returns GeoJSON features. Its public
    instance does not support `lang=vi`, and `osm_tag=highway` restricts results
    to roads, which avoids POIs/buildings winning street queries.
    """

    name = "photon"
    url = PHOTON_URL

    def _params(self, query: str) -> dict:
        return {
            "q": query,
            "limit": 10,
            "bbox": f"{LNG_MIN},{LAT_MIN},{LNG_MAX},{LAT_MAX}",
            "osm_tag": "highway",
        }

    def _normalize(self, payload) -> list[dict] | None:
        if not isinstance(payload, dict):
            return None
        features = payload.get("features")
        if not isinstance(features, list):
            return None
        results: list[dict] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") or {}
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2:
                continue
            lat, lon = coords[1], coords[0]
            name = props.get("name") or ""
            street = props.get("street") or ""
            label = " ".join(
                dict.fromkeys(part for part in (name, street) if part)
            )
            parts = [str(label)] if label else []
            for key in ("housenumber", "district", "city", "state", "country"):
                value = props.get(key)
                if value and str(value) not in parts:
                    parts.append(str(value))
            results.append(
                {
                    "lat": str(lat),
                    "lon": str(lon),
                    "display_name": ", ".join(parts),
                    "label": label,
                    "class": props.get("osm_key") or "",
                    "type": props.get("osm_value") or "",
                    "importance": props.get("importance"),
                }
            )
        return results


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def geocode_group(client: GeocoderClient, group: Tier1Group) -> Outcome:
    ward = group.ward_info
    saw_far = False
    last_query = ""

    for street, fallback in attempts(group):
        for query in queries_for(
            client.name, street, ward.ward_name_vi, group.district
        ):
            last_query = query
            results = client.search(query)
            if results is None:
                return Outcome(group, "error", query=query)
            if results:
                pick = pick_result(
                    results, ward.lat, ward.lng, ward.radius_km, street=street
                )
                if pick is not None:
                    return Outcome(
                        group,
                        "ok",
                        query=query,
                        pick=pick,
                        confidence=confidence_for(pick.distance_km, fallback=fallback),
                        fallback=fallback,
                    )
                saw_far = saw_far or has_candidate_outside_radius(
                    results, ward.lat, ward.lng, ward.radius_km, street=street
                )

    return Outcome(
        group, "too_far" if saw_far else "geocode_failed", query=last_query
    )


def evaluate_groups(
    groups: Sequence[Tier1Group],
    cache: NominatimCache,
    provider: str = "nominatim",
) -> list[Outcome]:
    """Build outcomes from the cache without making network calls."""
    outcomes: list[Outcome] = []
    for group in groups:
        outcome = _evaluate_group_from_cache(group, cache, provider=provider)
        outcomes.append(outcome)
    return outcomes


def _evaluate_group_from_cache(
    group: Tier1Group,
    cache: NominatimCache,
    provider: str = "nominatim",
) -> Outcome:
    ward = group.ward_info
    saw_far = False
    last_query = ""
    for street, fallback in attempts(group):
        for query in queries_for(
            provider, street, ward.ward_name_vi, group.district
        ):
            last_query = query
            results = cache.get(query)
            if results is None:
                return Outcome(group, "not_geocoded", query=query)
            if results:
                pick = pick_result(
                    results, ward.lat, ward.lng, ward.radius_km, street=street
                )
                if pick is not None:
                    return Outcome(
                        group,
                        "ok",
                        query=query,
                        pick=pick,
                        confidence=confidence_for(pick.distance_km, fallback=fallback),
                        fallback=fallback,
                    )
                saw_far = saw_far or has_candidate_outside_radius(
                    results, ward.lat, ward.lng, ward.radius_km, street=street
                )
    return Outcome(group, "too_far" if saw_far else "geocode_failed", query=last_query)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(outcomes: Sequence[Outcome], path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "location_name",
        "ward",
        "district",
        "ward_name_vi",
        "row_count",
        "street",
        "parent",
        "query",
        "status",
        "display_name",
        "lat",
        "lng",
        "distance_km",
        "confidence",
        "fallback",
        "osm_class",
        "osm_type",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for outcome in outcomes:
            group = outcome.group
            pick = outcome.pick
            writer.writerow(
                {
                    "location_name": group.location_name,
                    "ward": group.ward or "",
                    "district": group.district or "",
                    "ward_name_vi": group.ward_info.ward_name_vi,
                    "row_count": group.row_count,
                    "street": group.street,
                    "parent": group.parent or "",
                    "query": outcome.query,
                    "status": outcome.status,
                    "display_name": pick.display_name if pick else "",
                    "lat": f"{pick.lat:.6f}" if pick else "",
                    "lng": f"{pick.lng:.6f}" if pick else "",
                    "distance_km": f"{pick.distance_km:.3f}" if pick else "",
                    "confidence": f"{outcome.confidence:.2f}" if outcome.confidence else "",
                    "fallback": "yes" if outcome.fallback else "",
                    "osm_class": pick.osm_class if pick else "",
                    "osm_type": pick.osm_type if pick else "",
                }
            )
    return summarize(outcomes)


def summarize(outcomes: Sequence[Outcome]) -> dict:
    by_status = Counter(outcome.status for outcome in outcomes)
    rows_by_status: Counter[str] = Counter()
    distances: list[float] = []
    confidences: Counter[str] = Counter()
    for outcome in outcomes:
        rows_by_status[outcome.status] += outcome.group.row_count
        if outcome.pick:
            distances.append(outcome.pick.distance_km)
            confidences[f"{outcome.confidence:.2f}"] += 1

    bands = {
        "<=0.5 km": sum(1 for d in distances if d <= 0.5),
        "0.5-1 km": sum(1 for d in distances if 0.5 < d <= 1.0),
        "1-2.5 km": sum(1 for d in distances if 1.0 < d <= 2.5),
        "2.5-5 km": sum(1 for d in distances if 2.5 < d <= 5.0),
    }
    return {
        "groups": len(outcomes),
        "rows": sum(outcome.group.row_count for outcome in outcomes),
        "by_status": dict(by_status),
        "rows_by_status": dict(rows_by_status),
        "bands": bands,
        "confidences": dict(confidences),
        "fallbacks": sum(1 for outcome in outcomes if outcome.fallback),
    }


def format_summary(outcomes: Sequence[Outcome]) -> str:
    summary = summarize(outcomes)
    lines = [
        f"Tier-1 groups: {summary['groups']}",
        f"Rows covered:  {summary['rows']}",
        "",
        "Group status:",
    ]
    for status, count in sorted(summary["by_status"].items()):
        lines.append(
            f"  {status:16s} {count:5d} groups / "
            f"{summary['rows_by_status'].get(status, 0):5d} rows"
        )
    lines.append("")
    lines.append("Distance from ward centroid (successful hits):")
    for band, count in summary["bands"].items():
        lines.append(f"  {band:10s} {count:5d}")
    lines.append("")
    lines.append("Confidence:")
    for value, count in sorted(summary["confidences"].items()):
        lines.append(f"  {value:>5s} {count:5d}")
    lines.append(f"  parent-street fallbacks: {summary['fallbacks']}")
    return "\n".join(lines)


def write_summary(outcomes: Sequence[Outcome], path: Path, report_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = [format_summary(outcomes), "", f"CSV: {report_path}", ""]
    good = [outcome for outcome in outcomes if outcome.status == "ok" and outcome.pick]
    sample = random.Random(42).sample(good, k=min(30, len(good))) if good else []
    if sample:
        text.append("30 random spot checks:")
        for outcome in sample:
            pick = outcome.pick
            text.append(
                f"- {outcome.group.location_name} | {outcome.group.ward_info.ward_name_vi} "
                f"| {pick.distance_km:.2f} km | conf {outcome.confidence:.2f} "
                f"| {pick.display_name}"
            )
            text.append(
                f"  https://www.openstreetmap.org/?mlat={pick.lat}&mlon={pick.lng}"
                f"#map=17/{pick.lat}/{pick.lng}"
            )
            text.append(
                f"  https://www.google.com/maps/search/?api=1&query={pick.lat},{pick.lng}"
            )
    path.write_text("\n".join(text) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# DB apply / rollback
# ---------------------------------------------------------------------------

def group_filter(group: Tier1Group, applied: bool = False):
    """Rows of one group: still coordinate-less, or (applied=True) already
    written by this script and eligible for reconciliation."""
    conditions = [
        FloodEvent.coord_source == "geocoded_street"
        if applied
        else FloodEvent.lat.is_(None),
        FloodEvent.location_name == group.location_name,
    ]
    if group.ward is None:
        conditions.append(FloodEvent.ward.is_(None))
    else:
        conditions.append(FloodEvent.ward == group.ward)
    if group.district is None:
        conditions.append(FloodEvent.district.is_(None))
    else:
        conditions.append(FloodEvent.district == group.district)
    return conditions


def group_update(group: Tier1Group):
    return update(FloodEvent).where(*group_filter(group))


def apply_outcomes(
    session: Session,
    outcomes: Sequence[Outcome],
    admin_rows: Sequence,
    apply: bool = True,
) -> dict:
    stats: Counter[str] = Counter()
    not_geocoded = 0

    for outcome in outcomes:
        if outcome.status == "ok" and outcome.pick:
            pick = outcome.pick
            statement = group_update(outcome.group).values(
                lat=round(pick.lat, 6),
                lng=round(pick.lng, 6),
                geom=func.ST_SetSRID(
                    func.ST_MakePoint(round(pick.lng, 6), round(pick.lat, 6)), 4326
                ).cast(Geography(geometry_type="POINT", srid=4326)),
                coord_source="geocoded_street",
                coord_confidence=outcome.confidence,
                coord_flag=None,
            )
            stats["geocoded"] += _rows(session, statement, apply)
        elif outcome.status in ("geocode_failed", "too_far"):
            # Keep deliberate exceptions (ward_only / generic_area): a failed
            # street lookup must not overwrite them.
            statement = (
                group_update(outcome.group)
                .where(
                    or_(
                        FloodEvent.coord_flag.is_(None),
                        FloodEvent.coord_flag.in_(("geocode_failed", "too_far")),
                    )
                )
                .values(coord_flag=outcome.status)
            )
            stats[outcome.status] += _rows(session, statement, apply)
        else:
            not_geocoded += 1

    admin_ids = [row.id for row in admin_rows]
    for start in range(0, len(admin_ids), 500):
        chunk = admin_ids[start:start + 500]
        statement = (
            update(FloodEvent)
            .where(FloodEvent.id.in_(chunk))
            .where(FloodEvent.lat.is_(None))
            .values(coord_flag="ward_only")
        )
        stats["ward_only"] += _rows(session, statement, apply)

    if apply:
        session.commit()

    stats["not_geocoded"] = not_geocoded
    return dict(stats)


def _rows(session: Session, statement, apply: bool) -> int:
    if apply:
        return session.execute(statement).rowcount or 0
    count_statement = (
        select(func.count())
        .select_from(FloodEvent)
        .where(statement.whereclause)
    )
    return session.execute(count_statement).scalar_one()


def reconcile_outcomes(
    session: Session,
    outcomes: Sequence[Outcome],
    applied_points: Mapping[tuple, tuple[float, float]],
    apply: bool = True,
) -> dict:
    """Re-check rows this script already wrote against the CURRENT matching rules.

    - still ok, same point        -> untouched
    - still ok, different point   -> coordinates replaced
    - no longer acceptable        -> coordinates cleared, coord_flag set
    """
    stats: Counter[str] = Counter()
    changes: list[str] = []
    for outcome in outcomes:
        group = outcome.group
        key = (group.location_name, group.ward, group.district)
        statement = update(FloodEvent).where(*group_filter(group, applied=True))
        if outcome.status == "ok" and outcome.pick:
            pick = outcome.pick
            old = applied_points.get(key)
            if old and haversine_km(old[0], old[1], pick.lat, pick.lng) < 0.02:
                stats["unchanged_groups"] += 1
                continue
            values = dict(
                lat=round(pick.lat, 6),
                lng=round(pick.lng, 6),
                geom=func.ST_SetSRID(
                    func.ST_MakePoint(round(pick.lng, 6), round(pick.lat, 6)), 4326
                ).cast(Geography(geometry_type="POINT", srid=4326)),
                coord_confidence=outcome.confidence,
            )
            stats["moved_rows"] += _applied_rows(session, statement.values(**values), group, apply)
            changes.append(f"MOVED   {group.location_name} | {group.ward} -> {pick.display_name[:60]}")
        elif outcome.status in ("geocode_failed", "too_far"):
            values = dict(
                lat=None,
                lng=None,
                geom=None,
                coord_source=None,
                coord_confidence=None,
                coord_flag=outcome.status,
            )
            stats["cleared_rows"] += _applied_rows(session, statement.values(**values), group, apply)
            changes.append(f"CLEARED {group.location_name} | {group.ward} ({outcome.status})")
        else:
            stats["skipped_not_geocoded"] += 1
    if apply:
        session.commit()
    stats["changes"] = changes  # type: ignore[assignment]
    return dict(stats)


def _applied_rows(session: Session, statement, group: Tier1Group, apply: bool) -> int:
    if apply:
        return session.execute(statement).rowcount or 0
    return session.execute(
        select(func.count())
        .select_from(FloodEvent)
        .where(*group_filter(group, applied=True))
    ).scalar_one()


def rollback(session: Session) -> int:
    statement = (
        update(FloodEvent)
        .where(FloodEvent.coord_source == "geocoded_street")
        .values(
            lat=None,
            lng=None,
            geom=None,
            coord_source=None,
            coord_confidence=None,
        )
    )
    result = session.execute(statement)
    session.commit()
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_rows(session: Session) -> list:
    return list(
        session.execute(
            select(
                FloodEvent.id,
                FloodEvent.location_name,
                FloodEvent.ward,
                FloodEvent.district,
            ).where(FloodEvent.lat.is_(None))
        ).all()
    )


def load_ward_index(session: Session) -> dict[str, WardInfo]:
    rows = session.execute(
        select(
            WardCentroid.ward_name,
            WardCentroid.ward_name_vi,
            WardCentroid.lat,
            WardCentroid.lng,
        )
    ).all()
    return build_ward_index(rows)


def cmd_select(session: Session, limit: int | None) -> int:
    rows = load_rows(session)
    index = load_ward_index(session)
    groups = group_tier1(rows, index)
    if limit:
        groups = groups[:limit]
    print(f"NULL-coordinate rows: {len(rows)}")
    print(f"Tier-1 groups: {len(groups)} ({sum(g.row_count for g in groups)} rows)")
    print(f"Admin-only (ward_only) rows: {len(admin_only_rows(rows))}")
    print()
    print("Top groups:")
    for group in groups[:20]:
        print(
            f"  {group.row_count:3d}  {group.location_name} "
            f"| {group.ward_info.ward_name_vi} | {group.district or ''}"
        )
    return 0


def cmd_geocode(
    session: Session,
    cache_path: Path,
    limit: int | None,
    min_interval: float,
    timeout: int,
    provider: str = "nominatim",
    include_applied: bool = False,
) -> int:
    rows = load_rows(session)
    if include_applied:
        # Also (re)look up places already written, so --reconcile can find a
        # better street candidate for rows that no longer pass the matcher.
        rows += list(
            session.execute(
                select(
                    FloodEvent.id,
                    FloodEvent.location_name,
                    FloodEvent.ward,
                    FloodEvent.district,
                ).where(FloodEvent.coord_source == "geocoded_street")
            ).all()
        )
    groups = group_tier1(rows, load_ward_index(session))
    if limit:
        groups = groups[:limit]
    cache = NominatimCache(cache_path)
    client_class = PhotonClient if provider == "photon" else NominatimClient
    client = client_class(cache, min_interval=min_interval, timeout=timeout)

    outcomes: list[Outcome] = []
    errors = 0
    for idx, group in enumerate(groups, start=1):
        outcome = geocode_group(client, group)
        outcomes.append(outcome)
        if outcome.status == "error":
            errors += 1
        if idx % 25 == 0 or idx == len(groups):
            log.info(
                "geocoded %d/%d groups (%d network calls, %d errors)",
                idx,
                len(groups),
                client.network_calls,
                errors,
            )
            cache.save()
    cache.save(force=True)

    print(format_summary(outcomes))
    print()
    print(f"Cache: {cache_path} ({len(cache.data)} queries)")
    print(f"Provider: {provider}")
    if errors:
        print(f"{errors} group(s) hit network errors — re-run --geocode to retry")
    return 0


def cmd_report(
    session: Session,
    cache_path: Path,
    report_path: Path,
    summary_path: Path,
    limit: int | None,
    provider: str = "nominatim",
) -> int:
    groups = group_tier1(load_rows(session), load_ward_index(session))
    if limit:
        groups = groups[:limit]
    cache = NominatimCache(cache_path)
    outcomes = evaluate_groups(groups, cache, provider=provider)
    write_report(outcomes, report_path)
    write_summary(outcomes, summary_path, report_path)
    print(format_summary(outcomes))
    print()
    print(f"CSV:     {report_path}")
    print(f"Summary: {summary_path}")
    return 0


def cmd_apply(
    session: Session,
    cache_path: Path,
    limit: int | None,
    dry_run: bool,
    provider: str = "nominatim",
) -> int:
    rows = load_rows(session)
    groups = group_tier1(rows, load_ward_index(session))
    if limit:
        groups = groups[:limit]
    cache = NominatimCache(cache_path)
    outcomes = evaluate_groups(groups, cache, provider=provider)
    stats = apply_outcomes(session, outcomes, admin_only_rows(rows), apply=not dry_run)
    if dry_run:
        session.rollback()
    else:
        deleted = asyncio.run(_invalidate_search_cache())
        if deleted:
            print(f"Search cache keys cleared: {deleted}")

    summary = summarize(outcomes)
    print(format_summary(outcomes))
    print()
    print(f"{'DRY RUN — nothing written' if dry_run else 'APPLIED'}")
    print(f"Rows geocoded:      {stats.get('geocoded', 0)}")
    print(f"Rows flagged failed:{stats.get('geocode_failed', 0)}")
    print(f"Rows flagged too_far: {stats.get('too_far', 0)}")
    print(f"Rows flagged ward_only: {stats.get('ward_only', 0)}")
    if summary["by_status"].get("not_geocoded"):
        print(
            f"Skipped (not geocoded): {summary['by_status']['not_geocoded']} groups "
            "— run --geocode first"
        )
    return 0


def cmd_reconcile(
    session: Session,
    cache_path: Path,
    dry_run: bool,
    provider: str = "nominatim",
) -> int:
    rows = list(
        session.execute(
            select(
                FloodEvent.id,
                FloodEvent.location_name,
                FloodEvent.ward,
                FloodEvent.district,
                FloodEvent.lat,
                FloodEvent.lng,
            ).where(FloodEvent.coord_source == "geocoded_street")
        ).all()
    )
    applied_points: dict[tuple, tuple[float, float]] = {}
    for row in rows:
        applied_points.setdefault(
            (row.location_name, row.ward, row.district), (float(row.lat), float(row.lng))
        )
    groups = group_tier1(rows, load_ward_index(session))
    cache = NominatimCache(cache_path)
    outcomes = evaluate_groups(groups, cache, provider=provider)
    stats = reconcile_outcomes(session, outcomes, applied_points, apply=not dry_run)
    if dry_run:
        session.rollback()
    else:
        deleted = asyncio.run(_invalidate_search_cache())
        if deleted:
            print(f"Search cache keys cleared: {deleted}")
    print(f"{'DRY RUN — nothing written' if dry_run else 'APPLIED'}")
    print(f"Applied rows checked: {len(rows)} in {len(groups)} groups")
    print(f"Groups unchanged:     {stats.get('unchanged_groups', 0)}")
    print(f"Rows moved:           {stats.get('moved_rows', 0)}")
    print(f"Rows cleared:         {stats.get('cleared_rows', 0)}")
    print(f"Skipped (not cached): {stats.get('skipped_not_geocoded', 0)} groups")
    for line in stats.get("changes", []):
        print("  " + line)
    return 0


async def _invalidate_search_cache() -> int:
    """Clear search:v2:* after new pins land (no-op when Redis is offline)."""
    await init_redis()
    try:
        return await cache_delete_prefix("search:v2:")
    finally:
        await close_redis()


def cmd_rollback(session: Session, dry_run: bool) -> int:
    if dry_run:
        rows = session.execute(
            select(func.count())
            .select_from(FloodEvent)
            .where(FloodEvent.coord_source == "geocoded_street")
        ).scalar_one()
        print(f"Would roll back {rows} geocoded row(s) (dry run).")
        session.rollback()
        return 0
    count = rollback(session)
    print(f"Rolled back {count} geocoded row(s).")
    return 0


def default_paths(provider: str) -> tuple[Path, Path, Path]:
    """Nominatim uses the plan's file names; other providers get a suffix."""
    if provider == "nominatim":
        return DEFAULT_CACHE, DEFAULT_REPORT, DEFAULT_SUMMARY
    return (
        DEFAULT_CACHE.with_name(f"tier1_cache_{provider}.json"),
        DEFAULT_REPORT.with_name(f"tier1_report_{provider}.csv"),
        DEFAULT_SUMMARY.with_name(f"tier1_summary_{provider}.md"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--select", action="store_true", help="Show Tier-1 groups (read-only).")
    mode.add_argument("--geocode", action="store_true", help="Run the geocoder into the cache.")
    mode.add_argument("--report", action="store_true", help="Write CSV + summary from the cache.")
    mode.add_argument("--apply", action="store_true", help="Write coordinates and flags.")
    mode.add_argument("--rollback", action="store_true", help="Clear geocoded streets.")
    mode.add_argument(
        "--reconcile",
        action="store_true",
        help="Re-check already-written rows against the current matching rules "
        "(move or clear the ones that no longer pass). Use --dry-run first.",
    )
    parser.add_argument("--dry-run", action="store_true", help="With --apply/--rollback: no writes.")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of groups.")
    parser.add_argument(
        "--provider",
        choices=("nominatim", "photon"),
        default="nominatim",
        help="Geocoder (default nominatim; photon is an OSM fallback for blocked networks).",
    )
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--report-csv", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument(
        "--database-url",
        default=settings.database_sync_url,
    )
    parser.add_argument(
        "--include-applied",
        action="store_true",
        help="With --geocode: also look up places already written (for --reconcile).",
    )
    parser.add_argument("--min-interval", type=float, default=1.1)
    parser.add_argument("--timeout", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    default_cache, default_report, default_summary = default_paths(args.provider)
    cache_path = args.cache or default_cache
    report_path = args.report_csv or default_report
    summary_path = args.summary or default_summary
    engine: Engine = create_engine(args.database_url)
    try:
        with Session(engine) as session:
            if args.geocode:
                return cmd_geocode(
                    session,
                    cache_path,
                    args.limit,
                    args.min_interval,
                    args.timeout,
                    provider=args.provider,
                    include_applied=args.include_applied,
                )
            if args.report:
                return cmd_report(
                    session,
                    cache_path,
                    report_path,
                    summary_path,
                    args.limit,
                    provider=args.provider,
                )
            if args.apply:
                return cmd_apply(
                    session,
                    cache_path,
                    args.limit,
                    dry_run=args.dry_run,
                    provider=args.provider,
                )
            if args.reconcile:
                return cmd_reconcile(
                    session, cache_path, dry_run=args.dry_run, provider=args.provider
                )
            if args.rollback:
                return cmd_rollback(session, dry_run=args.dry_run)
            return cmd_select(session, args.limit)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
