"""
Tests for the AI layer using a fake LLM — no API key, no cost, fully repeatable.
The fake returns scripted replies in order, so we can test retries and limits.
"""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.ai.goal_intake import (
    answer_question, apply_correction, check_goal, generate_blueprint, start_interview,
)
from app.ai.llm import LLMOutputError, generate_validated
from app.main import app, get_llm
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalProfile, InterviewTurn

TODAY = date(2026, 9, 28)


class FakeLLM:
    def __init__(self, replies: list):
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.calls: list[list[dict]] = []

    def complete_json(self, system, messages):
        self.calls.append(list(messages))
        if not self.replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        return self.replies.pop(0)


def turn(question=None, done=False, **profile):
    # Most tests aren't about benchmarks: default to "user has none" so the code
    # doesn't add its benchmark question. Benchmark tests pass it explicitly.
    profile.setdefault("benchmark_status", "none")
    return {"done": done, "question": question, "profile": profile}


BLUEPRINT = {
    "goal": "Learn SQL",
    "category": "learning",
    "summary": "Basics then joins.",
    "milestones": [
        {"key": "basics", "name": "Basics", "hours_by_kind": {"learn": 4, "practice": 4},
         "confidence": 0.8, "done_criteria": ["30 problems"]},
        {"key": "joins", "name": "Joins", "hours_by_kind": {"learn": 5, "practice": 7},
         "confidence": 0.7, "depends_on": ["basics"], "done_criteria": ["40 problems"]},
    ],
}


# ---------- generate_validated ----------

def test_retry_fixes_bad_json():
    llm = FakeLLM(["not json at all", turn("What do you know already?")])
    out = generate_validated(llm, InterviewTurn, "sys", [{"role": "user", "content": "hi"}])
    assert out.question == "What do you know already?"
    # second call must include the bad reply + the error, so the model can fix it
    assert "invalid" in llm.calls[1][-1]["content"]


def test_gives_up_after_retry():
    llm = FakeLLM(["{}", "{}"])
    with pytest.raises(LLMOutputError):
        generate_validated(llm, InterviewTurn, "sys", [])


def test_question_required_when_not_done():
    llm = FakeLLM([turn(None, done=False), turn(None, done=True)])
    out = generate_validated(llm, InterviewTurn, "sys", [])
    assert out.done


# ---------- interview ----------

def test_interview_flow_until_done():
    llm = FakeLLM([
        turn("What SQL do you know?"),
        turn("By when?", current_level="none"),
        turn(done=True, current_level="none", target_outcome="analyst queries", deadline="2026-12-01"),
    ])
    s = start_interview(llm, "Learn SQL", max_questions=6, today=TODAY)
    assert s.current_question == "What SQL do you know?"
    s = answer_question(llm, s, "Nothing", 6, TODAY)
    s = answer_question(llm, s, "December", 6, TODAY)
    assert s.done and s.current_question is None
    assert s.questions_asked == 2
    assert s.profile.deadline == date(2026, 12, 1)


def test_code_enforces_question_limit():
    llm = FakeLLM([turn("Q1?"), turn("Q2?"), turn("Q3 — model wants more", deadline_status="none")])
    s = start_interview(llm, "Learn SQL", max_questions=2, today=TODAY)
    s = answer_question(llm, s, "a", 2, TODAY)
    s = answer_question(llm, s, "b", 2, TODAY)
    assert s.done  # model asked for more, code said no
    assert s.questions_asked == 2


def test_code_asks_deadline_if_model_forgot():
    llm = FakeLLM([
        turn("What do you know?"),
        turn(done=True, current_level="basics", target_outcome="pass"),   # forgot deadline
        turn(done=True, current_level="basics", target_outcome="pass", deadline="2026-11-01"),
    ])
    s = start_interview(llm, "Learn ML", 6, TODAY)
    s = answer_question(llm, s, "basics", 6, TODAY)
    assert not s.done
    assert "deadline" in s.current_question.lower()
    s = answer_question(llm, s, "1 Nov", 6, TODAY)
    assert s.done and s.profile.deadline == date(2026, 11, 1)


def test_deadline_asked_only_once():
    llm = FakeLLM([turn(done=True), turn(done=True)])  # user never gives a date
    s = start_interview(llm, "x", 6, TODAY)
    assert s.current_question and "deadline" in s.current_question.lower()
    s = answer_question(llm, s, "not sure", 6, TODAY)
    assert s.done  # no infinite loop


def test_no_deadline_answer_is_respected():
    llm = FakeLLM([turn(done=True, deadline_status="none")])
    s = start_interview(llm, "x", 6, TODAY)
    assert s.done


def test_deadline_taken_from_latest_key_date():
    llm = FakeLLM([turn(done=True, key_dates=[
        {"key": "a2_due", "label": "A2", "date": "2026-10-15"},
        {"key": "a3_due", "label": "A3", "date": "2026-11-05"},
    ])])
    s = start_interview(llm, "Kaggle", 6, TODAY)
    assert s.done   # no forced deadline question: key dates give one
    assert s.profile.deadline == date(2026, 11, 5)
    assert s.profile.key_date_map()["a2_due"] == date(2026, 10, 15)


def test_blueprint_retry_on_unknown_key_date():
    bad = json.loads(json.dumps(BLUEPRINT))
    bad["milestones"][0]["due_by"] = "made_up_date"
    llm = FakeLLM([bad, BLUEPRINT])
    profile = GoalProfile(key_dates=[{"key": "a2_due", "label": "A2", "date": "2026-10-15"}])
    generate_blueprint(llm, "Learn SQL", profile)
    assert "unknown key date" in llm.calls[1][-1]["content"]


def test_code_asks_benchmark_if_model_forgot():
    llm = FakeLLM([
        turn(done=True, deadline="2026-11-01", benchmark_status="unknown"),   # skipped benchmark
        turn(done=True, deadline="2026-11-01",
             benchmarks=[{"key": "a1", "what": "Assignment 1", "hours": 25,
                          "parts": {"notebook": 18, "video": 4, "peer_review": 3}}]),
    ])
    s = start_interview(llm, "Kaggle", 6, TODAY)
    assert not s.done
    assert "similar before" in s.current_question
    s = answer_question(llm, s, "A1: 18 notebook, 4 video, 3 reviews", 6, TODAY)
    assert s.done
    assert s.profile.benchmarks[0].hours == 25
    assert s.profile.benchmark_status == "given"


def test_benchmark_no_is_respected_and_asked_once():
    llm = FakeLLM([turn(done=True, deadline="2026-11-01", benchmark_status="unknown"),
                   turn(done=True, deadline="2026-11-01", benchmark_status="unknown")])
    s = start_interview(llm, "Learn guitar", 6, TODAY)
    assert "similar before" in s.current_question
    s = answer_question(llm, s, "no", 6, TODAY)
    assert s.done   # model didn't record "none", but code asked once already -> stop


def test_deadline_asked_before_benchmark():
    llm = FakeLLM([turn(done=True, benchmark_status="unknown"),
                   turn(done=True, deadline="2026-11-01", benchmark_status="unknown"),
                   turn(done=True, deadline="2026-11-01", benchmark_status="none")])
    s = start_interview(llm, "x", 6, TODAY)
    assert "deadline" in s.current_question.lower()
    s = answer_question(llm, s, "1 Nov", 6, TODAY)
    assert "similar before" in s.current_question
    s = answer_question(llm, s, "no", 6, TODAY)
    assert s.done


def test_empty_answer_rejected():
    llm = FakeLLM([turn("Q?")])
    s = start_interview(llm, "x", 6, TODAY)
    with pytest.raises(ValueError):
        answer_question(llm, s, "   ", 6, TODAY)


def test_cannot_answer_finished_interview():
    llm = FakeLLM([turn(done=True, deadline_status="none")])
    s = start_interview(llm, "x", 6, TODAY)
    with pytest.raises(ValueError):
        answer_question(llm, s, "late answer", 6, TODAY)


# ---------- blueprint ----------

def test_blueprint_retry_on_cycle():
    bad = json.loads(json.dumps(BLUEPRINT))
    bad["milestones"][0]["depends_on"] = ["joins"]  # cycle
    llm = FakeLLM([bad, BLUEPRINT])
    bp = generate_blueprint(llm, "Learn SQL", GoalProfile(current_level="none"))
    assert isinstance(bp, GoalBlueprint)
    assert "cycle" in llm.calls[1][-1]["content"]


# ---------- API end to end ----------

@pytest.fixture
def client_with(isolated_app):
    def make(replies):
        fake = FakeLLM(replies)
        app.dependency_overrides[get_llm] = lambda: fake
        return TestClient(app)
    return make


def test_api_goal_to_feasibility(client_with):
    c = client_with([
        turn("What SQL do you know?"),
        turn(done=True, current_level="none", target_outcome="analyst", deadline="2026-10-25"),
        BLUEPRINT,
    ])
    g = c.post("/goals", json={"goal": "Learn SQL"}).json()
    assert g["question"] == "What SQL do you know?"

    # blueprint before interview finishes -> refused
    assert c.post(f"/goals/{g['id']}/blueprint").status_code == 409

    g = c.post(f"/goals/{g['id']}/answer", json={"answer": "none"}).json()
    assert g["interview_done"] is True

    bp = c.post(f"/goals/{g['id']}/blueprint").json()
    assert len(bp["milestones"]) == 2

    slots = [{"weekday": d, "start": "19:00", "end": "20:00"} for d in range(5)]
    r = c.post(f"/goals/{g['id']}/feasibility", json={
        "capacity": {"slots": slots, "sustainable_ratio": 1.0},
        "start": "2026-09-28",  # deadline comes from the interview
    }).json()
    assert r["required_hours"] == 20
    assert r["available_hours"] == 20
    assert r["feasible"] is True


def test_api_edit_hours_logs_correction(client_with, isolated_app):
    c = client_with([turn(done=True, deadline_status="none",
                          benchmarks=[{"what": "Assignment 1", "hours": 25}]), BLUEPRINT])
    g = c.post("/goals", json={"goal": "Learn SQL"}).json()
    c.post(f"/goals/{g['id']}/blueprint")

    res = c.patch(f"/goals/{g['id']}/blueprint/milestones/joins", json={"hours": 6})
    assert res.status_code == 200
    joins = next(m for m in res.json()["milestones"] if m["key"] == "joins")
    assert sum(joins["hours_by_kind"].values()) == 6

    row = isolated_app.corrections[0]       # the API logs to the database, per user + goal
    assert row["ai_hours"] == 12 and row["user_hours"] == 6 and row["ratio"] == 0.5
    assert row["had_benchmark"] is True and row["source"] == "api" and row["goal_id"] == g["id"]

    assert c.patch(f"/goals/{g['id']}/blueprint/milestones/nope", json={"hours": 6}).status_code == 404
    assert c.patch(f"/goals/{g['id']}/blueprint/milestones/joins", json={"hours": 0}).status_code == 422


def test_api_estimate_check_and_deliverable_edit(client_with):
    bp = json.loads(json.dumps(BLUEPRINT))
    for m in bp["milestones"]:
        m["deliverable"] = "sql_course"
    bp["anchors"] = {"sql_course": "past"}                     # 20h vs 10h benchmark = 2x
    c = client_with([turn(done=True, deadline_status="none",
                          benchmarks=[{"key": "past", "what": "Last SQL course", "hours": 10}]), bp])
    g = c.post("/goals", json={"goal": "Learn SQL"}).json()
    c.post(f"/goals/{g['id']}/blueprint")

    check = c.get(f"/goals/{g['id']}/estimate-check").json()
    assert check["warnings"][0]["ratio"] == 2.0
    assert "took you 10h" in check["messages"][0]

    res = c.patch(f"/goals/{g['id']}/blueprint/deliverables/sql_course", json={"hours": 11})
    assert res.status_code == 200
    assert c.get(f"/goals/{g['id']}/estimate-check").json()["warnings"] == []
    assert c.patch(f"/goals/{g['id']}/blueprint/deliverables/nope", json={"hours": 5}).status_code == 404


def test_blueprint_retry_on_unknown_benchmark():
    bad = json.loads(json.dumps(BLUEPRINT))
    for m in bad["milestones"]:
        m["deliverable"] = "sql_course"
    bad["anchors"] = {"sql_course": "invented"}
    good = json.loads(json.dumps(bad))
    good["anchors"] = {"sql_course": "past"}
    llm = FakeLLM([bad, good])
    profile = GoalProfile(benchmarks=[{"key": "past", "what": "x", "hours": 10}])
    generate_blueprint(llm, "Learn SQL", profile)
    assert "unknown benchmark" in llm.calls[1][-1]["content"]


def test_placeholder_benchmark_does_not_kill_the_interview():
    """Real crash, 28 Sep: model sent {"hours": 0} before the user gave any benchmark."""
    llm = FakeLLM([turn("By when?", benchmarks=[{"key": "a1", "what": "", "hours": 0}])])
    s = start_interview(llm, "Kaggle", 6, TODAY)
    assert s.current_question == "By when?"
    assert s.profile.benchmarks == []


def test_benchmark_with_only_parts_is_kept():
    p = GoalProfile(benchmarks=[{"key": "a1", "what": "A1", "hours": 0,
                                 "parts": {"notebook": 18, "video": 4, "junk": 0}}])
    assert p.benchmarks[0].hours == 22 and "junk" not in p.benchmarks[0].parts


def test_benchmarks_parsed_into_profile():
    llm = FakeLLM([turn(done=True, deadline_status="none",
                        benchmarks=[{"what": "A1 incl. video", "hours": 30}])])
    s = start_interview(llm, "Kaggle", 6, TODAY)
    assert s.profile.benchmarks[0].hours == 30


def test_api_schedule(client_with):
    c = client_with([turn(done=True, deadline="2026-12-01"), BLUEPRINT])
    g = c.post("/goals", json={"goal": "Learn SQL"}).json()
    assert c.post(f"/goals/{g['id']}/schedule", json={"capacity": {"slots": []}}).status_code == 409
    c.post(f"/goals/{g['id']}/blueprint")
    slots = [{"weekday": d, "start": "19:00", "end": "20:00"} for d in range(5)]
    res = c.post(f"/goals/{g['id']}/schedule", json={
        "capacity": {"slots": slots, "sustainable_ratio": 1.0}, "start": "2026-09-28"})
    assert res.status_code == 200
    first = res.json()["sprints"][0]
    assert first["start"] == "2026-09-28" and first["planned_minutes"] == 300
    monday = first["days"][0]["sessions"][0]
    assert (monday["start"], monday["kind"]) == ("19:00:00", "learn")


def test_api_checkin_loop(client_with):
    c = client_with([turn(done=True, deadline="2026-10-25"), BLUEPRINT])
    g = c.post("/goals", json={"goal": "Learn SQL"}).json()
    c.post(f"/goals/{g['id']}/blueprint")
    assert c.get(f"/goals/{g['id']}/replan").status_code == 409          # no capacity yet
    slots = [{"weekday": d, "start": "19:00", "end": "20:00"} for d in range(5)]
    c.put(f"/goals/{g['id']}/capacity", json={"slots": slots, "sustainable_ratio": 1.0})

    # a bad first week: every session missed
    week = [{"day": f"2026-09-{d}", "milestone_key": "basics", "outcome": "missed"} for d in range(28, 31)]
    r = c.post(f"/goals/{g['id']}/checkins", json={"checkins": week, "today": "2026-09-30"}).json()
    assert r["today"] == "2026-10-01"
    basics = next(p for p in r["progress"] if p["key"] == "basics")
    assert basics["sessions_missed"] == 3 and basics["remaining_minutes"] == 480
    assert r["feasibility"]["feasible"] is False                         # 20h left, ~17h of evenings
    assert r["alerts"]

    bad = [{"day": "2026-10-01", "milestone_key": "nope", "outcome": "missed"}]
    assert c.post(f"/goals/{g['id']}/checkins", json={"checkins": bad}).status_code == 422
    # the rejected batch wasn't kept
    again = c.get(f"/goals/{g['id']}/replan", params={"today": "2026-10-01"}).json()
    assert len([p for p in again["progress"] if p["sessions_missed"]]) == 1


# ---------- goal check ----------

STATUS_TEXT = ("Assignment 1 (flight price regression) is done. Assignment 2 not started. "
               "I know Python and pandas basics.")


def test_goal_check_spots_background():
    llm = FakeLLM([{"is_goal": False, "suggested_goal": "Finish Kaggle assignments 2 and 3",
                    "background": STATUS_TEXT}])
    c = check_goal(llm, STATUS_TEXT)
    assert not c.is_goal and c.suggested_goal.startswith("Finish")


def test_background_goes_into_the_first_message():
    llm = FakeLLM([turn("What does done look like?")])
    s = start_interview(llm, "Finish Kaggle 2 and 3", 6, TODAY, background=STATUS_TEXT)
    first = s.messages[0]["content"]
    assert first.startswith("My goal: Finish Kaggle 2 and 3")
    assert "Where I'm starting from: Assignment 1" in first


# ---------- corrections ----------

def finished_interview(llm_replies_after=()):
    llm = FakeLLM([turn(done=True, deadline="2026-10-15",
                        target_outcome="baseline + 7 models; peer reviews optional"),
                   *llm_replies_after])
    return llm, start_interview(llm, "Kaggle", 6, TODAY)


def test_correction_updates_profile_and_transcript():
    llm, s = finished_interview([turn(done=True, deadline="2026-10-15",
                                      target_outcome="baseline + 7 models; peer reviews are graded and mandatory")])
    fixed = apply_correction(llm, s, "peer reviews are mandatory, not optional", TODAY)
    assert "mandatory" in fixed.profile.target_outcome
    assert fixed.messages[-1]["content"] == "Correction: peer reviews are mandatory, not optional"
    assert "optional" in s.profile.target_outcome           # original untouched
    from app.ai.goal_intake import transcript
    assert transcript(fixed.messages)[-1]["answer"].startswith("Correction:")


def test_correction_needs_finished_interview():
    llm = FakeLLM([turn("Q?")])
    s = start_interview(llm, "x", 6, TODAY)
    with pytest.raises(ValueError, match="finish the interview"):
        apply_correction(llm, s, "fix", TODAY)


def test_profile_describe_is_readable():
    p = GoalProfile(
        current_level="A1 done", target_outcome="cutoff + 7 models",
        key_dates=[{"key": "a3_due", "label": "A3 due", "date": "2026-11-05"},
                   {"key": "a2_due", "label": "A2 due", "date": "2026-10-15", "hard": False}],
        benchmarks=[{"key": "a1", "what": "Assignment 1", "hours": 25,
                     "parts": {"notebook": 18, "video": 4, "peer_review": 3}}],
    )
    lines = p.describe()
    assert "A2 due — Thu 15 Oct (flexible)" in lines[2]            # sorted by date
    assert "A3 due — Thu 05 Nov (hard)" in lines[3]
    assert "Assignment 1 took 25h (notebook 18h, video 4h, peer review 3h)" in lines[4]


def test_api_goal_check_and_correction(client_with):
    c = client_with([
        {"is_goal": False, "suggested_goal": "Finish Kaggle 2 and 3", "background": STATUS_TEXT},
        turn(done=True, deadline="2026-10-25", target_outcome="reviews optional"),
        BLUEPRINT,
        turn(done=True, deadline="2026-10-25", target_outcome="reviews mandatory"),
    ])
    check = c.post("/goals/check", json={"text": STATUS_TEXT}).json()
    assert check["is_goal"] is False
    g = c.post("/goals", json={"goal": check["suggested_goal"], "background": check["background"]}).json()
    assert any("reviews optional" in line for line in g["summary"])
    c.post(f"/goals/{g['id']}/blueprint")

    g = c.post(f"/goals/{g['id']}/correct", json={"text": "reviews are mandatory"}).json()
    assert any("reviews mandatory" in line for line in g["summary"])
    assert g["has_blueprint"] is False                        # stale blueprint dropped
    assert c.post(f"/goals/{g['id']}/correct", json={"text": "  "}).status_code == 422


def test_api_due_sessions_and_checked_through(client_with):
    """The check-in screen's loop: what's due -> answer -> nothing due until tomorrow."""
    c = client_with([turn(done=True, deadline="2026-12-01"), BLUEPRINT])
    g = c.post("/goals", json={"goal": "Learn SQL"}).json()
    c.post(f"/goals/{g['id']}/blueprint")
    slots = [{"weekday": d, "start": "19:00", "end": "20:00"} for d in range(5)]
    c.put(f"/goals/{g['id']}/capacity", params={"start": "2026-09-28"},
          json={"slots": slots, "sustainable_ratio": 1.0})

    early = c.get(f"/goals/{g['id']}/due", params={"today": "2026-09-27"}).json()
    assert early["sessions"] == [] and "starts Mon 28 Sep" in early["message"]

    due = c.get(f"/goals/{g['id']}/due", params={"today": "2026-09-29"}).json()
    assert [(s["day"], s["milestone_key"], s["planned_minutes"]) for s in due["sessions"]] == [
        ("2026-09-28", "basics", 60), ("2026-09-29", "basics", 60)]

    answers = [{"day": s["day"], "milestone_key": s["milestone_key"], "outcome": "done",
                "planned_minutes": 60, "actual_minutes": 60} for s in due["sessions"]]
    c.post(f"/goals/{g['id']}/checkins", json={"checkins": answers, "today": "2026-09-29"})
    assert c.get(f"/goals/{g['id']}").json()["checked_through"] == "2026-09-29"

    again = c.get(f"/goals/{g['id']}/due", params={"today": "2026-09-29"}).json()
    assert again["sessions"] == [] and "Already checked in" in again["message"]
    nxt = c.get(f"/goals/{g['id']}/due", params={"today": "2026-09-30"}).json()
    assert [s["day"] for s in nxt["sessions"]] == ["2026-09-30"]


def test_api_reads_and_replan_start(client_with):
    c = client_with([turn(done=True, deadline="2026-12-01"), BLUEPRINT])
    g = c.post("/goals", json={"goal": "Learn SQL"}).json()
    assert c.get(f"/goals/{g['id']}/blueprint").status_code == 404
    assert c.get(f"/goals/{g['id']}/capacity").status_code == 404
    c.post(f"/goals/{g['id']}/blueprint")
    assert len(c.get(f"/goals/{g['id']}/blueprint").json()["milestones"]) == 2
    slots = [{"weekday": d, "start": "19:00", "end": "20:00"} for d in range(5)]
    c.put(f"/goals/{g['id']}/capacity", params={"start": "2026-10-05"}, json={"slots": slots})
    assert c.get(f"/goals/{g['id']}/capacity").json()["slots"][0]["start"] == "19:00:00"
    # before the plan starts, the plan is shown from its start day
    assert c.get(f"/goals/{g['id']}/replan", params={"today": "2026-10-01"}).json()["today"] == "2026-10-05"
    # after checking in through the 6th, it's shown from the 7th
    c.post(f"/goals/{g['id']}/checkins", json={"checkins": [], "today": "2026-10-06"})
    assert c.get(f"/goals/{g['id']}/replan", params={"today": "2026-10-06"}).json()["today"] == "2026-10-07"


def test_api_cors_allows_frontend(client_with):
    c = client_with([])
    res = c.options("/goals", headers={"Origin": "http://localhost:3000",
                                       "Access-Control-Request-Method": "GET"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = c.options("/goals", headers={"Origin": "https://evil.example",
                                       "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_api_bad_ai_output_is_502(client_with):
    c = client_with(["garbage", "still garbage"])
    res = c.post("/goals", json={"goal": "x"})
    assert res.status_code == 502


def test_expired_ai_key_is_a_clear_503_not_a_500():
    """Real incident (9 Oct): the aipipe JWT expired and /goals/check returned a bare 500."""
    import httpx
    import openai
    from fastapi.testclient import TestClient
    from app.main import app, get_llm

    class Expired:
        def complete_json(self, system, messages):
            req = httpx.Request("POST", "https://aipipe.org/openai/v1/chat/completions")
            raise openai.AuthenticationError(
                "Bearer token is invalid: JWTExpired", body=None,
                response=httpx.Response(401, request=req))

    app.dependency_overrides[get_llm] = lambda: Expired()
    try:
        r = TestClient(app, raise_server_exceptions=False).post("/goals/check", json={"text": "Learn DSA"})
    finally:
        app.dependency_overrides.pop(get_llm, None)
    assert r.status_code == 503 and r.json()["code"] == "ai_key_expired"
    assert "OPENAI_API_KEY" in r.json()["detail"]


def test_a_crash_still_carries_cors_headers():
    """Real incident (9 Oct): a 500 had no CORS headers, so the browser showed "can't reach the API"."""
    from fastapi.testclient import TestClient
    from app.core import config
    from app.main import app, get_llm

    class Boom:
        def complete_json(self, system, messages):
            raise RuntimeError("boom")

    app.dependency_overrides[get_llm] = lambda: Boom()
    origin = config.FRONTEND_ORIGINS[0]
    r = TestClient(app, raise_server_exceptions=False).post("/goals/check", json={"text": "x"},
                                                            headers={"Origin": origin})
    assert r.status_code == 500 and r.json()["code"] == "server_error"
    assert r.headers.get("access-control-allow-origin") == origin
