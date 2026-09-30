"""
When to message the user, and what to say. Pure code (sending lives in core/telegram.py).

Messages, each optional:
  morning  the day's quote first (the lock-screen glance), today's sessions, habit streaks,
           and "yesterday?" buttons for habits left blank. Sent even with no goals or habits (quote only).
  evening  only if a session is still unticked or a habit isn't answered for today
  nudge    at most one a day, 30 min before the user's own risky time (from their urge log)
Each is sent once per local day, inside a window after its time (so a late cron run still
sends it, but a 3pm "good morning" never happens).
"""

from __future__ import annotations

import html
import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.planners.habits import Habit, HabitStats, RiskTime, word
from app.schemas.integrations import NotifyPrefs

MORNING_WINDOW = timedelta(hours=3)
EVENING_WINDOW = timedelta(hours=2)
NUDGE_WINDOW = timedelta(hours=1)


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


class HabitBrief(BaseModel):
    """One habit as the messages need it: the habit, its numbers, and personal lines (habits.lines)."""
    habit: Habit
    stats: HabitStats
    lines: list[str] = []


def _habit_head(b: HabitBrief) -> str:
    st = b.stats
    s = f"<b>{html.escape(b.habit.shown)}</b> — day {st.current}"
    if st.best > st.current:
        s += f" · best {st.best}"
    if st.window >= 3:
        s += f" · {st.kept_window}/{st.window} days {word(b.habit)}"
    return s


def _ask_row(b: HabitBrief, day: date, when: str) -> list:
    name, h = b.habit.shown, b.habit
    yes = ("✓ clean " if h.kind == "quit" else "✓ did it ") + when
    no = "✗ slipped" if h.kind == "quit" else "✗ missed"
    data = {"t": "h", "habit_id": h.id, "day": day.isoformat()}
    return [(f"{name}: {yes}"[:60], {**data, "kept": True}), (no, {**data, "kept": False})]


def build_message(kind: str, day: date, goals: list[tuple[str, str, object]],
                  habits: list[HabitBrief] | tuple = (), quote: tuple[str, str] | None = None
                  ) -> tuple[str | None, list[list]]:
    """goals: [(goal_id, goal_title, TodayView)]; habits: HabitBrief per active habit; quote: (text, author).
    Returns (html text, button rows) or (None, []) when there's nothing worth sending.
    Button data: tick {goal_id, session_id, day, locked} | habit {t:"h", habit_id, day, kept}.

    The quote goes FIRST: on a lock screen only the first line or two show, so that's the glance."""
    blocks, rows = [], []
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
            rows.append([(label[:60], {"goal_id": gid, "session_id": s.id, "day": day.isoformat(),
                                       "locked": s.locked and not s.done})])

    habit_lines = []
    yesterday = day - timedelta(days=1)
    for b in habits:
        if kind == "morning":
            text = _habit_head(b)
            if b.lines:
                text += f"\n     <i>{html.escape(b.lines[0])}</i>"
            habit_lines.append(text)
            if b.stats.yesterday is None and yesterday >= b.habit.started:
                rows.append(_ask_row(b, yesterday, "yesterday"))
        else:
            if b.stats.today is None:
                q = "clean today?" if b.habit.kind == "quit" else "done today?"
                habit_lines.append(f"<b>{html.escape(b.habit.shown)}</b> — {q} (day {b.stats.current} so far)")
                rows.append(_ask_row(b, day, "today"))
            else:
                mark = "✓" if b.stats.today else "✗"
                habit_lines.append(f"{mark} {html.escape(b.habit.shown)} — day {b.stats.current}")
    ask_today = kind == "evening" and any(b.stats.today is None for b in habits)

    if kind == "morning" and not (blocks or habits or quote):
        return None, []
    if kind == "evening" and not (blocks or ask_today):
        return None, []

    parts = []
    if kind == "morning" and quote:
        parts.append(f"<i>“{html.escape(quote[0])}”</i> — {html.escape(quote[1])}")
    if kind == "morning":
        parts.append(f"<b>Today · {day:%a %d %b}</b>" + (f" — {_mins(planned)} planned" if blocks else ""))
    elif blocks:
        parts.append(f"<b>Still open today</b> — {open_} session{'s' if open_ != 1 else ''}")
    else:
        parts.append("<b>Evening check</b>")
    parts += blocks
    if habit_lines:
        parts.append("\n".join(habit_lines))
    if blocks:
        parts.append("<i>" + ("Tap a task when it's done. ⊘ = ticks itself when watched." if kind == "morning"
                              else "Done? Tap it. Anything unticked moves to the coming days, spread out.") + "</i>")
    return "\n\n".join(parts), rows


def build_nudge(b: HabitBrief, risk: RiskTime) -> tuple[str, list[list]]:
    """The heads-up before their usual risky time. Built only from their own urge log."""
    text = (f"<b>Heads-up · {html.escape(b.habit.shown)}</b>\n"
            f"Your urges usually hit {risk.label}. Day {b.stats.current} so far.")
    why = next((x for x in b.lines if x.startswith("Your reason")), None) or (b.lines[0] if b.lines else None)
    if why:
        text += f"\n<i>{html.escape(why)}</i>"
    return text, [[("🔥 Urge right now — log it", {"t": "u", "habit_id": b.habit.id})]]


def nudge_due(now_local: datetime, risk: RiskTime, last: date | None) -> bool:
    """Inside the hour after the nudge time, once a day."""
    now = now_local.replace(tzinfo=None)
    if last == now.date():
        return False
    # a window starting at 00:00 means a nudge at 23:30 the evening before
    return any(at <= now < at + NUDGE_WINDOW
               for at in (risk.nudge_at(now.date()), risk.nudge_at(now.date() + timedelta(days=1))))


def new_token() -> str:
    return secrets.token_urlsafe(6)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
