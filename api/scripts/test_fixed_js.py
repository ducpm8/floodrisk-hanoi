"""Verify _EXTRACT_POSTS_JS fix — loads cookies from DB, checks text is real content."""
import json, sys
sys.path.insert(0, "c:\\firstproject")

import sqlalchemy
from sqlalchemy import text as sqtext
from playwright.sync_api import sync_playwright
from api.scrapers.facebook import _EXTRACT_POSTS_JS

engine = sqlalchemy.create_engine("postgresql+psycopg2://flood:flood@localhost:5432/flooddb")
with engine.connect() as conn:
    row = conn.execute(sqtext(
        "SELECT cookies_encrypted FROM crawler_sessions WHERE source_name='facebook'"
    )).fetchone()
cookies = json.loads(row[0])

TARGET = "https://www.facebook.com/profile/100069422482221/search/?q=ng%E1%BA%ADp"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ))
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    try:
        page.goto(TARGET, wait_until="networkidle", timeout=30000)
    except Exception:
        page.goto(TARGET, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    seen = set()
    all_posts = []
    for scroll_n in range(3):
        posts = page.evaluate(_EXTRACT_POSTS_JS)
        new = [p for p in posts if p["id"] not in seen]
        seen.update(p["id"] for p in posts)
        all_posts.extend(new)
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(2000)

    browser.close()

out_path = "c:\\firstproject\\fixed_js_out.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(all_posts, f, ensure_ascii=False, indent=2)

print(f"Posts found: {len(all_posts)}")
for p in all_posts:
    text_preview = (p.get("text") or "").replace("\n", "|")[:100]
    nav_garbage = text_preview.startswith("Facebook|Facebook")
    status = "BAD (nav)" if nav_garbage else "OK"
    print(f"  [{status}] id={p['id']}  text={text_preview!r}")
