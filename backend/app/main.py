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
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from app.ai.goal_intake import (
    answer_question, apply_correction, check_goal, generate_blueprint, start_interview,
)
from app.ai.llm import LLMClient, LLMOutputError, OpenAIClient
from app.core import config
from app.core.auth import current_user
from app.core.estimate_log import make_row
from app.core.repo import (
    DatabaseUnavailable, GoalRecord, GoalRepo, GoalSummary, InMemoryRepo, PostgresRepo,
)
from app.planners.capacity import CapacityProfile
from app.planners.estimate_check import EstimateWarning, check_estimates
from app.planners.feasibility import FeasibilityResult, check_feasibility, resolve_items
from app.planners.progress import CheckIn, ReplanResult, replan
from app.planners.scheduler import Schedule, build_schedule
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalCheck, GoalProfile

app = FastAPI(title="Planly API", version="0.3.0")


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
        generate_blueprint, llm, rec.interview.goal, rec.interview.profile, rec.interview.messages
    )
    ctx.save(rec)
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
    warnings = check_estimates(require_blueprint(rec), rec.interview.profile.benchmark_map())
    return EstimateCheck(warnings=warnings, messages=[w.message for w in warnings])


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
def set_capacity(goal_id: str, body: CapacityProfile, ctx: Ctx = Depends()) -> CapacityProfile:
    rec = ctx.load(goal_id)
    rec.capacity = body
    ctx.save(rec)
    return body


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


class CheckInBatch(BaseModel):
    checkins: list[CheckIn]
    today: date | None = None   # default: today. The replan starts the day after.


@app.post("/goals/{goal_id}/checkins", response_model=ReplanResult)
def add_checkins(goal_id: str, body: CheckInBatch, ctx: Ctx = Depends()) -> ReplanResult:
    """Log what actually happened; get back progress, alerts and the updated plan from tomorrow."""
    rec = ctx.load(goal_id)
    # Replan FIRST with the new check-ins; only store them if that works (bad batch = nothing saved).
    result = _replan(rec, rec.checkins + body.checkins, (body.today or date.today()) + timedelta(days=1))
    ctx.repo.add_checkins(ctx.user_id, rec.id, body.checkins)
    return result


@app.get("/goals/{goal_id}/replan", response_model=ReplanResult)
def get_replan(goal_id: str, today: date | None = None, ctx: Ctx = Depends()) -> ReplanResult:
    rec = ctx.load(goal_id)
    return _replan(rec, rec.checkins, today or date.today())


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
