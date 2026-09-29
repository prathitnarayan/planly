"""
Scheduler: turn the plan into real sessions in the user's free slots,
grouped into weekly sprints.

It does NOT decide anything new. It replays the exact same day-by-day
simulation the feasibility check uses (earliest deadline first), records
"on this day, spend N minutes on milestone X", then:
  1. splits each milestone's minutes by kind, in order: learn -> practice ->
     project -> revise -> assess -> buffer (you learn before you practise)
  2. places the minutes into that day's slots, earliest slot first
  3. groups days into Monday-Sunday sprints
So the schedule and the feasibility verdict can never disagree.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from pydantic import BaseModel

from app.planners.capacity import CapacityProfile
from app.planners.feasibility import Allocation, PlanItem, simulate
from app.schemas.blueprint import GoalBlueprint, TaskKind

KIND_ORDER = list(TaskKind)   # learn, practice, project, revise, assess, buffer
MIN_SESSION_MINUTES = 15      # nobody does a 2-minute "practice" session


class Session(BaseModel):
    day: date
    start: time | None          # None when the day has no slot times (a one-off override)
    end: time | None
    minutes: int
    milestone_key: str
    milestone_name: str
    deliverable: str | None
    kind: TaskKind


class DayPlan(BaseModel):
    day: date
    capacity_minutes: int
    sessions: list[Session]

    @property
    def planned_minutes(self) -> int:
        return sum(s.minutes for s in self.sessions)


class Sprint(BaseModel):
    number: int
    start: date
    end: date
    capacity_minutes: int
    planned_minutes: int
    working_on: list[str]          # milestone names with sessions this week
    finishing: list[str]           # milestone keys that finish this week
    definition_of_done: list[str]  # done_criteria of what finishes this week
    days: list[DayPlan]


class Schedule(BaseModel):
    sprints: list[Sprint]
    finish: date | None
    unscheduled_minutes: int       # work that never fit (0 when everything finishes)


# ---------- helpers ----------

def _minutes_between(a: time, b: time) -> int:
    return int((datetime.combine(date.min, b) - datetime.combine(date.min, a)).total_seconds() // 60)


def _plus(t: time, minutes: int) -> time:
    return (datetime.combine(date.min, t) + timedelta(minutes=minutes)).time()


def _kind_queues(blueprint: GoalBlueprint, items: list[PlanItem]) -> dict[str, list[list]]:
    """
    Per milestone: [[kind, minutes_left], ...] in KIND_ORDER.
    Skips the item's done_minutes from the front (you don't re-learn after doing
    the learning), then scales what's left to the item's minutes (multiplier etc.).
    """
    by_key = {m.key: m for m in blueprint.milestones}
    queues = {}
    for it in items:
        m = by_key[it.key]
        queue = [[kind, m.hours_by_kind[kind] * 60] for kind in KIND_ORDER if kind in m.hours_by_kind]
        _take_kinds(queue, it.done_minutes)
        left = sum(q[1] for q in queue)
        if not queue or left <= 1e-9:
            # everything "estimated" is used up but work remains (overrun): call it the last kind
            last_kind = KIND_ORDER[max(KIND_ORDER.index(k) for k in m.hours_by_kind)]
            queue, left = [[last_kind, it.minutes]], it.minutes
        factor = it.minutes / left
        queues[it.key] = [[kind, minutes * factor] for kind, minutes in queue]
    return queues


def _take_kinds(queue: list[list], minutes: float) -> list[tuple[TaskKind, float]]:
    """Consume `minutes` from a milestone's kind queue -> [(kind, minutes), ...]."""
    out = []
    while minutes > 1e-9 and queue:
        kind, left = queue[0]
        used = min(left, minutes)
        out.append((kind, used))
        minutes -= used
        queue[0][1] -= used
        if queue[0][1] <= 1e-9:
            queue.pop(0)
    return out


def _merge_small(chunks: list[tuple[str, TaskKind, float]]) -> list[tuple[str, TaskKind, float]]:
    """
    Fold chunks shorter than MIN_SESSION_MINUTES into a neighbouring chunk of the SAME
    milestone (the kind label changes slightly; total minutes per milestone don't).
    A short chunk with no same-milestone neighbour stays — it's the tail of a milestone.
    """
    out = [list(c) for c in chunks]
    i = 0
    while i < len(out):
        key, _, minutes = out[i]
        if minutes < MIN_SESSION_MINUTES:
            if i > 0 and out[i - 1][0] == key:
                out[i - 1][2] += minutes
                out.pop(i)
                continue
            if i + 1 < len(out) and out[i + 1][0] == key:
                out[i + 1][2] += minutes
                out.pop(i)
                continue
        i += 1
    return [tuple(c) for c in out]


def _free_windows(slots, taken) -> list[list]:
    """Slot windows minus times already used by higher-priority goals -> [[start, minutes]]."""
    busy = sorted((a, b) for a, b, _ in taken if a is not None and b is not None)
    out = []
    for s in slots:
        cur, end = s.start, s.end
        for a, b in busy:
            if b <= cur or a >= end:
                continue
            if a > cur:
                out.append([cur, _minutes_between(cur, a)])
            cur = max(cur, b)
        if cur < end:
            out.append([cur, _minutes_between(cur, end)])
    return [w for w in out if w[1] > 0]


def _place_in_slots(day: date, chunks, profile: CapacityProfile, items_by_key, deliverable_of):
    """chunks: [(milestone_key, kind, minutes)] -> timed sessions, earliest slot first."""
    slots = sorted((s for s in profile.slots if s.weekday == day.weekday()), key=lambda s: s.start)
    use_times = bool(slots) and day not in profile.overrides
    free = _free_windows(slots, profile.taken.get(day, [])) if use_times else []
    sessions = []
    for key, kind, minutes in chunks:
        minutes = round(minutes)
        while minutes > 0:
            if use_times and free:
                start, room = free[0]
                length = min(room, minutes)
                if length <= 0:
                    free.pop(0)
                    continue
                sessions.append(Session(
                    day=day, start=start, end=_plus(start, length), minutes=length,
                    milestone_key=key, milestone_name=items_by_key[key].name,
                    deliverable=deliverable_of[key], kind=kind,
                ))
                free[0] = [_plus(start, length), room - length]
                if free[0][1] <= 0:
                    free.pop(0)
            else:
                length = minutes
                sessions.append(Session(
                    day=day, start=None, end=None, minutes=length,
                    milestone_key=key, milestone_name=items_by_key[key].name,
                    deliverable=deliverable_of[key], kind=kind,
                ))
            minutes -= length
    # merge back-to-back sessions of the same milestone + kind
    merged: list[Session] = []
    for s in sessions:
        prev = merged[-1] if merged else None
        if (prev and prev.milestone_key == s.milestone_key and prev.kind == s.kind
                and prev.end is not None and prev.end == s.start):
            prev.end, prev.minutes = s.end, prev.minutes + s.minutes
        else:
            merged.append(s)
    return merged


# ---------- main ----------

def build_schedule(
    blueprint: GoalBlueprint,
    items: list[PlanItem],
    profile: CapacityProfile,
    start: date,
) -> Schedule:
    record: list[Allocation] = []
    finishes = simulate(items, profile, start, record=record)

    items_by_key = {it.key: it for it in items}
    deliverable_of = {m.key: m.deliverable for m in blueprint.milestones}
    criteria_of = {m.key: m.done_criteria for m in blueprint.milestones}
    queues = _kind_queues(blueprint, items)

    # day -> [(key, kind, minutes)]
    per_day: dict[date, list[tuple[str, TaskKind, float]]] = {}
    for day, key, minutes in record:
        for kind, m in _take_kinds(queues[key], minutes):
            per_day.setdefault(day, []).append((key, kind, m))

    last_day = max(per_day) if per_day else start
    sprints: list[Sprint] = []
    week_start = start
    number = 1
    while week_start <= last_day:
        week_end = week_start + timedelta(days=6 - week_start.weekday())  # through Sunday
        days = []
        d = week_start
        while d <= week_end:
            days.append(DayPlan(
                day=d,
                capacity_minutes=profile.sustainable_minutes_on(d),
                sessions=_place_in_slots(
                    d, _merge_small(per_day.get(d, [])), profile, items_by_key, deliverable_of
                ),
            ))
            d += timedelta(days=1)
        finishing = [k for k, f in finishes.items() if f and week_start <= f <= week_end]
        working_on = []
        for day_plan in days:
            for s in day_plan.sessions:
                if s.milestone_name not in working_on:
                    working_on.append(s.milestone_name)
        sprints.append(Sprint(
            number=number,
            start=week_start,
            end=week_end,
            capacity_minutes=sum(dp.capacity_minutes for dp in days),
            planned_minutes=sum(dp.planned_minutes for dp in days),
            working_on=working_on,
            finishing=finishing,
            definition_of_done=[c for k in finishing for c in criteria_of[k]],
            days=days,
        ))
        week_start = week_end + timedelta(days=1)
        number += 1

    done_minutes = sum(m for _, _, m in record)
    total = sum(it.minutes for it in items)
    finish = None if any(f is None for f in finishes.values()) else max(finishes.values(), default=start)
    return Schedule(
        sprints=sprints,
        finish=finish,
        unscheduled_minutes=max(0, round(total - done_minutes)),
    )
