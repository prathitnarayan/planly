"""Habit streaks, the user's own risky times, daily quote, and the messages built from them."""

from datetime import date, datetime, timedelta

from app.planners import habits as H
from app.planners import notify
from app.planners.habits import Habit, Urge

D0 = date(2026, 9, 1)            # a Tuesday
day = lambda n: D0 + timedelta(days=n)


def smoke(**kw) -> Habit:
    return Habit(name="No smoking", started=D0, **kw)


def log(pattern: str) -> dict[date, bool]:
    """'++-+.' -> day 0 kept, 1 kept, 2 slip, 3 kept, 4 blank."""
    return {day(i): c == "+" for i, c in enumerate(pattern) if c in "+-"}


# ---------- streaks ----------

def test_slip_resets_current_but_keeps_best_and_record():
    st = H.stats(smoke(), log("+++++-+++"), day(8))
    assert (st.current, st.best, st.previous_best) == (3, 5, 5)
    assert st.kept_window == 8 and st.window == 9 and st.slips_window == 1
    assert st.last_slip == day(5)


def test_blank_days_neither_count_nor_break():
    st = H.stats(smoke(), log("++..++"), day(5))
    assert st.current == 4 and st.best == 4
    assert [s.state for s in st.strip[-6:]] == ["kept", "kept", "blank", "blank", "kept", "kept"]


def test_today_unanswered_window_ends_yesterday():
    st = H.stats(smoke(), log("+++"), day(3))     # day 3 = today, not answered yet
    assert st.today is None and st.window == 3 and st.kept_window == 3   # not "3 of 4"
    assert st.yesterday is True


def test_new_record_and_milestone_lines():
    h = smoke(why="I want to run 10k without coughing")
    st = H.stats(h, log("++++-" + "+" * 7), day(11))
    assert (st.current, st.previous_best) == (7, 4)
    out = H.lines(h, st, day(11), None)
    assert out[0] == "New best: 7 days (old best 4)."
    assert out[-1] == "Your reason: “I want to run 10k without coughing”"
    first = H.stats(h, log("+++"), day(2))
    assert H.lines(h, first, day(2), None) == ["3 days clean — a real start.", out[-1]]


def test_after_a_slip_the_message_is_the_30_day_record():
    st = H.stats(smoke(), log("++++++++-"), day(9))
    assert H.lines(smoke(), st, day(9), None)[0] == \
        "Yesterday slipped. You're still 8 of the last 9 days clean — one day doesn't undo that."


def test_build_habit_wording():
    gym = Habit(name="Gym", kind="build", started=D0)
    st = H.stats(gym, log("+++"), day(2))
    assert "3 days done" in H.lines(gym, st, day(2), None)[0]


# ---------- their own patterns ----------

def urge(d: date, h: int, m: int = 0) -> Urge:
    t = datetime(d.year, d.month, d.day, h, m)
    return Urge(habit_id="x", at=t, local=t)


def test_risky_time_needs_enough_urges_in_one_window():
    today = day(20)
    assert H.risk_time([urge(day(1), 22), urge(day(2), 23), urge(day(3), 22)], today) is None   # only 3
    r = H.risk_time([urge(day(1), 22), urge(day(2), 23, 40), urge(day(3), 22, 10), urge(day(4), 9)], today)
    assert r and r.hour == 22 and r.label == "22:00–00:00" and (r.in_window, r.total) == (3, 4)
    assert r.nudge_at(today) == datetime(2026, 9, 21, 21, 30)
    scattered = [urge(day(i), h) for i, h in enumerate([2, 6, 10, 14, 18, 22])]
    assert H.risk_time(scattered, today) is None                 # no real pattern: no nudge
    old = [urge(day(-80 + i), 22) for i in range(5)]
    assert H.risk_time(old, today) is None                       # too old to matter


def test_risky_weekday_from_slips():
    fridays = {D0 + timedelta(days=3 + 7 * i): False for i in range(3)}   # Fri 4, 11, 18 Sep
    fridays[day(1)] = False                                               # one Wednesday
    r = H.risk_day(fridays, day(20))
    assert r and r.name == "Friday" and (r.slips, r.total) == (3, 4)
    friday = D0 + timedelta(days=24)
    line = H.lines(smoke(), H.stats(smoke(), fridays, friday), friday, r)
    assert any("Fridays are when most of your slips happen (3 of your last 4)" in x for x in line)


# ---------- quote ----------

def test_quote_same_all_day_and_cycles_through_the_list():
    q = [H.quote_for("user-a", day(i)) for i in range(len(H.QUOTES))]
    assert len(set(q)) == len(H.QUOTES)                           # no repeats within one full cycle
    assert H.quote_for("user-a", day(0)) == q[0]


# ---------- messages ----------

def brief(h: Habit, pattern: str, today: date) -> notify.HabitBrief:
    st = H.stats(h, log(pattern), today)
    return notify.HabitBrief(habit=h, stats=st, lines=H.lines(h, st, today, None))


def test_quote_only_morning_when_nothing_else():
    text, rows = notify.build_message("morning", day(3), [], [], ("Well begun is half done.", "Aristotle"))
    assert text.startswith("<i>“Well begun is half done.”</i> — Aristotle")       # the lock-screen glance
    assert "Today · Fri 04 Sep" in text and rows == []
    assert notify.build_message("morning", day(3), [], [], None) == (None, [])


def test_morning_asks_about_a_blank_yesterday_using_the_private_label():
    h = smoke(label="H1")
    text, rows = notify.build_message("morning", day(3), [], [brief(h, "++", day(3))], None)
    assert "No smoking" not in text and "<b>H1</b> — day 2" in text
    (yes, y), (no, n) = rows[0]
    assert yes == "H1: ✓ clean yesterday" and no == "✗ slipped"
    assert y == {"t": "h", "habit_id": h.id, "day": "2026-09-03", "kept": True} and n["kept"] is False


def test_evening_only_when_a_habit_is_unanswered():
    h = smoke()
    assert notify.build_message("evening", day(2), [], [brief(h, "+++", day(2))])[0] is None   # answered
    text, rows = notify.build_message("evening", day(2), [], [brief(h, "++", day(2))])
    assert "<b>Evening check</b>" in text and "clean today? (day 2 so far)" in text
    assert rows[0][0][1]["day"] == "2026-09-03"


def test_nudge_time_window_once_a_day_and_across_midnight():
    r = H.RiskTime(hour=22, in_window=3, total=4)
    at = lambda h, m: datetime(2026, 9, 5, h, m)
    assert notify.nudge_due(at(21, 40), r, None)
    assert not notify.nudge_due(at(21, 40), r, date(2026, 9, 5))           # already sent today
    assert not notify.nudge_due(at(20, 0), r, None) and not notify.nudge_due(at(22, 45), r, None)
    midnight = H.RiskTime(hour=0, in_window=3, total=4)
    assert notify.nudge_due(at(23, 45), midnight, None)                    # 23:30 the evening before
    text, rows = notify.build_nudge(brief(smoke(why="lungs", label="H1"), "+++", day(3)), r)
    assert "Heads-up · H1" in text and "22:00–00:00" in text and "lungs" in text
    assert rows[0][0][1] == {"t": "u", "habit_id": rows[0][0][1]["habit_id"]}


def test_urge_reply_uses_their_numbers():
    h = smoke(why="lungs")
    st = H.stats(h, log("+++++-++"), day(7))
    msg = H.urge_reply(h, st, 2)
    assert msg.startswith("Logged. 2 days so far — your best is 5.") and "“lungs”" in msg and "2 today" in msg


# ---------- API + Telegram ----------

import pytest  # noqa: E402

import app.main as main  # noqa: E402
from app.core.auth import DEV_USER_ID  # noqa: E402
from tests.test_integrations import FakeTelegram, TODAY, env, link  # noqa: E402,F401

WH = {"X-Telegram-Bot-Api-Secret-Token": "whs"}
Q = {"today": TODAY.isoformat()}


def add(c, **kw):
    r = c.post("/habits", json={"name": "No smoking", "why": "lungs", "label": "H1", "today": TODAY.isoformat(), **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_habit_crud_days_and_rules(env):
    c, repo, fake, gid = env
    v = add(c, started=(TODAY - timedelta(days=3)).isoformat())
    hid = v["habit"]["id"]
    assert v["stats"]["current"] == 0 and len(v["stats"]["strip"]) == 30
    for i, kept in [(3, True), (2, True), (1, False)]:
        r = c.put(f"/habits/{hid}/days", json={"day": (TODAY - timedelta(days=i)).isoformat(), "kept": kept, **Q})
    v = c.put(f"/habits/{hid}/days", json={"day": TODAY.isoformat(), "kept": True, **Q}).json()
    assert (v["stats"]["current"], v["stats"]["best"], v["stats"]["kept_window"], v["stats"]["window"]) == (1, 2, 3, 4)
    assert v["lines"][0].startswith("Yesterday slipped. You're still 3 of the last 4 days clean")
    bad = lambda d: c.put(f"/habits/{hid}/days", json={"day": d.isoformat(), "kept": True, **Q}).status_code
    assert bad(TODAY + timedelta(days=1)) == 422 and bad(TODAY - timedelta(days=4)) == 422   # future / before start
    # edit: clear the label, archive
    v = c.patch(f"/habits/{hid}", json={"label": None, "archived": True, **Q}).json()
    assert v["habit"]["label"] is None and v["habit"]["archived"] is True and v["habit"]["why"] == "lungs"
    assert c.get("/habits", params=Q).json()[0]["habit"]["archived"] is True
    # another user can't see or touch it
    repo.save_settings("someone-else", repo.get_settings("someone-else"))
    assert repo.get_habit("someone-else", hid) is None
    with pytest.raises(KeyError):
        repo.set_habit_day("someone-else", hid, TODAY, True)
    assert c.delete(f"/habits/{hid}").status_code == 200 and c.get("/habits", params=Q).json() == []
    assert c.delete(f"/habits/{hid}").status_code == 404


def test_limit_of_ten_active_habits(env):
    c, *_ = env
    for i in range(10):
        add(c, name=f"h{i}")
    assert c.post("/habits", json={"name": "one more", **Q}).status_code == 409


def test_quote_only_morning_for_someone_with_no_goals_or_habits(env, monkeypatch):
    c, repo, fake, gid = env
    repo.delete(DEV_USER_ID, gid)
    link(c, fake)
    monkeypatch.setattr(main.notify_logic, "due", lambda now, prefs, tz: [] if prefs.last_morning else ["morning"])
    assert c.post("/cron/notify", headers={"X-Cron-Secret": "cs"}).json() == {"sent": 1}
    text = fake.sent()[-1]["text"]
    q, a = H.quote_for(DEV_USER_ID, TODAY)
    assert text.startswith(f"<i>“{q}”</i>") and "Today · Mon 05 Oct" in text and "reply_markup" not in fake.sent()[-1]
    st = repo.get_settings(DEV_USER_ID)
    st.notify.quote = False
    st.notify.last_morning = None
    repo.save_settings(DEV_USER_ID, st)
    assert c.post("/cron/notify", headers={"X-Cron-Secret": "cs"}).json() == {"sent": 0}   # nothing to say


def test_morning_button_answers_yesterday_and_evening_asks_today(env, monkeypatch):
    c, repo, fake, gid = env
    repo.delete(DEV_USER_ID, gid)
    link(c, fake)
    hid = add(c, started=(TODAY - timedelta(days=2)).isoformat())["habit"]["id"]
    c.post("/me/telegram/test")
    msg = fake.sent()[-1]
    assert "<b>H1</b> — day 0" in msg["text"] and "No smoking" not in msg["text"]      # private label only
    yes = msg["reply_markup"]["inline_keyboard"][0][0]
    assert yes["text"] == "H1: ✓ clean yesterday"
    c.post("/telegram/webhook", headers=WH, json={"callback_query": {
        "id": "cb", "data": yes["callback_data"], "message": {"chat": {"id": 42}, "message_id": 5}}})
    days = repo.habit_days(DEV_USER_ID, TODAY - timedelta(days=5))
    assert [(d.day, d.kept) for d in days] == [(TODAY - timedelta(days=1), True)]
    assert any(m == "answerCallbackQuery" and p["text"] == "Kept ✓" for m, p in fake.calls)
    edited = [p for m, p in fake.calls if m == "editMessageText"][-1]
    assert "day 1" in edited["text"] and edited["reply_markup"] == {"inline_keyboard": []}   # asked once only

    # evening: no goals, but today isn't answered -> one check
    monkeypatch.setattr(main.notify_logic, "due", lambda now, prefs, tz: [] if prefs.last_evening else ["evening"])
    assert c.post("/cron/notify", headers={"X-Cron-Secret": "cs"}).json() == {"sent": 1}
    ev = fake.sent()[-1]
    assert "<b>H1</b> — clean today? (day 1 so far)" in ev["text"]
    slip = ev["reply_markup"]["inline_keyboard"][0][1]
    c.post("/telegram/webhook", headers=WH, json={"callback_query": {
        "id": "cb2", "data": slip["callback_data"], "message": {"chat": {"id": 42}, "message_id": 6}}})
    assert repo.get_habit(DEV_USER_ID, hid) and \
        {d.day: d.kept for d in repo.habit_days(DEV_USER_ID, TODAY)}[TODAY] is False
    # the morning buttons are still valid after the evening message was sent (2-day token life)
    assert any(t["msg"] == "morning" for t in repo.get_settings(DEV_USER_ID).telegram.buttons.values())


def test_urge_command_and_nudge_from_their_own_urge_times(env):
    c, repo, fake, gid = env
    link(c, fake)
    hid = add(c, started=(TODAY - timedelta(days=10)).isoformat())["habit"]["id"]
    c.post("/telegram/webhook", headers=WH, json={"message": {"chat": {"id": 42}, "text": "/urge"}})
    assert fake.sent()[-1]["text"].startswith("Logged. 0 days so far") and "“lungs”" in fake.sent()[-1]["text"]
    ctx = main.Ctx(user_id=DEV_USER_ID, repo=repo)
    ist = lambda d, h, m=0: datetime(d.year, d.month, d.day, h, m)
    utc_at = lambda d, h, m=0: datetime(d.year, d.month, d.day, h, m, tzinfo=main.timezone.utc)
    # 21:40 IST = 16:10 UTC. No pattern yet -> no nudge.
    assert not main._send_nudge(ctx, utc_at(TODAY, 16, 10))
    for i, (h, m) in enumerate([(22, 5), (22, 40), (23, 15), (22, 30)]):
        d = TODAY - timedelta(days=i + 1)
        repo.add_urge(DEV_USER_ID, Urge(habit_id=hid, at=utc_at(d, 0), local=ist(d, h, m)))
    ctx = main.Ctx(user_id=DEV_USER_ID, repo=repo)
    assert main._send_nudge(ctx, utc_at(TODAY, 16, 10))
    n = fake.sent()[-1]
    assert "Heads-up · H1" in n["text"] and "22:00–00:00" in n["text"]
    assert n["reply_markup"]["inline_keyboard"][0][0]["text"] == "🔥 Urge right now — log it"
    ctx = main.Ctx(user_id=DEV_USER_ID, repo=repo)
    assert not main._send_nudge(ctx, utc_at(TODAY, 16, 20))                 # once a day
    # tapping the urge button logs it
    before = len(repo.list_urges(DEV_USER_ID, TODAY - timedelta(days=30)))
    c.post("/telegram/webhook", headers=WH, json={"callback_query": {"id": "u", "data": n["reply_markup"][
        "inline_keyboard"][0][0]["callback_data"], "message": {"chat": {"id": 42}, "message_id": 9}}})
    assert len(repo.list_urges(DEV_USER_ID, TODAY - timedelta(days=30))) == before + 1
    # the web view shows the learned risky time
    v = c.get("/habits", params=Q).json()[0]
    assert v["risk_time"] == "22:00–00:00" and v["urges_7d"] >= 4
