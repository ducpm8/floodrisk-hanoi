"""
Unit tests for api/seeds/seed_wards_2025.py

No database required: the planner helpers are pure, and the bundled dataset
is read from disk. Covers dataset integrity, insert/collision classification,
alias planning, and the dry-run-by-default CLI.
"""

import pytest

from api.seeds import seed_wards_2025 as seed
from api.seeds.seed_wards_2025 import (
    AliasPlan,
    ExistingWard,
    bare_name,
    classify_unit,
    flag_far_from_predecessors,
    in_hanoi_bbox,
    load_dataset,
    plan_aliases,
    plan_inserts,
    unit_norm,
    validate_dataset,
)


@pytest.fixture(scope="module")
def dataset() -> dict:
    return load_dataset()


@pytest.fixture(scope="module")
def units(dataset: dict) -> list[dict]:
    return dataset["units"]


def _existing(
    name_vi: str,
    lat: float = 21.0,
    lng: float = 105.8,
    ward_name: str | None = None,
) -> ExistingWard:
    return ExistingWard(
        ward_name=ward_name or unit_norm({"name_vi": name_vi}),
        ward_name_vi=name_vi,
        lat=lat,
        lng=lng,
    )


# ---------------------------------------------------------------------------
# Bundled dataset
# ---------------------------------------------------------------------------

class TestDataset:
    def test_has_126_units(self, dataset: dict) -> None:
        assert dataset["count"] == 126
        assert len(dataset["units"]) == 126

    def test_type_split_is_51_phuong_75_xa(self, units: list[dict]) -> None:
        types = [u["type"] for u in units]
        assert types.count("phuong") == 51
        assert types.count("xa") == 75
        assert "thi_tran" not in types

    def test_names_are_unique_after_normalization(self, units: list[dict]) -> None:
        norms = [unit_norm(u) for u in units]
        assert len(norms) == len(set(norms))

    def test_every_name_keeps_its_admin_prefix(self, units: list[dict]) -> None:
        for unit in units:
            assert unit_norm(unit).startswith(("phuong ", "xa ")), unit["name_vi"]

    def test_all_points_inside_hanoi_bbox(self, units: list[dict]) -> None:
        for unit in units:
            assert in_hanoi_bbox(unit["lat"], unit["lng"]), unit["name_vi"]

    def test_all_units_have_predecessors(self, units: list[dict]) -> None:
        for unit in units:
            assert unit["predecessors"], f"{unit['name_vi']} has no predecessors"
            for pred in unit["predecessors"]:
                assert pred.get("name_vi"), f"{unit['name_vi']} has a nameless predecessor"

    def test_bundled_dataset_passes_validation(self, dataset: dict) -> None:
        assert validate_dataset(dataset) == []

    def test_source_metadata_is_recorded(self, dataset: dict) -> None:
        assert dataset["source_version"]
        assert dataset["source_license"] == "MIT"
        assert "1656" in dataset["authority"]


class TestValidation:
    def _dataset(self, units: list[dict]) -> dict:
        return {"count": len(units), "units": units}

    def test_flags_out_of_bbox(self) -> None:
        bad = {"name_vi": "Xã Xa", "type": "xa", "lat": 10.0, "lng": 105.8}
        errors = validate_dataset(self._dataset([bad]))
        assert any("outside" in e for e in errors)

    def test_flags_duplicate_names(self) -> None:
        unit = {"name_vi": "Xã Một", "type": "xa", "lat": 21.0, "lng": 105.8}
        errors = validate_dataset(self._dataset([unit, dict(unit)]))
        assert any("duplicate" in e for e in errors)

    def test_flags_wrong_count(self) -> None:
        unit = {"name_vi": "Xã Một", "type": "xa", "lat": 21.0, "lng": 105.8}
        errors = validate_dataset(self._dataset([unit]))
        assert any("expected 126" in e for e in errors)

    def test_flags_bad_type(self) -> None:
        unit = {"name_vi": "Xã Một", "type": "other", "lat": 21.0, "lng": 105.8}
        errors = validate_dataset(self._dataset([unit]))
        assert any("unexpected type" in e for e in errors)


class TestNormalizationHelpers:
    def test_unit_norm_keeps_prefix(self) -> None:
        assert unit_norm({"name_vi": "Phường Từ Liêm"}) == "phuong tu liem"

    def test_bare_name_strips_prefix(self) -> None:
        assert bare_name("Phường Từ Liêm") == "tu liem"
        assert bare_name("Xã Thư Lâm") == "thu lam"

    def test_bbox_boundaries(self) -> None:
        assert in_hanoi_bbox(20.5, 105.2)
        assert in_hanoi_bbox(21.5, 106.1)
        assert not in_hanoi_bbox(20.499, 105.8)
        assert not in_hanoi_bbox(21.0, 106.101)


# ---------------------------------------------------------------------------
# Insert planning
# ---------------------------------------------------------------------------

class TestClassifyUnit:
    def test_missing(self) -> None:
        unit = {"name_vi": "Xã Mới", "type": "xa", "lat": 21.0, "lng": 105.8}
        assert classify_unit(unit, {}) == "missing"

    def test_present_same_name(self) -> None:
        unit = {"name_vi": "Phường Từ Liêm", "type": "phuong", "lat": 21.0, "lng": 105.8}
        existing = {"phuong tu liem": _existing("Phường Từ Liêm")}
        assert classify_unit(unit, existing) == "present_same_name"

    def test_present_name_mismatch(self) -> None:
        # Pre-existing inconsistent OSM row: "xa o dien" ↔ "Đan Phượng".
        unit = {"name_vi": "Xã Ô Diên", "type": "xa", "lat": 21.0, "lng": 105.8}
        existing = {
            "xa o dien": _existing("Đan Phượng", ward_name="xa o dien")
        }
        assert classify_unit(unit, existing) == "present_name_mismatch"


class TestPlanInserts:
    def test_only_missing_units_are_inserted(self) -> None:
        units = [
            {"name_vi": "Phường Cũ", "type": "phuong", "lat": 21.0, "lng": 105.8},
            {"name_vi": "Phường Mới", "type": "phuong", "lat": 21.01, "lng": 105.81},
        ]
        existing = {"phuong cu": _existing("Phường Cũ")}
        inserts, same_name, conflicts = plan_inserts(units, existing)
        assert [u["name_vi"] for u in inserts] == ["Phường Mới"]
        assert [u["name_vi"] for u in same_name] == ["Phường Cũ"]
        assert conflicts == []

    def test_name_conflict_is_reported_not_inserted(self) -> None:
        units = [{"name_vi": "Xã Ô Diên", "type": "xa", "lat": 21.09, "lng": 105.67}]
        existing = {
            "xa o dien": _existing(
                "Đan Phượng", lat=21.0882, lng=105.6706, ward_name="xa o dien"
            )
        }
        inserts, same_name, conflicts = plan_inserts(units, existing)
        assert inserts == []
        assert same_name == []
        assert conflicts[0]["ward_name"] == "xa o dien"
        assert conflicts[0]["existing_name_vi"] == "Đan Phượng"
        assert conflicts[0]["distance_km"] >= 0

    def test_existing_mapping_is_not_mutated(self) -> None:
        units = [{"name_vi": "Xã Mới", "type": "xa", "lat": 21.0, "lng": 105.8}]
        existing: dict[str, ExistingWard] = {}
        plan_inserts(units, existing)
        assert existing == {}


# ---------------------------------------------------------------------------
# Alias planning
# ---------------------------------------------------------------------------

class TestPlanAliases:
    def _units(self) -> list[dict]:
        return [
            {
                "code": "1",
                "name_vi": "Phường Một",
                "predecessors": [{"name_vi": "Phường Cũ Một"}],
            },
            {
                "code": "2",
                "name_vi": "Phường Hai",
                "predecessors": [{"name_vi": "Phường Cũ Hai"}],
            },
        ]

    def test_plans_alias_when_old_name_does_not_resolve(self) -> None:
        plans, stats = plan_aliases(self._units(), lambda n: False, set())
        assert [p.alias_norm for p in plans] == ["phuong cu hai", "phuong cu mot"]
        assert {p.ward_name for p in plans} == {"phuong mot", "phuong hai"}
        assert all(p.alias_vi.startswith("Phường Cũ") for p in plans)
        assert stats["resolvable"] == 0

    def test_skips_alias_when_lookup_already_resolves(self) -> None:
        plans, stats = plan_aliases(
            self._units(), lambda n: n == "phuong cu mot", set()
        )
        assert [p.alias_norm for p in plans] == ["phuong cu hai"]
        assert stats["resolvable"] == 1

    def test_skips_alias_when_alias_row_exists(self) -> None:
        plans, stats = plan_aliases(
            self._units(), lambda n: False, {"phuong cu mot"}
        )
        assert [p.alias_norm for p in plans] == ["phuong cu hai"]
        assert stats["already"] == 1

    def test_skips_alias_equal_to_a_new_unit_name(self) -> None:
        units = [
            {
                "code": "1",
                "name_vi": "Phường Một",
                "predecessors": [{"name_vi": "Phường Một"}],
            }
        ]
        plans, stats = plan_aliases(units, lambda n: False, set())
        assert plans == []
        assert stats["unchanged"] == 1

    def test_ambiguous_old_name_is_skipped(self) -> None:
        units = [
            {
                "code": "1",
                "name_vi": "Phường Một",
                "predecessors": [{"name_vi": "Xã Chung"}],
            },
            {
                "code": "2",
                "name_vi": "Phường Hai",
                "predecessors": [{"name_vi": "Xã Chung"}],
            },
        ]
        plans, stats = plan_aliases(units, lambda n: False, set())
        assert plans == []
        assert stats["ambiguous"] == [
            {
                "alias_vi": "Xã Chung",
                "alias_norm": "xa chung",
                "targets": ["phuong hai", "phuong mot"],
            }
        ]

    def test_duplicate_predecessor_entries_deduplicate(self) -> None:
        units = [
            {
                "code": "1",
                "name_vi": "Phường Một",
                "predecessors": [
                    {"name_vi": "Phường Cũ"},
                    {"name_vi": "Phường Cũ"},
                ],
            }
        ]
        plans, _ = plan_aliases(units, lambda n: False, set())
        assert len(plans) == 1


class TestPlanAliasesAgainstBundledDataset:
    def test_aliases_are_unique_and_point_at_2025_units(self, units: list[dict]) -> None:
        plans, stats = plan_aliases(units, lambda n: False, set())
        norms = [p.alias_norm for p in plans]
        assert len(norms) == len(set(norms))
        new_norms = {unit_norm(u) for u in units}
        assert all(p.ward_name in new_norms for p in plans)
        assert all(isinstance(p, AliasPlan) for p in plans)

    def test_known_ambiguous_old_names_are_skipped(self, units: list[dict]) -> None:
        _, stats = plan_aliases(units, lambda n: False, set())
        ambiguous = {row["alias_norm"] for row in stats["ambiguous"]}
        assert "phuong nga tu so" in ambiguous
        assert "xa dai hung" in ambiguous


# ---------------------------------------------------------------------------
# Predecessor distance flags
# ---------------------------------------------------------------------------

class TestFlagFarFromPredecessors:
    def test_near_unit_is_not_flagged(self) -> None:
        units = [
            {
                "name_vi": "Xã Gần",
                "lat": 21.0,
                "lng": 105.8,
                "predecessors": [{"name_vi": "Xã Cũ"}],
            }
        ]
        existing = {"xa cu": _existing("Xã Cũ", lat=21.001, lng=105.801)}
        assert flag_far_from_predecessors(units, existing, set()) == []

    def test_far_unit_is_flagged(self) -> None:
        units = [
            {
                "name_vi": "Xã Xa",
                "lat": 21.4,
                "lng": 105.8,
                "predecessors": [{"name_vi": "Xã Cũ"}],
            }
        ]
        existing = {"xa cu": _existing("Xã Cũ", lat=20.55, lng=105.8)}
        flagged = flag_far_from_predecessors(units, existing, set(), max_km=5)
        assert len(flagged) == 1
        assert flagged[0]["name_vi"] == "Xã Xa"
        assert flagged[0]["distance_km"] > 5


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli:
    def test_defaults_to_dry_run(self, dataset: dict, monkeypatch, capsys) -> None:
        calls = []
        monkeypatch.setattr(seed, "load_dataset", lambda path: dataset)
        monkeypatch.setattr(
            seed,
            "run",
            lambda url, ds, apply, max_predecessor_km: calls.append(apply)
            or seed.SeedReport(dataset_units=len(ds["units"])),
        )
        assert seed.main([]) == 0
        assert calls == [False]
        assert "DRY RUN" in capsys.readouterr().out

    def test_apply_flag_is_forwarded(self, dataset: dict, monkeypatch) -> None:
        calls = []
        monkeypatch.setattr(seed, "load_dataset", lambda path: dataset)
        monkeypatch.setattr(
            seed,
            "run",
            lambda url, ds, apply, max_predecessor_km: calls.append(apply)
            or seed.SeedReport(dataset_units=len(ds["units"])),
        )
        assert seed.main(["--apply"]) == 0
        assert calls == [True]

    def test_invalid_dataset_aborts_before_connecting(self, monkeypatch) -> None:
        calls = []
        monkeypatch.setattr(
            seed, "load_dataset", lambda path: {"count": 0, "units": []}
        )
        monkeypatch.setattr(
            seed, "run", lambda *args, **kwargs: calls.append(args) or seed.SeedReport()
        )
        assert seed.main([]) == 1
        assert calls == []
