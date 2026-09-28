"""
Estimate check: compare the AI's hours with the user's own track record.

Why this exists: in real runs the LLM wrote "Assignment 1 took 25 hours" in its
assumptions and then planned 56h for a comparable assignment. LLMs repeat
numbers; they don't reliably reason with them. So the comparison is code.

Rule: for every deliverable the blueprint anchors to a benchmark, sum its
milestones. If that's more than 1.5x or less than 0.5x the benchmark, warn.
The user decides — the check only surfaces the gap.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import Benchmark

TOO_HIGH = 1.5
TOO_LOW = 0.5


class EstimateWarning(BaseModel):
    deliverable: str
    ai_hours: float
    benchmark_key: str
    benchmark_what: str
    benchmark_hours: float
    benchmark_parts: dict[str, float]
    ratio: float
    direction: str            # "high" or "low"
    suggested_hours: float    # the benchmark itself — the user can adjust from there

    @property
    def message(self) -> str:
        parts = ", ".join(f"{k} {v:g}" for k, v in self.benchmark_parts.items())
        parts = f" ({parts})" if parts else ""
        return (
            f"AI estimates {self.deliverable} at {self.ai_hours:g}h, but "
            f"'{self.benchmark_what}' took you {self.benchmark_hours:g}h{parts} "
            f"— {self.ratio:.1f}x."
        )


def check_estimates(
    blueprint: GoalBlueprint, benchmarks: dict[str, Benchmark]
) -> list[EstimateWarning]:
    groups = blueprint.deliverables()
    warnings = []
    for deliverable, bm_key in blueprint.anchors.items():
        bm = benchmarks.get(bm_key)
        if bm is None or deliverable not in groups:
            continue
        ai_hours = round(sum(m.estimated_hours for m in groups[deliverable]), 2)
        ratio = ai_hours / bm.hours
        if TOO_LOW <= ratio <= TOO_HIGH:
            continue
        warnings.append(EstimateWarning(
            deliverable=deliverable,
            ai_hours=ai_hours,
            benchmark_key=bm.key,
            benchmark_what=bm.what,
            benchmark_hours=bm.hours,
            benchmark_parts=bm.parts,
            ratio=round(ratio, 2),
            direction="high" if ratio > TOO_HIGH else "low",
            suggested_hours=bm.hours,
        ))
    return warnings
