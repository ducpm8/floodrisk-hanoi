"""
Probe thoatnuochanoi.vn to determine render strategy before implementing the scraper.

Checks whether the flood data table is:
  A) Static HTML — requests can scrape directly
  B) JavaScript/AJAX rendered — needs Playwright or an XHR API endpoint

Usage:
    python scripts/probe_thoatnuoc.py
    python scripts/probe_thoatnuoc.py --save-html /tmp/thoatnuoc.html

Output:
    STATIC  — table with data rows found in raw HTML. requests-based scraper works.
    JS_AJAX — page loaded but table is empty / missing. Likely AJAX. Check XHR hints.
    API     — embedded JSON or API URL hint found in page source.
    FAILED  — could not fetch the page.

Next steps:
    If JS_AJAX: open the site in Chrome DevTools → Network → XHR/Fetch, reload,
    and look for a JSON API endpoint. If found, switch to direct API calls.
    If no API: add Playwright dependency and rewrite _fetch_page() in official.py.
"""

import argparse
import re
import sys

import requests
from bs4 import BeautifulSoup

URL = "https://thoatnuochanoi.vn/ungngap"
TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (compatible; FloodRiskBot/1.0; +https://floodrisk.vn/about)"

# Known patterns that suggest an XHR/API endpoint is used
API_HINTS = [
    r"fetch\(['\"]([^'\"]*ungngap[^'\"]*)['\"]",
    r"axios\.[a-z]+\(['\"]([^'\"]*ungngap[^'\"]*)['\"]",
    r"XMLHttpRequest",
    r"\.php\?",
    r"api/",
    r"/data/",
]


def probe() -> None:
    parser = argparse.ArgumentParser(description="Probe thoatnuochanoi.vn render strategy")
    parser.add_argument("--save-html", metavar="PATH", help="Save raw HTML to this path")
    args = parser.parse_args()

    print(f"Fetching {URL} ...")
    try:
        resp = requests.get(URL, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        resp.encoding = "utf-8"
        html = resp.text
    except Exception as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)

    print(f"  HTTP {resp.status_code} | {len(html):,} bytes | Content-Type: {resp.headers.get('Content-Type', '?')}")

    if args.save_html:
        with open(args.save_html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  Saved HTML to {args.save_html}")

    soup = BeautifulSoup(html, "html.parser")

    # --- Table check ---
    table = soup.find("table")
    if table:
        rows = [r for r in table.find_all("tr")[1:] if len(r.find_all(["td", "th"])) >= 4]
        print(f"\n  <table> found. Data rows (>=4 cells): {len(rows)}")
        if rows:
            first = rows[0]
            cells = [c.get_text(strip=True)[:40] for c in first.find_all(["td", "th"])]
            print(f"  First row sample: {cells}")
            print("\nRESULT: STATIC — table has data. requests-based scraper should work.")
            sys.exit(0)
        else:
            print("  Table exists but has 0 data rows. Likely JS-rendered (AJAX populates rows).")
    else:
        print("\n  No <table> found in raw HTML.")

    # --- Flood keyword check ---
    body_text = soup.get_text()
    has_flood_keywords = any(kw in body_text.lower() for kw in ("ngập", "thoát nước", "ungngap"))
    print(f"  Flood keywords in page text: {has_flood_keywords}")

    # --- Script / API hint check ---
    scripts = soup.find_all("script")
    script_text = "\n".join(s.string or "" for s in scripts if s.string)
    api_urls_found = []
    for pattern in API_HINTS:
        matches = re.findall(pattern, script_text, re.IGNORECASE)
        api_urls_found.extend(matches)

    if api_urls_found:
        print(f"\n  API/XHR hints found in scripts:")
        for u in set(api_urls_found):
            print(f"    {u}")
        print("\nRESULT: API — check these endpoints. If they return JSON, use direct API calls.")
        sys.exit(0)

    # --- iframe / src hint ---
    iframes = soup.find_all("iframe")
    for iframe in iframes:
        src = iframe.get("src", "")
        if "ngap" in src.lower() or "flood" in src.lower():
            print(f"  Iframe with flood-related src: {src}")

    # --- Embedded JSON check ---
    json_pattern = re.search(r'\{[^{}]*"(ward|district|ngap|flood)[^{}]*\}', script_text, re.IGNORECASE)
    if json_pattern:
        print(f"  Embedded JSON-like data found near: {json_pattern.group()[:100]}")
        print("\nRESULT: API — embedded JSON detected. Parse from script tag.")
        sys.exit(0)

    print(
        "\nRESULT: JS_AJAX — page loaded but no table data and no obvious API endpoint found.\n"
        "\nNext steps:\n"
        "  1. Open https://thoatnuochanoi.vn/ungngap in Chrome\n"
        "  2. DevTools → Network → XHR/Fetch tab → reload page\n"
        "  3. Look for requests returning JSON with flood data\n"
        "  4. If found: add endpoint URL to official.py and fetch directly\n"
        "  5. If not found: add playwright dependency, rewrite _fetch_page() to use headless Chrome\n"
        "\nSee FIR-26 for tracking."
    )
    if args.save_html:
        print(f"\nRaw HTML saved to {args.save_html} — inspect for clues.")


if __name__ == "__main__":
    probe()
