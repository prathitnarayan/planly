"""
Temporary in-memory store. Everything is lost when the server restarts.
Replaced by Supabase in the next phase — same functions, real tables.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.ai.goal_intake import InterviewState
from app.planners.capacity import CapacityProfile
from app.planners.progress import CheckIn
from app.schemas.blueprint import GoalBlueprint


class GoalRecord(BaseModel):
    id: str
    interview: InterviewState
    blueprint: GoalBlueprint | None = None
    capacity: CapacityProfile | None = None
    checkins: list[CheckIn] = Field(default_factory=list)


_goals: dict[str, GoalRecord] = {}


def create_goal(interview: InterviewState) -> GoalRecord:
    rec = GoalRecord(id=uuid.uuid4().hex[:8], interview=interview)
    _goals[rec.id] = rec
    return rec


def get_goal(goal_id: str) -> GoalRecord | None:
    return _goals.get(goal_id)


def clear() -> None:
    _goals.clear()
