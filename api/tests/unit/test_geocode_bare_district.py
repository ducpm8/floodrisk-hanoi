"""
Unit tests for scripts/geocode_bare_district.py (Tier 4b-1).

No network, no database.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import and_
from sqlalchemy.dialects import postgresql

from scripts.geocode_bare_district import (
    BDGroup,
    apply_outcomes,  # noqa: F401  (imported to make sure the module wires up)
    build_groups,
    classify,
    classify_rows,
    confidence_for_pick,
    evaluate,
    has_house_number,
    is_candidate_name,
    is_db_target,
    pick_result,
    queries_for,
    street_core,
    ward_names_by_district,
    _null_conditions,
)
from scripts.geocode_district import zone_km

DISTRICT = "Quận Cầu Giấy"
DISTRICTS = {
    "Quận Cầu Giấy", "Quận Hoàn Kiếm", "Quận Đống Đa", "Quận Hai Bà Trưng",
    "Huyện Đông Anh",
}
WARD_NAMES = {
    DISTRICT: {"dich vong", "phuong dich vong", "my dinh", "phuong my dinh"},
    "Quận Hoàn Kiếm": {"hang bai", "phuong hang bai"},
}
ANCHORS = {
    DISTRICT: [(21.030, 105.790), (21.040, 105.800)],
    "Huyện Đông Anh": [(21.150, 105.850)],
}


def _hit(lat, lng, label, key="highway", value="residential"):
    return {"lat": str(lat), "lon": str(lng), "display_name": label, "label": label,
            "class": key, "type": value}


def _row(name, district=DISTRICT, ward=None, lat=None, source=None, flag=None,
         conf=None, rid=1):
    return SimpleNamespace(
        id=rid, location_name=name, district=district, ward=ward, lat=lat,
        lng=None if lat is None else 105.8, coord_source=source,
        coord_flag=flag, coord_confidence=conf,
    )


def _group(street="Thái Hà", district=DISTRICT, cls="A", rid=1):
    return BDGroup((("thai", "ha"), district), district, [street], [rid], street, {cls})


def _cache(data, street="Thái Hà", district=DISTRICT):
    """Every query of the group is cached; `data` overrides some with hits."""
    base = {query: [] for query, _ in queries_for(street, district)}
    base.update(data)
    return lambda query: base.get(query)


class TestTargetFilter:
    @pytest.mark.parametrize(
        "name", ["Thái Hà", "Hoa Bằng", "Lệ Mật", "Phan Văn Trường", "Thụy Khuê"]
    )
    def test_bare_names_stay_in(self, name):
        assert is_candidate_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "Đường Trần Bình", "Phố Hoa Bằng", "Ngõ 165 Thái Hà", "Cầu Giấy",
            "Khu đô thị Nam An Khánh", "Chợ Mơ", "Hồ Tây", "Bệnh viện K",
            "Trường Tiểu học A", "Tổ dân phố 10", "Phường Dịch Vọng",
            "Đê La Thành",
        ],
    )
    def test_street_and_place_keywords_leave(self, name):
        assert not is_candidate_name(name)

    def test_db_target_requires_empty_provenance_and_ward(self):
        assert is_db_target(_row("Thái Hà"))
        assert not is_db_target(_row("Thái Hà", ward="phuong dich vong"))
        assert not is_db_target(_row("Thái Hà", lat=21.0))
        assert not is_db_target(_row("Thái Hà", flag="ward_only"))
        assert not is_db_target(_row("Thái Hà", district="Unknown"))


class TestClassification:
    def test_plain_street(self):
        verdict = classify("Thái Hà", DISTRICT, WARD_NAMES, DISTRICTS)
        assert (verdict.cls, verdict.street) == ("A", "Thái Hà")

    @pytest.mark.parametrize(
        "name,street",
        [
            ("Số 165 Thái Hà", "Thái Hà"),
            ("165 Thái Hà", "Thái Hà"),
            ("165A Thái Hà", "Thái Hà"),
            ("Số nhà 12/3 Thái Hà", "Thái Hà"),
        ],
    )
    def test_house_number_goes_to_the_parent_street(self, name, street):
        verdict = classify(name, DISTRICT, WARD_NAMES, DISTRICTS)
        assert (verdict.cls, verdict.street) == ("B", street)
        assert has_house_number(name)

    def test_street_with_its_own_district_in_parens(self):
        verdict = classify("Phùng Hưng (Hoàn Kiếm)", "Quận Hoàn Kiếm", WARD_NAMES, DISTRICTS)
        assert (verdict.cls, verdict.street) == ("C", "Phùng Hưng")

    def test_street_with_another_district_is_not_placed(self):
        verdict = classify("Phùng Hưng (Hai Bà Trưng)", DISTRICT, WARD_NAMES, DISTRICTS)
        assert verdict.cls == "C" and verdict.street is None
        assert verdict.note == "district_mismatch"

    def test_street_with_landmark_in_parens(self):
        verdict = classify("Võ Chí Công (tòa nhà UDIC)", DISTRICT, WARD_NAMES, DISTRICTS)
        assert (verdict.cls, verdict.street) == ("D", "Võ Chí Công")

    @pytest.mark.parametrize(
        "name",
        ["Tạ Hiện – Lương Ngọc Quyền", "Phan Bội Châu - Lý Thường Kiệt",
         "Ngã tư Quang Trung – Trần Quốc Toản", "Phạm Hùng giao Duy Tân",
         "Vòng xuyến ngã tư Tôn Thất Thuyết – Trần Thái Tông",
         "Nút Cổ Linh (phường Long Biên – cầu Vĩnh Tuy)"],
    )
    def test_two_streets_and_intersections_are_left_for_4b2(self, name):
        assert classify(name, DISTRICT, WARD_NAMES, DISTRICTS).cls == "E"

    @pytest.mark.parametrize(
        "name,street",
        [
            ("Tuyến Tố Hữu", "Tố Hữu"),
            ("Dốc Minh Khai", "Minh Khai"),
            ("Gầm cầu Thiên Đức", "Thiên Đức"),
            ("Gầm chui xe lửa Thiên Đức", "Thiên Đức"),
            ("Chân cầu vượt Lê Quang Đạo", "Lê Quang Đạo"),
            ("Đầu cầu vượt 70 – Miêu Nha", "Miêu Nha"),
            ("Bà Triệu kéo dài", "Bà Triệu"),
            ("Quang Trung đến Ba La", "Quang Trung"),
            ("Võ Chí Công hướng Nội Bài", "Võ Chí Công"),
        ],
    )
    def test_leading_position_words_are_dropped(self, name, street):
        verdict = classify(name, DISTRICT, WARD_NAMES, DISTRICTS)
        assert (verdict.cls, verdict.street) == ("A", street)

    @pytest.mark.parametrize(
        "name",
        ["Nhiều đoạn trong quận ngập gần 1 m", "Khu vực hồ Văn Quán",
         "Các tuyến đường bị ngập", "Tổ dân phố 10"],
    )
    def test_generic_areas(self, name):
        assert classify(name, DISTRICT, WARD_NAMES, DISTRICTS).cls == "F"

    @pytest.mark.parametrize("name", ["Dịch Vọng", "Mỹ Đình"])
    def test_ward_restatement(self, name):
        assert classify(name, DISTRICT, WARD_NAMES, DISTRICTS).cls == "G"

    def test_an_area_around_a_ward_is_not_the_ward_itself(self):
        assert classify("Khu vực Dịch Vọng", DISTRICT, WARD_NAMES, DISTRICTS).cls == "F"

    def test_a_house_number_beats_a_ward_name(self):
        wards = {"Quận Ba Đình": {"quan thanh", "phuong quan thanh"}}
        verdict = classify("192 Quán Thánh", "Quận Ba Đình", wards, DISTRICTS)
        assert (verdict.cls, verdict.street) == ("B", "Quán Thánh")

    def test_a_note_beats_a_ward_name(self):
        wards = {"Quận Ba Đình": {"lieu giai", "phuong lieu giai"}}
        verdict = classify("Liễu Giai (Ngân hàng Quân Đội)", "Quận Ba Đình", wards, DISTRICTS)
        assert (verdict.cls, verdict.street) == ("D", "Liễu Giai")

    def test_district_restatement(self):
        assert classify("Cầu Giấy", DISTRICT, WARD_NAMES, DISTRICTS).cls == "G"

    def test_street_core_strips_number_and_notes(self):
        assert street_core("Số 165 Thái Hà (gần chợ)") == "Thái Hà"
        assert street_core("Khu vực Triều Khúc") == "Triều Khúc"

    def test_trailing_district_is_not_part_of_the_street(self):
        assert street_core("Quang Trung Hà Đông", "Quận Hà Đông") == "Quang Trung"
        assert street_core("Hà Đông", "Quận Hà Đông") == "Hà Đông"

    def test_ward_names_come_from_predecessors(self):
        units = [{"name_vi": "Phường Dịch Vọng", "predecessors": [
            {"name_vi": "Phường Dịch Vọng", "district_vi": DISTRICT},
        ]}]
        names = ward_names_by_district(units)
        assert "dich vong" in names[DISTRICT]
        assert "phuong dich vong" in names[DISTRICT]


class TestQueriesAndConfidence:
    def test_prefix_variants_with_district_in_the_cache_key(self):
        assert queries_for("Thái Hà", DISTRICT) == [
            (f"Phố Thái Hà@@{DISTRICT}", True),
            (f"Đường Thái Hà@@{DISTRICT}", True),
            (f"Thái Hà@@{DISTRICT}", False),
        ]

    def test_confidence_bands(self):
        assert confidence_for_pick(0.4, "A", True) == 0.60
        assert confidence_for_pick(1.0, "D", True) == 0.60
        assert confidence_for_pick(1.5, "A", True) == 0.55
        assert confidence_for_pick(0.4, "B", True) == 0.55
        assert confidence_for_pick(0.4, "A", False) == 0.55

    def test_zone_sizes_are_the_district_ones(self):
        assert zone_km(DISTRICT) == 2.0
        assert zone_km("Huyện Đông Anh") == 4.0


class TestPickResult:
    PTS = ANCHORS[DISTRICT]

    def test_inside_the_zone(self):
        pick, far = pick_result([_hit(21.031, 105.791, "Phố Thái Hà")], self.PTS, 2.0, "Thái Hà")
        assert pick is not None and pick.anchor_km < 1 and not far

    def test_outside_the_zone_is_far(self):
        pick, far = pick_result([_hit(21.30, 105.79, "Phố Thái Hà")], self.PTS, 2.0, "Thái Hà")
        assert pick is None and far

    def test_non_highway_and_other_streets_are_ignored(self):
        results = [
            _hit(21.031, 105.791, "Phố Thái Hà", key="place"),
            _hit(21.031, 105.791, "Phố Thái Hòa"),
        ]
        pick, far = pick_result(results, self.PTS, 2.0, "Thái Hà")
        assert pick is None and not far

    def test_plot_label_is_not_a_street(self):
        pick, _ = pick_result(
            [_hit(21.031, 105.791, "Ô 72 lô 5 Đền Lừ 2")], self.PTS, 2.0, "Đền Lừ"
        )
        assert pick is None

    def test_tone_aware(self):
        assert pick_result(
            [_hit(21.031, 105.791, "Phố Thiên Đức")], self.PTS, 2.0, "Thiên Đức"
        )[0] is not None
        assert pick_result(
            [_hit(21.031, 105.791, "Phố Đinh Đức Thiện")], self.PTS, 2.0, "Thiên Đức"
        )[0] is None


class TestEvaluate:
    def test_ok_with_prefix_and_near_anchor(self):
        lookup = _cache({
            f"Phố Thái Hà@@{DISTRICT}": [_hit(21.0305, 105.7905, "Phố Thái Hà")],
        })
        outcome = evaluate(_group(), ANCHORS, lookup)
        assert (outcome.status, outcome.confidence, outcome.method) == ("ok", 0.60, "photon")

    def test_bare_name_hit_is_capped(self):
        lookup = _cache({
            f"Thái Hà@@{DISTRICT}": [_hit(21.0305, 105.7905, "Phố Thái Hà")],
        })
        outcome = evaluate(_group(), ANCHORS, lookup)
        assert (outcome.status, outcome.confidence) == ("ok", 0.55)

    def test_house_number_group_keeps_the_lower_band(self):
        lookup = _cache({
            f"Phố Thái Hà@@{DISTRICT}": [_hit(21.0305, 105.7905, "Phố Thái Hà")],
        })
        assert evaluate(_group(cls="B"), ANCHORS, lookup).confidence == 0.55

    def test_far_and_failed(self):
        far = _cache({
            f"Phố Thái Hà@@{DISTRICT}": [_hit(21.30, 105.79, "Phố Thái Hà")],
        })
        empty = _cache({})
        assert evaluate(_group(), ANCHORS, far).status == "too_far"
        assert evaluate(_group(), ANCHORS, empty).status == "geocode_failed"

    def test_uncached_and_no_anchors(self):
        assert evaluate(_group(), ANCHORS, _cache({})).status == "geocode_failed"
        assert evaluate(_group(), ANCHORS, lambda q: None).status == "not_geocoded"
        assert evaluate(_group(district="Quận Lạ"), ANCHORS, _cache({})).status == "no_anchors"

    def test_cross_district_reference(self):
        refs = {("thai", "ha"): [("Quận Đống Đa", 21.031, 105.791, 0.9)]}
        outcome = evaluate(_group(), ANCHORS, lambda q: None, refs)
        assert (outcome.status, outcome.method, outcome.confidence) == ("ok", "cross_district", 0.55)

    def test_far_cross_district_reference_is_ignored(self):
        refs = {("thai", "ha"): [("Quận Đống Đa", 20.90, 105.70, 0.9)]}
        assert evaluate(_group(), ANCHORS, lambda q: None, refs).status == "not_geocoded"


class TestGroupsAndApply:
    def test_rows_merge_by_street_key(self):
        rows = [
            _row("Thái Hà", rid=1),
            _row("Số 165 Thái Hà", rid=2),
            _row("165 Thái Hà", rid=3),
            _row("Tạ Hiện – Lương Ngọc Quyền", rid=4),
            _row("Nhiều đoạn trong quận ngập gần 1 m", rid=5),
            _row("Dịch Vọng", rid=6),
            _row("Phùng Hưng (Hai Bà Trưng)", rid=7),
        ]
        classified = classify_rows(rows, WARD_NAMES, DISTRICTS)
        (group,) = build_groups(classified)
        assert sorted(group.row_ids) == [1, 2, 3]
        assert group.street == "Thái Hà"
        assert group.cls == "B"  # a numbered row lowers the whole group
        assert len(group.names) == 3

    def test_district_separates_groups(self):
        rows = [
            _row("Thái Hà", rid=1),
            _row("Thái Hà", district="Quận Đống Đa", rid=2),
        ]
        groups = build_groups(classify_rows(rows, WARD_NAMES, DISTRICTS))
        assert len(groups) == 2

    def test_apply_conditions_protect_existing_data(self):
        sql = str(and_(*_null_conditions([1])).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for fragment in ("lat IS NULL", "coord_source IS NULL", "ward IS NULL"):
            assert fragment in sql
        # a previous failure may be retried, never ward_only / generic_area
        assert "geocode_failed" in sql and "too_far" in sql
        assert "ward_only" not in sql and "generic_area" not in sql
