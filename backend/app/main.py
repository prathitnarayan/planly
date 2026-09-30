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

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.ai.goal_intake import (
    answer_question, apply_correction, check_goal, generate_blueprint, reality_check, start_interview,
)
from app.planners.reality import RealityVerdict, assess
from app.ai.source_extract import extract_source
from app.ai.llm import LLMClient, LLMOutputError, OpenAIClient
from app.core import config
from app.core import gcal, telegram, web_read
from app.core.auth import current_user
from app.core.estimate_log import make_row
from app.core.repo import (
    DatabaseUnavailable, GoalRecord, GoalRepo, GoalSummary, InMemoryRepo, PostgresRepo, UserSettings,
)
from app.planners import habits as habit_logic
from app.planners.habits import Habit, HabitStats, Urge
from app.planners.learning import Learned, learn
from app.planners import notify as notify_logic
from app.planners.pool import busy_taken, shared_pool_taken
from app.planners.capacity import CapacityProfile
from app.planners.estimate_check import EstimateWarning, check_estimates
from app.planners.load import (
    SourceLoad, blueprint_brief, course_key_dates, coverage_warning, source_load,
)
from app.planners.feasibility import FeasibilityResult, check_feasibility, resolve_items
from app.planners.integrity import IntegrityEvent, WatchEvidence, auto_done, coverage, now
from app.planners.progress import (
    CheckIn, DayContext, DueSession, ReplanResult, TodaySession, close_days, day_plan, due_sessions, replan,
)
from app.planners.tasks import video_key
from app.planners.scheduler import Schedule, build_schedule
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalCheck, GoalProfile, RealityCheck
from app.schemas.integrations import GoogleLink, NotifyPrefs, TelegramLink
from app.schemas.source import CourseSource, SourceItem, parse_duration

app = FastAPI(title="Planly API", version="0.12.0")

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
    @app.exception_handler(pg_errors.ForeignKeyViolation)
    async def account_gone(_: Request, exc: Exception) -> JSONResponse:
        """Real incident (29 Sep): a user was deleted in Supabase Auth while their browser still held
        a valid token (tokens live up to an hour). Saving anything then broke the user_id link -> 500.
        Tell the app to sign out instead."""
        if "user_id" in str(exc):
            return JSONResponse(status_code=401, content={
                "detail": "This login belongs to an account that no longer exists. Sign in again.",
                "code": "account_gone",
            })
        return JSONResponse(status_code=409, content={"detail": "That refers to something that doesn't exist."})
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

    # ---- one pool of free time for all goals + what Planly learned (006) ----
    _settings: UserSettings | None = None
    _caps: dict | None = None

    @property
    def settings(self) -> UserSettings:
        if self._settings is None:
            self._settings = self.repo.get_settings(self.user_id)
        return self._settings

    def save_settings(self) -> None:
        self.repo.save_settings(self.user_id, self.settings)
        self._caps = None

    def goal_order(self) -> list[str]:
        """Priority order: the user's saved order, then any other goals (newest first)."""
        ids = [g.id for g in self.repo.list(self.user_id)]
        saved = [g for g in self.settings.goal_order if g in ids]
        return saved + [g for g in ids if g not in saved]

    def base_capacity(self, rec: GoalRecord) -> CapacityProfile | None:
        return self.settings.capacity or rec.capacity

    def cap(self, rec: GoalRecord, start: date) -> CapacityProfile | None:
        """This goal's free time: the shared pool, adjusted by what Planly learned about each
        weekday, minus the times already planned for higher-priority goals."""
        base = self.base_capacity(rec)
        if base is None:
            return None
        self._caps = self._caps or {}
        key = (rec.id, start)
        if key not in self._caps:
            learned = base.model_copy(update={"weekday_ratio": self.settings.learned.weekday_ratio(base.sustainable_ratio)})
            taken = shared_pool_taken(self._higher_goals(rec), learned, start, _items,
                                      initial=self.calendar_taken(learned, start))
            self._caps[key] = learned.model_copy(update={"taken": taken})
        return self._caps[key]

    def calendar_taken(self, cap: CapacityProfile, start: date):
        """Meetings from Google Calendar (read-only free/busy), cut out of your free slots."""
        g = self.settings.google
        if not (g.connected and gcal.available()):
            return {}
        now = datetime.now(timezone.utc)
        want_from = datetime.combine(start, datetime.min.time(), timezone.utc) - timedelta(days=1)
        stale = (not g.fetched_at or now - g.fetched_at > gcal.CACHE or not g.busy_from
                 or g.busy_from > want_from or g.busy_until < want_from + timedelta(days=60))
        if stale:
            try:
                g.busy = gcal.freebusy(gcal.decrypt(g.refresh_token_enc), want_from, want_from + gcal.HORIZON)
                g.busy_from, g.busy_until, g.error = want_from, want_from + gcal.HORIZON, None
            except Exception as e:                      # keep the last known busy times
                g.error = str(e)[:200]
            g.fetched_at = now
            self.repo.save_settings(self.user_id, self.settings)
        return busy_taken(g.busy, cap, self.settings.timezone, start, start + gcal.HORIZON)

    def _higher_goals(self, rec: GoalRecord) -> list[GoalRecord]:
        out = []
        for gid in self.goal_order():
            if gid == rec.id:
                break
            h = self.repo.get(self.user_id, gid)
            if h and h.blueprint and h.plan_start:
                out.append(h)
        return out


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
    return {"ok": True, "version": app.version, "auth": config.AUTH_MODE,
            "storage": "postgres" if config.DATABASE_URL else "memory"}


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
    rec.interview.reality = None      # the goal/background may have changed: ask again
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
    ctx.settings.capacity = body            # one pool of free time, shared by every goal
    ctx.save_settings()
    return body


@app.get("/goals/{goal_id}/capacity", response_model=CapacityProfile)
def read_capacity(goal_id: str, ctx: Ctx = Depends()) -> CapacityProfile:
    rec = ctx.load(goal_id)
    cap = ctx.base_capacity(rec)
    if not cap:
        raise HTTPException(status_code=404, detail="no capacity yet")
    return cap


def _replan(ctx: "Ctx", rec: GoalRecord, checkins: list[CheckIn], today: date) -> ReplanResult:
    bp = require_blueprint(rec)
    if not rec.capacity:
        raise HTTPException(status_code=409, detail="set capacity first: PUT /goals/{id}/capacity")
    p = rec.interview.profile
    try:
        items = resolve_items(bp, p.key_date_map(), p.deadline, 1.0, p.soft_key_dates(), p.deadline_hard)
        return replan(bp, items, checkins, ctx.cap(rec, today), today, penalties=rec.integrity.penalty_minutes)
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
        sessions = due_sessions(require_blueprint(rec), _items(rec), rec.checkins, ctx.cap(rec, since), since, today)
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
    result = _replan(ctx, rec, rec.checkins + body.checkins, today + timedelta(days=1))
    if body.checkins:
        ctx.repo.add_checkins(ctx.user_id, rec.id, body.checkins)
    if rec.checked_through is None or today > rec.checked_through:
        rec.checked_through = today
        ctx.save(rec)
    return result


# ---------- today: checkboxes; unticked work rolls over at the end of the day ----------

def server_today() -> date:
    return date.today()


def _plausible_today(today: date | None) -> bool:
    """The browser sends the user's local date (the server runs in UTC). Only trust it within
    a day of the server's date, so a wrong clock can't close future days."""
    return today is None or abs((today - server_today()).days) <= 1


def _user_today(today: date | None) -> date:
    if not _plausible_today(today):
        raise HTTPException(status_code=422, detail="today is too far from the server's date")
    return today or server_today()


def _day_ctx(ctx: "Ctx", rec: GoalRecord) -> DayContext:
    """Courses + standing + the watch evidence for this goal's videos."""
    keys = [k for src in rec.sources for it in src.items if (k := video_key(it.url, it.kind))]
    evidence = ctx.repo.get_watch(ctx.user_id, keys) if keys else {}
    return DayContext(sources=rec.sources, integrity=rec.integrity, evidence=evidence,
                      video_factor=ctx.settings.learned.pace())


def _close_past_days(ctx: "Ctx", rec: GoalRecord, today: date) -> None:
    """Every day before today that isn't closed yet: ticks are checked against the evidence and
    turned into check-ins (unticked = missed, disproved = no credit + time owed). The replan then
    spreads what's left over the coming days at normal daily capacity."""
    if not (rec.blueprint and rec.capacity and rec.plan_start):
        return
    first = rec.checked_through + timedelta(days=1) if rec.checked_through else rec.plan_start
    last = today - timedelta(days=1)
    if first > last:
        return
    try:
        new, notes, integrity, outcomes = close_days(
            rec.blueprint, _items(rec), rec.checkins, ctx.cap(rec, first),
            rec.ticks.days, first, last, _day_ctx(ctx, rec), rec.ticks.at)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if new:
        ctx.repo.add_checkins(ctx.user_id, rec.id, new)
        rec.checkins += new
    rec.integrity = integrity
    rec.checked_through = last
    _learn_from(ctx, rec, outcomes, today)
    keep = lambda sid: sid[:10] > last.isoformat()   # ids start with the ISO day
    rec.ticks.days = {d: ids for d, ids in rec.ticks.days.items() if d > last.isoformat()}
    rec.ticks.at = {k: v for k, v in rec.ticks.at.items() if keep(k)}
    rec.ticks.auto = [k for k in rec.ticks.auto if keep(k)]
    rec.ticks.notes, rec.ticks.notes_day = notes, today
    ctx.save(rec)


def _learn_from(ctx: "Ctx", rec: GoalRecord, outcomes, today: date) -> None:
    """Store what happened, then re-learn this user's pace and reliable days (learning.py)."""
    if not outcomes:
        return
    ctx.repo.add_outcomes(ctx.user_id, rec.id, outcomes)
    history = ctx.repo.list_outcomes(ctx.user_id, today - timedelta(days=90))
    ctx.settings.learned = learn(history, today)
    ctx.save_settings()


class Standing(BaseModel):
    trust: int
    streak: int
    evidence_only: bool
    lock_reason: str | None
    owed_minutes: int                     # extra time added by penalties, still in the plan
    last_day: list[IntegrityEvent]        # verdicts of the most recently closed day


class TodayView(BaseModel):
    day: date
    sessions: list[TodaySession]
    planned_minutes: int
    done_minutes: int
    moved: list[str]              # unfinished / disproved work from earlier days
    closed: bool = False          # today was already checked in on the check-in page
    message: str | None = None
    next_day: date | None = None                               # when today has nothing: the next day that does
    next_sessions: list[TodaySession] = Field(default_factory=list)   # preview only, not tickable yet
    can_start_today: bool = False                              # plan starts later and nothing is logged yet
    watched: dict[str, float] = Field(default_factory=dict)    # video key -> share of the planned part played
    standing: Standing | None = None


def _standing(rec: GoalRecord, today: date) -> Standing:
    it = rec.integrity
    last = max((e.day for e in it.events), default=None)
    return Standing(trust=it.trust, streak=it.streak, evidence_only=it.evidence_only(today),
                    lock_reason=it.why_locked(today), owed_minutes=sum(it.penalty_minutes.values()),
                    last_day=[e for e in it.events if e.day == last] if last else [])


def _next_planned(ctx: "Ctx", rec: GoalRecord, after: date, dctx: DayContext) -> tuple[date | None, list[TodaySession]]:
    """First day after `after` with sessions, as the plan stands now (preview)."""
    start = max(after + timedelta(days=1), rec.plan_start or after)
    plan = replan(rec.blueprint, _items(rec), rec.checkins, ctx.cap(rec, start), today=start,
                  penalties=rec.integrity.penalty_minutes)
    for sprint in plan.schedule.sprints:
        for d in sprint.days:
            if d.sessions:
                sessions, _ = day_plan(rec.blueprint, _items(rec), rec.checkins, ctx.cap(rec, start), d.day, dctx)
                return d.day, sessions
    return None, []


def _today_view(ctx: "Ctx", rec: GoalRecord, today: date, save_auto: bool = True) -> TodayView:
    dctx = _day_ctx(ctx, rec)
    view = _today_view_inner(ctx, rec, today, dctx, save_auto)
    if not view.sessions and not view.closed:
        try:
            view.next_day, view.next_sessions = _next_planned(ctx, rec, today, dctx)
        except ValueError:
            pass
    view.standing = _standing(rec, today)
    return view


def _today_view_inner(ctx: "Ctx", rec: GoalRecord, today: date, dctx: DayContext, save_auto: bool) -> TodayView:
    moved = rec.ticks.notes if rec.ticks.notes_day == today else []
    if rec.plan_start and today < rec.plan_start:
        return TodayView(day=today, sessions=[], planned_minutes=0, done_minutes=0, moved=moved,
                         message=f"Your plan starts {rec.plan_start:%a %d %b}.",
                         can_start_today=not rec.checkins)
    if rec.checked_through and rec.checked_through >= today:
        return TodayView(day=today, sessions=[], planned_minutes=0, done_minutes=0, moved=moved, closed=True,
                         message="Today is already checked in.")
    try:
        sessions, _ = day_plan(rec.blueprint, _items(rec), rec.checkins, ctx.cap(rec, today), today, dctx)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    key = today.isoformat()
    ticked = set(rec.ticks.days.get(key, []))
    sites = dctx.sites()
    evidence_only = rec.integrity.evidence_only(today)
    changed = False
    for s in sessions:
        # the evidence ticks a session by itself once every item is proven (e.g. >= 80% watched)
        if s.id not in ticked and auto_done(s.items, dctx.evidence, sites, s.minutes):
            ticked.add(s.id)
            rec.ticks.auto.append(s.id)
            rec.ticks.at[s.id] = now()
            changed = True
        s.done = s.id in ticked
        s.auto = s.id in rec.ticks.auto
        s.locked = evidence_only and s.checkable and not s.done
    if changed and save_auto:
        rec.ticks.days[key] = sorted(ticked)
        ctx.save(rec)
    watched = {}
    for s in sessions:
        for it in s.items:
            if it.video_key and it.video_key in dctx.evidence:
                watched[it.video_key] = round(coverage(dctx.evidence[it.video_key], it.part_from, it.part_to), 2)
    return TodayView(day=today, sessions=sessions, planned_minutes=sum(s.minutes for s in sessions),
                     done_minutes=sum(s.minutes for s in sessions if s.done), moved=moved, watched=watched,
                     message=None if sessions else "Nothing planned today.")


@app.get("/goals/{goal_id}/today", response_model=TodayView)
def get_today(goal_id: str, today: date | None = None, ctx: Ctx = Depends()) -> TodayView:
    rec = ctx.load(goal_id)
    if not rec.capacity or not rec.plan_start:
        raise HTTPException(status_code=409, detail="set capacity first: PUT /goals/{id}/capacity")
    require_blueprint(rec)
    today = _user_today(today)
    _close_past_days(ctx, rec, today)
    return _today_view(ctx, rec, today)


class StartToday(BaseModel):
    today: date


@app.post("/goals/{goal_id}/start-today", response_model=TodayView)
def start_today(goal_id: str, body: StartToday, ctx: Ctx = Depends()) -> TodayView:
    """'Start today instead': move the plan's first day to today (only before anything is logged)."""
    rec = ctx.load(goal_id)
    today = _user_today(body.today)
    require_blueprint(rec)
    if not rec.capacity:
        raise HTTPException(status_code=409, detail="set capacity first: PUT /goals/{id}/capacity")
    if rec.checkins or (rec.plan_start and rec.plan_start <= today):
        raise HTTPException(status_code=409, detail="the plan has already started")
    rec.plan_start = today
    ctx.save(rec)
    return _today_view(ctx, rec, today)


class Tick(BaseModel):
    day: date
    session_id: str = Field(min_length=3, max_length=300)
    done: bool


@app.put("/goals/{goal_id}/ticks", response_model=TodayView)
def set_tick(goal_id: str, body: Tick, ctx: Ctx = Depends()) -> TodayView:
    """Tick / untick one of today's sessions. Checked against the evidence when the day closes.
    While in evidence-only mode, checkable sessions can't be ticked by hand."""
    rec = ctx.load(goal_id)
    today = _user_today(body.day)
    _close_past_days(ctx, rec, today)
    if rec.checked_through and body.day <= rec.checked_through:
        raise HTTPException(status_code=409, detail="that day is already closed")
    if not body.session_id.startswith(body.day.isoformat() + "|"):
        raise HTTPException(status_code=422, detail="session doesn't belong to that day")
    _apply_tick(ctx, rec, today, body.session_id, body.done)
    return _today_view(ctx, rec, today, save_auto=False)


def _apply_tick(ctx: "Ctx", rec: GoalRecord, today: date, session_id: str, done: bool) -> None:
    """Shared by the web app, the extension bar and Telegram buttons."""
    view = _today_view(ctx, rec, today)
    session = next((s for s in view.sessions if s.id == session_id), None)
    if session is None:
        raise HTTPException(status_code=404, detail="no such session today (the plan changed? reload)")
    if done and session.locked:
        raise HTTPException(status_code=423, detail=(rec.integrity.why_locked(today) or "Evidence only.")
                            + " Watch / solve it and it ticks itself.")
    ids = set(rec.ticks.days.get(today.isoformat(), []))
    if done:
        ids.add(session_id)
        rec.ticks.at[session_id] = now()
    else:
        ids.discard(session_id)
        rec.ticks.at.pop(session_id, None)
        rec.ticks.auto = [k for k in rec.ticks.auto if k != session_id]
    rec.ticks.days[today.isoformat()] = sorted(ids)
    ctx.save(rec)


# ---------- reality check: usual prep time for well-known goals vs your runway ----------

class RealityView(BaseModel):
    check: RealityCheck | None
    verdict: RealityVerdict


@app.get("/goals/{goal_id}/reality", response_model=RealityView)
def goal_reality(goal_id: str, today: date | None = None, ctx: Ctx = Depends(),
                 llm: LLMClient = Depends(get_llm)) -> RealityView:
    """Asked once per goal (after the interview), then compared in code with the deadline and
    your free time. Warns about UPSC-in-a-month; never blocks."""
    rec = ctx.load(goal_id)
    if not rec.interview.done:
        raise HTTPException(status_code=409, detail="finish the interview first")
    if rec.interview.reality is None:
        rec.interview.reality = llm_call(reality_check, llm, rec.interview.goal, rec.interview.profile)
        ctx.save(rec)
    cap = ctx.base_capacity(rec)
    weekly = cap.weekly_summary()["sustainable_hours"] if cap else None
    verdict = assess(rec.interview.reality, today if _plausible_today(today) and today else server_today(),
                     rec.interview.profile.deadline, weekly)
    return RealityView(check=rec.interview.reality, verdict=verdict)


# ---------- you: shared free time, goal priority, what Planly learned ----------

class LearnedView(BaseModel):
    learned: Learned
    notes: list[str]
    goal_order: list[str]
    has_shared_capacity: bool


@app.get("/me/learned", response_model=LearnedView)
def me_learned(ctx: Ctx = Depends()) -> LearnedView:
    st = ctx.settings
    return LearnedView(learned=st.learned, notes=st.learned.notes(), goal_order=ctx.goal_order(),
                       has_shared_capacity=st.capacity is not None)


class GoalOrder(BaseModel):
    goal_ids: list[str] = Field(max_length=200)


@app.put("/me/goal-order")
def set_goal_order(body: GoalOrder, ctx: Ctx = Depends()) -> dict:
    """Priority: the first goal gets first pick of your free time, the next gets what's left..."""
    mine = {g.id for g in ctx.repo.list(ctx.user_id)}
    ctx.settings.goal_order = [g for g in body.goal_ids if g in mine]
    ctx.save_settings()
    return {"goal_order": ctx.goal_order()}



# ---------- settings: timezone, notifications, Telegram, Google Calendar ----------

class TelegramView(BaseModel):
    available: bool             # the backend has a bot configured
    linked: bool
    username: str | None
    bot: str | None


class GoogleView(BaseModel):
    available: bool
    connected: bool
    fetched_at: datetime | None
    error: str | None
    busy_hours_next_7d: float | None


class SettingsView(BaseModel):
    timezone: str
    notify: NotifyPrefs
    telegram: TelegramView
    google: GoogleView


def _settings_view(ctx: "Ctx") -> SettingsView:
    st = ctx.settings
    g = st.google
    busy7 = None
    if g.connected and g.busy:
        now = datetime.now(timezone.utc)
        busy7 = round(sum(max(0, (min(b, now + timedelta(days=7)) - max(a, now)).total_seconds())
                          for a, b in g.busy) / 3600, 1)
    return SettingsView(
        timezone=st.timezone, notify=st.notify,
        telegram=TelegramView(available=telegram.available(), linked=bool(st.telegram.chat_id),
                              username=st.telegram.username, bot=config.TELEGRAM_BOT_USERNAME or None),
        google=GoogleView(available=gcal.available(), connected=g.connected, fetched_at=g.fetched_at,
                          error=g.error, busy_hours_next_7d=busy7),
    )


@app.get("/me/settings", response_model=SettingsView)
def get_settings(ctx: Ctx = Depends()) -> SettingsView:
    return _settings_view(ctx)


class SettingsIn(BaseModel):
    timezone: str | None = None
    notify: NotifyPrefs | None = None


@app.put("/me/settings", response_model=SettingsView)
def put_settings(body: SettingsIn, ctx: Ctx = Depends()) -> SettingsView:
    from zoneinfo import ZoneInfo
    if body.timezone:
        try:
            ZoneInfo(body.timezone)
        except Exception:
            raise HTTPException(status_code=422, detail="unknown timezone")
        ctx.settings.timezone = body.timezone
    if body.notify:
        keep = ctx.settings.notify          # don't let the client reset "already sent today"
        ctx.settings.notify = body.notify.model_copy(update={"last_morning": keep.last_morning,
                                                             "last_evening": keep.last_evening,
                                                             "last_nudge": keep.last_nudge})
    ctx.save_settings()
    return _settings_view(ctx)


# ---- Telegram ----

@app.post("/me/telegram/link")
def telegram_link(ctx: Ctx = Depends()) -> dict:
    """One-time link: open it, tap Start in Telegram, done. Valid for 15 minutes."""
    if not telegram.available():
        raise HTTPException(status_code=409, detail="Telegram isn't set up on this Planly server yet.")
    code = notify_logic.new_token().replace("-", "x").replace("_", "y")
    ctx.settings.telegram.link_code = code
    ctx.settings.telegram.link_expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    ctx.save_settings()
    try:
        telegram.ensure_webhook()
    except telegram.TelegramError:
        pass
    return {"url": f"https://t.me/{config.TELEGRAM_BOT_USERNAME}?start={code}"}


@app.delete("/me/telegram")
def telegram_unlink(ctx: Ctx = Depends()) -> dict:
    ctx.settings.telegram = TelegramLink()
    ctx.save_settings()
    return {"linked": False}


def _local_today(ctx: "Ctx") -> date:
    return notify_logic.local_now(datetime.now(timezone.utc), ctx.settings.timezone).date()


def _goals_today(ctx: "Ctx", day: date) -> list[tuple[str, str, "TodayView"]]:
    out = []
    for gid in ctx.goal_order():
        rec = ctx.repo.get(ctx.user_id, gid)
        if not (rec and rec.blueprint and rec.capacity and rec.plan_start):
            continue
        try:
            _close_past_days(ctx, rec, day)
            out.append((rec.id, rec.title, _today_view(ctx, rec, day)))
        except HTTPException:
            continue
    return out


# ---- habits (008): streaks, the user's own risky times, daily quote ----

class HabitView(BaseModel):
    habit: Habit
    stats: HabitStats
    lines: list[str]                  # personal lines, from their own numbers
    risk_time: str | None             # "22:00–00:00" once their urge log shows a pattern
    risk_day: str | None              # "Friday (3 of your last 4 slips)"
    urges_7d: int


def _habit_views(ctx: "Ctx", today: date, archived: bool = True) -> list[HabitView]:
    hs = [h for h in ctx.repo.list_habits(ctx.user_id) if archived or not h.archived]
    if not hs:
        return []
    days: dict[str, dict[date, bool]] = {}
    for d in ctx.repo.habit_days(ctx.user_id, min(h.started for h in hs)):
        days.setdefault(d.habit_id, {})[d.day] = d.kept
    urges = ctx.repo.list_urges(ctx.user_id, today - timedelta(days=habit_logic.URGE_LOOKBACK_DAYS))
    out = []
    for h in hs:
        mine = days.get(h.id, {})
        st = habit_logic.stats(h, mine, today)
        rday = habit_logic.risk_day(mine, today)
        hu = [u for u in urges if u.habit_id == h.id]
        rt = habit_logic.risk_time(hu, today)
        out.append(HabitView(
            habit=h, stats=st, lines=habit_logic.lines(h, st, today, rday),
            risk_time=rt.label if rt else None,
            risk_day=f"{rday.name} ({rday.slips} of your last {rday.total} slips)" if rday else None,
            urges_7d=sum(1 for u in hu if u.local.date() > today - timedelta(days=7))))
    return sorted(out, key=lambda v: v.habit.archived)


def _briefs(views: list[HabitView]) -> list[notify_logic.HabitBrief]:
    return [notify_logic.HabitBrief(habit=v.habit, stats=v.stats, lines=v.lines)
            for v in views if not v.habit.archived]


def _load_habit(ctx: "Ctx", habit_id: str) -> Habit:
    h = ctx.repo.get_habit(ctx.user_id, habit_id)
    if not h:
        raise HTTPException(status_code=404, detail="habit not found")
    return h


def _one_view(ctx: "Ctx", habit_id: str, today: date) -> HabitView:
    return next(v for v in _habit_views(ctx, today) if v.habit.id == habit_id)


@app.get("/habits", response_model=list[HabitView])
def list_habits(today: date | None = None, ctx: Ctx = Depends()) -> list[HabitView]:
    return _habit_views(ctx, _user_today(today))


class HabitIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: habit_logic.Kind = "quit"
    why: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=30)
    started: date | None = None
    today: date | None = None


@app.post("/habits", response_model=HabitView)
def create_habit(body: HabitIn, ctx: Ctx = Depends()) -> HabitView:
    today = _user_today(body.today)
    if sum(1 for h in ctx.repo.list_habits(ctx.user_id) if not h.archived) >= habit_logic.MAX_HABITS:
        raise HTTPException(status_code=409, detail=f"{habit_logic.MAX_HABITS} habits at once is the limit — archive one first.")
    started = body.started or today
    if started > today or started < today - timedelta(days=365):
        raise HTTPException(status_code=422, detail="start date must be within the last year, not in the future")
    h = Habit(name=body.name, kind=body.kind, why=body.why, label=body.label, started=started)
    ctx.repo.save_habit(ctx.user_id, h)
    return _one_view(ctx, h.id, today)


class HabitPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    why: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=30)
    archived: bool | None = None
    nudge: bool | None = None
    today: date | None = None


@app.patch("/habits/{habit_id}", response_model=HabitView)
def edit_habit(habit_id: str, body: HabitPatch, ctx: Ctx = Depends()) -> HabitView:
    h = _load_habit(ctx, habit_id)
    changes = {k: getattr(body, k) for k in body.model_fields_set if k != "today"}
    if "name" in changes and not changes["name"]:
        raise HTTPException(status_code=422, detail="name can't be empty")
    for k in ("archived", "nudge"):
        if k in changes and changes[k] is None:
            changes.pop(k)
    h = Habit.model_validate({**h.model_dump(), **changes})      # why/label may be cleared with null
    ctx.repo.save_habit(ctx.user_id, h)
    return _one_view(ctx, h.id, _user_today(body.today))


@app.delete("/habits/{habit_id}")
def delete_habit(habit_id: str, ctx: Ctx = Depends()) -> dict:
    if not ctx.repo.delete_habit(ctx.user_id, habit_id):
        raise HTTPException(status_code=404, detail="habit not found")
    return {"deleted": habit_id}


class HabitDayIn(BaseModel):
    day: date
    kept: bool | None                 # null clears the answer
    today: date | None = None


HABIT_BACKFILL_DAYS = 7


def _set_day(ctx: "Ctx", h: Habit, day: date, kept: bool | None, today: date) -> None:
    if day > today:
        raise HTTPException(status_code=422, detail="can't answer for a day that hasn't happened")
    if day < h.started:
        raise HTTPException(status_code=422, detail="that's before this habit started")
    if day < today - timedelta(days=HABIT_BACKFILL_DAYS):
        raise HTTPException(status_code=422, detail=f"only the last {HABIT_BACKFILL_DAYS} days can be changed")
    ctx.repo.set_habit_day(ctx.user_id, h.id, day, kept)


@app.put("/habits/{habit_id}/days", response_model=HabitView)
def set_habit_day(habit_id: str, body: HabitDayIn, ctx: Ctx = Depends()) -> HabitView:
    today = _user_today(body.today)
    h = _load_habit(ctx, habit_id)
    _set_day(ctx, h, body.day, body.kept, today)
    return _one_view(ctx, h.id, today)


class UrgeOut(BaseModel):
    reply: str
    view: HabitView


def _log_urge(ctx: "Ctx", h: Habit) -> str:
    now_utc = datetime.now(timezone.utc)
    local = notify_logic.local_now(now_utc, ctx.settings.timezone)
    ctx.repo.add_urge(ctx.user_id, Urge(habit_id=h.id, at=now_utc, local=local.replace(tzinfo=None)))
    v = _one_view(ctx, h.id, local.date())
    today_n = sum(1 for u in ctx.repo.list_urges(ctx.user_id, local.date()) if u.habit_id == h.id)
    return habit_logic.urge_reply(h, v.stats, today_n)


@app.post("/habits/{habit_id}/urges", response_model=UrgeOut)
def log_urge(habit_id: str, ctx: Ctx = Depends()) -> UrgeOut:
    h = _load_habit(ctx, habit_id)
    reply = _log_urge(ctx, h)
    return UrgeOut(reply=reply, view=_one_view(ctx, h.id, _local_today(ctx)))


# ---- Telegram messages ----

def _render(ctx: "Ctx", kind: str, day: date) -> tuple[str | None, list]:
    """The morning / evening message as it stands right now (also used to refresh after a tap)."""
    quote = habit_logic.quote_for(ctx.user_id, day) if kind == "morning" and ctx.settings.notify.quote else None
    return notify_logic.build_message(kind, day, _goals_today(ctx, day), _briefs(_habit_views(ctx, day)), quote)


def _tokens(ctx: "Ctx", rows: list, kind: str, day: date) -> list[list[tuple[str, str]]]:
    """Swap button data for short tokens (Telegram allows 64 bytes). Buttons stay valid for today and
    yesterday, so tapping the morning message still works after the evening one arrives."""
    keep = {t: d for t, d in ctx.settings.telegram.buttons.items()
            if d.get("made", "") >= (day - timedelta(days=1)).isoformat()}
    out = []
    for row in rows:
        line = []
        for label, data in row:
            tok = notify_logic.new_token()
            keep[tok] = {**data, "msg": kind, "made": day.isoformat()}
            line.append((label, f"t:{tok}"))
        out.append(line)
    ctx.settings.telegram.buttons = keep
    return out


def _send_digest(ctx: "Ctx", kind: str, force: bool = False) -> bool:
    """Morning (quote + list) or evening check to the user's Telegram. Returns True if something was sent."""
    chat = ctx.settings.telegram.chat_id
    if not chat:
        return False
    day = _local_today(ctx)
    text, rows = _render(ctx, kind, day)
    if text is None:
        if not force:
            return False
        text, rows = ("<b>Nothing planned today.</b> Enjoy it." if kind == "morning"
                      else "<b>All done for today.</b> ✓"), []
    telegram.send(chat, text, _tokens(ctx, rows, kind, day))
    ctx.save_settings()
    return True


def _send_nudge(ctx: "Ctx", now_utc: datetime) -> bool:
    """At most one heads-up a day: 30 min before the user's OWN usual urge time, only for habits
    not answered yet today. No pattern in their log = no nudge."""
    prefs = ctx.settings.notify
    if not (prefs.habit_nudge and ctx.settings.telegram.chat_id):
        return False
    local = notify_logic.local_now(now_utc, ctx.settings.timezone)
    views = [v for v in _habit_views(ctx, local.date(), archived=False)
             if v.habit.nudge and v.habit.kind == "quit" and v.stats.today is None]
    if not views:
        return False
    urges = ctx.repo.list_urges(ctx.user_id, local.date() - timedelta(days=habit_logic.URGE_LOOKBACK_DAYS))
    for v in views:
        rt = habit_logic.risk_time([u for u in urges if u.habit_id == v.habit.id], local.date())
        if rt and notify_logic.nudge_due(local, rt, prefs.last_nudge):
            text, rows = notify_logic.build_nudge(_briefs([v])[0], rt)
            telegram.send(ctx.settings.telegram.chat_id, text, _tokens(ctx, rows, "nudge", local.date()))
            prefs.last_nudge = local.date()
            ctx.save_settings()
            return True
    return False


@app.post("/me/telegram/test")
def telegram_test(ctx: Ctx = Depends()) -> dict:
    if not ctx.settings.telegram.chat_id:
        raise HTTPException(status_code=409, detail="Link Telegram first.")
    try:
        _send_digest(ctx, "morning", force=True)
    except telegram.TelegramError as e:
        raise HTTPException(status_code=502, detail=f"Telegram: {e}")
    return {"sent": True}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, repo: GoalRepo = Depends(get_repo)) -> dict:
    """Updates from Telegram. Only accepted with our secret header (set via setWebhook)."""
    if not config.TELEGRAM_WEBHOOK_SECRET or \
            request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    update = await request.json()
    try:
        _handle_update(update, repo)
    except telegram.TelegramError:
        pass                         # never make Telegram retry forever
    return {"ok": True}


HELP = ("<b>planly.</b>\n/today — today's quote, tasks and habits\n/urge — log a craving right now\n"
        "/stop — disconnect this chat\nMessage times can be changed in Planly → Settings.")


def _refresh(ctx: "Ctx", chat: int, message_id: int, kind: str, day: date) -> None:
    """Redraw the message the button was on, so it shows the new state."""
    if kind == "nudge":
        return
    text, rows = _render(ctx, "morning" if kind == "morning" else "evening", day)
    if text is None:
        text, rows = "<b>All done for today.</b> ✓", []
    telegram.edit(chat, message_id, text, _tokens(ctx, rows, kind, day))
    ctx.save_settings()


def _handle_callback(cq: dict, repo: GoalRepo) -> None:
    chat = cq.get("message", {}).get("chat", {}).get("id")
    uid = repo.user_by_chat(chat) if chat else None
    if not uid:
        return telegram.answer(cq["id"], "This chat isn't linked to Planly.")
    ctx = Ctx(user_id=uid, repo=repo)
    data = ctx.settings.telegram.buttons.get((cq.get("data") or "")[2:])
    if not data:
        return telegram.answer(cq["id"], "That button is old — send /today for a fresh list.")
    today = _local_today(ctx)
    msg_id = cq.get("message", {}).get("message_id")
    kind = data.get("msg", "morning")

    if data.get("t") == "u":                                   # urge
        h = repo.get_habit(uid, data["habit_id"])
        if not h:
            return telegram.answer(cq["id"], "That habit was removed.")
        telegram.answer(cq["id"], "Logged")
        return telegram.send(chat, _log_urge(ctx, h))

    if data.get("t") == "h":                                   # habit yes / no
        h = repo.get_habit(uid, data["habit_id"])
        day = date.fromisoformat(data["day"])
        if not h:
            return telegram.answer(cq["id"], "That habit was removed.")
        if day < today - timedelta(days=1):
            return telegram.answer(cq["id"], "Too old for chat — change it on the Habits page.")
        try:
            _set_day(ctx, h, day, data["kept"], today)
        except HTTPException as e:
            return telegram.answer(cq["id"], str(e.detail)[:190])
        telegram.answer(cq["id"], ("Kept ✓" if data["kept"] else "Noted. Tomorrow counts the same."))
        if msg_id:
            _refresh(ctx, chat, msg_id, kind, today)
        return

    if data.get("locked"):                                     # study session tick
        return telegram.answer(cq["id"], "Evidence only: watch it (Planly extension) and it ticks itself.")
    rec = repo.get(uid, data["goal_id"])
    day = date.fromisoformat(data["day"])
    if not rec or day != today:
        return telegram.answer(cq["id"], "That day is over — send /today.")
    current = set(rec.ticks.days.get(day.isoformat(), []))
    done = data["session_id"] not in current
    try:
        _apply_tick(ctx, rec, day, data["session_id"], done)
    except HTTPException as e:
        return telegram.answer(cq["id"], str(e.detail)[:190])
    telegram.answer(cq["id"], "Ticked ✓" if done else "Unticked")
    if msg_id:
        _refresh(ctx, chat, msg_id, "morning", day)


def _handle_update(update: dict, repo: GoalRepo) -> None:
    if "callback_query" in update:
        return _handle_callback(update["callback_query"], repo)

    msg = update.get("message") or {}
    chat = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat or not text:
        return
    if text.startswith("/start"):
        code = text.split(maxsplit=1)[1] if " " in text else ""
        uid = repo.user_by_link_code(code) if code else None
        if not uid:
            return telegram.send(chat, "Open Planly → Settings → Connect Telegram to get a fresh link.")
        ctx = Ctx(user_id=uid, repo=repo)
        tg = ctx.settings.telegram
        if not tg.link_expires or tg.link_expires < datetime.now(timezone.utc):
            return telegram.send(chat, "That link expired. Get a fresh one in Planly → Settings.")
        other = repo.user_by_chat(chat)
        if other and other != uid:
            return telegram.send(chat, "This Telegram chat is already linked to another Planly account.")
        tg.chat_id, tg.link_code, tg.link_expires = chat, None, None
        tg.username = (msg.get("from") or {}).get("username")
        ctx.save_settings()
        return telegram.send(chat, "✓ Linked to Planly. Every morning: a quote, today's tasks and your habit "
                                   "streaks. In the evening: one check, only if something's open "
                                   "(change or turn off in Settings).\n\n" + HELP)
    uid = repo.user_by_chat(chat)
    if not uid:
        return telegram.send(chat, "This chat isn't linked yet. Planly → Settings → Connect Telegram.")
    ctx = Ctx(user_id=uid, repo=repo)
    if text.startswith("/today"):
        _send_digest(ctx, "morning", force=True)
    elif text.startswith("/urge"):
        hs = [h for h in repo.list_habits(uid) if not h.archived and h.kind == "quit"]
        if not hs:
            telegram.send(chat, "No habit to quit yet — add one in Planly → Habits.")
        elif len(hs) == 1:
            telegram.send(chat, _log_urge(ctx, hs[0]))
        else:
            day = _local_today(ctx)
            rows = [[(f"🔥 {h.shown}"[:60], {"t": "u", "habit_id": h.id})] for h in hs]
            telegram.send(chat, "Which one?", _tokens(ctx, rows, "nudge", day))
            ctx.save_settings()
    elif text.startswith("/stop"):
        ctx.settings.telegram = TelegramLink()
        ctx.save_settings()
        telegram.send(chat, "Disconnected. Planly won't message this chat again.")
    else:
        telegram.send(chat, HELP)


@app.post("/cron/notify")
def cron_notify(request: Request, repo: GoalRepo = Depends(get_repo)) -> dict:
    """Called every ~15 min (GitHub Actions / cron-job.org). Sends each user's morning message and
    evening check once, in their own timezone, plus at most one habit heads-up a day.
    Also keeps the free Render instance awake."""
    if not config.CRON_SECRET or request.headers.get("X-Cron-Secret") != config.CRON_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        telegram.ensure_webhook()
    except telegram.TelegramError:
        pass
    sent, now_ = [], datetime.now(timezone.utc)
    for uid in repo.telegram_users():
        ctx = Ctx(user_id=uid, repo=repo)
        for kind in notify_logic.due(now_, ctx.settings.notify, ctx.settings.timezone):
            day = _local_today(ctx)
            try:
                if _send_digest(ctx, kind):
                    sent.append(kind)
            except telegram.TelegramError:
                continue
            setattr(ctx.settings.notify, f"last_{kind}", day)     # once a day, sent or nothing to say
            ctx.save_settings()
        try:
            if _send_nudge(ctx, now_):
                sent.append("nudge")
        except telegram.TelegramError:
            pass
    return {"sent": len(sent)}


# ---- Google Calendar (read-only free/busy) ----

@app.get("/me/google/connect")
def google_connect(ctx: Ctx = Depends()) -> dict:
    if not gcal.available():
        raise HTTPException(status_code=409, detail="Google Calendar isn't set up on this Planly server yet.")
    return {"url": gcal.auth_url(ctx.user_id)}


@app.get("/google/callback")
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                    repo: GoalRepo = Depends(get_repo)):
    from fastapi.responses import RedirectResponse
    back = f"{config.FRONTEND_URL}/settings"
    if error or not code or not state:
        return RedirectResponse(f"{back}?google=cancelled")
    try:
        uid = gcal.user_from_state(state)
        refresh = gcal.exchange(code)
    except gcal.CalendarError:
        return RedirectResponse(f"{back}?google=failed")
    st = repo.get_settings(uid)
    st.google = GoogleLink(refresh_token_enc=gcal.encrypt(refresh), connected_at=datetime.now(timezone.utc))
    repo.save_settings(uid, st)
    return RedirectResponse(f"{back}?google=connected")


@app.delete("/me/google")
def google_disconnect(ctx: Ctx = Depends()) -> dict:
    g = ctx.settings.google
    if g.refresh_token_enc:
        try:
            gcal.revoke(gcal.decrypt(g.refresh_token_enc))
        except Exception:
            pass
    ctx.settings.google = GoogleLink()
    ctx.save_settings()
    return {"connected": False}


# ---------- evidence from the Chrome extension ----------

class WatchEvent(BaseModel):
    key: str = Field(pattern=r"^(yt:[A-Za-z0-9_-]{11}|page:.{1,500})$")
    url: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, max_length=300)
    duration_s: float = Field(ge=0, le=24 * 3600)   # 0 = unknown (page time only)
    intervals: list[tuple[float, float]] = Field(max_length=500)
    active_s: float = Field(default=0, ge=0, le=6 * 3600)   # page time since the last report


class WatchBatch(BaseModel):
    events: list[WatchEvent] = Field(max_length=50)


@app.post("/evidence/watch")
def post_watch(body: WatchBatch, user_id: str = Depends(current_user), repo: GoalRepo = Depends(get_repo)) -> dict:
    """Played ranges of videos on sites the user switched on. Only ever adds (union)."""
    merged = repo.merge_watch(user_id, [WatchEvidence(**e.model_dump()) for e in body.events])
    return {"coverage": {k: round(coverage(v), 3) for k, v in merged.items()}}


class GoalToday(BaseModel):
    goal_id: str
    goal: str
    today: TodayView


@app.get("/today", response_model=list[GoalToday])
def all_today(today: date | None = None, ctx: Ctx = Depends()) -> list[GoalToday]:
    """Today's sessions for every goal (the extension's on-page bar matches videos against these)."""
    today = _user_today(today)
    out = []
    for g in ctx.repo.list(ctx.user_id):
        rec = ctx.repo.get(ctx.user_id, g.id)
        if not (rec and rec.blueprint and rec.capacity and rec.plan_start):
            continue
        _close_past_days(ctx, rec, today)
        out.append(GoalToday(goal_id=rec.id, goal=rec.title, today=_today_view(ctx, rec, today)))
    return out


@app.get("/goals/{goal_id}/replan", response_model=ReplanResult)
def get_replan(goal_id: str, today: date | None = None, ctx: Ctx = Depends()) -> ReplanResult:
    """The plan from the first day that still needs doing: not before the plan starts,
    and not a day you've already checked in for (that work is already counted)."""
    rec = ctx.load(goal_id)
    if rec.blueprint and rec.capacity and _plausible_today(today):   # "what if" dates don't close days
        _close_past_days(ctx, rec, today or server_today())
    start = today or date.today()
    if rec.plan_start and rec.plan_start > start:
        start = rec.plan_start
    if rec.checked_through and rec.checked_through >= start:
        start = rec.checked_through + timedelta(days=1)
    return _replan(ctx, rec, rec.checkins, start)


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
