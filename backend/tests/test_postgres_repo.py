"""
PostgresRepo against a real Postgres with the real migration (+ a tiny Supabase stub).
Skipped unless PLANLY_TEST_PG points at a server where we may create databases, e.g.
    PLANLY_TEST_PG="postgresql://postgres@/postgres?host=/tmp&port=55432" pytest
"""

import os
import uuid
from datetime import date
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

ADMIN_DSN = os.getenv("PLANLY_TEST_PG", "")
ROOT = Path(__file__).resolve().parents[2]
ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"

pytestmark = pytest.mark.skipif(not ADMIN_DSN, reason="set PLANLY_TEST_PG to run Postgres tests")


def _dsn_for(db: str) -> str:
    base, _, query = ADMIN_DSN.partition("?")
    return base.rsplit("/", 1)[0] + f"/{db}" + (f"?{query}" if query else "")


@pytest.fixture(scope="module")
def dsn():
    name = f"planly_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"create database {name}")
    url = _dsn_for(name)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute((ROOT / "backend/tests/sql/supabase_stub.sql").read_text())
        for migration in sorted((ROOT / "database/migrations").glob("*.sql")):   # all, in order
            conn.execute(migration.read_text())
        from app.core.auth import DEV_USER_ID   # the API test below runs in dev-login mode
        conn.execute("insert into auth.users values (%s), (%s), (%s)", (ALICE, BOB, DEV_USER_ID))
    yield url
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"drop database {name} with (force)")


@pytest.fixture
def repo(dsn):
    from app.core.repo import PostgresRepo
    r = PostgresRepo(dsn, pool_size=2)
    yield r
    r.close()


def interview(goal="Finish Kaggle 2 and 3"):
    from app.ai.goal_intake import InterviewState
    return InterviewState(goal=goal, messages=[{"role": "user", "content": f"My goal: {goal}"}])


def test_create_get_save_roundtrip(repo):
    from app.schemas.blueprint import GoalBlueprint
    from tests.test_ai import BLUEPRINT
    rec = repo.create(ALICE, interview())
    got = repo.get(ALICE, rec.id)
    assert got.title == "Finish Kaggle 2 and 3" and got.blueprint is None

    got.blueprint = GoalBlueprint.model_validate(BLUEPRINT)
    repo.save(ALICE, got)
    again = repo.get(ALICE, rec.id)
    assert again.blueprint.total_hours == 20


def test_tracking_dates_roundtrip(repo):
    rec = repo.create(ALICE, interview())
    rec.plan_start, rec.checked_through = date(2026, 9, 29), date(2026, 10, 1)
    repo.save(ALICE, rec)
    got = repo.get(ALICE, rec.id)
    assert (got.plan_start, got.checked_through) == (date(2026, 9, 29), date(2026, 10, 1))


def test_other_user_sees_nothing(repo):
    rec = repo.create(ALICE, interview())
    assert repo.get(BOB, rec.id) is None
    assert all(s.id != rec.id for s in repo.list(BOB))
    assert not repo.delete(BOB, rec.id)
    rec.interview.goal = "hijacked"
    with pytest.raises(KeyError):
        repo.save(BOB, rec)
    assert repo.get(ALICE, rec.id).title == "Finish Kaggle 2 and 3"


def test_checkins_append_and_come_back_in_order(repo):
    from app.planners.progress import CheckIn
    rec = repo.create(ALICE, interview())
    repo.add_checkins(ALICE, rec.id, [
        CheckIn(day=date(2026, 9, 30), milestone_key="a2", outcome="missed"),
        CheckIn(day=date(2026, 9, 29), milestone_key="a2", outcome="done", actual_minutes=72,
                planned_minutes=72, milestone_complete=False),
    ])
    repo.add_checkins(ALICE, rec.id, [CheckIn(day=date(2026, 10, 1), milestone_key="a2",
                                              outcome="partial", actual_minutes=40, note="tired")])
    got = repo.get(ALICE, rec.id)
    assert [c.day.day for c in got.checkins] == [29, 30, 1]
    assert got.checkins[2].note == "tired"
    with pytest.raises(KeyError):
        repo.add_checkins(BOB, rec.id, got.checkins[:1])     # can't log onto someone else's goal
    s = next(s for s in repo.list(ALICE) if s.id == rec.id)
    assert s.checkins == 3


def test_save_never_touches_checkins(repo):
    from app.planners.progress import CheckIn
    rec = repo.create(ALICE, interview())
    repo.add_checkins(ALICE, rec.id, [CheckIn(day=date(2026, 9, 29), milestone_key="a2", outcome="missed")])
    stale = repo.get(ALICE, rec.id)
    stale.checkins = []
    repo.save(ALICE, stale)
    assert len(repo.get(ALICE, rec.id).checkins) == 1


def test_bad_ids_are_just_not_found(repo):
    assert repo.get(ALICE, "not-a-uuid") is None
    assert repo.get(ALICE, str(uuid.uuid4())) is None
    assert repo.delete(ALICE, "nope") is False


def test_delete_cascades(repo, dsn):
    from app.planners.progress import CheckIn
    rec = repo.create(ALICE, interview())
    repo.add_checkins(ALICE, rec.id, [CheckIn(day=date(2026, 9, 29), milestone_key="a2", outcome="missed")])
    assert repo.delete(ALICE, rec.id)
    with psycopg.connect(dsn) as conn:
        assert conn.execute("select count(*) from checkins where goal_id = %s", (rec.id,)).fetchone()[0] == 0


def test_correction_logged(repo, dsn):
    from app.core.estimate_log import make_row
    rec = repo.create(ALICE, interview())
    repo.log_correction(ALICE, rec.id, make_row("g", "assignment_2", "A2", 56, 27, 0.7, True, "api"))
    with psycopg.connect(dsn) as conn:
        row = conn.execute("select ai_hours, user_hours, ratio, source from estimate_corrections "
                           "where goal_id = %s", (rec.id,)).fetchone()
    assert (float(row[0]), float(row[1]), float(row[2]), row[3]) == (56, 27, 0.482, "api")


def test_full_api_flow_on_postgres(repo):
    """The whole loop through the API, stored in Postgres: interview -> blueprint -> capacity -> check-ins."""
    from fastapi.testclient import TestClient
    from app.main import app, get_llm, get_repo
    from tests.test_ai import BLUEPRINT, FakeLLM, turn

    fake = FakeLLM([turn("What do you know?"), turn(done=True, deadline="2026-10-25"), BLUEPRINT])
    app.dependency_overrides[get_llm] = lambda: fake
    app.dependency_overrides[get_repo] = lambda: repo
    c = TestClient(app)

    g = c.post("/goals", json={"goal": "Learn SQL"}).json()
    c.post(f"/goals/{g['id']}/answer", json={"answer": "nothing"})
    assert c.post(f"/goals/{g['id']}/blueprint").status_code == 200
    slots = [{"weekday": d, "start": "19:00", "end": "20:00"} for d in range(5)]
    c.put(f"/goals/{g['id']}/capacity", json={"slots": slots, "sustainable_ratio": 1.0})
    week = [{"day": "2026-09-28", "milestone_key": "basics", "outcome": "done",
             "actual_minutes": 60, "planned_minutes": 60}]
    r = c.post(f"/goals/{g['id']}/checkins", json={"checkins": week, "today": "2026-09-28"}).json()
    basics = next(p for p in r["progress"] if p["key"] == "basics")
    assert basics["spent_minutes"] == 60

    # a new "request" reads everything back from the database
    view = c.get(f"/goals/{g['id']}").json()
    assert view["interview_done"] and view["has_blueprint"] and view["has_capacity"]
    again = c.get(f"/goals/{g['id']}/replan", params={"today": "2026-09-29"}).json()
    assert next(p for p in again["progress"] if p["key"] == "basics")["spent_minutes"] == 60


def test_push_plan_uploads_saved_plan(dsn, repo, tmp_path, monkeypatch):
    """scripts.push_plan: saved terminal plan + check-ins -> Supabase, under the signed-in user."""
    import builtins
    import getpass
    import time
    from types import SimpleNamespace

    import jwt

    import scripts.push_plan as push
    from app.core import config, plan_file
    from app.planners.progress import CheckIn
    from app.schemas.interview import GoalProfile
    from tests.test_planning import KAGGLE_DATES, MON, kaggle_blueprint, kaggle_week

    url, secret = "https://abcd1234.supabase.co", "legacy-secret-for-tests-0123456789"
    token = jwt.encode({"sub": ALICE, "aud": "authenticated", "iss": f"{url}/auth/v1",
                        "exp": int(time.time()) + 600}, secret, algorithm="HS256")
    for k, v in {"SUPABASE_URL": url, "SUPABASE_ANON_KEY": "anon", "SUPABASE_JWT_SECRET": secret,
                 "DATABASE_URL": dsn}.items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(push.httpx, "post", lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: {"access_token": token}, text=""))
    monkeypatch.setattr(builtins, "input", lambda *_: "me@example.com")
    monkeypatch.setattr(getpass, "getpass", lambda *_: "pw")
    monkeypatch.setattr(plan_file, "PLAN_PATH", tmp_path / "plan.json")

    plan_file.save(plan_file.SavedPlan(
        goal="Kaggle 2 and 3", blueprint=kaggle_blueprint(), capacity=kaggle_week(1.5), start=MON,
        profile=GoalProfile(key_dates=[{"key": k, "label": k, "date": v} for k, v in KAGGLE_DATES.items()]),
        checkins=[CheckIn(day=MON, milestone_key="a2_build", outcome="done", actual_minutes=72)],
    ))
    push.main()

    mine = [s for s in repo.list(ALICE) if s.title == "Kaggle 2 and 3"]
    assert len(mine) == 1 and mine[0].checkins == 1 and mine[0].has_blueprint and mine[0].has_capacity
    got = repo.get(ALICE, mine[0].id)
    assert got.interview.done and got.interview.profile.key_date_map() == KAGGLE_DATES
    assert got.plan_start == MON
    assert repo.list(BOB) == [] or all(s.title != "Kaggle 2 and 3" for s in repo.list(BOB))


def test_bad_password_fails_fast_without_leaking_it():
    """Real incident: wrong password -> background retries -> Supabase blocked the IP."""
    import time as _t
    from app.core.repo import DatabaseUnavailable, PostgresRepo
    t0 = _t.monotonic()
    with pytest.raises(DatabaseUnavailable) as err:
        PostgresRepo("postgresql://postgres:S3cretPw9@127.0.0.1:1/postgres", connect_timeout=2)
    assert _t.monotonic() - t0 < 10                     # stops, doesn't retry forever
    msg = str(err.value)
    assert "S3cretPw9" not in msg                        # the password is never printed
    assert "Session pooler" in msg and "user: postgres" in msg


def test_placeholder_password_is_called_out():
    from app.core.repo import _explain
    msg = _explain("postgresql://postgres.abcd:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres")
    assert "placeholder is still in DATABASE_URL" in msg
    assert "Not the Session pooler" not in msg           # it IS the pooler


def test_missing_tables_give_a_clear_503(dsn):
    """Real first run: GET /goals -> bare 500 because the migration hadn't been run."""
    from fastapi.testclient import TestClient
    from app.core.repo import PostgresRepo
    from app.main import app, get_repo
    empty = _dsn_for("postgres")                     # a database with no Planly tables
    repo = PostgresRepo(empty, pool_size=1)
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        res = TestClient(app).get("/goals")
        assert res.status_code == 503
        assert "001_planly.sql" in res.json()["detail"]
    finally:
        repo.close()


def test_checkin_script_on_supabase(dsn, repo, monkeypatch):
    """scripts.checkin against the database: asks about due sessions, stores answers, moves checked_through."""
    import builtins
    import sys as _sys

    import scripts.checkin as ck
    import scripts.session as session
    from app.core import config
    from app.schemas.interview import GoalProfile
    from app.ai.goal_intake import InterviewState
    from tests.test_planning import KAGGLE_DATES, MON, kaggle_blueprint, kaggle_week

    carol = str(uuid.uuid4())
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("insert into auth.users values (%s)", (carol,))
    profile = GoalProfile(key_dates=[{"key": k, "label": k, "date": v} for k, v in KAGGLE_DATES.items()])
    rec = repo.create(carol, InterviewState(goal="Kaggle", profile=profile, done=True))
    rec.blueprint, rec.capacity, rec.plan_start = kaggle_blueprint(), kaggle_week(1.5), MON
    repo.save(carol, rec)

    monkeypatch.setattr(config, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(config, "DATABASE_URL", dsn)
    monkeypatch.setattr(session, "current_user", lambda interactive=True: (carol, "carol@example.com"))
    answers = iter(["", "m", "n"])          # Mon done, Tue missed, "finished?" no
    monkeypatch.setattr(builtins, "input", lambda *_: next(answers))
    monkeypatch.setattr(_sys, "argv", ["checkin", "2026-09-29"])
    ck.main()

    got = repo.get(carol, rec.id)
    assert got.checked_through == date(2026, 9, 29)
    assert [(c.day.day, c.outcome) for c in got.checkins] == [(28, "done"), (29, "missed")]
    assert got.checkins[0].actual_minutes == got.checkins[0].planned_minutes == 72

    # run again the same day: nothing new to ask, nothing stored
    monkeypatch.setattr(builtins, "input", lambda *_: pytest.fail("shouldn't ask anything"))
    ck.main()
    assert len(repo.get(carol, rec.id).checkins) == 2


def test_watch_evidence_unions_and_stays_per_user(repo):
    from app.planners.integrity import WatchEvidence
    k = "yt:" + "q" * 11
    repo.merge_watch(ALICE, [WatchEvidence(key=k, duration_s=600, intervals=[(0, 100)])])
    got = repo.merge_watch(ALICE, [WatchEvidence(key=k, duration_s=600, intervals=[(90, 300), (500, 900)])])
    assert got[k].intervals == [(0, 300), (500, 600)]                  # union, clipped to the length
    assert repo.get_watch(ALICE, [k])[k].intervals == [(0, 300), (500, 600)]
    assert repo.get_watch(BOB, [k]) == {}


def test_integrity_and_tick_times_roundtrip(repo):
    from datetime import datetime, timezone

    from app.planners.integrity import Integrity
    rec = repo.create(ALICE, interview())
    rec.integrity = Integrity(trust=70, streak=2, penalty_minutes={"a": 15}, items_part={"x:1": 0.5})
    rec.ticks.at["2026-10-05|a|learn|0"] = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    repo.save(ALICE, rec)
    got = repo.get(ALICE, rec.id)
    assert (got.integrity.trust, got.integrity.penalty_minutes, got.integrity.items_part) == (70, {"a": 15}, {"x:1": 0.5})
    assert got.ticks.at["2026-10-05|a|learn|0"].year == 2026


def test_deleted_account_gets_401_not_500(dsn, monkeypatch):
    """Real incident: a user deleted in Supabase Auth still had a valid token -> FK error -> 500."""
    import uuid as _uuid

    from fastapi.testclient import TestClient

    from app.core import auth as auth_mod
    from app.core.repo import PostgresRepo
    from app.main import app, current_user, get_llm, get_repo
    from tests.test_ai import FakeLLM, turn
    ghost = str(_uuid.uuid4())                      # not in auth.users
    repo = PostgresRepo(dsn, pool_size=1)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user] = lambda: ghost
    app.dependency_overrides[get_llm] = lambda: FakeLLM([turn("q?")])
    try:
        r = TestClient(app).post("/goals", json={"goal": "x"})
        assert r.status_code == 401 and r.json()["code"] == "account_gone"
    finally:
        app.dependency_overrides.pop(current_user, None)
        repo.close()


def test_settings_and_outcomes_roundtrip(repo):
    from app.core.repo import UserSettings
    from app.planners.capacity import CapacityProfile
    from app.planners.learning import Learned, Outcome, OutcomeItem, WeekdayStat
    st = UserSettings(capacity=CapacityProfile(slots=[{"weekday": 0, "start": "19:00", "end": "20:00"}]),
                      goal_order=["g1"], learned=Learned(weekday={1: WeekdayStat(ratio=0.5, sessions=4)}))
    repo.save_settings(ALICE, st)
    got = repo.get_settings(ALICE)
    assert got.capacity.slots[0].minutes == 60 and got.goal_order == ["g1"] and got.learned.weekday[1].ratio == 0.5
    assert repo.get_settings(BOB).capacity is None
    rec = repo.create(ALICE, interview())
    row = Outcome(day=date(2026, 10, 6), weekday=1, kind="learn", planned_min=60, credited_min=30, ticked=True,
                  items=[OutcomeItem(kind="video", length_min=20, watched=1.0, active_min=35)])
    repo.add_outcomes(ALICE, rec.id, [row])
    rows = repo.list_outcomes(ALICE, date(2026, 10, 1))
    assert len(rows) == 1 and rows[0].goal_id == rec.id and rows[0].items[0].active_min == 35
    assert repo.list_outcomes(BOB, date(2026, 10, 1)) == []


def test_watch_active_time_adds_up(repo):
    from app.planners.integrity import WatchEvidence
    k = "yt:" + "z" * 11
    repo.merge_watch(ALICE, [WatchEvidence(key=k, duration_s=600, intervals=[(0, 60)], active_s=90)])
    got = repo.merge_watch(ALICE, [WatchEvidence(key=k, duration_s=600, intervals=[(60, 120)], active_s=100)])
    assert got[k].active_s == 190


def test_telegram_lookups_and_integrations_roundtrip(repo):
    from app.core.repo import UserSettings
    st = UserSettings(timezone="Europe/London")
    st.telegram.link_code = "abc123"
    repo.save_settings(ALICE, st)
    assert repo.user_by_link_code("abc123") == ALICE and repo.user_by_link_code("nope") is None
    st = repo.get_settings(ALICE)
    st.telegram.chat_id, st.telegram.link_code = 4242, None
    st.google.refresh_token_enc = "enc"
    repo.save_settings(ALICE, st)
    assert repo.user_by_chat(4242) == ALICE and ALICE in repo.telegram_users()
    got = repo.get_settings(ALICE)
    assert got.timezone == "Europe/London" and got.google.connected and got.telegram.link_code is None
