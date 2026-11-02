#!/usr/bin/env python3
"""
import_chrome_cookies.py

Import cookies exported from EditThisCookie (or similar extension) into
the crawler_sessions table (Playwright format).

Steps:
  1. In Chrome, go to facebook.com (logged in)
  2. Click EditThisCookie extension -> Export (clipboard icon)
  3. Paste into a .json file, e.g. C:\\firstproject\\fb_cookies.json
  4. Run: python scripts/import_chrome_cookies.py --file fb_cookies.json --source facebook

Requires:
    pip install psycopg2-binary
"""

import argparse
import json
import sys
from pathlib import Path


# Map EditThisCookie sameSite values -> Playwright values
_SAMESITE_MAP = {
    "unspecified":    "None",
    "no_restriction": "None",
    "lax":            "Lax",
    "strict":         "Strict",
}

_AUTH_COOKIES = {
    "facebook":  {"c_user", "xs"},
    "instagram": {"sessionid", "ds_user_id"},
}


def _convert(raw: list[dict]) -> list[dict]:
    """Convert EditThisCookie JSON -> Playwright cookie format."""
    result = []
    for c in raw:
        result.append({
            "name":     c["name"],
            "value":    c["value"],
            "domain":   c["domain"],
            "path":     c.get("path", "/"),
            "expires":  c.get("expirationDate", -1),
            "httpOnly": c.get("httpOnly", False),
            "secure":   c.get("secure", False),
            "sameSite": _SAMESITE_MAP.get(
                str(c.get("sameSite", "unspecified")).lower(), "None"
            ),
        })
    return result


def _upsert(source: str, cookies: list[dict], db_url: str) -> None:
    try:
        import psycopg2
    except ImportError:
        print("[ERROR] pip install psycopg2-binary")
        sys.exit(1)
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crawler_sessions
                    (source_name, cookies_encrypted, last_valid_at, status)
                VALUES (%s, %s, NOW(), 'active')
                ON CONFLICT (source_name) DO UPDATE SET
                    cookies_encrypted = EXCLUDED.cookies_encrypted,
                    last_valid_at     = NOW(),
                    status            = 'active',
                    updated_at        = NOW()
                """,
                (source, json.dumps(cookies)),
            )
        conn.commit()
        print(f"[OK]  Saved {len(cookies)} cookies for '{source}' (status=active)")
    finally:
        conn.close()


def _load_db_url(project_root: Path) -> str:
    env_file = project_root / ".env"
    if not env_file.exists():
        raise FileNotFoundError(f".env not found: {env_file}")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip("\"'")
            # Strip SQLAlchemy driver suffix (e.g. postgresql+asyncpg:// -> postgresql://)
            if "+" in url.split("://")[0]:
                url = url.split("+")[0] + "://" + url.split("://")[1]
            return url
    raise ValueError("DATABASE_URL not found in .env")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import EditThisCookie JSON export -> crawler_sessions DB"
    )
    parser.add_argument(
        "--file", "-f", required=True,
        help="Path to the exported cookies JSON file"
    )
    parser.add_argument(
        "--source", choices=["facebook", "instagram"], required=True,
        help="Which source these cookies belong to"
    )
    parser.add_argument("--db-url", default="")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    db_url = args.db_url or _load_db_url(project_root)

    cookie_file = Path(args.file)
    if not cookie_file.is_absolute():
        cookie_file = project_root / cookie_file
    if not cookie_file.exists():
        print(f"[ERROR] File not found: {cookie_file}")
        sys.exit(1)

    raw = json.loads(cookie_file.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        print("[ERROR] Expected a JSON array of cookies.")
        sys.exit(1)

    print(f"[*] Loaded {len(raw)} cookies from {cookie_file.name}")
    cookies = _convert(raw)

    # Auth cookie check
    found = {c["name"] for c in cookies}
    missing = _AUTH_COOKIES.get(args.source, set()) - found
    if missing:
        print(f"[WARN]  Auth cookies missing: {', '.join(missing)}")
        print(f"        Are you logged into {args.source}.com?")
    else:
        present = _AUTH_COOKIES.get(args.source, set()) & found
        print(f"[OK]  Auth cookies present: {', '.join(present)}")

    _upsert(args.source, cookies, db_url)
    print("[DONE] Crawlers will use new session on next run.")


if __name__ == "__main__":
    main()
