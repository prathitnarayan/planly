"""'Something came up': fair to real emergencies, useless for faking."""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace as S

import pytest
from pydantic import ValidationError

from app.planners import breaks as B
from app.planners.breaks import Break
from app.planners.integrity import Integrity
from app.planners.progress import close_days
from tests.test_ticks import BP, CAP, ITEMS, MON, minutes_on

TUE = MON + timedelta(days=1)


def brk(kind="rest_of_day", day=MON, at="18:00", **kw) -> Break:
    return Break(kind=kind, created_day=kw.pop("created", day), first=kw.pop("first", day),
                 last=kw.pop("last", day), at_local=time.fromisoformat(at) if at else None,
                 at=datetime(2026, 10, 5, 12, 0) + timedelta(seconds=len(kw)), **kw)


def sess(i, start, end, minutes):
    return S(id=f"s{i}", start=start, end=end, minutes=minutes)


DAY = [sess(1, "07:00", "08:00", 60), sess(2, "19:00", "20:00", 60), sess(3, "20:00", "21:30", 90)]


# ---------- shape ----------

def test_past_days_and_wrong_shapes_are_refused():
    with pytest.raises(ValidationError):
        brk("days", first=MON - timedelta(days=1), last=MON)                 # already over
    with pytest.raises(ValidationError):
        brk("minutes", minutes=5)                                            # too small
    with pytest.raises(ValidationError):
        brk("rest_of_day", first=TUE, last=TUE)                              # 'rest of today' is today only


# ---------- say it when it happens ----------

def test_rest_of_today_covers_only_what_was_still_ahead():
    ex = B.excused_ids(DAY, set(), [brk(at="18:30")])
    assert ex == {"s2": True, "s3": True}                                    # the 7am session had ended
    assert B.excused_ids(DAY, set(), [brk(at="23:30")]) == {}                # bedtime excuse: nothing


def test_grace_hour_after_a_session_ends():
    assert "s2" in B.excused_ids(DAY, set(), [brk(at="20:55")])              # ended 20:00, said 20:55
    assert "s2" not in B.excused_ids(DAY, set(), [brk(at="21:05")])


def test_lost_minutes_eat_the_latest_open_sessions_first():
    assert B.excused_ids(DAY, set(), [brk("minutes", minutes=90, at="18:00")]) == {"s3": True}
    two = B.excused_ids(DAY, set(), [brk("minutes", minutes=120, at="18:00")])
    assert two == {"s3": True, "s2": True}                                   # 30 left covers half of s2
    assert B.excused_ids(DAY, set(), [brk("minutes", minutes=30, at="18:00")]) == {}   # < half of 90


def test_ticked_sessions_are_never_excused():
    assert B.excused_ids(DAY, {"s3"}, [brk(at="18:00")]) == {"s2": True}


# ---------- allowance ----------

def test_allowance_counts_same_day_excuses_only():
    bs = [brk(day=MON - timedelta(days=i), created=MON - timedelta(days=i)) for i in range(3)]
    bs.append(brk("minutes", minutes=60, day=MON - timedelta(days=3), created=MON - timedelta(days=3)))
    assert B.allowance_left(bs, MON) == 0.5
    ahead = brk("days", created=MON, first=TUE, last=TUE + timedelta(days=6))   # told in advance: free
    assert B.cost(ahead) == 0 and B.allowance_left(bs + [ahead], MON) == 0.5
    old = [brk(day=MON - timedelta(days=40), created=MON - timedelta(days=40))]
    assert B.allowance_left(old, MON) == 4.0


def test_days_off_start_the_day_after_it_was_said():
    b = brk("days", created=MON, first=MON, last=MON + timedelta(days=2))
    assert B.off_days([b]) == {TUE, TUE + timedelta(days=1)}
    assert B.made_on([b], MON) == [b] and B.cost(b) == 1.0                  # today's part = excuse


def test_pattern_named_when_the_same_weekday_keeps_getting_excused():
    thursdays = [brk(day=d, created=d) for d in (date(2026, 9, 17), date(2026, 9, 24), date(2026, 10, 1))]
    assert "Thursdays were excused 3 times" in B.pattern(thursdays, date(2026, 10, 3))
    assert B.pattern(thursdays[:2], date(2026, 10, 3)) is None
    t = B.tally(thursdays, date(2026, 10, 3))
    assert (t.times, t.days_off, t.allowance_left, t.over_allowance) == (3, 3, 1.0, 0) and t.pattern


# ---------- closing the day ----------

def test_excused_day_keeps_the_streak_and_teaches_nothing():
    day1 = minutes_on(MON, [])
    integ = Integrity(streak=4)
    from app.planners.progress import DayContext
    ctx = DayContext(integrity=integ)
    new, notes, out_integ, outcomes = close_days(BP, ITEMS, [], CAP, {}, MON, MON, ctx,
                                                 breaks=[brk(at="18:00")])
    assert new == [] and outcomes == []                         # no "missed" check-in, nothing learned
    assert out_integ.streak == 4                                # paused, not reset
    assert "excused (something came up)" in notes[0] and "no streak lost" in notes[0]
    # the work still has to happen: tomorrow plans it first, at normal capacity
    day2 = minutes_on(TUE, new)
    assert day2[0].milestone_key == "basics" and sum(s.minutes for s in day2) == 60
    # without the excuse the same day resets the streak
    _, _, plain, learned = close_days(BP, ITEMS, [], CAP, {}, MON, MON, DayContext(integrity=Integrity(streak=4)))
    assert plain.streak == 0 and len(learned) == len(day1)


def test_over_the_allowance_it_moves_but_counts_as_missed():
    from app.planners.progress import DayContext
    over = brk(at="18:00", free=False)
    new, notes, integ, outcomes = close_days(BP, ITEMS, [], CAP, {}, MON, MON,
                                             DayContext(integrity=Integrity(streak=4)), breaks=[over])
    assert [c.outcome for c in new] == ["missed"] and integ.streak == 0 and outcomes
    assert "past your monthly allowance — counted as missed" in notes[0]


def test_too_late_excuse_changes_nothing():
    from app.planners.progress import DayContext
    late = brk(at="23:50")                                      # the 19:00-20:00 session ended long ago
    new, notes, integ, _ = close_days(BP, ITEMS, [], CAP, {}, MON, MON,
                                      DayContext(integrity=Integrity(streak=4)), breaks=[late])
    assert [c.outcome for c in new] == ["missed"] and integ.streak == 0
    assert "spread over the next days" in notes[0]


# ---------- API + Telegram ----------

import app.main as main  # noqa: E402
from app.core.auth import DEV_USER_ID  # noqa: E402
from tests.test_integrations import TODAY, env, link  # noqa: E402,F401

Q = {"today": TODAY.isoformat()}


def at(monkeypatch, d: date, hh: int, mm: int = 0):
    from zoneinfo import ZoneInfo
    t = datetime(d.year, d.month, d.day, hh, mm, tzinfo=ZoneInfo("Asia/Kolkata"))
    monkeypatch.setattr(main.notify_logic, "local_now", lambda now, tz: t)
    monkeypatch.setattr(main, "_local_today", lambda ctx: d)


def test_api_rest_of_today_then_the_day_closes_without_penalty(env, monkeypatch):
    c, repo, fake, gid = env
    at(monkeypatch, TODAY, 18, 30)
    rec = repo.get(DEV_USER_ID, gid)
    rec.integrity.streak = 5
    repo.save(DEV_USER_ID, rec)
    r = c.post("/me/breaks", json={"kind": "rest_of_day", "reason": "family"}).json()
    assert r["tally"]["times"] == 1 and r["tally"]["allowance_left"] == 3.0 and r["breaks"][0]["can_undo"]
    v = c.get(f"/goals/{gid}/today", params=Q).json()
    assert all(s["excused"] for s in v["sessions"]) and v["excused_minutes"] == v["planned_minutes"] > 0
    # next morning: the day closes — no missed check-in, streak kept, nothing learned
    at(monkeypatch, TODAY + timedelta(days=1), 8)
    v2 = c.get(f"/goals/{gid}/today", params={"today": (TODAY + timedelta(days=1)).isoformat()}).json()
    rec = repo.get(DEV_USER_ID, gid)
    assert rec.checkins == [] and rec.integrity.streak == 5 and repo.list_outcomes(DEV_USER_ID, TODAY) == []
    assert any("excused (something came up)" in m for m in v2["moved"])
    # it can't be undone any more
    bid = r["breaks"][0]["brk"]["id"]
    assert c.delete(f"/me/breaks/{bid}").status_code == 409


def test_api_allowance_runs_out(env, monkeypatch):
    c, *_ = env
    for i in range(4):
        at(monkeypatch, TODAY - timedelta(days=4 - i), 18)
        c.post("/me/breaks", json={"kind": "rest_of_day"})
    at(monkeypatch, TODAY, 18)
    r = c.post("/me/breaks", json={"kind": "rest_of_day"}).json()
    assert r["breaks"][0]["brk"]["free"] is False and r["tally"]["over_allowance"] == 1
    assert r["tally"]["allowance_left"] == 0


def test_api_days_ahead_are_free_and_planned_around(env, monkeypatch):
    c, repo, fake, gid = env
    at(monkeypatch, TODAY, 10)
    tue, wed = TODAY + timedelta(days=1), TODAY + timedelta(days=2)
    before = c.get(f"/goals/{gid}/replan", params=Q).json()
    r = c.post("/me/breaks", json={"kind": "days", "first": tue.isoformat(), "last": wed.isoformat(),
                                   "reason": "travel"}).json()
    assert r["breaks"][0]["brk"]["free"] and r["tally"]["allowance_left"] == 4.0     # told ahead: free
    after = c.get(f"/goals/{gid}/replan", params=Q).json()
    days = {d["day"] for sp in after["schedule"]["sprints"] for d in sp["days"] if d["sessions"]}
    assert tue.isoformat() not in days and wed.isoformat() not in days
    assert TODAY.isoformat() in days                                                  # today untouched
    assert before != after
    assert c.post("/me/breaks", json={"kind": "days", "first": (TODAY - timedelta(days=1)).isoformat(),
                                      "last": TODAY.isoformat()}).status_code == 422  # past day
    assert c.post("/me/breaks", json={"kind": "days", "first": tue.isoformat(),
                                      "last": (tue + timedelta(days=40)).isoformat()}).status_code == 422
    # undo a future break
    assert c.delete(f"/me/breaks/{r['breaks'][0]['brk']['id']}").json()["breaks"] == []


def test_telegram_evening_skip_button(env, monkeypatch):
    c, repo, fake, gid = env
    link(c, fake)
    at(monkeypatch, TODAY, 18, 50)
    monkeypatch.setattr(main.notify_logic, "due", lambda now, prefs, tz: [] if prefs.last_evening else ["evening"])
    c.post("/cron/notify", headers={"X-Cron-Secret": "cs"})
    ev = fake.sent()[-1]
    skip = ev["reply_markup"]["inline_keyboard"][-1][0]
    assert skip["text"].startswith("🌧 Something came up")
    c.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "whs"}, json={"callback_query": {
        "id": "x", "data": skip["callback_data"], "message": {"chat": {"id": 42}, "message_id": 3}}})
    ans = [p for m, p in fake.calls if m == "answerCallbackQuery"][-1]["text"]
    assert ans.startswith("Excused 96 min") and "no streak lost" in ans
    # asking twice doesn't spend the allowance twice
    c.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "whs"},
           json={"message": {"chat": {"id": 42}, "text": "/skip"}})
    assert fake.sent()[-1]["text"] == "The rest of today is already excused."
    assert len(repo.get_settings(DEV_USER_ID).breaks) == 1


def test_telegram_skip_at_bedtime_excuses_nothing(env, monkeypatch):
    c, repo, fake, gid = env
    link(c, fake)
    at(monkeypatch, TODAY, 23, 40)                      # the 19:00-21:00 sessions ended long ago
    c.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "whs"},
           json={"message": {"chat": {"id": 42}, "text": "/skip"}})
    assert "Say it when it happens" in fake.sent()[-1]["text"]
    v = c.get(f"/goals/{gid}/today", params=Q).json()
    assert v["excused_minutes"] == 0
