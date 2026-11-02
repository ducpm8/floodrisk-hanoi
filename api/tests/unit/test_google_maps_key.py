"""
Live smoke test: Google Maps API key validity — FIR-38 re-enable.

Reads NEXT_PUBLIC_GOOGLE_MAPS_API_KEY from web/.env.local and validates it
against the Maps JavaScript API loader endpoint (no billing required).
Geocoding API is intentionally NOT used — app uses static ward centroid table.

Skipped automatically when the key is absent (CI without .env.local).
Requires outbound HTTPS to maps.googleapis.com.
"""

import os
import re
from pathlib import Path

import pytest
import requests


# ---------------------------------------------------------------------------
# Key loader
# ---------------------------------------------------------------------------

def _load_maps_key() -> str | None:
    env_file = Path(__file__).parents[3] / "web" / ".env.local"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^NEXT_PUBLIC_GOOGLE_MAPS_API_KEY\s*=\s*(.+)$", line.strip())
            if m:
                return m.group(1).strip()
    return os.getenv("NEXT_PUBLIC_GOOGLE_MAPS_API_KEY")


MAPS_KEY = _load_maps_key()

pytestmark = pytest.mark.skipif(
    not MAPS_KEY,
    reason="NEXT_PUBLIC_GOOGLE_MAPS_API_KEY not set — skipping live API test",
)

# Maps JS API loader — no billing required, validates key acceptance
MAPS_JS_URL = "https://maps.googleapis.com/maps/api/js"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_api_key_format():
    """Key must match Google API key format: AIza followed by 35 alphanumeric chars."""
    assert re.match(r"^AIza[0-9A-Za-z_\-]{35}$", MAPS_KEY), (
        f"Key format invalid: {MAPS_KEY!r}"
    )


def test_maps_js_api_loads():
    """Maps JS API loader must return 200 — confirms key is accepted by Google."""
    resp = requests.get(
        MAPS_JS_URL,
        params={"key": MAPS_KEY, "libraries": "geometry", "callback": "cb"},
        timeout=10,
    )
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"


def test_maps_js_api_not_denied():
    """Response body must not contain AuthFailure — key accepted for Maps JS."""
    resp = requests.get(
        MAPS_JS_URL,
        params={"key": MAPS_KEY, "libraries": "geometry", "callback": "cb"},
        timeout=10,
    )
    body = resp.text
    assert "AuthFailure" not in body, "Key rejected: AuthFailure in Maps JS response"
    assert "InvalidKey" not in body, "Key rejected: InvalidKey in Maps JS response"


def test_maps_js_response_contains_google_maps_js():
    """Response must be actual Maps JS bundle — not an error page."""
    resp = requests.get(
        MAPS_JS_URL,
        params={"key": MAPS_KEY, "libraries": "geometry", "callback": "cb"},
        timeout=10,
    )
    assert "google" in resp.text.lower(), "Response does not look like Maps JS bundle"
