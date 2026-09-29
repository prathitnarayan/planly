"""Today's checkboxes: ticks stay put during the day; at day's end unticked work is
logged as missed and spread over the next days at normal capacity (never piled up)."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.ai.goal_intake import InterviewState
from app.core.auth import DEV_USER_ID
from app.main import app, get_repo
from app.planners.capacity import CapacityProfile
from app.planners.feasibility import resolve_items
from app.planners.progress import close_days, day_plan
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalProfile

MON = date(2026, 10, 5)
# 75 free minutes every day -> 60 sustainable minutes a day
CAP = CapacityProfile(slots=[{"weekday": d, "start": "19:00", "end": "20:15"} for d in range(7)])
BP = GoalBlueprint.model_validate({
    "goal": "Learn SQL", "category": "learning", "summary": "x",
    "milestones": [
        {"key": "basics", "name": "Basics", "hours_by_kind": {"learn": 2}, "confidence": 0.8,
         "done_criteria": ["ok"]},
        {"key": "joins", "name": "Joins", "hours_by_kind": {"practice": 2}, "confidence": 0.8,
         "done_criteria": ["ok"], "depends_on": ["basics"]},
    ],
    "assumptions": [], "open_questions": [],
})
ITEMS = resolve_items(BP, {}, date(2026, 12, 1))


def minutes_on(day, checkins):
    sessions, _ = day_plan(BP, ITEMS, checkins, CAP, day)
    return sessions


def test_missed_day_is_spread_not_piled_onto_tomorrow():
    day1 = minutes_on(MON, [])
    assert sum(s.minutes for s in day1) == 60
    assert day1[0].id == f"{MON.isoformat()}|basics|learn|0" and day1[0].kind == "learn"
    new, notes = close_days(BP, ITEMS, [], CAP, {}, MON, MON)          # nothing ticked
    assert [(c.outcome, c.actual_minutes) for c in new] == [("missed", 0)]
    assert "spread over the next days" in notes[0]
    day2 = minutes_on(MON + timedelta(days=1), new)
    assert sum(s.minutes for s in day2) == 60                          # still one day's worth, not 120
    assert day2[0].milestone_key == "basics"                           # the missed work comes first


def test_ticked_work_counts_and_the_plan_moves_on():
    day1 = minutes_on(MON, [])
    ticks = {MON.isoformat(): [s.id for s in day1]}
    new, notes = close_days(BP, ITEMS, [], CAP, ticks, MON, MON)
    assert [(c.outcome, c.actual_minutes) for c in new] == [("done", 60)] and notes == []


def test_partial_when_some_sessions_ticked():
    # a longer Monday: basics' 2h, then the start of joins
    cap = CapacityProfile(slots=CAP.slots, overrides={MON: 150})       # 150 min on Monday
    s1, _ = day_plan(BP, ITEMS, [], cap, MON)
    assert {s.milestone_key for s in s1} == {"basics", "joins"}
    ticks = {MON.isoformat(): [s.id for s in s1 if s.milestone_key == "basics"]}
    new, notes = close_days(BP, ITEMS, [], cap, ticks, MON, MON)
    out = {c.milestone_key: (c.outcome, c.milestone_complete) for c in new}
    assert out == {"basics": ("done", True), "joins": ("missed", False)}   # last piece ticked = finished
    assert len(notes) == 1 and "Joins" in notes[0]


def test_days_are_closed_one_by_one_against_that_days_plan():
    new, notes = close_days(BP, ITEMS, [], CAP, {}, MON, MON + timedelta(days=2))
    assert [c.day for c in new] == [MON, MON + timedelta(days=1), MON + timedelta(days=2)]
    assert all(c.outcome == "missed" for c in new) and len(notes) == 3


# ---------- API ----------

@pytest.fixture
def goal(monkeypatch):
    repo = app.dependency_overrides[get_repo]()
    iv = InterviewState(goal="Learn SQL", done=True, profile=GoalProfile(deadline=date(2026, 12, 1)))
    rec = repo.create(DEV_USER_ID, iv)
    rec.blueprint, rec.capacity, rec.plan_start = BP, CAP, MON
    repo.save(DEV_USER_ID, rec)
    clock = {"today": MON}
    monkeypatch.setattr(main, "server_today", lambda: clock["today"])
    return TestClient(app), rec.id, clock, repo


def test_api_tick_untick_then_next_day_rolls_over(goal):
    c, gid, clock, repo = goal
    t = c.get(f"/goals/{gid}/today", params={"today": MON.isoformat()}).json()
    assert t["planned_minutes"] == 60 and t["done_minutes"] == 0
    sid = t["sessions"][0]["id"]

    t = c.put(f"/goals/{gid}/ticks", json={"day": MON.isoformat(), "session_id": sid, "done": True}).json()
    assert t["sessions"][0]["done"] is True and t["done_minutes"] == 60
    t = c.put(f"/goals/{gid}/ticks", json={"day": MON.isoformat(), "session_id": sid, "done": False}).json()
    assert t["sessions"][0]["done"] is False                           # untick works: nothing logged yet
    assert repo.get(DEV_USER_ID, gid).checkins == []

    tue = MON + timedelta(days=1)
    clock["today"] = tue
    t = c.get(f"/goals/{gid}/today", params={"today": tue.isoformat()}).json()
    assert t["planned_minutes"] == 60                                  # spread, not doubled
    assert t["moved"] and "Basics" in t["moved"][0]
    rec = repo.get(DEV_USER_ID, gid)
    assert [(ch.day, ch.outcome) for ch in rec.checkins] == [(MON, "missed")]
    assert rec.checked_through == MON and MON.isoformat() not in rec.ticks.days

    # the closed day can't be ticked any more; a far-off date is refused
    r = c.put(f"/goals/{gid}/ticks", json={"day": MON.isoformat(), "session_id": sid, "done": True})
    assert r.status_code in (409, 422)
    assert c.get(f"/goals/{gid}/today", params={"today": "2027-01-01"}).status_code == 422


def test_api_ticked_day_logs_done(goal):
    c, gid, clock, repo = goal
    sid = c.get(f"/goals/{gid}/today", params={"today": MON.isoformat()}).json()["sessions"][0]["id"]
    c.put(f"/goals/{gid}/ticks", json={"day": MON.isoformat(), "session_id": sid, "done": True})
    tue = MON + timedelta(days=1)
    clock["today"] = tue
    t = c.get(f"/goals/{gid}/today", params={"today": tue.isoformat()}).json()
    assert t["moved"] == []
    assert [(ch.outcome, ch.actual_minutes) for ch in repo.get(DEV_USER_ID, gid).checkins] == [("done", 60)]


def test_api_replan_also_closes_past_days(goal):
    c, gid, clock, repo = goal
    wed = MON + timedelta(days=2)
    clock["today"] = wed
    r = c.get(f"/goals/{gid}/replan", params={"today": wed.isoformat()}).json()
    assert r["today"] == wed.isoformat()
    assert len(repo.get(DEV_USER_ID, gid).checkins) == 2               # Mon + Tue closed as missed
