"""Scheduler: sessions in real slots, learn-before-practice, sprints, and agreement with feasibility."""

from datetime import date, time

import pytest

from app.planners.capacity import CapacityProfile, Slot
from app.planners.feasibility import check_feasibility, resolve_items
from app.planners.scheduler import build_schedule
from app.schemas.blueprint import GoalBlueprint, TaskKind
from tests.test_planning import KAGGLE_DATES, MON, kaggle_blueprint, kaggle_week, sql_blueprint


def evenings_19_to_20() -> CapacityProfile:
    return CapacityProfile(
        slots=[Slot(weekday=d, start=time(19), end=time(20)) for d in range(5)],
        sustainable_ratio=1.0,
    )


def schedule_for(bp, profile, start=MON, key_dates=None, deadline=None):
    items = resolve_items(bp, key_dates or {}, deadline)
    return build_schedule(bp, items, profile, start), items


def all_sessions(schedule):
    return [s for sp in schedule.sprints for d in sp.days for s in d.sessions]


def test_every_minute_is_scheduled():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    sched, items = schedule_for(bp, evenings_19_to_20(), deadline=date(2026, 12, 1))
    per = {}
    for s in all_sessions(sched):
        per[s.milestone_key] = per.get(s.milestone_key, 0) + s.minutes
    assert per == {"basics": 480, "joins": 720, "project": 600}
    assert sched.unscheduled_minutes == 0


def test_sessions_sit_inside_slots_and_capacity():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    sched, _ = schedule_for(bp, evenings_19_to_20(), deadline=date(2026, 12, 1))
    for sp in sched.sprints:
        for d in sp.days:
            assert d.planned_minutes <= d.capacity_minutes
            for s in d.sessions:
                assert time(19) <= s.start < s.end <= time(20)
            assert not (d.day.weekday() >= 5 and d.sessions)   # no weekend slots


def test_learn_comes_before_practice():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    sched, _ = schedule_for(bp, evenings_19_to_20(), deadline=date(2026, 12, 1))
    basics = [s for s in all_sessions(sched) if s.milestone_key == "basics"]
    kinds = [s.kind for s in basics]
    assert kinds.index(TaskKind.practice) > max(i for i, k in enumerate(kinds) if k == TaskKind.learn)
    # 4h learn = first 4 weekday evenings
    assert [s.day for s in basics if s.kind == TaskKind.learn] == [
        date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]


def test_sprints_are_monday_to_sunday():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    sched, _ = schedule_for(bp, evenings_19_to_20(), start=date(2026, 9, 30), deadline=date(2026, 12, 1))
    first, second = sched.sprints[0], sched.sprints[1]
    assert (first.start, first.end) == (date(2026, 9, 30), date(2026, 10, 4))   # Wed..Sun
    assert (second.start, second.end) == (date(2026, 10, 5), date(2026, 10, 11))
    assert first.capacity_minutes == 3 * 60


def test_definition_of_done_lists_what_finishes_that_week():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    sched, _ = schedule_for(bp, evenings_19_to_20(), deadline=date(2026, 12, 1))
    week2 = sched.sprints[1]                       # basics (8h) ends Wed 7 Oct
    assert week2.finishing == ["basics"]
    assert week2.definition_of_done == ["30 problems"]


def test_peer_review_sessions_wait_for_window():
    sched, _ = schedule_for(kaggle_blueprint(), kaggle_week(2.0), key_dates=KAGGLE_DATES)
    review_days = [s.day for s in all_sessions(sched) if s.milestone_key == "a2_review"]
    assert review_days and min(review_days) >= date(2026, 10, 16)


@pytest.mark.parametrize("weekday_hours", [1.5, 2.0])
def test_schedule_agrees_with_feasibility(weekday_hours):
    bp, profile = kaggle_blueprint(), kaggle_week(weekday_hours)
    verdict = check_feasibility(bp, profile, MON, key_dates=KAGGLE_DATES)
    sched, _ = schedule_for(bp, profile, key_dates=KAGGLE_DATES)
    last_session = {}
    for s in all_sessions(sched):
        last_session[s.milestone_key] = max(last_session.get(s.milestone_key, s.day), s.day)
    for m in verdict.milestones:
        assert last_session[m.key] == m.projected_finish
    assert sched.finish == verdict.projected_finish


def test_override_day_has_untimed_sessions():
    profile = evenings_19_to_20()
    profile.overrides[date(2026, 10, 3)] = 180   # free Saturday, 3h, no set times
    bp = GoalBlueprint.model_validate(sql_blueprint())
    sched, _ = schedule_for(bp, profile, deadline=date(2026, 12, 1))
    sat = next(d for d in sched.sprints[0].days if d.day == date(2026, 10, 3))
    assert sat.planned_minutes == 180
    assert all(s.start is None for s in sat.sessions)


def test_nothing_fits_means_unscheduled_work():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    sched, _ = schedule_for(bp, CapacityProfile(slots=[]), deadline=date(2026, 12, 1))
    assert sched.finish is None
    assert sched.unscheduled_minutes == 1800


def test_no_tiny_sessions():
    """Real run had '10:10–10:12 practice'. Same-milestone leftovers under 15 min are merged."""
    bp = GoalBlueprint.model_validate({
        "goal": "x", "category": "learning", "summary": "x",
        "milestones": [{"key": "nb", "name": "Notebook", "confidence": 0.7, "done_criteria": ["x"],
                        "hours_by_kind": {"learn": 4.9, "practice": 3}}],   # learn ends 6 min into day 5
    })
    sched, _ = schedule_for(bp, evenings_19_to_20(), deadline=date(2026, 12, 1))
    sessions = all_sessions(sched)
    assert min(s.minutes for s in sessions) >= 15
    assert sum(s.minutes for s in sessions) == round(7.9 * 60)
