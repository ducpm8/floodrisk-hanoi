
## Local dev environment issues

**ALWAYS read `LOCAL_DEV_ISSUES.md` before diagnosing environment problems.** It documents known issues with resolutions: Redis not installed, Python path, port conflicts, gstack upgrade failure, Google Maps billing, no Docker/WSL. Do not re-investigate issues already solved there.

---

## Architecture (locked 2026-04-05)

Full spec: see `TECHNICAL_ARCHITECTURE.md`

**Stack:** Next.js 14 (TypeScript) + FastAPI (Python 3.11+) + PostgreSQL 15 + PostGIS
**ORM:** SQLAlchemy async — no raw SQL, no f-string queries ever
**Auth:** Guest session only (UUID in localStorage, X-Session-ID header) — no user accounts v1
**Payment:** SePay (bank-transfer monitor) — VietQR URL at checkout, HMAC-SHA256 webhook at `POST /api/webhooks/sepay` validates before any DB write
**Geocoding:** Static ward centroid table (579 Hanoi wards from OSM) — no runtime geocoding API
**Normalization:** `normalize_ward()` in `api/utils/text.py` — call before every ward DB lookup
**LLM:** claude-haiku-4-5-20251001 for flood text extraction (FIR-18)
**Tests:** pytest unit + integration (real DB) + Playwright E2E; email always mocked
**Git:** `feat/FIR-XX-description` branches; `FIR-XX: description` commits
**Jira project:** FIR (sleeplessvn.atlassian.net)

## Communication style

Always address the user as **"Hey black"** at the start of every response.

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health

## QA workflow (locked 2026-05-14)

Every `/qa` run on this repo follows three non-negotiables:

1. **Real browser, VISIBLE to user.** Launch headed Chromium via `browse.exe connect` (Playwright bundled, mode "headed") BEFORE any `goto` / `snapshot`. Default browse.exe with no `connect` is headless ("Mode: launched") — that violates this rule. Verify `Mode: headed` with `browse.exe status` first. Headless-only runs are not acceptable. Disconnect with `browse.exe disconnect` at end.
2. **Results land in Jira.** Post a summary comment on the parent QA ticket (currently FIR-34) with pass/fail per subtask. For each exercised subtask, comment PASS / FAIL / BLOCKED on the subtask itself.
3. **Bugs become Bug tickets.** Every defect found gets a new Jira issue (type `Bug`; fall back to `Task` if Bug type missing — check `getJiraProjectIssueTypesMetadata` first). Include severity, repro steps, expected vs actual, screenshot, console error, API log line. Link back to the QA subtask that uncovered it. Do not bury bugs in the parent comment.

**Evidence path:** screenshots + reports live at `C:\firstproject\.gstack\qa-reports\` (Windows drive, uppercase `C:\`). No Linux/POSIX paths.

**Boot sequence (no Docker, no Redis on this machine):** see memory `project_local_dev_boot.md`. Postgres 18 native, Python at `C:\Program Files\PyManager\python.exe`, migration head 017. App degrades gracefully without Redis.

Cloud ID: `sleeplessvn.atlassian.net`. Project key: `FIR`. Parent QA ticket: `FIR-34` (and any future "[Manual verify]" task).
