# Unit test failure report — `api/tests/unit`

- **Date:** 2026-11-09
- **Repo:** `C:\firstproject` (`ducpm8/floodrisk-hanoi`)
- **Python:** 3.14.4 (`C:\Program Files\PyManager\python.exe`)
- **Commands:**
  ```powershell
  # focused re-run of the failing modules
  & 'C:\Program Files\PyManager\python.exe' -X utf8 -m pytest `
      api/tests/unit/test_admin_ingest.py `
      api/tests/unit/test_admin_settings.py `
      api/tests/unit/test_ai_extraction_worker.py `
      api/tests/unit/test_payment_router.py `
      api/tests/unit/test_ward_lookup.py -q --tb=short

  # modules that fail during collection
  & 'C:\Program Files\PyManager\python.exe' -X utf8 -m pytest `
      api/tests/unit/test_news_scraper.py `
      api/tests/unit/test_official_scraper.py -q --tb=short
  ```
- **Result:** 15 failed, 126 passed (focused run); 2 modules fail collection.
- **Scope note:** none of these failures are caused by the 2025 ward-centroid work.
  `api/utils/ward_lookup.py` is unmodified in git, and the new
  `api/tests/unit/test_seed_wards_2025.py` passes 34/34.

---

## 1. Collection errors — 2 modules never run

### 1.1 `api/tests/unit/test_news_scraper.py`

```
ImportError: cannot import name '_MOCK_EXTRACTION' from 'api.scrapers.news'
```

The test also imports `_extract_with_haiku`, `_insert_news_event`, `_parse_iso_date` — all **missing** from the current module.
`_parse_article_generic` and `_parse_list_generic` are present (re-imported from `api.scrapers.base`).

**Root cause:** the test targets an older `news.py` API. The current module exposes
`scrape_news_source`, `scrape_all_news`, `scrape_vnexpress`, `scrape_tuoitre`,
`_scrape_source`, `_fetch_article_list`, `_fetch_url`, etc. `_MOCK_EXTRACTION` and
friends do not exist even in `HEAD` (the repo has a single `Initial commit`).

**Fix options:** update the test to the current functions, or restore the removed helpers.

### 1.2 `api/tests/unit/test_official_scraper.py`

```
ImportError: cannot import name '_infer_severity' from 'api.scrapers.official'
```

Also missing: `_parse_event_date`, `_parse_html`.
The current `official.py` only exposes `scrape_official` + `_fetch_rows` (Playwright path);
`thoatnuoc_com.py` has its own `_parse_event_date`.

**Impact:** `pytest api/tests/unit` aborts collection unless these two files are ignored:

```powershell
& 'C:\Program Files\PyManager\python.exe' -X utf8 -m pytest api/tests/unit -q `
    --ignore=api/tests/unit/test_news_scraper.py `
    --ignore=api/tests/unit/test_official_scraper.py
```

---

## 2. `test_admin_ingest.py` — N11 / N13 return 503 instead of 422

```
api/tests/unit/test_admin_ingest.py:722: assert 503 == 422
api/tests/unit/test_admin_ingest.py:749: assert 503 == 422
```

Actual response body (reproduced):

```json
{"detail":"Whisper fallback needs YouTube cookies: set YOUTUBE_COOKIES_FILE in .env (see scripts/youtube_cookies_export.py)"}
```

Captured log:

```
WARNING api.cache:cache.py:29 Redis unavailable — caching disabled:
Error 22 connecting to localhost:6379. The remote computer refused the network connection.
```

**Root cause:** `api/routers/admin_ingest.py:486` calls `_fetch_via_whisper()` whenever
captions are absent or empty (`:483`). The local `.env` has:

- `OPENAI_API_KEY` → **set** (164 chars)
- `YOUTUBE_COOKIES_FILE` → **unset**

The Whisper fallback is therefore enabled, then raises 503
(`api/routers/admin_ingest.py:385-386`) because audio download needs cookies.
The tests only mock yt-dlp/subtitles and assume the fallback is disabled.

The Redis warning above is **not** the cause; it is non-fatal.

**Fix:** patch `api.routers.admin_ingest._fetch_via_whisper` to return `None` in these two tests,
or clear `OPENAI_API_KEY` / set `YOUTUBE_COOKIES_FILE` for the test run.

---

## 3. `test_admin_settings.py` — 2 failures

```
test_get_settings_returns_default_when_redis_miss
    assert {'map_mode': 'google'} == {'map_mode': 'text'}

test_get_settings_falls_back_to_default_on_invalid_stored_value
    assert 'google' == 'text'
```

**Root cause:** `api/routers/admin_settings.py:27`

```python
_DEFAULT_MAP_MODE = "google"
```

`LOCAL_DEV_ISSUES.md` documents that this was changed from `"text"` to `"google"` to work
around the missing Redis. The tests still expect `"text"`.

**Fix:** patch `_DEFAULT_MAP_MODE` in these tests, or restore the `"text"` default if the
local override is no longer wanted.

---

## 4. `test_ai_extraction_worker.py` — 8 failures (one root cause)

```
TestSaveEvent.test_high_confidence_inserts_flood_events
TestSaveEvent.test_medium_confidence_inserts_flood_events
    AssertionError: assert <MagicMock name='mock.execute().rowcount'> == 1

TestRunWorker.test_social_row_adds_event
TestRunWorker.test_news_row_adds_event
TestRunWorker.test_mixed_rows_both_processed
TestRunWorkerFIR41.test_manual_row_processed
TestRunWorkerFIR41.test_governance_row_processed
TestRunWorkerFIR41.test_youtube_row_processed
    TypeError: '>=' not supported between instances of 'MagicMock' and 'int'
```

**Root cause:** `api/scrapers/ai_extraction_worker.py:389`

```python
return result.rowcount
```

`save_event()` correctly returns the SQL `rowcount` (0 when
`ON CONFLICT (source_url, event_date, COALESCE(ward, ''), COALESCE(location_name, '')) DO NOTHING`
skips a duplicate). The tests use a bare `conn = MagicMock()` without configuring
`conn.execute(...).rowcount = 1`, so the return value is a `MagicMock`; `run_worker()` then
sums it into `records_added`, producing the `MagicMock >= int` `TypeError`.

**Fix:** in the test fixtures set `conn.execute.return_value.rowcount = 1`
(or use `MagicMock(rowcount=1)`), and `rowcount = 0` for duplicate/conflict cases.

Files/lines:
- `api/tests/unit/test_ai_extraction_worker.py:252-263` (SaveEvent)
- `api/tests/unit/test_ai_extraction_worker.py:350,358,389,433,441,449` (RunWorker)
- `api/scrapers/ai_extraction_worker.py:348-389` (production)

---

## 5. `test_payment_router.py` — 1 failure

```
api/tests/unit/test_payment_router.py:208: assert 200 == 404
```

**Root cause:** `api/routers/payment.py:32`

```python
_DEMO_MODE = os.getenv("PAYMENT_DEMO_MODE", "false").lower() == "true"
```

The local `.env` contains `PAYMENT_DEMO_MODE=true`. The flag is evaluated once at import
time, so `POST /api/payment/dev-confirm` is live (200) instead of returning 404
(`api/routers/payment.py:181`). The test assumes the production default.

**Fix:** monkeypatch `api.routers.payment._DEMO_MODE = False` in the test, or run the suite
with `PAYMENT_DEMO_MODE=false`. Note that `monkeypatch.setenv()` alone will not work because
the value is captured at import time.

---

## 6. `test_ward_lookup.py` — 2 Haiku fallback failures

```
test_high_confidence_auto_accepted
    AssertionError: assert <MagicMock name='mock.fetchone().alias_type'> == 'llm'

test_low_confidence_queued_as_pending
    AssertionError: assert ResolvedLocation(...) is None
```

**Root cause:** the FIR-73 substring-alias step added one more DB call. Before Haiku,
`resolve_location()` now executes **7** statements:

| Step | Calls |
|---|---|
| `_ward_lookup` (exact, 3 prepends, LIKE) | 5 |
| alias exact (`api/utils/ward_lookup.py:208`) | 1 |
| alias substring (`api/utils/ward_lookup.py:222`) | 1 |
| **total before Haiku** | **7** |

Both tests supply only **6 misses**:

```python
conn.execute.side_effect = (
    [miss] * 5   # step 1: ward miss
    + [miss]     # step 2: alias exact miss
    + [ward_hit] # expected step 3, but actually consumed by step 2b
    + [insert_ok]
)
```

The 7th call (substring lookup) consumes `ward_hit`, so `resolve_location()` returns a fake
alias row whose `resolution_level` is a `MagicMock` instead of proceeding to Haiku.

**Fix:** change both sequences to `[miss] * 7 + [ward_hit] + [insert_ok]`
(`api/tests/unit/test_ward_lookup.py:175-181` and `:203-205`).

---

## 7. Integration tests cannot run locally

`api/tests/integration/test_ward_lookup_2025.py` collects 6 tests but requires a test
database that is not running:

```
DATABASE_TEST_URL=postgresql+psycopg2://flood:flood@localhost:5433/flooddb_test
```

Nothing is listening on port **5433** (only the dev DB on 5432). The integration test was
written as part of the 2025 ward work and cannot be executed in this environment.

---

## Summary

| Group | Failures | Cause | Pre-existing | Related to ward work |
|---|---|---|---|---|
| Scraper test collection | 2 modules | Tests import removed symbols (`_MOCK_EXTRACTION`, `_infer_severity`, …) | Yes | No |
| YouTube ingest 503 | 2 | `.env` has `OPENAI_API_KEY`, no `YOUTUBE_COOKIES_FILE`; Whisper fallback fires | Yes (env) | No |
| Admin settings default | 2 | `_DEFAULT_MAP_MODE = "google"` (documented local override) | Yes | No |
| AI worker rowcount | 8 | Test mocks do not set `execute().rowcount` | Yes | No |
| Payment dev-confirm | 1 | `.env` `PAYMENT_DEMO_MODE=true` captured at import time | Yes (env) | No |
| Ward lookup Haiku | 2 | Test mocks miss the step-2b substring lookup call | Yes | No |

**Verdict:** all 15 failures and 2 collection errors are pre-existing test/environment drift.
The 2025 ward-centroid changes are green: seed unit tests pass 34/34, the dev DB has all 126
units resolving, and no production code touched by this feature is involved in the failures.
