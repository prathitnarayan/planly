"""
When to message the user, and what to say. Pure code (sending lives in core/telegram.py).

At most two messages a day, both optional:
  morning  today's sessions, one tap to tick each
  evening  only if something is still unticked ("done? tap it; otherwise it moves on")
Each is sent once per local day, inside a window after its time (so a late cron run still
sends it, but a 3pm "good morning" never happens).
"""

from __future__ import annotations

import html
import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.schemas.integrations import NotifyPrefs

MORNING_WINDOW = timedelta(hours=3)
EVENING_WINDOW = timedelta(hours=2)


def local_now(now_utc: datetime, tz: str) -> datetime:
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("Asia/Kolkata")
    return now_utc.astimezone(zone)


def due(now_utc: datetime, prefs: NotifyPrefs, tz: str) -> list[str]:
    now = local_now(now_utc, tz)
    today = now.date()
    out = []
    for kind, on, at, last, window in (
        ("morning", prefs.morning, prefs.morning_at, prefs.last_morning, MORNING_WINDOW),
        ("evening", prefs.evening, prefs.evening_at, prefs.last_evening, EVENING_WINDOW),
    ):
        start = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
        if on and last != today and start <= now < start + window:
            out.append(kind)
    return out


def _mins(m: int) -> str:
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m} min"


def _session_line(s) -> str:
    when = f"{s.start}–{s.end}" if s.start else f"{s.minutes} min"
    mark = "✓" if s.done else ("⊘" if s.locked else "○")
    line = f"{mark} {html.escape(when)}  {html.escape(s.milestone_name)}"
    if s.items:
        line += "\n     " + html.escape(", ".join(i.title for i in s.items[:4]) + (" …" if len(s.items) > 4 else ""))
    return line


def build_message(kind: str, day: date, goals: list[tuple[str, str, object]]) -> tuple[str | None, list]:
    """goals: [(goal_id, goal_title, TodayView)]. Returns (html text, buttons) or (None, []) when
    there's nothing worth sending. Buttons: [(label, {goal_id, session_id, day})]."""
    blocks, buttons = [], []
    planned = done = open_ = 0
    for gid, title, view in goals:
        sessions = view.sessions if kind == "morning" else [s for s in view.sessions if not s.done]
        if not sessions:
            continue
        blocks.append(f"<b>{html.escape(title)}</b>\n" + "\n".join(_session_line(s) for s in sessions))
        for s in view.sessions:
            planned += s.minutes
            done += s.minutes if s.done else 0
            open_ += 0 if s.done else 1
        for s in sessions:
            label = ("✓ " if s.done else "⊘ " if s.locked else "Tick: ") + s.milestone_name
            label += f" {s.start}" if s.start else ""
            buttons.append((label[:60], {"goal_id": gid, "session_id": s.id, "day": day.isoformat(),
                                         "locked": s.locked and not s.done}))
    if not blocks:
        return None, []
    if kind == "morning":
        head = f"<b>Today · {day:%a %d %b}</b> — {_mins(planned)} planned"
        foot = "Tap a task when it's done. ⊘ = ticks itself when watched."
    else:
        head = f"<b>Still open today</b> — {open_} session{'s' if open_ != 1 else ''}"
        foot = "Done? Tap it. Anything unticked moves to the coming days, spread out."
    return f"{head}\n\n" + "\n\n".join(blocks) + f"\n\n<i>{foot}</i>", buttons


def new_token() -> str:
    return secrets.token_urlsafe(6)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
