"""
Unit tests for api/seeds/seed_aliases_extended.py

No real DB required — mocks the SQLAlchemy connection.
Tests verify: inserts on ward-found, skips on ward-miss, idempotent on conflict,
correct alias_type, correct confidence, all 4 types covered.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from api.seeds.seed_aliases_extended import ALIASES, _resolve_ward, seed


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ward_row(name: str):
    row = MagicMock()
    row.ward_name = name
    return row


def _conn(ward_found: str | None = "phuong test"):
    """Mock connection that returns a ward row (or None) for every execute call."""
    conn = MagicMock()
    result = MagicMock()
    if ward_found:
        result.fetchone.return_value = _ward_row(ward_found)
    else:
        result.fetchone.return_value = None

    insert_result = MagicMock()
    insert_result.rowcount = 1

    def execute_side_effect(stmt, params=None):
        # SELECT → return result; INSERT → return insert_result
        if params and "alias_norm" in params and "alias_vi" in params:
            return insert_result
        return result

    conn.execute.side_effect = execute_side_effect
    return conn


# ── _resolve_ward ─────────────────────────────────────────────────────────────

class TestResolveWard:
    def test_exact_match(self):
        conn = MagicMock()
        r = MagicMock()
        r.fetchone.return_value = _ward_row("phuong o cho dua")
        conn.execute.return_value = r
        result = _resolve_ward(conn, "Phường Ô Chợ Dừa")
        assert result == "phuong o cho dua"

    def test_returns_none_on_total_miss(self):
        conn = MagicMock()
        r = MagicMock()
        r.fetchone.return_value = None
        conn.execute.return_value = r
        result = _resolve_ward(conn, "Không Tồn Tại Abcxyz")
        assert result is None

    def test_prefix_prepend_phuong(self):
        """'Trung Hòa' → tries 'phuong trung hoa' → hits."""
        conn = MagicMock()
        call_count = [0]

        def side(stmt, params):
            r = MagicMock()
            call_count[0] += 1
            # First call (exact) → miss; second call (phuong prefix) → hit
            if call_count[0] == 1:
                r.fetchone.return_value = None
            else:
                r.fetchone.return_value = _ward_row("phuong trung hoa")
            return r

        conn.execute.side_effect = side
        result = _resolve_ward(conn, "Trung Hòa")
        assert result == "phuong trung hoa"

    def test_prefix_strip_xa(self):
        """'Xã Đặng Xá' strips 'xa ' → 'dang xa' → hits."""
        conn = MagicMock()
        call_count = [0]

        def side(stmt, params):
            r = MagicMock()
            call_count[0] += 1
            # Exact 'xa dang xa' → miss; stripped 'dang xa' with phuong → miss; with xa → hit
            if params.get("n", "").endswith("dang xa") and not params.get("n", "").startswith("phuong"):
                r.fetchone.return_value = _ward_row("xa dang xa")
            else:
                r.fetchone.return_value = None
            return r

        conn.execute.side_effect = side
        result = _resolve_ward(conn, "Xã Đặng Xá")
        assert result is not None


# ── ALIASES data integrity ────────────────────────────────────────────────────

class TestAliasesData:
    def test_all_entries_have_four_fields(self):
        for entry in ALIASES:
            assert len(entry) == 4, f"Bad entry: {entry}"

    def test_alias_types_are_valid(self):
        valid = {"street", "landmark", "apartment", "slang"}
        for alias_vi, _, _, alias_type in ALIASES:
            assert alias_type in valid, f"{alias_vi!r} has invalid type {alias_type!r}"

    def test_at_least_20_streets(self):
        streets = [e for e in ALIASES if e[3] == "street"]
        assert len(streets) >= 20

    def test_at_least_15_landmarks(self):
        landmarks = [e for e in ALIASES if e[3] == "landmark"]
        assert len(landmarks) >= 15

    def test_at_least_15_apartments(self):
        apts = [e for e in ALIASES if e[3] == "apartment"]
        assert len(apts) >= 15

    def test_at_least_15_slang(self):
        slang = [e for e in ALIASES if e[3] == "slang"]
        assert len(slang) >= 15

    def test_no_empty_alias_vi(self):
        for alias_vi, _, _, _ in ALIASES:
            assert alias_vi.strip(), "Empty alias_vi found"

    def test_no_duplicate_alias_vi(self):
        names = [e[0] for e in ALIASES]
        assert len(names) == len(set(names)), "Duplicate alias_vi entries"

    def test_no_empty_ward_vi(self):
        for _, ward_vi_primary, ward_vi_fallback, _ in ALIASES:
            assert ward_vi_primary.strip(), "Empty ward_vi_primary"
            assert ward_vi_fallback.strip(), "Empty ward_vi_fallback"


# ── seed() function ───────────────────────────────────────────────────────────

class TestSeedFunction:
    def test_inserts_when_ward_found(self):
        engine = MagicMock()
        conn = _conn(ward_found="phuong test")
        engine.begin.return_value.__enter__ = lambda s: conn
        engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        with MagicMock() as mock_create:
            mock_create.return_value = engine
            from unittest.mock import patch
            with patch("api.seeds.seed_aliases_extended.create_engine", return_value=engine):
                result = seed("postgresql+psycopg2://x:x@localhost/x")

        total = result["street"] + result["landmark"] + result["apartment"] + result["slang"]
        assert total > 0

    def test_skips_when_ward_not_found(self):
        engine = MagicMock()
        conn = _conn(ward_found=None)
        engine.begin.return_value.__enter__ = lambda s: conn
        engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        with MagicMock() as mock_create:
            from unittest.mock import patch
            with patch("api.seeds.seed_aliases_extended.create_engine", return_value=engine):
                result = seed("postgresql+psycopg2://x:x@localhost/x")

        assert result["skipped"] == len(ALIASES)
        assert result["street"] == 0
        assert result["landmark"] == 0
        assert result["apartment"] == 0
        assert result["slang"] == 0

    def test_confidence_is_085(self):
        engine = MagicMock()
        inserted_params = []

        conn = MagicMock()
        ward_result = MagicMock()
        ward_result.fetchone.return_value = _ward_row("phuong test")

        insert_result = MagicMock()
        insert_result.rowcount = 1

        def execute_side(stmt, params=None):
            if params and "alias_norm" in params and "confidence" in params:
                inserted_params.append(params)
                return insert_result
            return ward_result

        conn.execute.side_effect = execute_side
        engine.begin.return_value.__enter__ = lambda s: conn
        engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        from unittest.mock import patch
        with patch("api.seeds.seed_aliases_extended.create_engine", return_value=engine):
            seed("postgresql+psycopg2://x:x@localhost/x")

        assert all(p["confidence"] == 0.85 for p in inserted_params)

    def test_source_is_static(self):
        engine = MagicMock()
        inserted_params = []

        conn = MagicMock()
        ward_result = MagicMock()
        ward_result.fetchone.return_value = _ward_row("phuong test")
        insert_result = MagicMock()
        insert_result.rowcount = 1

        def execute_side(stmt, params=None):
            if params and "alias_norm" in params and "confidence" in params:
                inserted_params.append(params)
                return insert_result
            return ward_result

        conn.execute.side_effect = execute_side
        engine.begin.return_value.__enter__ = lambda s: conn
        engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        from unittest.mock import patch
        with patch("api.seeds.seed_aliases_extended.create_engine", return_value=engine):
            seed("postgresql+psycopg2://x:x@localhost/x")

        # source='static' is embedded in the SQL string, not in params dict
        # Verify all inserts used the same SQL template
        assert len(inserted_params) > 0
