"""
Every time a user corrects an AI hour estimate, log it.

This is the first real training data for the "personal multiplier" / duration
model later: (what the AI guessed, what the human said, how confident the AI was).

For now: one JSON line per correction in backend/data/estimate_corrections.jsonl.
Moves to a Supabase table with the rest of storage.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "estimate_corrections.jsonl"


def make_row(
    goal: str,
    milestone_key: str,
    milestone_name: str,
    ai_hours: float,
    user_hours: float,
    confidence: float,
    had_benchmark: bool,
    source: str = "user_review",   # "user_review" | "benchmark_check" | "api"
) -> dict | None:
    """The correction as a dict, or None if nothing changed (retyping the same number isn't data)."""
    if abs(user_hours - ai_hours) < 0.01:
        return None
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "goal": goal,
        "milestone_key": milestone_key,       # or a deliverable name
        "milestone_name": milestone_name,
        "ai_hours": ai_hours,
        "user_hours": user_hours,
        "ratio": round(user_hours / ai_hours, 3),
        "ai_confidence": confidence,
        "had_benchmark": had_benchmark,
        "source": source,
    }


def log_correction(
    goal: str,
    milestone_key: str,
    milestone_name: str,
    ai_hours: float,
    user_hours: float,
    confidence: float,
    had_benchmark: bool,
    path: Path | None = None,
    source: str = "user_review",
) -> dict | None:
    """Terminal scripts: append to data/estimate_corrections.jsonl. (The API logs to the database.)"""
    row = make_row(goal, milestone_key, milestone_name, ai_hours, user_hours,
                   confidence, had_benchmark, source)
    if row is None:
        return None
    target = path or LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row
