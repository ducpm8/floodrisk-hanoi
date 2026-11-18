"""
Seed the 126 Hà Nội phường/xã in effect from 2025-07-01, plus old-name aliases.

Nghị quyết 1656/NQ-UBTVQH15 (effective 2025-07-01) merged Hà Nội's old
commune/ward units into 126 units (51 phường, 75 xã). `ward_centroids` was
seeded from pre-2025 OSM boundaries, so post-2025 events ("Phường Từ Liêm",
"Xã Thư Lâm", …) do not resolve today.

The verified dataset lives in `api/seeds/data/hanoi_wards_2025.json`:
  - names + type: Nghị quyết 1656/NQ-UBTVQH15 (S2's `fullName` matches it)
  - coordinates: representative point of each polygon from
    ThangLeQuoc/vietnamese-provinces-database v5.2.0 GeoJSON (MIT)
  - predecessors: quangtam/vietnam-address-database ward_mappings (old → new)

This script is idempotent and defaults to a dry run. Old rows are never
updated: the primary key is `ward_name`, so an old unit and a 2025 unit that
share a name cannot both exist. Same-name collisions are reported for manual
review (see the plan's decision #2).

Usage:
    cd api/
    python -m seeds.seed_wards_2025              # dry run (default)
    python -m seeds.seed_wards_2025 --apply      # insert missing rows + aliases

Rollback: the report lists every row this script inserts. Delete those
`ward_centroids` rows (they cascade to their aliases) or the `location_aliases`
rows with `source = 'manual'` that point at the new units.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from pathlib import Path

from geoalchemy2.elements import WKTElement
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from api.models import LocationAlias, WardCentroid  # noqa: E402
from api.utils.text import normalize_ward  # noqa: E402
from api.utils.ward_lookup import _ward_lookup  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

DEFAULT_DATASET = Path(__file__).resolve().parent / "data" / "hanoi_wards_2025.json"

# Same Hà Nội sanity bounding box as the admin CSV import
# (api/routers/admin_import.py:50-51).
LAT_MIN, LAT_MAX = 20.5, 21.5
LNG_MIN, LNG_MAX = 105.2, 106.1

ADMIN_PREFIXES = ("phuong ", "xa ", "thi tran ", "quan ", "huyen ")
UNIT_TYPES = {"phuong", "xa", "thi_tran"}

ALIAS_CONFIDENCE = 0.9
ALIAS_TYPE = "other"
ALIAS_SOURCE = "manual"
PREDECESSOR_FLAG_KM = 5.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExistingWard:
    """The subset of a `ward_centroids` row the planner needs."""

    ward_name: str
    ward_name_vi: str
    lat: float
    lng: float


@dataclass(frozen=True)
class AliasPlan:
    alias_norm: str
    alias_vi: str
    ward_name: str
    predecessor_of: str


@dataclass
class SeedReport:
    dataset_units: int = 0
    inserts: list[dict] = field(default_factory=list)
    present_same_name: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    flagged: list[dict] = field(default_factory=list)
    aliases: list[AliasPlan] = field(default_factory=list)
    aliases_already: int = 0
    aliases_resolvable: int = 0
    aliases_unchanged: int = 0
    aliases_ambiguous: list[dict] = field(default_factory=list)
    applied: bool = False


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def load_dataset(path: Path | str = DEFAULT_DATASET) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def unit_norm(unit: Mapping) -> str:
    """`ward_centroids.ward_name` value for a dataset unit."""
    return normalize_ward(unit["name_vi"])


def bare_name(name: str) -> str:
    """Normalize and drop the admin-unit prefix ("Phường Từ Liêm" → "tu liem")."""
    norm = normalize_ward(name)
    for prefix in ADMIN_PREFIXES:
        if norm.startswith(prefix):
            return norm[len(prefix):]
    return norm


def in_hanoi_bbox(lat: float, lng: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def validate_dataset(dataset: Mapping) -> list[str]:
    """Structural validation of the bundled dataset (no DB access)."""
    errors: list[str] = []
    units = dataset.get("units") or []
    if dataset.get("count") != len(units):
        errors.append(f"count field {dataset.get('count')} != {len(units)} units")
    if len(units) != 126:
        errors.append(f"expected 126 units, got {len(units)}")

    seen: set[str] = set()
    for unit in units:
        name = unit.get("name_vi")
        if not name:
            errors.append("unit without name_vi")
            continue
        norm = normalize_ward(name)
        if norm in seen:
            errors.append(f"duplicate normalized name: {norm}")
        seen.add(norm)
        if unit.get("type") not in UNIT_TYPES:
            errors.append(f"{name}: unexpected type {unit.get('type')!r}")
        try:
            lat, lng = float(unit["lat"]), float(unit["lng"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{name}: missing/invalid lat/lng")
            continue
        if not in_hanoi_bbox(lat, lng):
            errors.append(f"{name}: ({lat}, {lng}) outside the Hà Nội bbox")
    return errors


def classify_unit(unit: Mapping, existing: Mapping[str, ExistingWard]) -> str:
    """Return 'missing' | 'present_same_name' | 'present_name_mismatch'."""
    row = existing.get(unit_norm(unit))
    if row is None:
        return "missing"
    if bare_name(row.ward_name_vi) == bare_name(unit["name_vi"]):
        return "present_same_name"
    # Same PK, different Vietnamese name: the OSM row is inconsistent
    # (e.g. "xa o dien" ↔ "Đan Phượng"). Report it, never update it.
    return "present_name_mismatch"


def plan_inserts(
    units: Sequence[Mapping],
    existing: Mapping[str, ExistingWard],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split units into (to insert, same-name present, conflicting present)."""
    inserts: list[dict] = []
    same_name: list[dict] = []
    conflicts: list[dict] = []
    for unit in units:
        status = classify_unit(unit, existing)
        if status == "missing":
            inserts.append(dict(unit))
            continue
        row = existing[unit_norm(unit)]
        if status == "present_same_name":
            same_name.append(dict(unit))
            continue
        conflicts.append(
            {
                "name_vi": unit["name_vi"],
                "ward_name": row.ward_name,
                "existing_name_vi": row.ward_name_vi,
                "distance_km": round(
                    haversine_km(
                        float(unit["lat"]), float(unit["lng"]), row.lat, row.lng
                    ),
                    3,
                ),
            }
        )
    return inserts, same_name, conflicts


def flag_far_from_predecessors(
    units: Sequence[Mapping],
    existing: Mapping[str, ExistingWard],
    inserted_norms: Set[str],
    max_km: float = PREDECESSOR_FLAG_KM,
) -> list[dict]:
    """Flag units whose point is far from the median of known predecessor rows.

    Median (not mean) keeps one wrong old row from dominating the comparison.
    Rows being inserted are treated as known so a batch is self-consistent.
    """
    unit_points = {
        unit_norm(u): (float(u["lat"]), float(u["lng"])) for u in units
    }
    flagged: list[dict] = []
    for unit in units:
        points: list[tuple[float, float]] = []
        for pred in unit.get("predecessors") or []:
            pred_norm = normalize_ward(pred.get("name_vi") or "")
            row = existing.get(pred_norm)
            if row is not None:
                points.append((row.lat, row.lng))
            elif pred_norm in inserted_norms and pred_norm in unit_points:
                points.append(unit_points[pred_norm])
        if not points:
            continue
        med_lat = sorted(p[0] for p in points)[len(points) // 2]
        med_lng = sorted(p[1] for p in points)[len(points) // 2]
        distance = haversine_km(float(unit["lat"]), float(unit["lng"]), med_lat, med_lng)
        if distance > max_km:
            flagged.append(
                {
                    "name_vi": unit["name_vi"],
                    "distance_km": round(distance, 3),
                    "known_predecessors": len(points),
                }
            )
    return flagged


def plan_aliases(
    units: Sequence[Mapping],
    resolvable: Callable[[str], bool],
    existing_alias_norms: Set[str] | frozenset[str],
) -> tuple[list[AliasPlan], dict]:
    """Plan old-name → 2025-unit aliases.

    An alias is only useful when the four-strategy ward lookup cannot resolve
    the old name (the lookup runs before the alias table). Names that already
    match a 2025 unit are left alone; an old name claimed by two different
    units is ambiguous and skipped for manual review.
    """
    new_norms = {unit_norm(unit) for unit in units}

    stats = {
        "already": 0,
        "resolvable": 0,
        "unchanged": 0,
        "ambiguous": [],
    }
    candidates: dict[str, dict] = {}
    for unit in sorted(units, key=lambda u: (u.get("code") or "", u["name_vi"])):
        target = unit_norm(unit)
        for pred in unit.get("predecessors") or []:
            alias_vi = pred.get("name_vi") or ""
            alias_norm = normalize_ward(alias_vi)
            if not alias_norm:
                continue
            if alias_norm == target:
                stats["unchanged"] += 1
                continue
            entry = candidates.setdefault(
                alias_norm, {"alias_vi": alias_vi, "targets": set(), "origins": set()}
            )
            entry["targets"].add(target)
            entry["origins"].add(unit["name_vi"])

    plans: list[AliasPlan] = []
    for alias_norm in sorted(candidates):
        entry = candidates[alias_norm]
        if alias_norm in existing_alias_norms:
            stats["already"] += 1
            continue
        if alias_norm in new_norms:
            # The 2025 unit itself carries this name; exact lookup wins.
            stats["unchanged"] += 1
            continue
        if resolvable(alias_norm):
            stats["resolvable"] += 1
            continue
        if len(entry["targets"]) > 1:
            stats["ambiguous"].append(
                {
                    "alias_vi": entry["alias_vi"],
                    "alias_norm": alias_norm,
                    "targets": sorted(entry["targets"]),
                }
            )
            continue
        plans.append(
            AliasPlan(
                alias_norm=alias_norm,
                alias_vi=entry["alias_vi"],
                ward_name=next(iter(entry["targets"])),
                predecessor_of=sorted(entry["origins"])[0],
            )
        )
    return plans, stats


# ---------------------------------------------------------------------------
# DB apply
# ---------------------------------------------------------------------------

def load_existing(session: Session) -> dict[str, ExistingWard]:
    rows = session.execute(select(WardCentroid)).scalars().all()
    return {
        row.ward_name: ExistingWard(
            ward_name=row.ward_name,
            ward_name_vi=row.ward_name_vi,
            lat=float(row.lat),
            lng=float(row.lng),
        )
        for row in rows
    }


def build_report(
    dataset: Mapping,
    existing: Mapping[str, ExistingWard],
    resolvable: Callable[[str], bool],
    existing_alias_norms: Set[str],
    max_predecessor_km: float = PREDECESSOR_FLAG_KM,
) -> SeedReport:
    units = dataset["units"]
    report = SeedReport(dataset_units=len(units))

    inserts, same_name, conflicts = plan_inserts(units, existing)
    report.inserts = inserts
    report.present_same_name = [u["name_vi"] for u in same_name]
    report.conflicts = conflicts

    inserted_norms = {unit_norm(u) for u in inserts}
    report.flagged = flag_far_from_predecessors(
        units, existing, inserted_norms, max_km=max_predecessor_km
    )

    aliases, stats = plan_aliases(units, resolvable, existing_alias_norms)
    report.aliases = aliases
    report.aliases_already = stats["already"]
    report.aliases_resolvable = stats["resolvable"]
    report.aliases_unchanged = stats["unchanged"]
    report.aliases_ambiguous = stats["ambiguous"]
    return report


def run(
    database_url: str,
    dataset: Mapping,
    apply: bool,
    max_predecessor_km: float = PREDECESSOR_FLAG_KM,
) -> SeedReport:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            existing = load_existing(session)
            alias_norms = set(
                session.execute(select(LocationAlias.alias_norm)).scalars().all()
            )
            conn = session.connection()
            report = build_report(
                dataset,
                existing,
                lambda name: _ward_lookup(conn, name) is not None,
                alias_norms,
                max_predecessor_km=max_predecessor_km,
            )

            if not apply:
                session.rollback()
                return report

            for unit in report.inserts:
                lat = round(float(unit["lat"]), 6)
                lng = round(float(unit["lng"]), 6)
                session.add(
                    WardCentroid(
                        ward_name=unit_norm(unit),
                        ward_name_vi=unit["name_vi"],
                        district=None,  # 2025 structure has no district level
                        lat=lat,
                        lng=lng,
                        geom=WKTElement(f"POINT({lng} {lat})", srid=4326),
                    )
                )
            session.flush()

            for alias in report.aliases:
                session.add(
                    LocationAlias(
                        alias_norm=alias.alias_norm,
                        alias_vi=alias.alias_vi,
                        ward_name=alias.ward_name,
                        alias_type=ALIAS_TYPE,
                        confidence=ALIAS_CONFIDENCE,
                        source=ALIAS_SOURCE,
                    )
                )
            session.commit()
            report.applied = True
            return report
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Reporting / CLI
# ---------------------------------------------------------------------------

def format_report(report: SeedReport, dry_run: bool) -> str:
    lines = [
        f"Dataset units: {report.dataset_units}",
        f"Mode: {'DRY RUN (nothing written)' if dry_run else 'APPLIED'}",
        "",
        f"ward_centroids: {len(report.inserts)} to insert, "
        f"{len(report.present_same_name)} already present with the same name, "
        f"{len(report.conflicts)} name conflicts (kept, manual review)",
    ]
    if report.conflicts:
        for conflict in report.conflicts:
            lines.append(
                f"  CONFLICT {conflict['ward_name']}: existing "
                f"{conflict['existing_name_vi']!r} vs new {conflict['name_vi']!r} "
                f"({conflict['distance_km']} km apart)"
            )
    if report.flagged:
        lines.append(
            f"\n{len(report.flagged)} unit(s) farther than "
            f"{PREDECESSOR_FLAG_KM:g} km from known predecessor centroids:"
        )
        for flag in report.flagged:
            lines.append(
                f"  FLAG {flag['name_vi']}: {flag['distance_km']} km "
                f"({flag['known_predecessors']} known predecessors)"
            )
    lines.extend(
        [
            "",
            f"location_aliases: {len(report.aliases)} to insert "
            f"({report.aliases_already} already present, "
            f"{report.aliases_resolvable} resolvable without an alias, "
            f"{report.aliases_unchanged} same as a 2025 unit name, "
            f"{len(report.aliases_ambiguous)} ambiguous)",
        ]
    )
    if report.aliases_ambiguous:
        for ambiguous in report.aliases_ambiguous:
            lines.append(
                f"  AMBIGUOUS {ambiguous['alias_vi']!r}: "
                f"{', '.join(ambiguous['targets'])}"
            )
    lines.append("")
    lines.append("Rollback (if applied):")
    if report.inserts:
        names = ", ".join(repr(unit_norm(u)) for u in report.inserts)
        lines.append(f"  DELETE FROM ward_centroids WHERE ward_name IN ({names});")
    if report.aliases:
        norms = ", ".join(repr(a.alias_norm) for a in report.aliases)
        lines.append(
            "  DELETE FROM location_aliases "
            f"WHERE source = '{ALIAS_SOURCE}' AND alias_norm IN ({norms});"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to the database (default: dry run).",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_SYNC_URL",
            "postgresql+psycopg2://flood:flood@localhost:5432/flooddb",
        ),
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--max-predecessor-km",
        type=float,
        default=PREDECESSOR_FLAG_KM,
        help="Flag units farther than this from known predecessor centroids.",
    )
    args = parser.parse_args(argv)

    dataset = load_dataset(args.dataset)
    errors = validate_dataset(dataset)
    if errors:
        for error in errors:
            log.error("dataset: %s", error)
        return 1

    report = run(
        args.database_url,
        dataset,
        apply=args.apply,
        max_predecessor_km=args.max_predecessor_km,
    )
    print(format_report(report, dry_run=not args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
