"""
Unit tests for scripts/geocode_poi.py (named-place geocoding).

No network, no database: name classification, query building, label matching,
distance validation and the apply filter are pure.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from scripts.geocode_poi import (
    PoiGroup,
    build_queries,
    confidence_for,
    distinctive_tokens,
    evaluate_groups,
    generic_rows,
    group_filter,
    group_poi,
    is_generic_area,
    is_named_poi,
    label_matches,
    pick_result,
    poi_category,
    poi_core,
)
from scripts.geocode_tier1 import NominatimCache, WardInfo

WARD = WardInfo(ward_name="xa thu lam", ward_name_vi="Xã Thư Lâm", lat=21.15, lng=105.88)
URBAN = WardInfo(ward_name="phuong xuan la", ward_name_vi="Phường Xuân La", lat=21.07, lng=105.80)


def _hit(lat, lng, label, key="place", value="hamlet"):
    return {
        "lat": str(lat),
        "lon": str(lng),
        "display_name": label,
        "label": label,
        "class": key,
        "type": value,
    }


def _row(name, ward="xa thu lam", district="Huyện Đông Anh", rid=1):
    return SimpleNamespace(id=rid, location_name=name, ward=ward, district=district)


class TestClassification:
    @pytest.mark.parametrize(
        "name",
        [
            "Thôn Kim Tiên (xã Thư Lâm)",
            "Khu đô thị Ciputra",
            "Bến xe Yên Nghĩa – ngã ba Ba La",
            "Chân cầu HH2 Dương Nội",
            "Sân vận động quốc gia Mỹ Đình",
            "Xóm Bến Vôi (xã Cấn Hữu)",
        ],
    )
    def test_named_places(self, name):
        assert is_named_poi(name)

    @pytest.mark.parametrize(
        "name",
        [
            "Đường Trần Bình",                       # street -> tier 1
            "Xã Thư Lâm",                            # ward-only
            "Tổ dân phố 10P (phường Phú La cũ)",     # generic subdivision
            "Khu dân cư số 3",
            "Cao tốc Pháp Vân – Cầu Giẽ (Km191)",    # road mentioning a bridge
            "Đại lộ Thăng Long (cầu vượt An Khánh)", # street
            "Hoa Bằng",                              # no place keyword
            "Nhà dân tại phường Cầu Giấy",           # something else comes first
            "Sân Miếu bản làng Giang Cao (thôn 2)",
            "Dãy LK 23, KĐT Vân Canh",
        ],
    )
    def test_not_named_places(self, name):
        assert not is_named_poi(name)

    @pytest.mark.parametrize(
        "name",
        ["Khu vực cổng chào Khu đô thị Nam An Khánh", "Các tuyến quanh SVĐ quốc gia Mỹ Đình", "Trước Bệnh viện K Tân Triều"],
    )
    def test_leading_noise_words_are_allowed(self, name):
        assert is_named_poi(name)

    def test_generic_area(self):
        assert is_generic_area("Tổ dân phố số 1+4 phường Yên Nghĩa")
        assert not is_generic_area("Thôn Kim Tiên")

    def test_generic_rows_picks_only_generic(self):
        rows = [_row("Tổ dân phố 10P"), _row("Thôn Kim Tiên"), _row("Đường Mỹ Đình")]
        assert [r.location_name for r in generic_rows(rows)] == ["Tổ dân phố 10P"]


class TestStreetPlusPoi:
    def test_street_name_is_rejected_by_default(self):
        assert not is_named_poi("Cầu 72II đường 423 (Km8+400)")

    def test_allowed_after_the_street_pass_failed(self):
        assert is_named_poi("Cầu 72II đường 423 (Km8+400)", allow_street=True)

    def test_core_stops_before_the_street(self):
        assert poi_core("Cầu 72II đường 423 (Km8+400)") == "Cầu 72II"
        assert poi_core("Cầu 72II trên đường 423 (xã An Khánh)") == "Cầu 72II"

    def test_group_uses_failed_flag(self):
        ward = WardInfo("xa an khanh", "Xã An Khánh", 21.0, 105.7)
        ok = SimpleNamespace(id=1, location_name="Cầu 72II đường 423", ward="xa an khanh",
                             district=None, coord_flag="geocode_failed")
        fresh = SimpleNamespace(id=2, location_name="Cầu 72II đường 423", ward="xa an khanh",
                                district=None, coord_flag=None)
        assert len(group_poi([ok], {"xa an khanh": ward})) == 1
        assert group_poi([fresh], {"xa an khanh": ward}) == []

    def test_compact_number_token_matches_spaced_label(self):
        assert label_matches("Cầu 72II", "Cầu 72 II, Xã Hưng Đạo")

    def test_road_descriptions_are_generic_areas(self):
        for name in (
            "Các tuyến đường đến Trường Tiểu học Tây Mỗ",
            "Đường giao thông nông thôn và nội đồng xã Quảng Bị",
            "Đường nội bộ KĐT Geleximco",
        ):
            assert is_generic_area(name)
        assert not is_generic_area("Tuyến đường Lê Văn Lương")


class TestPoiCore:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Thôn Kim Tiên (xã Thư Lâm)", "Thôn Kim Tiên"),
            ("Khu vực bến xe Yên Nghĩa", "bến xe Yên Nghĩa"),
            ("Chân cầu HH2 Dương Nội", "cầu HH2 Dương Nội"),
            ("Xóm Hòa Bình, thôn An Lạc (xã Trung Giã)", "Xóm Hòa Bình"),
            ("Bến xe Yên Nghĩa – ngã ba Ba La", "Bến xe Yên Nghĩa"),
            ("Thôn Nhân Lý xã Nam Phương Tiến", "Thôn Nhân Lý"),
        ],
    )
    def test_core_extraction(self, name, expected):
        assert poi_core(name) == expected

    def test_distinctive_tokens_drop_leading_keyword(self):
        assert distinctive_tokens("Thôn Kim Tiên") == ["kim", "tien"]
        assert distinctive_tokens("Khu đô thị Ciputra") == ["ciputra"]
        assert distinctive_tokens("Thôn") == []


class TestQueries:
    def test_core_then_core_with_ward(self):
        assert build_queries("Thôn Kim Tiên", "Xã Thư Lâm")[:2] == [
            "Thôn Kim Tiên",
            "Thôn Kim Tiên Xã Thư Lâm",
        ]

    def test_bare_name_variant_needs_two_words(self):
        assert "Kim Tiên Xã Thư Lâm" in build_queries("Thôn Kim Tiên", "Xã Thư Lâm")
        assert "Ciputra Phường Xuân La" not in build_queries("Khu đô thị Ciputra", "Phường Xuân La")

    def test_no_duplicates(self):
        queries = build_queries("Thôn Kim Tiên", "")
        assert len(queries) == len(set(queries))


class TestLabelMatching:
    def test_all_tokens_required(self):
        assert label_matches("Thôn Kim Tiên", "Thôn Kim Tiên, Thư Lâm")
        assert not label_matches("Thôn Kim Tiên", "Thôn Kim Sơn")

    def test_extra_label_words_are_fine_for_places(self):
        assert label_matches("Khu đô thị Ciputra", "Khu đô thị Ciputra Nam Thăng Long Đường Nguyễn Hoàng Tôn")

    def test_empty_name_never_matches(self):
        assert not label_matches("Thôn", "Thôn Kim Tiên")
        assert not label_matches("Thôn Kim Tiên", "")


class TestPickResult:
    def test_picks_matching_place_within_radius(self):
        pick = pick_result([_hit(21.151, 105.881, "Thôn Kim Tiên")], WARD, "Thôn Kim Tiên")
        assert pick is not None and pick.distance_km < 1

    def test_rejects_other_place(self):
        assert pick_result([_hit(21.151, 105.881, "Thôn Kim Sơn")], WARD, "Thôn Kim Tiên") is None

    def test_rejects_beyond_radius(self):
        assert pick_result([_hit(21.40, 105.88, "Thôn Kim Tiên")], WARD, "Thôn Kim Tiên") is None

    def test_rejects_outside_hanoi(self):
        assert pick_result([_hit(10.0, 106.0, "Thôn Kim Tiên")], WARD, "Thôn Kim Tiên") is None

    def test_ignores_administrative_boundaries(self):
        boundary = _hit(21.15, 105.88, "Thôn Kim Tiên", key="boundary", value="administrative")
        assert pick_result([boundary], WARD, "Thôn Kim Tiên") is None

    def test_place_beats_nearer_road(self):
        road = _hit(21.1501, 105.8801, "Đường Kim Tiên", key="highway", value="residential")
        place = _hit(21.152, 105.882, "Kim Tiên", key="place", value="hamlet")
        pick = pick_result([road, place], WARD, "Thôn Kim Tiên")
        assert pick is not None and pick.osm_class == "place"

    def test_urban_ward_uses_tighter_radius(self):
        # ~3.9 km from the ward centroid: outside a phường (2.5 km), inside a xã (5 km).
        far = _hit(URBAN.lat + 0.035, URBAN.lng, "Ciputra", key="landuse", value="residential")
        rural = WardInfo("xa xuan la", "Xã Xuân La", URBAN.lat, URBAN.lng)
        assert pick_result([far], URBAN, "Khu đô thị Ciputra") is None
        assert pick_result([far], rural, "Khu đô thị Ciputra") is not None


class TestCompatibility:
    def test_category_from_leading_keyword(self):
        assert poi_category("Thôn Kim Tiên") == "village"
        assert poi_category("KĐT An Khánh") == "estate"
        assert poi_category("Bến xe Yên Nghĩa") == "bus_station"
        assert poi_category("cầu HH2 Dương Nội") == "crossing"
        assert poi_category("Sân vận động Mỹ Đình") == "stadium"

    @pytest.mark.parametrize(
        "core,key,value",
        [
            ("KĐT An Khánh", "landuse", "industrial"),     # sewage plant
            ("Thôn Tân Phú", "highway", "bus_stop"),
            ("Trường TH Tây Mỗ", "amenity", "police"),
            ("Bệnh viện K", "amenity", "bus_station"),
        ],
    )
    def test_wrong_kind_is_rejected(self, core, key, value):
        hit = _hit(21.15, 105.88, core.replace("TH", "Tiểu học"), key=key, value=value)
        assert pick_result([hit], WARD, core) is None

    def test_bus_stop_is_not_a_bridge(self):
        stop = _hit(21.15, 105.88, "Trước cầu Tân Phú 85m", key="highway", value="bus_stop")
        road = _hit(21.15, 105.88, "Cầu Tân Phú", key="highway", value="secondary")
        assert pick_result([stop], WARD, "Cầu Tân Phú") is None
        assert pick_result([road], WARD, "Cầu Tân Phú") is not None

    def test_right_kind_is_accepted(self):
        hit = _hit(21.15, 105.88, "Khu đô thị An Khánh", key="landuse", value="residential")
        assert pick_result([hit], WARD, "KĐT An Khánh") is not None

    def test_ward_own_name_is_not_a_bridge(self):
        ward = WardInfo("phuong cau giay", "Phường Cầu Giấy", 21.03, 105.79)
        assert group_poi([_row("Cầu Giấy", ward="phuong cau giay")], {"phuong cau giay": ward}) == []


class TestConfidence:
    @pytest.mark.parametrize(
        "distance,expected",
        [(0.2, 0.85), (1.0, 0.85), (1.5, 0.70), (2.5, 0.70), (4.0, 0.55)],
    )
    def test_bands(self, distance, expected):
        assert confidence_for(distance) == expected


class TestGrouping:
    INDEX = {"xa thu lam": WARD}

    def test_groups_and_counts_rows(self):
        rows = [_row("Thôn Kim Tiên"), _row("Thôn Kim Tiên", rid=2), _row("Thôn Đào Thục", rid=3)]
        groups = group_poi(rows, self.INDEX)
        assert [(g.location_name, g.row_count) for g in groups] == [
            ("Thôn Kim Tiên", 2),
            ("Thôn Đào Thục", 1),
        ]

    def test_skips_streets_generic_and_unknown_wards(self):
        rows = [
            _row("Đường Trần Bình"),
            _row("Tổ dân phố 10P"),
            _row("Thôn Kim Tiên", ward="xa khong co"),
        ]
        assert group_poi(rows, self.INDEX) == []


class TestEvaluate:
    def _group(self):
        return PoiGroup("Thôn Kim Tiên", "xa thu lam", "Huyện Đông Anh", WARD, "Thôn Kim Tiên",
                        queries=["Thôn Kim Tiên", "Thôn Kim Tiên Xã Thư Lâm"])

    def _cache(self, data):
        cache = NominatimCache.__new__(NominatimCache)
        cache.path = None
        cache.data = data
        cache._unsaved = 0
        return cache

    def test_ok_on_second_query(self):
        cache = self._cache(
            {
                "Thôn Kim Tiên": [],
                "Thôn Kim Tiên Xã Thư Lâm": [_hit(21.151, 105.881, "Thôn Kim Tiên")],
            }
        )
        (outcome,) = evaluate_groups([self._group()], cache)
        assert outcome.status == "ok" and outcome.confidence == 0.85

    def test_far_hit_is_too_far(self):
        cache = self._cache(
            {
                "Thôn Kim Tiên": [_hit(21.40, 105.88, "Thôn Kim Tiên")],
                "Thôn Kim Tiên Xã Thư Lâm": [],
            }
        )
        (outcome,) = evaluate_groups([self._group()], cache)
        assert outcome.status == "too_far"

    def test_no_hit_is_failed(self):
        cache = self._cache({"Thôn Kim Tiên": [], "Thôn Kim Tiên Xã Thư Lâm": []})
        (outcome,) = evaluate_groups([self._group()], cache)
        assert outcome.status == "geocode_failed"

    def test_uncached_query_is_not_geocoded(self):
        (outcome,) = evaluate_groups([self._group()], self._cache({"Thôn Kim Tiên": []}))
        assert outcome.status == "not_geocoded"


class TestApplyFilter:
    def _sql(self, group):
        conditions = group_filter(group)
        from sqlalchemy import and_

        return str(
            and_(*conditions).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )

    def test_only_touches_unplaced_rows(self):
        group = PoiGroup("Thôn Kim Tiên", "xa thu lam", "Huyện Đông Anh", WARD, "Thôn Kim Tiên")
        sql = self._sql(group)
        assert "lat IS NULL" in sql
        assert "coord_source IS NULL" in sql
        assert "location_name = 'Thôn Kim Tiên'" in sql

    def test_null_district_uses_is_null(self):
        group = PoiGroup("Thôn Kim Tiên", "xa thu lam", None, WARD, "Thôn Kim Tiên")
        assert "district IS NULL" in self._sql(group)
