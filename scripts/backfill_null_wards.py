"""FIR-73: backfill flood_events rows with NULL ward by substring-matching
location_name against location_aliases (longest alias wins = most specific).

resolve_location() in api/utils/ward_lookup.py only does exact alias_norm
matches, so long descriptive strings like "Đường Nguyễn Chí Thanh cổng Đài
truyền hình Việt Nam" never hit the "nguyen chi thanh" alias. This script
fixes that for existing data and is idempotent — safe to re-run.

Run:
  "C:\\Program Files\\PyManager\\python.exe" -m scripts.backfill_null_wards
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select, update

from api.database import AsyncSessionLocal
from api.models.flood_event import FloodEvent
from api.models.location_alias import LocationAlias
from api.models.ward_centroid import WardCentroid
from api.utils.text import normalize_ward

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def backfill() -> None:
    async with AsyncSessionLocal() as db:
        # Find NULL ward rows
        null_rows = (
            await db.execute(
                select(FloodEvent.id, FloodEvent.location_name).where(FloodEvent.ward.is_(None))
            )
        ).fetchall()

        if not null_rows:
            log.info("No NULL ward rows. Nothing to backfill.")
            return

        # Load all aliases joined with ward centroid data; sort longest-first
        # so the most specific alias wins ("le quang dao" beats "le duc tho")
        all_aliases = (
            await db.execute(
                select(
                    LocationAlias.alias_norm,
                    LocationAlias.ward_name,
                    WardCentroid.district,
                    WardCentroid.lat,
                    WardCentroid.lng,
                ).join(WardCentroid, WardCentroid.ward_name == LocationAlias.ward_name)
            )
        ).fetchall()
        aliases_sorted = sorted(all_aliases, key=lambda a: -len(a.alias_norm))

        resolved = 0
        unresolved: list[tuple[str, str]] = []
        for row in null_rows:
            normalized = normalize_ward(row.location_name or "")
            if not normalized:
                unresolved.append((str(row.id), row.location_name or ""))
                continue

            match = next((a for a in aliases_sorted if a.alias_norm in normalized), None)
            if not match:
                unresolved.append((str(row.id), row.location_name))
                continue

            geom = func.ST_SetSRID(func.ST_MakePoint(match.lng, match.lat), 4326)
            await db.execute(
                update(FloodEvent)
                .where(FloodEvent.id == row.id)
                .values(
                    ward=match.ward_name,
                    district=match.district,
                    lat=match.lat,
                    lng=match.lng,
                    geom=geom,
                )
            )
            resolved += 1
            log.info(
                "RESOLVED %s | %r → %s (via %r)",
                row.id, row.location_name[:60], match.ward_name, match.alias_norm,
            )

        await db.commit()
        log.info("Backfill complete: %d resolved, %d unresolved.", resolved, len(unresolved))
        for rid, name in unresolved:
            log.warning("UNRESOLVED %s | %r", rid, name)


if __name__ == "__main__":
    asyncio.run(backfill())
