# Planly — context for coding sessions

AI goal planner. User states a goal → short AI interview → blueprint of milestones →
check it against their weekly free time → weekly sprints and daily tasks →
log done/missed → replan. Built first as a learning project (Python, FastAPI, ML),
possibly sold later.

## The one rule
**The LLM is not the planner.** The LLM turns fuzzy text into structured JSON
(interview questions, blueprint, resource breakdown) and explains results.
All time math, capacity, feasibility, scheduling, task state and dependency
resolution is plain Python in `backend/app/planners/`, with tests.
Never ask the LLM to add up hours or pick dates.

## Stack
- Backend: Python, FastAPI, Pydantic v2 (source of truth for all logic)
- DB/auth: Supabase (Postgres + Auth). RLS on every table, every table has `user_id`
- Frontend: Next.js + TypeScript + Tailwind on Vercel — display and input only
- LLM: OpenAI (JSON mode) behind `app/ai/llm.py` → `LLMClient`. Swap provider = one new class.
  Called only from the backend. Keys never reach the frontend

## Layout
```
backend/app/
  schemas/blueprint.py   GoalBlueprint — the JSON contract the LLM must return
  schemas/interview.py   GoalProfile, InterviewTurn
  planners/capacity.py   weekly slots → minutes per day (max / sustainable / fallback)
  planners/feasibility.py day-by-day EDF simulation: each milestone vs its own due date → honest options
  planners/estimate_check.py deliverable totals vs user's benchmarks → warnings (>1.5x or <0.5x)
  planners/scheduler.py  replays the SAME simulation, records (day, milestone, minutes),
                         splits by kind (learn→practice→project…), places in slots, groups Mon–Sun sprints
  planners/load.py       course items -> study minutes (video x1.5, problems by difficulty); unsized = asked, not guessed
  planners/progress.py   check-ins → progress (remaining + its source) → personal multiplier → replan;
                         day_plan/close_days: Today checkboxes -> VERIFIED check-ins, one per milestone per closed day
  planners/tasks.py      sessions -> concrete lectures/problems (learn→videos, practice→problems), long videos split
  planners/integrity.py  evidence vs ticks: verified / partial / self / mismatch; trust, streak, owed minutes, locks
  planners/learning.py   Outcome rows (one per planned session of each closed day) -> learned weekday reliability
                         + video pace (real page time / video watched); recency-weighted, shrunk to defaults
  planners/reality.py    reality check: AI gives the commonly quoted prep range for WELL-KNOWN goals (UPSC, CAT,
                         marathon...); code compares with deadline + free time -> ok / tight / unrealistic (warn only)
  planners/notify.py     when to message (morning/evening windows, once per local day, habit nudge) + text/buttons
  planners/breaks.py     "something came up": lost minutes / rest of today / days away -> excused sessions + days off,
                         with anti-faking checks (say it when it happens, 4 free days per 30, patterns named)
  planners/habits.py     habit streaks (current / best / 30-day record, blanks skipped), risky time from the
                         user's own urge log, risky weekday from their slips, personal lines, daily quote
  core/telegram.py       Bot API calls; webhook auto-registered (secret header). core/gcal.py: Google OAuth,
                         calendar.freebusy ONLY, refresh token encrypted with APP_SECRET, busy cached 30 min
  planners/pool.py       ONE pool of free time for all goals: goals plan in priority order, lower goals get the
                         leftover time (exact times cut out of their slots via CapacityProfile.taken)
  core/plan_file.py      TEMP: one saved plan in data/plan.json for the terminal scripts
  ai/llm.py              LLMClient, OpenAIClient, generate_validated (validate + 1 retry)
  ai/prompts.py          all prompts
  ai/goal_intake.py      interview turns + blueprint generation (gets measured course_load)
  ai/source_extract.py   course page text -> items (chunked, merged). AI copies "12:34"/"15 Oct"; code parses
  schemas/source.py      CourseSource / SourceItem, parse_duration, parse_due (code picks the year)
  core/config.py         .env settings
  core/auth.py           verifies Supabase JWTs (ES256/RS256 via public JWKS, legacy HS256); dev mode = fixed user
  core/repo.py           GoalRepo: InMemoryRepo (dev/tests) + PostgresRepo (Supabase). EVERY query filters by user_id
  core/estimate_log.py   AI estimate vs user correction → data/estimate_corrections.jsonl (future ML data)
  main.py                FastAPI app
backend/scripts/try_goal.py  interview → blueprint → estimates → feasibility → schedule → save
backend/scripts/checkin.py   daily check-in → replan. Supabase by default (scripts/session.py remembers
                             sign-in via refresh token in data/session.json); --local = data/plan.json
backend/sync/            LAPTOP tool: Playwright w/ saved login (data/browser/, no passwords stored),
                         expand+scroll+follow, YouTube via yt-dlp -> POST /goals/{id}/sources[/page]. docs/COURSE_SYNC.md
  core/web_read.py       server-side PUBLIC reads only: YouTube Data API (YOUTUBE_API_KEY), public pages
                         (private/loopback addresses refused, redirects re-checked)
extension/               Chrome MV3: per-site ON via optional_host_permissions (Chrome enforces), Planly login
                         (Supabase refresh token in chrome.storage), lib/reader.js expands+scrolls+reads the page,
                         background.js POSTs /sources/page and auto-re-syncs watched pages (<= every 6h)
frontend/components/goal/Courses.tsx  "Your courses" panel: load per course, items by section, add by link
backend/tests/           pytest; every planner function gets tests
database/migrations/     001..008, run in order (002 dates, 003 sources, 004 ticks, 005 integrity + watch_evidence,
                         006 user_settings (shared capacity, goal order, learned) + outcomes + watch_evidence.active_s,
                         007 timezone + integrations + telegram lookups, 008 habits + habit_days + urges).
                         001_planly.sql — goals (JSONB docs) + append-only checkins / estimate_corrections, RLS
docs/SUPABASE_SETUP.md   step-by-step connection guide
docs/normalized_schema_future.sql  NOT applied — future row-per-milestone design
```

## Conventions
- Planner functions are pure: inputs in, result out. No DB or network inside them.
- Plan at *sustainable* capacity (default 80% of free slots), never maximum.
- The LLM never writes dates. Interview records the user's `key_dates`; blueprint
  milestones only REFERENCE them (`due_by`, `start_after`, `due_offset_days`).
  `resolve_items()` turns references into real dates.
- `required=False` marks optional work. Only optional milestones are ever offered as cuts.
- Key dates are HARD by default (graded submissions can't slip). "Accept a later finish"
  is only offered when every late item has a soft deadline.
- The scheduler never decides anything new: it records the feasibility simulation's
  allocations, so the schedule and the verdict can't disagree (tested).
- The blueprint prompt gets the raw interview transcript; the user's words beat the profile.
- Replanning never guesses remaining work: complete → 0; user's number → that;
  else estimate − spent; spent it all and not done → overrun floor. The personal
  multiplier (from ≥2 COMPLETED milestones, clamped 0.5–2.0) only touches AI estimates.
- Benchmark parts are compared against the SUM of milestones tagged with that part.
  Parts that match are locked when the user rescales a deliverable; if ALL match, the
  change goes to the biggest part (main work). The "kept as-is" message comes from the
  same `_scaling_plan()` that does the scaling, so it can't disagree (a real past bug).
- The interview opens with a goal check (people type their status where the goal goes)
  and ends with a plain-language "Look right?" confirm; corrections are applied by the
  LLM, kept in the transcript, and drop any stale blueprint.
- Invalid placeholder values from the LLM in optional fields are dropped, not fatal;
  the terminal script retries AI calls instead of crashing mid-interview.
- No session under 15 min; the scheduler continues a milestone's learn→practice→project
  from where progress left off (PlanItem.done_minutes).
- Feasibility starts tomorrow by default (today is usually half gone).
- Estimates are anchored on the user's own `benchmarks` (how long similar work took,
  with optional parts). Lesson from real runs: the LLM quotes the benchmark then
  ignores it (25h benchmark → 56h plan). So the comparison is CODE: milestones are
  tagged by `deliverable`, the blueprint maps deliverables → benchmarks in `anchors`,
  and `check_estimates()` warns. The user decides.
- The interview guarantees in CODE (not prompt) that the deadline and a benchmark are asked
  once each ("no" is accepted). Real runs showed the model skipping both.
- Users review hours per DELIVERABLE, not per milestone (people think "Assignment 2
  = 27h"); the code splits proportionally. Every real change is logged with a source.
- If the goal doesn't fit, say so. Return options (more hours / later deadline /
  cut scope). Never quietly squeeze.
- `task_executions` is append-only and stores estimate + actual. It's the future
  ML dataset — don't skip logging.
- LLM output is validated by Pydantic. On failure, send the error back to the LLM
  and retry once.
- The backend connects with the DB password, which bypasses RLS: repo.py filters EVERY
  query by user_id (tested: another user gets 404 on read/write/check-in/delete).
  RLS stays on as the second lock. Never put the service_role key in the backend.
- Repos return copies; changes persist only via save()/add_checkins() — so in-memory
  behaves like the DB. Check-ins are append-only and only change via add_checkins().
- A Supabase token stays valid (up to 1h) after its user is DELETED. Writes then hit the goals.user_id
  foreign key: the API turns that into 401 {code: account_gone} and the frontend signs out (29 Sep incident).
- No SUPABASE_URL = dev mode (in-memory, one dev user). DATABASE_URL without SUPABASE_URL
  is refused at startup (a real DB with no login).
- Tests: conftest forces in-memory + dev auth even if .env has real values. Postgres tests
  run only with PLANLY_TEST_PG set (they create/drop their own database).

## Run
```
cd backend
pip install -r requirements.txt
pytest                         # tests (AI tests use a FakeLLM, no key needed)
python -m scripts.login        # Supabase sign-in → token for /docs Authorize
python -m scripts.try_goal     # chat with the real API in the terminal
python -m uvicorn app.main:app --reload --reload-dir app  # API at http://localhost:8000/docs
```

## Phase status
- [x] Blueprint schema + validation
- [x] DB schema (001_init.sql)
- [x] Capacity + feasibility engine, `/feasibility` endpoint (stateless)
- [x] AI interview → blueprint generation (OpenAI JSON mode, validated, code-enforced question limit)
- [x] Goal check at the start + 'Look right?' confirm/correct step (script + API)
- [x] Supabase: JWT auth in FastAPI, Postgres repo, RLS migration, login + push_plan scripts
- [ ] Resources typed in by user → structured units → milestone mapping
- [x] Per-milestone due dates / start windows, required vs optional, EDF feasibility
- [x] Benchmarks (with parts), code-level estimate check, per-deliverable overrides, correction log
- [x] Hard vs soft deadlines, days late per item
- [x] Sprint scheduler: timed sessions in slots, learn-before-practice, weekly sprints + definition of done, /schedule endpoint
- [x] Check-ins (done / partial / missed + finished?) → progress → multiplier → replan; API + checkin script
- [x] Frontend (frontend/): Next.js 16 + Tailwind, monochrome. Login (Supabase JS), goals, goal check,
      interview, confirm/correct, hours + estimate warnings, free time, plan, check-in. See frontend/README.md.
      Backend: CORS (FRONTEND_ORIGINS), /due, GET /blueprint, GET /capacity, replan starts at the right day.
- [x] Course sync (backend/sync): 12 sites + any site generically, measured load, course deadlines -> hard key dates,
      course_load fed to blueprint, coverage warning in /estimate-check
- [x] Chrome extension + add-by-link (YouTube API / public pages) + Courses panel on the goal page
- [x] Today checkboxes (GET /today, PUT /ticks; 004_ticks.sql). Past days auto-close on /today or /replan:
      ticked = done, some = partial, none = missed; last planned piece ticked = milestone complete.
- [x] Verification (API 0.8.0): lecture-level tasks, extension watch tracking + on-page bar, auto-tick at 80%,
      strict consequences (no credit, +25% owed, trust, streak, evidence-only mode, 3-in-7 lock). 005_integrity.sql
- [x] Learning stage 1 + shared time pool (API 0.9.0, 006_learning.sql): weekday reliability & video pace learned
      nightly from outcomes and used in planning; goals share one capacity in priority order (/me/goal-order)
- [x] Reality check (API 0.10.0): GET /goals/{id}/reality, asked once per goal (reset on correction), stored in
      interview.reality (no migration); card on the confirm step, and on the plan page when tight/unrealistic
- [x] Telegram nudges + Google Calendar read-only (API 0.11.0, 007_integrations.sql, docs/INTEGRATIONS.md):
      /me/settings, /me/telegram/*, /telegram/webhook, /cron/notify (GitHub Actions every 15 min), /me/google/*
- [x] Habit streaks + daily quote (API 0.12.0, 008_habits.sql): /habits CRUD, PUT /habits/{id}/days,
      POST /habits/{id}/urges; Habits page; Telegram yes/no buttons, /urge, one heads-up a day at the
      user's own risky time; quote at the top of every morning message, even with no goals
- [x] "Something came up" (API 0.13.0, no migration: stored in user_settings.integrations.breaks): /me/breaks
      GET/POST/DELETE, panel on the Today card, excused rows, Telegram evening button + /skip
- [x] API 0.14.1 / extension 0.2.1: record only tracked lectures; one entry per course (no duplicate syncs); expired AI key -> clear 503 (code ai_key_expired);
      crashes return JSON 500 WITH CORS headers (a header-less 500 made the app say "can't reach the API", 9 Oct)
- [ ] Learning stage 2 (crowd priors per course item) and 3 (model); long-haul mode (phases, rolling detail,
      revision cycles, books, mock scores, fixed daily blocks)
- [ ] Codeforces/LeetCode solved via their APIs (stronger practice evidence); trust per user instead of per goal
- Later: web research, PDF parsing, ML duration model, what-if simulation

## Today / ticks rules
- Ticks never change today's list; it's the replan from today with check-ins up to yesterday (stable ids
  "day|milestone|kind|index"). Only when a day is over are ticks turned into check-ins.
- Missed work is never piled onto the next day: the simulation fills each day only up to sustainable
  capacity, so it spreads forward (and feasibility flags a deadline if it no longer fits).
- The browser sends its local date (server is UTC); trusted only within ±1 day of the server's date.
- Frontend calls /today BEFORE /replan (sequentially) so a day is closed exactly once.
- A day with nothing to tick (plan starts later / free day) shows "Next up" (preview, hatched boxes) and,
  before the plan starts, "Start today instead" (POST /start-today; only if nothing is logged yet).

## Notification + calendar rules
- One channel (Telegram), each switchable: morning (quote + list + habits); evening ONLY if a session is
  unticked or a habit is unanswered; at most one habit heads-up. Sent once per LOCAL day inside a window (3h / 2h) so a late cron never sends a stale message.
- Button data is a short token (Telegram's 64-byte limit) mapped to {goal, session, day} in the user's settings;
  buttons live for today + yesterday. Locked (evidence-only) sessions can't be ticked from chat.
- Calendar is READ-ONLY free/busy: never titles, never writes events. Only the part of a meeting that overlaps a
  free slot is taken out (merged), before any goal plans. A failed fetch keeps the last busy times + shows an error.
- No profiling users (age/"nature") for messages: personal = built from their own streaks and numbers.

## "Something came up" rules (planners/breaks.py)
- TODAY is never re-planned: unticked sessions are marked excused (ids stay stable). FUTURE days in a
  "days" break get capacity 0 (overrides, computed in Ctx.cap, never saved), so the planner routes around them.
- Excused within the allowance = not a miss: no "missed" check-in, no streak reset, no learning outcome.
  The work still moves forward. A milestone is never marked complete on a day part of it was excused.
- Checks: only sessions not yet ended (+1 h grace) at the time of the excuse; never ticked ones; never past
  days. Same-day excuses get 4 free days per 30 (lost time = ½); past that it still moves the work but
  counts as missed. Told-ahead days off are free. One "rest of today" per day. Same weekday excused
  >= 3 times in 5 weeks -> named as a pattern with a suggestion to lower that day's free time.
- Undo: today's excuse until the day closes, and anything still ahead. Closed days stay judged.

## Habit rules
- Self-report only, so NO penalties, trust or locks. A slip resets the current streak; best streak and
  "N of the last 30 days" stay. Blank days neither count nor break the streak.
- Answers can be changed for the last 7 days (web) / today and yesterday (Telegram buttons), never before
  the habit's start date or in the future. Max 10 active habits.
- Nudge = at most one a day, only once >= 4 urges in 60 days put >= 3 (and >= 40%) in one 2-hour window;
  30 min before that window, only for unanswered quit habits with nudge on. No pattern, no nudge.
- Messages show `label` (lock-screen name) when set, never the real name. Personal lines only from the
  user's own numbers + their own "why" line. Quotes: short, old / proverbs; same all day, cycles per user.
- Telegram buttons now stay valid for today + yesterday (token has msg kind + made day), so the morning
  buttons still work after the evening message. Button rows can hold 2 buttons (yes / no).

## Reality check rules
- The model only says whether a goal is well-known and the usual range (hours + months), labelled an AI estimate.
  Code does the comparison: unrealistic < 50% of the usual minimum (months OR hours), tight < 100%.
- It warns and suggests (a later attempt date, narrower goals); it never blocks the plan.

## Learning + shared pool rules
- Learned numbers only kick in with enough evidence (3 sessions per weekday, 3 lectures for pace) and are
  pulled towards the defaults; recency is relative to the newest sample (half-life 14 days).
- The weekday ratio replaces sustainable_ratio for that weekday (0.8 x share really done), clamped 0.3-1.0.
- CapacityProfile.weekday_ratio / .taken are computed per request (exclude=True) and never saved.
- The shared capacity lives in user_settings; saving free time on any goal saves it for all goals.
  The goal's own capacity is only the fallback for accounts from before 006.
- Page time (active_s) only counts in the top frame, while the tab is visible + focused and a video plays or
  the page was touched in the last 5 min. Evidence with unknown length (duration 0) proves nothing.

## Verification rules
- A penalty needs PROOF: video opened in tracked Chrome but < 50% of the planned part played, or the site
  re-read AFTER the tick still shows it not done. No evidence = self-reported, never punished.
- Watch evidence only grows (union of played ranges), only while the tab is visible, ads and > 2x skipped.
  Collected only on sites switched On AND only for lectures in the user's synced courses (GET /evidence/tracked:
  "yt:<id>" videos + "page:<origin><path>" lecture pages). Anything else — a music video on YouTube — is never
  recorded, no bar is shown, and /evidence/watch drops it server-side too (real bug 9 Oct: everything was recorded).
  Until the tracked list loads, nothing is recorded. The bar shows on a tracked lecture (recording) or on a
  synced course / playlist page (says it's in Planly); × hides it for that page only. The tracker
  registration is re-checked every time the service worker wakes (real bug 9 Oct: it was lost, bar never
  came back on refresh).
- A disproved session credits nothing (not even its unproven items). Auto-tick needs items to fill >= 80%
  of the session AND every item proven.
- All thresholds are constants at the top of planners/integrity.py.

## Course sync rules
- Logged-in reading happens ONLY in the user's own browser (extension) or laptop (sync tool). The server
  reads public data only. The extension touches a site only after the user switches it On.
- Runs on the user's laptop only. Never store third-party passwords anywhere; the saved browser
  session lives in backend/data/browser/. Read-only: no downloads, no DRM/CAPTCHA bypass.
- AI copies durations/dates as text; parse_duration/parse_due turn them into numbers.
- The page reader never clicks links and never presses Back. If a click changes the page path, it stops
  expanding (real bug 29 Sep: history.back() on Striver's sheet sent the tab to the previous page).
  Sections that put state in ?query/#hash are fine.
- /health returns the API version: check it after a deploy (0.5.0+ has course sync).
- Items that can't be sized honestly (assignment with no length) go to `unsized`, never a made-up number.
- A course is identified by `source_key(url)` (schemas/source.py, mirrored in extension watch.js courseKey):
  YouTube playlists by list id, other pages by host+path+meaningful query (utm_/si/index/tab... ignored).
  Syncing the same course again REPLACES it and keeps the first URL as its id; GET /sources heals old
  duplicates (real bug 9 Oct: add-by-link + "Sync this page" stored the playlist twice). A YouTube playlist
  synced from the tab uses the YouTube API list (exact) when YOUTUBE_API_KEY is set, not the page text.

## Frontend rules
- Monochrome only: ink / paper / muted / line / soft tokens (app/globals.css). State via weight, rules and
  symbols (✓ ! ○), never colour. Dark mode = same tokens inverted.
- Texture and motion instead of colour: .hatch = late / not yet, .strike draws itself when ticked,
  .pop / .rise / .grow for feedback (all off under prefers-reduced-motion).
- Plan page: Today card (ring + checkboxes) → verdict (need/have/finish + load bar) → milestone timeline
  (bar = worked on, tall tick = due, hatched = late) → week strip (days under their weekday) → later weeks.
- Pages only call the API (lib/api.ts adds the Supabase token). No planning logic in the frontend.
- Destructive actions are two-step (DeleteGoal: "Delete" -> "Yes, delete"); deleting a goal cascades its check-ins.
- tests/fake_ai_server.py runs the real API with a scripted AI, for UI work without an AI key.

## Out of scope for v0
Web research agent, PDF/course parsing, calendar sync, ML models, non-learning goal types.
