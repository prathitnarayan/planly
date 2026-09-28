"""
Feasibility: can every milestone be finished by its own due date?

How it works (plain simulation, no magic):
  Walk day by day from the start date. Each day has some minutes of capacity.
  Spend them on the milestone that is READY (prerequisites done, start date
  reached) and DUE SOONEST. This rule is called "earliest deadline first" (EDF).
  Record the day each milestone finishes, then compare with its due date.

If something is late, the planner never pretends. It returns honest options:
  1. how many more free-slot hours per week would make everything fit
     (and whether that's realistic)
  2. the date everything would actually finish
  3. which OPTIONAL milestones could be dropped — required ones never are
The user picks. The LLM may explain the result, but never computes it.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel

from app.planners.capacity import CapacityProfile, available_minutes
from app.schemas.blueprint import GoalBlueprint

MAX_PROJECTION_DAYS = 3 * 365   # stop simulating after 3 years
MAX_SCALE = 10.0                # "what if you had 10x the time" is the search ceiling
REALISTIC_EXTRA_RATIO = 0.5     # adding more than +50% of your current free time = unrealistic


# ---------- plan items: blueprint milestones with real dates ----------

class PlanItem(BaseModel):
    key: str
    name: str
    minutes: float
    depends_on: list[str]
    required: bool
    earliest_start: date | None
    due: date | None
    hard: bool = True   # hard = can't slip (graded submission); soft = self-imposed
    # Already-done work, in ESTIMATE minutes. The scheduler skips this much from the
    # front of learn -> practice -> project, so it continues where the user left off.
    done_minutes: float = 0.0


def resolve_items(
    blueprint: GoalBlueprint,
    key_dates: dict[str, date],
    goal_deadline: date | None,
    personal_multiplier: float = 1.0,
    soft_keys: set[str] | None = None,
    deadline_hard: bool = True,
) -> list[PlanItem]:
    """
    Turn key-date references into real dates. Result is in dependency order.
    soft_keys: key dates that may slip. Everything else is hard by default.
    """
    soft_keys = soft_keys or set()
    by_key = {m.key: m for m in blueprint.milestones}
    items = []
    for key in blueprint.topological_order():
        m = by_key[key]
        for ref in (m.start_after, m.due_by):
            if ref and ref not in key_dates:
                raise ValueError(f"milestone '{key}' uses unknown key date '{ref}'")
        start = key_dates[m.start_after] + timedelta(days=1) if m.start_after else None
        due = key_dates[m.due_by] + timedelta(days=m.due_offset_days) if m.due_by else goal_deadline
        items.append(PlanItem(
            key=key,
            name=m.name,
            minutes=m.estimated_hours * 60 * personal_multiplier,
            depends_on=m.depends_on,
            required=m.required,
            earliest_start=start,
            due=due,
            hard=(m.due_by not in soft_keys) if m.due_by else deadline_hard,
        ))
    return items


Allocation = tuple[date, str, float]   # (day, milestone key, minutes)


def simulate(
    items: list[PlanItem],
    profile: CapacityProfile,
    start: date,
    scale: float = 1.0,
    record: list[Allocation] | None = None,
) -> dict[str, date | None]:
    """
    Finish day per milestone key (None = didn't finish within 3 years).
    If `record` is given, every (day, milestone, minutes) decision is appended to it —
    that's what the scheduler turns into sessions, so plan and check always agree.
    """
    order = {it.key: i for i, it in enumerate(items)}
    remaining = {it.key: it.minutes for it in items}
    done_on: dict[str, date] = {}
    day = start
    for _ in range(MAX_PROJECTION_DAYS):
        if len(done_on) == len(items):
            break
        capacity = profile.sustainable_minutes_on(day, scale)
        while capacity > 0:
            ready = [
                it for it in items
                if it.key not in done_on
                and all(d in done_on for d in it.depends_on)
                and (it.earliest_start is None or it.earliest_start <= day)
            ]
            if not ready:
                break
            it = min(ready, key=lambda i: (i.due or date.max, order[i.key]))
            spent = min(capacity, remaining[it.key])
            if record is not None:
                record.append((day, it.key, spent))
            remaining[it.key] -= spent
            capacity -= spent
            if remaining[it.key] <= 1e-9:
                done_on[it.key] = day
        day += timedelta(days=1)
    return {it.key: done_on.get(it.key) for it in items}


def _late_keys(items: list[PlanItem], finishes: dict[str, date | None]) -> list[str]:
    return [
        it.key for it in items
        if finishes[it.key] is None or (it.due is not None and finishes[it.key] > it.due)
    ]


# ---------- results ----------

class MilestoneForecast(BaseModel):
    key: str
    name: str
    hours: float
    required: bool
    earliest_start: date | None
    due: date | None
    projected_finish: date | None
    late: bool
    days_late: int | None = None   # None = on time, or never finishes
    hard: bool = True


class Options(BaseModel):
    extra_hours_per_week: float | None   # free-slot hours to ADD per week; None = can't fix this way
    extra_hours_realistic: bool          # False if it means adding > 50% more free time
    earliest_finish: date | None         # when everything would actually be done
    later_finish_ok: bool                # False if any late item has a HARD deadline
    hard_late: list[str]                 # late items whose deadline can't slip
    optional_cuts: list[str]             # optional milestones (+ anything depending on them)
    feasible_after_cuts: bool


class FeasibilityResult(BaseModel):
    feasible: bool
    required_hours: float
    available_hours: float     # between start and the last due date
    gap_hours: float           # positive = spare time, negative = deficit
    weeks_left: float
    last_due: date
    projected_finish: date | None
    milestones: list[MilestoneForecast]
    late: list[str]
    options: Options | None = None   # only when not feasible


def _days_late(it: PlanItem, finish: date | None) -> int | None:
    if finish is None or it.due is None or finish <= it.due:
        return None
    return (finish - it.due).days


def _min_extra_hours(items, profile, start) -> float | None:
    """Smallest weekly slot-hours to add so nothing is late (binary search)."""
    weekly_max = profile.weekly_summary()["max_hours"]
    if weekly_max <= 0 or _late_keys(items, simulate(items, profile, start, MAX_SCALE)):
        return None
    lo, hi = 1.0, MAX_SCALE
    for _ in range(40):
        mid = (lo + hi) / 2
        if _late_keys(items, simulate(items, profile, start, mid)):
            lo = mid
        else:
            hi = mid
    return round((hi - 1) * weekly_max, 1)


def _optional_cuts(items: list[PlanItem]) -> list[str]:
    """Optional milestones, plus everything that depends on them."""
    cut = {it.key for it in items if not it.required}
    changed = True
    while changed:
        changed = False
        for it in items:
            if it.key not in cut and any(d in cut for d in it.depends_on):
                cut.add(it.key)
                changed = True
    return [it.key for it in items if it.key in cut]


def check_feasibility(
    blueprint: GoalBlueprint,
    profile: CapacityProfile,
    start: date,
    deadline: date | None = None,
    personal_multiplier: float = 1.0,
    key_dates: dict[str, date] | None = None,
    soft_keys: set[str] | None = None,
    deadline_hard: bool = True,
) -> FeasibilityResult:
    """
    deadline: overall goal deadline — used for milestones with no due date of their own.
    key_dates: the user's dates (e.g. {"a2_due": date(2026, 10, 15)}), referenced by milestones.
    personal_multiplier: learned later from task_executions.
        1.3 means this user usually takes 30% longer than estimated.
    """
    items = resolve_items(
        blueprint, key_dates or {}, deadline, personal_multiplier, soft_keys, deadline_hard
    )
    dues = [it.due for it in items if it.due]
    if not dues:
        raise ValueError("no deadline: give the goal a deadline or milestones due dates")
    if max(dues) < start:
        raise ValueError("deadline is before start date")
    return check_items(items, profile, start)


def check_items(items: list[PlanItem], profile: CapacityProfile, start: date) -> FeasibilityResult:
    """
    The check itself, on already-resolved items. Used directly by replanning, where
    items are 'what's left' and some may already be overdue (due before today).
    """
    dues = [it.due for it in items if it.due]
    last_due = max(dues) if dues else start
    finishes = simulate(items, profile, start)
    late = _late_keys(items, finishes)
    required_min = sum(it.minutes for it in items)
    available_min = available_minutes(profile, start, last_due)
    finish_all = (
        None if any(f is None for f in finishes.values())
        else max(finishes.values(), default=start)
    )

    result = FeasibilityResult(
        feasible=not late,
        required_hours=round(required_min / 60, 1),
        available_hours=round(available_min / 60, 1),
        gap_hours=round((available_min - required_min) / 60, 1),
        weeks_left=max(0.0, round(((last_due - start).days + 1) / 7, 1)),
        last_due=last_due,
        projected_finish=finish_all,
        milestones=[
            MilestoneForecast(
                key=it.key, name=it.name, hours=round(it.minutes / 60, 1), required=it.required,
                earliest_start=it.earliest_start, due=it.due,
                projected_finish=finishes[it.key], late=it.key in late,
                days_late=_days_late(it, finishes[it.key]), hard=it.hard,
            )
            # chronological: what finishes first is listed first
            for it in sorted(items, key=lambda i: (finishes[i.key] or date.max, i.due or date.max))
        ],
        late=late,
    )
    if result.feasible:
        return result

    extra = _min_extra_hours(items, profile, start)
    weekly_max = profile.weekly_summary()["max_hours"]
    cuts = _optional_cuts(items)
    kept = [it for it in items if it.key not in cuts]
    feasible_after_cuts = bool(cuts) and not _late_keys(kept, simulate(kept, profile, start))

    hard_late = [it.key for it in items if it.key in late and it.hard]
    result.options = Options(
        extra_hours_per_week=extra,
        extra_hours_realistic=extra is not None and extra <= weekly_max * REALISTIC_EXTRA_RATIO,
        earliest_finish=finish_all,
        later_finish_ok=not hard_late,
        hard_late=hard_late,
        optional_cuts=cuts,
        feasible_after_cuts=feasible_after_cuts,
    )
    return result
