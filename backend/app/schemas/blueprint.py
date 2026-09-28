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

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


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

    # Which deliverable this belongs to (e.g. "assignment_2"). Milestones of one
    # deliverable are summed and compared with the user's benchmark.
    deliverable: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")
    # Which part of the anchored benchmark this matches, e.g. "video" (see Benchmark.parts)
    benchmark_part: str | None = None

    # Mandatory parts can never be offered as "cut to fit".
    required: bool = True

    # Dates are REFERENCES to the user's key dates, never dates the LLM wrote.
    # The planner turns them into real dates.
    start_after: str | None = Field(default=None, description="key date; can start the day after it")
    due_by: str | None = Field(default=None, description="key date this is due on")
    due_offset_days: int = Field(default=0, ge=0, le=365, description="days after due_by, only if user said so")

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
    # deliverable -> benchmark key it's comparable to, e.g. {"assignment_2": "a1"}
    anchors: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_graph(self, info: ValidationInfo) -> "GoalBlueprint":
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

        # 4. date references must exist (only checked when we know the key dates)
        known = (info.context or {}).get("key_dates") if info else None
        if known is not None:
            for m in self.milestones:
                for ref in (m.start_after, m.due_by):
                    if ref and ref not in known:
                        raise ValueError(
                            f"milestone '{m.key}' uses unknown key date '{ref}'; known: {sorted(known)}"
                        )

        # 5. anchors point at real deliverables and (when known) real benchmarks
        groups = self.deliverables()
        known_bm = (info.context or {}).get("benchmarks") if info else None
        for deliverable, bm in self.anchors.items():
            if deliverable not in groups:
                raise ValueError(f"anchor for unknown deliverable '{deliverable}'; known: {sorted(groups)}")
            if known_bm is not None and bm not in known_bm:
                raise ValueError(f"anchor uses unknown benchmark '{bm}'; known: {sorted(known_bm)}")
        return self

    def deliverables(self) -> dict[str, list[Milestone]]:
        """
        Milestones grouped by deliverable (untagged milestones stand alone).
        Within a group: dependency order, with anything waiting for a date window
        (e.g. peer review) last — that's the order it actually happens in.
        """
        by_key = {m.key: m for m in self.milestones}
        position = {k: i for i, k in enumerate(self.topological_order())}
        groups: dict[str, list[Milestone]] = {}
        for key in self.topological_order():
            m = by_key[key]
            groups.setdefault(m.deliverable or m.key, []).append(m)
        for members in groups.values():
            members.sort(key=lambda m: (m.start_after is not None, position[m.key]))
        return groups

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

    def with_milestone_hours(self, key: str, total_hours: float) -> "GoalBlueprint":
        """
        Copy with one milestone's total changed by the user.
        The learn/practice/... split keeps its proportions.
        """
        if not any(m.key == key for m in self.milestones):
            raise ValueError(f"no milestone '{key}'")
        return self._scaled({key}, total_hours)

    def with_deliverable_hours(
        self, deliverable: str, total_hours: float, parts: dict[str, float] | None = None
    ) -> "GoalBlueprint":
        """
        Copy with a whole deliverable's total changed (e.g. 'Assignment 2 = 27h').

        parts: the anchored benchmark's parts (e.g. {"video": 4, ...}). Milestones whose
        benchmark_part already matches that part (within 25%) are LOCKED — they were
        right, so they don't shrink. Only the others absorb the change. If that's
        impossible (locked alone exceed the new total), everything scales together.
        """
        groups = self.deliverables()
        if deliverable not in groups:
            raise ValueError(f"no deliverable '{deliverable}'")
        scale, _ = self._scaling_plan(groups[deliverable], total_hours, parts)
        kept_hours = sum(m.estimated_hours for m in groups[deliverable] if m.key not in scale)
        return self._scaled(scale, total_hours - kept_hours)

    def anchor_parts(self, deliverable: str, benchmarks: dict) -> dict[str, float] | None:
        """Parts of the benchmark this deliverable is anchored to, if any."""
        bm = benchmarks.get(self.anchors.get(deliverable, ""))
        return bm.parts if bm is not None and bm.parts else None

    def locked_milestones(
        self, deliverable: str, parts: dict[str, float] | None, total_hours: float | None = None
    ) -> list[str]:
        """
        Names of milestones that rescaling to total_hours would keep exactly as they are.
        Uses the SAME plan as with_deliverable_hours, so the message can't disagree with
        what happens (a real bug: it once said "kept as-is" and then scaled them anyway).
        """
        members = self.deliverables().get(deliverable, [])
        if total_hours is None:
            total_hours = sum(m.estimated_hours for m in members)
        _, kept = self._scaling_plan(members, total_hours, parts)
        return [m.name for m in members if m.key in kept]

    @classmethod
    def _scaling_plan(
        cls, members: list[Milestone], total_hours: float, parts: dict[str, float] | None
    ) -> tuple[set[str], set[str]]:
        """
        Decide which milestones absorb a change to the deliverable's total -> (scale, keep).
          1. Parts that match the benchmark stay; the rest absorb the change.
          2. If EVERY part matches, the change goes to the main work — the biggest part
             (new skills cost time there, not in the video or the reviews).
          3. If that's impossible (kept parts alone exceed the new total), scale everything.
        """
        all_keys = {m.key for m in members}
        locked = set(cls._locked_keys(members, parts))
        free = all_keys - locked
        hours = {m.key: m.estimated_hours for m in members}

        if locked and not free and parts:
            by_part: dict[str, set[str]] = {}
            for m in members:
                by_part.setdefault(m.benchmark_part, set()).add(m.key)
            main = max(by_part.values(), key=lambda keys: sum(hours[k] for k in keys))
            locked, free = all_keys - main, main

        if locked and free and total_hours - sum(hours[k] for k in locked) > 0:
            return free, locked
        return all_keys, set()

    @staticmethod
    def _locked_keys(members: list[Milestone], parts: dict[str, float] | None) -> list[str]:
        """
        A benchmark part is 'right' if ALL milestones tagged with it together are within
        25% of it. (Comparing one by one is wrong: notebook = baseline 18h + tuning 17h
        = 35h vs 18h, but the baseline alone would look like a perfect match.)
        """
        if not parts:
            return []
        by_part: dict[str, list[Milestone]] = {}
        for m in members:
            if m.benchmark_part in parts:
                by_part.setdefault(m.benchmark_part, []).append(m)
        locked = []
        for part, ms in by_part.items():
            total = sum(m.estimated_hours for m in ms)
            if abs(total - parts[part]) <= 0.25 * parts[part]:
                locked += [m.key for m in ms]
        return locked

    def _scaled(self, keys: set[str], total_hours: float) -> "GoalBlueprint":
        if total_hours <= 0:
            raise ValueError("hours must be > 0")
        targets = [m for m in self.milestones if m.key in keys]
        factor = total_hours / sum(m.estimated_hours for m in targets)
        splits = {
            m.key: {k: round(h * factor, 2) for k, h in m.hours_by_kind.items()} for m in targets
        }
        # fix rounding so the total is exactly what the user typed
        drift = round(total_hours - sum(sum(v.values()) for v in splits.values()), 2)
        last = splits[targets[-1].key]
        first_kind = next(iter(last))
        last[first_kind] = round(last[first_kind] + drift, 2)
        if last[first_kind] <= 0:
            raise ValueError("hours too small to split")
        milestones = [
            m.model_copy(update={"hours_by_kind": splits[m.key]}) if m.key in keys else m
            for m in self.milestones
        ]
        return self.model_copy(update={"milestones": milestones})


