# Connecting Planly to Supabase

About 10 minutes. Nothing here needs the secret / service_role key — never paste it anywhere.

## 1. Create the tables
Supabase dashboard -> **SQL Editor** -> **New query** -> paste all of
`database/migrations/001_planly.sql` -> **Run**. Then the same for every later file in
`database/migrations/` (e.g. `002_tracking.sql`), in number order. Each is safe to re-run.

Check: **Table Editor** shows `goals`, `checkins`, `estimate_corrections`, each marked
**RLS enabled**.

## 2. Fill in `backend/.env`
```
SUPABASE_URL=https://<your-ref>.supabase.co
SUPABASE_ANON_KEY=<publishable / anon key>
DATABASE_URL=<Connect -> Session pooler string, password filled in>
```
- Password has special characters like `@ # /`? They must be URL-encoded in
  DATABASE_URL (`@` -> `%40`, `#` -> `%23`, `/` -> `%2F`). Easiest: reset the DB
  password to letters + digits only.
- Use the **Session pooler** string, not "Direct connection" (direct is IPv6-only
  and times out on most home networks).

## 3. Install the new packages
```
cd backend && source .venv/bin/activate
pip install -r requirements.txt
pytest        # 151 pass, 9 Postgres tests skip on your machine — expected
```

## 4. (Optional, for testing) skip email confirmation
Authentication -> **Sign In / Providers** -> **Email** -> turn off **Confirm email**.
Otherwise you'll need to click a link in your inbox after signing up.

## 5. Sign up + get a token
```
python -m scripts.login
```
It signs you in (or offers to create the account) and checks the backend can verify
the token.

## 6. Run the API against Supabase
```
python -m uvicorn app.main:app --reload --reload-dir app
```
- http://localhost:8000/health should say `"auth": "supabase", "storage": "postgres"`.
- http://localhost:8000/docs -> **Authorize** -> paste the token -> try `GET /goals`.
- Without a token, `/goals` now answers **401** — that's the point.

## 7. Move your real plan in
```
python -m scripts.push_plan
```
Copies `data/plan.json` (and its check-ins) into Supabase under your account.
Check **Table Editor -> goals**.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Connection hangs / times out | You used the Direct connection string. Use **Session pooler**. |
| `password authentication failed` | Wrong DB password, or special characters not URL-encoded. |
| `invalid token: unsupported token algorithm: HS256` | Your project uses the legacy JWT secret: copy it from Settings -> JWT into `SUPABASE_JWT_SECRET`. |
| `DATABASE_URL is set but SUPABASE_URL is empty` | Deliberate safety stop: a database with no login. Add `SUPABASE_URL`. |
| `/health` says `memory` | `.env` not picked up — run uvicorn from `backend/`. |
