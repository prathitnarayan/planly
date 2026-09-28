# Planly frontend

Next.js 16 + TypeScript + Tailwind. Monochrome: ink, paper, three greys — no accent colour.
Everything it shows comes from the FastAPI backend; no planning logic lives here.

## Run
```
cd frontend
npm install
cp .env.local.example .env.local     # fill in the two Supabase values (same project as backend/.env)
npm run dev                          # http://localhost:3000
```
The backend must be running on :8000 (`python -m uvicorn app.main:app --reload --reload-dir app`).

- `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` empty → no login (dev mode;
  the backend must also be in dev mode).
- The anon key is public by design. Never put the service_role key or the DB password here.

## Try it without an AI key
```
cd backend && python -m tests.fake_ai_server   # scripted AI, in-memory, no login
cd frontend && npm run dev                     # with the Supabase vars empty
```

## Screens
`/login` · `/` goals · `/goals/new` (goal check) · `/goals/[id]` (questions → "look right?" →
hours → free time → plan) · `/goals/[id]/checkin`
