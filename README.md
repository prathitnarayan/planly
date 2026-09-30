# Planly

An AI goal planner that checks whether a goal actually fits your time, builds
weekly sprints, and replans when you miss things.

See `CLAUDE.md` for architecture, rules and phase status.

## Quick start (backend)
```
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
python -m uvicorn app.main:app --reload --reload-dir app
```
Open http://localhost:8000/docs and try `POST /feasibility`.

## Frontend
See `frontend/README.md` (`npm install`, `npm run dev`).

## Database
Run every file in `database/migrations/` (001, 002, …) in the Supabase SQL editor, in order.
Full setup: `docs/SUPABASE_SETUP.md`.

## Course sync
Chrome extension (`extension/README.md`) for logged-in course sites, "Add by link" on the
goal page for YouTube / public pages, or the terminal tool. See `docs/COURSE_SYNC.md`.

## Telegram nudges + Google Calendar
Optional. Setup: `docs/INTEGRATIONS.md` (bot token, Google OAuth client, the 15-minute GitHub Actions timer).
