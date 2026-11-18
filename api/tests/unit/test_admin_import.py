"""
Unit tests for admin CSV import (api/routers/admin_import.py).

DB calls are faked via dependency_overrides — no PostGIS required.
"""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.database import get_db
from api.main import app

_HEADER = (
    "location_name,event_date,severity,lat,lng,ward,district,"
    "depth_cm,duration_hours,source,source_url,cause\n"
)

_GOOD_ROW = (
    "Ngã tư Vành Đai 3,2025-09-15,moderate,21.03,105.78,"
    "Mỹ Đình I,Nam Từ Liêm,35,4,community,,\n"
)


class _FakeResult:
    def __init__(self, *, rowcount: int = 1, first: object = None) -> None:
        self.rowcount = rowcount
        self._first = first

    def first(self) -> object:
        return self._first


class _FakeNested:
    async def __aenter__(self) -> "_FakeNested":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeSession:
    """Minimal AsyncSession stand-in for the import endpoints."""

    def __init__(self, existing: set | None = None, fail_names: set | None = None) -> None:
        self.existing = set(existing or ())
        self.fail_names = set(fail_names or ())
        self.inserted: list[dict] = []
        self.committed = 0

    @staticmethod
    def _key(params: dict) -> tuple:
        return (
            params["source_url"],
            params["event_date"],
            params["ward"],
            params["location_name"],
        )

    async def execute(self, stmt: object, params: dict | None = None) -> _FakeResult:
        params = params or {}
        if str(stmt).lstrip().upper().startswith("SELECT"):
            return _FakeResult(first=1 if self._key(params) in self.existing else None)

        if params["location_name"] in self.fail_names:
            raise RuntimeError("db exploded")
        key = self._key(params)
        if key in self.existing:
            return _FakeResult(rowcount=0)
        self.existing.add(key)
        self.inserted.append(params)
        return _FakeResult(rowcount=1)

    def begin_nested(self) -> _FakeNested:
        return _FakeNested()

    async def commit(self) -> None:
        self.committed += 1


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client(fake: FakeSession) -> TestClient:
    async def _get_db():
        yield fake

    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


def _post(client: TestClient, body: str, *, dry_run: bool = False, name: str = "flood.csv"):
    return client.post(
        "/api/admin/flood-import",
        files={"file": (name, body.encode("utf-8"), "text/csv")},
        data={"dry_run": "true" if dry_run else "false"},
    )


# ---------------------------------------------------------------------------
# Template + auth
# ---------------------------------------------------------------------------

def test_template_has_bom_and_required_headers() -> None:
    with TestClient(app) as client:
        r = client.get("/api/admin/flood-import/template")

    assert r.status_code == 200
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")
    first_line = text.splitlines()[0]
    for header in ("location_name", "event_date", "severity", "lat", "lng"):
        assert header in first_line


def test_forbidden_without_valid_token() -> None:
    fake = FakeSession()
    client = _client(fake)
    with patch("api.routers.admin_crawl._ADMIN_TOKEN", "secret"):
        r = _post(client, _HEADER + _GOOD_ROW)
    assert r.status_code == 403
    assert fake.inserted == []


# ---------------------------------------------------------------------------
# Whole-file rejection
# ---------------------------------------------------------------------------

def test_missing_required_header_rejects_whole_file() -> None:
    fake = FakeSession()
    client = _client(fake)
    r = _post(client, "location_name,event_date,severity,lat\nx,2025-01-01,minor,21.0\n")
    assert r.status_code == 422
    assert "lng" in r.json()["detail"]
    assert fake.inserted == []


def test_too_many_rows_rejected() -> None:
    fake = FakeSession()
    client = _client(fake)
    body = _HEADER + "".join(
        f"Điểm {i},2025-01-01,minor,21.03,105.78,,,,,community,,\n" for i in range(2001)
    )
    r = _post(client, body)
    assert r.status_code == 422
    assert "2000" in r.json()["detail"]


def test_non_csv_extension_rejected() -> None:
    fake = FakeSession()
    client = _client(fake)
    r = _post(client, _HEADER + _GOOD_ROW, name="flood.txt")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Row validation + partial import
# ---------------------------------------------------------------------------

def test_partial_import_keeps_good_rows_and_reports_bad_one() -> None:
    fake = FakeSession()
    client = _client(fake)
    body = _HEADER + _GOOD_ROW + (
        "Điểm lỗi,2025-13-40,severe,21.03,105.78,,,,,community,,\n"
    )
    r = _post(client, body)

    assert r.status_code == 200
    data = r.json()
    assert data["inserted"] == 1
    assert data["skipped"] == 1
    assert data["duplicates"] == 0
    assert len(fake.inserted) == 1
    assert any(err["field"] == "event_date" for err in data["errors"])
    statuses = {row["status"] for row in data["rows"]}
    assert statuses == {"inserted", "error"}


@pytest.mark.parametrize(
    "lat,lng,field",
    [
        ("", "105.78", "lat"),
        ("abc", "105.78", "lat"),
        ("21.03", "999", "lng"),
        ("10.0", "105.78", "lat"),  # outside Hanoi bbox
        ("21.03", "100.0", "lng"),
    ],
)
def test_bad_coordinates_are_row_errors(lat: str, lng: str, field: str) -> None:
    fake = FakeSession()
    client = _client(fake)
    body = _HEADER + f"Điểm x,2025-01-01,minor,{lat},{lng},,,,,,,\n"
    r = _post(client, body)

    assert r.status_code == 200
    data = r.json()
    assert data["inserted"] == 0
    assert any(err["field"] == field for err in data["errors"])


def test_bad_severity_is_row_error() -> None:
    fake = FakeSession()
    client = _client(fake)
    body = _HEADER + "Điểm x,2025-01-01,huge,21.03,105.78,,,,,,\n"
    r = _post(client, body)
    data = r.json()
    assert data["inserted"] == 0
    assert any(err["field"] == "severity" for err in data["errors"])


def test_future_event_date_is_row_error() -> None:
    fake = FakeSession()
    client = _client(fake)
    body = _HEADER + "Điểm x,2999-01-01,minor,21.03,105.78,,,,,,\n"
    r = _post(client, body)
    data = r.json()
    assert data["inserted"] == 0
    assert any(err["field"] == "event_date" for err in data["errors"])


def test_blank_source_url_stored_as_manual_sentinel() -> None:
    fake = FakeSession()
    client = _client(fake)
    r = _post(client, _HEADER + _GOOD_ROW)

    assert r.status_code == 200
    assert fake.inserted[0]["source_url"] == "manual://admin-csv"


def test_cp1258_file_decodes() -> None:
    fake = FakeSession()
    client = _client(fake)
    # 0xe0/0xe1 are invalid UTF-8 but valid CP1258 bytes.
    body = (
        _HEADER.encode("ascii")
        + b"Ha Noi flooding "
        + bytes([0xE0, 0xE1])
        + b",2025-09-15,moderate,21.03,105.78,,,,,community,,\n"
    )
    r = client.post(
        "/api/admin/flood-import",
        files={"file": ("flood.csv", body, "text/csv")},
        data={"dry_run": "false"},
    )
    assert r.status_code == 200
    assert r.json()["inserted"] == 1


# ---------------------------------------------------------------------------
# Duplicates + dry run
# ---------------------------------------------------------------------------

def test_duplicate_row_reported_on_second_import() -> None:
    fake = FakeSession()
    client = _client(fake)
    first = _post(client, _HEADER + _GOOD_ROW).json()
    assert first["inserted"] == 1

    second = _post(client, _HEADER + _GOOD_ROW).json()
    assert second["inserted"] == 0
    assert second["duplicates"] == 1


def test_dry_run_writes_nothing_and_detects_duplicates() -> None:
    fake = FakeSession()
    client = _client(fake)

    r = _post(client, _HEADER + _GOOD_ROW, dry_run=True)
    data = r.json()
    assert r.status_code == 200
    assert data["dry_run"] is True
    assert data["inserted"] == 1
    assert fake.inserted == []
    assert fake.committed == 0

    # Pretend the row now exists, dry run must flag it as duplicate.
    fake.existing.add(
        ("manual://admin-csv", date(2025, 9, 15), "my dinh i", "Ngã tư Vành Đai 3")
    )
    data = _post(client, _HEADER + _GOOD_ROW, dry_run=True).json()
    assert data["duplicates"] == 1
    assert data["inserted"] == 0


def test_bind_params_are_asyncpg_safe() -> None:
    """Regression: the fake session hid two real asyncpg failures.

    - event_date must be a date object (a str raises DataError).
    - lat/lng must not be reused for both the NUMERIC column and
      ST_MakePoint (AmbiguousParameterError), so geom gets its own binds.
    """
    from api.routers.admin_import import _INSERT_SQL, _bind_params, _validate_row

    row = {
        "location_name": "X", "event_date": "2025-09-15", "severity": "minor",
        "lat": "21.03", "lng": "105.78",
    }
    values, errors = _validate_row(row, 2)
    assert errors == []
    params = _bind_params(values)

    assert type(params["event_date"]) is date
    assert params["lat_pt"] == 21.03 and params["lng_pt"] == 105.78
    sql = str(_INSERT_SQL)
    assert "ST_MakePoint(:lng_pt, :lat_pt)" in sql


def test_db_error_on_one_row_does_not_abort_others() -> None:
    fake = FakeSession(fail_names={"Điểm sập"})
    client = _client(fake)
    body = _HEADER + _GOOD_ROW + (
        "Điểm sập,2025-01-01,minor,21.03,105.78,,,,,community,,\n"
    )
    r = _post(client, body)

    assert r.status_code == 200
    data = r.json()
    assert data["inserted"] == 1
    assert data["skipped"] == 1
    assert any(err["field"] == "db" for err in data["errors"])
