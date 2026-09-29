"""
Planly API.

Every /goals route needs a signed-in user (Supabase token in `Authorization: Bearer`),
except in local dev mode (no SUPABASE_URL in .env), where everyone is one dev user.
In /docs, click "Authorize" and paste a token from `python -m scripts.login`.

Flow:
  POST /goals/check            {"text"}                  is it a goal or background?
  POST /goals                  {"goal", "background"?}   -> first question
  POST /goals/{id}/answer      {"answer"}                -> next question, until done
  POST /goals/{id}/correct     {"text"}                  fix "what I understood"
  POST /goals/{id}/blueprint                             -> milestones + hours
  GET  /goals/{id}/estimate-check                        -> vs. the user's track record
  PATCH .../deliverables/{d}   {"hours"}                 -> user's own estimate
  PUT  /goals/{id}/capacity    {slots...}                -> weekly free time
  GET  /goals/{id}/replan                                -> feasibility + schedule from today
  POST /goals/{id}/checkins    {"checkins": [...]}       -> log what happened, replan
  POST /goals/{id}/sources/page {url, platform, text}    <- sync tool: course page -> items -> load
  GET  /goals/{id}/sources                               -> courses + measured work left
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.ai.goal_intake import (
    answer_question, apply_correction, check_goal, generate_blueprint, start_interview,
)
from app.ai.source_extract import extract_source
from app.ai.llm import LLMClient, LLMOutputError, OpenAIClient
from app.core import config
from app.core import web_read
from app.core.auth import current_user
from app.core.estimate_log import make_row
from app.core.repo import (
    DatabaseUnavailable, GoalRecord, GoalRepo, GoalSummary, InMemoryRepo, PostgresRepo,
)
from app.planners.capacity import CapacityProfile
from app.planners.estimate_check import EstimateWarning, check_estimates
from app.planners.load import (
    SourceLoad, blueprint_brief, course_key_dates, coverage_warning, source_load,
)
from app.planners.feasibility import FeasibilityResult, check_feasibility, resolve_items
from app.planners.progress import CheckIn, DueSession, ReplanResult, due_sessions, replan
from app.planners.scheduler import Schedule, build_schedule
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalCheck, GoalProfile
from app.schemas.source import CourseSource, SourceItem, parse_duration

app = FastAPI(title="Planly API", version="0.4.0")

from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

# The browser frontend runs on another port; allow it (and only it) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.FRONTEND_ORIGINS,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)


try:  # only when Postgres support is installed
    from psycopg import errors as pg_errors

    @app.exception_handler(pg_errors.UndefinedTable)
    async def tables_missing(_: Request, exc: Exception) -> JSONResponse:
        """Real first run (28 Sep): a bare 500 when the migration hadn't been run yet."""
        return JSONResponse(status_code=503, content={
            "detail": "Database tables are missing. In Supabase: SQL Editor -> New query -> paste "
                      "database/migrations/001_planly.sql -> Run.",
            "error": str(exc).splitlines()[0],
        })

    @app.exception_handler(pg_errors.UndefinedColumn)
    async def columns_missing(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=503, content={
            "detail": "The database needs a newer migration. In Supabase: SQL Editor -> run every "
                      "file in database/migrations/ you haven't run yet (e.g. 002_tracking.sql).",
            "error": str(exc).splitlines()[0],
        })
except ImportError:  # pragma: no cover
    pass


# ---------- dependencies ----------

@lru_cache
def get_llm() -> LLMClient:
    return OpenAIClient(config.OPENAI_API_KEY, config.OPENAI_MODEL, config.OPENAI_BASE_URL)


_repo: GoalRepo | None = None


def get_repo() -> GoalRepo:
    """Created on first use. If the database is unreachable, every request says why (503) —
    and we retry at most once per request, not in a background loop."""
    global _repo
    if _repo is None:
        config.check_safe_config()
        if config.DATABASE_URL:
            try:
                _repo = PostgresRepo(config.DATABASE_URL)
            except DatabaseUnavailable as e:
                raise HTTPException(status_code=503, detail=str(e))
        else:
            _repo = InMemoryRepo()
    return _repo


def llm_call(fn, *args):
    try:
        return fn(*args)
    except LLMOutputError as e:
        raise HTTPException(status_code=502, detail=f"AI returned unusable output: {e}")


class Ctx:
    """The signed-in user + storage, bundled so every route gets both the same way."""

    def __init__(self, user_id: str = Depends(current_user), repo: GoalRepo = Depends(get_repo)):
        self.user_id = user_id
        self.repo = repo

    def load(self, goal_id: str) -> GoalRecord:
        rec = self.repo.get(self.user_id, goal_id)
        if rec is None:
            # same answer for "doesn't exist" and "someone else's" — don't leak which
            raise HTTPException(status_code=404, detail="goal not found")
        return rec

    def save(self, rec: GoalRecord) -> None:
        self.repo.save(self.user_id, rec)

    def log(self, rec: GoalRecord, row: dict | None) -> None:
        if row:
            self.repo.log_correction(self.user_id, rec.id, row)


# ---------- views ----------

class GoalView(BaseModel):
    id: str
    goal: str
    interview_done: bool
    question: str | None
    questions_asked: int
    profile: GoalProfile
    summary: list[str]          # plain-language "what I understood", for the confirm screen
    has_blueprint: bool
    has_capacity: bool
    plan_start: date | None
    checked_through: date | None


def to_view(rec: GoalRecord) -> GoalView:
    iv = rec.interview
    return GoalView(
        id=rec.id,
        goal=iv.goal,
        interview_done=iv.done,
        question=iv.current_question,
        questions_asked=iv.questions_asked,
        profile=iv.profile,
        summary=iv.profile.describe(),
        has_blueprint=rec.blueprint is not None,
        has_capacity=rec.capacity is not None,
        plan_start=rec.plan_start,
        checked_through=rec.checked_through,
    )


def require_blueprint(rec: GoalRecord) -> GoalBlueprint:
    if not rec.blueprint:
        raise HTTPException(status_code=409, detail="generate the blueprint first")
    return rec.blueprint


def tomorrow() -> date:
    return date.today() + timedelta(days=1)


# ---------- public ----------

@app.get("/health")
def health() -> dict:
    return {"ok": True, "auth": config.AUTH_MODE, "storage": "postgres" if config.DATABASE_URL else "memory"}


@app.get("/me")
def me(user_id: str = Depends(current_user)) -> dict:
    return {"user_id": user_id}


# ---------- goals + interview ----------

class TextIn(BaseModel):
    text: str


@app.post("/goals/check", response_model=GoalCheck)
def goal_check(body: TextIn, _: str = Depends(current_user), llm: LLMClient = Depends(get_llm)) -> GoalCheck:
    """Call before POST /goals: is this a goal, or the user's background? (frontend decides)"""
    return llm_call(check_goal, llm, body.text)


class NewGoal(BaseModel):
    goal: str
    background: str | None = None   # e.g. what /goals/check returned as background


@app.get("/goals", response_model=list[GoalSummary])
def list_goals(ctx: Ctx = Depends()) -> list[GoalSummary]:
    return ctx.repo.list(ctx.user_id)


@app.post("/goals", response_model=GoalView)
def create_goal(body: NewGoal, ctx: Ctx = Depends(), llm: LLMClient = Depends(get_llm)) -> GoalView:
    state = llm_call(
        lambda: start_interview(llm, body.goal, config.MAX_INTERVIEW_QUESTIONS, background=body.background)
    )
    return to_view(ctx.repo.create(ctx.user_id, state))


@app.get("/goals/{goal_id}", response_model=GoalView)
def read_goal(goal_id: str, ctx: Ctx = Depends()) -> GoalView:
    return to_view(ctx.load(goal_id))


@app.delete("/goals/{goal_id}")
def delete_goal(goal_id: str, ctx: Ctx = Depends()) -> dict:
    if not ctx.repo.delete(ctx.user_id, goal_id):
        raise HTTPException(status_code=404, detail="goal not found")
    return {"deleted": goal_id}


class Answer(BaseModel):
    answer: str


@app.post("/goals/{goal_id}/answer", response_model=GoalView)
def answer(goal_id: str, body: Answer, ctx: Ctx = Depends(), llm: LLMClient = Depends(get_llm)) -> GoalView:
    rec = ctx.load(goal_id)
    if rec.interview.done:
        raise HTTPException(status_code=409, detail="interview already finished")
    if not body.answer.strip():
        raise HTTPException(status_code=422, detail="answer is empty")
    rec.interview = llm_call(
        answer_question, llm, rec.interview, body.answer, config.MAX_INTERVIEW_QUESTIONS
    )
    ctx.save(rec)
    return to_view(rec)


@app.post("/goals/{goal_id}/correct", response_model=GoalView)
def correct_profile(goal_id: str, body: TextIn, ctx: Ctx = Depends(),
                    llm: LLMClient = Depends(get_llm)) -> GoalView:
    """User fixes 'what I understood'. Any existing blueprint is dropped — it was built on the old profile."""
    rec = ctx.load(goal_id)
    if not rec.interview.done:
        raise HTTPException(status_code=409, detail="finish the interview first")
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="correction is empty")
    rec.interview = llm_call(apply_correction, llm, rec.interview, body.text)
    rec.blueprint = None
    ctx.save(rec)
    return to_view(rec)


# ---------- blueprint + estimates ----------

@app.post("/goals/{goal_id}/blueprint", response_model=GoalBlueprint)
def make_blueprint(goal_id: str, ctx: Ctx = Depends(), llm: LLMClient = Depends(get_llm)) -> GoalBlueprint:
    rec = ctx.load(goal_id)
    if not rec.interview.done:
        raise HTTPException(status_code=409, detail="finish the interview first")
    rec.blueprint = llm_call(
        generate_blueprint, llm, rec.interview.goal, rec.interview.profile, rec.interview.messages,
        [blueprint_brief(s, date.today()) for s in rec.sources],
    )
    ctx.save(rec)
    return rec.blueprint


@app.get("/goals/{goal_id}/blueprint", response_model=GoalBlueprint)
def read_blueprint(goal_id: str, ctx: Ctx = Depends()) -> GoalBlueprint:
    rec = ctx.load(goal_id)
    if not rec.blueprint:
        raise HTTPException(status_code=404, detail="no blueprint yet")
    return rec.blueprint


class HoursEdit(BaseModel):
    hours: float


@app.patch("/goals/{goal_id}/blueprint/milestones/{key}", response_model=GoalBlueprint)
def edit_milestone_hours(goal_id: str, key: str, body: HoursEdit, ctx: Ctx = Depends()) -> GoalBlueprint:
    """User says 'this will take me X hours'. Split keeps its proportions; the change is logged."""
    rec = ctx.load(goal_id)
    bp = require_blueprint(rec)
    old = next((m for m in bp.milestones if m.key == key), None)
    try:
        rec.blueprint = bp.with_milestone_hours(key, body.hours)
    except ValueError as e:
        raise HTTPException(status_code=404 if old is None else 422, detail=str(e))
    ctx.save(rec)
    ctx.log(rec, make_row(rec.title, key, old.name, old.estimated_hours, body.hours,
                          old.confidence, bool(rec.interview.profile.benchmarks), source="api"))
    return rec.blueprint


@app.patch("/goals/{goal_id}/blueprint/deliverables/{deliverable}", response_model=GoalBlueprint)
def edit_deliverable_hours(goal_id: str, deliverable: str, body: HoursEdit, ctx: Ctx = Depends()) -> GoalBlueprint:
    """User says 'Assignment 2 will take me X hours'. Parts matching their track record stay put."""
    rec = ctx.load(goal_id)
    bp = require_blueprint(rec)
    group = bp.deliverables().get(deliverable)
    try:
        parts = bp.anchor_parts(deliverable, rec.interview.profile.benchmark_map())
        rec.blueprint = bp.with_deliverable_hours(deliverable, body.hours, parts)
    except ValueError as e:
        raise HTTPException(status_code=404 if group is None else 422, detail=str(e))
    ctx.save(rec)
    ctx.log(rec, make_row(
        rec.title, deliverable, ", ".join(m.name for m in group),
        sum(m.estimated_hours for m in group), body.hours,
        min(m.confidence for m in group), bool(rec.interview.profile.benchmarks), source="api",
    ))
    return rec.blueprint


class EstimateCheck(BaseModel):
    warnings: list[EstimateWarning]
    messages: list[str]


@app.get("/goals/{goal_id}/estimate-check", response_model=EstimateCheck)
def estimate_check(goal_id: str, ctx: Ctx = Depends()) -> EstimateCheck:
    rec = ctx.load(goal_id)
    bp = require_blueprint(rec)
    warnings = check_estimates(bp, rec.interview.profile.benchmark_map())
    messages = [w.message for w in warnings]
    covered = coverage_warning(sum(m.estimated_hours for m in bp.milestones), rec.sources, date.today())
    if covered:
        messages.append(covered)
    return EstimateCheck(warnings=warnings, messages=messages)


# ---------- feasibility + schedule (capacity passed in: try "what if" without saving) ----------

class GoalFeasibilityRequest(BaseModel):
    capacity: CapacityProfile
    start: date | None = None      # default: tomorrow (today is usually half gone)
    deadline: date | None = None   # default: deadline from the interview
    personal_multiplier: float = 1.0


@app.post("/goals/{goal_id}/feasibility", response_model=FeasibilityResult)
def goal_feasibility(goal_id: str, body: GoalFeasibilityRequest, ctx: Ctx = Depends()) -> FeasibilityResult:
    rec = ctx.load(goal_id)
    profile = rec.interview.profile
    try:
        return check_feasibility(
            require_blueprint(rec), body.capacity, body.start or tomorrow(),
            body.deadline or profile.deadline, body.personal_multiplier,
            profile.key_date_map(), profile.soft_key_dates(), profile.deadline_hard,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/goals/{goal_id}/schedule", response_model=Schedule)
def goal_schedule(goal_id: str, body: GoalFeasibilityRequest, ctx: Ctx = Depends()) -> Schedule:
    """Weekly sprints with timed sessions. Same simulation as /feasibility, so they always agree."""
    rec = ctx.load(goal_id)
    bp = require_blueprint(rec)
    profile = rec.interview.profile
    try:
        items = resolve_items(
            bp, profile.key_date_map(), body.deadline or profile.deadline,
            body.personal_multiplier, profile.soft_key_dates(), profile.deadline_hard,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return build_schedule(bp, items, body.capacity, body.start or tomorrow())


# ---------- the loop: capacity -> check-ins -> replan ----------

@app.put("/goals/{goal_id}/capacity", response_model=CapacityProfile)
def set_capacity(goal_id: str, body: CapacityProfile, start: date | None = None,
                 ctx: Ctx = Depends()) -> CapacityProfile:
    """Save weekly free time. The first time, this also fixes the plan's start (default tomorrow)."""
    rec = ctx.load(goal_id)
    rec.capacity = body
    if rec.plan_start is None or start is not None:
        rec.plan_start = start or tomorrow()
    ctx.save(rec)
    return body


@app.get("/goals/{goal_id}/capacity", response_model=CapacityProfile)
def read_capacity(goal_id: str, ctx: Ctx = Depends()) -> CapacityProfile:
    rec = ctx.load(goal_id)
    if not rec.capacity:
        raise HTTPException(status_code=404, detail="no capacity yet")
    return rec.capacity


def _replan(rec: GoalRecord, checkins: list[CheckIn], today: date) -> ReplanResult:
    bp = require_blueprint(rec)
    if not rec.capacity:
        raise HTTPException(status_code=409, detail="set capacity first: PUT /goals/{id}/capacity")
    p = rec.interview.profile
    try:
        items = resolve_items(bp, p.key_date_map(), p.deadline, 1.0, p.soft_key_dates(), p.deadline_hard)
        return replan(bp, items, checkins, rec.capacity, today)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


def _items(rec: GoalRecord):
    p = rec.interview.profile
    return resolve_items(require_blueprint(rec), p.key_date_map(), p.deadline, 1.0,
                         p.soft_key_dates(), p.deadline_hard)


class DueView(BaseModel):
    since: date
    until: date
    sessions: list[DueSession]
    message: str | None = None   # e.g. "your plan starts Tue 29 Sep"


@app.get("/goals/{goal_id}/due", response_model=DueView)
def get_due(goal_id: str, today: date | None = None, ctx: Ctx = Depends()) -> DueView:
    """The planned sessions since the last check-in, up to today — what the check-in screen asks about."""
    rec = ctx.load(goal_id)
    if not rec.capacity or not rec.plan_start:
        raise HTTPException(status_code=409, detail="set capacity first: PUT /goals/{id}/capacity")
    today = today or date.today()
    since = (rec.checked_through + timedelta(days=1)) if rec.checked_through else rec.plan_start
    if since > today:
        msg = (f"Your plan starts {rec.plan_start:%a %d %b}." if rec.checked_through is None
               else f"Already checked in up to {rec.checked_through:%a %d %b}.")
        return DueView(since=since, until=today, sessions=[], message=msg)
    try:
        sessions = due_sessions(require_blueprint(rec), _items(rec), rec.checkins, rec.capacity, since, today)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return DueView(since=since, until=today, sessions=sessions,
                   message=None if sessions else "Nothing was planned in that stretch.")


class CheckInBatch(BaseModel):
    checkins: list[CheckIn]
    today: date | None = None   # default: today. The replan starts the day after.


@app.post("/goals/{goal_id}/checkins", response_model=ReplanResult)
def add_checkins(goal_id: str, body: CheckInBatch, ctx: Ctx = Depends()) -> ReplanResult:
    """Log what actually happened; get back progress, alerts and the updated plan from tomorrow."""
    rec = ctx.load(goal_id)
    today = body.today or date.today()
    # Replan FIRST with the new check-ins; only store them if that works (bad batch = nothing saved).
    result = _replan(rec, rec.checkins + body.checkins, today + timedelta(days=1))
    if body.checkins:
        ctx.repo.add_checkins(ctx.user_id, rec.id, body.checkins)
    if rec.checked_through is None or today > rec.checked_through:
        rec.checked_through = today
        ctx.save(rec)
    return result


@app.get("/goals/{goal_id}/replan", response_model=ReplanResult)
def get_replan(goal_id: str, today: date | None = None, ctx: Ctx = Depends()) -> ReplanResult:
    """The plan from the first day that still needs doing: not before the plan starts,
    and not a day you've already checked in for (that work is already counted)."""
    rec = ctx.load(goal_id)
    start = today or date.today()
    if rec.plan_start and rec.plan_start > start:
        start = rec.plan_start
    if rec.checked_through and rec.checked_through >= start:
        start = rec.checked_through + timedelta(days=1)
    return _replan(rec, rec.checkins, start)


# ---------- courses: synced from the real course pages by backend/sync ----------

class SourcePage(BaseModel):
    url: str = Field(min_length=4, max_length=2000)
    platform: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=1_000_000)
    title: str | None = None
    youtube_ids: list[str] = Field(default_factory=list, max_length=1000)   # embedded lectures


class SourceLink(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class SourceItems(BaseModel):
    """Already structured (e.g. a YouTube playlist read with exact durations): no AI needed."""
    url: str = Field(min_length=4, max_length=2000)
    platform: str = Field(min_length=1, max_length=40)
    title: str
    items: list[SourceItem] = Field(max_length=5000)


class SyncResult(BaseModel):
    load: SourceLoad
    items: list[SourceItem]
    key_dates_added: list[str]
    key_dates_moved: list[str]
    messages: list[str]


class SourceView(BaseModel):
    load: SourceLoad
    items: list[SourceItem]
    synced_at: datetime


def _store_source(ctx: "Ctx", rec: GoalRecord, src: CourseSource) -> SyncResult:
    today = date.today()
    rec.sources = [s for s in rec.sources if s.url != src.url] + [src]
    added, moved = [], []
    kds = {k.key: k for k in rec.interview.profile.key_dates}
    for kd in course_key_dates(src, today):
        if kd.key not in kds:
            rec.interview.profile.key_dates.append(kd)
            added.append(f"{kd.label} — {kd.date:%a %d %b}")
        elif kds[kd.key].date != kd.date:
            kds[kd.key].date = kd.date
            moved.append(f"{kd.label} — now {kd.date:%a %d %b}")
    ctx.save(rec)
    load = source_load(src, today)
    messages = load.describe()
    if not src.items:
        messages.append("No study items found on that page. Is it the course contents page, "
                        "and were you logged in?")
    if (added or moved) and rec.blueprint:
        messages.append("Course deadlines changed: regenerate the plan so it uses them.")
    return SyncResult(load=load, items=src.items, key_dates_added=added, key_dates_moved=moved,
                      messages=messages)


@app.post("/goals/{goal_id}/sources/page", response_model=SyncResult)
def sync_source_page(goal_id: str, body: SourcePage, ctx: Ctx = Depends(),
                     llm: LLMClient = Depends(get_llm)) -> SyncResult:
    """Text of a course page (read by the sync tool on the user's laptop) -> items -> measured load."""
    rec = ctx.load(goal_id)
    text = body.text + _embedded_video_lengths(body.youtube_ids)
    src = llm_call(lambda: extract_source(llm, url=body.url, platform=body.platform,
                                          text=text, title=body.title, today=date.today()))
    return _store_source(ctx, rec, src)


def _embedded_video_lengths(ids: list[str]) -> str:
    """Exact lengths of YouTube lectures embedded in the page, for the reader to match up."""
    if not ids or not config.YOUTUBE_API_KEY:
        return ""
    try:
        vids = web_read.youtube_videos(ids)
    except web_read.NotAvailable:
        return ""
    lines = [f"- {v['title']} | {v['duration']}" for v in vids.values()]
    return "\n\n=== Embedded videos (exact lengths) ===\n" + "\n".join(lines) if lines else ""


@app.post("/goals/{goal_id}/sources/link", response_model=SyncResult)
def sync_source_link(goal_id: str, body: SourceLink, ctx: Ctx = Depends(),
                     llm: LLMClient = Depends(get_llm)) -> SyncResult:
    """Pasted in the app: a YouTube playlist (exact lengths) or a PUBLIC course page.
    Anything behind a login goes through the Chrome extension instead."""
    rec = ctx.load(goal_id)
    url = body.url.strip()
    try:
        if web_read.playlist_id(url) and ("youtube.com" in url or "youtu.be" in url):
            title, vids = web_read.youtube_playlist(url)
            items = [SourceItem(title=v["title"], kind="video", duration_text=v["duration"],
                                minutes=parse_duration(v["duration"]),
                                url=f"https://www.youtube.com/watch?v={v['id']}") for v in vids]
            return _store_source(ctx, rec, CourseSource(url=url, platform="youtube", title=title, items=items))
        title, text = web_read.public_page(url)
    except web_read.NotAvailable as e:
        raise HTTPException(status_code=422, detail=str(e))
    host = (urlparse(url).hostname or "web").removeprefix("www.").split(".")[0]
    src = llm_call(lambda: extract_source(llm, url=url, platform=host, text=text, title=title or None,
                                          today=date.today()))
    return _store_source(ctx, rec, src)


@app.post("/goals/{goal_id}/sources", response_model=SyncResult)
def sync_source_items(goal_id: str, body: SourceItems, ctx: Ctx = Depends()) -> SyncResult:
    rec = ctx.load(goal_id)
    return _store_source(ctx, rec, CourseSource(url=body.url, platform=body.platform,
                                                title=body.title, items=body.items))


@app.get("/goals/{goal_id}/sources", response_model=list[SourceView])
def list_sources(goal_id: str, ctx: Ctx = Depends()) -> list[SourceView]:
    rec = ctx.load(goal_id)
    return [SourceView(load=source_load(s, date.today()), items=s.items, synced_at=s.synced_at)
            for s in rec.sources]


@app.delete("/goals/{goal_id}/sources")
def delete_source(goal_id: str, url: str, ctx: Ctx = Depends()) -> dict:
    """Removes the course. Deadlines it added stay (they may be in the plan); edit them if needed."""
    rec = ctx.load(goal_id)
    kept = [s for s in rec.sources if s.url != url]
    if len(kept) == len(rec.sources):
        raise HTTPException(status_code=404, detail="no course with that url")
    rec.sources = kept
    ctx.save(rec)
    return {"deleted": url}


# ---------- stateless, no login: experiment with a hand-written blueprint ----------

class FeasibilityRequest(BaseModel):
    blueprint: GoalBlueprint
    capacity: CapacityProfile
    start: date
    deadline: date | None = None
    personal_multiplier: float = 1.0
    key_dates: dict[str, date] = {}
    soft_keys: list[str] = []       # key dates that may slip; the rest are hard


@app.post("/feasibility", response_model=FeasibilityResult)
def feasibility(req: FeasibilityRequest) -> FeasibilityResult:
    try:
        return check_feasibility(
            req.blueprint, req.capacity, req.start, req.deadline, req.personal_multiplier,
            req.key_dates, set(req.soft_keys),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
