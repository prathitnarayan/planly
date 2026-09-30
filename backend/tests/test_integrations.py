"""Telegram nudges (at most 2 a day, tick from chat) and read-only Google Calendar busy times."""

from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.ai.goal_intake import InterviewState
from app.core import config, gcal, telegram
from app.core.auth import DEV_USER_ID
from app.core.repo import UserSettings
from app.main import app, get_repo
from app.planners import notify
from app.planners.capacity import CapacityProfile
from app.planners.pool import busy_taken
from app.schemas.blueprint import GoalBlueprint
from app.schemas.integrations import NotifyPrefs
from app.schemas.interview import GoalProfile

IST = "Asia/Kolkata"
TODAY = date(2026, 10, 5)                                   # a Monday
SLOTS = [{"weekday": d, "start": "19:00", "end": "21:00"} for d in range(7)]
utc = lambda d, h, m=0: datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc)


# ---------- when to send ----------

def test_morning_and_evening_windows_once_a_day():
    p = NotifyPrefs()                                        # 08:00 and 21:30 local
    assert notify.due(utc(TODAY, 2, 40), p, IST) == ["morning"]          # 08:10 IST
    assert notify.due(utc(TODAY, 6, 0), p, IST) == []                     # 11:30 IST: too late for "good morning"
    assert notify.due(utc(TODAY, 16, 5), p, IST) == ["evening"]          # 21:35 IST
    sent = p.model_copy(update={"last_morning": TODAY})
    assert notify.due(utc(TODAY, 2, 40), sent, IST) == []
    off = p.model_copy(update={"evening": False})
    assert notify.due(utc(TODAY, 16, 5), off, IST) == []


# ---------- calendar ----------

def test_meetings_only_take_the_part_that_overlaps_free_slots():
    cap = CapacityProfile(slots=SLOTS)
    ist = lambda h, m=0: datetime(2026, 10, 5, h, m, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    busy = [(ist(19, 30), ist(20, 30)), (ist(15), ist(16)), (ist(20), ist(20, 45))]
    taken = busy_taken(busy, cap, IST, TODAY, TODAY + timedelta(days=7))
    assert taken == {TODAY: [(time(19, 30), time(20, 45), 75)]}           # merged, the 3pm one ignored


# ---------- API ----------

BP = GoalBlueprint.model_validate({
    "goal": "x", "category": "learning", "summary": "x",
    "milestones": [{"key": "m", "name": "Basics", "hours_by_kind": {"learn": 20}, "confidence": 0.8,
                    "done_criteria": ["ok"]}],
    "assumptions": [], "open_questions": [],
})


class FakeTelegram:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload):
        self.calls.append((method, payload))
        if method == "getWebhookInfo":
            return {"url": ""}
        return {"message_id": 1}

    def sent(self):
        return [p for m, p in self.calls if m == "sendMessage"]


@pytest.fixture
def env(monkeypatch):
    for k, v in {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_BOT_USERNAME": "planly_bot", "TELEGRAM_WEBHOOK_SECRET": "whs",
                 "CRON_SECRET": "cs", "PUBLIC_API_URL": "https://api.test", "APP_SECRET": "s" * 32,
                 "GOOGLE_CLIENT_ID": "gid", "GOOGLE_CLIENT_SECRET": "gsec", "FRONTEND_URL": "https://app.test"}.items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(telegram, "_webhook_checked", False)
    fake = FakeTelegram()
    monkeypatch.setattr(telegram, "call", fake)
    repo = app.dependency_overrides[get_repo]()
    rec = repo.create(DEV_USER_ID, InterviewState(goal="Learn DSA", done=True,
                                                  profile=GoalProfile(deadline=date(2026, 12, 31))))
    rec.blueprint, rec.capacity, rec.plan_start = BP, CapacityProfile(slots=SLOTS), TODAY
    repo.save(DEV_USER_ID, rec)
    repo.save_settings(DEV_USER_ID, UserSettings(capacity=CapacityProfile(slots=SLOTS)))
    monkeypatch.setattr(main, "server_today", lambda: TODAY)
    monkeypatch.setattr(main, "_local_today", lambda ctx: TODAY)
    return TestClient(app), repo, fake, rec.id


def link(c, fake):
    url = c.post("/me/telegram/link").json()["url"]
    code = url.split("start=")[1]
    r = c.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "whs"},
               json={"message": {"chat": {"id": 42}, "from": {"username": "utk"}, "text": f"/start {code}"}})
    assert r.status_code == 200


def test_link_telegram_with_one_time_code(env):
    c, repo, fake, gid = env
    assert c.get("/me/settings").json()["telegram"] == {"available": True, "linked": False, "username": None, "bot": "planly_bot"}
    link(c, fake)
    st = repo.get_settings(DEV_USER_ID)
    assert st.telegram.chat_id == 42 and st.telegram.link_code is None
    assert "Linked to Planly" in fake.sent()[-1]["text"]
    assert ("setWebhook", {"url": "https://api.test/telegram/webhook", "secret_token": "whs",
                           "allowed_updates": ["message", "callback_query"]}) in fake.calls
    # the same code can't be reused, and a wrong secret is refused
    assert c.post("/telegram/webhook", json={"message": {"chat": {"id": 1}, "text": "/start x"}}).status_code == 403


def test_cron_sends_morning_once_and_buttons_tick(env, monkeypatch):
    c, repo, fake, gid = env
    link(c, fake)
    monkeypatch.setattr(notify, "due", lambda now, prefs, tz: [] if prefs.last_morning else ["morning"])
    assert c.post("/cron/notify").status_code == 403
    assert c.post("/cron/notify", headers={"X-Cron-Secret": "cs"}).json() == {"sent": 1}
    msg = fake.sent()[-1]
    assert "Today · Mon 05 Oct" in msg["text"] and "Basics" in msg["text"]
    button = msg["reply_markup"]["inline_keyboard"][0][0]
    assert button["text"].startswith("Tick: Basics")
    assert c.post("/cron/notify", headers={"X-Cron-Secret": "cs"}).json() == {"sent": 0}   # once a day

    c.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "whs"}, json={"callback_query": {
        "id": "cb1", "data": button["callback_data"], "message": {"chat": {"id": 42}, "message_id": 7}}})
    rec = repo.get(DEV_USER_ID, gid)
    assert len(rec.ticks.days[TODAY.isoformat()]) == 1                       # ticked from Telegram
    assert any(m == "answerCallbackQuery" and p["text"] == "Ticked ✓" for m, p in fake.calls)
    assert any(m == "editMessageText" and "✓" in p["text"] for m, p in fake.calls)


def test_evening_only_when_something_is_open(env, monkeypatch):
    c, repo, fake, gid = env
    link(c, fake)
    rec = repo.get(DEV_USER_ID, gid)
    view = c.get(f"/goals/{gid}/today", params={"today": TODAY.isoformat()}).json()
    c.put(f"/goals/{gid}/ticks", json={"day": TODAY.isoformat(), "session_id": view["sessions"][0]["id"], "done": True})
    monkeypatch.setattr(notify, "due", lambda now, prefs, tz: [] if prefs.last_evening else ["evening"])
    before = len(fake.sent())
    assert c.post("/cron/notify", headers={"X-Cron-Secret": "cs"}).json() == {"sent": 0}   # all done: stay quiet
    assert len(fake.sent()) == before
    assert repo.get_settings(DEV_USER_ID).notify.last_evening == TODAY


def test_stop_unlinks(env):
    c, repo, fake, gid = env
    link(c, fake)
    c.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "whs"},
           json={"message": {"chat": {"id": 42}, "text": "/stop"}})
    assert repo.get_settings(DEV_USER_ID).telegram.chat_id is None


def test_settings_update_keeps_sent_markers_and_checks_timezone(env):
    c, repo, fake, gid = env
    st = repo.get_settings(DEV_USER_ID)
    st.notify.last_morning = TODAY
    repo.save_settings(DEV_USER_ID, st)
    r = c.put("/me/settings", json={"timezone": "Europe/London",
                                    "notify": {"morning": False, "morning_at": "07:15", "evening": True, "evening_at": "22:00"}})
    got = repo.get_settings(DEV_USER_ID)
    assert r.status_code == 200 and got.timezone == "Europe/London" and got.notify.last_morning == TODAY
    assert got.notify.morning is False and got.notify.morning_at == time(7, 15)
    assert c.put("/me/settings", json={"timezone": "Mars/Base"}).status_code == 422


def test_google_connect_read_only_busy_times_shrink_the_evening(env, monkeypatch):
    c, repo, fake, gid = env
    before = c.get(f"/goals/{gid}/today", params={"today": TODAY.isoformat()}).json()["planned_minutes"]
    url = c.get("/me/google/connect").json()["url"]
    assert "calendar.freebusy" in url and "access_type=offline" in url
    state = url.split("state=")[1].split("&")[0]
    monkeypatch.setattr(gcal, "exchange", lambda code: "refresh-123")
    r = c.get("/google/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "https://app.test/settings?google=connected"
    st = repo.get_settings(DEV_USER_ID)
    assert st.google.connected and "refresh-123" not in st.google.refresh_token_enc      # stored encrypted
    assert gcal.decrypt(st.google.refresh_token_enc) == "refresh-123"
    ist = timezone(timedelta(hours=5, minutes=30))
    meeting = (datetime(2026, 10, 5, 19, 0, tzinfo=ist), datetime(2026, 10, 5, 20, 0, tzinfo=ist))
    monkeypatch.setattr(gcal, "freebusy", lambda refresh, a, b: [meeting])
    after = c.get(f"/goals/{gid}/today", params={"today": TODAY.isoformat()}).json()
    assert before == 96 and after["planned_minutes"] == 36                  # 96 - the 60-min meeting
    assert after["sessions"][0]["start"] == "20:00"                          # placed after the meeting
    assert c.get("/me/settings").json()["google"]["connected"] is True
    bad = c.get("/google/callback", params={"code": "x", "state": "forged"}, follow_redirects=False)
    assert bad.headers["location"].endswith("google=failed")
    c.delete("/me/google")
    assert not repo.get_settings(DEV_USER_ID).google.connected
