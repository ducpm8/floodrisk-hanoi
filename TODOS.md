# TODOS

Items deferred from planning/review sessions. Each has enough context to pick up cold.

---

## [x] Add docker-compose.yml for local dev DB — DONE in FIR-3 (infra/docker-compose.yml)

---

## [ ] Create low-priority ticket: Manual OFFB curation admin form

**What:** Simple admin form (no auth) for manually entering flood events from OFFB Facebook community posts. Fields: location_name, ward, district, lat, lng, event_date, severity, source_url.

**Why:** Deferred from FIR-4 (2026-04-05). OFFB data is valuable but manual curation is lower priority than the automated scrapers. The automated Facebook crawler (FIR-16) covers the automation path.

**Where to start:** Create Jira ticket under FIR-1 epic, priority Low, link to FIR-4 as related.

**Depends on:** FIR-4 (flood_events table must exist)

---

## [ ] thoatnuochanoi.vn scraper: confirm site format before implementing

**What:** Before writing the official scraper (FIR-4), manually check whether thoatnuochanoi.vn/ungngap returns static HTML or a JSON API. The implementation path differs significantly.

**Why:** FIR-4 ticket says "pending access confirmation from FIR research task." If it's a JSON API, use `requests` + JSON parsing. If static HTML, use `requests` + BeautifulSoup. If JS-rendered, need Playwright.

**Where to start:** Open https://thoatnuochanoi.vn/ungngap in browser, check Network tab for XHR/fetch requests. If JSON API found, document the endpoint in a comment at top of `api/scrapers/official.py`.

**Depends on:** Nothing — manual research step

**What:** Add `infra/docker-compose.yml` with a `postgres:15-postgis` service + env vars for local development.

**Why:** FIR-3's acceptance criteria (run `alembic upgrade head`, verify PostGIS 500m query via `EXPLAIN ANALYZE`) are untestable without a running PostGIS instance. Without this, every developer spends 30-60 minutes manually installing PostgreSQL 15 + PostGIS 3.x — a common source of "works on my machine" breakage.

**Pros:** One-command local setup (`docker compose up -d db`). Consistent environment across all developers. CI can use the same image.

**Cons:** Requires Docker installed locally. Adds one infra file.

**Context:** Identified during FIR-3 eng review (2026-04-05). Should be done before FIR-3 implementation starts so AC can be verified locally.

**Where to start:** `infra/docker-compose.yml` — use `postgis/postgis:15-3.4` image. Expose port 5432. Set `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` from `.env`.

**Depends on:** Nothing — can be done immediately.

---

## [ ] Build admin frontend page for manual ingest (from 2026-04-30 review)

**What:** `web/app/admin/page.tsx` — two sections: (1) paste textarea → `POST /admin/manual-ingest` (JSON), (2) file picker (`<input type="file" accept=".txt,.html,.htm">`) → `POST /admin/manual-ingest/upload` (multipart). Both include optional `source_url` + `event_date` fields. Sends `X-Admin-Token` header.

**Why:** The backend endpoints are curl-only without the UI. Admin needs to paste or upload files from a browser tab during triage.

**Where to start:** `web/app/admin/page.tsx` — two `<form>` sections. No auth UI needed (token hardcoded in env or prompt).

**Depends on:** FIR-39 backend (both ingest endpoints must exist first)

---

## [ ] Implement admin manual ingest endpoints (FIR-39, decisions locked 2026-04-30)

**What:** Two endpoints in `api/routers/admin_ingest.py` for entering flood data without crawling:
- `POST /admin/manual-ingest` — JSON body (paste path)
- `POST /admin/manual-ingest/upload` — multipart/form-data (file upload path)

Both insert into `raw_flood_staging` with `source_type='manual'`; AI extraction worker picks up automatically.

**Why:** Sources that block crawling (paywalled news, Facebook posts). Reuses full FIR-18 pipeline without per-source scraper.

**Files to change:**
1. `api/routers/admin_ingest.py` — new: both endpoints + shared `_insert_staging(db, content, source_url, event_date) -> int` + `_read_file(file) -> str` helpers
2. `api/migrations/versions/013_resolution_provenance.py` — add `'manual'` to `raw_flood_staging.source_type` CHECK (alongside resolution_level changes)
3. `api/tests/unit/test_admin_ingest.py` — 10 cases: 3 paste (202, 403, 422) + 7 upload (202 .txt, 202 .html, 403, 422 size, 422 MIME, 422 empty, CP1258 fallback)
4. `requirements.txt` — add `python-multipart` (FastAPI raises 500 at runtime without it)

**Endpoint specs:**
```
POST /admin/manual-ingest
  {content: str (max 100k), source_url?, event_date?}
  → 202 {staging_id, status: "queued"}

POST /admin/manual-ingest/upload
  multipart: file (.txt/.html, max 100KB), source_url?, event_date?
  → 202 {staging_id, status: "queued"}
```

**Encoding:** `file.read(MAX+1)` → try UTF-8 → fallback CP1258 → raise 422
**Auth:** `require_admin` from `admin_crawl.py` (X-Admin-Token)

**Where to start:** `api/routers/admin_ingest.py` — write `_insert_staging()` first, then both endpoints.

**Depends on:** migration 013 (adds 'manual' to source_type CHECK); implement alongside.

**Depends on:** migration 013 (adds 'manual' to source_type CHECK); implement alongside migration 013 changeset.

---

## [ ] Implement migration 013 + location resolution pipeline fixes (from 2026-04-29 review)

**What:** 6-file change set locking in location resolution architecture decisions D1–D7.

**Why:** (1) `flood_events` missing `resolution_level`/`resolution_confidence` — can't filter low-confidence map pins or show "approximate location" badges to users. (2) Upsert key `(source_url, event_date, ward)` silently fails when `ward=NULL` — re-crawl duplicates unresolved events indefinitely. (3) `geocode_ward()` bypasses `resolve_location()` entirely — compound descriptions like "Vành Đai 3 giao BigC" never reach the alias+Haiku chain and fall back to a district centroid (wrong ward). (4) Extraction prompt strips intersection context — Haiku abbreviates "Vành Đai 3 giao BigC Thăng Long" to "Vành Đai 3", making the compound undisambiguable.

**Files to change:**
1. `api/migrations/versions/013_resolution_provenance.py` — add resolution columns to flood_events, replace UNIQUE constraint with COALESCE index, add 'intersection' to alias_type CHECK
2. `api/models/flood_event.py` — add `resolution_level` + `resolution_confidence` SQLAlchemy columns
3. `api/utils/ward_lookup.py` — remove `conn.commit()` at line 232; add `ORDER BY ward_name` to `_WARD_LIKE`
4. `api/scrapers/ai_extraction_worker.py` — add `resolve_location()` fallback after `geocode_ward()` returns (None, None); update `NEWS_SYSTEM_PROMPT` + `SOCIAL_SYSTEM_PROMPT` with compound location instruction
5. `api/tests/unit/test_flood_events_upsert.py` — new regression test: NULL-ward same-source rows deduplicate after migration fix
6. `api/tests/unit/test_ai_extraction_worker.py` — add eval case: compound location string ("Vành Đai 3 giao BigC") preserved in location_name, not stripped to street name

**Where to start:** Write migration 013 first — DB must be updated before model/pipeline changes land.

**Depends on:** migration 012 (already at head)

---

## [ ] Add source_url index on raw_flood_staging (from 2026-05-01 CEO review)

**What:** `CREATE INDEX idx_raw_flood_staging_source_url ON raw_flood_staging(source_url)` — add as migration 015 (or bundle into next migration).

**Why:** The YouTube ingest duplicate check (`SELECT id FROM raw_flood_staging WHERE source_url = :url LIMIT 1`) does a full table scan. Fine at current scale (hundreds of rows), degrades as staging table grows with YouTube + manual submissions.

**Pros:** O(log n) duplicate check, cheap to add, zero behavior change.

**Cons:** Small write overhead on every staging INSERT. Negligible at current volume.

**Where to start:** New migration file `api/migrations/versions/015_staging_source_url_index.py`. Single `CREATE INDEX` statement, no downtime.

**Effort:** S (human: ~15min / CC: ~1min) **Priority:** P3

**Depends on:** migration 014 (YouTube source type)

---

## [x] Evaluate YouTube Data API v3 captions as stable transcript source — OBSOLETED 2026-05-18

DOM-scraper fragility concern resolved by swapping `youtube-transcript-api` → `yt-dlp` in FIR-41 (eng review 2026-05-18, decisions D1–D13). yt-dlp is a maintained CLI tool, not a DOM scraper. Re-open only if yt-dlp itself becomes unreliable at scale during Phase 2 auto-crawl.

---

## [ ] Background job: auto-expire stale pending SePay orders

**What:** Implement `jobs/verify_pending_payments.py` — mark `purchased_reports` rows as `status='failed'` (or `status='expired'`) when they remain `pending` for more than 24 hours.

**Why:** If a customer abandons the VietQR QR code or transfers the wrong amount, the SePay webhook never arrives and the order stays `pending` forever. Currently there is no cleanup mechanism. The column `idx_purchased_reports_status` index (created in migration 004) already targets `WHERE status='pending' AND created_at < NOW() - interval '60s'`, indicating this job was always intended.

**Pros:** Keeps the admin purchases table clean. Allows the session to re-initiate payment (idempotency check by payment_id won't block it if the original is expired). Enables future retry-payment flow.

**Cons:** Adds a background job dependency. Requires a scheduled trigger (cron or Celery beat). Could falsely expire orders if SePay webhook is severely delayed (rare but possible — add a generous buffer, e.g. 24h).

**Context:** Referenced in migration 004 comment: "Used by Celery job: jobs/verify_pending_payments.py". The job was never built. Identified during SePay migration eng review (2026-05-16). After SePay ships, stale pending orders will accumulate in the admin UI with no cleanup.

**Where to start:** `api/jobs/verify_pending_payments.py`. Query: `SELECT * FROM purchased_reports WHERE status='pending' AND created_at < NOW() - INTERVAL '24 hours'`. Update `status='expired'` (requires adding 'expired' to the CHECK constraint in a follow-up migration, or reuse 'failed'). Trigger via cron endpoint `POST /api/internal/expire-pending` protected by `INTERNAL_CRON_KEY`.

**Depends on:** SePay payment migration (FIR-55 or equivalent) must ship first.

---
