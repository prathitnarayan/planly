"""
Planly course sync: reads your real course pages and turns them into planned work.

  cd backend
  pip install -r requirements-sync.txt && playwright install chrome   # once (or: playwright install chromium)

  python -m sync login                 # browser opens on every site's login page; sign in; Enter
  python -m sync login iitm udemy      # just these
  python -m sync add <course url>      # pick a goal, read the course, send it to Planly
  python -m sync add <url> --follow 20 # also read up to 20 linked pages of the same course
  python -m sync run                   # re-read everything you've added (put this on a daily timer)
  python -m sync list | remove <url> | platforms

Talks to PLANLY_API_URL from backend/.env (your Render URL, or http://localhost:8000),
signed in as you (same saved session as the check-in script).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from app.core import config
from sync import youtube
from sync.platforms import BY_NAME, PLATFORMS, detect, is_youtube_playlist

WATCHED = Path(__file__).resolve().parents[1] / "data" / "sync.json"


# ---------- Planly API ----------

def _headers() -> dict:
    if config.AUTH_MODE == "dev":
        return {}
    from scripts.session import access_token
    return {"Authorization": f"Bearer {access_token()}"}


def api(method: str, path: str, **kw):
    url = f"{config.PLANLY_API_URL}{path}"
    try:
        # long timeout: a big course = several AI calls; a sleeping Render instance = ~50s
        r = httpx.request(method, url, headers=_headers(), timeout=600, **kw)
    except httpx.HTTPError as e:
        sys.exit(f"Can't reach Planly at {config.PLANLY_API_URL} ({e.__class__.__name__}). "
                 "Is PLANLY_API_URL right in backend/.env, and is the backend running?")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        sys.exit(f"Planly said {r.status_code}: {detail}")
    return r.json()


# ---------- the watched list (which course feeds which goal) ----------

def load_watched() -> list[dict]:
    return json.loads(WATCHED.read_text()) if WATCHED.exists() else []


def save_watched(rows: list[dict]) -> None:
    WATCHED.parent.mkdir(parents=True, exist_ok=True)
    WATCHED.write_text(json.dumps(rows, indent=2))


def pick_goal() -> str:
    goals = api("GET", "/goals")
    if not goals:
        sys.exit("No goals yet. Create one in the app first, then add courses to it.")
    for i, g in enumerate(goals, 1):
        print(f"  {i}. {g['title']}")
    while True:
        choice = input("Which goal is this course for? number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(goals):
            return goals[int(choice) - 1]["id"]


# ---------- reading + sending ----------

def page_payload(reads, platform: str) -> tuple[dict | None, str | None]:
    """(body for /sources/page, problem). Adds exact lengths of embedded YouTube videos."""
    from sync.browser import PageRead  # noqa: F401  (type only)

    first = reads[0]
    if first.needs_login:
        return None, f"not signed in to {platform}. Run: python -m sync login {platform}"
    if first.challenged:
        return None, f"{platform} showed a 'are you human' check. Run with --show and solve it once."
    parts = [f"=== PAGE: {r.title} ({r.final_url}) ===\n{r.text}" for r in reads]
    ids = list(dict.fromkeys(i for r in reads for i in r.youtube_ids))
    if ids:
        try:
            vids = youtube.videos_by_id(ids)
            lines = [f"- {v['title']} | {youtube.clock(v['seconds'])}" for v in vids.values() if v["seconds"]]
            if lines:
                parts.append("=== Embedded videos (exact lengths) ===\n" + "\n".join(lines))
        except Exception as e:   # durations are a bonus; the page text still goes
            print(f"  (couldn't read YouTube lengths: {e.__class__.__name__})")
    return {"url": first.url, "platform": platform, "title": first.title or None,
            "text": "\n\n".join(parts)}, None


def sync_one(row: dict, show: bool = False) -> bool:
    url, goal = row["url"], row["goal_id"]
    plat = detect(url)
    print(f"\n→ {plat.label}: {url}")
    if is_youtube_playlist(url):
        title, vids = youtube.playlist(url)
        items = [{"title": v["title"], "kind": "video",
                  "minutes": -(-v["seconds"] // 60) if v["seconds"] else None,
                  "duration_text": youtube.clock(v["seconds"]),
                  "url": f"https://www.youtube.com/watch?v={v['id']}"} for v in vids]
        result = api("POST", f"/goals/{goal}/sources",
                     json={"url": url, "platform": "youtube", "title": title, "items": items})
    else:
        from sync.browser import read_course
        reads = read_course(url, headless=not show, follow=row.get("follow", 0))
        if reads[0].challenged and not show:
            print("  site wants a human check: opening the browser so you can pass it...")
            reads = read_course(url, headless=False, follow=row.get("follow", 0))
        body, problem = page_payload(reads, plat.name)
        if problem:
            print(f"  ✗ {problem}")
            return False
        print(f"  read {len(reads)} page(s), {len(body['text']):,} characters. Planly is listing the items...")
        result = api("POST", f"/goals/{goal}/sources/page", json=body)
    for line in result["messages"]:
        print(f"  {line}")
    for kd in result["key_dates_added"]:
        print(f"  + deadline: {kd}")
    for kd in result["key_dates_moved"]:
        print(f"  ~ deadline moved: {kd}")
    return True


# ---------- commands ----------

def cmd_login(args) -> None:
    from sync.browser import login
    names = args.platforms or [p.name for p in PLATFORMS if p.login_url]
    urls = []
    for n in names:
        if n in BY_NAME:
            if BY_NAME[n].login_url:
                urls.append(BY_NAME[n].login_url)
        elif n.startswith("http"):
            urls.append(n)
        else:
            sys.exit(f"Unknown site '{n}'. Try: {', '.join(BY_NAME)} (or paste the site's URL)")
    print("Opening:", *urls, sep="\n  ")
    login(urls)


def cmd_add(args) -> None:
    goal = args.goal or pick_goal()
    rows = [r for r in load_watched() if r["url"] != args.url]
    row = {"url": args.url, "goal_id": goal, "follow": args.follow}
    if sync_one(row, show=args.show):
        save_watched(rows + [row])
        print("\nAdded. `python -m sync run` re-reads it (and your other courses) any time.")


def cmd_run(args) -> None:
    rows = load_watched()
    if not rows:
        sys.exit("Nothing added yet: python -m sync add <course url>")
    ok = sum(sync_one(r, show=args.show) for r in rows)
    print(f"\nSynced {ok}/{len(rows)}.")
    if ok < len(rows):
        sys.exit(1)


def cmd_list(_) -> None:
    for r in load_watched():
        print(f"{detect(r['url']).label:22} goal {r['goal_id'][:8]}  follow {r.get('follow', 0):>2}  {r['url']}")


def cmd_remove(args) -> None:
    rows = load_watched()
    row = next((r for r in rows if r["url"] == args.url), None)
    if not row:
        sys.exit("Not in your list (python -m sync list).")
    api("DELETE", f"/goals/{row['goal_id']}/sources", params={"url": args.url})
    save_watched([r for r in rows if r["url"] != args.url])
    print("Removed from the goal and from the sync list.")


def cmd_platforms(_) -> None:
    for p in PLATFORMS:
        how = "exact durations, no login" if p.reader == "youtube" else "page read with your login"
        print(f"{p.name:11} {p.label:24} {how}")
    print("Any other site works too: it's read the same way as the others.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m sync", description="Sync your courses into Planly.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login", help="sign in to your course sites (once)")
    s.add_argument("platforms", nargs="*", help="e.g. iitm udemy (default: all)")
    s.set_defaults(fn=cmd_login)
    s = sub.add_parser("add", help="read a course and attach it to a goal")
    s.add_argument("url")
    s.add_argument("--goal", help="goal id (default: choose from a list)")
    s.add_argument("--follow", type=int, default=0, help="also read N linked pages of the course")
    s.add_argument("--show", action="store_true", help="show the browser while reading")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("run", help="re-read every course you've added")
    s.add_argument("--show", action="store_true")
    s.set_defaults(fn=cmd_run)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("remove")
    s.add_argument("url")
    s.set_defaults(fn=cmd_remove)
    sub.add_parser("platforms").set_defaults(fn=cmd_platforms)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
