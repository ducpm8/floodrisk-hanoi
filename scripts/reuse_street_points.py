"""
Tier 3 step 1: reuse street coordinates that are already in the database.

Target rows : street-type `location_name`, `district` known, `ward` NULL, no
              coordinates, never processed (coord_flag IS NULL).
Reference   : rows this pipeline already placed (coord_source='geocoded_street')
              with a known ward, in the SAME district, whose street key matches.

No network. The point of the best reference is copied (highest
coord_confidence; ties -> the point shared by most reference rows). The new
confidence is min(0.60, reference confidence): the reference is the same street
but possibly a different stretch of it.

Skipped on purpose: a street whose reference points are spread over more than
3 km within the district (a long street or two streets sharing a name).

    python -m scripts.reuse_street_points --select
    python -m scripts.reuse_street_points --apply [--dry-run]
    python -m scripts.reuse_street_points --rollback [--dry-run]

--apply writes the ids it changed to .gstack/geocode/reuse_applied_ids.csv;
--rollback clears exactly those rows (coord_source is shared with the geocoder,
so the CSV is the only precise record).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
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
    _invalidate_search_cache,
    clean_street,
    haversine_km,
    is_admin_only_name,
    is_street_name,
    street_tokens,
)

log = logging.getLogger("reuse_street_points")

IDS_PATH = REPO_ROOT / ".gstack" / "geocode" / "reuse_applied_ids.csv"
MAX_CONFIDENCE = 0.60
MAX_SPREAD_KM = 3.0


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def street_key(name: str | None) -> tuple[str, ...] | None:
    """Type-insensitive key: 'Phố Hoa Bằng' == 'Đường Hoa Bằng (phường Yên Hòa)'.

    Number-only streets keep their type ('Đường 32' != 'Ngõ 32'), and alleys keep
    their number ('Ngõ 100 Đội Cấn' != 'Phố Đội Cấn').
    """
    if not name:
        return None
    street = clean_street(name)
    tokens = tuple(street_tokens(street))
    if not tokens:
        return None
    if all(token.isdigit() for token in tokens):
        return ("#",) + tuple(normalize_ward(street).split())
    return tokens


@dataclass(frozen=True)
class Reference:
    lat: float
    lng: float
    confidence: float


@dataclass
class Match:
    key: tuple[str, ...]
    district: str
    location_names: list[str]
    row_ids: list
    reference: Reference | None = None
    confidence: float | None = None
    skipped: str = ""       # '' | no_reference | spread


def best_reference(points: Sequence[Reference]) -> tuple[Reference | None, str]:
    """-> (reference, skip_reason). Highest confidence, then the most shared point."""
    if not points:
        return None, "no_reference"
    for a in points:
        for b in points:
            if haversine_km(a.lat, a.lng, b.lat, b.lng) > MAX_SPREAD_KM:
                return None, "spread"
    support = Counter((round(p.lat, 4), round(p.lng, 4)) for p in points)
    best = max(
        points,
        key=lambda p: (p.confidence, support[(round(p.lat, 4), round(p.lng, 4))]),
    )
    return best, ""


def reuse_confidence(reference_confidence: float) -> float:
    return round(min(MAX_CONFIDENCE, reference_confidence), 2)


def is_target(row) -> bool:
    return (
        row.lat is None
        and row.coord_source is None
        and row.coord_flag is None
        and row.ward is None
        and bool(row.district)
        and is_street_name(row.location_name)
        and not is_admin_only_name(row.location_name)
    )


def is_reference(row) -> bool:
    return (
        row.lat is not None
        and row.coord_source == "geocoded_street"
        and row.ward is not None
        and bool(row.district)
        and row.coord_confidence is not None
    )


def build_matches(rows: Iterable) -> list[Match]:
    rows = list(rows)
    refs: dict[tuple, list[Reference]] = defaultdict(list)
    for row in rows:
        if is_reference(row):
            key = street_key(row.location_name)
            if key:
                refs[(key, row.district)].append(
                    Reference(float(row.lat), float(row.lng), float(row.coord_confidence))
                )

    matches: dict[tuple, Match] = {}
    for row in rows:
        if not is_target(row):
            continue
        key = street_key(row.location_name)
        if not key:
            continue
        slot = (key, row.district)
        match = matches.setdefault(slot, Match(key, row.district, [], []))
        match.row_ids.append(row.id)
        if row.location_name not in match.location_names:
            match.location_names.append(row.location_name)

    for slot, match in matches.items():
        reference, reason = best_reference(refs.get(slot, []))
        match.skipped = reason
        if reference:
            match.reference = reference
            match.confidence = reuse_confidence(reference.confidence)
    return sorted(matches.values(), key=lambda m: -len(m.row_ids))


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def load_rows(session: Session) -> list:
    return list(
        session.execute(
            select(
                FloodEvent.id,
                FloodEvent.location_name,
                FloodEvent.ward,
                FloodEvent.district,
                FloodEvent.lat,
                FloodEvent.lng,
                FloodEvent.coord_source,
                FloodEvent.coord_flag,
                FloodEvent.coord_confidence,
            )
        ).all()
    )


def apply_matches(session: Session, matches: Sequence[Match], apply: bool = True) -> dict:
    stats: Counter[str] = Counter()
    changed: list = []
    for match in matches:
        if match.reference is None or match.confidence is None:
            stats[f"skipped_{match.skipped}_rows"] += len(match.row_ids)
            stats[f"skipped_{match.skipped}_streets"] += 1
            continue
        lat, lng = round(match.reference.lat, 6), round(match.reference.lng, 6)
        stats["matched_streets"] += 1
        for start in range(0, len(match.row_ids), 500):
            chunk = match.row_ids[start:start + 500]
            conditions = [
                FloodEvent.id.in_(chunk),
                FloodEvent.lat.is_(None),
                FloodEvent.coord_source.is_(None),
                FloodEvent.coord_flag.is_(None),
                FloodEvent.ward.is_(None),
            ]
            if apply:
                result = session.execute(
                    update(FloodEvent).where(*conditions).values(
                        lat=lat,
                        lng=lng,
                        geom=func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326).cast(
                            Geography(geometry_type="POINT", srid=4326)
                        ),
                        coord_source="geocoded_street",
                        coord_confidence=match.confidence,
                    )
                )
                stats["rows"] += result.rowcount or 0
            else:
                stats["rows"] += session.execute(
                    select(func.count()).select_from(FloodEvent).where(*conditions)
                ).scalar_one()
            changed.extend(
                (str(row_id), lat, lng, match.confidence, match.district) for row_id in chunk
            )
    if apply:
        session.commit()
    stats["changed"] = changed  # type: ignore[assignment]
    return dict(stats)


def write_ids(changed: Sequence[tuple], path: Path = IDS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "lat", "lng", "confidence", "district"])
        writer.writerows(changed)


def read_ids(path: Path = IDS_PATH) -> list[str]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [row["id"] for row in csv.DictReader(handle)]


def rollback(session: Session, ids: Sequence[str], apply: bool = True) -> int:
    total = 0
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        conditions = [
            FloodEvent.id.in_(chunk),
            FloodEvent.coord_source == "geocoded_street",
            FloodEvent.ward.is_(None),
        ]
        if apply:
            total += session.execute(
                update(FloodEvent).where(*conditions).values(
                    lat=None, lng=None, geom=None, coord_source=None, coord_confidence=None
                )
            ).rowcount or 0
        else:
            total += session.execute(
                select(func.count()).select_from(FloodEvent).where(*conditions)
            ).scalar_one()
    if apply:
        session.commit()
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def summarize(matches: Sequence[Match]) -> str:
    total = sum(len(m.row_ids) for m in matches)
    ok = [m for m in matches if m.reference]
    lines = [
        f"Target rows: {total} in {len(matches)} (street, district) pairs",
        f"  reusable   : {sum(len(m.row_ids) for m in ok):>5} rows / {len(ok)} pairs",
    ]
    for reason in ("no_reference", "spread"):
        subset = [m for m in matches if m.skipped == reason]
        lines.append(f"  {reason:<11}: {sum(len(m.row_ids) for m in subset):>5} rows / {len(subset)} pairs")
    conf = Counter(m.confidence for m in ok for _ in m.row_ids)
    lines.append("  confidence : " + ", ".join(f"{k}={v}" for k, v in sorted(conf.items())))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    for flag in ("select", "apply", "rollback"):
        mode.add_argument(f"--{flag}", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--database-url", default=settings.database_sync_url)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    engine = create_engine(args.database_url)
    try:
        with Session(engine) as session:
            if args.rollback:
                ids = read_ids()
                count = rollback(session, ids, apply=not args.dry_run)
                print(f"{'DRY RUN: would clear' if args.dry_run else 'Cleared'} {count} rows ({len(ids)} ids in CSV)")
                if not args.dry_run:
                    asyncio.run(_invalidate_search_cache())
                return 0

            matches = build_matches(load_rows(session))
            print(summarize(matches))
            if not args.apply:
                print("\nTop reusable pairs:")
                for m in [m for m in matches if m.reference][:15]:
                    print(f"  {len(m.row_ids):>4} | {m.location_names[0][:42]} | {m.district} | conf {m.confidence} | ({m.reference.lat:.4f},{m.reference.lng:.4f})")
                print("\nTop skipped pairs:")
                for m in [m for m in matches if not m.reference][:8]:
                    print(f"  {len(m.row_ids):>4} | {m.location_names[0][:42]} | {m.district} | {m.skipped}")
                return 0

            stats = apply_matches(session, matches, apply=not args.dry_run)
            changed = stats.pop("changed", [])
            if args.dry_run:
                session.rollback()
            else:
                write_ids(changed)
                asyncio.run(_invalidate_search_cache())
            print(f"\n{'DRY RUN — nothing written' if args.dry_run else 'APPLIED'}")
            print(f"Rows updated: {stats.get('rows', 0)} in {stats.get('matched_streets', 0)} streets")
            if not args.dry_run:
                print(f"Ids saved to {IDS_PATH}")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
