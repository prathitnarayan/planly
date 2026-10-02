"""
"Something came up": telling Planly that life got in the way, so it plans around it instead of
counting it against you. Pure code.

Three kinds:
  minutes      "I lost 1 hour today"         -> up to that much of today's unticked work is excused
  rest_of_day  "Skip the rest of today"       -> all of today's unticked work is excused
  days         "Away Fri-Sun" (sick, travel)  -> those days get no study time at all

How it's applied:
- TODAY is never re-planned (the list you saw this morning stays, ticks keep their ids). Unticked
  sessions are marked EXCUSED instead. You can still tick one if you do it anyway.
- FUTURE days in a "days" break get zero free time, so the planner simply routes around them.
- An excused session is not a miss: the streak doesn't reset, it doesn't teach Planly that this
  weekday is unreliable, and the evening message doesn't nag about it. The work itself still has
  to happen, so it moves to the coming days, spread out like any other leftover.
- Only today and later can be excused: a day that's already over has been judged. Ticked sessions
  are never excused (a tick is a claim and is still checked against the evidence).
- Nothing is hidden: the last 30 days' excuses are counted and shown.

Checks, because an excuse can't be proven (nobody uploads a doctor's note to a study planner):
- SAY IT WHEN IT HAPPENS: an excuse only covers sessions that hadn't ended (+1 h grace) when it was
  made. "Rest of today" at 23:30 after doing nothing excuses nothing.
- ALLOWANCE: same-day excuses get 4 free days per 30 (lost time = half a day). Past that, the work
  still moves (planning stays honest) but it counts as missed for the streak and for learning.
- TELLING AHEAD IS FREE: days marked off before they start just get no study time. Nothing to fake.
- PATTERNS ARE NAMED: the same weekday excused again and again isn't "sudden" — Planly says so and
  suggests changing that day's free time instead.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

BreakKind = Literal["minutes", "rest_of_day", "days"]
Reason = Literal["health", "family", "work", "travel", "other"]

MIN_MINUTES, MAX_MINUTES = 15, 12 * 60
MAX_DAYS_AHEAD = 60          # a break can't start further out than this
MAX_SPAN_DAYS = 30           # nor last longer
KEEP_DAYS = 120              # older breaks are dropped from storage
TALLY_DAYS = 30
ALLOWANCE_DAYS = 4.0         # free same-day excuses per 30 days, in days
MINUTES_COST = 0.5           # "lost some time" uses half a day of the allowance
GRACE = timedelta(hours=1)   # a session can still be excused up to 1 h after it ended
PATTERN_WEEKS = 5            # same weekday excused >= 3 times in the last 5 weeks = a pattern
PATTERN_MIN = 3
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class Break(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: BreakKind
    created_day: date                # the user's local date when they said it
    first: date
    last: date
    minutes: int | None = None       # kind == "minutes"
    reason: Reason | None = None
    note: str | None = Field(default=None, max_length=120)
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    at_local: time | None = None     # the user's clock when they said it (for "say it when it happens")
    free: bool = True                # within the allowance; False = moves the work but counts as missed

    @field_validator("note", mode="before")
    @classmethod
    def _strip(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _shape(self) -> "Break":
        if self.last < self.first:
            raise ValueError("the last day is before the first")
        if self.kind == "minutes":
            if not self.minutes or not MIN_MINUTES <= self.minutes <= MAX_MINUTES:
                raise ValueError(f"minutes must be {MIN_MINUTES}-{MAX_MINUTES}")
            if self.first != self.created_day or self.last != self.created_day:
                raise ValueError("lost time is for today only")
        if self.kind == "rest_of_day" and (self.first != self.created_day or self.last != self.created_day):
            raise ValueError("'rest of today' is for today only")
        if self.first < self.created_day:
            raise ValueError("a day that's already over can't be excused")
        return self


def same_day(b: Break) -> bool:
    """Excuses work that was already on today's list (as opposed to days off told in advance)."""
    return b.kind != "days" or b.first == b.created_day


def cost(b: Break) -> float:
    """Allowance used: only the same-day part counts; telling Planly ahead is free."""
    if not same_day(b):
        return 0.0
    return MINUTES_COST if b.kind == "minutes" else 1.0


def allowance_left(breaks: list[Break], today: date) -> float:
    since = today - timedelta(days=TALLY_DAYS - 1)
    used = sum(cost(b) for b in breaks if b.free and b.created_day >= since)
    return max(0.0, ALLOWANCE_DAYS - used)


def made_on(breaks: list[Break], day: date) -> list[Break]:
    """The same-day excuses made on `day`, oldest first (a break made earlier turns the day off instead)."""
    return sorted([b for b in breaks if b.created_day == day and b.first <= day <= b.last and same_day(b)],
                  key=lambda b: b.at)


def off_days(breaks: list[Break]) -> set[date]:
    """Days with no study time at all: every day of a 'days' break AFTER the day it was made."""
    out = set()
    for b in breaks:
        if b.kind != "days":
            continue
        d = max(b.first, b.created_day + timedelta(days=1))
        while d <= b.last:
            out.add(d)
            d += timedelta(days=1)
    return out


def _still_open(s, at_local: time | None) -> bool:
    """Say it when it happens: the session hadn't ended (+ grace) when the excuse was made."""
    if at_local is None or not s.end:
        return True
    end = datetime.combine(date.min, time.fromisoformat(s.end)) + GRACE
    if s.start and s.end < s.start:                 # runs past midnight
        end += timedelta(days=1)
    return datetime.combine(date.min, at_local) <= end


def excused_ids(sessions, ticked: set[str], todays: list[Break]) -> dict[str, bool]:
    """Unticked sessions excused by today's breaks -> free? (True = within the allowance).
    'Rest of today' covers every session still open when said; lost minutes cover the LATEST open
    sessions first (lost time eats into what's ahead), a session counts if half of it is covered."""
    out: dict[str, bool] = {}
    for b in todays:
        open_ = [s for s in sessions if s.id not in ticked and s.id not in out and _still_open(s, b.at_local)]
        if b.kind != "minutes":
            out.update({s.id: b.free for s in open_})
            continue
        left = b.minutes or 0
        for s in sorted(open_, key=lambda s: (s.start or "", s.id), reverse=True):
            if left < s.minutes / 2:
                break
            out[s.id] = b.free
            left -= s.minutes
    return out


def pattern(breaks: list[Break], today: date) -> str | None:
    """The same weekday excused again and again isn't sudden: name it, suggest a fix."""
    since = today - timedelta(weeks=PATTERN_WEEKS)
    days = {b.first for b in breaks if same_day(b) and b.first >= since}
    by_wd: dict[int, int] = {}
    for d in days:
        by_wd[d.weekday()] = by_wd.get(d.weekday(), 0) + 1
    wd = max(by_wd, key=by_wd.get, default=None)
    if wd is None or by_wd[wd] < PATTERN_MIN:
        return None
    return (f"{WEEKDAYS[wd]}s were excused {by_wd[wd]} times in the last {PATTERN_WEEKS} weeks — that's a pattern, "
            f"not a surprise. Lower your free time on {WEEKDAYS[wd]}s so the plan stops counting on it.")


class Tally(BaseModel):
    times: int            # breaks made in the last 30 days
    days_off: int         # whole days off in the last 30 days (incl. rest-of-day)
    minutes: int          # "lost time" minutes in the last 30 days
    allowance_left: float # free same-day excuse days left (of ALLOWANCE_DAYS)
    over_allowance: int   # excuses past the allowance (counted as missed)
    pattern: str | None


def tally(breaks: list[Break], today: date) -> Tally:
    since = today - timedelta(days=TALLY_DAYS - 1)
    recent = [b for b in breaks if b.created_day >= since]
    days = set()
    for b in recent:
        if b.kind != "minutes":
            d = b.first
            while d <= min(b.last, today):
                days.add(d)
                d += timedelta(days=1)
    return Tally(times=len(recent), days_off=len(days),
                 minutes=sum(b.minutes or 0 for b in recent if b.kind == "minutes"),
                 allowance_left=allowance_left(breaks, today),
                 over_allowance=sum(1 for b in recent if not b.free), pattern=pattern(breaks, today))


def prune(breaks: list[Break], today: date) -> list[Break]:
    return [b for b in breaks if b.last >= today - timedelta(days=KEEP_DAYS)]


def describe(b: Break) -> str:
    if b.kind == "minutes":
        h, m = divmod(b.minutes or 0, 60)
        return f"Lost {f'{h} h ' if h else ''}{f'{m} min' if m else ''}".strip() + f" · {b.first:%a %d %b}"
    if b.kind == "rest_of_day":
        return f"Rest of the day off · {b.first:%a %d %b}"
    if b.first == b.last:
        return f"Day off · {b.first:%a %d %b}"
    return f"Away · {b.first:%a %d %b} – {b.last:%a %d %b}"
