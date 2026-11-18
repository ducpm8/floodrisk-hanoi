"""
Unit tests for scripts/assign_ward_from_name.py (Tier 3 step 2).

No network, no database.
"""

from types import SimpleNamespace

import pytest

from scripts.assign_ward_from_name import (
    evaluate,
    is_target,
    judge,
    ward_district_history,
    ward_mentions,
)

WARDS = {"phuong cau giay", "phuong mai dich", "phuong thanh xuan", "xa thanh xuan", "xa mai lam", "xa an khanh"}


def _row(name, district="Quận Cầu Giấy", ward=None, lat=None, flag=None, source=None, rid=1):
    return SimpleNamespace(id=rid, location_name=name, district=district, ward=ward,
                           lat=lat, coord_flag=flag, coord_source=source)


class TestMentions:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Phố Dương Đình Nghệ (P. Cầu Giấy)", ["phuong cau giay"]),
            ("Phố Trần Bình (UBND phường Mai Dịch – Bệnh viện 19/8)", ["phuong mai dich"]),
            ("Phố Trần Bình (UBND P.Mai Dịch đến BV 19/8)", ["phuong mai dich"]),
            ("Quốc lộ 3 (qua xã Mai Lâm)", ["xa mai lam"]),
            ("Phố Quan Nhân và Nhân Hòa (phường Thanh Xuân)", ["phuong thanh xuan"]),
            ("Tỉnh lộ 421B (nối Quốc Oai – thị trấn Xuân Mai)", ["thi tran xuan mai"]),
        ],
    )
    def test_single_mention(self, name, expected):
        assert ward_mentions(name) == expected

    def test_two_wards(self):
        assert ward_mentions("Đường tỉnh 421B (nối xã Xuân Mai – xã Kiều Phú)") == ["xa xuan mai", "xa kieu phu"]

    def test_same_ward_twice_counts_once(self):
        assert ward_mentions("Phố A (phường Mai Dịch, UBND phường Mai Dịch)") == ["phuong mai dich"]

    def test_prefix_is_kept(self):
        assert ward_mentions("Đường X (xã Thanh Xuân)") == ["xa thanh xuan"]
        assert ward_mentions("Đường X (phường Thanh Xuân)") == ["phuong thanh xuan"]

    @pytest.mark.parametrize("name", ["Phố Hoa Bằng", "Đường Tố Hữu (đoạn qua THPT Trung Văn)", "Phố A (phường)"])
    def test_none(self, name):
        assert ward_mentions(name) == []

    def test_lowercase_word_is_not_a_ward(self):
        assert ward_mentions("Phố X (phường lân cận)") == []


class TestTarget:
    def test_target_rules(self):
        assert is_target(_row("Phố Hoa Bằng (P. Cầu Giấy)"))
        assert not is_target(_row("Phố Hoa Bằng", ward="phuong cau giay"))
        assert not is_target(_row("Phố Hoa Bằng", district=None))
        assert not is_target(_row("Phố Hoa Bằng", lat=21.0))
        assert not is_target(_row("Phố Hoa Bằng", flag="geocode_failed"))
        assert not is_target(_row("Phố Hoa Bằng", source="geocoded_street"))
        assert not is_target(_row("Xã Thư Lâm (đường 32)"))


class TestJudge:
    HISTORY = {"phuong cau giay": {"Quận Cầu Giấy"}, "xa an khanh": {"Huyện Hoài Đức"}}

    def test_ok_when_district_agrees(self):
        v = judge(_row("Phố X (P. Cầu Giấy)"), WARDS, self.HISTORY)
        assert (v.verdict, v.ward) == ("ok", "phuong cau giay")

    def test_unverified_without_history(self):
        v = judge(_row("Phố X (phường Mai Dịch)"), WARDS, self.HISTORY)
        assert (v.verdict, v.ward) == ("unverified", "phuong mai dich")

    def test_district_mismatch_is_not_assigned(self):
        v = judge(_row("Đường Hoàng Tùng (xã An Khánh)", district="Quận Hà Đông"), WARDS, self.HISTORY)
        assert v.verdict == "district_mismatch"

    def test_multiple_wards_are_skipped(self):
        v = judge(_row("Phố X (phường Mai Dịch – phường Cầu Giấy)"), WARDS, self.HISTORY)
        assert v.verdict == "multi" and v.ward is None

    def test_unknown_ward_is_unresolved(self):
        v = judge(_row("Phố X (phường Không Có)"), WARDS, self.HISTORY)
        assert v.verdict == "unresolved"

    def test_no_mention(self):
        assert judge(_row("Phố Hoa Bằng"), WARDS, self.HISTORY).verdict == "none"


class TestHistory:
    def test_unknown_placeholder_is_ignored(self):
        rows = [
            _row("a", district="Unknown", ward="phuong mai dich"),
            _row("b", district="Quận Cầu Giấy", ward="phuong cau giay"),
            _row("c", district="Quận Hà Đông", ward="ward-not-in-centroids"),
        ]
        history = ward_district_history(rows, WARDS)
        assert "phuong mai dich" not in history
        assert history["phuong cau giay"] == {"Quận Cầu Giấy"}
        assert "ward-not-in-centroids" not in history

    def test_evaluate_only_targets(self):
        rows = [_row("Phố X (P. Cầu Giấy)"), _row("Phố Y", ward="phuong cau giay", rid=2)]
        assert [v.row_id for v in evaluate(rows, WARDS, {})] == [1]
