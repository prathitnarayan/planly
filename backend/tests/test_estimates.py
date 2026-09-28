"""Benchmark check, per-deliverable edits, correction logging, hour parsing."""

import json

import pytest
from pydantic import ValidationError

from app.core.estimate_log import log_correction
from app.planners.estimate_check import check_estimates
from app.schemas.blueprint import GoalBlueprint
from app.schemas.interview import Benchmark
from scripts.try_goal import parse_hours


def ms(key, hours, deliverable, **kw):
    return {"key": key, "name": key, "hours_by_kind": {"learn": hours / 2, "practice": hours / 2},
            "confidence": 0.7, "done_criteria": ["done"], "deliverable": deliverable, **kw}


def real_run_blueprint(anchors=None):
    """Shape of the real 27 Sep run: A2 = 56h, A3 = 44h, A1 benchmark = 25h."""
    return GoalBlueprint.model_validate({
        "goal": "Kaggle 2 and 3", "category": "learning", "summary": "x",
        "milestones": [
            ms("a2_base", 18, "assignment_2"),
            ms("a2_tune", 25, "assignment_2", depends_on=["a2_base"]),
            ms("a2_video", 8, "assignment_2", depends_on=["a2_tune"]),
            ms("a2_review", 5, "assignment_2", depends_on=["a2_video"]),
            ms("a3_base", 14, "assignment_3"),
            ms("a3_tune", 17, "assignment_3", depends_on=["a3_base"]),
            ms("a3_video", 8, "assignment_3", depends_on=["a3_tune"]),
            ms("a3_review", 5, "assignment_3", depends_on=["a3_video"]),
        ],
        "anchors": anchors if anchors is not None else {"assignment_2": "a1", "assignment_3": "a1"},
    })


A1 = Benchmark(key="a1", what="Assignment 1", hours=99,
               parts={"notebook": 18, "video": 4, "peer_review": 3})


# ---------- benchmark ----------

def test_benchmark_total_comes_from_parts_not_the_model():
    assert A1.hours == 25   # model said 99, parts say 25


# ---------- deliverables ----------

def test_deliverables_grouped_in_order():
    groups = real_run_blueprint().deliverables()
    assert list(groups) == ["assignment_2", "assignment_3"]
    assert [m.key for m in groups["assignment_2"]] == ["a2_base", "a2_tune", "a2_video", "a2_review"]


def test_windowed_milestones_listed_last_in_their_group():
    data = real_run_blueprint().model_dump(mode="json")
    data["milestones"][3].update(depends_on=[], start_after="a2_due")   # a2_review sorts first alphabetically
    groups = GoalBlueprint.model_validate(data).deliverables()
    assert [m.key for m in groups["assignment_2"]][-1] == "a2_review"


def test_untagged_milestones_stand_alone():
    data = real_run_blueprint().model_dump(mode="json")
    data["milestones"][0]["deliverable"] = None
    assert "a2_base" in GoalBlueprint.model_validate(data).deliverables()


def test_scale_whole_deliverable_keeps_proportions():
    bp = real_run_blueprint().with_deliverable_hours("assignment_2", 28)   # AI said 56
    a2 = bp.deliverables()["assignment_2"]
    assert sum(m.estimated_hours for m in a2) == pytest.approx(28)
    assert [m.estimated_hours for m in a2] == pytest.approx([9, 12.5, 4, 2.5])  # all halved
    assert sum(m.estimated_hours for m in bp.deliverables()["assignment_3"]) == 44  # untouched


def test_unknown_deliverable_rejected():
    with pytest.raises(ValueError, match="no deliverable"):
        real_run_blueprint().with_deliverable_hours("assignment_9", 10)


def test_anchor_must_point_at_real_deliverable():
    with pytest.raises(ValidationError, match="unknown deliverable"):
        real_run_blueprint(anchors={"assignment_9": "a1"})


def test_anchor_must_point_at_real_benchmark_when_known():
    data = real_run_blueprint().model_dump(mode="json")
    with pytest.raises(ValidationError, match="unknown benchmark"):
        GoalBlueprint.model_validate(data, context={"benchmarks": {"something_else"}})


# ---------- the check ----------

def test_real_run_is_flagged():
    warnings = check_estimates(real_run_blueprint(), {"a1": A1})
    by = {w.deliverable: w for w in warnings}
    assert by["assignment_2"].ai_hours == 56
    assert by["assignment_2"].ratio == 2.24
    assert by["assignment_2"].direction == "high"
    assert by["assignment_2"].suggested_hours == 25
    assert "took you 25h (notebook 18, video 4, peer_review 3)" in by["assignment_2"].message
    assert by["assignment_3"].ratio == 1.76


def test_reasonable_estimate_not_flagged():
    bp = real_run_blueprint().with_deliverable_hours("assignment_2", 30)   # 1.2x
    flagged = {w.deliverable for w in check_estimates(bp, {"a1": A1})}
    assert "assignment_2" not in flagged


def test_suspiciously_low_is_flagged_too():
    bp = real_run_blueprint().with_deliverable_hours("assignment_3", 10)   # 0.4x
    w = next(w for w in check_estimates(bp, {"a1": A1}) if w.deliverable == "assignment_3")
    assert w.direction == "low"


def test_no_anchors_no_warnings():
    assert check_estimates(real_run_blueprint(anchors={}), {"a1": A1}) == []


# ---------- logging ----------

def test_same_number_is_not_logged(tmp_path):
    log = tmp_path / "c.jsonl"
    assert log_correction("g", "k", "n", 24, 24, 0.7, True, path=log) is None
    assert not log.exists()
    log_correction("g", "k", "n", 24, 12, 0.7, True, path=log)
    assert json.loads(log.read_text())["ratio"] == 0.5


# ---------- input parsing ----------

@pytest.mark.parametrize("text, hours", [("27", 27), ("27h", 27), ("10 hrs", 10), ("3 hours", 3), ("4.5", 4.5)])
def test_parse_hours(text, hours):
    assert parse_hours(text) == hours


@pytest.mark.parametrize("text", ["abc", "0", "-2", "5 days", ""])
def test_parse_hours_rejects(text):
    with pytest.raises(ValueError):
        parse_hours(text)


# ---------- part-aware scaling ----------

def parts_blueprint():
    """A2 from the real 28 Sep run: notebook 35h inflated, video 4 and reviews 3 already right."""
    return GoalBlueprint.model_validate({
        "goal": "x", "category": "learning", "summary": "x",
        "milestones": [
            ms("a2_base", 18, "assignment_2", benchmark_part="notebook"),
            ms("a2_tune", 17, "assignment_2", depends_on=["a2_base"], benchmark_part="notebook"),
            ms("a2_video", 4, "assignment_2", depends_on=["a2_tune"], benchmark_part="video"),
            ms("a2_review", 3, "assignment_2", benchmark_part="peer_review"),
        ],
        "anchors": {"assignment_2": "a1"},
    })


def test_parts_that_match_the_benchmark_stay_fixed():
    bp = parts_blueprint()
    parts = bp.anchor_parts("assignment_2", {"a1": A1})
    # a2_base alone (18h) equals the notebook part (18h) — but the notebook is base+tune = 35h
    assert set(bp.locked_milestones("assignment_2", parts)) == {"a2_video", "a2_review"}
    scaled = bp.with_deliverable_hours("assignment_2", 30, parts)
    hours = {m.key: m.estimated_hours for m in scaled.milestones}
    assert hours["a2_video"] == 4 and hours["a2_review"] == 3      # untouched
    assert hours["a2_base"] + hours["a2_tune"] == pytest.approx(23)  # notebook absorbs it all


def test_without_parts_everything_scales_together():
    scaled = parts_blueprint().with_deliverable_hours("assignment_2", 21)   # 42 -> 21
    assert {m.key: m.estimated_hours for m in scaled.milestones}["a2_video"] == pytest.approx(2)


def test_locked_parts_bigger_than_total_falls_back_to_proportional():
    bp = parts_blueprint()
    parts = bp.anchor_parts("assignment_2", {"a1": A1})
    scaled = bp.with_deliverable_hours("assignment_2", 6, parts)   # less than video+review (7)
    assert sum(m.estimated_hours for m in scaled.milestones) == pytest.approx(6)


# ---------- open questions ----------

def test_schedule_questions_filtered_in_code():
    from app.ai.goal_intake import drop_schedule_questions
    bp = parts_blueprint().model_copy(update={"open_questions": [
        "What is the approximate expected workload or hours you can dedicate weekly?",
        "Are there specific datasets for Assignment 3?",
        "What's your availability on weekends?",
    ]})
    assert drop_schedule_questions(bp).open_questions == ["Are there specific datasets for Assignment 3?"]


def all_match_blueprint():
    """Real run 28 Sep 01:06: notebook 18, video 4, reviews 3 — all equal to the benchmark."""
    return GoalBlueprint.model_validate({
        "goal": "x", "category": "learning", "summary": "x",
        "milestones": [
            ms("a2_notebook", 18, "assignment_2", benchmark_part="notebook"),
            ms("a2_video", 4, "assignment_2", depends_on=["a2_notebook"], benchmark_part="video"),
            ms("a2_review", 3, "assignment_2", benchmark_part="peer_review"),
        ],
        "anchors": {"assignment_2": "a1"},
    })


def test_all_parts_match_extra_goes_to_main_work():
    bp = all_match_blueprint()
    parts = bp.anchor_parts("assignment_2", {"a1": A1})
    scaled = bp.with_deliverable_hours("assignment_2", 30, parts)
    hours = {m.key: m.estimated_hours for m in scaled.milestones}
    assert hours == pytest.approx({"a2_notebook": 23, "a2_video": 4, "a2_review": 3})


def test_kept_message_matches_what_happens():
    bp = all_match_blueprint()
    parts = bp.anchor_parts("assignment_2", {"a1": A1})
    kept = bp.locked_milestones("assignment_2", parts, 30)
    assert set(kept) == {"a2_video", "a2_review"}             # NOT the notebook
    scaled = bp.with_deliverable_hours("assignment_2", 30, parts)
    unchanged = {m.name for m, o in zip(scaled.milestones, bp.milestones)
                 if m.estimated_hours == o.estimated_hours}
    assert unchanged == set(kept)


@pytest.mark.parametrize("name, deliverable, expected", [
    ("Assignment 2: Video walkthrough", "assignment_2", "Video walkthrough"),
    ("Assignment 2 Notebook: Baseline and Improved Models", "assignment_2", "Notebook: Baseline and Improved Models"),
    ("A2 peer reviews", "assignment_2", "Peer reviews"),
    ("Learn XGBoost", "assignment_2", "Learn XGBoost"),
    ("Assignment 3 Video", "assignment_2", "Assignment 3 Video"),   # different number: keep
    ("Anything", None, "Anything"),
])
def test_short_names(name, deliverable, expected):
    from scripts.try_goal import short
    assert short(name, deliverable) == expected
