"""
Daily check-in: what did you actually do? Then replan from tomorrow.

    cd backend
    python -m scripts.checkin              # your plan in Supabase (signs in once, then remembers)
    python -m scripts.checkin --local      # the old local data/plan.json instead
    python -m scripts.checkin --sign-out   # forget the saved sign-in
    python -m scripts.checkin 2026-10-05   # pretend today is that date (testing only!)

For every planned session since your last check-in, answer:
    Enter / d      done, as planned
    d 90           done, but it took 90 minutes
    p 40           partial: worked 40 minutes
    m              missed
Then, for each milestone you worked on: is it finished?
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from app.core import config, plan_file
from app.planners.capacity import CapacityProfile
from app.planners.feasibility import PlanItem, resolve_items
from app.planners.progress import CheckIn, due_sessions, replan
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalProfile
from scripts.try_goal import fmt, short, week_summary


def parse_answer(text: str, planned: int) -> tuple[str, int]:
    """'' -> done as planned · 'd 90' · 'p 40' · 'm'  ->  (outcome, actual_minutes)."""
    parts = text.strip().lower().split()
    if not parts or parts == ["d"]:
        return "done", planned
    head, rest = parts[0], parts[1:]
    if head in ("m", "missed"):
        return "missed", 0
    if head in ("d", "done", "p", "partial") and len(rest) == 1:
        minutes = int(rest[0].rstrip("m"))
        if minutes <= 0:
            raise ValueError
        return ("done" if head.startswith("d") else "partial"), minutes
    raise ValueError(f"didn't understand {text!r}")


# ---------- where the plan lives: Supabase (default) or the old local file ----------

@dataclass
class Tracked:
    title: str
    profile: GoalProfile
    blueprint: GoalBlueprint
    capacity: CapacityProfile
    start: date
    checked_through: date | None
    checkins: list[CheckIn]
    commit: Callable[[list[CheckIn], date], None]   # store new check-ins + checked-through
    where: str

    def items(self) -> list[PlanItem]:
        p = self.profile
        return resolve_items(self.blueprint, p.key_date_map(), p.deadline, 1.0,
                             p.soft_key_dates(), p.deadline_hard)


def from_local() -> Tracked | None:
    plan = plan_file.load()
    if plan is None:
        print("No saved plan yet. Run: python -m scripts.try_goal  (and save at the end)")
        return None

    def commit(new: list[CheckIn], through: date) -> None:
        plan.checkins += new
        plan.checked_through = through
        plan_file.save(plan)

    return Tracked(plan.goal, plan.profile, plan.blueprint, plan.capacity, plan.start,
                   plan.checked_through, list(plan.checkins), commit, "this Mac (data/plan.json)")


def from_supabase() -> Tracked | None:
    from app.core.repo import PostgresRepo
    from scripts.session import SignInError, current_user

    try:
        user_id, email = current_user()
    except SignInError as e:
        print(f"Sign-in failed: {e}")
        return None
    repo = PostgresRepo(config.DATABASE_URL, pool_size=1)
    goals = [g for g in repo.list(user_id) if g.has_blueprint and g.has_capacity]
    if not goals:
        print(f"No plans with a schedule for {email} yet. Upload one: python -m scripts.push_plan")
        return None
    chosen = goals[0]
    if len(goals) > 1:
        for i, g in enumerate(goals, 1):
            print(f"  {i}. {g.title}")
        pick = input("Which plan? [1]: ").strip() or "1"
        chosen = goals[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(goals) else goals[0]
    rec = repo.get(user_id, chosen.id)

    if rec.plan_start is None:   # uploaded before plans remembered their start
        default = date.today() + timedelta(days=1)
        reply = input(f"When does this plan start? [{default:%Y-%m-%d}]: ").strip()
        rec.plan_start = date.fromisoformat(reply) if reply else default
        repo.save(user_id, rec)

    def commit(new: list[CheckIn], through: date) -> None:
        if new:
            repo.add_checkins(user_id, rec.id, new)
        rec.checked_through = through
        repo.save(user_id, rec)

    return Tracked(rec.title, rec.interview.profile, rec.blueprint, rec.capacity, rec.plan_start,
                   rec.checked_through, list(rec.checkins), commit, f"Supabase ({email})")


def main() -> None:
    args = sys.argv[1:]
    if "--sign-out" in args:
        from scripts.session import sign_out
        sign_out()
        print("Signed out. Next check-in will ask for your email and password.")
        return
    local = "--local" in args or not (config.SUPABASE_URL and config.DATABASE_URL)
    dates = [a for a in args if not a.startswith("--")]
    today = date.fromisoformat(dates[0]) if dates else date.today()

    plan = from_local() if local else from_supabase()
    if plan is None:
        return
    print(f"Plan: {plan.title}  ·  stored in {plan.where}")

    items = plan.items()
    names = {it.key: it.name for it in items}
    group = {m.key: m.deliverable for m in plan.blueprint.milestones}

    def label(key: str) -> str:
        d = group.get(key)
        return f"{d} · {short(names[key], d)}" if d else names[key]
    last = plan.checked_through or plan.start - timedelta(days=1)
    since = last + timedelta(days=1)
    if since > today:
        if plan.checked_through is None:
            print(f"Your plan starts {fmt(plan.start)} — first check-in that evening.")
        else:
            print(f"Already checked in up to {fmt(last)}. Come back tomorrow.")
        return

    # What the plan (as of the last check-in) asked for between then and today.
    due = due_sessions(plan.blueprint, items, plan.checkins, plan.capacity, since, today)

    print(f"Check-in for {fmt(since)} – {fmt(today)}")
    if not due:
        print("Nothing was planned in that stretch.")

    new: list[CheckIn] = []
    worked_on: list[str] = []
    for d in due:
        day, key, planned = d.day, d.milestone_key, d.planned_minutes
        while True:
            reply = input(f"  {fmt(day)}  {label(key)[:52]:<52} {planned:>4} min  "
                          "[Enter=done / d 90 / p 40 / m]: ")
            try:
                outcome, actual = parse_answer(reply, planned)
                break
            except ValueError:
                print("    Enter, 'd 90', 'p 40' or 'm'")
        new.append(CheckIn(day=day, milestone_key=key, outcome=outcome,
                           planned_minutes=planned, actual_minutes=actual))
        if actual and key not in worked_on:
            worked_on.append(key)

    # Is anything you worked on finished?
    for key in worked_on:
        reply = input(f"  Is '{label(key)}' finished? [y/N]: ").strip().lower()
        if reply in ("y", "yes"):
            last_ci = next(c for c in reversed(new) if c.milestone_key == key and c.actual_minutes)
            last_ci.milestone_complete = True

    plan.commit(new, today)
    plan.checkins += new

    # ---- replan from tomorrow ----
    tomorrow = today + timedelta(days=1)
    r = replan(plan.blueprint, items, plan.checkins, plan.capacity, today=tomorrow)

    print("\nProgress:")
    order = [m.key for ms in plan.blueprint.deliverables().values() for m in ms]
    for p in sorted(r.progress, key=lambda p: order.index(p.key)):
        if p.complete:
            status = "✅ done"
        else:
            status = f"{p.spent_minutes / 60:.1f}h done, {p.remaining_minutes / 60:.1f}h left"
        print(f"  {label(p.key)[:52]:<52} {status}")
    if r.multiplier:
        print(f"  Your pace: ×{r.multiplier} of estimates (from finished milestones)")

    if r.alerts:
        print("\n⚠️  Heads up:")
        for a in r.alerts:
            print(f"  - {a}")
    else:
        print("\n✅ Still on track for every deadline.")

    if r.schedule.sprints:
        sp = r.schedule.sprints[0]
        print(f"\nRest of this week ({fmt(sp.start)} – {fmt(sp.end)}):")
        for d in sp.days:
            for i, s in enumerate(d.sessions):
                when = f"{s.start:%H:%M}–{s.end:%H:%M}" if s.start else f"{s.minutes} min"
                print(f"  {fmt(d.day) if i == 0 else '':<10}  {when:<11}  {label(s.milestone_key)[:52]} ({s.kind.value})")
        for later in r.schedule.sprints[1:3]:
            print(f"  then {fmt(later.start)}: {week_summary(later, {})}")


if __name__ == "__main__":
    main()
