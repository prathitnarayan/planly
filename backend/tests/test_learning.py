"""Learning from what actually happened, and one shared pool of free time for all goals."""

from datetime import date, time, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.ai.goal_intake import InterviewState
from app.core.auth import DEV_USER_ID
from app.core.repo import UserSettings
from app.main import app, get_repo
from app.planners.capacity import CapacityProfile
from app.planners.learning import Learned, Outcome, OutcomeItem, WeekdayStat, learn
from app.planners.scheduler import _free_windows
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalProfile
from app.schemas.source import CourseSource, SourceItem

MON = date(2026, 10, 5)


def out(day, planned, credited, items=()):
    return Outcome(day=day, weekday=day.weekday(), kind="learn", planned_min=planned,
                   credited_min=credited, ticked=credited > 0, items=list(items))


# ---------- learning ----------

def test_weekday_reliability_is_learned_and_shrunk_towards_the_default():
    tuesdays = [out(MON + timedelta(days=1 + 7 * i), 60, 24) for i in range(4)]      # 40% on Tuesdays
    saturdays = [out(MON + timedelta(days=5 + 7 * i), 60, 60) for i in range(4)]     # 100% on Saturdays
    one_wed = [out(MON + timedelta(days=2), 60, 0)]                                    # a single bad Wednesday
    lr = learn(tuesdays + saturdays + one_wed, MON + timedelta(days=30))
    assert 0.4 < lr.weekday[1].ratio < 0.6                   # pulled a bit towards 1.0 by the prior
    assert lr.weekday[5].ratio == 1.0
    ratios = lr.weekday_ratio(0.8)
    assert 2 not in ratios                                   # one Wednesday isn't enough evidence
    assert ratios[1] == pytest.approx(0.8 * lr.weekday[1].ratio, abs=0.001)
    notes = " ".join(lr.notes())
    assert "Tues" in notes and "Sat" in notes


def test_recent_days_count_more():
    old_bad = [out(MON + timedelta(days=1 + 7 * i), 60, 0) for i in range(4)]
    new_good = [out(MON + timedelta(days=57 + 7 * i), 60, 60) for i in range(3)]
    lr = learn(old_bad + new_good, MON + timedelta(days=80))
    assert lr.weekday[1].ratio > 0.75


def test_video_pace_from_real_page_time():
    vids = [out(MON + timedelta(days=i), 60, 60,
                [OutcomeItem(kind="video", length_min=20, watched=1.0, active_min=40)]) for i in range(4)]
    lr = learn(vids, MON + timedelta(days=5))
    assert lr.video_samples == 4 and 1.7 < lr.pace() < 2.0   # ~2x, nudged towards the 1.5 prior
    few = learn(vids[:2], MON + timedelta(days=5))
    assert few.pace() is None                                # not enough lectures yet
    silly = [out(MON, 60, 60, [OutcomeItem(kind="video", length_min=20, watched=0.3, active_min=5)])]
    assert learn(silly, MON).video_samples == 0              # barely-watched videos aren't pace samples


# ---------- capacity: learned days + time taken by other goals ----------

SLOTS = [{"weekday": d, "start": "19:00", "end": "21:00"} for d in range(7)]   # 120 min, 96 sustainable


def test_capacity_uses_learned_weekday_and_subtracts_taken_time():
    cap = CapacityProfile(slots=SLOTS)
    tue = MON + timedelta(days=1)
    assert cap.sustainable_minutes_on(tue) == 96
    learned = cap.model_copy(update={"weekday_ratio": {1: 0.4}})
    assert learned.sustainable_minutes_on(tue) == 48 and learned.sustainable_minutes_on(MON) == 96
    taken = learned.model_copy(update={"taken": {MON: [(time(19), time(19, 30), 30)]}})
    assert taken.sustainable_minutes_on(MON) == 66
    assert "weekday_ratio" not in taken.model_dump() and "taken" not in taken.model_dump()   # never saved


def test_free_windows_skip_taken_times():
    cap = CapacityProfile(slots=SLOTS)
    wins = _free_windows(cap.slots[:1], [(time(19), time(19, 30), 30), (time(20), time(20, 10), 10)])
    assert wins == [[time(19, 30), 30], [time(20, 10), 50]]


# ---------- API: shared pool across goals ----------

BP = GoalBlueprint.model_validate({
    "goal": "x", "category": "learning", "summary": "x",
    "milestones": [{"key": "m", "name": "Work", "hours_by_kind": {"learn": 20}, "confidence": 0.8,
                    "done_criteria": ["ok"]}],
    "assumptions": [], "open_questions": [],
})


@pytest.fixture
def two_goals(monkeypatch):
    repo = app.dependency_overrides[get_repo]()
    ids = []
    for title in ("Kaggle", "DSA"):
        rec = repo.create(DEV_USER_ID, InterviewState(goal=title, done=True,
                                                      profile=GoalProfile(deadline=date(2026, 12, 31))))
        rec.blueprint, rec.capacity, rec.plan_start = BP, CapacityProfile(slots=SLOTS), MON
        repo.save(DEV_USER_ID, rec)
        ids.append(rec.id)
    repo.save_settings(DEV_USER_ID, UserSettings(capacity=CapacityProfile(slots=SLOTS), goal_order=ids))
    monkeypatch.setattr(main, "server_today", lambda: MON)
    return TestClient(app), ids, repo


def today(c, gid):
    return c.get(f"/goals/{gid}/today", params={"today": MON.isoformat()}).json()


def test_second_goal_gets_what_the_first_leaves_and_never_the_same_time(two_goals):
    c, (kaggle, dsa), repo = two_goals
    a, b = today(c, kaggle), today(c, dsa)
    assert a["planned_minutes"] == 96 and b["planned_minutes"] == 0     # Kaggle fills the evening
    assert "Nothing planned" in (b["message"] or "")


def test_priority_order_decides_who_gets_the_evening(two_goals):
    c, (kaggle, dsa), repo = two_goals
    r = c.put("/me/goal-order", json={"goal_ids": [dsa, kaggle]})
    assert r.json()["goal_order"] == [dsa, kaggle]
    assert today(c, dsa)["planned_minutes"] == 96 and today(c, kaggle)["planned_minutes"] == 0


def test_goals_share_an_evening_without_overlap(two_goals):
    c, (kaggle, dsa), repo = two_goals
    small = GoalBlueprint.model_validate({**BP.model_dump(mode="json"), "milestones": [
        {"key": "m", "name": "Work", "hours_by_kind": {"learn": 0.5}, "confidence": 0.8, "done_criteria": ["ok"]}]})
    rec = repo.get(DEV_USER_ID, kaggle)
    rec.blueprint = small                                              # Kaggle needs only 30 min
    repo.save(DEV_USER_ID, rec)
    a, b = today(c, kaggle), today(c, dsa)
    assert a["planned_minutes"] == 30 and b["planned_minutes"] == 66
    assert a["sessions"][0]["end"] <= b["sessions"][0]["start"]          # DSA starts after Kaggle ends


def test_saving_free_time_on_any_goal_updates_the_shared_pool(two_goals):
    c, (kaggle, dsa), repo = two_goals
    new = {"slots": [{"weekday": d, "start": "06:00", "end": "07:00"} for d in range(7)]}
    c.put(f"/goals/{dsa}/capacity", json=new)
    assert c.get(f"/goals/{kaggle}/capacity").json()["slots"][0]["start"] == "06:00:00"


def test_learned_weak_day_gets_less_work(two_goals):
    c, (kaggle, dsa), repo = two_goals
    st = repo.get_settings(DEV_USER_ID)
    st.learned = Learned(weekday={0: WeekdayStat(ratio=0.5, sessions=5)})    # Mondays: half done
    repo.save_settings(DEV_USER_ID, st)
    assert today(c, kaggle)["planned_minutes"] == 48


def test_closing_a_day_stores_outcomes_and_relearns(two_goals):
    c, (kaggle, dsa), repo = two_goals
    today(c, kaggle)
    monkey_next = MON + timedelta(days=1)
    main.server_today = lambda: monkey_next                              # (fixture restores it)
    c.get(f"/goals/{kaggle}/today", params={"today": monkey_next.isoformat()})
    rows = repo.list_outcomes(DEV_USER_ID, MON)
    assert len(rows) == 1 and rows[0].planned_min == 96 and rows[0].credited_min == 0
    lv = c.get("/me/learned").json()
    assert lv["learned"]["outcomes"] == 1 and lv["has_shared_capacity"]


def test_learned_video_pace_sizes_lecture_tasks(two_goals):
    c, (kaggle, dsa), repo = two_goals
    rec = repo.get(DEV_USER_ID, kaggle)
    rec.sources = [CourseSource(url="https://www.youtube.com/playlist?list=x", platform="youtube", title="P",
                                items=[SourceItem(title="L1", kind="video", minutes=20,
                                                  url="https://youtu.be/aaaaaaaaaaa")])]
    repo.save(DEV_USER_ID, rec)
    assert today(c, kaggle)["sessions"][0]["items"][0]["minutes"] == 30      # default 1.5x
    st = repo.get_settings(DEV_USER_ID)
    st.learned = Learned(video_factor=2.0, video_samples=5)
    repo.save_settings(DEV_USER_ID, st)
    assert today(c, kaggle)["sessions"][0]["items"][0]["minutes"] == 40      # your pace: 2x
