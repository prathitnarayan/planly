"""Reality check: typical prep time (AI estimate) vs the user's runway (code)."""

import json
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.ai.goal_intake import InterviewState
from app.core.auth import DEV_USER_ID
from app.main import app, get_llm, get_repo
from app.planners.capacity import CapacityProfile
from app.planners.reality import assess
from app.schemas.interview import GoalProfile, RealityCheck

TODAY = date(2026, 10, 1)
UPSC = RealityCheck(known=True, name="UPSC Civil Services", typical_hours_low=1500, typical_hours_high=2500,
                    typical_months_low=10, typical_months_high=18, basis="Commonly cited by aspirants",
                    scope_suggestions=["Finish Polity (Laxmikanth) + 200 MCQs"])


def test_upsc_in_a_month_is_flagged_unrealistic_with_numbers():
    v = assess(UPSC, TODAY, TODAY + timedelta(days=30), weekly_hours=10)
    assert v.level == "unrealistic"
    assert v.share_of_minimum < 0.03                      # ~43h of 1,500h
    assert "30 days away" in v.lines[0]
    assert "~350 h a week (more hours than a day has)" in v.lines[1] and "~10 h a week" in v.lines[1]
    assert v.earliest_realistic.year == 2027
    assert any("Polity" in s for s in v.suggestions) and any("later attempt" in s for s in v.suggestions)
    assert "10–18 months" in v.headline


def test_tight_and_ok():
    assert assess(UPSC, TODAY, TODAY + timedelta(days=240), weekly_hours=45).level == "tight"   # 8 of 10 months
    assert assess(UPSC, TODAY, TODAY + timedelta(days=400), weekly_hours=40).level == "ok"
    # enough months but far too few hours is still unrealistic
    assert assess(UPSC, TODAY, TODAY + timedelta(days=400), weekly_hours=5).level == "unrealistic"


def test_unknown_goals_and_no_deadline():
    assert assess(RealityCheck(known=False), TODAY, TODAY, 10).level == "unknown"
    assert assess(None, TODAY, TODAY, 10).level == "unknown"
    v = assess(UPSC, TODAY, None, None)
    assert v.level == "ok" and v.headline and v.suggestions == []


def test_model_must_give_ranges_for_known_goals():
    import pytest
    with pytest.raises(ValueError):
        RealityCheck(known=True, name="x")
    rc = RealityCheck(known=True, typical_hours_low=100, typical_months_low=2, typical_hours_high=50)
    assert rc.typical_hours_high == 100 and rc.typical_months_high == 2    # high can't be below low


class OneReply:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def complete_json(self, system, messages):
        self.calls += 1
        return json.dumps(self.reply)


def test_api_asks_once_then_compares_with_shared_free_time(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "server_today", lambda: TODAY)
    repo = app.dependency_overrides[get_repo]()
    rec = repo.create(DEV_USER_ID, InterviewState(goal="Clear UPSC", done=True,
                                                  profile=GoalProfile(deadline=TODAY + timedelta(days=30))))
    rec.capacity = CapacityProfile(slots=[{"weekday": d, "start": "19:00", "end": "21:00"} for d in range(7)])
    repo.save(DEV_USER_ID, rec)
    llm = OneReply(UPSC.model_dump(mode="json"))
    app.dependency_overrides[get_llm] = lambda: llm
    c = TestClient(app)
    r = c.get(f"/goals/{rec.id}/reality", params={"today": TODAY.isoformat()}).json()
    assert r["verdict"]["level"] == "unrealistic" and r["check"]["name"] == "UPSC Civil Services"
    assert r["verdict"]["hours_available"] < 60
    c.get(f"/goals/{rec.id}/reality")
    assert llm.calls == 1                                     # cached on the goal


def test_api_not_before_interview_is_done():
    repo = app.dependency_overrides[get_repo]()
    rec = repo.create(DEV_USER_ID, InterviewState(goal="x"))
    app.dependency_overrides[get_llm] = lambda: OneReply({"known": False})
    assert TestClient(app).get(f"/goals/{rec.id}/reality").status_code == 409
