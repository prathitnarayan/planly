"""Lecture-level tasks + verification: strict, but a penalty needs proof."""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.ai.goal_intake import InterviewState
from app.core.auth import DEV_USER_ID
from app.main import app, get_repo
from app.planners.capacity import CapacityProfile
from app.planners.integrity import (
    Integrity, SiteState, WatchEvidence, apply_day, auto_done, check_session, coverage, merge_intervals,
)
from app.planners.tasks import attach_items, item_key
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalProfile
from app.schemas.source import CourseSource, SourceItem

MON = date(2026, 10, 5)
CAP = CapacityProfile(slots=[{"weekday": d, "start": "19:00", "end": "20:15"} for d in range(7)])   # 60 min/day
BP = GoalBlueprint.model_validate({
    "goal": "DSA", "category": "learning", "summary": "x",
    "milestones": [
        {"key": "basics", "name": "Basics", "hours_by_kind": {"learn": 2}, "confidence": 0.8, "done_criteria": ["ok"]},
        {"key": "sheet", "name": "Sheet", "hours_by_kind": {"practice": 2}, "confidence": 0.8,
         "done_criteria": ["ok"], "depends_on": ["basics"]},
    ],
    "assumptions": [], "open_questions": [],
})
yt = lambda i: f"https://www.youtube.com/watch?v={i * 11}"   # "aaaaaaaaaaa" ...
PLAYLIST = CourseSource(url="https://www.youtube.com/playlist?list=PL1", platform="youtube", title="A2Z", items=[
    SourceItem(title="G-1", kind="video", minutes=20, url=yt("a")),    # study 30
    SourceItem(title="G-2", kind="video", minutes=40, url=yt("b")),    # study 60
    SourceItem(title="G-3", kind="video", minutes=10, url=yt("c")),    # study 15
])
T0 = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
SHEET = CourseSource(url="https://takeuforward.org/sheet", platform="striver", title="Sheet", synced_at=T0, items=[
    SourceItem(title="Two Sum", kind="practice", difficulty="easy"),
    SourceItem(title="3Sum", kind="practice", difficulty="easy", done=True),
    SourceItem(title="LRU", kind="practice", difficulty="hard"),
])


class S:   # a bare session
    def __init__(self, kind, minutes):
        self.kind, self.minutes = kind, minutes


def ev(key, dur_min, *ranges_min):
    return WatchEvidence(key=key, duration_s=dur_min * 60, intervals=[(a * 60, b * 60) for a, b in ranges_min])


# ---------- tasks ----------

def test_sessions_get_the_next_lectures_split_and_done_skipped():
    out = attach_items([S("learn", 60), S("practice", 60), S("learn", 60)], [PLAYLIST, SHEET], set())
    learn1, practice, learn2 = out
    assert [(i.title, i.minutes, i.part_from, i.part_to) for i in learn1] == [("G-1", 30, 0, 1), ("G-2", 30, 0, 0.5)]
    assert [i.title for i in practice] == ["Two Sum", "LRU"]            # 3Sum already done on the site
    assert [(i.title, i.part_from) for i in learn2] == [("G-2", 0.5), ("G-3", 0)]
    assert learn1[0].video_key == "yt:" + "a" * 11 and learn1[0].verify == "video"
    assert practice[0].verify == "site"


def test_credited_items_are_not_planned_again():
    done = {item_key(PLAYLIST, 0)}
    [learn] = attach_items([S("learn", 30)], [PLAYLIST], done)
    assert learn[0].title == "G-2"


def test_tiny_leftover_is_folded_in():
    [a] = attach_items([S("learn", 28)], [PLAYLIST], set())             # G-1 needs 30: 2 min left -> take it all
    assert [(i.title, i.minutes) for i in a] == [("G-1", 30)]


# ---------- evidence ----------

def test_intervals_merge_and_coverage_counts_content_not_wall_time():
    assert merge_intervals([(0, 10), (5, 20), (21, 30), (100, 50)], 25) == [(0, 25)]
    e = ev("yt:x", 20, (0, 10))
    assert coverage(e) == 0.5 and coverage(e, 0, 0.5) == 1.0 and coverage(e, 0.5, 1) == 0


def items_for(minutes=60):
    return attach_items([S("learn", minutes)], [PLAYLIST], set())[0]    # G-1 whole + first half of G-2


def test_verified_when_watched():
    evid = {"yt:" + "a" * 11: ev("yt:a", 20, (0, 19)), "yt:" + "b" * 11: ev("yt:b", 40, (0, 20))}
    chk = check_session(60, items_for(), evid, {}, None, trust=100)
    assert chk.verdict == "verified" and chk.credit_minutes == 60
    assert auto_done(items_for(), evid, {}, 60)
    assert not auto_done(items_for(), evid, {}, 200)       # the videos are only part of a longer session


def test_opened_but_barely_watched_is_a_mismatch():
    evid = {"yt:" + "a" * 11: ev("yt:a", 20, (0, 2))}
    chk = check_session(60, items_for(), evid, {}, None, trust=100)
    assert chk.verdict == "mismatch" and chk.credit_minutes == 0


def test_no_evidence_is_self_reported_never_punished():
    chk = check_session(60, items_for(), {}, {}, None, trust=100)
    assert chk.verdict == "self" and chk.credit_minutes == 60
    assert check_session(60, items_for(), {}, {}, None, trust=30).credit_minutes == 30   # half credit < 40


def test_partial_watch_gets_partial_credit():
    evid = {"yt:" + "a" * 11: ev("yt:a", 20, (0, 12)), "yt:" + "b" * 11: ev("yt:b", 40, (0, 20))}  # G-1 60%
    chk = check_session(60, items_for(), evid, {}, None, trust=100)
    assert chk.verdict == "partial" and chk.credit_minutes == round(30 * 0.6 + 30)


def test_site_evidence_only_counts_if_read_after_the_tick():
    [prac] = attach_items([S("practice", 20)], [SHEET], set())      # Two Sum
    k = item_key(SHEET, 0)
    later = SiteState(done=False, synced_at=T0 + timedelta(hours=3))
    before = SiteState(done=False, synced_at=T0 - timedelta(hours=3))
    assert check_session(20, prac, {}, {k: later}, T0, 100).verdict == "mismatch"
    assert check_session(20, prac, {}, {k: before}, T0, 100).verdict == "self"
    assert check_session(20, prac, {}, {k: SiteState(done=True, synced_at=T0)}, T0, 100).verdict == "verified"


def test_standing_mismatch_penalty_streak_and_lock():
    mis = check_session(60, items_for(), {"yt:" + "a" * 11: ev("yt:a", 20, (0, 1))}, {}, None, 100)
    ok = check_session(60, items_for(), {}, {}, None, 100)
    integ = apply_day(Integrity(), MON, [("basics", "Basics", ok, items_for())], True)
    assert (integ.trust, integ.streak) == (100, 1)
    integ = apply_day(integ, MON + timedelta(days=1), [("basics", "Basics", mis, items_for())], True)
    assert (integ.trust, integ.streak, integ.penalty_minutes) == (85, 0, {"basics": 15})
    assert not integ.evidence_only(MON)
    integ = apply_day(integ, MON + timedelta(days=2), [("basics", "Basics", mis, items_for())], True)
    assert integ.trust == 70 and integ.lock_until is None
    integ = apply_day(integ, MON + timedelta(days=3), [("basics", "Basics", mis, items_for())], True)
    assert integ.trust == 55 and integ.lock_until == MON + timedelta(days=6)   # 3 mismatch days in a week
    assert integ.evidence_only(MON + timedelta(days=4)) and "mismatches" in integ.why_locked(MON + timedelta(days=4))


def test_nothing_ticked_on_a_planned_day_resets_streak():
    integ = Integrity(streak=4)
    assert apply_day(integ, MON, [], anything_planned=True).streak == 0
    assert apply_day(integ, MON, [], anything_planned=False).streak == 4


# ---------- API ----------

@pytest.fixture
def goal(monkeypatch):
    repo = app.dependency_overrides[get_repo]()
    iv = InterviewState(goal="DSA", done=True, profile=GoalProfile(deadline=date(2026, 12, 1)))
    rec = repo.create(DEV_USER_ID, iv)
    rec.blueprint, rec.capacity, rec.plan_start, rec.sources = BP, CAP, MON, [PLAYLIST]
    repo.save(DEV_USER_ID, rec)
    clock = {"today": MON}
    monkeypatch.setattr(main, "server_today", lambda: clock["today"])
    return TestClient(app), rec.id, clock, repo


def watch(c, vid, dur_min, *ranges):
    r = c.post("/evidence/watch", json={"events": [{"key": f"yt:{vid * 11}", "duration_s": dur_min * 60,
                                                    "intervals": [[a * 60, b * 60] for a, b in ranges]}]})
    assert r.status_code == 200, r.text
    return r.json()


def test_api_today_lists_lectures_and_evidence_ticks_it(goal):
    c, gid, clock, repo = goal
    t = c.get(f"/goals/{gid}/today", params={"today": MON.isoformat()}).json()
    s = t["sessions"][0]
    assert [i["title"] for i in s["items"]] == ["G-1", "G-2"] and s["checkable"] and not s["done"]
    watch(c, "a", 20, (0, 20))
    assert watch(c, "b", 40, (0, 10), (10, 21))["coverage"]["yt:" + "b" * 11] == pytest.approx(0.525)
    t = c.get(f"/goals/{gid}/today", params={"today": MON.isoformat()}).json()
    s = t["sessions"][0]
    assert s["done"] and s["auto"]                                  # the evidence ticked it
    assert t["watched"]["yt:" + "a" * 11] == 1.0
    clock["today"] = MON + timedelta(days=1)
    t = c.get(f"/goals/{gid}/today", params={"today": clock["today"].isoformat()}).json()
    assert [e["verdict"] for e in t["standing"]["last_day"]] == ["verified"]
    assert t["sessions"][0]["items"][0]["title"] == "G-2" and t["sessions"][0]["items"][0]["part_from"] == 0.5


def test_api_false_tick_is_caught_next_day(goal):
    c, gid, clock, repo = goal
    sid = c.get(f"/goals/{gid}/today", params={"today": MON.isoformat()}).json()["sessions"][0]["id"]
    assert c.put(f"/goals/{gid}/ticks", json={"day": MON.isoformat(), "session_id": sid, "done": True}).status_code == 200
    watch(c, "a", 20, (0, 1))                                        # opened G-1, left after a minute
    clock["today"] = MON + timedelta(days=1)
    t = c.get(f"/goals/{gid}/today", params={"today": clock["today"].isoformat()}).json()
    st = t["standing"]
    assert st["trust"] == 85 and st["streak"] == 0 and st["owed_minutes"] == 15
    assert st["last_day"][0]["verdict"] == "mismatch" and any("didn't count" in m for m in t["moved"])
    assert [i["title"] for i in t["sessions"][0]["items"]][:2] == ["G-1", "G-2"]   # nothing from it was credited
    rec = repo.get(DEV_USER_ID, gid)
    assert rec.checkins[0].outcome == "missed"                       # no credit
    r = c.get(f"/goals/{gid}/replan", params={"today": clock["today"].isoformat()}).json()
    basics = next(m for m in r["feasibility"]["milestones"] if m["key"] == "basics")
    assert basics["hours"] == pytest.approx((120 + 15) / 60, abs=0.06)   # 2h still left + 15 min owed


def test_api_evidence_only_blocks_manual_ticks(goal):
    c, gid, clock, repo = goal
    rec = repo.get(DEV_USER_ID, gid)
    rec.integrity = Integrity(trust=60)
    repo.save(DEV_USER_ID, rec)
    t = c.get(f"/goals/{gid}/today", params={"today": MON.isoformat()}).json()
    s = t["sessions"][0]
    assert s["locked"] and t["standing"]["evidence_only"]
    r = c.put(f"/goals/{gid}/ticks", json={"day": MON.isoformat(), "session_id": s["id"], "done": True})
    assert r.status_code == 423 and "ticks itself" in r.json()["detail"]


def test_api_watch_rejects_bad_keys_and_is_per_user(goal):
    c, gid, clock, repo = goal
    bad = c.post("/evidence/watch", json={"events": [{"key": "evil", "duration_s": 60, "intervals": []}]})
    assert bad.status_code == 422
    watch(c, "a", 20, (0, 5))
    assert repo.get_watch("someone-else", ["yt:" + "a" * 11]) == {}


def test_video_keys_for_youtube_and_lecture_pages():
    from app.planners.tasks import video_key
    assert video_key("https://youtu.be/abcdefghijk") == "yt:abcdefghijk"
    assert video_key("https://www.udemy.com/course/x/learn/lecture/42?start=0") == "page:https://www.udemy.com/course/x/learn/lecture/42#1"
    assert video_key("https://leetcode.com/problems/two-sum/", "practice") is None
