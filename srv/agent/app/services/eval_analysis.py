"""
Eval failure analysis service.

Analyzes failed scenarios in an eval run and uses LLM to generate
concrete improvement suggestions for agent prompts and configuration.
"""

import json
import logging
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import EvalRun, EvalScore, EvalScenario

logger = logging.getLogger(__name__)


async def analyze_failures(
    session: AsyncSession,
    eval_run_id: uuid.UUID,
) -> Dict[str, Any]:
    """
    Group failed scenarios by failure mode and generate LLM improvement suggestions.

    Failure modes:
    - wrong_agent: routing_accuracy scorer failed
    - wrong_tools: tool_selection scorer failed
    - poor_quality: llm_quality scorer failed
    - no_response: success scorer failed

    Returns structured suggestions for prompt/config changes.
    """
    # Load all failed scores for this run
    result = await session.execute(
        select(EvalScore)
        .where(
            and_(
                EvalScore.eval_run_id == eval_run_id,
                EvalScore.passed == False,  # noqa: E712
            )
        )
        .order_by(EvalScore.scenario_id, EvalScore.scorer_name)
    )
    failed_scores = result.scalars().all()

    if not failed_scores:
        return {
            "eval_run_id": str(eval_run_id),
            "total_failures": 0,
            "failure_modes": {},
            "suggestions": [],
            "message": "No failures found — all scenarios passed!",
        }

    # Group failures by mode
    failure_modes: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    scenario_ids = {s.scenario_id for s in failed_scores if s.scenario_id}

    # Load scenario queries for context
    scenarios_by_id: Dict[uuid.UUID, EvalScenario] = {}
    if scenario_ids:
        scenarios_result = await session.execute(
            select(EvalScenario).where(EvalScenario.id.in_(scenario_ids))
        )
        for s in scenarios_result.scalars().all():
            scenarios_by_id[s.id] = s

    for score in failed_scores:
        scenario = scenarios_by_id.get(score.scenario_id) if score.scenario_id else None
        entry = {
            "scenario_id": str(score.scenario_id) if score.scenario_id else None,
            "scenario_name": scenario.name if scenario else "unknown",
            "query": scenario.query[:200] if scenario else "",
            "scorer": score.scorer_name,
            "score": score.score,
            "details": score.details,
        }

        if score.scorer_name == "routing_accuracy":
            failure_modes["wrong_agent"].append(entry)
        elif score.scorer_name == "tool_selection":
            failure_modes["wrong_tools"].append(entry)
        elif score.scorer_name == "llm_quality":
            failure_modes["poor_quality"].append(entry)
        elif score.scorer_name == "success":
            failure_modes["no_response"].append(entry)
        else:
            failure_modes["other"].append(entry)

    # Generate LLM suggestions
    suggestions = await _generate_suggestions(failure_modes)

    return {
        "eval_run_id": str(eval_run_id),
        "total_failures": len(failed_scores),
        "failure_modes": {
            mode: {
                "count": len(items),
                "examples": items[:3],  # Show up to 3 examples
            }
            for mode, items in failure_modes.items()
        },
        "suggestions": suggestions,
    }


async def _generate_suggestions(
    failure_modes: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, str]]:
    """
    Use fast LLM to analyze failure patterns and suggest improvements.
    """
    if not failure_modes:
        return []

    try:
        import litellm

        # Build a summary of failures for the LLM
        failure_summary = []
        for mode, items in failure_modes.items():
            example_queries = [item["query"] for item in items[:3]]
            failure_summary.append(
                f"- {mode}: {len(items)} failures. "
                f"Example queries: {'; '.join(example_queries)}"
            )

        prompt = f"""You are an AI agent optimization expert analyzing evaluation failures.

FAILURE SUMMARY:
{chr(10).join(failure_summary)}

Based on these failure patterns, suggest 3-5 concrete improvements. Each suggestion should be:
- Specific and actionable (not generic advice)
- Targeted at the right component (system prompt, routing config, tool list, etc.)
- Ranked by expected impact

Respond with ONLY valid JSON (array of suggestion objects):
[
  {{
    "category": "system_prompt | routing | tools | model | other",
    "priority": "high | medium | low",
    "suggestion": "Specific change to make",
    "rationale": "Why this will fix the observed failures",
    "example_change": "Optional: before/after example"
  }}
]"""

        resp = await litellm.acompletion(
            model="fast",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        suggestions = json.loads(raw)
        return suggestions if isinstance(suggestions, list) else []

    except Exception as exc:
        logger.warning(f"Failed to generate improvement suggestions: {exc}")
        return _heuristic_suggestions(failure_modes)


def _heuristic_suggestions(
    failure_modes: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, str]]:
    """Fallback heuristic suggestions when LLM is unavailable."""
    suggestions = []

    if "wrong_agent" in failure_modes:
        suggestions.append({
            "category": "routing",
            "priority": "high",
            "suggestion": "Review agent routing keywords and descriptions",
            "rationale": f"{len(failure_modes['wrong_agent'])} queries routed to wrong agent. "
                        "Update AGENT_DESCRIPTIONS in agentic_dispatcher.py.",
            "example_change": "",
        })

    if "wrong_tools" in failure_modes:
        suggestions.append({
            "category": "tools",
            "priority": "high",
            "suggestion": "Update planner prompt to improve tool selection",
            "rationale": f"{len(failure_modes['wrong_tools'])} scenarios used wrong tools. "
                        "Review planning LLM instructions in chat_agent.py.",
            "example_change": "",
        })

    if "poor_quality" in failure_modes:
        suggestions.append({
            "category": "system_prompt",
            "priority": "medium",
            "suggestion": "Enhance system prompt with more specific instructions",
            "rationale": f"{len(failure_modes['poor_quality'])} responses rated low quality. "
                        "Add explicit output format requirements and examples.",
            "example_change": "",
        })

    if "no_response" in failure_modes:
        suggestions.append({
            "category": "model",
            "priority": "high",
            "suggestion": "Investigate and fix scenarios causing no response",
            "rationale": f"{len(failure_modes['no_response'])} scenarios produced no response. "
                        "Check for tool errors or context length issues.",
            "example_change": "",
        })

    return suggestions
