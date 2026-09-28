"""
Daily check-in: what did you actually do? Then replan from tomorrow.

    cd backend
    python -m scripts.checkin              # check in up to today
    python -m scripts.checkin 2026-10-05   # pretend today is that date (for testing)

For every planned session since your last check-in, answer:
    Enter / d      done, as planned
    d 90           done, but it took 90 minutes
    p 40           partial: worked 40 minutes
    m              missed
Then, for each milestone you worked on: is it finished?
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from app.core import plan_file
from app.planners.progress import CheckIn, replan
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


def main() -> None:
    today = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    plan = plan_file.load()
    if plan is None:
        print("No saved plan yet. Run: python -m scripts.try_goal  (and save at the end)")
        return

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
    before = replan(plan.blueprint, items, plan.checkins, plan.capacity, today=since)
    due_sessions: dict[tuple[date, str], int] = {}
    for sprint in before.schedule.sprints:
        for day in sprint.days:
            if day.day > today:
                continue
            for s in day.sessions:
                due_sessions[(day.day, s.milestone_key)] = due_sessions.get((day.day, s.milestone_key), 0) + s.minutes

    print(f"Check-in for {fmt(since)} – {fmt(today)}")
    if not due_sessions:
        print("Nothing was planned in that stretch.")

    new: list[CheckIn] = []
    worked_on: list[str] = []
    for (day, key), planned in sorted(due_sessions.items()):
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

    plan.checkins += new
    plan.checked_through = today
    plan_file.save(plan)

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
