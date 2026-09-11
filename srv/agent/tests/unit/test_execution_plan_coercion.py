"""ExecutionPlan must accept the loosely-typed JSON the fast planner model emits.

Regression for QA finding #6: production logs showed every plan rejected with
  steps.0.id: Input should be a valid string [input_value=1, input_type=int]
  estimated_duration: Input should be a valid string [input_value=5, input_type=int]
which sent every query to the generic fallback plan.
"""

import pytest

from app.agents.chat_agent import ExecutionPlan, PlanStep


PROD_PAYLOAD_2026_09_08 = {
    "summary": "Search documents and the web, then synthesize.",
    "steps": [
        {"id": 1, "tool": "document_search", "objective": "Find internal context", "run_mode": "parallel",
         "args": {"query": "latest dredging news"}},
        {"id": 2, "tool": "web_search", "objective": "Find recent news", "run_mode": "parallel",
         "args": {"query": "dredging news September 2026"}},
    ],
    "parallel_groups": [[1, 2]],
    "feedback_points": [{"after_step_id": 1, "message": "Checked internal docs"}],
    "estimated_duration": 5,
}


def test_production_payload_validates():
    plan = ExecutionPlan.model_validate(PROD_PAYLOAD_2026_09_08)
    assert [s.id for s in plan.steps] == ["1", "2"]
    assert [s.tool for s in plan.steps] == ["document_search", "web_search"]
    assert plan.parallel_groups == [["1", "2"]]
    assert plan.feedback_points[0].after_step_id == "1"
    assert plan.estimated_duration == "quick"


@pytest.mark.parametrize("raw,expected", [
    (5, "quick"), (30, "moderate"), (600, "long"),
    ("quick", "quick"), ("Moderate", "moderate"), ("a few seconds", "quick"),
    ("takes a while", "moderate"), ("about an hour", "long"), (None, "quick"),
])
def test_duration_coercion(raw, expected):
    plan = ExecutionPlan.model_validate({"summary": "x", "estimated_duration": raw})
    assert plan.estimated_duration == expected


def test_flat_parallel_group_is_wrapped():
    plan = ExecutionPlan.model_validate({"summary": "x", "parallel_groups": [1, 2]})
    assert plan.parallel_groups == [["1", "2"]]


def test_none_collections_become_empty():
    plan = ExecutionPlan.model_validate(
        {"summary": None, "steps": None, "parallel_groups": None, "feedback_points": None}
    )
    assert plan.summary == ""
    assert plan.steps == [] and plan.parallel_groups == [] and plan.feedback_points == []


def test_step_args_none_becomes_dict():
    step = PlanStep.model_validate({"id": 3, "tool": "web_search", "objective": "o", "args": None})
    assert step.id == "3" and step.args == {}


def test_string_ids_untouched():
    plan = ExecutionPlan.model_validate(
        {"summary": "x", "steps": [{"id": "step_1", "tool": "t", "objective": "o"}]}
    )
    assert plan.steps[0].id == "step_1"
