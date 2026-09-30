"""
One pool of free time, shared by all of a user's goals.

Before this, every goal had its own copy of your evenings, so two goals could both say
"it fits" while together they needed the same Monday 19:00 twice.

Now goals are in priority order. The first goal plans against your free time as usual;
its sessions (exact times) are then marked as taken; the second goal plans against
what's left; and so on. Same planner, same rules, just a smaller pool for each goal
further down. If a lower goal no longer fits, its verdict says so honestly.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Callable

from app.planners.capacity import CapacityProfile
from app.planners.progress import replan

Taken = dict[date, list[tuple[time | None, time | None, int]]]


def add_sessions(taken: Taken, schedule) -> Taken:
    out = {d: list(v) for d, v in taken.items()}
    for sprint in schedule.sprints:
        for d in sprint.days:
            for s in d.sessions:
                out.setdefault(d.day, []).append((s.start, s.end, s.minutes))
    return out


def shared_pool_taken(higher_goals: list, capacity: CapacityProfile, start: date,
                      items_of: Callable, initial: Taken | None = None) -> Taken:
    """Times used by the higher-priority goals, planned in order, each from what the ones
    above it left. `higher_goals`: goal records (blueprint, checkins, plan_start, integrity).
    `initial`: times already busy before any goal (calendar meetings)."""
    taken: Taken = {d: list(v) for d, v in (initial or {}).items()}
    for h in higher_goals:
        begin = max(start, h.plan_start) if h.plan_start else start
        try:
            plan = replan(h.blueprint, items_of(h), h.checkins,
                          capacity.model_copy(update={"taken": taken}), today=begin,
                          penalties=h.integrity.penalty_minutes)
        except ValueError:
            continue            # a broken goal shouldn't block the others
        taken = add_sessions(taken, plan.schedule)
    return taken


def busy_taken(busy: list[tuple[datetime, datetime]], capacity: CapacityProfile, tz: str,
               first: date, last: date) -> Taken:
    """Calendar busy times (UTC) -> the parts that overlap your free slots, per local day.
    A meeting at 3pm doesn't touch a 7pm study slot; a meeting at 7:30pm takes 7:30-8:00 out."""
    from zoneinfo import ZoneInfo
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("Asia/Kolkata")
    out: Taken = {}
    for b_start, b_end in busy:
        a, b = b_start.astimezone(zone), b_end.astimezone(zone)
        day = a.date()
        while day <= b.date():
            if first <= day <= last:
                day_start = datetime.combine(day, time.min, zone)
                lo = max(a, day_start)
                hi = min(b, day_start + timedelta(days=1))
                for s in capacity.slots:
                    if s.weekday != day.weekday():
                        continue
                    s0 = datetime.combine(day, s.start, zone)
                    s1 = datetime.combine(day, s.end, zone)
                    o0, o1 = max(lo, s0), min(hi, s1)
                    if o1 > o0:
                        out.setdefault(day, []).append((o0.time(), o1.time(), int((o1 - o0).total_seconds() // 60)))
            day += timedelta(days=1)
    # overlapping meetings shouldn't count twice
    merged: Taken = {}
    for day, spans in out.items():
        spans = sorted(spans)
        acc: list[list] = []
        for a, b, _ in spans:
            if acc and a <= acc[-1][1]:
                acc[-1][1] = max(acc[-1][1], b)
            else:
                acc.append([a, b])
        merged[day] = [(a, b, (datetime.combine(day, b) - datetime.combine(day, a)).seconds // 60) for a, b in acc]
    return merged
