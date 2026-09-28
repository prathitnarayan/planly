"""
Progress + replanning: close the loop.

  plan -> do (or don't) -> check in -> replan from today

A check-in says what actually happened to a session:
  done     worked the time (actual_minutes > 0)
  partial  worked some of it
  missed   didn't work at all (actual_minutes = 0)
Separately, the user can say a milestone is COMPLETE (finished early or on time),
or give their own "how much is left".

Replanning never guesses. Remaining work per milestone is, in order of trust:
  1. complete                -> 0
  2. user's remaining_minutes -> that
  3. estimate - time spent    -> but if they've spent it all and it's NOT done,
                                 keep a floor (it clearly isn't finished)
Then the SAME feasibility check + scheduler run on what's left, from today.

The personal multiplier (how much longer than estimated this user takes) is
learned only from COMPLETED milestones: actual total / estimate. It needs at
least 2 of them before it's trusted, and is clamped to 0.5-2.0.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.planners.capacity import CapacityProfile
from app.planners.feasibility import FeasibilityResult, PlanItem, check_items
from app.planners.scheduler import Schedule, build_schedule
from app.schemas.blueprint import GoalBlueprint

MIN_COMPLETED_FOR_MULTIPLIER = 2
MULTIPLIER_RANGE = (0.5, 2.0)
OVERRUN_FLOOR_RATIO = 0.10   # spent everything but not done -> assume at least 10% left
OVERRUN_FLOOR_MINUTES = 30   # ...and at least 30 minutes


class CheckIn(BaseModel):
    day: date
    milestone_key: str
    outcome: Literal["done", "partial", "missed"]
    planned_minutes: int = Field(default=0, ge=0)
    actual_minutes: int = Field(default=0, ge=0)
    milestone_complete: bool = False          # "this whole milestone is finished"
    remaining_minutes: int | None = Field(default=None, ge=0)  # user's own "how much is left"
    note: str | None = None

    @model_validator(mode="after")
    def outcome_matches_minutes(self) -> "CheckIn":
        if self.outcome == "missed" and self.actual_minutes:
            raise ValueError("a missed session has 0 actual minutes")
        if self.outcome in ("done", "partial") and not self.actual_minutes:
            raise ValueError(f"a '{self.outcome}' session needs actual_minutes > 0")
        if self.milestone_complete and self.remaining_minutes:
            raise ValueError("a complete milestone has nothing remaining")
        return self


class MilestoneProgress(BaseModel):
    key: str
    name: str
    estimate_minutes: float
    spent_minutes: int
    remaining_minutes: float
    # where the remaining number came from — only "estimate" gets the personal multiplier
    remaining_source: Literal["complete", "user", "estimate", "overrun_floor"]
    complete: bool
    overrun: bool          # spent at least the estimate and still not complete
    sessions_missed: int


def summarize(items: list[PlanItem], checkins: list[CheckIn]) -> list[MilestoneProgress]:
    """items: the ORIGINAL plan items (multiplier 1.0) — estimates to compare against."""
    known = {it.key for it in items}
    for c in checkins:
        if c.milestone_key not in known:
            raise ValueError(f"check-in for unknown milestone '{c.milestone_key}'")

    out = []
    for it in items:
        mine = sorted((c for c in checkins if c.milestone_key == it.key), key=lambda c: c.day)
        spent = sum(c.actual_minutes for c in mine)
        complete = any(c.milestone_complete for c in mine)
        user_says = next((c.remaining_minutes for c in reversed(mine) if c.remaining_minutes is not None), None)
        overrun = False
        if complete:
            remaining, source = 0.0, "complete"
        elif user_says is not None:
            remaining, source = float(user_says), "user"
        elif spent >= it.minutes:
            # used up the whole estimate and still not done: it clearly needs more
            overrun = True
            remaining = max(it.minutes * OVERRUN_FLOOR_RATIO, OVERRUN_FLOOR_MINUTES)
            source = "overrun_floor"
        else:
            remaining, source = it.minutes - spent, "estimate"
        out.append(MilestoneProgress(
            key=it.key, name=it.name, estimate_minutes=it.minutes, spent_minutes=spent,
            remaining_minutes=round(remaining, 1), remaining_source=source,
            complete=complete, overrun=overrun,
            sessions_missed=sum(1 for c in mine if c.outcome == "missed"),
        ))
    return out


def personal_multiplier(progress: list[MilestoneProgress]) -> float | None:
    """actual / estimate over completed milestones. None until there's enough evidence."""
    done = [p for p in progress if p.complete and p.spent_minutes > 0]
    if len(done) < MIN_COMPLETED_FOR_MULTIPLIER:
        return None
    ratio = sum(p.spent_minutes for p in done) / sum(p.estimate_minutes for p in done)
    lo, hi = MULTIPLIER_RANGE
    return round(min(max(ratio, lo), hi), 2)


def remaining_items(
    items: list[PlanItem], progress: list[MilestoneProgress], multiplier: float = 1.0
) -> list[PlanItem]:
    """
    What's left, as plan items. Complete milestones drop out, and dependencies on
    them count as satisfied. The multiplier applies to AI-estimated remainders only —
    never to a number the user gave.
    """
    by_key = {p.key: p for p in progress}
    left = []
    for it in items:
        p = by_key[it.key]
        if p.complete or p.remaining_minutes <= 0:
            continue
        factor = multiplier if p.remaining_source == "estimate" else 1.0
        minutes = p.remaining_minutes * factor
        done = max(0.0, min(p.estimate_minutes, p.estimate_minutes - p.remaining_minutes))
        left.append(it.model_copy(update={"minutes": minutes, "done_minutes": done}))
    alive = {it.key for it in left}
    return [it.model_copy(update={"depends_on": [d for d in it.depends_on if d in alive]}) for it in left]


class ReplanResult(BaseModel):
    today: date
    progress: list[MilestoneProgress]
    multiplier: float | None       # None = not enough completed milestones yet
    feasibility: FeasibilityResult
    schedule: Schedule
    alerts: list[str]              # plain-language things the user should know


def replan(
    blueprint: GoalBlueprint,
    items: list[PlanItem],
    checkins: list[CheckIn],
    capacity: CapacityProfile,
    today: date,
    use_multiplier: bool = True,
) -> ReplanResult:
    progress = summarize(items, checkins)
    multiplier = personal_multiplier(progress)
    left = remaining_items(items, progress, multiplier if (use_multiplier and multiplier) else 1.0)
    verdict = check_items(left, capacity, today)
    schedule = build_schedule(blueprint, left, capacity, today)

    alerts = []
    for f in verdict.milestones:
        if f.due and f.due < today:
            alerts.append(f"{f.name} was due {f.due:%a %d %b} and isn't finished"
                          + (" (hard deadline)" if f.hard else ""))
        elif f.late:
            when = f"{f.days_late}d late" if f.days_late else "won't finish"
            alerts.append(f"{f.name} is now {when}" + (" — hard deadline" if f.hard else ""))
    for p in progress:
        if p.overrun:
            alerts.append(f"{p.name}: spent {p.spent_minutes / 60:.1f}h (estimate "
                          f"{p.estimate_minutes / 60:.1f}h) and not done — how much is left?")
    if multiplier and abs(multiplier - 1) >= 0.15:
        direction = "longer" if multiplier > 1 else "less"
        alerts.append(f"You're taking about {abs(multiplier - 1):.0%} {direction} than estimated — "
                      f"remaining work is adjusted (×{multiplier}).")
    return ReplanResult(today=today, progress=progress, multiplier=multiplier,
                        feasibility=verdict, schedule=schedule, alerts=alerts)


class DueSession(BaseModel):
    day: date
    milestone_key: str
    milestone_name: str
    planned_minutes: int


def due_sessions(
    blueprint: GoalBlueprint,
    items: list[PlanItem],
    checkins: list[CheckIn],
    capacity: CapacityProfile,
    since: date,
    until: date,
) -> list[DueSession]:
    """
    What the plan asked for between `since` and `until` (inclusive), as it stood at `since`
    — i.e. after the previous check-in. One row per (day, milestone). This is the list the
    user answers done / partial / missed for.
    """
    if since > until:
        return []
    names = {it.key: it.name for it in items}
    before = replan(blueprint, items, checkins, capacity, today=since)
    totals: dict[tuple[date, str], int] = {}
    for sprint in before.schedule.sprints:
        for day in sprint.days:
            if day.day > until:
                continue
            for s in day.sessions:
                totals[(day.day, s.milestone_key)] = totals.get((day.day, s.milestone_key), 0) + s.minutes
    return [
        DueSession(day=d, milestone_key=k, milestone_name=names[k], planned_minutes=m)
        for (d, k), m in sorted(totals.items())
    ]
