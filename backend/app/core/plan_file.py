"""
Save/load one plan to backend/data/plan.json, so the terminal scripts can
remember a plan between runs (check-ins need that). Replaced by Supabase later —
the SavedPlan shape is basically what the tables will hold.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from app.planners.capacity import CapacityProfile
from app.planners.feasibility import PlanItem, resolve_items
from app.planners.progress import CheckIn
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalProfile

PLAN_PATH = Path(__file__).resolve().parents[2] / "data" / "plan.json"


class SavedPlan(BaseModel):
    goal: str
    profile: GoalProfile
    blueprint: GoalBlueprint
    capacity: CapacityProfile
    start: date
    checkins: list[CheckIn] = Field(default_factory=list)
    checked_through: date | None = None   # last day the user has checked in for
    saved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def items(self) -> list[PlanItem]:
        """The original plan items (multiplier 1.0) — what progress is measured against."""
        p = self.profile
        return resolve_items(
            self.blueprint, p.key_date_map(), p.deadline, 1.0, p.soft_key_dates(), p.deadline_hard
        )


def save(plan: SavedPlan, path: Path | None = None) -> Path:
    target = path or PLAN_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    plan.saved_at = datetime.now(timezone.utc)
    target.write_text(plan.model_dump_json(indent=2))
    return target


def load(path: Path | None = None) -> SavedPlan | None:
    target = path or PLAN_PATH
    if not target.exists():
        return None
    return SavedPlan.model_validate_json(target.read_text())
