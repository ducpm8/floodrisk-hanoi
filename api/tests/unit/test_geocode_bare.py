"""
Unit tests for scripts/geocode_bare.py (Tier 4a: bare names with a known ward).

No network, no database.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import and_
from sqlalchemy.dialects import postgresql

from scripts.geocode_bare import (
    BareGroup,
    SegmentPick,
    combine,
    confidence_for,
    evaluate,
    group_bare,
    group_conditions,
    is_bare_name,
    is_ward_restatement,
    queries_for_segment,
    segments_of,
)
from scripts.geocode_tier1 import WardInfo

WARD = WardInfo("phuong khuong dinh", "Phường Khương Đình", 20.9885, 105.8085)


def _hit(lat, lng, label, key="highway", value="residential"):
    return {"lat": str(lat), "lon": str(lng), "display_name": label, "label": label,
            "class": key, "type": value}


def _row(name, ward="phuong khuong dinh", district="Quận Thanh Xuân", rid=1):
    return SimpleNamespace(id=rid, location_name=name, ward=ward, district=district)


def _lookup(data):
    return lambda query: data.get(query)


class TestNames:
    @pytest.mark.parametrize("name", ["Triều Khúc", "Hoa Bằng", "Phan Bội Châu – Lý Thường Kiệt", "Khu vực Tây Mỗ"])
    def test_bare_names(self, name):
        assert is_bare_name(name)

    @pytest.mark.parametrize(
        "name",
        ["Đường Trần Bình", "Thôn Kim Tiên", "Xã Thư Lâm", "Tổ dân phố 10P", "Khu dân cư số 3"],
    )
    def test_not_bare(self, name):
        assert not is_bare_name(name)

    def test_segments_single_and_compound(self):
        assert segments_of("Triều Khúc") == ["Triều Khúc"]
        assert segments_of("Phan Bội Châu – Lý Thường Kiệt") == ["Phan Bội Châu", "Lý Thường Kiệt"]
        assert segments_of("Ngã tư Quang Trung – Trần Quốc Toản") == ["Quang Trung", "Trần Quốc Toản"]

    def test_parenthetical_notes_are_dropped(self):
        assert segments_of("Nguyễn Trác (chân cầu HH2)") == ["Nguyễn Trác"]
        assert segments_of("Khu vực Triều Khúc") == ["Triều Khúc"]

    @pytest.mark.parametrize(
        "name",
        ["Khu Ngoại giao đoàn", "Khu vực hồ Văn Quán", "Các xã Cấn Hữu, Phú Cát", "Công viên Thống Nhất"],
    )
    def test_areas_and_groups_are_not_segments(self, name):
        assert segments_of(name) == []

    def test_long_or_numbered_segments_are_skipped(self):
        assert segments_of("Dãy LK 23") == []
        assert segments_of("Một đoạn rất dài của con phố nào đó ở đây") == []

    def test_ward_restatement(self):
        assert is_ward_restatement("Khương Đình", "phuong khuong dinh")
        assert is_ward_restatement("Khu vực Khương Đình", "phuong khuong dinh")
        assert is_ward_restatement("Khu vực cổng chào An Khánh", "xa an khanh")
        assert not is_ward_restatement("Triều Khúc", "phuong khuong dinh")


class TestQueries:
    def test_prefix_variants_then_bare(self):
        assert queries_for_segment("Triều Khúc") == [
            ("Phố Triều Khúc", "Phố Triều Khúc"),
            ("Đường Triều Khúc", "Đường Triều Khúc"),
            ("Triều Khúc", "Phố Triều Khúc"),
        ]


class TestConfidence:
    @pytest.mark.parametrize(
        "distance,compound,expected",
        [(0.5, False, 0.70), (2.0, False, 0.60), (4.0, False, 0.55), (0.5, True, 0.55)],
    )
    def test_bands_and_compound_cap(self, distance, compound, expected):
        assert confidence_for(distance, compound) == expected


class TestCombine:
    def _pick(self, lat, lng, km):
        return SegmentPick("x", lat, lng, km, "x")

    def test_single_pick_is_used_as_is(self):
        assert combine([self._pick(21.0, 105.8, 0.4)], WARD) == (21.0, 105.8, 0.4)

    def test_close_pair_gives_midpoint(self):
        lat, lng, _ = combine([self._pick(21.000, 105.800, 1.0), self._pick(21.010, 105.800, 1.2)], WARD)
        assert lat == pytest.approx(21.005) and lng == pytest.approx(105.8)

    def test_distant_pair_keeps_the_pick_nearer_the_ward(self):
        lat, _, km = combine([self._pick(21.00, 105.80, 2.0), self._pick(21.10, 105.80, 0.5)], WARD)
        assert (lat, km) == (21.10, 0.5)

    def test_nothing_gives_none(self):
        assert combine([], WARD) is None


class TestEvaluate:
    def _group(self, *segments):
        return BareGroup("x", "phuong khuong dinh", "Quận Thanh Xuân", WARD, list(segments))

    def test_first_prefix_variant_wins(self):
        lookup = _lookup({"Phố Triều Khúc": [_hit(20.989, 105.809, "Phố Triều Khúc")]})
        outcome = evaluate(self._group("Triều Khúc"), lookup)
        assert outcome.status == "ok" and outcome.confidence == 0.70 and not outcome.compound

    def test_falls_through_to_duong(self):
        lookup = _lookup({
            "Phố Triều Khúc": [],
            "Đường Triều Khúc": [_hit(20.989, 105.809, "Đường Triều Khúc")],
        })
        assert evaluate(self._group("Triều Khúc"), lookup).status == "ok"

    def test_other_street_is_rejected(self):
        lookup = _lookup({q: [_hit(20.989, 105.809, "Phố Triều Hoa")] for q, _ in queries_for_segment("Triều Khúc")})
        assert evaluate(self._group("Triều Khúc"), lookup).status == "geocode_failed"

    def test_far_result_is_too_far(self):
        lookup = _lookup({q: [_hit(21.30, 105.809, "Phố Triều Khúc")] for q, _ in queries_for_segment("Triều Khúc")})
        assert evaluate(self._group("Triều Khúc"), lookup).status == "too_far"

    def test_compound_midpoint_has_capped_confidence(self):
        lookup = _lookup({
            "Phố Lý Thường Kiệt": [_hit(20.990, 105.809, "Phố Lý Thường Kiệt")],
            "Phố Phan Bội Châu": [_hit(20.992, 105.809, "Phố Phan Bội Châu")],
        })
        outcome = evaluate(self._group("Phan Bội Châu", "Lý Thường Kiệt"), lookup)
        assert outcome.status == "ok" and outcome.compound and outcome.confidence == 0.55

    def test_compound_with_one_hit_still_places(self):
        lookup = _lookup({"Phố Phan Bội Châu": [_hit(20.992, 105.809, "Phố Phan Bội Châu")]})
        lookup2 = lambda q: lookup(q) if q in ("Phố Phan Bội Châu",) else []
        outcome = evaluate(self._group("Phan Bội Châu", "Lý Thường Kiệt"), lookup2)
        assert outcome.status == "ok" and outcome.confidence == 0.55

    def test_uncached_is_not_geocoded(self):
        assert evaluate(self._group("Triều Khúc"), _lookup({})).status == "not_geocoded"


class TestGrouping:
    INDEX = {"phuong khuong dinh": WARD}

    def test_groups_rows_and_separates_restatements(self):
        rows = [
            _row("Triều Khúc"),
            _row("Triều Khúc", rid=2),
            _row("Khương Đình", rid=3),
            _row("Đường Trần Bình", rid=4),
            _row("Khu Ngoại giao đoàn", rid=5),
        ]
        groups, restating = group_bare(rows, self.INDEX)
        assert [(g.location_name, g.row_count) for g in groups] == [("Triều Khúc", 2)]
        assert [r.id for r in restating] == [3]

    def test_ward_name_segment_is_not_a_street(self):
        ward = WardInfo("phuong phu dien", "Phường Phú Diễn", 21.05, 105.76)
        groups, _ = group_bare(
            [_row("Doãn Kế Thiện – Phú Diễn", ward="phuong phu dien")], {"phuong phu dien": ward}
        )
        assert groups[0].segments == ["Doãn Kế Thiện"]

    def test_house_descriptions_are_not_streets(self):
        assert segments_of("Nhà dân bị ngập – Sơn Đồng") == []

    def test_unknown_ward_is_skipped(self):
        assert group_bare([_row("Triều Khúc", ward="phuong khong co")], self.INDEX) == ([], [])


class TestApplyFilter:
    def test_only_untouched_rows(self):
        group = BareGroup("Triều Khúc", "phuong khuong dinh", None, WARD, ["Triều Khúc"])
        sql = str(and_(*group_conditions(group)).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for fragment in ("lat IS NULL", "coord_source IS NULL", "coord_flag IS NULL", "district IS NULL"):
            assert fragment in sql
