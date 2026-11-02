"""Debug ancestor walk on authenticated Facebook search page."""
import json
import sys

sys.path.insert(0, "c:\\firstproject")

import sqlalchemy
from sqlalchemy import text as sqtext
from playwright.sync_api import sync_playwright

engine = sqlalchemy.create_engine("postgresql+psycopg2://flood:flood@localhost:5432/flooddb")
with engine.connect() as conn:
    row = conn.execute(sqtext("SELECT cookies_encrypted FROM crawler_sessions WHERE source_name='facebook'")).fetchone()
cookies = json.loads(row[0])

TARGET = "https://www.facebook.com/profile/100069422482221/search/?q=ng%E1%BA%ADp"

DEBUG_JS = r"""
() => {
    // Collect ALL hrefs matching post patterns for diagnosis
    const allHrefs = [];
    document.querySelectorAll("a[href]").forEach(a => {
        const h = a.href || "";
        if (h.includes("facebook.com") && (h.includes("/posts/") || h.includes("fbid=") || h.includes("story_fbid="))) {
            allHrefs.push(h.slice(0, 120));
        }
    });

    const patterns = [
        /\/groups\/[^/]+\/posts\/(\d{10,})/,
        /\/groups\/[^/]+\/permalink\/(\d{10,})/,
        /\/permalink\/(\d{10,})/,
        /[?&]story_fbid=(\d{10,})/,
        /[?&]fbid=(\d{10,})/,
        /\/[^/?]+\/posts\/(\d{10,})/
    ];
    const results = [];
    const seenIds = new Set();

    document.querySelectorAll("a[href]").forEach(a => {
        const href = a.href || "";
        for (const p of patterns) {
            const m = href.match(p);
            if (m && !seenIds.has(m[1]) && results.length < 2) {
                seenIds.add(m[1]);
                let el = a;
                const ancestors = [];
                for (let i = 0; i < 20; i++) {
                    if (!el.parentElement) break;
                    el = el.parentElement;
                    const tc = (el.textContent || "").trim();
                    const it = (el.innerText || "").trim();
                    ancestors.push({
                        "i": i,
                        "tag": el.tagName,
                        "role": el.getAttribute("role") || "",
                        "pagelet": el.getAttribute("data-pagelet") || "",
                        "aria": (el.getAttribute("aria-label") || "").slice(0, 40),
                        "h": el.offsetHeight,
                        "tcLen": tc.length,
                        "itLen": it.length,
                        "itPreview": it.slice(0, 120).replace(/\n/g, "|")
                    });
                    if (el.getAttribute("role") === "article") break;
                }
                results.push({"id": m[1], "href": href.slice(0, 100), "ancestors": ancestors});
                break;
            }
        }
    });
    return {"allHrefs": allHrefs.slice(0, 10), "posts": results};
}
"""

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    try:
        page.goto(TARGET, wait_until="networkidle", timeout=30000)
    except Exception:
        page.goto(TARGET, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)
    data = page.evaluate(DEBUG_JS)
    browser.close()

# Write to file to avoid encoding issues
out_path = "c:\\firstproject\\debug_fb_out.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Written to {out_path}")
print(f"allHrefs count: {len(data['allHrefs'])}")
print(f"posts found: {len(data['posts'])}")
for h in data["allHrefs"][:5]:
    print(f"  href: {h}")
