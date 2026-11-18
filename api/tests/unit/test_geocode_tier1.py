"""
Unit tests for scripts/geocode_tier1.py

No network and no database: Nominatim/Photon normalisation, query cleaning,
distance validation, confidence bands, grouping and the apply filter are pure.
"""

import csv
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from scripts.geocode_tier1 import (
    GeocoderClient,
    NominatimClient,
    NominatimCache,
    PhotonClient,
    Tier1Group,
    WardInfo,
    attempts,
    build_queries,
    build_ward_index,
    bare_street,
    clean_street,
    confidence_for,
    default_paths,
    geocode_group,
    group_filter,
    group_tier1,
    group_update,
    haversine_km,
    in_hanoi_bbox,
    is_admin_only_name,
    is_street_name,
    matches_street,
    parent_street,
    pick_result,
    queries_for,
    resolve_ward,
    street_alternates,
    street_tokens,
    write_report,
)


WARD = WardInfo(
    ward_name="phuong doi can",
    ward_name_vi="Phường Đội Cấn",
    lat=21.0345,
    lng=105.8285,
)


def _row(location_name, ward="phuong doi can", district="Quận Ba Đình", rid=1):
    return SimpleNamespace(
        id=rid,
        location_name=location_name,
        ward=ward,
        district=district,
    )


def _group(name="Ngõ 100 Đội Cấn", parent="Đội Cấn", row_count=1):
    return Tier1Group(
        location_name=name,
        ward=WARD.ward_name,
        district="Quận Ba Đình",
        ward_info=WARD,
        street=clean_street(name),
        parent=parent,
        row_count=row_count,
    )


def _hit(lat, lng, cls="highway", typ="residential", name="Phố Đội Cấn"):
    return {
        "lat": str(lat),
        "lon": str(lng),
        "display_name": name,
        "label": name,
        "class": cls,
        "type": typ,
        "importance": 0.5,
    }


class FakeClient(GeocoderClient):
    """In-memory client: responses keyed by exact query, no HTTP."""

    name = "fake"
    url = "fake://"

    def __init__(self, responses):
        self.cache = NominatimCache.__new__(NominatimCache)
        self.cache.path = None
        self.cache.data = {}
        self.cache._unsaved = 0
        self.network_calls = 0
        self.responses = responses
        self.calls = []

    def search(self, query):
        self.calls.append(query)
        return self.responses.get(query)


# ---------------------------------------------------------------------------
# Name classification / cleaning
# ---------------------------------------------------------------------------

class TestNameHelpers:
    @pytest.mark.parametrize(
        "name",
        [
            "Đường Mỹ Đình",
            "Phố Đội Cấn",
            "Ngõ 100 Đội Cấn",
            "Ngách 4/2 Đại La",
            "Hẻm 5 Nguyễn Trãi",
            "Đại lộ Thăng Long",
            "Quốc lộ 32",
            "Tỉnh lộ 421B",
            "Đê Bùi 2",
            "QL32",
            "TL 421B",
        ],
    )
    def test_street_names_are_detected(self, name):
        assert is_street_name(name)

    @pytest.mark.parametrize(
        "name",
        ["Xã Sóc Sơn", "Phường Đội Cấn", "Thị trấn Xuân Mai", "Quận Ba Đình", None, ""],
    )
    def test_non_street_names_are_rejected(self, name):
        assert not is_street_name(name)

    def test_admin_only_names(self):
        assert is_admin_only_name("Xã Sóc Sơn")
        assert is_admin_only_name("Phường Đội Cấn")
        assert is_admin_only_name("Huyện Chương Mỹ")
        assert not is_admin_only_name("Đường Mỹ Đình")

    def test_clean_street_strips_parentheses(self):
        assert clean_street("Phố Minh Khai (chân cầu Vĩnh Tuy)") == "Phố Minh Khai"
        assert clean_street("Đường 32 (đoạn cổng chào An Khánh)") == "Đường 32"

    def test_clean_street_strips_note_clauses(self):
        assert clean_street("Đường Yên Nghĩa - ngã ba Ba La") == "Đường Yên Nghĩa"
        assert clean_street("Phố Đội Cấn — đoạn qua chợ") == "Phố Đội Cấn"

    def test_clean_street_normalizes_slash_type(self):
        assert clean_street("Đường/phố Thái Hà") == "Đường Thái Hà"
        assert clean_street("Phố/đường Láng Hạ") == "Đường Láng Hạ"

    def test_clean_street_collapses_whitespace(self):
        assert clean_street("  Đường   Mỹ Đình  ") == "Đường Mỹ Đình"


class TestParentStreet:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Ngõ 100 Đội Cấn", "Đội Cấn"),
            ("Ngách 4/2 Đại La", "Đại La"),
            ("Hẻm 5 Nguyễn Trãi", "Nguyễn Trãi"),
            ("Ngõ 100 Đội Cấn (gần chợ)", "Đội Cấn"),
            ("Phố Đội Cấn", None),
            ("Đường Mỹ Đình", None),
        ],
    )
    def test_parent_extraction(self, name, expected):
        assert parent_street(name) == expected


# ---------------------------------------------------------------------------
# Query building / geometry / confidence
# ---------------------------------------------------------------------------

class TestQueries:
    def test_ward_then_district_order(self):
        queries = build_queries("Phố Đội Cấn", "Phường Đội Cấn", "Quận Ba Đình")
        assert queries[0] == (
            "Phố Đội Cấn, Phường Đội Cấn, Quận Ba Đình, Hà Nội, Việt Nam"
        )
        assert queries[1] == "Phố Đội Cấn, Quận Ba Đình, Hà Nội, Việt Nam"

    def test_empty_ward_is_skipped(self):
        queries = build_queries("Phố Đội Cấn", None, "Quận Ba Đình")
        assert queries[0] == "Phố Đội Cấn, Quận Ba Đình, Hà Nội, Việt Nam"
        assert len(queries) == 1

    def test_bare_street_strips_type(self):
        assert bare_street("Đường Vĩnh Hưng") == "Vĩnh Hưng"
        assert bare_street("Phố Đội Cấn") == "Đội Cấn"
        assert bare_street("Ngõ 100 Đội Cấn") == "100 Đội Cấn"
        assert bare_street("Đường 32") == "32"

    def test_photon_queries_use_street_then_bare(self):
        assert queries_for("photon", "Đường Vĩnh Hưng", "Phường Vĩnh Hưng", "Quận Hoàng Mai") == [
            "Đường Vĩnh Hưng",
            "Vĩnh Hưng",
        ]

    def test_photon_skips_bare_for_number_only_streets(self):
        assert queries_for("photon", "Đường 32", "Xã An Khánh", "Huyện Hoài Đức") == [
            "Đường 32"
        ]

    def test_nominatim_queries_keep_addresses(self):
        assert queries_for(
            "nominatim", "Phố Đội Cấn", "Phường Đội Cấn", "Quận Ba Đình"
        ) == build_queries("Phố Đội Cấn", "Phường Đội Cấn", "Quận Ba Đình")


class TestGeometry:
    def test_hanoi_bbox_boundaries(self):
        assert in_hanoi_bbox(20.5, 105.2)
        assert in_hanoi_bbox(21.5, 106.1)
        assert not in_hanoi_bbox(20.49, 105.8)
        assert not in_hanoi_bbox(21.0, 106.11)

    def test_haversine_zero_and_known_distance(self):
        assert haversine_km(21.0, 105.8, 21.0, 105.8) == pytest.approx(0.0)
        # ~1 km east at this latitude
        assert haversine_km(21.0, 105.8, 21.0, 105.8096) == pytest.approx(1.0, abs=0.05)

    @pytest.mark.parametrize(
        ("distance", "fallback", "expected"),
        [
            (0.0, False, 0.90),
            (1.0, False, 0.90),
            (1.01, False, 0.75),
            (2.5, False, 0.75),
            (2.6, False, 0.60),
            (4.9, False, 0.60),
            (0.2, True, 0.60),
            (4.0, True, 0.60),
        ],
    )
    def test_confidence_bands(self, distance, fallback, expected):
        assert confidence_for(distance, fallback=fallback) == expected


class TestStreetMatching:
    def test_tokens_drop_type_words(self):
        assert street_tokens("Phố Đội Cấn") == ["doi", "can"]
        assert street_tokens("Ngõ 100 Đội Cấn") == ["100", "doi", "can"]
        assert street_tokens("Đường Mỹ Đình") == ["my", "dinh"]

    def test_matches_only_same_street(self):
        assert matches_street("Phố Vĩnh Hưng", "Phố Vĩnh Hưng")
        assert matches_street("Ngõ 100 Đội Cấn", "Ngõ 100 Phố Đội Cấn")
        assert not matches_street("Phố Vĩnh Hưng", "Ngõ 79 Phố Thanh Đàm")
        assert not matches_street("Đường Vĩnh Hưng", "Phố Vĩnh Hoàng")

    def test_pick_rejects_unrelated_street(self):
        results = [_hit(21.0346, 105.8286, name="Ngõ 79 Phố Thanh Đàm")]
        assert (
            pick_result(
                results, WARD.lat, WARD.lng, radius_km=2.5, street="Phố Vĩnh Hưng"
            )
            is None
        )
        assert (
            pick_result(
                results, WARD.lat, WARD.lng, radius_km=2.5, street="Phố Thanh Đàm"
            )
            is not None
        )

    def test_number_only_street_needs_full_name(self):
        assert not matches_street("Đường 32", "Ngõ 32 Đường Phú Vinh")
        assert matches_street("Đường 32", "Đường 32")

    @pytest.mark.parametrize(
        "street,label",
        [
            # A different street that merely starts with the same word.
            ("Đường Thành", "Phố Thanh Yên, Hồng Hà, Hà Nội"),
            # Estate / POI named after the street.
            ("Đường Văn Quán", "Đường TT15 Khu Đô Thị Văn Quán, Hà Đông, Hà Nội"),
            ("Đường Yên Hòa", "Đường Nội Khu Chung Cư Yên Hòa Park View, Yên Hoà, Hà Nội"),
            ("Đường Phú Lương", "Dự án KĐT mới Phú Lương (Đường Phúc La - Văn Phú) Phố Văn Khê, Hà Nội"),
            # POI note that only mentions the street in parentheses.
            ("Đường Đội Cấn", "Gần ngõ 378 đường Bưởi tránh (bên giao Đội Cấn) Ngõ 376 Đường Bưởi, Hà Nội"),
        ],
    )
    def test_rejects_labels_with_extra_distinctive_words(self, street, label):
        assert not matches_street(street, label)

    @pytest.mark.parametrize(
        "street,label",
        [
            # Alley numbers and street-type words are not distinctive.
            ("Đường Vĩnh Hưng", "Ngách 76/2 Phố Vĩnh Hưng, Vĩnh Hưng, Hà Nội"),
            ("Ngõ 77 Xuân La", "Hẻm 15 Ngách 25 Ngõ 77 Xuân La, Tây Hồ, Hà Nội"),
            # Parenthetical in the label is a note, not part of the name.
            ("Đại lộ Thăng Long", "Đại lộ Thăng Long (Láng - Hoà Lạc), Tây Mỗ, Hà Nội"),
            # Multi-token street may carry one extra word.
            ("Đường Tố Hữu", "Đường Galaxy Tố Hữu, Hà Đông, Hà Nội"),
        ],
    )
    def test_accepts_same_street_with_alley_numbers_or_notes(self, street, label):
        assert matches_street(street, label)


class TestStreetAlternates:
    def test_compound_name_is_split(self):
        name = "Phố Đội Cấn và ngõ 100 Đội Cấn"
        alts = street_alternates(name, clean_street(name), parent_street(name))
        assert "Phố Đội Cấn" in alts
        assert "ngõ 100 Đội Cấn" in alts

    def test_leading_noise_is_dropped(self):
        name = "Cuối ngõ 42 Giang Văn Minh"
        alts = street_alternates(name, clean_street(name), parent_street(name))
        assert alts[0] == "ngõ 42 Giang Văn Minh"
        assert "Giang Văn Minh" in alts

    def test_last_street_keyword_wins(self):
        name = "Chân cầu HH2 đường Nguyễn Trác"
        alts = street_alternates(name, clean_street(name), parent_street(name))
        assert alts[0] == "đường Nguyễn Trác"

    def test_gom_road_falls_back_to_main_road(self):
        name = "Đường gom Đại lộ Thăng Long (cầu vượt An Khánh – hầm 6)"
        alts = street_alternates(name, clean_street(name), parent_street(name))
        assert "Đại lộ Thăng Long" in alts

    def test_to_dan_pho_is_not_a_street(self):
        name = "Tổ dân phố 10P (phường Phú La cũ)"
        assert street_alternates(name, clean_street(name), None) == []

    def test_street_named_only_in_parentheses(self):
        name = "Khu đô thị Nam An Khánh (đường Lê Trọng Tấn)"
        alts = street_alternates(name, clean_street(name), parent_street(name))
        assert "đường Lê Trọng Tấn" in alts

    def test_parenthetical_without_street_adds_nothing(self):
        name = "Đường Tây Tựu (trước cổng trường)"
        assert street_alternates(name, clean_street(name), None) == []

    def test_bare_alley_numbers_are_not_alternates(self):
        name = "Ngõ 1, 3, 5 Tổ dân phố Ngô Quyền"
        alts = street_alternates(name, clean_street(name), None)
        assert "Ngõ 1" not in alts
        assert all(alt.strip().lower() not in {"ngõ 3", "ngõ 5"} for alt in alts)

    def test_trailing_location_notes_are_cut(self):
        name = "Quốc lộ 6 đoạn qua Yên Nghĩa"
        alts = street_alternates(name, "Quốc lộ 6 đoạn qua Yên Nghĩa", None)
        assert "Quốc lộ 6" in alts

    def test_primary_and_parent_are_not_repeated(self):
        name = "Ngõ 100 Đội Cấn"
        alts = street_alternates(name, clean_street(name), parent_street(name))
        assert "Ngõ 100 Đội Cấn" not in alts
        assert "Đội Cấn" not in alts

    def test_attempt_order_and_fallback_flags(self):
        group = _group("Phố Đội Cấn và ngõ 100 Đội Cấn", parent=None)
        group.alternates = ["Phố Đội Cấn"]
        assert attempts(group) == [
            (group.street, False),
            ("Phố Đội Cấn", True),
        ]


class TestPickResult:
    def test_exact_label_beats_nearer_alley(self):
        results = [
            _hit(21.0345, 105.8285, name="Ngõ 120 Phố Đội Cấn"),
            _hit(21.0370, 105.8330, name="Phố Đội Cấn"),
        ]
        pick = pick_result(
            results, WARD.lat, WARD.lng, radius_km=2.5, street="Phố Đội Cấn"
        )
        assert pick is not None
        assert pick.lat == pytest.approx(21.0370)

    def test_prefers_highway_over_nearer_amenity(self):
        results = [
            _hit(21.0345, 105.8285, cls="amenity", typ="restaurant"),
            _hit(21.0370, 105.8330, cls="highway", typ="tertiary"),
        ]
        pick = pick_result(results, WARD.lat, WARD.lng, radius_km=2.5)
        assert pick is not None
        assert pick.osm_class == "highway"

    def test_nearest_wins_within_same_class(self):
        results = [
            _hit(21.0445, 105.8285, cls="highway"),
            _hit(21.0350, 105.8285, cls="highway"),
        ]
        pick = pick_result(results, WARD.lat, WARD.lng, radius_km=2.5)
        assert pick is not None
        assert pick.lat == pytest.approx(21.0350)

    def test_rejects_outside_bbox(self):
        results = [_hit(10.0, 105.8, cls="highway")]
        assert pick_result(results, WARD.lat, WARD.lng, radius_km=2.5) is None

    def test_rejects_beyond_radius(self):
        results = [_hit(21.08, 105.8285, cls="highway")]  # ~5 km north
        assert pick_result(results, WARD.lat, WARD.lng, radius_km=2.5) is None

    def test_xa_uses_the_5km_radius(self):
        xa = WardInfo("xa an khanh", "Xã An Khánh", 21.0, 105.72)
        results = [_hit(21.0, 105.765, cls="highway")]  # ~4.7 km
        assert pick_result(results, xa.lat, xa.lng, radius_km=5.0) is not None


# ---------------------------------------------------------------------------
# Ward resolution / grouping
# ---------------------------------------------------------------------------

class TestWardResolution:
    def _index(self):
        return build_ward_index(
            [
                SimpleNamespace(
                    ward_name="phuong doi can",
                    ward_name_vi="Phường Đội Cấn",
                    lat=21.0345,
                    lng=105.8285,
                ),
                SimpleNamespace(
                    ward_name="xa an khanh",
                    ward_name_vi="Xã An Khánh",
                    lat=21.0,
                    lng=105.72,
                ),
            ]
        )

    def test_exact(self):
        assert resolve_ward("phuong doi can", self._index()) is not None

    def test_prefix_added(self):
        assert resolve_ward("doi can", self._index()).ward_name == "phuong doi can"

    def test_prefix_stripped(self):
        assert resolve_ward("Phường Đội Cấn", self._index()).ward_name == "phuong doi can"

    def test_unknown(self):
        assert resolve_ward("phuong khong ton tai", self._index()) is None
        assert resolve_ward(None, self._index()) is None


class TestGroupTier1:
    def _index(self):
        return {
            "phuong doi can": WARD,
            "xa an khanh": WardInfo("xa an khanh", "Xã An Khánh", 21.0, 105.72),
            "thi tran xuan mai": WardInfo(
                "thi tran xuan mai", "Thị trấn Xuân Mai", 20.9, 105.57
            ),
        }

    def test_admin_names_with_street_mentions_are_not_tier1(self):
        rows = [_row("Xã Xuân Mai (tràn đê Bùi 2)", ward="thi tran xuan mai")]
        assert group_tier1(rows, self._index()) == []

    def test_groups_street_rows_with_resolvable_ward(self):
        rows = [
            _row("Ngõ 100 Đội Cấn", rid=1),
            _row("Ngõ 100 Đội Cấn", rid=2),
            _row("Phố Đội Cấn", rid=3),
        ]
        groups = group_tier1(rows, self._index())
        assert [g.location_name for g in groups] == ["Ngõ 100 Đội Cấn", "Phố Đội Cấn"]
        assert groups[0].row_count == 2
        assert groups[0].parent == "Đội Cấn"

    def test_skips_ward_only_and_unknown_wards(self):
        rows = [
            _row("Xã Sóc Sơn", ward="xa soc son"),
            _row("Đường Mỹ Đình", ward="phuong khong ton tai"),
        ]
        assert group_tier1(rows, self._index()) == []

    def test_grouping_is_district_sensitive(self):
        rows = [
            _row("Phố Đội Cấn", district="Quận Ba Đình", rid=1),
            _row("Phố Đội Cấn", district="Quận Đống Đa", rid=2),
        ]
        groups = group_tier1(rows, self._index())
        assert len(groups) == 2


# ---------------------------------------------------------------------------
# Geocode outcomes
# ---------------------------------------------------------------------------

class TestGeocodeGroup:
    def test_direct_hit(self):
        group = _group("Phố Đội Cấn", parent=None)
        queries = build_queries(group.street, WARD.ward_name_vi, group.district)
        client = FakeClient({queries[0]: [_hit(21.0348, 105.8290)]})
        outcome = geocode_group(client, group)
        assert outcome.status == "ok"
        assert outcome.fallback is False
        assert outcome.confidence == 0.90
        assert client.calls == [queries[0]]

    def test_parent_street_fallback(self):
        group = _group("Ngõ 100 Đội Cấn", parent="Đội Cấn")
        direct = build_queries(group.street, WARD.ward_name_vi, group.district)
        parent = build_queries(group.parent, WARD.ward_name_vi, group.district)
        client = FakeClient(
            {direct[0]: [], direct[1]: [], parent[0]: [_hit(21.0346, 105.8286)]}
        )
        outcome = geocode_group(client, group)
        assert outcome.status == "ok"
        assert outcome.fallback is True
        assert outcome.confidence == 0.60

    def test_far_hit_is_flagged_too_far(self):
        group = _group("Phố Đội Cấn", parent=None)
        queries = build_queries(group.street, WARD.ward_name_vi, group.district)
        client = FakeClient({q: [_hit(21.08, 105.8285)] for q in queries})
        outcome = geocode_group(client, group)
        assert outcome.status == "too_far"
        assert outcome.pick is None

    def test_no_hit_is_geocode_failed(self):
        group = _group("Phố Đội Cấn", parent=None)
        queries = build_queries(group.street, WARD.ward_name_vi, group.district)
        client = FakeClient({q: [] for q in queries})
        outcome = geocode_group(client, group)
        assert outcome.status == "geocode_failed"

    def test_unrelated_far_result_is_failed_not_too_far(self):
        group = _group("Phố Đội Cấn", parent=None)
        queries = build_queries(group.street, WARD.ward_name_vi, group.district)
        client = FakeClient(
            {q: [_hit(21.08, 105.8285, name="Phố Thanh Đàm")] for q in queries}
        )
        outcome = geocode_group(client, group)
        assert outcome.status == "geocode_failed"

    def test_network_error_is_not_cached(self):
        group = _group("Phố Đội Cấn", parent=None)
        queries = build_queries(group.street, WARD.ward_name_vi, group.district)
        client = FakeClient({queries[0]: None})
        outcome = geocode_group(client, group)
        assert outcome.status == "error"


# ---------------------------------------------------------------------------
# Apply filter (only NULL coordinates)
# ---------------------------------------------------------------------------

class TestApplyFilter:
    def test_update_only_touches_null_lat(self):
        statement = group_update(_group("Phố Đội Cấn"))
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "flood_events.lat IS NULL" in sql
        assert "flood_events.location_name" in sql
        assert "flood_events.ward" in sql
        assert "flood_events.district" in sql

    def test_filters_match_the_group_key(self):
        group = _group("Phố Đội Cấn")
        conditions = group_filter(group)
        assert len(conditions) == 4  # lat, location_name, ward, district

    def test_null_district_uses_is_null(self):
        group = _group("Phố Đội Cấn")
        group.district = None
        sql = str(group_update(group).compile(dialect=postgresql.dialect()))
        assert "flood_events.district IS NULL" in sql


# ---------------------------------------------------------------------------
# Report / providers / CLI paths
# ---------------------------------------------------------------------------

class TestReport:
    def test_csv_has_expected_columns_and_rows(self, tmp_path):
        group = _group("Phố Đội Cấn", parent=None)
        outcome = geocode_group(
            FakeClient(
                {
                    build_queries(
                        group.street, WARD.ward_name_vi, group.district
                    )[0]: [_hit(21.0348, 105.8290)]
                }
            ),
            group,
        )
        path = tmp_path / "report.csv"
        write_report([outcome], path)
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        assert len(rows) == 1
        assert rows[0]["status"] == "ok"
        assert rows[0]["confidence"] == "0.90"
        assert rows[0]["location_name"] == "Phố Đội Cấn"


class TestProviders:
    def test_photon_normalizes_features(self):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "geometry": {"coordinates": [105.8286, 21.0346]},
                    "properties": {
                        "name": "Phố Đội Cấn",
                        "street": "Phố Đội Cấn",
                        "city": "Hà Nội",
                        "district": "Ba Đình",
                        "osm_key": "highway",
                        "osm_value": "tertiary",
                    },
                }
            ],
        }
        results = PhotonClient.__new__(PhotonClient)._normalize(payload)
        assert results[0]["lat"] == "21.0346"
        assert results[0]["lon"] == "105.8286"
        assert results[0]["class"] == "highway"
        assert results[0]["label"] == "Phố Đội Cấn"
        assert "Phố Đội Cấn" in results[0]["display_name"]

    def test_photon_rejects_bad_payload(self):
        client = PhotonClient.__new__(PhotonClient)
        assert client._normalize([]) is None
        assert client._normalize({"features": None}) is None

    def test_photon_filters_to_highways(self):
        client = PhotonClient.__new__(PhotonClient)
        assert client._params("q")["osm_tag"] == "highway"

    def test_nominatim_normalizes_list(self):
        client = NominatimClient.__new__(NominatimClient)
        results = client._normalize(
            [{"lat": "21", "lon": "105", "display_name": "Phố Đội Cấn", "name": "Phố Đội Cấn"}]
        )
        assert results[0]["lat"] == "21"
        assert results[0]["label"] == "Phố Đội Cấn"
        assert client._normalize({"features": []}) is None

    def test_default_paths(self):
        cache, report, summary = default_paths("nominatim")
        assert cache.name == "tier1_cache.json"
        assert report.name == "tier1_report.csv"
        assert summary.name == "tier1_summary.md"

        cache, report, summary = default_paths("photon")
        assert cache.name == "tier1_cache_photon.json"
        assert report.name == "tier1_report_photon.csv"
        assert summary.name == "tier1_summary_photon.md"
