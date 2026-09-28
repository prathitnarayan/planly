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
uvicorn app.main:app --reload
```
Open http://localhost:8000/docs and try `POST /feasibility`.

## Database
Run `database/migrations/001_init.sql` in the Supabase SQL editor.
