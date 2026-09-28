"""
Run the real API with a scripted stand-in for the AI — for trying the frontend
without an AI key or credits. Dev mode only (in-memory, no login).

    cd backend
    python -m tests.fake_ai_server        # then open the frontend at localhost:3000

The fake plays the Kaggle scenario: goal check, four interview questions, a blueprint
that's inflated vs the user's benchmark (so the estimate warning shows up).
"""

from __future__ import annotations

import json

import uvicorn

from app.ai.prompts import BLUEPRINT_SYSTEM, CORRECTION_SYSTEM, GOAL_CHECK_SYSTEM
from app.core import config
from app.main import app, get_llm

QUESTIONS = [
    "What have you finished already, and what do you know so far?",
    "What does 'done' look like for each assignment?",
    "When is each assignment due?",
]

PROFILE = {
    "current_level": "Assignment 1 done; Python, pandas, linear/logistic regression. No feature engineering or XGBoost yet.",
    "target_outcome": "Per assignment: clear the Kaggle cutoff, 7 models (tune 3), 8–12 min video, review 5 peers.",
    "key_dates": [
        {"key": "a2_due", "label": "Assignment 2 due", "date": "2026-10-15", "hard": True},
        {"key": "a3_due", "label": "Assignment 3 due", "date": "2026-11-05", "hard": True},
    ],
    "resources": ["MLP lectures", "Assignment 1 notebook"],
    "benchmarks": [{"key": "a1", "what": "Assignment 1 incl. video and reviews", "hours": 25,
                    "parts": {"notebook": 18, "video": 4, "peer_review": 3}}],
    "notes": "\"Peer review is open for 4 days after each deadline.\"",
}


def _ms(key, name, split, deliverable, part, **kw):
    return {"key": key, "name": name, "hours_by_kind": split, "confidence": 0.6,
            "done_criteria": kw.pop("dod", ["Done"]), "deliverable": deliverable,
            "benchmark_part": part, **kw}


BLUEPRINT = {
    "goal": "Finish MLP Kaggle Assignments 2 and 3", "category": "learning",
    "summary": "Learn feature engineering and XGBoost inside Assignment 2, then reuse it for 3.",
    "milestones": [
        _ms("a2_notebook", "Assignment 2: Notebook — baseline, 7 models, tune 3",
            {"learn": 8, "practice": 16, "project": 11}, "assignment_2", "notebook", due_by="a2_due",
            dod=["Baseline clears the Kaggle cutoff", "7 models trained, 3 tuned"]),
        _ms("a2_video", "Assignment 2: Walkthrough video", {"project": 4}, "assignment_2", "video",
            depends_on=["a2_notebook"], due_by="a2_due", dod=["8–12 min video, own voice, view access on"]),
        _ms("a2_review", "Assignment 2: Peer reviews", {"assess": 3}, "assignment_2", "peer_review",
            start_after="a2_due", due_by="a2_due", due_offset_days=4, dod=["5 peer notebooks reviewed"]),
        _ms("a3_notebook", "Assignment 3: Notebook — baseline, 7 models, tune 3",
            {"learn": 2, "practice": 9, "project": 7}, "assignment_3", "notebook",
            depends_on=["a2_notebook"], due_by="a3_due", dod=["Baseline clears the Kaggle cutoff"]),
        _ms("a3_video", "Assignment 3: Walkthrough video", {"project": 4}, "assignment_3", "video",
            depends_on=["a3_notebook"], due_by="a3_due"),
        _ms("a3_review", "Assignment 3: Peer reviews", {"assess": 3}, "assignment_3", "peer_review",
            start_after="a3_due", due_by="a3_due", due_offset_days=4),
    ],
    "assumptions": ["Anchored on Assignment 1 (25h); +learning for feature engineering and XGBoost."],
    "open_questions": ["What dataset is Assignment 3 on?"],
    "anchors": {"assignment_2": "a1", "assignment_3": "a1"},
}


class ScriptedAI:
    """Answers by looking at which prompt it got — no network, deterministic."""

    def complete_json(self, system, messages):
        if system.startswith(GOAL_CHECK_SYSTEM[:40]):
            text = messages[-1]["content"].lower()
            is_goal = not any(w in text for w in (" is done", "not started", "i know"))
            return json.dumps({"is_goal": is_goal, "suggested_goal": None if is_goal else
                               "Finish MLP Kaggle Assignments 2 and 3",
                               "background": None if is_goal else messages[-1]["content"]})
        if system.startswith(BLUEPRINT_SYSTEM[:40]):
            return json.dumps(BLUEPRINT)
        if CORRECTION_SYSTEM[-60:] in system:
            fix = messages[-1]["content"].removeprefix("Correction: ")
            return json.dumps({"done": True, "question": None,
                               "profile": {**PROFILE, "notes": f"{PROFILE['notes']} Correction: {fix}"}})
        answered = sum(1 for m in messages if m["role"] == "user") - 1
        if answered < len(QUESTIONS):
            return json.dumps({"done": False, "question": QUESTIONS[answered], "profile": {}})
        return json.dumps({"done": True, "question": None, "profile": PROFILE})


if __name__ == "__main__":
    config.AUTH_MODE = "dev"
    config.DATABASE_URL = ""
    app.dependency_overrides[get_llm] = lambda: ScriptedAI()
    print("Fake-AI Planly API on http://localhost:8000 (dev mode, in-memory, no login)")
    uvicorn.run(app, host="127.0.0.1", port=8000)
