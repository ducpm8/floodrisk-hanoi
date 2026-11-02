"""
Import Facebook cookies from current Chrome session → crawler_sessions table.

Chrome v127+ uses app-bound encryption (v20) that cannot be decrypted from
outside the Chrome process. This script instead:
  1. Gracefully closes Chrome
  2. Relaunches it with --remote-debugging-port=9222 (same user data dir, so
     existing Facebook login is preserved)
  3. Connects via Playwright CDP (no decryption needed — browser returns
     cookies directly)
  4. Extracts all .facebook.com cookies
  5. Upserts into crawler_sessions table

Run when logged into Facebook in Chrome:
    python api/scripts/import_chrome_facebook_cookies.py

The script will close and reopen Chrome automatically.
"""

import json
import os
import subprocess
import sys
import time

# Must add project root to path before local imports
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent.parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: pip install playwright && python -m playwright install chromium")
    sys.exit(1)

import sqlalchemy
from sqlalchemy import text

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_EXE_X86 = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
CDP_PORT = 9222
DB_URL = os.getenv("DATABASE_SYNC_URL", "postgresql+psycopg2://flood:flood@localhost:5432/flooddb")
SOURCE_NAME = "facebook"


def find_chrome() -> str:
    for path in (CHROME_EXE, CHROME_EXE_X86):
        if os.path.exists(path):
            return path
    raise FileNotFoundError("Chrome not found at default paths")


def kill_chrome():
    print("Closing Chrome gracefully...")
    subprocess.run(
        ["taskkill", "/F", "/IM", "chrome.exe"],
        capture_output=True,
    )
    time.sleep(2)


def launch_chrome_with_debugging(chrome_path: str):
    print(f"Relaunching Chrome with --remote-debugging-port={CDP_PORT} ...")
    subprocess.Popen(
        [
            chrome_path,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={USER_DATA_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "https://www.facebook.com",
        ],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    # Poll until CDP responds (up to 30s)
    import urllib.request, urllib.error
    print("Waiting for Chrome CDP to become available", end="", flush=True)
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=1)
            print(" ready.")
            break
        except Exception:
            print(".", end="", flush=True)
            time.sleep(1)
    else:
        raise RuntimeError(f"Chrome CDP not available after 30s on port {CDP_PORT}")


def extract_facebook_cookies_via_cdp() -> list[dict]:
    cdp_url = f"http://localhost:{CDP_PORT}"
    print(f"Connecting to Chrome via CDP at {cdp_url} ...")

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0]

        # Wait for Facebook to load (give it a moment)
        print("Waiting for Facebook to load...")
        time.sleep(4)

        raw = ctx.cookies("https://www.facebook.com")
        facebook_cookies = [
            c for c in raw
            if "facebook.com" in c.get("domain", "")
        ]
        print(f"Extracted {len(facebook_cookies)} Facebook cookies via CDP")

        # Convert to Playwright DB format (remove unnecessary fields)
        cookies = []
        for c in facebook_cookies:
            cookie = {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": c.get("sameSite", "None"),
            }
            if c.get("expires") and c["expires"] > 0:
                cookie["expires"] = int(c["expires"])
            cookies.append(cookie)

        browser.close()
        return cookies


def upsert_to_db(cookies: list[dict]) -> None:
    engine = sqlalchemy.create_engine(DB_URL)
    with engine.connect() as conn:
        conn.execute(
            text("""
                INSERT INTO crawler_sessions (source_name, cookies_encrypted, last_valid_at, status)
                VALUES (:src, :cookies, NOW(), 'active')
                ON CONFLICT (source_name) DO UPDATE SET
                    cookies_encrypted = EXCLUDED.cookies_encrypted,
                    last_valid_at = NOW(),
                    status = 'active',
                    updated_at = NOW()
            """),
            {"src": SOURCE_NAME, "cookies": json.dumps(cookies)},
        )
        conn.commit()
    print(f"Upserted {len(cookies)} cookies → crawler_sessions (source_name='{SOURCE_NAME}')")


def main():
    auto_yes = "--yes" in sys.argv or "-y" in sys.argv
    chrome_path = find_chrome()
    print(f"Chrome: {chrome_path}")
    print(f"User data dir: {USER_DATA_DIR}")
    print()
    print("WARNING: Chrome will be closed and relaunched with remote debugging.")
    print("Your Facebook login will be preserved (same user data dir).")
    if not auto_yes:
        ans = input("Proceed? [y/N] ").strip().lower()
        if ans != "y":
            print("Aborted.")
            sys.exit(0)

    kill_chrome()
    launch_chrome_with_debugging(chrome_path)

    cookies = extract_facebook_cookies_via_cdp()

    if not cookies:
        print("ERROR: No Facebook cookies found. Are you logged into Facebook?")
        sys.exit(1)

    print("\nCookie names extracted:")
    for c in cookies:
        print(f"  {c['domain']:30s}  {c['name']}")

    upsert_to_db(cookies)
    print("\nDone. Facebook session ready for crawler.")
    print(f"Re-run the live test: python api/scripts/fir45_live_test.py <url>")


if __name__ == "__main__":
    main()
