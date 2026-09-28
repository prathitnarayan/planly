"""
Try the interview + blueprint + feasibility in your terminal, with the real LLM.

    cd backend
    python -m scripts.try_goal

Uses the same code as the API, just without the server.
"""

import re
from datetime import date, datetime, time, timedelta

from app.ai.goal_intake import (
    answer_question, apply_correction, check_goal, generate_blueprint, start_interview,
)
from app.ai.llm import LLMOutputError, OpenAIClient
from app.core import config
from app.core import plan_file
from app.core.estimate_log import log_correction
from app.planners.capacity import CapacityProfile, Slot
from app.planners.estimate_check import check_estimates
from app.planners.feasibility import check_feasibility, resolve_items
from app.planners.scheduler import build_schedule


def with_retry(call, attempts: int = 3):
    """The AI sometimes returns unusable output. Try again instead of crashing mid-interview."""
    for n in range(1, attempts + 1):
        try:
            return call()
        except LLMOutputError as e:
            if n == attempts:
                raise SystemExit(f"\nThe AI kept returning unusable output, giving up: {e}")
            print(f"  (AI hiccup, retrying {n}/{attempts - 1}...)")


def ask_goal(llm) -> tuple[str, str | None]:
    """Get a real goal. If the user typed their background instead, say so and ask again."""
    text = ask("Your goal: ").strip()
    check = with_retry(lambda: check_goal(llm, text))
    if check.is_goal:
        return text, None
    print("\n  That reads like where you are now, not what you want to achieve.")
    hint = f" (Enter for: {check.suggested_goal})" if check.suggested_goal else ""
    goal = input(f"  What's the goal?{hint}: ").strip() or check.suggested_goal
    while not goal:
        goal = ask("  What's the goal?: ").strip()
    print("  Got it — I'll use what you wrote as your starting point.")
    return goal, check.background or text


def confirm_profile(llm, state, max_rounds: int = 3):
    """Show what we understood in plain words; let the user fix it before the blueprint."""
    for _ in range(max_rounds):
        print("\nWhat I understood:")
        for line in state.profile.describe():
            print(f"  {line}")
        reply = input("\nLook right? [Enter = yes / or type what to fix]: ").strip()
        if reply.lower() in ("", "y", "yes"):
            return state
        state = with_retry(lambda: apply_correction(llm, state, reply))
    print("\n(Using this — you can adjust hours in the next step.)")
    return state


def ask(prompt: str) -> str:
    reply = ""
    while not reply.strip():
        reply = input(prompt)
    return reply


def ask_hours(prompt: str) -> float:
    while True:
        try:
            return float(ask(prompt))
        except ValueError:
            print("  Just a number please, e.g. 1.5")


def parse_hours(text: str) -> float:
    """'27', '27h', '27 hrs' -> 27.0. Raises ValueError if it's not a positive number."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?|\.\d+)\s*(h|hr|hrs|hour|hours)?\s*", text.lower())
    if not match:
        raise ValueError(f"not a number of hours: {text!r}")
    hours = float(match.group(1))
    if hours <= 0:
        raise ValueError("must be > 0")
    return hours


def deliverable_total(bp, name: str) -> float:
    return round(sum(m.estimated_hours for m in bp.deliverables()[name]), 2)


def set_deliverable(bp, name, hours, goal, profile, source):
    before = deliverable_total(bp, name)
    confidence = min(m.confidence for m in bp.deliverables()[name])
    parts = bp.anchor_parts(name, profile.benchmark_map())
    kept = bp.locked_milestones(name, parts, hours)
    bp = bp.with_deliverable_hours(name, hours, parts)
    if kept:
        print(f"    kept as-is (they match your track record): {', '.join(kept)}")
    label = ", ".join(m.name for m in bp.deliverables()[name])
    log_correction(goal, name, label, before, hours, confidence,
                   bool(profile.benchmarks), source=source)
    return bp


def review_estimates(bp, goal, profile):
    # 1. code compares AI totals with the user's track record
    for w in check_estimates(bp, profile.benchmark_map()):
        print(f"\n⚠️  {w.message}")
        while True:
            reply = input(f"   Use {w.suggested_hours:g}h instead? [Y = yes / n = keep AI / or type hours]: ").strip()
            if reply.lower() in ("", "y", "yes"):
                bp = set_deliverable(bp, w.deliverable, w.suggested_hours, goal, profile, "benchmark_check")
                break
            if reply.lower() in ("n", "no"):
                break
            try:
                bp = set_deliverable(bp, w.deliverable, parse_hours(reply), goal, profile, "benchmark_check")
                break
            except ValueError:
                print("   Y, n, or a number like 27")

    # 2. the user reviews each deliverable's total (not each milestone — people think per assignment)
    print("\nHours per deliverable — Enter to keep, or type your own (e.g. 27 or 27h):")
    for name, milestones in bp.deliverables().items():
        parts = " + ".join(f"{round(m.estimated_hours, 1):g}" for m in milestones)
        label = name if len(milestones) > 1 else milestones[0].name
        while True:
            reply = input(f"  {label} [{deliverable_total(bp, name):g}h = {parts}]: ").strip()
            if not reply:
                break
            try:
                bp = set_deliverable(bp, name, parse_hours(reply), goal, profile, "user_review")
                break
            except ValueError:
                print("    A positive number please, or Enter to keep.")
    print(f"Total now: {bp.total_hours:g}h")
    return bp


def ask_time(prompt: str, default: time) -> time:
    while True:
        reply = input(prompt).strip()
        if not reply:
            return default
        try:
            return time.fromisoformat(reply if ":" in reply else f"{int(reply):02d}:00")
        except ValueError:
            print("  Like 19:00 or 7 — or Enter for the default")


def make_slot(weekday: int, start: time, hours: float) -> Slot:
    end = (datetime.combine(date.min, start) + timedelta(minutes=int(hours * 60))).time()
    if end <= start:
        raise SystemExit("A slot can't run past midnight — pick an earlier start time.")
    return Slot(weekday=weekday, start=start, end=end)


def fmt(d: date | None) -> str:
    return d.strftime("%a %d %b") if d else "—"


def main() -> None:
    llm = OpenAIClient(config.OPENAI_API_KEY, config.OPENAI_MODEL, config.OPENAI_BASE_URL)
    goal, background = ask_goal(llm)

    # ---- interview ----
    state = with_retry(lambda: start_interview(
        llm, goal, config.MAX_INTERVIEW_QUESTIONS, background=background))
    while not state.done:
        print(f"\n🤖 {state.current_question}")
        answer = ask("> ")
        # answer_question mutates state; retry on a copy so a failed AI call doesn't half-apply
        state = with_retry(lambda: answer_question(
            llm, state.model_copy(deep=True), answer, config.MAX_INTERVIEW_QUESTIONS))

    state = confirm_profile(llm, state)

    # ---- blueprint ----
    print("\nBuilding blueprint...")
    bp = with_retry(lambda: generate_blueprint(llm, goal, state.profile, state.messages))
    print_blueprint(bp)

    # ---- estimates: benchmark check (code), then the user's own numbers ----
    bp = review_estimates(bp, goal, state.profile)

    if not state.profile.deadline:
        print("\nNo deadline, so no feasibility check.")
        return

    # ---- capacity ----
    print()
    weekday = ask_hours("Hours free per weekday (e.g. 1.5): ")
    weekday_from = ask_time("  starting at [19:00]: ", time(19, 0)) if weekday > 0 else None
    weekend = ask_hours("Hours free per weekend day (e.g. 4): ")
    weekend_from = ask_time("  starting at [10:00]: ", time(10, 0)) if weekend > 0 else None

    slots = [make_slot(d, weekday_from, weekday) for d in range(5) if weekday > 0]
    slots += [make_slot(d, weekend_from, weekend) for d in (5, 6) if weekend > 0]
    capacity = CapacityProfile(slots=slots)
    start = date.today() + timedelta(days=1)
    profile = state.profile

    # ---- feasibility ----
    r = check_feasibility(
        bp, capacity, start, profile.deadline,
        key_dates=profile.key_date_map(),
        soft_keys=profile.soft_key_dates(),
        deadline_hard=profile.deadline_hard,
    )
    print_feasibility(bp, capacity, start, r)
    names = {m.key: m.name for m in bp.milestones}

    # ---- schedule ----
    items = resolve_items(bp, profile.key_date_map(), profile.deadline, 1.0,
                          profile.soft_key_dates(), profile.deadline_hard)
    print_schedule(build_schedule(bp, items, capacity, start), names)

    # ---- save, so check-ins can use it ----
    if input("\nSave this plan for daily check-ins? [Y/n]: ").strip().lower() in ("", "y", "yes"):
        path = plan_file.save(plan_file.SavedPlan(
            goal=goal, profile=profile, blueprint=bp, capacity=capacity, start=start,
        ))
        print(f"Saved to {path.name}. After each day: python -m scripts.checkin")


# ---------- printing ----------

def print_blueprint(bp) -> None:
    print(f"\n{bp.summary}")
    n = 0
    for deliverable, milestones in bp.deliverables().items():
        total = sum(m.estimated_hours for m in milestones)
        if len(milestones) > 1 or milestones[0].deliverable:
            print(f"\n■ {deliverable} — {total:g}h")
        for m in milestones:
            n += 1
            split = ", ".join(f"{k.value} {h:g}" for k, h in m.hours_by_kind.items())
            tag = "" if m.required else "  [optional]"
            print(f"  {n}. {m.name} — {m.estimated_hours:g}h ({split}), confidence {m.confidence}{tag}")
            if m.depends_on:
                print(f"       after: {', '.join(m.depends_on)}")
            dates = []
            if m.start_after:
                dates.append(f"starts after {m.start_after}")
            if m.due_by:
                extra = f" + {m.due_offset_days}d" if m.due_offset_days else ""
                dates.append(f"due {m.due_by}{extra}")
            if dates:
                print(f"       {' · '.join(dates)}")
            for c in m.done_criteria:
                print(f"       ✓ {c}")
    print(f"\nTotal: {bp.total_hours:g}h")
    if bp.anchors:
        print("Compared with your track record: " +
              ", ".join(f"{d} ↔ {b}" for d, b in bp.anchors.items()))
    for label, items in (("Assumed", bp.assumptions), ("Open questions", bp.open_questions)):
        if items:
            print(f"\n{label}:")
            for a in items:
                print(f"  - {a}")


def print_feasibility(bp, capacity, start, r) -> None:
    week = capacity.weekly_summary()
    print(f"\nFrom {fmt(start)}: {week['max_hours']:g}h/week free, planning with "
          f"{week['sustainable_hours']:g}h/week (sustainable).")
    print(f"Need {r.required_hours:g}h · have {r.available_hours:g}h until {fmt(r.last_due)}\n")
    deliverable_of = {m.key: (m.deliverable or "") for m in bp.milestones}
    print(f"{'Deliverable':<14} {'Milestone':<40} {'Hours':>6}  {'Due':<11} {'Done by':<11}")
    for m in r.milestones:
        if m.late:
            days = f"{m.days_late}d late" if m.days_late else "never"
            flag = f"❌ {days}" + (" (hard deadline)" if m.hard else "")
        else:
            flag = "✅"
        print(f"{deliverable_of[m.key][:13]:<14} {m.name[:39]:<40} {m.hours:>6g}  "
              f"{fmt(m.due):<11} {fmt(m.projected_finish):<11} {flag}")

    if r.feasible:
        print(f"\n✅ Fits. Everything done by {fmt(r.projected_finish)}.")
        return

    o = r.options
    print("\n⚠️  Doesn't fit as is. Options:")
    if o.extra_hours_per_week is None:
        print("  1. More hours: can't fix it even with 10x your free time.")
    else:
        warn = "" if o.extra_hours_realistic else "  (that's a lot — probably not realistic)"
        per_day = o.extra_hours_per_week * 60 / 7
        print(f"  1. Add {o.extra_hours_per_week:g}h of free time per week "
              f"(~{per_day:.0f} min a day){warn}")
    if o.later_finish_ok:
        print(f"  2. Accept a later finish: {fmt(o.earliest_finish)}")
    else:
        names = {m.key: m.name for m in bp.milestones}
        late = ", ".join(names[k] for k in o.hard_late)
        what = "has a hard deadline" if len(o.hard_late) == 1 else "have hard deadlines"
        print(f"  2. A later finish isn't an option: {late} {what}.")
    if o.optional_cuts:
        result = "fits" if o.feasible_after_cuts else "still doesn't fit"
        print(f"  3. Drop optional parts ({', '.join(o.optional_cuts)}) — then it {result}")
    else:
        print("  3. Drop parts: nothing optional to drop — everything here is required.")
    print("\n(The schedule below shows the plan as is, so you can see where it slips.)")


def short(name: str, deliverable: str | None = None) -> str:
    """
    Drop the assignment prefix when the group already shows it:
      'Assignment 2: Video walkthrough'          -> 'Video walkthrough'
      'Assignment 2 Notebook: Baseline and ...'  -> 'Notebook: Baseline and ...'
    Only a leading 'Assignment 2' / 'A2' style prefix is removed — never real words.
    """
    import re as _re
    number = _re.search(r"(\d+)$", deliverable or "")
    pattern = rf"^(?:assignment|a|task|project|module)\s*{number.group(1)}\b[\s:\-–·]*" if number else None
    if pattern:
        stripped = _re.sub(pattern, "", name, flags=_re.I)
        if stripped:
            return stripped[0].upper() + stripped[1:]
    return name


def week_summary(sprint, deliverables: dict[str, str | None]) -> str:
    """'assignment_2: tuning, video ✓ · assignment_3: baseline' — ✓ = finishes this week."""
    groups: dict[str, list[str]] = {}
    seen = set()
    for day in sprint.days:
        for s in day.sessions:
            if s.milestone_key in seen:
                continue
            seen.add(s.milestone_key)
            tick = " ✓" if s.milestone_key in sprint.finishing else ""
            groups.setdefault(s.deliverable or "other", []).append(
                short(s.milestone_name, s.deliverable) + tick)
    return " · ".join(f"{d}: {', '.join(ms)}" for d, ms in groups.items())


def print_schedule(sched, names: dict[str, str]) -> None:
    deliverables = {}
    if not sched.sprints:
        return
    first = sched.sprints[0]
    print(f"\n══ Sprint {first.number}: {fmt(first.start)} – {fmt(first.end)} "
          f"· {first.planned_minutes / 60:.1f}h of {first.capacity_minutes / 60:.1f}h ══")
    for d in first.days:
        if not d.sessions:
            print(f"{fmt(d.day)}  —")
            continue
        for i, s in enumerate(d.sessions):
            when = f"{s.start:%H:%M}–{s.end:%H:%M}" if s.start else f"{s.minutes} min"
            label = fmt(d.day) if i == 0 else ""
            print(f"{label:<10}  {when:<11}  {s.milestone_name[:48]} ({s.kind.value})")
    if first.definition_of_done:
        print("Done this week when:")
        for c in first.definition_of_done:
            print(f"  ✓ {c}")

    print("\nNext sprints:")
    for sp in sched.sprints[1:]:
        print(f"  {sp.number:>2}. {fmt(sp.start)} – {fmt(sp.end)}  {sp.planned_minutes / 60:>4.1f}h  "
              f"{week_summary(sp, deliverables) or 'free week'}")
    if sched.unscheduled_minutes:
        print(f"\n⚠️  {sched.unscheduled_minutes / 60:.1f}h never fits in your free time.")


if __name__ == "__main__":
    main()
