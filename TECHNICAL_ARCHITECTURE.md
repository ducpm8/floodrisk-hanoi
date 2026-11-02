# Technical Architecture — Hanoi Flood Risk & Price History App

> Locked: 2026-04-05 | Last updated: 2026-05-09 | Status: APPROVED | Covers: FIR-1 through FIR-50

---

## 1. Stack Overview

| Layer | Technology | Reason |
|---|---|---|
| Frontend | Next.js 14 (App Router, TypeScript) | Responsive web MVP; React Native Phase 2 |
| Backend | FastAPI (Python 3.11+) | pandas/geopandas ecosystem; Vietnamese NLP libraries |
| Database | PostgreSQL 15 + PostGIS | Geospatial queries (ST_DWithin); relational payment data |
| ORM | SQLAlchemy 2.x (async) | Parameterized queries by default — no raw SQL |
| Maps | Text proximity list (default) + Google Maps SDK (optional) | No API key needed for MVP; admin toggles to Google Maps via Redis. FIR-38. |
| Fuzzy search | Fuse.js (client-side) | No server round-trip for ward name suggestions |
| Payment | MoMo + VNPay | Start both merchant apps in parallel; use whichever approves first |
| Background jobs | Celery + Redis | Payment verification probe (60s); crawl scheduler (FIR-16) |
| LLM extraction | Claude API (claude-haiku-4-5-20251001) | Unstructured flood text to FloodEvent (FIR-18) |
| Hosting | VPS Vietnam (Vultr Singapore) | Latency + Vietnamese data residency |
| CI/CD | GitHub Actions | Test → build → deploy |

---

## 2. Monorepo Folder Structure

```
firstproject/
├── api/                            # FastAPI backend
│   ├── main.py                     # App factory, middleware, routers
│   ├── config.py                   # Settings (pydantic-settings, env vars)
│   ├── database.py                 # SQLAlchemy engine + session
│   ├── models/
│   │   ├── flood_event.py          # + is_seed (migration 011)
│   │   ├── price_record.py         # + is_seed (migration 011)
│   │   ├── purchased_report.py
│   │   ├── ward_centroid.py
│   │   ├── crawl_source_config.py  # FIR-16
│   │   ├── crawl_run.py            # FIR-16
│   │   └── location_alias.py       # FIR-37: district/landmark aliases
│   ├── routers/
│   │   ├── search.py               # GET /api/search
│   │   ├── flood.py                # GET /api/flood/{id}
│   │   ├── flood_zones.py          # GET /api/flood-zones (map circles)
│   │   ├── payment.py              # POST /api/payment/*, webhooks
│   │   ├── price.py                # GET /api/price
│   │   ├── admin_crawl.py          # FIR-16: crawl source management + trigger
│   │   ├── admin_report.py         # FIR-36: purchased report admin view + CSV export
│   │   └── admin_settings.py       # FIR-38: GET/PATCH /api/admin/settings (map_mode)
│   ├── utils/
│   │   ├── text.py                 # normalize_ward() — diacritic stripping
│   │   └── ward_lookup.py          # FIR-37: resolve_location() with alias fallback
│   ├── cache.py                    # Redis singleton; graceful degradation
│   ├── migrations/
│   │   ├── env.py
│   │   ├── alembic.ini
│   │   └── versions/
│   │       ├── 001_initial_schema.py
│   │       ├── 002_ward_centroids.py
│   │       ├── 003_spatial_index.py
│   │       ├── 004_purchased_reports.py
│   │       ├── 005_crawl_sources.py        # FIR-16
│   │       ├── 006_raw_flood_staging.py
│   │       ├── 007_crawler_sessions.py
│   │       ├── 008_search_optimization.py  # FIR-19
│   │       ├── 009_flood_source_price.py
│   │       ├── 010_flood_events_upsert_key.py
│   │       ├── 011_is_seed_flag.py         # is_seed on flood_events + price_records
│   │       ├── 012_location_aliases.py     # FIR-37
        ├── 013_resolution_provenance.py # FIR-38: resolution columns + COALESCE upsert fix
        ├── 014_crawl_runs.py           # FIR-35: source_url + stats columns + crawl_run_id FK
        └── 015_cause_field.py          # FIR-40: cause TEXT on flood_events
│   ├── seeds/
│   │   ├── seed_wards.py
│   │   ├── seed_dev.py             # 10 flood events + 5 price records (is_seed=TRUE)
│   │   └── seed_manual_test.py     # 90 events + 360 price records (is_seed=TRUE)
│   └── tests/
│       ├── unit/                   # all DB/Redis mocked; ~233 tests
│       └── conftest.py
│
├── web/                            # Next.js frontend
│   ├── app/
│   │   ├── page.tsx                # Home: search + conditional map/proximity list
│   │   ├── admin/
│   │   │   ├── crawl/page.tsx      # FIR-16: crawl management + FIR-38 map mode toggle
│   │   │   └── purchases/page.tsx  # FIR-36: purchased report admin table + CSV export
│   │   └── layout.tsx
│   ├── components/
│   │   ├── map/
│   │   │   └── FloodMap.tsx        # Google Maps circles (active when map_mode=google)
│   │   └── search/
│   │       ├── SearchSection.tsx
│   │       ├── ResultSheet.tsx     # Bottom sheet with flood/price data
│   │       └── ProximityList.tsx   # FIR-38: top-5 nearby zones, Haversine, default mode
│   └── lib/
│       ├── search.ts
│       ├── admin-crawl.ts          # FIR-16 admin API calls
│       ├── admin-purchases.ts      # FIR-36 admin API calls
│       ├── admin-settings.ts       # FIR-38: fetchSettings(), patchMapMode()
│       └── geo.ts                  # FIR-38: haversineMetres(), formatDistance()
│
├── scrapers/
│   ├── flood/
│   │   ├── thoatnuoc_scraper.py
│   │   ├── news_scraper.py         # VnExpress / Tuổi Trẻ / Dân Trí
│   │   └── llm_extractor.py        # FIR-18: text → FloodEvent via Claude API
│   └── price/
│       └── batdongsan_scraper.py   # pending ToS confirmation
│
├── jobs/                           # Celery tasks
│   ├── verify_pending_payments.py  # Every 60s
│   ├── crawl_flood_data.py         # FIR-16: daily Facebook/OFFB + scheduled site
│   └── update_search_index.py      # FIR-19
│
├── infra/
│   ├── docker-compose.yml          # Local: postgres+postgis, redis, api, web
│   ├── Dockerfile.api
│   ├── Dockerfile.web
│   └── nginx.conf                  # /api → FastAPI, / → Next.js
│
├── CLAUDE.md
└── TECHNICAL_ARCHITECTURE.md
```

---

## 3. Database Schema

No Prisma — Python backend uses SQLAlchemy 2.x async ORM.

### WardCentroid
```python
class WardCentroid(Base):
    __tablename__ = "ward_centroids"
    ward_name    = Column(String(100), primary_key=True)  # normalized, no diacritics
    ward_name_vi = Column(String(100), nullable=False)    # "Đống Đa"
    district     = Column(String(100), nullable=False)
    lat          = Column(Numeric(9, 6), nullable=False)
    lng          = Column(Numeric(9, 6), nullable=False)
    geom         = Column(Geography("POINT", srid=4326))
```

### FloodEvent
```python
class FloodEvent(Base):
    __tablename__ = "flood_events"
    id             = Column(UUID, primary_key=True, default=uuid4)
    location_name  = Column(String(200))
    ward           = Column(String(100))           # normalized
    district       = Column(String(100))
    lat            = Column(Numeric(9, 6))
    lng            = Column(Numeric(9, 6))
    geom           = Column(Geography("POINT", srid=4326))
    event_date     = Column(Date, nullable=False)
    severity       = Column(Enum("minor", "moderate", "severe"))
    depth_cm       = Column(Integer, nullable=True)
    duration_hours = Column(Integer, nullable=True)
    source         = Column(Enum("official", "news", "community"))
    source_url     = Column(String(500), nullable=True)
    cause          = Column(Text, nullable=True)   # weather trigger extracted by Haiku; NULL for governance source. migration 015
    is_seed        = Column(Boolean, nullable=False, default=False)  # migration 011
    crawl_run_id   = Column(UUID, ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True)  # migration 014
    created_at     = Column(DateTime, default=func.now())

# Migration 003:
# CREATE INDEX idx_flood_events_geom ON flood_events USING GIST(geom);
# Seed cleanup before go-live: DELETE FROM flood_events WHERE is_seed = TRUE
```

### PriceRecord
```python
class PriceRecord(Base):
    __tablename__ = "price_records"
    id               = Column(UUID, primary_key=True, default=uuid4)
    ward             = Column(String(100))          # normalized
    district         = Column(String(100))
    property_type    = Column(Enum("apartment", "house", "land"))
    price_per_m2_vnd = Column(BigInteger)
    recorded_month   = Column(Date)                 # month-year granularity
    last_updated     = Column(DateTime)
    source           = Column(String(200))
    source_url       = Column(String(500), nullable=True)
    data_caveat      = Column(String(500))          # "listing price, not confirmed sale"
    created_at       = Column(DateTime, default=func.now())
```

### PurchasedReport
```python
class PurchasedReport(Base):
    __tablename__ = "purchased_reports"
    id                   = Column(UUID, primary_key=True, default=uuid4)
    session_id           = Column(String(36), nullable=False, index=True)
    email                = Column(String(200), nullable=False)  # collected at checkout
    location_query       = Column(String(200))
    ward_normalized      = Column(String(100))
    location_lat         = Column(Numeric(9, 6))
    location_lng         = Column(Numeric(9, 6))
    payment_gateway      = Column(Enum("momo", "vnpay"))
    payment_id           = Column(String(200))
    amount_vnd           = Column(Integer, default=35000)
    status               = Column(Enum("pending", "paid", "failed"), default="pending")
    failure_reason       = Column(String(500), nullable=True)
    paid_at              = Column(DateTime, nullable=True)
    report_data_snapshot = Column(JSON)             # frozen at purchase time
    created_at           = Column(DateTime, default=func.now())
```

---

## 4. API Design

All responses: `Content-Type: application/json`
All errors: `{"error": "ERROR_CODE", "message": "human readable"}`

```
GET  /api/search?q={ward_name}
     → { ward, ward_vi, district, lat, lng, flood_zone_count, teaser_events }
     → 404 WARD_NOT_FOUND

GET  /api/flood-zones
     → { zones: [{id, lat, lng, severity, ward, ward_vi, district, radius_m, event_date, cause: str|null}], total }

GET  /api/report/{ward_normalized}
     Header: X-Session-ID: {uuid}
     → { flood_events, price_records, generated_at }
     → 402 PAYMENT_REQUIRED
     → 404 WARD_NOT_FOUND

POST /api/payment/initiate
     Body: { session_id, ward_normalized, email, gateway: "momo"|"vnpay" }
     → { payment_url, order_id }

GET  /api/payment/status/{session_id}
     → { status: "pending"|"paid"|"failed", report_url? }

POST /api/payment/webhook/momo
     → 200 always (log failures internally)

POST /api/payment/webhook/vnpay
     → 200 always

# Admin (X-Admin-Token header required when ADMIN_TOKEN env var is set)
GET  /api/admin/settings                          — public; homepage reads map_mode on load
     → { map_mode: "text"|"google" }
PATCH /api/admin/settings                         — admin-only; stored in Redis (no TTL)
     Body: { map_mode: "text"|"google" }
     → { map_mode }  |  503 if Redis offline

GET  /api/admin/purchases?status=&q=&page=&page_size=
     → { items: [...], total, page, page_size }
GET  /api/admin/purchases/export                  — CSV download
GET  /api/admin/crawl-sources
POST /api/admin/crawl-sources
PATCH /api/admin/crawl-sources/{name}
DELETE /api/admin/crawl-sources/{name}
POST /api/admin/crawl-trigger/{name}              — 202 Accepted | 409 already running
```

---

## 5. Authentication & Session Flow

Guest checkout only. No user accounts in v1.

```
1. App load → check localStorage for session_id
2. If none → generate UUID v4 → store in localStorage
3. session_id sent as X-Session-ID header on every request
4. POST /payment/initiate → creates PurchasedReport with session_id + email
5. Payment confirmed → PurchasedReport.status = "paid"
6. Re-open app → GET /payment/status/{session_id} → if paid, show cached report
7. New device / cleared storage → new session_id → pay again (intentional)
```

Add persistent accounts in v2 if repeat-purchase rate exceeds 20%.

---

## 6. Payment Recovery Flow

```
Happy path:  webhook → HMAC validate → mark paid → return 200

Fallback:
  Frontend polls GET /payment/status every 3s × 3 attempts (9s total)
  After 3 failures → show "Payment can't be verified — report will be emailed once confirmed"
  End payment session on frontend

Backend:
  Celery task every 60s → query MoMo/VNPay order-status API for:
    PurchasedReport WHERE status = 'pending' AND created_at < NOW() - 60s
  On confirmed → mark paid → send email with report snapshot + 7-day link
```

Webhook signature validation (both gateways):
```python
# api/routers/payment.py
def verify_momo_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

---

## 7. Geocoding

Static ward centroid table. 579 Hanoi wards seeded from OSM Overpass (one-time).

```python
# api/utils/text.py
import unicodedata, re

def normalize_ward(name: str) -> str:
    """'Đống Đa' → 'dong da'"""
    nfkd = unicodedata.normalize("NFKD", name.lower())
    ascii_str = nfkd.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", ascii_str).strip()
```

Applied at: seed time (`ward_centroids.ward_name`) AND before every DB lookup in `routers/search.py`.

PostGIS radius query:
```python
ward = db.query(WardCentroid).filter_by(ward_name=normalize_ward(q)).first()
floods = db.query(FloodEvent).filter(
    ST_DWithin(FloodEvent.geom, ward.geom, 500)  # 500m radius
).all()
```

---

## 8. State Management (Frontend)

No Redux or Zustand for v1.

| State | Location |
|---|---|
| session_id | `localStorage` via `lib/session.ts` |
| search query + suggestions | `useState` in `SearchBar` |
| map markers | `useState` in `FloodMap` |
| server data (flood/price) | SWR |
| payment polling | `hooks/usePaymentStatus.ts` |

---

## 9. Facebook Crawler — Manual Admin Crawl (FIR-45 / FIR-50)

### Endpoint
`POST /api/admin/facebook-crawl` — streams progress as SSE (`text/event-stream`).

### Parameters
| Field | Default | Constraint | Notes |
|-------|---------|------------|-------|
| `facebook_url` | required | must contain `facebook.com` or `fb.com` | group or profile/search URL |
| `max_posts` | 10 | 1–100 | max new posts to stage per run |
| `max_comments_per_post` | 10 | 0–100 | 0 disables comment extraction |
| `cdp_url` | null | optional | CDP endpoint of existing Chrome (e.g. `http://localhost:9222`) |

### Flow
1. Load Facebook session cookies from `crawler_sessions` table (or connect via CDP).
2. Launch Playwright (non-headless) → navigate with `wait_until="domcontentloaded"`.
3. Scroll loop: extract posts via `page.evaluate(_EXTRACT_POSTS_JS)`.
4. Per post: keyword pre-filter → dedup check → stage → comment extraction.
5. Per staged post: open permalink in new tab → click "Xem thêm bình luận" up to `max_comments_per_post` times → extract comments → keyword filter → dedup → stage.
6. After all staging: call `run_worker(batch_size=staged_posts + staged_comments)` inline.
7. Emit `extraction_start` / `extraction_done` / `extraction_error` SSE events.

### Keyword Pre-filter (`api/scrapers/base.py`)
`FLOOD_KEYWORDS` frozenset + `contains_flood_keywords(text)` — shared by `facebook.py` and `news.py`.
Content that matches no keyword is skipped **before** staging, preventing unnecessary Haiku API calls.

Current keywords: `ngập`, `lụt`, `ngập lụt`, `ngập úng`, `ngập nước`, `nước ngập`, `thoát nước`, `mực nước`, `mưa lớn`, `nước dâng`, `cm`, `mét nước`.

### Dedup (external_id namespace)
| Content type | external_id format | Example |
|---|---|---|
| Post | raw Facebook post ID (numeric or pfbid) | `1081277157529690` |
| Comment | `comment:{comment_id}` | `comment:987654321` |

### Post ID extraction (`_EXTRACT_POSTS_JS`)
Patterns tried in order: `/groups/*/posts/{id}`, `/groups/*/permalink/{id}`, `/permalink/{id}`, `?story_fbid=`, `?set=pcb.`, `?fbid=`, `/{user}/posts/{pfbid...}` (modern obfuscated IDs), `/{user}/posts/{numeric}`, `/share/p/{id}`.

### Depth / Severity convention
`depth_cm` is only set when an explicit measurement appears in text (e.g. "ngập 50cm").  
Qualitative terms ("ngập sâu", "ngập rất sâu") → `depth_cm=null`, `severity="moderate"/"severe"`.  
Haiku prompt instruction: `"Nếu không đề cập độ sâu rõ ràng, để depth_cm = null"`.

---

## 9a. LLM Extraction Pipeline (FIR-18)

```python
# api/scrapers/ai_extraction_worker.py  (actual path)
import anthropic

client = anthropic.Anthropic()

def extract_flood_event(raw_text: str) -> dict | None:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": f"""Extract flood event from this Vietnamese text.
Return JSON only: {{location_name, ward, district, event_date (YYYY-MM-DD),
severity (minor/moderate/severe), depth_cm (int or null), source_url}}.
Return null if not a flood event.

Text: {raw_text}"""
        }]
    )
    # parse + validate schema → return dict or None
```

Anti-detection (FIR-17): randomized 2–8s delays, rotating user agents, Playwright for JS-rendered pages.

---

## 10. Error Handling

### Backend
```python
# Standard error shape
{"error": "WARD_NOT_FOUND",    "message": "Ward not found. Try a ward name (e.g., 'Dong Da')"}
{"error": "PAYMENT_REQUIRED",  "message": "Purchase a report to view full data"}
{"error": "INVALID_SIGNATURE", "message": "Webhook signature validation failed"}

@app.exception_handler(Exception)
async def unhandled(request, exc):
    logger.error(f"Unhandled: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"error": "INTERNAL_ERROR", "message": "..."})
```

Payment webhooks always return 200 — never surface errors to the gateway.

### Frontend
- Ward not found → inline message below search bar
- Payment failure → inline message inside PaymentModal, stay on page
- API errors → `react-hot-toast`, top-right, 4s auto-dismiss
- Payment timeout → "report will be emailed" inline message

---

## 11. Testing Strategy

```
Unit (pytest)
├── test_text.py              — normalize_ward(): 15+ diacritic variants
└── test_payment_signatures.py — HMAC: valid, tampered body, wrong secret

Integration (pytest + real PostgreSQL test DB)
├── test_search.py    — ward found, not found, diacritic variants, malformed input
├── test_payment.py   — initiate (MoMo mock, VNPay mock), status (pending/paid/failed)
└── test_internal.py  — verify-pending confirms payment, sends email (mocked)

E2E (Playwright)
├── search → map loads → flood markers visible (free teaser)
├── search → pay → full report delivered
└── payment timeout → email fallback message shown
```

Email client: mocked via `unittest.mock` in all integration tests. No real SMTP in CI.

---

## 12. Coding Conventions

### Python
- Python 3.11+, type hints on all function signatures
- `black`, `isort`, `flake8`
- SQLAlchemy ORM only — no raw SQL, no f-string queries
- `normalize_ward()` before every ward name DB lookup
- Config via `pydantic-settings` in `config.py`
- No `print()` in production — use `logging`

### TypeScript
- Strict mode (`"strict": true`)
- ESLint + Prettier
- Components: PascalCase files in `components/`
- Utilities: camelCase files in `lib/`
- No `any` — use `unknown` + type guards
- All API calls through `lib/api.ts` — no direct `fetch` in components
- Session ID always via `lib/session.ts`

### Git
- Branch: `{type}/{jira-key}-short-description` e.g. `feat/FIR-6-fastapi-setup`
- Commit: `FIR-XX: description`
- PRs require passing CI before merge

---

## 13. Build Order

```
Phase 0 — Blockers (no code until resolved)
  FIR-2, FIR-21–25: validate data sources + start MoMo/VNPay merchant onboarding

Phase 1 — Lock schema (blocks everything)
  FIR-3: PostgreSQL + PostGIS + migrations + ward centroid seed

Phase 2 — Core API (sequential)
  FIR-6 → FIR-7 → FIR-8

Phase 3 — Parallel lanes
  Frontend: FIR-10, FIR-11, FIR-12, FIR-13, FIR-14
  Scrapers: FIR-4, FIR-5
  Payment:  FIR-9

Phase 4 — AI + Optimization (parallel)
  FIR-16, FIR-17, FIR-18, FIR-19

Phase 5 — Ship
  FIR-15: VPS deployment + CI/CD
```

---

## 14. Failure Modes

| Failure | Mitigation |
|---|---|
| MoMo webhook never arrives | Frontend polls 3×/3s; backend probes every 60s; email fallback |
| Tampered webhook | HMAC-SHA256 before any DB write; always return 200 |
| Ward not found | 404 + inline "try a ward name" message |
| thoatnuochanoi.vn scraping blocked | Fall back to news-only pipeline |
| batdongsan.com.vn scraping blocked | Switch to alonhadat.com.vn or propzy.vn |
| PostGIS slow | GIST index on flood_events.geom; 500m radius cap |
| LLM hallucination | Validate extracted JSON against schema; discard invalid records |

---

## 15. Environment Variables

```bash
# api/.env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/flooddb
DATABASE_SYNC_URL=postgresql+psycopg2://user:pass@localhost/flooddb  # seeds only
REDIS_URL=redis://localhost:6379
ADMIN_TOKEN=                         # if unset, all admin endpoints open (dev only)
MOMO_PARTNER_CODE=
MOMO_ACCESS_KEY=
MOMO_SECRET_KEY=
VNPAY_TMN_CODE=
VNPAY_HASH_SECRET=
ANTHROPIC_API_KEY=
EMAIL_SMTP_HOST=
EMAIL_SMTP_USER=
EMAIL_SMTP_PASS=

# web/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_ADMIN_TOKEN=             # mirrors ADMIN_TOKEN — used by admin pages
NEXT_PUBLIC_GOOGLE_MAPS_API_KEY=     # only needed when map_mode=google
```
