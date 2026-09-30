# Telegram nudges + Google Calendar (read-only)

Both are optional. Until the backend has the settings below, Planly's Settings page just says
"not set up on this server yet".

**What each one does**
- **Telegram**: at most two messages a day, both switchable: a morning list of today's tasks with
  tick buttons, and an evening check *only if something is still open*. `/today` any time, `/stop` to leave.
- **Google Calendar**: **read-only, free/busy only** (scope `calendar.freebusy`). Planly sees *when* you're
  busy, never titles or people, and never creates events, so your calendar sends no extra notifications.
  Meetings inside your free slots are simply taken out of your study time.

---

## 1. Shared secrets (Render → planly service → Environment)
Generate three random strings (run it three times; on a Mac the command is `python3`):
```
python3 -c "import secrets;print(secrets.token_urlsafe(48))"
```
| Key | Value |
|---|---|
| `PUBLIC_API_URL` | `https://planly-u3y3.onrender.com` |
| `FRONTEND_URL` | `https://planly-ivory.vercel.app` |
| `APP_SECRET` | random string #1 (never change it later: it encrypts stored Google tokens) |
| `CRON_SECRET` | random string #2 |

## 2. Telegram (~3 minutes)
1. In Telegram, open **@BotFather** → `/newbot` → name it (e.g. *Planly*) → username ending in `bot`
   (e.g. `planly_utkarsh_bot`). Copy the **token**.
2. On Render add:
   | Key | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | the token |
   | `TELEGRAM_BOT_USERNAME` | the username, without @ |
   | `TELEGRAM_WEBHOOK_SECRET` | random string #3 (letters, digits, `_` and `-` only — the command above gives exactly that) |
3. Planly registers its webhook with Telegram by itself the first time someone connects.
4. In Planly → **Settings** → **Connect Telegram** → Telegram opens → tap **Start**. Done.

## 3. The 15-minute timer (so messages arrive on time)
The repo includes `.github/workflows/planly-cron.yml`. In GitHub → your repo → **Settings → Secrets and
variables → Actions → New repository secret**:
- `PLANLY_API_URL` = `https://planly-u3y3.onrender.com`
- `PLANLY_CRON_SECRET` = the same value as `CRON_SECRET`

Then **Actions → planly-cron → Run workflow** once to test (should print `{"sent":…}`).
GitHub timers can run a few minutes late; Planly's send windows (3 h morning, 2 h evening) absorb that.
No GitHub? Use cron-job.org (free): POST to `…/cron/notify` every 15 min with header `X-Cron-Secret`.

## 4. Google Calendar (~10 minutes)
1. <https://console.cloud.google.com> → create a project (e.g. *Planly*).
2. **APIs & Services → Library** → enable **Google Calendar API**.
3. **OAuth consent screen**: User type **External** → app name *Planly*, your email → **Scopes: add
   `.../auth/calendar.freebusy`** → Test users: add your Gmail (and friends').
   Then **Publish app** (to *In production*). Why: in *Testing*, Google expires the sign-in after 7 days.
   You'll see "Google hasn't verified this app" when connecting → **Advanced → Go to Planly**. That's
   expected for a personal app (verification is only needed for 100+ users).
4. **Credentials → Create credentials → OAuth client ID** → *Web application*:
   - Authorised redirect URI: `https://planly-u3y3.onrender.com/google/callback`
5. On Render add `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
6. Planly → **Settings** → **Connect Google Calendar**.

## 5. Database
Run in Supabase (SQL Editor), in order, if you haven't yet: `007_integrations.sql`, then `008_habits.sql`.
Both are safe to run twice.

## 6. Habits + the daily quote (API 0.12.0)
Nothing to configure. Once Telegram is connected:
- **Every morning**: the day's quote comes first (it's what shows on the lock screen), then today's tasks,
  then habit streaks, with "yesterday?" buttons for any habit left blank. People with no goals or
  habits still get the quote. Switch it off in Settings → "Daily quote".
- **Evening**: only if a task is unticked or a habit isn't answered for today.
- **Heads-up**: at most one a day, 30 minutes before *your* usual urge time. It only starts after
  you've logged a few urges (/urge in Telegram, or "Urge right now" on the Habits page).
- Telegram shows a habit's **lock-screen name** (e.g. "H1") instead of its real name, if you set one.
- Bot commands: `/today`, `/urge`, `/stop`.

