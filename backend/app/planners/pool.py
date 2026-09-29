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

from datetime import date, time
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
                      items_of: Callable) -> Taken:
    """Times used by the higher-priority goals, planned in order, each from what the ones
    above it left. `higher_goals`: goal records (blueprint, checkins, plan_start, integrity)."""
    taken: Taken = {}
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
