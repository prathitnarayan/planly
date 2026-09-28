"""
Planly API — v0.

Flow you can try in /docs:
  POST /goals                      {"goal": "..."}           -> first question
  POST /goals/{id}/answer          {"answer": "..."}         -> next question, until done
  POST /goals/{id}/blueprint                                  -> milestones + hours
  POST /goals/{id}/feasibility     {"capacity": {...}}       -> fits or not, with options

Storage is in-memory for now (lost on restart). Supabase comes next.
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
from app.core import config, store
from app.core.estimate_log import log_correction
from app.planners.capacity import CapacityProfile
from app.planners.estimate_check import EstimateWarning, check_estimates
from app.planners.feasibility import FeasibilityResult, check_feasibility, resolve_items
from app.planners.progress import CheckIn, ReplanResult, replan
from app.planners.scheduler import Schedule, build_schedule
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalCheck, GoalProfile

app = FastAPI(title="Planly API", version="0.2.0")


@lru_cache
def get_llm() -> LLMClient:
    return OpenAIClient(config.OPENAI_API_KEY, config.OPENAI_MODEL, config.OPENAI_BASE_URL)


def llm_call(fn, *args):
    try:
        return fn(*args)
    except LLMOutputError as e:
        raise HTTPException(status_code=502, detail=f"AI returned unusable output: {e}")


def load_goal(goal_id: str) -> store.GoalRecord:
    rec = store.get_goal(goal_id)
    if not rec:
        raise HTTPException(status_code=404, detail="goal not found")
    return rec


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


def to_view(rec: store.GoalRecord) -> GoalView:
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
    )


# ---------- routes ----------

@app.get("/health")
def health() -> dict:
    return {"ok": True}


class TextIn(BaseModel):
    text: str


@app.post("/goals/check", response_model=GoalCheck)
def goal_check(body: TextIn, llm: LLMClient = Depends(get_llm)) -> GoalCheck:
    """Call before POST /goals: is this a goal, or the user's background? (frontend decides)"""
    return llm_call(check_goal, llm, body.text)


class NewGoal(BaseModel):
    goal: str
    background: str | None = None   # e.g. what /goals/check returned as background


@app.post("/goals", response_model=GoalView)
def create_goal(body: NewGoal, llm: LLMClient = Depends(get_llm)) -> GoalView:
    state = llm_call(
        lambda: start_interview(llm, body.goal, config.MAX_INTERVIEW_QUESTIONS, background=body.background)
    )
    return to_view(store.create_goal(state))


@app.get("/goals/{goal_id}", response_model=GoalView)
def read_goal(goal_id: str) -> GoalView:
    return to_view(load_goal(goal_id))


class Answer(BaseModel):
    answer: str


@app.post("/goals/{goal_id}/answer", response_model=GoalView)
def answer(goal_id: str, body: Answer, llm: LLMClient = Depends(get_llm)) -> GoalView:
    rec = load_goal(goal_id)
    if rec.interview.done:
        raise HTTPException(status_code=409, detail="interview already finished")
    if not body.answer.strip():
        raise HTTPException(status_code=422, detail="answer is empty")
    rec.interview = llm_call(
        answer_question, llm, rec.interview, body.answer, config.MAX_INTERVIEW_QUESTIONS
    )
    return to_view(rec)


@app.post("/goals/{goal_id}/correct", response_model=GoalView)
def correct_profile(goal_id: str, body: TextIn, llm: LLMClient = Depends(get_llm)) -> GoalView:
    """User fixes 'what I understood'. Any existing blueprint is dropped — it was built on the old profile."""
    rec = load_goal(goal_id)
    if not rec.interview.done:
        raise HTTPException(status_code=409, detail="finish the interview first")
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="correction is empty")
    rec.interview = llm_call(apply_correction, llm, rec.interview, body.text)
    rec.blueprint = None
    return to_view(rec)


@app.post("/goals/{goal_id}/blueprint", response_model=GoalBlueprint)
def make_blueprint(goal_id: str, llm: LLMClient = Depends(get_llm)) -> GoalBlueprint:
    rec = load_goal(goal_id)
    if not rec.interview.done:
        raise HTTPException(status_code=409, detail="finish the interview first")
    rec.blueprint = llm_call(
        generate_blueprint, llm, rec.interview.goal, rec.interview.profile, rec.interview.messages
    )
    return rec.blueprint


def tomorrow() -> date:
    return date.today() + timedelta(days=1)


class HoursEdit(BaseModel):
    hours: float


@app.patch("/goals/{goal_id}/blueprint/milestones/{key}", response_model=GoalBlueprint)
def edit_milestone_hours(goal_id: str, key: str, body: HoursEdit) -> GoalBlueprint:
    """User says 'this will take me X hours'. Split keeps its proportions; the change is logged."""
    rec = load_goal(goal_id)
    if not rec.blueprint:
        raise HTTPException(status_code=409, detail="generate the blueprint first")
    old = next((m for m in rec.blueprint.milestones if m.key == key), None)
    try:
        rec.blueprint = rec.blueprint.with_milestone_hours(key, body.hours)
    except ValueError as e:
        raise HTTPException(status_code=404 if old is None else 422, detail=str(e))
    log_correction(
        rec.interview.goal, key, old.name, old.estimated_hours, body.hours,
        old.confidence, bool(rec.interview.profile.benchmarks), source="api",
    )
    return rec.blueprint


@app.patch("/goals/{goal_id}/blueprint/deliverables/{deliverable}", response_model=GoalBlueprint)
def edit_deliverable_hours(goal_id: str, deliverable: str, body: HoursEdit) -> GoalBlueprint:
    """User says 'Assignment 2 will take me X hours'. Milestones inside keep their proportions."""
    rec = load_goal(goal_id)
    if not rec.blueprint:
        raise HTTPException(status_code=409, detail="generate the blueprint first")
    group = rec.blueprint.deliverables().get(deliverable)
    try:
        parts = rec.blueprint.anchor_parts(deliverable, rec.interview.profile.benchmark_map())
        rec.blueprint = rec.blueprint.with_deliverable_hours(deliverable, body.hours, parts)
    except ValueError as e:
        raise HTTPException(status_code=404 if group is None else 422, detail=str(e))
    old_hours = sum(m.estimated_hours for m in group)
    old_conf = min(m.confidence for m in group)
    log_correction(
        rec.interview.goal, deliverable, deliverable, old_hours, body.hours,
        old_conf, bool(rec.interview.profile.benchmarks), source="api",
    )
    return rec.blueprint


class EstimateCheck(BaseModel):
    warnings: list[EstimateWarning]
    messages: list[str]


@app.get("/goals/{goal_id}/estimate-check", response_model=EstimateCheck)
def estimate_check(goal_id: str) -> EstimateCheck:
    rec = load_goal(goal_id)
    if not rec.blueprint:
        raise HTTPException(status_code=409, detail="generate the blueprint first")
    warnings = check_estimates(rec.blueprint, rec.interview.profile.benchmark_map())
    return EstimateCheck(warnings=warnings, messages=[w.message for w in warnings])


class GoalFeasibilityRequest(BaseModel):
    capacity: CapacityProfile
    start: date | None = None      # default: tomorrow (today is usually half gone)
    deadline: date | None = None   # default: deadline from the interview
    personal_multiplier: float = 1.0


@app.post("/goals/{goal_id}/feasibility", response_model=FeasibilityResult)
def goal_feasibility(goal_id: str, body: GoalFeasibilityRequest) -> FeasibilityResult:
    rec = load_goal(goal_id)
    if not rec.blueprint:
        raise HTTPException(status_code=409, detail="generate the blueprint first")
    profile = rec.interview.profile
    try:
        return check_feasibility(
            rec.blueprint,
            body.capacity,
            body.start or tomorrow(),
            body.deadline or profile.deadline,
            body.personal_multiplier,
            profile.key_date_map(),
            profile.soft_key_dates(),
            profile.deadline_hard,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/goals/{goal_id}/schedule", response_model=Schedule)
def goal_schedule(goal_id: str, body: GoalFeasibilityRequest) -> Schedule:
    """Weekly sprints with timed sessions. Same simulation as /feasibility, so they always agree."""
    rec = load_goal(goal_id)
    if not rec.blueprint:
        raise HTTPException(status_code=409, detail="generate the blueprint first")
    profile = rec.interview.profile
    try:
        items = resolve_items(
            rec.blueprint, profile.key_date_map(), body.deadline or profile.deadline,
            body.personal_multiplier, profile.soft_key_dates(), profile.deadline_hard,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return build_schedule(rec.blueprint, items, body.capacity, body.start or tomorrow())


# ---------- the loop: capacity -> check-ins -> replan ----------

@app.put("/goals/{goal_id}/capacity", response_model=CapacityProfile)
def set_capacity(goal_id: str, body: CapacityProfile) -> CapacityProfile:
    rec = load_goal(goal_id)
    rec.capacity = body
    return body


def _replan(rec: store.GoalRecord, today: date) -> ReplanResult:
    if not rec.blueprint:
        raise HTTPException(status_code=409, detail="generate the blueprint first")
    if not rec.capacity:
        raise HTTPException(status_code=409, detail="set capacity first: PUT /goals/{id}/capacity")
    p = rec.interview.profile
    try:
        items = resolve_items(rec.blueprint, p.key_date_map(), p.deadline, 1.0,
                              p.soft_key_dates(), p.deadline_hard)
        return replan(rec.blueprint, items, rec.checkins, rec.capacity, today)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class CheckInBatch(BaseModel):
    checkins: list[CheckIn]
    today: date | None = None   # default: today. The replan starts the day after.


@app.post("/goals/{goal_id}/checkins", response_model=ReplanResult)
def add_checkins(goal_id: str, body: CheckInBatch) -> ReplanResult:
    """Log what actually happened; get back progress, alerts and the updated plan from tomorrow."""
    rec = load_goal(goal_id)
    before = list(rec.checkins)
    rec.checkins += body.checkins
    try:
        return _replan(rec, (body.today or date.today()) + timedelta(days=1))
    except HTTPException:
        rec.checkins = before   # don't keep check-ins that couldn't be applied
        raise


@app.get("/goals/{goal_id}/replan", response_model=ReplanResult)
def get_replan(goal_id: str, today: date | None = None) -> ReplanResult:
    return _replan(load_goal(goal_id), today or date.today())


# Stateless version, handy for experimenting with a hand-written blueprint.
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
