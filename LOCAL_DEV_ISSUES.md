# Local Dev Known Issues & Resolutions

Auto-loaded via CLAUDE.md. Check this file before diagnosing environment problems.

---

## 1. Redis not installed — PATCH /api/admin/settings returns 503

**Symptom:** `PATCH /api/admin/settings` with `{ "map_mode": "google" }` returns `503 Settings store unavailable (Redis offline)`.

**Root cause:** Redis is not installed on this machine. No Memurai, no WSL Redis.

**Resolution:** Change `_DEFAULT_MAP_MODE` directly in `api/routers/admin_settings.py:27`.
```python
_DEFAULT_MAP_MODE = "google"  # current value as of 2026-06-14
```
Do NOT attempt to install Redis or use `redis-cli` — it will fail. The app degrades gracefully; GET /api/admin/settings falls back to the code default.

---

## 2. Python executable — must use full path

**Symptom:** `python` or `python3` commands fail or use wrong interpreter.

**Resolution:** Always use:
```
"C:\Program Files\PyManager\python.exe"
```
Or in Bash: `"/c/Program Files/PyManager/python.exe"`

---

## 3. Port 3000 / Node process hangs

**Symptom:** `npm run dev` fails with EADDRINUSE or process appears stuck.

**Resolution:**
```powershell
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force
```
Then restart dev server.

---

## 4. gstack upgrade fails — bun subshell error

**Symptom:** `./setup` during gstack upgrade throws:
`"Subshells with redirections are currently not supported. Please open a GitHub issue."`

**Root cause:** bun 1.3.11 bug on Windows — subshell redirections in build scripts.

**Resolution:** Skip upgrade. Continue on current version (1.40.0.0). Run `~/.claude/skills/gstack/bin/gstack-config set auto_upgrade false` if upgrade prompts are disruptive.

---

## 5. Google Maps API key requires GCP billing

**Symptom:** Homepage shows "Lỗi bản đồ" error overlay. `gm-authfailure` event fires. FloodMap.tsx:94 catches it.

**Root cause:** Maps JavaScript API requires billing enabled on GCP project, even for basic map rendering.

**Current key:** `AIzaSyC8-h4TUvgYC3C8_bqIN_4jTve1yGdCNxs` (set in `web/.env.local`)
**GCP billing:** Must be enabled at console.cloud.google.com → Billing.
**Note:** Geocoding API also requires billing but app does NOT use it (static ward centroid table used instead).

---

## 6. No Docker, no WSL — pure Windows native

**Symptom:** `docker`, `docker-compose`, `wsl` commands all fail.

**Resolution:** Everything runs natively on Windows. PostgreSQL 18 runs as Windows service `postgresql-x64-18`. No containers.

---

## 7. API server must run from repo root

**Symptom:** `ModuleNotFoundError: No module named 'api'` when starting uvicorn.

**Root cause:** `api.main:app` is a dotted import — cwd must be `c:\firstproject` (not `c:\firstproject\api`).

**Resolution:**
```bash
# cwd = c:\firstproject
"/c/Program Files/PyManager/python.exe" -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

---

## 8. Admin token unset in dev — auth bypassed

**Symptom:** `PATCH /api/admin/...` with no `X-Admin-Token` returns 200 (not 401).

**Root cause:** `ADMIN_TOKEN` env var not set in `.env`. Dev mode bypasses auth by design (`api/routers/admin_crawl.py:46`).

**Resolution:** This is expected in local dev. For auth testing, set `ADMIN_TOKEN=secret` in `.env` temporarily.

---

## Environment summary (as of 2026-06-14)

| Component | Status | Notes |
|-----------|--------|-------|
| PostgreSQL 18 | ✅ Running | Windows service `postgresql-x64-18`, DB `flooddb` |
| Redis | ❌ Not installed | App degrades gracefully; map_mode hardcoded in code |
| Node / Next.js | ✅ Available | Port 3000, kill with `Get-Process node \| Stop-Process -Force` |
| Python | ✅ Available | `C:\Program Files\PyManager\python.exe` only |
| Google Maps | ✅ Key set | Billing required; key in `web/.env.local` |
| Docker | ❌ Not installed | Not needed |
