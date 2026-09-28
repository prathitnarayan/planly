from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class KeyDate(BaseModel):
    """A date the user stated, e.g. an assignment due date."""

    key: str = Field(pattern=r"^[a-z0-9_]+$", description="e.g. 'a2_due'")
    label: str
    date: dt.date
    # hard = can't slip (graded submission, exam). Soft = self-imposed target.
    hard: bool = True


class Benchmark(BaseModel):
    """How long similar past work took the user — the best anchor for estimates."""

    key: str = Field(default="benchmark", pattern=r"^[a-z0-9_]+$", description="e.g. 'a1'")
    what: str
    hours: float = Field(gt=0)
    # Optional breakdown the user gave, e.g. {"notebook": 18, "video": 4, "peer_review": 3}
    parts: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def total_from_parts(self) -> "Benchmark":
        # Trust the user's parts over the model's arithmetic.
        if self.parts:
            if any(h <= 0 for h in self.parts.values()):
                raise ValueError("benchmark parts must be > 0")
            self.hours = round(sum(self.parts.values()), 2)
        return self


class GoalProfile(BaseModel):
    """What we know about the user for this goal. Filled by the interview."""

    current_level: str | None = None
    target_outcome: str | None = None
    deadline: dt.date | None = None
    # "given" = date known, "none" = user said there's no deadline, "unknown" = not asked yet
    deadline_status: Literal["given", "none", "unknown"] = "unknown"
    resources: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    key_dates: list[KeyDate] = Field(default_factory=list)
    benchmarks: list[Benchmark] = Field(default_factory=list)
    # "given" = have one, "none" = user said nothing comparable, "unknown" = not asked yet
    benchmark_status: Literal["given", "none", "unknown"] = "unknown"
    notes: str | None = None

    @field_validator("benchmarks", mode="before")
    @classmethod
    def drop_placeholder_benchmarks(cls, value):
        """
        Real bug (28 Sep): before the user mentioned any benchmark, the model sent
        {"hours": 0} as a placeholder, strict validation rejected the WHOLE turn twice,
        and the interview crashed. A half-filled optional field isn't worth that:
        drop entries with no real numbers, keep the rest (which are still validated).
        """
        if not isinstance(value, list):
            return value
        kept = []
        for b in value:
            if not isinstance(b, dict):
                kept.append(b)
                continue
            parts = {k: v for k, v in (b.get("parts") or {}).items()
                     if isinstance(v, (int, float)) and v > 0}
            hours = b.get("hours")
            has_hours = isinstance(hours, (int, float)) and hours > 0
            if not parts and not has_hours:
                continue          # placeholder — nothing the user actually said
            kept.append({**b, "parts": parts, "hours": hours if has_hours else sum(parts.values())})
        return kept

    @model_validator(mode="after")
    def deadline_from_key_dates(self) -> "GoalProfile":
        # Code decides, not the model: no overall deadline -> use the latest key date.
        if self.deadline is None and self.key_dates:
            self.deadline = max(k.date for k in self.key_dates)
        if self.deadline is not None:
            self.deadline_status = "given"
        if self.benchmarks:
            self.benchmark_status = "given"
        return self

    def benchmark_map(self) -> dict[str, Benchmark]:
        return {b.key: b for b in self.benchmarks}

    def soft_key_dates(self) -> set[str]:
        return {k.key for k in self.key_dates if not k.hard}

    @property
    def deadline_hard(self) -> bool:
        """Overall deadline is hard if it IS a hard key date, or there are no key dates to go by."""
        if self.deadline is None:
            return True
        matches = [k for k in self.key_dates if k.date == self.deadline]
        return any(k.hard for k in matches) if matches else True

    def describe(self) -> list[str]:
        """What we understood, in plain lines the user can check at a glance."""
        lines = [
            f"Starting from:  {self.current_level or '— (not given)'}",
            f"Done means:     {self.target_outcome or '— (not given)'}",
        ]
        if self.key_dates:
            for k in sorted(self.key_dates, key=lambda k: k.date):
                kind = "hard" if k.hard else "flexible"
                lines.append(f"Deadline:       {k.label} — {k.date:%a %d %b} ({kind})")
        elif self.deadline:
            lines.append(f"Deadline:       {self.deadline:%a %d %b}")
        else:
            lines.append("Deadline:       none")
        for b in self.benchmarks:
            parts = ", ".join(f"{k.replace('_', ' ')} {v:g}h" for k, v in b.parts.items())
            lines.append(f"Track record:   {b.what} took {b.hours:g}h" + (f" ({parts})" if parts else ""))
        if not self.benchmarks:
            lines.append("Track record:   none given")
        if self.resources:
            lines.append(f"Resources:      {', '.join(self.resources)}")
        if self.constraints:
            lines.append(f"Constraints:    {', '.join(self.constraints)}")
        if self.notes:
            lines.append(f"Notes:          {self.notes}")
        return lines

    def key_date_map(self) -> dict[str, dt.date]:
        return {k.key: k.date for k in self.key_dates}


class InterviewTurn(BaseModel):
    """One reply from the interviewer LLM."""

    done: bool
    question: str | None = None
    profile: GoalProfile

    @model_validator(mode="after")
    def question_unless_done(self) -> "InterviewTurn":
        if not self.done and not (self.question and self.question.strip()):
            raise ValueError("question is required when done is false")
        return self


class GoalCheck(BaseModel):
    """Is the first thing the user typed a goal, or their background?"""

    is_goal: bool
    suggested_goal: str | None = None
    background: str | None = None
