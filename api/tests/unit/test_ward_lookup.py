"""
Unit tests for api/utils/ward_lookup.py — resolve_location()

No real database required. Uses MagicMock to control DB responses.
Tests cover all resolution paths: ward hit, alias hit, Haiku fallback,
low-confidence queue, and ward_error.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from api.utils.ward_lookup import (
    ResolvedLocation,
    _ward_lookup,
    resolve_location,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_row(**kwargs):
    """Return a MagicMock that behaves like a SQLAlchemy Row."""
    row = MagicMock()
    row._asdict.return_value = kwargs
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def _conn_returning(row_or_none):
    """Return a mock connection whose execute().fetchone() returns row_or_none."""
    conn = MagicMock()
    result = MagicMock()
    result.fetchone.return_value = row_or_none
    conn.execute.return_value = result
    return conn


# ── _ward_lookup (4-strategy) ─────────────────────────────────────────────────

class TestWardLookup:
    def test_exact_match(self):
        row = _mock_row(ward_name="phuong o cho dua", district="Đống Đa", lat=21.02, lng=105.84)
        conn = _conn_returning(row)
        result = _ward_lookup(conn, "phuong o cho dua")
        assert result["ward_name"] == "phuong o cho dua"

    def test_prefix_prepend(self):
        """'o cho dua' → tries 'phuong o cho dua' and finds it."""
        conn = MagicMock()
        miss = MagicMock(); miss.fetchone.return_value = None
        hit = MagicMock(); hit.fetchone.return_value = _mock_row(
            ward_name="phuong o cho dua", district="Đống Đa", lat=21.02, lng=105.84
        )
        conn.execute.side_effect = [miss, hit]  # exact miss, phuong- hit
        result = _ward_lookup(conn, "o cho dua")
        assert result["ward_name"] == "phuong o cho dua"

    def test_prefix_strip(self):
        """'quan dong da' → strips 'quan ' → tries 'dong da' exact."""
        conn = MagicMock()
        miss = MagicMock(); miss.fetchone.return_value = None
        hit = MagicMock(); hit.fetchone.return_value = _mock_row(
            ward_name="dong da", district="Đống Đa", lat=21.02, lng=105.84
        )
        # exact, phuong-, xa-, thi tran-, then strip 'quan '
        conn.execute.side_effect = [miss, miss, miss, miss, hit]
        result = _ward_lookup(conn, "quan dong da")
        assert result["ward_name"] == "dong da"

    def test_like_fallback(self):
        """'thanh xuan' → LIKE %thanh xuan% → finds 'xa thanh xuan'."""
        conn = MagicMock()
        miss = MagicMock(); miss.fetchone.return_value = None
        hit = MagicMock(); hit.fetchone.return_value = _mock_row(
            ward_name="xa thanh xuan", district="Thanh Xuân", lat=20.99, lng=105.81
        )
        # exact + 3 prefix + 0 strip (no prefix to strip) + LIKE
        conn.execute.side_effect = [miss, miss, miss, miss, hit]
        result = _ward_lookup(conn, "thanh xuan")
        assert result["ward_name"] == "xa thanh xuan"

    def test_no_match_returns_none(self):
        conn = _conn_returning(None)
        result = _ward_lookup(conn, "completely unknown place xyz")
        assert result is None


# ── resolve_location — step 1: ward match ─────────────────────────────────────

class TestResolveLocationWardHit:
    def test_returns_resolved_location_ward_level(self):
        row = _mock_row(ward_name="phuong o cho dua", district="Đống Đa", lat=21.02, lng=105.84)
        conn = _conn_returning(row)
        result = resolve_location(conn, "Ô Chợ Dừa")
        assert isinstance(result, ResolvedLocation)
        assert result.ward_name == "phuong o cho dua"
        assert result.resolution_level == "ward"
        assert result.confidence == 1.0

    def test_empty_raw_name_returns_none(self):
        conn = MagicMock()
        assert resolve_location(conn, "") is None
        assert resolve_location(conn, "   ") is None
        conn.execute.assert_not_called()


# ── resolve_location — step 2: alias hit ─────────────────────────────────────

class TestResolveLocationAliasHit:
    def _conn_ward_miss_alias_hit(self, alias_type="district"):
        conn = MagicMock()

        ward_miss = MagicMock(); ward_miss.fetchone.return_value = None
        alias_row = MagicMock()
        alias_row.ward_name = "phuong bach khoa"
        alias_row.district = "Hai Bà Trưng"
        alias_row.lat = 21.01
        alias_row.lng = 105.87
        alias_row.alias_type = alias_type
        alias_row.confidence = 0.7
        alias_row.source = "static"
        alias_hit = MagicMock(); alias_hit.fetchone.return_value = alias_row

        # ward lookup: exact + 3 prefix + 0 strip (no prefix) + LIKE = 5 misses
        conn.execute.side_effect = [
            ward_miss, ward_miss, ward_miss, ward_miss, ward_miss,
            alias_hit,
        ]
        return conn

    def test_district_alias_resolves(self):
        conn = self._conn_ward_miss_alias_hit("district")
        result = resolve_location(conn, "Hai Bà Trưng")
        assert result is not None
        assert result.ward_name == "phuong bach khoa"
        assert result.resolution_level == "district"
        assert result.confidence == 0.7

    def test_landmark_alias_resolves(self):
        conn = self._conn_ward_miss_alias_hit("landmark")
        result = resolve_location(conn, "Hai Bà Trưng")
        assert result is not None
        assert result.resolution_level == "landmark"


# ── resolve_location — step 3: Haiku disambiguation ──────────────────────────

class TestResolveLocationHaikuFallback:
    def _all_miss_conn(self):
        """Connection that always returns None from execute().fetchone()."""
        conn = MagicMock()
        miss = MagicMock(); miss.fetchone.return_value = None
        conn.execute.return_value = miss
        return conn

    def _mock_client(self, ward_vi, confidence):
        client = MagicMock()
        msg = MagicMock()
        msg.content = [MagicMock()]
        msg.content[0].text = json.dumps({"ward_vi": ward_vi, "confidence": confidence})
        client.messages.create.return_value = msg
        return client

    def test_high_confidence_auto_accepted(self):
        """Haiku returns confidence 0.9 → alias stored as 'llm', location returned."""
        conn = MagicMock()
        miss = MagicMock(); miss.fetchone.return_value = None
        ward_row = _mock_row(ward_name="xa vinh thanh", district="Sóc Sơn", lat=21.25, lng=105.84)
        ward_hit = MagicMock(); ward_hit.fetchone.return_value = ward_row
        insert_ok = MagicMock(); insert_ok.fetchone.return_value = None

        # 5 ward misses (for "vinh thanh") + alias miss + 5 ward hits (Haiku's ward) + insert
        conn.execute.side_effect = (
            [miss] * 5   # step 1: ward miss for raw name
            + [miss]     # step 2: alias miss
            + [ward_hit] # step 3: ward lookup for Haiku's suggestion
            + [insert_ok]# step 3: alias insert
        )
        client = self._mock_client("Xã Vĩnh Thanh", 0.9)

        result = resolve_location(conn, "Vĩnh Thanh", client=client)
        assert result is not None
        assert result.ward_name == "xa vinh thanh"
        assert result.resolution_level == "llm"
        assert result.confidence == 0.9

        # Verify alias was inserted with source='llm'
        insert_call = conn.execute.call_args_list[-1]
        params = insert_call.args[1]
        assert params["source"] == "llm"

    def test_low_confidence_queued_as_pending(self):
        """Haiku returns confidence 0.5 → alias stored as 'pending', None returned."""
        conn = MagicMock()
        miss = MagicMock(); miss.fetchone.return_value = None
        ward_row = _mock_row(ward_name="xa vinh thanh", district="Sóc Sơn", lat=21.25, lng=105.84)
        ward_hit = MagicMock(); ward_hit.fetchone.return_value = ward_row
        insert_ok = MagicMock()

        conn.execute.side_effect = (
            [miss] * 5 + [miss] + [ward_hit] + [insert_ok]
        )
        client = self._mock_client("Xã Vĩnh Thanh", 0.5)

        result = resolve_location(conn, "Vĩnh Thanh", client=client)
        assert result is None  # pending — not emitted

        # Verify alias stored as 'pending'
        insert_call = conn.execute.call_args_list[-1]
        params = insert_call.args[1]
        assert params["source"] == "pending"

    def test_haiku_returns_null_ward(self):
        """Haiku says ward_vi=null (outside Hanoi) → None."""
        conn = self._all_miss_conn()
        client = self._mock_client(None, 0.0)
        result = resolve_location(conn, "Hồ Chí Minh City", client=client)
        assert result is None

    def test_no_client_skips_haiku(self):
        """No Anthropic client → step 3 skipped, returns None."""
        conn = self._all_miss_conn()
        result = resolve_location(conn, "Times City", client=None)
        assert result is None

    def test_haiku_exception_returns_none(self):
        """Haiku API error → gracefully returns None."""
        conn = self._all_miss_conn()
        client = MagicMock()
        client.messages.create.side_effect = Exception("API timeout")
        result = resolve_location(conn, "Times City", client=client)
        assert result is None
