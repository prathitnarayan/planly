# Course sync: your real courses → exact workload → plan

Three ways in, easiest first:

| How | For | Setup |
|---|---|---|
| **Chrome extension** (`extension/`) | anything behind a login: IITM, Udemy, Coursera, LeetCode, GFG, Testbook… | load once, see `extension/README.md` |
| **Add by link** on the goal page | YouTube playlists, public course pages | none (YouTube needs `YOUTUBE_API_KEY` on Render) |
| **Terminal tool** (`python -m sync`, below) | the same as the extension, scriptable / scheduled | Python + Playwright on your laptop |

Everything ends up in the same place: the goal's **Your courses** panel, measured hours,
and course deadlines on the plan.

---

## Terminal tool

Runs on **your laptop**, in a real browser that stays signed in to your course sites.
Planly never sees or stores your course passwords: you sign in yourself, once, and the
browser keeps the session in `backend/data/browser/` (gitignored, this laptop only).

It only **reads** what the course page shows you: titles, lengths, deadlines, ticks.
It doesn't download videos, get around DRM, or solve CAPTCHAs.

## One-time setup
```
cd backend
source .venv/bin/activate
pip install -r requirements-sync.txt
playwright install chrome        # uses real Google Chrome, which Google sign-in accepts
```
(If you already have Chrome installed, the `playwright install chrome` step can be skipped.)

In `backend/.env`, set where Planly runs:
```
PLANLY_API_URL=https://planly-u3y3.onrender.com
```

## Use
```
python -m sync login             # tabs open on every site's login page. Sign in, press Enter.
python -m sync add "<course url>"            # pick the goal; the course is read and sent
python -m sync add "<url>" --follow 20       # also read up to 20 linked pages of the course
python -m sync run                           # re-read everything (new lectures, ticks, deadlines)
python -m sync list | remove <url> | platforms
```
Use the page that LISTS the lectures (course contents / curriculum / syllabus / sheet),
not a single lecture. Add `--show` to watch the browser work.

## What happens to it
1. The page text goes to Planly. The AI lists the items, copying lengths and dates as written.
2. **Code** turns "12:34" into minutes and "15 Oct" into a date (the AI never does maths).
3. **Code** sizes the work: video × 1.5 (notes, pauses), problems 20/40/60 min by difficulty,
   tests × 1.5 (attempt + review). Assignments with no stated length are listed, not guessed.
4. Graded work with a date becomes a hard deadline on the goal.
5. The next "generate plan" gets the measured hours, and the hours check warns if a plan
   has fewer hours than your courses actually need.

YouTube playlists skip the browser and the AI: yt-dlp gives exact lengths. Videos embedded
in course pages (IITM lectures) get their exact lengths the same way.

## Daily auto-sync (Mac)
`crontab -e`, then one line (change the path):
```
30 6 * * * cd /Users/you/planly/backend && .venv/bin/python -m sync run >> data/sync.log 2>&1
```
The laptop must be awake at that time. If a site logged you out, the log says
`python -m sync login <site>`.

## Sites
iitm, youtube, striver (takeUforward), coursera, udemy, leetcode, codeforces, codechef,
gfg, rankers (Rankers Gurukul), parmar (Parmar Academy), testbook. Any other site works
the same way (it's read generically, not with a per-site scraper).

## Needs the database column
Run `database/migrations/003_sources.sql` in Supabase (SQL Editor) once.
