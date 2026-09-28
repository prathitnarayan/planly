"""
Goal Blueprint schema (v0).

The LLM must return JSON matching GoalBlueprint. Pydantic validates it;
if validation fails, send the error message back to the LLM and ask it to fix
its output (usually 1 retry is enough).

The LLM decides WHAT the milestones are. The backend decides everything
involving numbers and time (scheduling, capacity, feasibility).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class GoalCategory(str, Enum):
    learning = "learning"          # courses, skills, languages
    exam = "exam"                  # CAT, GRE, certifications
    career = "career"              # role switch, interview prep
    project = "project"            # build an app, write a book
    fitness = "fitness"
    other = "other"


class TaskKind(str, Enum):
    learn = "learn"          # watch / read
    practice = "practice"    # problems, exercises
    project = "project"      # build something
    revise = "revise"
    assess = "assess"        # mock test, quiz
    buffer = "buffer"        # slack time


class Milestone(BaseModel):
    key: str = Field(
        pattern=r"^[a-z0-9_]+$",
        description="Short stable id, e.g. 'sql_basics'. Used for dependencies.",
    )
    name: str
    description: str = ""
    topics: list[str] = Field(default_factory=list)

    # Rough effort split. The backend turns these into actual tasks.
    hours_by_kind: dict[TaskKind, float] = Field(
        description="e.g. {'learn': 6, 'practice': 8, 'revise': 2}"
    )
    confidence: float = Field(ge=0, le=1, description="How sure the estimate is")

    depends_on: list[str] = Field(default_factory=list, description="Milestone keys")
    done_criteria: list[str] = Field(
        min_length=1,
        description="Measurable checks, e.g. 'Solve 50 SQL problems'. Never 'Study SQL'.",
    )

    @field_validator("hours_by_kind")
    @classmethod
    def hours_positive(cls, v: dict[TaskKind, float]) -> dict[TaskKind, float]:
        if not v:
            raise ValueError("hours_by_kind must not be empty")
        for kind, hours in v.items():
            if hours <= 0:
                raise ValueError(f"hours for '{kind.value}' must be > 0, got {hours}")
        return v

    @property
    def estimated_hours(self) -> float:
        return sum(self.hours_by_kind.values())


class GoalBlueprint(BaseModel):
    goal: str
    category: GoalCategory
    summary: str = Field(description="1-2 lines: the approach in plain words")
    milestones: list[Milestone] = Field(min_length=1)

    # Honesty fields: keep AI guesses separate from facts the user gave.
    assumptions: list[str] = Field(
        default_factory=list,
        description="Things the AI assumed, e.g. 'User knows basic Python'",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Things worth asking the user before planning",
    )

    @model_validator(mode="after")
    def check_graph(self) -> "GoalBlueprint":
        keys = [m.key for m in self.milestones]

        # 1. keys unique
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate milestone keys: {sorted(dupes)}")

        # 2. dependencies point to real milestones, not themselves
        key_set = set(keys)
        for m in self.milestones:
            for dep in m.depends_on:
                if dep == m.key:
                    raise ValueError(f"milestone '{m.key}' depends on itself")
                if dep not in key_set:
                    raise ValueError(f"milestone '{m.key}' depends on unknown '{dep}'")

        # 3. no cycles (A needs B, B needs A) — LLMs do produce these
        self.topological_order()
        return self

    def topological_order(self) -> list[str]:
        """Order milestones so prerequisites come first. Raises on a cycle."""
        remaining = {m.key: set(m.depends_on) for m in self.milestones}
        order: list[str] = []
        while remaining:
            ready = sorted(k for k, deps in remaining.items() if not deps)
            if not ready:
                raise ValueError(f"dependency cycle among: {sorted(remaining)}")
            for k in ready:
                order.append(k)
                del remaining[k]
            for deps in remaining.values():
                deps.difference_update(ready)
        return order

    @property
    def total_hours(self) -> float:
        return sum(m.estimated_hours for m in self.milestones)


if __name__ == "__main__":
    example = {
        "goal": "Learn SQL for data analysis",
        "category": "learning",
        "summary": "Basics first, then joins and aggregation, then a real-data project.",
        "milestones": [
            {
                "key": "sql_basics",
                "name": "SQL basics",
                "hours_by_kind": {"learn": 4, "practice": 4},
                "confidence": 0.8,
                "done_criteria": ["Solve 30 SELECT/WHERE/ORDER BY problems"],
            },
            {
                "key": "joins_agg",
                "name": "Joins and aggregation",
                "hours_by_kind": {"learn": 5, "practice": 8, "revise": 2},
                "confidence": 0.7,
                "depends_on": ["sql_basics"],
                "done_criteria": ["Solve 40 join/group-by problems", "Score >= 80% on quiz"],
            },
            {
                "key": "project",
                "name": "Analysis project on a real dataset",
                "hours_by_kind": {"project": 10, "buffer": 2},
                "confidence": 0.5,
                "depends_on": ["joins_agg"],
                "done_criteria": ["Answer 5 business questions with queries, written up"],
            },
        ],
        "assumptions": ["User has no prior SQL experience"],
    }
    bp = GoalBlueprint.model_validate(example)
    print("order:", bp.topological_order())
    print("total hours:", bp.total_hours)

    # cycle should be rejected
    example["milestones"][0]["depends_on"] = ["project"]
    try:
        GoalBlueprint.model_validate(example)
    except ValidationError as e:
        print("rejected as expected:", e.errors()[0]["msg"])
