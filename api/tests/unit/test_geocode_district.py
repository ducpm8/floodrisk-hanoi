"""
Unit tests for scripts/geocode_district.py (Tier 3 step 3: district-zone geocoding).

No network, no database.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from scripts.geocode_district import (
    DGroup,
    PhotonDistrictClient,
    apply_outcomes,  # noqa: F401  (imported to make sure the module wires up)
    attempts,
    build_anchors,
    build_groups,
    centre_of,
    confidence_for,
    cross_district_reference,
    evaluate,
    nearest_anchor_km,
    pick_result,
    queries_for,
    tone_match,
    viet_word_key,
    zone_km,
    _null_conditions,
)
from scripts.geocode_tier1 import NominatimCache

ANCHORS = {
    "Quận Cầu Giấy": [(21.030, 105.790), (21.040, 105.800)],
    "Huyện Đông Anh": [(21.150, 105.850)],
}
UNITS = [
    {"predecessors": [
        {"name_vi": "Phường Mai Dịch", "district_vi": "Quận Cầu Giấy"},
        {"name_vi": "Xã Cổ Loa", "district_vi": "Huyện Đông Anh"},
        {"name_vi": "Phường Không Có", "district_vi": "Quận Cầu Giấy"},
    ]}
]


def _hit(lat, lng, label, key="highway", value="residential"):
    return {"lat": str(lat), "lon": str(lng), "display_name": label, "label": label,
            "class": key, "type": value}


def _row(name, district="Quận Cầu Giấy", ward=None, lat=None, source=None, flag=None, conf=None, rid=1):
    return SimpleNamespace(id=rid, location_name=name, district=district, ward=ward, lat=lat,
                           lng=None if lat is None else 105.8, coord_source=source,
                           coord_flag=flag, coord_confidence=conf)


def _group(name="Phố Hoa Bằng", district="Quận Cầu Giấy", parent=None, alternates=None):
    return DGroup(((name,), district), district, [name], [1], name, parent, alternates or [])


class TestAnchors:
    def test_predecessors_and_events_feed_the_anchors(self):
        points = {"phuong mai dich": (21.04, 105.77), "xa co loa cu": (21.15, 105.85), "phuong cau giay": (21.03, 105.79)}
        events = [
            SimpleNamespace(ward="phuong cau giay", district="Quận Cầu Giấy"),
            SimpleNamespace(ward="phuong cau giay", district="Unknown"),
            SimpleNamespace(ward="not-a-ward", district="Quận Cầu Giấy"),
        ]
        anchors = build_anchors(UNITS, points, events)
        assert set(anchors["Quận Cầu Giấy"]) == {(21.04, 105.77), (21.03, 105.79)}
        assert anchors["Huyện Đông Anh"] == [(21.15, 105.85)]
        assert "Unknown" not in anchors

    def test_nearest_and_centre(self):
        pts = ANCHORS["Quận Cầu Giấy"]
        assert nearest_anchor_km(21.030, 105.790, pts) == pytest.approx(0, abs=1e-6)
        assert nearest_anchor_km(21.040, 105.800, pts) == pytest.approx(0, abs=1e-6)
        assert centre_of(pts) in pts or isinstance(centre_of(pts), tuple)

    def test_zone_sizes(self):
        assert zone_km("Quận Cầu Giấy") == 2.0
        assert zone_km("Huyện Đông Anh") == 4.0

    @pytest.mark.parametrize("km,expected", [(0.4, 0.60), (1.0, 0.60), (1.5, 0.55)])
    def test_confidence_bands(self, km, expected):
        assert confidence_for(km) == expected


class TestQueriesAndAttempts:
    def test_district_rides_in_the_cache_key(self):
        assert queries_for("Phố Hoa Bằng", "Quận Cầu Giấy") == [
            "Phố Hoa Bằng@@Quận Cầu Giấy", "Hoa Bằng@@Quận Cầu Giấy",
        ]

    def test_number_only_street_has_no_bare_query(self):
        assert queries_for("Đường 32", "Huyện Hoài Đức") == ["Đường 32@@Huyện Hoài Đức"]

    def test_attempt_order(self):
        group = _group("Ngõ 165 Thái Hà", parent="Thái Hà", alternates=["Phố Thái Hà"])
        assert attempts(group) == [("Ngõ 165 Thái Hà", False), ("Thái Hà", True), ("Phố Thái Hà", True)]

    def test_client_strips_suffix_and_biases(self):
        cache = NominatimCache.__new__(NominatimCache)
        cache.data, cache.path, cache._unsaved = {}, None, 0
        client = PhotonDistrictClient(cache, {"Quận Cầu Giấy": (21.03, 105.79)})
        params = client._params("Hoa Bằng@@Quận Cầu Giấy")
        assert params["q"] == "Hoa Bằng" and params["lat"] == 21.03 and params["lon"] == 105.79
        assert "lat" not in client._params("Hoa Bằng@@Quận Lạ")


class TestPickResult:
    PTS = ANCHORS["Quận Cầu Giấy"]

    def test_inside_the_zone(self):
        pick, far = pick_result([_hit(21.031, 105.791, "Phố Hoa Bằng")], self.PTS, 2.0, "Phố Hoa Bằng")
        assert pick is not None and pick.anchor_km < 1 and not far

    def test_outside_the_zone_is_far(self):
        pick, far = pick_result([_hit(21.10, 105.79, "Phố Hoa Bằng")], self.PTS, 2.0, "Phố Hoa Bằng")
        assert pick is None and far

    def test_other_street_is_ignored(self):
        pick, far = pick_result([_hit(21.031, 105.791, "Phố Hoa Lâm")], self.PTS, 2.0, "Phố Hoa Bằng")
        assert pick is None and not far

    def test_outside_hanoi_counts_as_far(self):
        pick, far = pick_result([_hit(10.0, 106.0, "Phố Hoa Bằng")], self.PTS, 2.0, "Phố Hoa Bằng")
        assert pick is None and far

    def test_nearest_to_an_anchor_wins_within_class(self):
        results = [_hit(21.045, 105.803, "Phố Hoa Bằng"), _hit(21.0301, 105.7901, "Phố Hoa Bằng")]
        pick, _ = pick_result(results, self.PTS, 2.0, "Phố Hoa Bằng")
        assert pick.lat == 21.0301


class TestToneAwareMatching:
    def test_tone_placement_variants_are_equal(self):
        assert viet_word_key("Hòe") == viet_word_key("Hoè")
        assert viet_word_key("Thuý") == viet_word_key("Thúy")

    def test_different_tones_are_different_words(self):
        assert viet_word_key("Thiên") != viet_word_key("Thiện")

    @pytest.mark.parametrize(
        "street,label,expected",
        [
            ("Phố Hòe Nhai", "Phố Hoè Nhai, Ba Đình, Hà Nội", True),
            ("Phố Thiên Đức", "Phố Đinh Đức Thiện, Phúc Lợi, Hà Nội", False),
            ("Đường Láng Hạ", "Phố Láng Hạ, Láng, Hà Nội", True),
            ("Ngõ 77 Xuân La", "Hẻm 15 Ngách 25 Ngõ 77 Xuân La, Tây Hồ", True),
            ("Phố Hoa Bằng", "Phố Hoa Bàng, Cầu Giấy", False),
        ],
    )
    def test_tone_match(self, street, label, expected):
        assert tone_match(street, label) is expected

    def test_pick_rejects_the_diacritic_collision(self):
        results = [_hit(21.031, 105.791, "Phố Đinh Đức Thiện, Phúc Lợi")]
        pick, _ = pick_result(results, ANCHORS["Quận Cầu Giấy"], 2.0, "Phố Thiên Đức")
        assert pick is None


class TestEvaluate:
    def _lookup(self, data):
        return lambda q: data.get(q)

    def test_ok_and_confidence(self):
        lookup = self._lookup({"Phố Hoa Bằng@@Quận Cầu Giấy": [_hit(21.031, 105.791, "Phố Hoa Bằng")]})
        outcome = evaluate(_group(), ANCHORS, lookup)
        assert outcome.status == "ok" and outcome.confidence == 0.60 and outcome.method == "photon"

    def test_second_query_bare_name(self):
        lookup = self._lookup({
            "Phố Hoa Bằng@@Quận Cầu Giấy": [],
            "Hoa Bằng@@Quận Cầu Giấy": [_hit(21.031, 105.791, "Phố Hoa Bằng")],
        })
        assert evaluate(_group(), ANCHORS, lookup).status == "ok"

    def test_far_vs_failed(self):
        far = self._lookup({q: [_hit(21.30, 105.79, "Phố Hoa Bằng")] for q in queries_for("Phố Hoa Bằng", "Quận Cầu Giấy")})
        assert evaluate(_group(), ANCHORS, far).status == "too_far"
        empty = self._lookup({q: [] for q in queries_for("Phố Hoa Bằng", "Quận Cầu Giấy")})
        assert evaluate(_group(), ANCHORS, empty).status == "geocode_failed"

    def test_uncached_and_no_anchors(self):
        assert evaluate(_group(), ANCHORS, self._lookup({})).status == "not_geocoded"
        assert evaluate(_group(district="Quận Lạ"), ANCHORS, self._lookup({})).status == "no_anchors"

    def test_parent_street_fallback(self):
        group = _group("Ngõ 165 Thái Hà", parent="Thái Hà")
        lookup = self._lookup({
            "Ngõ 165 Thái Hà@@Quận Cầu Giấy": [],
            "165 Thái Hà@@Quận Cầu Giấy": [],
            "Thái Hà@@Quận Cầu Giấy": [_hit(21.031, 105.791, "Phố Thái Hà")],
        })
        assert evaluate(group, ANCHORS, lookup).status == "ok"


class TestCrossDistrict:
    def test_border_street_reused_when_inside_the_zone(self):
        group = _group("Phố Hoa Bằng")
        refs = {group.key[0]: [("Quận Đống Đa", 21.031, 105.791, 0.9)]}
        pick = cross_district_reference(group, refs, ANCHORS["Quận Cầu Giấy"])
        assert pick is not None
        outcome = evaluate(group, ANCHORS, lambda q: None, refs)
        assert (outcome.status, outcome.method, outcome.confidence) == ("ok", "cross_district", 0.55)

    def test_far_reference_is_not_reused(self):
        group = _group("Phố Hoa Bằng")
        refs = {group.key[0]: [("Quận Đống Đa", 20.90, 105.70, 0.9)]}
        assert cross_district_reference(group, refs, ANCHORS["Quận Cầu Giấy"]) is None

    def test_same_district_reference_is_ignored(self):
        group = _group("Phố Hoa Bằng")
        refs = {group.key[0]: [("Quận Cầu Giấy", 21.031, 105.791, 0.9)]}
        assert cross_district_reference(group, refs, ANCHORS["Quận Cầu Giấy"]) is None


class TestGroupsAndApply:
    def test_groups_merge_notes_and_skip_non_targets(self):
        rows = [
            _row("Phố Hoa Bằng", rid=1),
            _row("Đường Hoa Bằng (phường Yên Hòa)", rid=2),
            _row("Phố Hoa Bằng", ward="phuong yen hoa", rid=3),
            _row("Phố Hoa Bằng", flag="geocode_failed", rid=4),
            _row("Xã Thư Lâm", rid=5),
        ]
        (group,) = build_groups(rows)
        assert sorted(group.row_ids) == [1, 2] and len(group.names) == 2

    def test_apply_conditions_protect_existing_data(self):
        from sqlalchemy import and_
        sql = str(and_(*_null_conditions([1])).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for fragment in ("lat IS NULL", "coord_source IS NULL", "ward IS NULL"):
            assert fragment in sql
        # may overwrite a previous failure flag, never ward_only / generic_area
        assert "geocode_failed" in sql and "ward_only" not in sql and "generic_area" not in sql
