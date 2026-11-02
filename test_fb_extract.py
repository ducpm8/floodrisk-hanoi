"""Diagnostic: run actual _EXTRACT_POSTS_JS on search page, check text for first 6 posts."""
import sys, json, asyncio, pathlib
sys.path.insert(0, '.')
from api.database import engine
from sqlalchemy import text
from playwright.sync_api import sync_playwright
from api.scrapers.facebook import _EXTRACT_POSTS_JS

async def get_cookies():
    async with engine.begin() as conn:
        row = await conn.execute(text(
            "SELECT cookies_encrypted FROM crawler_sessions WHERE source_name='facebook' AND status='active'"
        ))
        r = row.fetchone()
        return json.loads(r[0]) if r else []

cookies = asyncio.run(get_cookies())
print(f"cookies loaded: {len(cookies)}")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context()
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    page.goto(
        'https://www.facebook.com/profile/100069422482221/search?q=ng%E1%BA%ADp',
        wait_until='domcontentloaded', timeout=30000
    )
    page.wait_for_timeout(6000)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(5000)

    posts = page.evaluate(_EXTRACT_POSTS_JS)
    output = [{"i": i, "id": p["id"], "text_preview": (p.get("text") or "")[:400]} for i, p in enumerate(posts[:8])]
    pathlib.Path('fb_extract_result.json').write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(f"posts found: {len(posts)}")
    print("written to fb_extract_result.json")
    browser.close()
