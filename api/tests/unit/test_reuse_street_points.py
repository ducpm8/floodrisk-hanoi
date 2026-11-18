"""
Unit tests for scripts/reuse_street_points.py (Tier 3 step 1: reuse placed points).

No network, no database.
"""

from types import SimpleNamespace

import pytest

from scripts.reuse_street_points import (
    Reference,
    best_reference,
    build_matches,
    is_reference,
    is_target,
    reuse_confidence,
    street_key,
)


def _row(
    name,
    district="Quận Cầu Giấy",
    ward=None,
    lat=None,
    lng=None,
    source=None,
    flag=None,
    conf=None,
    rid=1,
):
    return SimpleNamespace(
        id=rid, location_name=name, district=district, ward=ward, lat=lat, lng=lng,
        coord_source=source, coord_flag=flag, coord_confidence=conf,
    )


def _placed(name, lat, lng, conf, district="Quận Cầu Giấy", ward="phuong yen hoa", rid=100):
    return _row(name, district, ward, lat, lng, "geocoded_street", None, conf, rid)


class TestStreetKey:
    def test_type_and_notes_do_not_matter(self):
        assert street_key("Phố Hoa Bằng") == street_key("Đường Hoa Bằng (phường Yên Hòa)")
        assert street_key("Đường/phố Âu Cơ") == street_key("Phố Âu Cơ")

    def test_alley_number_is_part_of_the_key(self):
        assert street_key("Ngõ 100 Đội Cấn") != street_key("Phố Đội Cấn")

    def test_number_only_streets_keep_their_type(self):
        assert street_key("Đường 32") != street_key("Ngõ 32")
        assert street_key("Đường 32") == street_key("Đường 32 (xã Kim Chung)")

    def test_empty(self):
        assert street_key(None) is None
        assert street_key("") is None


class TestBestReference:
    def test_highest_confidence_wins(self):
        best, reason = best_reference([Reference(21.0, 105.8, 0.6), Reference(21.001, 105.801, 0.9)])
        assert best.confidence == 0.9 and reason == ""

    def test_ties_go_to_the_most_shared_point(self):
        points = [Reference(21.0, 105.8, 0.75), Reference(21.002, 105.802, 0.75), Reference(21.002, 105.802, 0.75)]
        best, _ = best_reference(points)
        assert (best.lat, best.lng) == (21.002, 105.802)

    def test_far_apart_points_are_skipped(self):
        best, reason = best_reference([Reference(21.0, 105.8, 0.9), Reference(21.05, 105.8, 0.9)])
        assert best is None and reason == "spread"

    def test_nothing(self):
        assert best_reference([]) == (None, "no_reference")


class TestConfidence:
    @pytest.mark.parametrize("ref,expected", [(0.9, 0.60), (0.75, 0.60), (0.6, 0.60), (0.55, 0.55)])
    def test_capped_at_060(self, ref, expected):
        assert reuse_confidence(ref) == expected


class TestRoles:
    def test_target_needs_district_no_ward_no_coords_no_flag(self):
        assert is_target(_row("Phố Hoa Bằng"))
        assert not is_target(_row("Phố Hoa Bằng", ward="phuong yen hoa"))
        assert not is_target(_row("Phố Hoa Bằng", district=None))
        assert not is_target(_row("Phố Hoa Bằng", lat=21.0, lng=105.8))
        assert not is_target(_row("Phố Hoa Bằng", flag="geocode_failed"))
        assert not is_target(_row("Phố Hoa Bằng", source="geocoded_poi"))
        assert not is_target(_row("Khu đô thị Ciputra"))          # not a street
        assert not is_target(_row("Xã Thư Lâm (đường 32)"))       # admin-only

    def test_reference_is_a_placed_street_with_ward_and_confidence(self):
        assert is_reference(_placed("Phố Hoa Bằng", 21.0, 105.8, 0.9))
        assert not is_reference(_row("Phố Hoa Bằng", ward="x", lat=21.0, lng=105.8, source="geocoded_poi", conf=0.9))
        assert not is_reference(_placed("Phố Hoa Bằng", 21.0, 105.8, None))
        assert not is_reference(_row("Phố Hoa Bằng", ward=None, lat=21.0, lng=105.8, source="geocoded_street", conf=0.9))


class TestBuildMatches:
    def test_reuses_point_in_same_district_only(self):
        rows = [
            _placed("Phố Hoa Bằng", 21.024, 105.793, 0.9),
            _row("Đường Hoa Bằng (phường Yên Hòa)", rid=1),
            _row("Phố Hoa Bằng", rid=2),
            _row("Phố Hoa Bằng", district="Quận Hà Đông", rid=3),
        ]
        matches = {m.district: m for m in build_matches(rows)}
        assert matches["Quận Cầu Giấy"].reference is not None
        assert matches["Quận Cầu Giấy"].confidence == 0.60
        assert sorted(matches["Quận Cầu Giấy"].row_ids) == [1, 2]
        assert matches["Quận Hà Đông"].reference is None
        assert matches["Quận Hà Đông"].skipped == "no_reference"

    def test_spread_reference_is_skipped(self):
        rows = [
            _placed("Đường Quang Trung", 20.96, 105.77, 0.9, district="Quận Hà Đông", rid=100),
            _placed("Đường Quang Trung", 20.99, 105.77, 0.9, district="Quận Hà Đông", rid=101),
            _row("Đường Quang Trung", district="Quận Hà Đông", rid=1),
        ]
        (match,) = build_matches(rows)
        assert match.reference is None and match.skipped == "spread"

    def test_other_streets_are_not_confused(self):
        rows = [_placed("Phố Hoa Bằng", 21.024, 105.793, 0.9), _row("Phố Trần Bình")]
        (match,) = build_matches(rows)
        assert match.reference is None
