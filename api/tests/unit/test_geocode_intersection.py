"""
Unit tests for scripts/geocode_intersection.py (4b-2 trial).

No network, no database.
"""

from types import SimpleNamespace

import pytest

from scripts.geocode_intersection import (
    EphGroup,
    InvalidE,
    JsonCache,  # noqa: F401  (import check)
    bbox_key,
    build_groups,
    closest_pair,
    district_bbox,
    evaluate,
    intersection_points,
    is_junction_label,
    label_has_street,
    matching_ways,
    names_adjacent,
    osm_intersection,
    parse_pair,
    parse_ways,
    photon_queries,
    sample_groups,
    segment_intersection,
    shared_nodes,
)
from scripts.reuse_street_points import street_key

DISTRICT = "Quận Cầu Giấy"
ANCHORS = {DISTRICT: [(21.030, 105.795)]}


def _group(a="Hoa Bằng", b="Thái Hà", district=DISTRICT, rows=(1, 2)):
    return EphGroup(
        (frozenset(), district), district, a, b, street_key(a), street_key(b),
        [f"{a} – {b}"], list(rows),
    )


def _way(name, points, nodes=None, wid=1):
    return {
        "id": wid, "name": name, "nodes": nodes or [],
        "geometry": [{"lat": lat, "lon": lng} for lat, lng in points],
    }


def _result(lat, lng, label, cls="highway", typ="residential"):
    return {"lat": str(lat), "lon": str(lng), "label": label,
            "display_name": label, "class": cls, "type": typ}


class TestParsePair:
    @pytest.mark.parametrize(
        "name,a,b",
        [
            ("Tạ Hiện – Lương Ngọc Quyền", "Tạ Hiện", "Lương Ngọc Quyền"),
            ("Nút giao Cổ Linh – Đàm Quang Trung", "Cổ Linh", "Đàm Quang Trung"),
            ("Nút Cổ Linh (Aeon Mall) - Đàm Quang Trung", "Cổ Linh", "Đàm Quang Trung"),
            ("Dương Đình Nghệ giao Phạm Hùng", "Dương Đình Nghệ", "Phạm Hùng"),
            ("Phan Bội Châu và Lý Thường Kiệt", "Phan Bội Châu", "Lý Thường Kiệt"),
            ("Nguyễn Trãi / Trần Phú", "Nguyễn Trãi", "Trần Phú"),
            ("Ngã tư Quang Trung - Trần Quốc Toản", "Quang Trung", "Trần Quốc Toản"),
        ],
    )
    def test_dashes_prefixes_and_words(self, name, a, b):
        pair = parse_pair(name, DISTRICT)
        assert pair.status == "ok"
        assert {pair.street_a, pair.street_b} == {a, b}
        assert pair.note == "Aeon Mall" if "Aeon Mall" in name else pair.note == ""

    def test_notes_are_kept_for_the_report(self):
        pair = parse_pair("Dương Đình Nghệ – Phạm Hùng (sau Keangnam)", DISTRICT)
        assert pair.status == "ok" and pair.note == "sau Keangnam"

    def test_three_segments_are_skipped(self):
        pair = parse_pair("Dương Đình Nghệ giao Phạm Hùng – Mễ Trì", DISTRICT)
        assert pair.status == "multi_segment"

    @pytest.mark.parametrize(
        "name",
        ["Đỗ Nhuận – Công viên Hòa Bình", "Nút giao Phạm Tu – Cầu Bươu",
         "Phạm Hùng – Bến xe Mỹ Đình"],
    )
    def test_landmarks_are_not_streets(self, name):
        assert parse_pair(name, DISTRICT).status == "segment_not_street"

    def test_one_segment_is_not_a_pair(self):
        assert parse_pair("Dốc Minh Khai", DISTRICT).status == "not_pair"

    def test_same_street_twice(self):
        assert parse_pair("Thái Hà – Thái Hà", DISTRICT).status == "same_street"

    def test_order_does_not_matter(self):
        forward = parse_pair("Hoa Bằng – Thái Hà", DISTRICT)
        backward = parse_pair("Thái Hà – Hoa Bằng", DISTRICT)
        assert (forward.key_a, forward.key_b) == (backward.key_a, backward.key_b)
        assert forward.street_a == backward.street_a

    def test_street_type_prefixes_are_accepted(self):
        pair = parse_pair("Đường Láng – Phố Huế", DISTRICT)
        assert pair.status == "ok"
        assert {pair.key_a, pair.key_b} == {street_key("Láng"), street_key("Huế")}

    def test_doan_giao_and_truc_prefixes_are_dropped(self):
        pair = parse_pair("Đoạn giao Nguyễn Trường Tộ – Phan Huy Ích", DISTRICT)
        assert pair.status == "ok"
        pair = parse_pair("Giao Nguyễn Trường Tộ – Phan Huy Ích", DISTRICT)
        assert pair.status == "ok" and pair.street_a == "Nguyễn Trường Tộ"
        pair = parse_pair("Trục Phạm Hùng – Nguyễn Hoàng", DISTRICT)
        assert pair.status == "ok"

    def test_person_named_ho_is_still_a_street(self):
        pair = parse_pair("Nút Hồ Tùng Mậu – Phạm Hùng", DISTRICT)
        assert pair.status == "ok"


class TestGroupsAndSampling:
    def _row(self, name, rid, district=DISTRICT):
        return SimpleNamespace(id=rid, location_name=name, district=district)

    def test_order_variants_merge(self):
        rows = [
            SimpleNamespace(row=self._row("Hoa Bằng – Thái Hà", 1), cls="E", street=None),
            SimpleNamespace(row=self._row("Thái Hà – Hoa Bằng", 2), cls="E", street=None),
        ]
        groups, invalid = build_groups(rows)
        assert not invalid and len(groups) == 1
        assert groups[0].rows == 2 and len(groups[0].names) == 2

    def test_invalid_rows_are_reported_not_grouped(self):
        rows = [
            SimpleNamespace(row=self._row("A – B – C", 1), cls="E", street=None),
            SimpleNamespace(row=self._row("A – Công viên B", 2), cls="E", street=None),
        ]
        groups, invalid = build_groups(rows)
        assert groups == []
        assert sorted(item.status for item in invalid) == ["multi_segment", "segment_not_street"]

    def test_districts_separate_groups(self):
        rows = [
            SimpleNamespace(row=self._row("Hoa Bằng – Thái Hà", 1), cls="E", street=None),
            SimpleNamespace(
                row=self._row("Hoa Bằng – Thái Hà", 2, "Quận Đống Đa"), cls="E", street=None
            ),
        ]
        groups, _ = build_groups(rows)
        assert len(groups) == 2

    def _groups(self):
        return [
            EphGroup((frozenset(), f"Quận {index // 4}"), f"Quận {index // 4}",
                     f"A{index}", f"B{index}", ("a",), ("b",), [], [index])
            for index in range(16)
        ]

    def test_sampling_is_deterministic_capped_and_repeatable(self):
        groups = self._groups()
        first = sample_groups(groups, 9, seed=7, per_district=4)
        second = sample_groups(groups, 9, seed=7, per_district=4)
        assert [g.street_a for g in first] == [g.street_a for g in second]
        assert len(first) == 9
        per_district = {g.district for g in first}
        assert all(sum(1 for g in first if g.district == d) <= 4 for d in per_district)

    def test_small_pool_returns_everything(self):
        assert len(sample_groups(self._groups()[:3], 30, seed=1)) == 3


class TestGeometry:
    def test_crossing_segments(self):
        hit = segment_intersection((0, 0), (10, 10), (0, 10), (10, 0))
        assert hit == pytest.approx((5, 5))

    def test_parallel_segments_do_not_cross(self):
        assert segment_intersection((0, 0), (10, 0), (0, 1), (10, 1)) is None

    def test_shared_node(self):
        way_a = _way("Phố Hoa Bằng", [(21.030, 105.790), (21.030, 105.795)],
                     nodes=[1, 2])
        way_b = _way("Phố Thái Hà", [(21.025, 105.795), (21.030, 105.795), (21.035, 105.795)],
                     nodes=[3, 2, 4])
        assert shared_nodes(way_a, way_b) == [(21.030, 105.795)]

    def test_segment_intersection_points(self):
        way_a = _way("Phố Hoa Bằng", [(21.030, 105.790), (21.030, 105.800)])
        way_b = _way("Phố Thái Hà", [(21.025, 105.795), (21.035, 105.795)])
        (lat, lng), = intersection_points(way_a, way_b)
        assert lat == pytest.approx(21.030, abs=1e-6)
        assert lng == pytest.approx(105.795, abs=1e-6)

    def test_near_midpoint_is_measured_in_km(self):
        way_a = _way("Phố Hoa Bằng", [(21.030, 105.790), (21.030, 105.800)])
        way_b = _way("Phố Thái Hà", [(21.0305, 105.790), (21.0305, 105.800)])
        (lat, lng), km = closest_pair(way_a, way_b)
        assert km == pytest.approx(0.055, abs=0.01)
        assert lat == pytest.approx(21.03025, abs=1e-4)

    def test_bbox_covers_the_zone(self):
        south, west, north, east = district_bbox([(21.030, 105.795)], DISTRICT)
        assert south < 21.030 < north and west < 105.795 < east
        assert (north - south) == pytest.approx(4 / 111, abs=1e-4)  # 2 km each side
        assert bbox_key((south, west, north, east)).startswith("ways:")


class TestOsmIntersection:
    def _crossing(self):
        return [
            _way("Phố Hoa Bằng", [(21.030, 105.790), (21.030, 105.800)], wid=1),
            _way("Phố Thái Hà", [(21.025, 105.795), (21.035, 105.795)], wid=2),
        ]

    def test_single_crossing_is_accepted(self):
        pick, status = osm_intersection(_group(), self._crossing(), ANCHORS[DISTRICT])
        assert status == "ok" and pick.method == "osm_intersection"
        assert pick.confidence == 0.70 and pick.anchor_km < 0.01

    def test_missing_street_is_not_found(self):
        ways = [_way("Phố Hoa Bằng", [(21.030, 105.790), (21.030, 105.800)])]
        assert osm_intersection(_group(), ways, ANCHORS[DISTRICT]) == (None, "not_found")

    def test_tone_mismatch_is_not_found(self):
        ways = [
            _way("Phố Hoa Bằng", [(21.030, 105.790), (21.030, 105.800)]),
            _way("Phố Đinh Đức Thiện", [(21.025, 105.795), (21.035, 105.795)]),
        ]
        group = _group(a="Hoa Bằng", b="Thiên Đức")
        assert osm_intersection(group, ways, ANCHORS[DISTRICT]) == (None, "not_found")

    def test_near_miss_uses_the_midpoint(self):
        ways = [
            _way("Phố Hoa Bằng", [(21.030, 105.790), (21.030, 105.800)]),
            _way("Phố Thái Hà", [(21.0305, 105.790), (21.0305, 105.800)]),
        ]
        pick, status = osm_intersection(_group(), ways, ANCHORS[DISTRICT])
        assert status == "ok" and pick.method == "osm_near_midpoint"
        assert pick.confidence == 0.60

    def test_far_miss_is_no_intersection(self):
        ways = [
            _way("Phố Hoa Bằng", [(21.030, 105.790), (21.030, 105.800)]),
            _way("Phố Thái Hà", [(21.033, 105.790), (21.033, 105.800)]),
        ]
        assert osm_intersection(_group(), ways, ANCHORS[DISTRICT]) == (None, "no_intersection")

    def test_two_far_apart_crossings_are_ambiguous(self):
        ways = [
            _way("Phố Hoa Bằng", [(21.030, 105.770), (21.030, 105.820)]),
            _way("Phố Thái Hà", [(21.025, 105.780), (21.035, 105.780)], wid=2),
            _way("Phố Thái Hà", [(21.025, 105.810), (21.035, 105.810)], wid=3),
        ]
        anchors = [(21.030, 105.795)]
        assert osm_intersection(_group(), ways, anchors)[1] == "ambiguous"

    def test_way_name_matching_is_type_and_tone_aware(self):
        ways = [
            _way("Đường Hoa Bằng", [(21.030, 105.790), (21.030, 105.800)]),
            _way("Phố Thái Hà", [(21.030, 105.790), (21.030, 105.800)]),
        ]
        assert len(matching_ways("Hoa Bằng", ways)) == 1
        assert matching_ways("Thiên Đức", ways) == []


class TestPhotonFallback:
    def test_label_needs_both_names(self):
        label = "Ngã tư Hoa Bằng - Thái Hà, Cầu Giấy, Hà Nội"
        assert label_has_street("Hoa Bằng", label)
        assert label_has_street("Thái Hà", label)
        assert not label_has_street("Hoa Lâm", label)

    def test_tone_is_respected(self):
        assert not label_has_street("Thiên Đức", "Phố Đinh Đức Thiện, Hà Nội")

    def test_junction_words(self):
        assert is_junction_label("Ngã tư Hoa Bằng - Thái Hà")
        assert is_junction_label("Nút giao Cổ Linh - Đàm Quang Trung")
        assert is_junction_label("Cầu vượt Láng Hạ - Huỳnh Thúc Kháng")
        assert is_junction_label("anything", osm_class="junction")
        assert not is_junction_label("Nhà chờ Lương Thế Vinh Phố Tố Hữu", "highway", "bus_stop")

    def test_bus_stop_named_after_two_roads_is_rejected(self):
        label = "Nhà chờ Lương Thế Vinh Phố Tố Hữu"
        lookup = lambda query: [_result(20.995336, 105.790943, label, "highway", "bus_stop")]
        outcome = evaluate(
            _group(a="Lương Thế Vinh", b="Tố Hữu"), ANCHORS, None, lookup, False
        )
        assert outcome.status == "not_found"

    def test_names_must_be_adjacent(self):
        assert names_adjacent("Láng Hạ", "Thái Hà", "Cầu vượt Láng Hạ - Thái Hà, Hà Nội")
        assert names_adjacent(
            "Láng Hạ", "Huỳnh Thúc Kháng", "Cầu vượt Láng Hạ - Huỳnh Thúc Kháng"
        )
        assert not names_adjacent(
            "Láng Hạ", "Thái Hà",
            "Cầu vượt Láng Hạ - Huỳnh Thúc Kháng Ngõ Thái Hà, Đống Đa",
        )

    def test_a_scattered_third_road_is_rejected_by_the_fallback(self):
        label = "Cầu vượt Láng Hạ - Huỳnh Thúc Kháng Ngõ Thái Hà, Đống Đa"
        lookup = lambda query: [_result(21.01645, 105.81484, label)]
        outcome = evaluate(_group(a="Láng Hạ", b="Thái Hà"), ANCHORS, None, lookup, False)
        assert outcome.status == "not_found"

    def test_queries_include_junction_forms_with_the_district_key(self):
        queries = photon_queries(_group(), DISTRICT)
        assert f"Ngã tư Hoa Bằng Thái Hà@@{DISTRICT}" in queries
        assert f"Nút giao Hoa Bằng Thái Hà@@{DISTRICT}" in queries
        assert f"Hoa Bằng Thái Hà@@{DISTRICT}" in queries
        assert all(query.endswith(f"@@{DISTRICT}") for query in queries)

    def test_photon_hit_when_osm_is_unavailable(self):
        label = "Ngã tư Hoa Bằng - Thái Hà, Cầu Giấy"
        lookup = lambda query: [_result(21.0302, 105.7951, label)] if "Ngã tư" in query else []
        outcome = evaluate(_group(), ANCHORS, None, lookup, osm_available=False)
        assert (outcome.status, outcome.pick.method, outcome.pick.confidence) == (
            "ok", "photon_junction", 0.60
        )

    def test_photon_hit_after_overpass_error(self):
        label = "Ngã tư Hoa Bằng - Thái Hà, Cầu Giấy"
        lookup = lambda query: [_result(21.0302, 105.7951, label)] if "Ngã tư" in query else []
        outcome = evaluate(_group(), ANCHORS, None, lookup, osm_available=True)
        assert outcome.status == "ok" and "overpass_error" in outcome.note

    def test_single_street_photon_hit_is_rejected(self):
        lookup = lambda query: [_result(21.0302, 105.7951, "Phố Hoa Bằng, Cầu Giấy")]
        outcome = evaluate(_group(), ANCHORS, [], lookup, osm_available=False)
        assert outcome.status == "not_found"

    def test_no_anchors(self):
        assert evaluate(_group(district="Quận Lạ"), {}, None, lambda q: None, False).status == "no_anchors"

    def test_outside_zone_is_too_far(self):
        label = "Ngã tư Hoa Bằng - Thái Hà, Hà Nội"
        lookup = lambda query: [_result(21.30, 105.795, label)]
        outcome = evaluate(_group(), ANCHORS, None, lookup, osm_available=False)
        assert outcome.status == "too_far"


class TestParseWays:
    def test_fixture_keeps_named_highways_only(self):
        payload = {
            "elements": [
                {
                    "type": "way", "id": 1,
                    "tags": {"name": "Phố Hoa Bằng", "highway": "residential"},
                    "nodes": [10, 11],
                    "geometry": [{"lat": 21.03, "lon": 105.79}, {"lat": 21.03, "lon": 105.80}],
                },
                {"type": "way", "id": 2, "tags": {"name": "No highway"}, "geometry": []},
                {"type": "node", "id": 3, "lat": 21.03, "lon": 105.79},
                {"type": "way", "id": 4, "tags": {"highway": "primary"}, "geometry": []},
            ]
        }
        ways = parse_ways(payload)
        assert len(ways) == 1
        assert ways[0]["name"] == "Phố Hoa Bằng"
        assert ways[0]["nodes"] == [10, 11]

    def test_bad_payload_is_empty(self):
        assert parse_ways(None) == []
        assert parse_ways({"elements": "nope"}) == []
