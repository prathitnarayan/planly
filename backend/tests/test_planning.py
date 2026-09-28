from datetime import date, time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.planners.capacity import CapacityProfile, Slot, available_minutes
from app.planners.feasibility import check_feasibility, resolve_items, simulate
from app.schemas.blueprint import GoalBlueprint, TaskKind

MON = date(2026, 9, 28)  # a Monday


def sql_blueprint(**overrides) -> dict:
    bp = {
        "goal": "Learn SQL for data analysis",
        "category": "learning",
        "summary": "Basics, then joins, then a project.",
        "milestones": [
            {"key": "basics", "name": "Basics", "hours_by_kind": {"learn": 4, "practice": 4},
             "confidence": 0.8, "done_criteria": ["30 problems"]},
            {"key": "joins", "name": "Joins", "hours_by_kind": {"learn": 5, "practice": 7},
             "confidence": 0.7, "depends_on": ["basics"], "done_criteria": ["40 problems"]},
            {"key": "project", "name": "Project", "hours_by_kind": {"project": 10},
             "confidence": 0.5, "depends_on": ["joins"], "done_criteria": ["Write-up"]},
        ],
    }
    bp.update(overrides)
    return bp


def evenings(hours_per_day: float = 1.0, ratio: float = 1.0) -> CapacityProfile:
    """Mon-Fri, 19:00 onwards."""
    end_minute = int(hours_per_day * 60)
    end = time(19 + end_minute // 60, end_minute % 60)
    return CapacityProfile(
        slots=[Slot(weekday=d, start=time(19, 0), end=end) for d in range(5)],
        sustainable_ratio=ratio,
    )


# ---------- blueprint ----------

def test_blueprint_totals_and_order():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    assert bp.total_hours == 30
    assert bp.topological_order() == ["basics", "joins", "project"]


@pytest.mark.parametrize("bad_deps, message", [
    (["project"], "cycle"),
    (["nope"], "unknown"),
    (["basics"], "itself"),
])
def test_blueprint_rejects_bad_dependencies(bad_deps, message):
    data = sql_blueprint()
    data["milestones"][0]["depends_on"] = bad_deps
    with pytest.raises(ValidationError, match=message):
        GoalBlueprint.model_validate(data)


def test_blueprint_rejects_vague_milestone():
    data = sql_blueprint()
    data["milestones"][0]["done_criteria"] = []
    with pytest.raises(ValidationError):
        GoalBlueprint.model_validate(data)


def test_user_can_override_milestone_hours():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    edited = bp.with_milestone_hours("joins", 6)       # AI said 12 (learn 5, practice 7)
    joins = next(m for m in edited.milestones if m.key == "joins")
    assert joins.estimated_hours == 6
    assert joins.hours_by_kind[TaskKind.practice] == 3.5  # proportions kept
    assert edited.total_hours == 24
    assert bp.total_hours == 30                         # original untouched


@pytest.mark.parametrize("key, hours", [("nope", 5), ("joins", 0), ("joins", -2)])
def test_bad_hour_override_rejected(key, hours):
    bp = GoalBlueprint.model_validate(sql_blueprint())
    with pytest.raises(ValueError):
        bp.with_milestone_hours(key, hours)


# ---------- capacity ----------

def test_slot_minutes_and_weekly_summary():
    p = evenings(1.5, ratio=0.8)
    assert p.slots[0].minutes == 90
    assert p.weekly_summary() == {"max_hours": 7.5, "sustainable_hours": 6.0, "fallback_hours": 3.75}


def test_available_minutes_skips_weekend_and_uses_overrides():
    p = evenings(1.0)
    week = available_minutes(p, MON, date(2026, 10, 4))  # Mon..Sun
    assert week == 5 * 60
    p.overrides[date(2026, 10, 3)] = 120  # Saturday bonus
    p.overrides[date(2026, 9, 29)] = 0    # busy Tuesday
    assert available_minutes(p, MON, date(2026, 10, 4)) == 5 * 60 + 120 - 60


# ---------- feasibility ----------

def test_feasible_plan():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    r = check_feasibility(bp, evenings(1.0), MON, date(2026, 11, 22))  # 8 weeks = 40h
    assert r.feasible
    assert r.required_hours == 30
    assert r.available_hours == 40
    assert r.gap_hours == 10
    assert r.options is None and r.late == []
    assert r.projected_finish == date(2026, 11, 6)  # 30th weekday


def test_infeasible_plan_names_late_milestones_and_options():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    r = check_feasibility(bp, evenings(1.0), MON, date(2026, 10, 25))  # 4 weeks = 20h
    assert not r.feasible
    assert r.gap_hours == -10
    assert r.late == ["project"]                    # basics + joins = 20h fit exactly
    assert r.options.extra_hours_per_week == 2.5    # 10h over 4 weeks
    assert r.options.extra_hours_realistic          # 2.5 on top of 5 is fine
    assert r.options.earliest_finish == date(2026, 11, 6)
    assert r.options.optional_cuts == []            # everything is required


def test_extra_hours_are_in_slot_hours():
    # 5h/week of slots at 80% = 16h over 4 weeks; 14h short.
    # 14h / 4 weeks = 3.5 usable h/week -> 3.5 / 0.8 = 4.4 slot h/week to add.
    bp = GoalBlueprint.model_validate(sql_blueprint())
    r = check_feasibility(bp, evenings(1.0, ratio=0.8), MON, date(2026, 10, 25))
    assert r.available_hours == 16
    assert r.options.extra_hours_per_week == 4.4
    assert r.projected_finish == date(2026, 11, 18)


def test_huge_extra_flagged_unrealistic():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    r = check_feasibility(bp, evenings(1.0), MON, date(2026, 10, 4))  # 1 week, 30h needed
    assert r.options.extra_hours_per_week > 5
    assert not r.options.extra_hours_realistic


def test_only_optional_milestones_are_cut():
    data = sql_blueprint()
    data["milestones"][2]["required"] = False  # project is optional
    bp = GoalBlueprint.model_validate(data)
    r = check_feasibility(bp, evenings(1.0), MON, date(2026, 10, 25))
    assert r.options.optional_cuts == ["project"]
    assert r.options.feasible_after_cuts


def test_cut_propagates_to_dependents():
    data = sql_blueprint()
    data["milestones"][1]["required"] = False  # joins optional -> project goes too
    bp = GoalBlueprint.model_validate(data)
    r = check_feasibility(bp, evenings(1.0), MON, date(2026, 10, 11))
    assert r.options.optional_cuts == ["joins", "project"]


def test_personal_multiplier_can_flip_feasibility():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    deadline = date(2026, 11, 8)  # 6 weeks = 30h, exactly enough at 1.0
    assert check_feasibility(bp, evenings(1.0), MON, deadline).feasible
    assert not check_feasibility(bp, evenings(1.0), MON, deadline, personal_multiplier=1.3).feasible


def test_no_capacity_never_finishes():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    r = check_feasibility(bp, CapacityProfile(slots=[]), MON, date(2026, 12, 1))
    assert r.projected_finish is None
    assert r.options.extra_hours_per_week is None


def test_deadline_before_start_rejected():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    with pytest.raises(ValueError):
        check_feasibility(bp, evenings(), MON, date(2026, 9, 1))


def test_no_deadline_anywhere_rejected():
    bp = GoalBlueprint.model_validate(sql_blueprint())
    with pytest.raises(ValueError, match="no deadline"):
        check_feasibility(bp, evenings(), MON, None)


# ---------- per-milestone dates ----------

def two_deliverables():
    """Listed 'late' first on purpose — the simulation must still do 'early' first."""
    return GoalBlueprint.model_validate({
        "goal": "Two things", "category": "project", "summary": "x",
        "milestones": [
            {"key": "late_one", "name": "Late", "hours_by_kind": {"project": 5},
             "confidence": 0.5, "done_criteria": ["done"], "due_by": "late_due"},
            {"key": "early_one", "name": "Early", "hours_by_kind": {"project": 5},
             "confidence": 0.5, "done_criteria": ["done"], "due_by": "early_due"},
        ],
    })


def test_earliest_due_is_worked_on_first():
    dates = {"early_due": date(2026, 10, 2), "late_due": date(2026, 10, 9)}
    r = check_feasibility(two_deliverables(), evenings(1.0), MON, key_dates=dates)
    assert r.feasible
    f = {m.key: m.projected_finish for m in r.milestones}
    assert f["early_one"] == date(2026, 10, 2)   # Mon-Fri, 1h/day
    assert f["late_one"] == date(2026, 10, 9)


def test_unknown_key_date_rejected():
    with pytest.raises(ValueError, match="unknown key date"):
        check_feasibility(two_deliverables(), evenings(), MON, key_dates={"early_due": MON})


def test_blueprint_validation_checks_key_dates_when_given():
    data = two_deliverables().model_dump(mode="json")
    GoalBlueprint.model_validate(data)  # no context -> not checked
    with pytest.raises(ValidationError, match="unknown key date"):
        GoalBlueprint.model_validate(data, context={"key_dates": {"early_due"}})


def kaggle_blueprint():
    """Shape of Utkarsh's real run: two assignments, each with build, video, peer review."""
    def ms(key, hours, **kw):
        return {"key": key, "name": key, "hours_by_kind": {"project": hours},
                "confidence": 0.6, "done_criteria": ["done"], **kw}
    return GoalBlueprint.model_validate({
        "goal": "Finish Kaggle assignments 2 and 3", "category": "learning", "summary": "x",
        "milestones": [
            ms("a2_build", 28, due_by="a2_due"),
            ms("a2_video", 6, depends_on=["a2_build"], due_by="a2_due"),
            ms("a2_review", 6, start_after="a2_due", due_by="a2_due", due_offset_days=4),
            ms("a3_build", 20, depends_on=["a2_build"], due_by="a3_due"),
            ms("a3_video", 6, depends_on=["a3_build"], due_by="a3_due"),
            ms("a3_review", 6, start_after="a3_due", due_by="a3_due", due_offset_days=4),
        ],
    })


def kaggle_week(weekday_hours: float) -> CapacityProfile:
    mins = int(weekday_hours * 60)
    return CapacityProfile(
        slots=[Slot(weekday=d, start=time(19), end=time(19 + mins // 60, mins % 60)) for d in range(5)]
        + [Slot(weekday=d, start=time(10), end=time(14)) for d in (5, 6)]
    )


KAGGLE_DATES = {"a2_due": date(2026, 10, 15), "a3_due": date(2026, 11, 5)}


def test_kaggle_with_real_hours_a2_is_late():
    # 1.5h weekdays + 4h weekends at 80% -> ~29.6h before 15 Oct, A2 build+video needs 34h
    r = check_feasibility(kaggle_blueprint(), kaggle_week(1.5), MON, key_dates=KAGGLE_DATES)
    assert "a2_video" in r.late
    by = {m.key: m for m in r.milestones}
    assert by["a2_review"].earliest_start == date(2026, 10, 16)
    assert by["a2_review"].due == date(2026, 10, 19)
    assert r.last_due == date(2026, 11, 9)
    # the fix is small, so it's flagged realistic
    assert r.options.extra_hours_realistic


def test_kaggle_with_2h_weekdays_fits_and_review_waits_for_window():
    r = check_feasibility(kaggle_blueprint(), kaggle_week(2.0), MON, key_dates=KAGGLE_DATES)
    by = {m.key: m for m in r.milestones}
    assert not by["a2_video"].late
    assert date(2026, 10, 16) <= by["a2_review"].projected_finish <= date(2026, 10, 19)
    assert r.feasible


def test_simulate_respects_dependencies():
    items = resolve_items(kaggle_blueprint(), {"a2_due": date(2026, 12, 1), "a3_due": date(2026, 12, 1)}, None)
    f = simulate(items, evenings(2.0), MON)
    assert f["a3_build"] >= f["a2_build"]
    assert f["a2_video"] >= f["a2_build"]


# ---------- API ----------

def test_feasibility_endpoint():
    client = TestClient(app)
    body = {
        "blueprint": sql_blueprint(),
        "capacity": {"slots": [{"weekday": d, "start": "19:00", "end": "20:00"} for d in range(5)],
                     "sustainable_ratio": 1.0},
        "start": "2026-09-28",
        "deadline": "2026-10-25",
    }
    res = client.post("/feasibility", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["feasible"] is False
    assert data["late"] == ["project"]


def test_feasibility_endpoint_bad_key_date_is_422():
    client = TestClient(app)
    body = {
        "blueprint": two_deliverables().model_dump(mode="json"),
        "capacity": {"slots": []},
        "start": "2026-09-28",
        "key_dates": {"early_due": "2026-10-02"},
    }
    assert client.post("/feasibility", json=body).status_code == 422
