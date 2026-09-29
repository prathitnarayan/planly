"""Course sync: durations/dates parsed by code, load measured by code, AI only copies."""

import json
from datetime import date

from fastapi.testclient import TestClient

from app.ai.source_extract import chunk_text, extract_source, merge_items
from app.main import app, get_llm
from app.planners.load import coverage_warning, course_key_dates, source_load, study_minutes
from app.schemas.source import (
    CourseSource, ExtractedItem, SourceItem, parse_due, parse_duration,
)

TODAY = date(2026, 9, 29)


class FakeLLM:
    def __init__(self, replies):
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.calls = []

    def complete_json(self, system, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


# ---------- parsing ----------

def test_durations_are_parsed_by_code_and_rounded_up():
    assert parse_duration("12:34") == 13
    assert parse_duration("1:02:03") == 63
    assert parse_duration("1h 5m") == 65
    assert parse_duration("1 hr 5 mins") == 65
    assert parse_duration("PT4M13S") == 5
    assert parse_duration("1.5 hours") == 90
    assert parse_duration("12m 30s") == 13
    assert parse_duration("45 min") == 45
    assert parse_duration("no idea") is None
    assert parse_duration(None) is None


def test_due_dates_code_picks_the_year_not_the_ai():
    assert parse_due("15 Oct 2026, 11:59 PM", TODAY) == date(2026, 10, 15)
    assert parse_due("Oct 15", TODAY) == date(2026, 10, 15)
    assert parse_due("15/10", TODAY) == date(2026, 10, 15)       # day first
    assert parse_due("Due: 5th Nov", TODAY) == date(2026, 11, 5)
    assert parse_due("Jan 3", TODAY) == date(2027, 1, 3)          # nearest to today
    assert parse_due("31/02", TODAY) is None
    assert parse_due("sometime soon", TODAY) is None


def test_ai_kind_aliases_are_normalised():
    assert ExtractedItem(title="x", kind="Lecture").kind == "video"
    assert ExtractedItem(title="x", kind="mock").kind == "test"
    assert ExtractedItem(title="x", kind="weird").kind == "other"
    assert ExtractedItem(title="x", difficulty="Medium").difficulty == "medium"
    assert ExtractedItem(title="x", difficulty="1400").difficulty is None


# ---------- load ----------

def course():
    return CourseSource(url="https://seek/mlp", platform="iitm", title="MLP", items=[
        SourceItem(title="L1", section="Week 1", kind="video", minutes=20, done=True),
        SourceItem(title="L2", section="Week 1", kind="video", minutes=30),
        SourceItem(title="L3", section="Week 2", kind="video", minutes=10),
        SourceItem(title="Two Sum", kind="practice", difficulty="easy"),
        SourceItem(title="LRU", kind="practice", difficulty="hard"),
        SourceItem(title="GA 2", section="Week 2", kind="assignment", due=date(2026, 10, 15)),
        SourceItem(title="Quiz 1", kind="test", minutes=60, due=date(2026, 10, 20)),
        SourceItem(title="Old GA", kind="assignment", due=date(2026, 9, 1)),
    ])


def test_load_skips_done_and_does_not_guess_assignments():
    load = source_load(course(), TODAY)
    # videos 30+10 = 40 shown -> x1.5 = 60; practice 20+60; test 60x1.5=90
    assert load.study_minutes == 60 + 80 + 90
    assert load.done == 1 and load.items == 8
    assert load.unsized == ["GA 2", "Old GA"]          # asked, not guessed
    video = next(k for k in load.by_kind if k.kind == "video")
    assert (video.remaining, video.shown_minutes, video.study_minutes) == (2, 40, 60)
    assert [d.title for d in load.due] == ["GA 2", "Quiz 1"]   # past date dropped


def test_video_without_length_is_unsized_not_zero():
    assert study_minutes(SourceItem(title="x", kind="video")) is None


def test_graded_work_becomes_hard_key_dates():
    kds = course_key_dates(course(), TODAY)
    assert [(k.key, k.date, k.hard) for k in kds] == [
        ("src_week_2_ga_2", date(2026, 10, 15), True),
        ("src_quiz_1", date(2026, 10, 20), True),
    ]


def test_coverage_warning_when_plan_ignores_measured_work():
    assert coverage_warning(1.0, [course()], TODAY) is not None
    assert coverage_warning(10.0, [course()], TODAY) is None


# ---------- extraction ----------

def test_chunks_split_on_lines_and_hard_split_giant_lines():
    text = "\n".join(f"line {i}" for i in range(100))
    chunks = chunk_text(text, size=50)
    assert "\n".join(chunks) == text
    assert all(len(c) <= 50 for c in chunks)
    assert chunk_text("x" * 120, size=50) == ["x" * 50, "x" * 50, "x" * 20]


def test_merge_fills_blanks_and_keeps_done():
    a = ExtractedItem(title="Lecture 1", section="Week 1", kind="video")
    b = ExtractedItem(title="lecture  1", section="week 1", duration_text="10:00", done=True)
    [m] = merge_items([a, b])
    assert (m.kind, m.duration_text, m.done) == ("video", "10:00", True)


def test_extract_passes_previous_section_and_parses_in_code():
    text = "A" * 30 + "\n" + "B" * 30
    llm = FakeLLM([
        {"course_title": "MLP", "items": [
            {"title": "L1", "section": "Week 1", "kind": "video", "duration_text": "12:01"}]},
        {"course_title": None, "items": [
            {"title": "GA", "section": "Week 1", "kind": "assignment", "due_text": "15/10"}]},
    ])
    import app.ai.source_extract as se
    old = se.CHUNK_CHARS
    se.CHUNK_CHARS = 40
    try:
        src = extract_source(llm, url="u", platform="iitm", text=text, today=TODAY)
    finally:
        se.CHUNK_CHARS = old
    assert len(llm.calls) == 2
    assert json.loads(llm.calls[1][0]["content"].split("\n", 1)[1])["previous_section"] == "Week 1"
    assert src.title == "MLP"
    assert src.items[0].minutes == 13
    assert src.items[1].due == date(2026, 10, 15)


# ---------- API ----------

def test_api_sync_page_stores_course_adds_deadlines_and_resync_replaces(monkeypatch):
    import app.main as main
    from app.core.auth import DEV_USER_ID
    monkeypatch.setattr(main, "date", type("D", (date,), {"today": staticmethod(lambda: TODAY)}))
    llm = FakeLLM([
        {"course_title": "MLP", "items": [
            {"title": "L1", "section": "Week 1", "kind": "video", "duration_text": "20:00"},
            {"title": "GA 1", "section": "Week 1", "kind": "assignment", "due_text": "15 Oct"}]},
        {"course_title": "MLP", "items": [
            {"title": "L1", "section": "Week 1", "kind": "video", "duration_text": "20:00", "done": True},
            {"title": "GA 1", "section": "Week 1", "kind": "assignment", "due_text": "17 Oct"}]},
    ])
    app.dependency_overrides[get_llm] = lambda: llm
    client = TestClient(app)
    from app.ai.goal_intake import InterviewState
    from app.main import get_repo
    repo = app.dependency_overrides[get_repo]()
    gid = repo.create(DEV_USER_ID, InterviewState(goal="Finish MLP")).id

    body = {"url": "https://seek/mlp", "platform": "iitm", "text": "page text"}
    r = client.post(f"/goals/{gid}/sources/page", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["load"]["study_minutes"] == 30
    assert out["key_dates_added"] == ["GA 1 — MLP — Thu 15 Oct"]

    r = client.post(f"/goals/{gid}/sources/page", json=body)   # re-sync: same url replaces
    assert r.json()["key_dates_moved"] == ["GA 1 — MLP — now Sat 17 Oct"]
    views = client.get(f"/goals/{gid}/sources").json()
    assert len(views) == 1 and views[0]["load"]["done"] == 1
    assert client.get(f"/goals/{gid}").json()["profile"]["key_dates"][0]["date"] == "2026-10-17"

    assert client.delete(f"/goals/{gid}/sources", params={"url": "https://seek/mlp"}).status_code == 200
    assert client.get(f"/goals/{gid}/sources").json() == []


def test_api_structured_items_need_no_ai():
    from app.ai.goal_intake import InterviewState
    from app.core.auth import DEV_USER_ID
    from app.main import get_repo
    repo = app.dependency_overrides[get_repo]()
    gid = repo.create(DEV_USER_ID, InterviewState(goal="DSA")).id
    client = TestClient(app)
    r = client.post(f"/goals/{gid}/sources", json={
        "url": "https://youtube.com/playlist?list=x", "platform": "youtube", "title": "Striver Graphs",
        "items": [{"title": "G-1", "kind": "video", "minutes": 20}]})
    assert r.status_code == 200 and r.json()["load"]["study_minutes"] == 30


# ---------- server-side reading: YouTube API + public pages (network mocked) ----------

def _client_with_goal(llm=None):
    from app.ai.goal_intake import InterviewState
    from app.core.auth import DEV_USER_ID
    from app.main import get_repo
    llm = llm or FakeLLM([])
    app.dependency_overrides[get_llm] = lambda: llm
    repo = app.dependency_overrides[get_repo]()
    return TestClient(app), repo.create(DEV_USER_ID, InterviewState(goal="DSA")).id


def test_link_youtube_playlist_uses_exact_lengths(monkeypatch):
    from app.core import web_read
    monkeypatch.setattr(web_read, "youtube_playlist", lambda url: ("Striver Graphs", [
        {"id": "a" * 11, "title": "G-1 Intro", "duration": "PT12M34S"},
        {"id": "b" * 11, "title": "G-2 BFS", "duration": "PT1H2M3S"}]))
    client, gid = _client_with_goal()
    r = client.post(f"/goals/{gid}/sources/link", json={"url": "https://www.youtube.com/playlist?list=PLx"})
    assert r.status_code == 200, r.text
    assert [i["minutes"] for i in r.json()["items"]] == [13, 63]
    assert r.json()["load"]["study_minutes"] == round(13 * 1.5) + round(63 * 1.5)


def test_link_explains_when_server_cannot_read(monkeypatch):
    from app.core import web_read

    def refuse(url):
        raise web_read.NotAvailable("That page needs a login. Use the Planly Chrome extension on it instead.")
    monkeypatch.setattr(web_read, "public_page", refuse)
    client, gid = _client_with_goal()
    r = client.post(f"/goals/{gid}/sources/link", json={"url": "https://www.udemy.com/course/x/learn"})
    assert r.status_code == 422 and "Chrome extension" in r.json()["detail"]


def test_link_public_page_goes_through_the_reader(monkeypatch):
    from app.core import web_read
    monkeypatch.setattr(web_read, "public_page", lambda url: ("Python Bootcamp", "Section 1\nIntro 5:00\n" * 20))
    llm = FakeLLM([{"course_title": None, "items": [{"title": "Intro", "kind": "video", "duration_text": "5:00"}]}])
    client, gid = _client_with_goal(llm)
    r = client.post(f"/goals/{gid}/sources/link", json={"url": "https://www.udemy.com/course/python/"})
    assert r.status_code == 200, r.text
    assert r.json()["load"]["title"] == "Python Bootcamp" and r.json()["load"]["platform"] == "udemy"


def test_embedded_youtube_lengths_are_added_for_the_reader(monkeypatch):
    from app.core import config, web_read
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "k")
    monkeypatch.setattr(web_read, "youtube_videos", lambda ids: {"x" * 11: {"title": "L1.1 Intro", "duration": "PT10M"}})
    llm = FakeLLM([{"course_title": "MLP", "items": []}])
    client, gid = _client_with_goal(llm)
    client.post(f"/goals/{gid}/sources/page", json={"url": "https://seek/x", "platform": "iitm",
                                                     "text": "Week 1", "youtube_ids": ["x" * 11]})
    sent = llm.calls[0][0]["content"]
    assert "Embedded videos (exact lengths)" in sent and "L1.1 Intro | PT10M" in sent


def test_private_addresses_are_refused():
    import pytest

    from app.core import web_read
    for url in ("http://127.0.0.1/", "http://localhost:8000/docs", "http://169.254.169.254/latest", "file:///etc/passwd"):
        with pytest.raises(web_read.NotAvailable):
            web_read._check_public_host(url)


def test_html_to_text_drops_scripts_and_keeps_lines():
    from app.core.web_read import html_to_text
    title, text = html_to_text("<html><head><title>Course</title><script>x=1</script></head>"
                               "<body><li>Intro <b>5:00</b></li><li>Loops 7:00</li></body></html>")
    assert title == "Course" and text.splitlines() == ["Intro 5:00", "Loops 7:00"]
