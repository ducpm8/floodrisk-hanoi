"""
One-shot structural analysis of the Facebook profile search page.
Opens browser with stored cookies, fully loads page, dumps:
  - viewport screenshot
  - full HTML
  - innerText of feed children
  - all script tags content (for embedded JSON)
  - all <a href> + their text/aria-label
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from api.config import settings
import sqlalchemy as sa
from sqlalchemy import text

URL = "https://www.facebook.com/profile/100069422482221/search?q=ng%E1%BA%ADp"
OUT = Path("c:/firstproject/scripts/_fb_analysis")
OUT.mkdir(exist_ok=True)


def load_cookies() -> list[dict]:
    engine = sa.create_engine(settings.database_sync_url)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT cookies_encrypted FROM crawler_sessions "
            "WHERE source_name = 'facebook' AND status = 'active'"
        )).fetchone()
        if not row or not row[0]:
            raise RuntimeError("no active facebook session")
        raw = row[0]
        return json.loads(raw) if isinstance(raw, str) else raw


def main():
    cookies = load_cookies()
    print(f"loaded {len(cookies)} cookies")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900},
        )
        ctx.add_cookies(cookies)
        page = ctx.new_page()

        print(f"goto {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
        print("waiting 8s for full settle...")
        page.wait_for_timeout(8000)

        # 1. Screenshot
        page.screenshot(path=str(OUT / "01_viewport.png"), full_page=False)
        print(f"  screenshot: {OUT/'01_viewport.png'}")

        # 2. Full HTML
        html = page.content()
        (OUT / "02_full.html").write_text(html, encoding="utf-8")
        print(f"  full HTML: {len(html)} bytes")

        # 3. Feed structure analysis
        feed_info = page.evaluate("""
            () => {
                const out = {
                    feeds: document.querySelectorAll('[role="feed"]').length,
                    articles: document.querySelectorAll('[role="article"]').length,
                    feedChildren: [],
                    sampleArticle: null,
                };
                const feed = document.querySelector('[role="feed"]');
                if (feed) {
                    const children = Array.from(feed.children);
                    out.feedChildrenCount = children.length;
                    children.slice(0, 5).forEach((c, i) => {
                        out.feedChildren.push({
                            idx: i,
                            tag: c.tagName,
                            cls: (c.className || '').toString().slice(0, 200),
                            innerTextLen: (c.innerText || '').length,
                            innerTextSample: (c.innerText || '').slice(0, 500),
                            outerHTMLSample: c.outerHTML.slice(0, 1000),
                            linkCount: c.querySelectorAll('a[href]').length,
                            links: Array.from(c.querySelectorAll('a[href]')).slice(0, 10).map(a => ({
                                href: a.href,
                                text: (a.innerText || '').slice(0, 80),
                                aria: a.getAttribute('aria-label') || '',
                            })),
                        });
                    });
                }
                return out;
            }
        """)
        (OUT / "03_feed_info.json").write_text(json.dumps(feed_info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  feeds={feed_info['feeds']} articles={feed_info['articles']} feedChildren={feed_info.get('feedChildrenCount')}")

        # 4. Look for embedded JSON with post content
        scripts = page.evaluate("""
            () => {
                const out = [];
                document.querySelectorAll('script').forEach((s, i) => {
                    const txt = s.textContent || '';
                    // Look for scripts that mention flood keywords or post content
                    if (txt.includes('ngập') || txt.includes('story_fbid') || txt.includes('message') || txt.length > 5000) {
                        out.push({
                            idx: i,
                            type: s.type || '',
                            length: txt.length,
                            sample: txt.slice(0, 300),
                            hasNgap: txt.includes('ngập'),
                            hasMessage: txt.includes('"message"'),
                            hasText: txt.includes('"text"'),
                        });
                    }
                });
                return out;
            }
        """)
        (OUT / "04_scripts.json").write_text(json.dumps(scripts, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  interesting scripts: {len(scripts)} (with ngập: {sum(1 for s in scripts if s['hasNgap'])})")

        # 5. Extract ALL text containing flood keywords from anywhere on page
        flood_hits = page.evaluate("""
            () => {
                const hits = [];
                const keywords = ['ngập', 'lụt', 'mưa', 'nước'];
                // Search all text nodes
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const txt = node.textContent || '';
                    if (txt.length < 10) continue;
                    if (keywords.some(k => txt.toLowerCase().includes(k))) {
                        // Walk up to find a meaningful container
                        let p = node.parentElement;
                        let containerHref = '';
                        for (let i = 0; i < 15 && p; i++) {
                            const link = p.querySelector ? p.querySelector('a[href*="set=pcb"], a[href*="/posts/"], a[href*="/videos/pcb."]') : null;
                            if (link) { containerHref = link.href; break; }
                            p = p.parentElement;
                        }
                        hits.push({ text: txt.slice(0, 300), parentTag: node.parentElement ? node.parentElement.tagName : '', containerHref });
                    }
                }
                return hits;
            }
        """)
        (OUT / "05_flood_text_hits.json").write_text(json.dumps(flood_hits, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  flood text hits in DOM: {len(flood_hits)}")
        if flood_hits:
            for h in flood_hits[:5]:
                print(f"    - {h['text'][:120]!r}  href={h['containerHref'][:80]}")

        # 6. Try mobile rewrite — sometimes Facebook serves cleaner DOM on mobile UA
        print("\n--- now navigating to mbasic for comparison ---")
        mbasic_url = "https://mbasic.facebook.com/profile.php?id=100069422482221&v=timeline&q=ng%E1%BA%ADp"
        try:
            page.goto(mbasic_url, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(3000)
            mbasic_html = page.content()
            (OUT / "06_mbasic.html").write_text(mbasic_html, encoding="utf-8")
            print(f"  mbasic HTML: {len(mbasic_html)} bytes")
            mbasic_text = page.locator("body").inner_text(timeout=3000)
            (OUT / "06_mbasic_text.txt").write_text(mbasic_text, encoding="utf-8")
            print(f"  mbasic visible text: {len(mbasic_text)} chars")
        except Exception as e:
            print(f"  mbasic failed: {e}")

        print("\ndone. browser stays open 30s for inspection.")
        page.wait_for_timeout(30_000)
        browser.close()


if __name__ == "__main__":
    main()
