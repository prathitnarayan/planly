"""
Goal interview + blueprint generation.

The code, not the LLM, enforces the question limit and the "done" state —
the LLM only proposes. Same idea as the planner: trust code for rules.
"""

from __future__ import annotations

import json
import re
from datetime import date

from pydantic import BaseModel, Field

from app.ai.llm import LLMClient, Message, generate_validated
from app.ai.prompts import BLUEPRINT_SYSTEM, CORRECTION_SYSTEM, GOAL_CHECK_SYSTEM, INTERVIEW_SYSTEM
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import GoalCheck, GoalProfile, InterviewTurn


class InterviewState(BaseModel):
    goal: str
    messages: list[Message] = Field(default_factory=list)  # the chat so far
    profile: GoalProfile = Field(default_factory=GoalProfile)
    questions_asked: int = 0
    deadline_forced: bool = False   # the code already asked the deadline question
    benchmark_forced: bool = False  # the code already asked the benchmark question
    done: bool = False

    @property
    def current_question(self) -> str | None:
        if self.done or not self.messages or self.messages[-1]["role"] != "assistant":
            return None
        return self.messages[-1]["content"]


DEADLINE_QUESTION = "Is there a deadline for this? Give a date, or say 'no deadline'."


BENCHMARK_QUESTION = (
    "Have you done something similar before? Roughly how many hours did it take "
    "(a breakdown helps, e.g. 18 notebook + 4 video)? Or say 'no'."
)


def _benchmark_missing(state: "InterviewState") -> bool:
    p = state.profile
    return not p.benchmarks and p.benchmark_status != "none" and not state.benchmark_forced


def _deadline_missing(state: "InterviewState") -> bool:
    p = state.profile
    return p.deadline is None and p.deadline_status != "none" and not state.deadline_forced


def _next_turn(llm: LLMClient, state: InterviewState, max_questions: int, today: date) -> InterviewState:
    system = INTERVIEW_SYSTEM.format(
        today=today.isoformat(), asked=state.questions_asked, max_q=max_questions
    )
    turn = generate_validated(llm, InterviewTurn, system, state.messages)
    state.profile = turn.profile

    wants_to_stop = turn.done or state.questions_asked >= max_questions
    # The plan depends on these two. Ask each once, in code — don't trust the model to remember.
    if wants_to_stop and _deadline_missing(state):
        state.messages.append({"role": "assistant", "content": DEADLINE_QUESTION})
        state.questions_asked += 1
        state.deadline_forced = True
    elif wants_to_stop and _benchmark_missing(state):
        state.messages.append({"role": "assistant", "content": BENCHMARK_QUESTION})
        state.questions_asked += 1
        state.benchmark_forced = True
    elif wants_to_stop:
        state.done = True
    else:
        state.messages.append({"role": "assistant", "content": turn.question})
        state.questions_asked += 1
    return state


def check_goal(llm: LLMClient, text: str) -> GoalCheck:
    """
    Real runs, 27-28 Sep: people type their STATUS where the goal goes ("Assignment 1
    is done, I know pandas..."), and the interview then asks about Assignment 1.
    Catch it before the interview starts.
    """
    return generate_validated(llm, GoalCheck, GOAL_CHECK_SYSTEM, [{"role": "user", "content": text}])


def start_interview(
    llm: LLMClient,
    goal: str,
    max_questions: int,
    today: date | None = None,
    background: str | None = None,
) -> InterviewState:
    messages = [{"role": "user", "content": f"My goal: {goal}"}]
    if background:
        messages[0]["content"] += f"\nWhere I'm starting from: {background}"
    state = InterviewState(goal=goal, messages=messages)
    return _next_turn(llm, state, max_questions, today or date.today())


def apply_correction(
    llm: LLMClient, state: InterviewState, correction: str, today: date | None = None
) -> InterviewState:
    """
    After 'What I understood', the user can fix it before the blueprint is built.
    Real run, 28 Sep: answers pasted into the wrong questions made peer reviews
    'optional' — a confirm step would have caught it.
    """
    if not state.done:
        raise ValueError("finish the interview first")
    if not correction.strip():
        raise ValueError("correction is empty")
    summary = state.profile.model_dump_json()
    messages = state.messages + [
        {"role": "assistant", "content": f"Here's what I understood: {summary}"},
        {"role": "user", "content": f"Correction: {correction}"},
    ]
    system = CORRECTION_SYSTEM.format(
        today=(today or date.today()).isoformat(), asked=state.questions_asked, max_q=state.questions_asked
    )
    turn = generate_validated(llm, InterviewTurn, system, messages)
    fixed = state.model_copy(deep=True)
    fixed.profile = turn.profile
    fixed.messages = messages      # keep the correction in the transcript the blueprint sees
    return fixed


def answer_question(
    llm: LLMClient, state: InterviewState, answer: str, max_questions: int, today: date | None = None
) -> InterviewState:
    if state.done:
        raise ValueError("interview is already finished")
    if not answer.strip():
        raise ValueError("answer is empty")
    state.messages.append({"role": "user", "content": answer})
    return _next_turn(llm, state, max_questions, today or date.today())


# The prompt says "never ask about schedule/hours" and the model does it anyway. Filter in code.
_SCHEDULE_QUESTION = re.compile(
    r"\b(hours?|per week|weekly|daily|schedule|availability|available|dedicate|free time)\b", re.I
)


def drop_schedule_questions(bp: GoalBlueprint) -> GoalBlueprint:
    kept = [q for q in bp.open_questions if not _SCHEDULE_QUESTION.search(q)]
    return bp.model_copy(update={"open_questions": kept})


def transcript(messages: list[Message]) -> list[dict[str, str]]:
    """The interview as question/answer pairs, in the user's exact words."""
    pairs, question = [], None
    for msg in messages:
        if msg["role"] == "assistant":
            question = msg["content"]
        elif question is not None:
            pairs.append({"question": question, "answer": msg["content"]})
            question = None
    return pairs


def generate_blueprint(
    llm: LLMClient, goal: str, profile: GoalProfile, messages: list[Message] | None = None,
    course_load: list[dict] | None = None,
) -> GoalBlueprint:
    brief = {
        "goal": goal,
        "what_we_know": profile.model_dump(mode="json"),
        "interview_transcript": transcript(messages or []),
    }
    if course_load:
        brief["course_load"] = course_load
    messages: list[Message] = [
        {"role": "user", "content": "Build the blueprint as JSON for:\n" + json.dumps(brief, indent=2)}
    ]
    known = {k.key for k in profile.key_dates}
    bp = generate_validated(
        llm, GoalBlueprint, BLUEPRINT_SYSTEM, messages,
        context={"key_dates": known, "benchmarks": set(profile.benchmark_map())},
    )
    return drop_schedule_questions(bp)
