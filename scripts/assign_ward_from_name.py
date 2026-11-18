"""
Tier 3 step 2: assign `ward` from a ward named inside `location_name`.

    "Phố Dương Đình Nghệ (P. Cầu Giấy)"            -> phuong cau giay
    "Phố Trần Bình (UBND phường Mai Dịch – ...)"   -> phuong mai dich
    "Quốc lộ 3 (qua xã Mai Lâm)"                    -> xa mai lam

Only `ward` is written (never coordinates): the row then qualifies for Tier 1
(`scripts/geocode_tier1.py`), which geocodes the street and validates it
against that ward.

Target rows: street name, `district` known, `ward` NULL, no coordinates,
coord_flag NULL. A row is assigned only when
  - exactly ONE distinct ward is mentioned ("từ phường A sang phường B" is skipped);
  - the written prefix (phường / xã / thị trấn / P. / TT.) resolves EXACTLY to a
    `ward_centroids` row (no prefix guessing: Thanh Xuân is both phường and xã);
  - the ward is compatible with the row's district, judged from the districts
    the same ward already has in the events table (unknown history = accepted
    but listed as 'unverified' in the preview).

    python -m scripts.assign_ward_from_name --select          # preview + CSV
    python -m scripts.assign_ward_from_name --apply [--dry-run]
    python -m scripts.assign_ward_from_name --rollback [--dry-run]

--apply stores every change in .gstack/geocode/ward_assigned.csv (id, old, new)
so --rollback restores ward = NULL for exactly those rows.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.config import settings  # noqa: E402
from api.models.flood_event import FloodEvent  # noqa: E402
from api.models.ward_centroid import WardCentroid  # noqa: E402
from api.utils.text import normalize_ward  # noqa: E402
from scripts.geocode_tier1 import (  # noqa: E402
    _invalidate_search_cache,
    is_admin_only_name,
    is_street_name,
)

log = logging.getLogger("assign_ward_from_name")

PREVIEW_PATH = REPO_ROOT / ".gstack" / "geocode" / "ward_assign_preview.csv"
CHANGES_PATH = REPO_ROOT / ".gstack" / "geocode" / "ward_assigned.csv"

_PREFIX_RE = re.compile(
    r"(?<![\w])(phường|xã|thị\s+trấn|p\.|tt\.)\s*", re.IGNORECASE
)
_PREFIXES = {
    "phường": "phuong", "p.": "phuong",
    "xã": "xa",
    "thị trấn": "thi tran", "tt.": "thi tran",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _capitalized_run(text: str, limit: int = 4) -> str:
    """Leading run of capitalised words: 'Mai Dịch – Bệnh viện' -> 'Mai Dịch'."""
    words: list[str] = []
    for raw in text.split():
        word = raw.strip(",;:()–—-/")
        if not word or not word[0].isupper():
            break
        words.append(word)
        if len(words) >= limit or raw != word:  # punctuation ends the name
            break
    return " ".join(words)


def ward_mentions(name: str) -> list[str]:
    """Normalised ward keys written in the name: ['phuong mai dich', ...] (deduped)."""
    found: list[str] = []
    for match in _PREFIX_RE.finditer(name):
        prefix = _PREFIXES[re.sub(r"\s+", " ", match.group(1).lower())]
        candidate = _capitalized_run(name[match.end():])
        if not candidate:
            continue
        key = f"{prefix} {normalize_ward(candidate)}"
        if key not in found:
            found.append(key)
    return found


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


@dataclass
class Verdict:
    row_id: object
    name: str
    district: str
    mentions: list[str]
    ward: str | None = None
    verdict: str = ""       # ok | unverified | multi | unresolved | district_mismatch | none


def judge(
    row,
    ward_names: set[str],
    ward_districts: Mapping[str, set[str]],
) -> Verdict:
    mentions = ward_mentions(row.location_name)
    result = Verdict(row.id, row.location_name, row.district, mentions)
    if not mentions:
        result.verdict = "none"
        return result
    if len(mentions) > 1:
        result.verdict = "multi"
        return result
    ward = mentions[0]
    if ward not in ward_names:
        result.verdict = "unresolved"
        return result
    known = ward_districts.get(ward)
    if known and row.district not in known:
        result.ward = ward
        result.verdict = "district_mismatch"
        return result
    result.ward = ward
    result.verdict = "ok" if known else "unverified"
    return result


def evaluate(rows: Iterable, ward_names: set[str], ward_districts: Mapping[str, set[str]]) -> list[Verdict]:
    return [judge(row, ward_names, ward_districts) for row in rows if is_target(row)]


def ward_district_history(rows: Iterable, ward_names: set[str]) -> dict[str, set[str]]:
    history: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        # 'Unknown' is a placeholder written by an early import, not a district.
        if row.ward in ward_names and row.district and row.district != "Unknown":
            history[row.ward].add(row.district)
    return history


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def load(session: Session) -> tuple[list, set[str]]:
    rows = list(
        session.execute(
            select(
                FloodEvent.id, FloodEvent.location_name, FloodEvent.ward,
                FloodEvent.district, FloodEvent.lat, FloodEvent.coord_source,
                FloodEvent.coord_flag,
            )
        ).all()
    )
    names = {w for (w,) in session.execute(select(WardCentroid.ward_name)).all()}
    return rows, names


def apply_verdicts(session: Session, verdicts: Sequence[Verdict], apply: bool = True) -> tuple[int, list]:
    chosen = [v for v in verdicts if v.verdict in ("ok", "unverified") and v.ward]
    changes: list = []
    total = 0
    by_ward: dict[str, list] = defaultdict(list)
    for v in chosen:
        by_ward[v.ward].append(v.row_id)
    for ward, ids in by_ward.items():
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            conditions = [FloodEvent.id.in_(chunk), FloodEvent.ward.is_(None), FloodEvent.lat.is_(None)]
            if apply:
                total += session.execute(
                    update(FloodEvent).where(*conditions).values(ward=ward)
                ).rowcount or 0
            else:
                total += session.execute(
                    select(func.count()).select_from(FloodEvent).where(*conditions)
                ).scalar_one()
            changes.extend((str(i), "", ward) for i in chunk)
    if apply:
        session.commit()
    return total, changes


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def rollback(session: Session, apply: bool = True) -> tuple[int, int]:
    if not CHANGES_PATH.exists():
        return 0, 0
    with CHANGES_PATH.open(encoding="utf-8-sig", newline="") as handle:
        entries = [(row["id"], row["new_ward"]) for row in csv.DictReader(handle)]
    total = 0
    for ward in {w for _, w in entries}:
        ids = [i for i, w in entries if w == ward]
        for start in range(0, len(ids), 500):
            conditions = [
                FloodEvent.id.in_(ids[start:start + 500]),
                FloodEvent.ward == ward,
                FloodEvent.lat.is_(None),   # never un-ward a row that was already placed
            ]
            if apply:
                total += session.execute(
                    update(FloodEvent).where(*conditions).values(ward=None)
                ).rowcount or 0
            else:
                total += session.execute(
                    select(func.count()).select_from(FloodEvent).where(*conditions)
                ).scalar_one()
    if apply:
        session.commit()
    return total, len(entries)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def summarize(verdicts: Sequence[Verdict]) -> str:
    counts: Counter[str] = Counter(v.verdict for v in verdicts)
    lines = [f"Target rows: {len(verdicts)}"]
    for key in ("ok", "unverified", "district_mismatch", "multi", "unresolved", "none"):
        lines.append(f"  {key:<18} {counts.get(key, 0)}")
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
                changed, listed = rollback(session, apply=not args.dry_run)
                print(f"{'DRY RUN: would clear' if args.dry_run else 'Cleared'} ward on {changed} rows ({listed} in CSV)")
                if not args.dry_run:
                    asyncio.run(_invalidate_search_cache())
                return 0

            rows, ward_names = load(session)
            verdicts = evaluate(rows, ward_names, ward_district_history(rows, ward_names))
            print(summarize(verdicts))
            written = [v for v in verdicts if v.verdict != "none"]
            write_csv(
                PREVIEW_PATH,
                ["id", "district", "location_name", "mentions", "ward", "verdict"],
                [(v.row_id, v.district, v.name, " | ".join(v.mentions), v.ward or "", v.verdict) for v in written],
            )
            print(f"Preview CSV: {PREVIEW_PATH} ({len(written)} rows with a ward mention)")

            if not args.apply:
                for v in sorted(written, key=lambda v: v.verdict)[:40]:
                    print(f"  [{v.verdict:<17}] {v.name[:64]} | {v.district} -> {v.ward or v.mentions}")
                return 0

            total, changes = apply_verdicts(session, verdicts, apply=not args.dry_run)
            if args.dry_run:
                session.rollback()
            else:
                write_csv(CHANGES_PATH, ["id", "old_ward", "new_ward"], changes)
                asyncio.run(_invalidate_search_cache())
            print(f"\n{'DRY RUN — nothing written' if args.dry_run else 'APPLIED'}: ward set on {total} rows")
            if not args.dry_run:
                print(f"Changes saved to {CHANGES_PATH}")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
