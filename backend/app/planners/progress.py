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

from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.planners.capacity import CapacityProfile
from app.planners.feasibility import FeasibilityResult, PlanItem, check_items
from app.planners.integrity import (
    Integrity, SiteState, WatchEvidence, apply_day, check_session, checkable,
)
from app.planners.tasks import SessionItem, attach_items, item_key
from app.schemas.source import CourseSource
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
    items: list[PlanItem], progress: list[MilestoneProgress], multiplier: float = 1.0,
    penalties: dict[str, int] | None = None,
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
        # + time owed for work that was ticked but disproved by the evidence (integrity.py)
        minutes = p.remaining_minutes * factor + (penalties or {}).get(it.key, 0)
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
    penalties: dict[str, int] | None = None,
) -> ReplanResult:
    progress = summarize(items, checkins)
    multiplier = personal_multiplier(progress)
    left = remaining_items(items, progress, multiplier if (use_multiplier and multiplier) else 1.0, penalties)
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


# ---------- daily ticks: the checkbox flow ----------
#
# During the day the user ticks sessions. Ticks never change today's list (it stays stable).
# When a day is over it is CLOSED: every ticked session is checked against the evidence
# (integrity.py), then each milestone on that day becomes one check-in with the minutes that
# were CREDITED. The next day's plan is the normal replan: unfinished work flows into the
# following days at the usual daily capacity, never piled onto one day.

MAX_DAYS_TO_CLOSE = 60


class TodaySession(BaseModel):
    id: str
    day: date
    start: str | None
    end: str | None
    minutes: int
    milestone_key: str
    milestone_name: str
    deliverable: str | None
    kind: str
    done: bool = False
    items: list[SessionItem] = Field(default_factory=list)   # the lectures / problems it covers
    checkable: bool = False      # has items the evidence can confirm
    auto: bool = False           # ticked by the evidence, not by hand
    locked: bool = False         # evidence-only: can't be ticked by hand right now


class DayContext(BaseModel):
    """Everything besides check-ins that shapes a day's plan and its verification."""
    model_config = {"arbitrary_types_allowed": True}
    sources: list[CourseSource] = Field(default_factory=list)
    integrity: Integrity = Field(default_factory=Integrity)
    evidence: dict[str, WatchEvidence] = Field(default_factory=dict)

    def sites(self) -> dict[str, SiteState]:
        out = {}
        for src in self.sources:
            for i, it in enumerate(src.items):
                out[item_key(src, i)] = SiteState(done=it.done, synced_at=src.synced_at)
        return out


def _kind(s) -> str:
    return getattr(s.kind, "value", s.kind)


def session_id(day: date, s, index: int) -> str:
    return f"{day.isoformat()}|{s.milestone_key}|{_kind(s)}|{index}"


def day_plan(blueprint, items, checkins, capacity, day: date,
             ctx: DayContext | None = None) -> tuple[list[TodaySession], set[str]]:
    """The sessions planned for `day`, given everything logged before it, plus the milestones
    that still have sessions AFTER that day (to know which ones would finish on `day`)."""
    ctx = ctx or DayContext()
    plan = replan(blueprint, items, checkins, capacity, today=day, penalties=ctx.integrity.penalty_minutes)
    sessions, later = [], set()
    for sprint in plan.schedule.sprints:
        for d in sprint.days:
            if d.day == day:
                sessions = [
                    TodaySession(id=session_id(day, s, i), day=day,
                                 start=s.start.strftime("%H:%M") if s.start else None,
                                 end=s.end.strftime("%H:%M") if s.end else None,
                                 minutes=s.minutes, milestone_key=s.milestone_key,
                                 milestone_name=s.milestone_name, deliverable=s.deliverable, kind=_kind(s))
                    for i, s in enumerate(d.sessions)
                ]
            elif d.day > day:
                later |= {s.milestone_key for s in d.sessions}
    if ctx.sources and sessions:
        for s, its in zip(sessions, attach_items(sessions, ctx.sources, set(ctx.integrity.items_done),
                                              ctx.integrity.items_part)):
            s.items = its
            s.checkable = checkable(its)
    return sessions, later


def close_days(
    blueprint, items, checkins: list[CheckIn], capacity, ticks: dict[str, list[str]],
    first: date, last: date, ctx: DayContext | None = None,
    ticked_at: dict[str, datetime] | None = None,
) -> tuple[list[CheckIn], list[str], Integrity]:
    """Turn ticks on days first..last (inclusive) into VERIFIED check-ins, one day at a time,
    so each day is judged against the plan the user actually saw that morning.
    Returns (new check-ins, plain-language notes, updated integrity)."""
    ctx = (ctx or DayContext()).model_copy()
    sites = ctx.sites()
    new: list[CheckIn] = []
    notes: list[str] = []
    day = max(first, last - timedelta(days=MAX_DAYS_TO_CLOSE - 1))
    while day <= last:
        sessions, later = day_plan(blueprint, items, checkins + new, capacity, day, ctx)
        ticked = set(ticks.get(day.isoformat(), []))
        results = []
        credit: dict[str, int] = {}
        for s in sessions:
            if s.id not in ticked:
                continue
            chk = check_session(s.minutes, s.items, ctx.evidence, sites,
                                (ticked_at or {}).get(s.id), ctx.integrity.trust)
            credit[s.id] = chk.credit_minutes
            results.append((s.milestone_key, s.milestone_name, chk, s.items))
            if chk.verdict in ("mismatch", "partial"):   # (the verdict itself is shown from integrity events)
                notes.append(f"{day:%a %d %b}: {s.minutes - chk.credit_minutes} min of {s.milestone_name} "
                             "didn't count — back in the plan.")
        by_ms: dict[str, list[TodaySession]] = {}
        for s in sessions:
            by_ms.setdefault(s.milestone_key, []).append(s)
        for key, ss in by_ms.items():
            planned = sum(s.minutes for s in ss)
            did = sum(credit.get(s.id, 0) for s in ss)
            outcome = "done" if did >= planned else "partial" if did else "missed"
            # Credited the whole last planned piece of a milestone = it's finished.
            complete = outcome == "done" and key not in later
            new.append(CheckIn(day=day, milestone_key=key, outcome=outcome, planned_minutes=planned,
                               actual_minutes=min(did, planned) if did else 0,
                               milestone_complete=complete, note="ticks"))
            unticked = sum(s.minutes for s in ss if s.id not in ticked)
            if unticked:
                notes.append(f"{day:%a %d %b}: {unticked} min of {ss[0].milestone_name} not done "
                             "— spread over the next days.")
        ctx.integrity = apply_day(ctx.integrity, day, results, anything_planned=bool(sessions))
        day += timedelta(days=1)
    return new, notes, ctx.integrity
