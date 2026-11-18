"""
Integration test: the 126 Hà Nội units from Nghị quyết 1656/NQ-UBTVQH15
(effective 2025-07-01) resolve through the real runtime chain.

Seeds `ward_centroids` + `location_aliases` via `seeds.seed_wards_2025`,
then asserts that:
  - every new unit resolves as level='ward'
  - a pre-2025 name that no longer exists resolves through its alias

Requires a real PostGIS database:
    docker compose -f infra/docker-compose.yml up -d db_test
    cd api/
    DATABASE_TEST_URL=postgresql+psycopg2://flood:flood@localhost:5433/flooddb_test \
        pytest tests/integration/test_ward_lookup_2025.py -v
"""

import pytest
from sqlalchemy import create_engine, text

from api.seeds import seed_wards_2025 as seed
from api.utils.ward_lookup import resolve_location


@pytest.fixture(scope="module")
def seeded(migrated_db: str):
    dataset = seed.load_dataset()
    report = seed.run(migrated_db, dataset, apply=True)
    assert report.applied

    yield report

    # Remove only what this test inserted, aliases first (FK → ward_centroids).
    alias_norms = [alias.alias_norm for alias in report.aliases]
    ward_names = [seed.unit_norm(unit) for unit in report.inserts]
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        if alias_norms:
            conn.execute(
                text(
                    "DELETE FROM location_aliases WHERE alias_norm = ANY(:norms)"
                ),
                {"norms": alias_norms},
            )
        if ward_names:
            conn.execute(
                text("DELETE FROM ward_centroids WHERE ward_name = ANY(:names)"),
                {"names": ward_names},
            )
    engine.dispose()


@pytest.fixture(scope="module")
def conn(migrated_db: str, seeded):
    engine = create_engine(migrated_db)
    with engine.connect() as connection:
        yield connection
    engine.dispose()


def test_every_2025_unit_resolves(conn, seeded) -> None:
    dataset = seed.load_dataset()
    missing = []
    for unit in dataset["units"]:
        resolved = resolve_location(conn, unit["name_vi"], client=None)
        if resolved is None:
            missing.append(unit["name_vi"])
        else:
            assert resolved.resolution_level == "ward", unit["name_vi"]
            assert resolved.ward_name == seed.unit_norm(unit), unit["name_vi"]
    assert missing == []


@pytest.mark.parametrize("raw_name", ["xa thu lam", "phuong tu liem", "xa my duc"])
def test_new_names_resolve_to_their_centroid(conn, seeded, raw_name: str) -> None:
    resolved = resolve_location(conn, raw_name, client=None)
    assert resolved is not None
    assert resolved.resolution_level == "ward"
    assert resolved.ward_name == raw_name
    assert 20.5 <= resolved.lat <= 21.5
    assert 105.2 <= resolved.lng <= 106.1


def test_old_name_resolves_through_alias(conn, seeded) -> None:
    # Thị trấn Vân Đình was replaced by Xã Vân Đình on 2025-07-01.
    resolved = resolve_location(conn, "Thị trấn Vân Đình", client=None)
    assert resolved is not None
    assert resolved.ward_name == "xa van dinh"
    assert resolved.resolution_level == "other"


def test_seed_is_idempotent(migrated_db: str, seeded) -> None:
    dataset = seed.load_dataset()
    second = seed.run(migrated_db, dataset, apply=True)
    assert second.inserts == []
    assert second.aliases == []
    assert len(second.present_same_name) + len(second.conflicts) == 126
