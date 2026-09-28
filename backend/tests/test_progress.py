"""Check-ins -> progress -> replan. The loop that makes Planly more than a static plan."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.planners.feasibility import resolve_items
from app.planners.progress import (
    CheckIn, personal_multiplier, remaining_items, replan, summarize,
)
from tests.test_planning import KAGGLE_DATES, MON, kaggle_blueprint, kaggle_week


def items():
    return resolve_items(kaggle_blueprint(), KAGGLE_DATES, None)


def ci(key, outcome, actual=0, day=MON, **kw):
    return CheckIn(day=day, milestone_key=key, outcome=outcome, actual_minutes=actual, **kw)


# ---------- check-in validation ----------

def test_missed_session_has_zero_minutes():
    with pytest.raises(ValidationError):
        ci("a2_build", "missed", actual=30)


def test_done_session_needs_minutes():
    with pytest.raises(ValidationError):
        ci("a2_build", "done", actual=0)


def test_unknown_milestone_rejected():
    with pytest.raises(ValueError, match="unknown milestone"):
        summarize(items(), [ci("nope", "done", 60)])


# ---------- progress ----------

def test_time_spent_reduces_remaining():
    p = {x.key: x for x in summarize(items(), [ci("a2_build", "done", 90), ci("a2_build", "partial", 30)])}
    assert p["a2_build"].spent_minutes == 120
    assert p["a2_build"].remaining_minutes == 28 * 60 - 120
    assert p["a2_build"].remaining_source == "estimate"


def test_missed_sessions_leave_work_in_the_pool():
    p = {x.key: x for x in summarize(items(), [ci("a2_build", "missed"), ci("a2_build", "missed")])}
    assert p["a2_build"].remaining_minutes == 28 * 60
    assert p["a2_build"].sessions_missed == 2


def test_complete_means_nothing_left_even_if_early():
    p = {x.key: x for x in summarize(items(), [ci("a2_video", "done", 120, milestone_complete=True)])}
    assert p["a2_video"].remaining_minutes == 0 and p["a2_video"].complete


def test_user_remaining_beats_arithmetic():
    p = {x.key: x for x in summarize(items(), [ci("a2_build", "done", 60, remaining_minutes=300)])}
    assert p["a2_build"].remaining_minutes == 300
    assert p["a2_build"].remaining_source == "user"


def test_overrun_keeps_a_floor():
    # spent the whole 6h video estimate, still not done
    p = {x.key: x for x in summarize(items(), [ci("a2_video", "done", 360)])}
    assert p["a2_video"].overrun
    assert p["a2_video"].remaining_minutes == 36   # max(10% of 360, 30)


# ---------- multiplier ----------

def test_multiplier_needs_two_completed_milestones():
    p = summarize(items(), [ci("a2_video", "done", 540, milestone_complete=True)])
    assert personal_multiplier(p) is None


def test_multiplier_from_completed_work():
    p = summarize(items(), [
        ci("a2_video", "done", 480, milestone_complete=True),    # 6h estimate -> 8h
        ci("a2_review", "done", 480, milestone_complete=True),   # 6h estimate -> 8h
    ])
    assert personal_multiplier(p) == 1.33


def test_multiplier_is_clamped():
    p = summarize(items(), [
        ci("a2_video", "done", 5000, milestone_complete=True),
        ci("a2_review", "done", 5000, milestone_complete=True),
    ])
    assert personal_multiplier(p) == 2.0


def test_multiplier_only_touches_ai_estimates():
    p = summarize(items(), [ci("a3_build", "done", 60, remaining_minutes=100)])
    left = {it.key: it.minutes for it in remaining_items(items(), p, multiplier=1.5)}
    assert left["a3_build"] == 100               # user's number, untouched
    assert left["a2_build"] == 28 * 60 * 1.5     # AI estimate, adjusted


def test_completed_dependencies_count_as_satisfied():
    p = summarize(items(), [ci("a2_build", "done", 1680, milestone_complete=True)])
    left = {it.key: it for it in remaining_items(items(), p)}
    assert "a2_build" not in left
    assert left["a2_video"].depends_on == []
    assert left["a3_build"].depends_on == []


# ---------- replan ----------

def test_replan_after_a_bad_week_raises_the_alarm():
    """Week 1: every A2 session missed. From Monday 5 Oct, A2 can't fit before 15 Oct."""
    week1 = [ci("a2_build", "missed", day=MON + timedelta(days=i)) for i in range(7)]
    r = replan(kaggle_blueprint(), items(), week1, kaggle_week(1.5), today=date(2026, 10, 5))
    assert not r.feasibility.feasible
    assert any("hard deadline" in a for a in r.alerts)
    assert r.schedule.sprints[0].start == date(2026, 10, 5)


def test_small_shortfalls_add_up():
    """72 of 96 planned minutes each weekday looks fine day to day — but A2's video slips."""
    week1 = [ci("a2_build", "partial", 72, day=MON + timedelta(days=i)) for i in range(5)]
    week1 += [ci("a2_build", "done", 192, day=date(2026, 10, 3)),
              ci("a2_build", "done", 192, day=date(2026, 10, 4))]
    r = replan(kaggle_blueprint(), items(), week1, kaggle_week(2.0), today=date(2026, 10, 5))
    assert r.feasibility.options.hard_late == ["a2_video"]
    assert r.feasibility.options.extra_hours_realistic


def test_replan_after_a_good_week_is_fine():
    week1 = [ci("a2_build", "done", 96, day=MON + timedelta(days=i)) for i in range(5)]
    week1 += [ci("a2_build", "done", 192, day=date(2026, 10, 3)),
              ci("a2_build", "done", 192, day=date(2026, 10, 4))]
    r = replan(kaggle_blueprint(), items(), week1, kaggle_week(2.0), today=date(2026, 10, 5))
    p = {x.key: x for x in r.progress}
    assert p["a2_build"].spent_minutes == 5 * 96 + 2 * 192
    assert r.feasibility.feasible


def test_overdue_work_is_flagged_not_crashed():
    """It's 17 Oct, the A2 video (due 15 Oct, hard) was never done."""
    done = [ci("a2_build", "done", 1680, milestone_complete=True)]
    r = replan(kaggle_blueprint(), items(), done, kaggle_week(2.0), today=date(2026, 10, 17))
    assert any("was due Thu 15 Oct" in a and "hard" in a for a in r.alerts)


def test_everything_complete():
    all_done = [ci(it.key, "done", int(it.minutes), milestone_complete=True) for it in items()]
    r = replan(kaggle_blueprint(), items(), all_done, kaggle_week(2.0), today=date(2026, 11, 10))
    assert r.feasibility.feasible
    assert r.schedule.sprints == [] or all(sp.planned_minutes == 0 for sp in r.schedule.sprints)


# ---------- check-in script input ----------

from scripts.checkin import parse_answer  # noqa: E402


@pytest.mark.parametrize("text, expected", [
    ("", ("done", 72)), ("d", ("done", 72)), ("d 90", ("done", 90)), ("done 90m", ("done", 90)),
    ("p 40", ("partial", 40)), ("m", ("missed", 0)), ("missed", ("missed", 0)),
])
def test_parse_checkin_answer(text, expected):
    assert parse_answer(text, planned=72) == expected


@pytest.mark.parametrize("text", ["x", "p", "p 0", "d -5", "p forty"])
def test_parse_checkin_answer_rejects(text):
    with pytest.raises(ValueError):
        parse_answer(text, planned=72)


def test_plan_file_round_trip(tmp_path):
    from app.core import plan_file
    from app.schemas.interview import GoalProfile
    plan = plan_file.SavedPlan(
        goal="Kaggle", blueprint=kaggle_blueprint(), capacity=kaggle_week(1.5), start=MON,
        profile=GoalProfile(key_dates=[{"key": k, "label": k, "date": v} for k, v in KAGGLE_DATES.items()]),
        checkins=[ci("a2_build", "done", 90)], checked_through=MON,
    )
    path = plan_file.save(plan, tmp_path / "plan.json")
    back = plan_file.load(path)
    assert back.checkins[0].actual_minutes == 90 and back.checked_through == MON
    assert {it.key for it in back.items()} == {it.key for it in items()}


def test_schedule_continues_where_you_left_off():
    """Real bug: after 9h on a milestone with 6h of learning, replan said '(learn)' again."""
    from app.schemas.blueprint import TaskKind
    progress = summarize(items(), [ci("a2_build", "done", 9 * 60)])   # a2_build: project 28h only
    left = remaining_items(items(), progress)
    assert next(it for it in left if it.key == "a2_build").done_minutes == 540

    from app.planners.scheduler import build_schedule
    from app.schemas.blueprint import GoalBlueprint
    bp = kaggle_blueprint().model_dump(mode="json")
    bp["milestones"][0]["hours_by_kind"] = {"learn": 6, "practice": 12, "project": 10}
    bp = GoalBlueprint.model_validate(bp)
    base = resolve_items(bp, KAGGLE_DATES, None)
    left = remaining_items(base, summarize(base, [ci("a2_build", "done", 9 * 60)]))
    sched = build_schedule(bp, left, kaggle_week(2.0), date(2026, 10, 5))
    kinds = [s.kind for sp in sched.sprints for d in sp.days for s in d.sessions if s.milestone_key == "a2_build"]
    assert TaskKind.learn not in kinds
    assert kinds[0] == TaskKind.practice
    minutes = sum(s.minutes for sp in sched.sprints for d in sp.days for s in d.sessions if s.milestone_key == "a2_build")
    assert minutes == 28 * 60 - 540
