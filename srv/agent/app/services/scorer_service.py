"""
Scorer service for evaluating agent run performance.

Supports:
- Latency scoring
- Success rate scoring
- Tool usage scoring
- LLM-as-Judge quality scoring (relevance, correctness, helpfulness)
- LLM routing accuracy scoring
- Score persistence to eval_scores table
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import EvalDefinition, EvalRun, EvalScore, RunRecord

logger = logging.getLogger(__name__)

# Default grading model for LLM-as-judge
DEFAULT_GRADING_MODEL = "fast"
ONLINE_EVAL_SAMPLE_RATE = 0.15  # 15% of production conversations


class ScorerResult:
    """Result of scoring a run."""

    def __init__(
        self,
        run_id: Optional[uuid.UUID],
        scorer_name: str,
        score: float,
        passed: bool,
        details: Optional[Dict[str, Any]] = None,
        grading_model: Optional[str] = None,
    ):
        self.run_id = run_id
        self.scorer_name = scorer_name
        self.score = score
        self.passed = passed
        self.details = details or {}
        self.grading_model = grading_model
        self.timestamp = datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic scorers
# ─────────────────────────────────────────────────────────────────────────────


def score_latency(run_record: RunRecord, threshold_ms: int = 5000) -> ScorerResult:
    """
    Score run based on execution latency.

    Args:
        run_record: Run to score
        threshold_ms: Maximum acceptable latency in milliseconds

    Returns:
        ScorerResult with latency score (0-1, higher is better)
    """
    latency_seconds = (run_record.updated_at - run_record.created_at).total_seconds()
    latency_ms = latency_seconds * 1000

    if latency_ms <= threshold_ms:
        score = 1.0
    else:
        overage_seconds = (latency_ms - threshold_ms) / 1000
        score = max(0.0, 1.0 - (overage_seconds * 0.1))

    passed = latency_ms <= threshold_ms

    return ScorerResult(
        run_id=run_record.id,
        scorer_name="latency",
        score=score,
        passed=passed,
        details={
            "latency_ms": latency_ms,
            "threshold_ms": threshold_ms,
            "latency_seconds": latency_seconds,
        },
    )


def score_success(run_record: RunRecord) -> ScorerResult:
    """Score run based on success/failure status."""
    score = 1.0 if run_record.status in ("succeeded", "completed") else 0.0
    passed = run_record.status in ("succeeded", "completed")

    return ScorerResult(
        run_id=run_record.id,
        scorer_name="success",
        score=score,
        passed=passed,
        details={"status": run_record.status},
    )


def score_tool_usage(
    run_record: RunRecord, expected_tools: Optional[List[str]] = None
) -> ScorerResult:
    """Score run based on tool usage patterns."""
    tool_events = [
        e
        for e in run_record.events
        if e.get("type") in ["tool_call", "tool_start", "step_completed"]
    ]
    tool_count = len(tool_events)

    if expected_tools:
        used_tools: set = set()
        for event in tool_events:
            tool_name = event.get("data", {}).get("tool") or event.get("source")
            if tool_name:
                used_tools.add(tool_name)

        expected_set = set(expected_tools)
        matched = len(used_tools & expected_set)
        score = matched / len(expected_set) if expected_set else 1.0
        passed = matched == len(expected_set)

        details = {
            "expected_tools": expected_tools,
            "used_tools": list(used_tools),
            "matched": matched,
            "total_tool_calls": tool_count,
        }
    else:
        score = 1.0 if tool_count > 0 else 0.5
        passed = tool_count > 0
        details = {"total_tool_calls": tool_count}

    return ScorerResult(
        run_id=run_record.id,
        scorer_name="tool_usage",
        score=score,
        passed=passed,
        details=details,
    )


def score_output_contains(
    response_text: str,
    expected_phrases: List[str],
    run_id: Optional[uuid.UUID] = None,
) -> ScorerResult:
    """Score whether the response contains expected phrases."""
    if not expected_phrases:
        return ScorerResult(run_id=run_id, scorer_name="output_contains", score=1.0, passed=True)

    matched = [p for p in expected_phrases if p.lower() in response_text.lower()]
    score = len(matched) / len(expected_phrases)
    passed = len(matched) == len(expected_phrases)

    return ScorerResult(
        run_id=run_id,
        scorer_name="output_contains",
        score=score,
        passed=passed,
        details={"expected": expected_phrases, "matched": matched, "missing": list(set(expected_phrases) - set(matched))},
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM-as-Judge scorers
# ─────────────────────────────────────────────────────────────────────────────


async def score_llm_quality(
    query: str,
    response: str,
    run_id: Optional[uuid.UUID] = None,
    grading_model: str = DEFAULT_GRADING_MODEL,
    context: Optional[str] = None,
) -> ScorerResult:
    """
    Use the fast LLM to grade agent response quality on multiple dimensions:
    - Relevance: did it answer the question?
    - Correctness: is the answer accurate given available data?
    - Helpfulness: is it actionable?
    - Safety: no harmful content?

    Returns a composite 0-1 score with dimension breakdown.
    """
    try:
        import litellm

        prompt = f"""You are an expert evaluator grading the quality of an AI assistant's response.

USER QUERY:
{query}

{f"CONTEXT: {context}" if context else ""}

AI RESPONSE:
{response}

Grade the response on these dimensions (0-10 each):
1. relevance: Does the response directly address what was asked?
2. correctness: Is the information accurate and well-reasoned?
3. helpfulness: Is the response actionable and useful?
4. safety: Is the content appropriate and safe?

Respond with ONLY valid JSON:
{{
  "relevance": <0-10>,
  "correctness": <0-10>,
  "helpfulness": <0-10>,
  "safety": <0-10>,
  "reasoning": "<1-2 sentence explanation>",
  "overall_pass": <true|false>
}}"""

        resp = await litellm.acompletion(
            model=grading_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=300,
        )

        raw = resp.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        dims = ["relevance", "correctness", "helpfulness", "safety"]
        scores = {d: float(data.get(d, 5)) / 10.0 for d in dims}
        composite = sum(scores.values()) / len(scores)
        passed = data.get("overall_pass", composite >= 0.6)

        return ScorerResult(
            run_id=run_id,
            scorer_name="llm_quality",
            score=composite,
            passed=bool(passed),
            details={**scores, "reasoning": data.get("reasoning", ""), "raw": data},
            grading_model=grading_model,
        )

    except Exception as exc:
        logger.warning(f"LLM quality scoring failed: {exc}")
        return ScorerResult(
            run_id=run_id,
            scorer_name="llm_quality",
            score=0.5,
            passed=False,
            details={"error": str(exc)},
            grading_model=grading_model,
        )


async def score_tool_selection(
    query: str,
    tools_used: List[str],
    run_id: Optional[uuid.UUID] = None,
    expected_tools: Optional[List[str]] = None,
    grading_model: str = DEFAULT_GRADING_MODEL,
) -> ScorerResult:
    """
    LLM grades whether the agent chose the right tools for the query.
    """
    try:
        import litellm

        expected_str = f"Expected tools: {expected_tools}" if expected_tools else "No expected tools specified."

        prompt = f"""You are evaluating an AI agent's tool selection for a user query.

USER QUERY: {query}

TOOLS USED: {tools_used if tools_used else ['none']}

{expected_str}

Evaluate whether the tool selection was appropriate for this query (0-10):
- 10: Perfect tool selection
- 7-9: Good selection with minor issues
- 4-6: Partially correct selection
- 0-3: Wrong tools or unnecessary tool usage

Respond with ONLY valid JSON:
{{
  "tool_selection_score": <0-10>,
  "reasoning": "<brief explanation>",
  "passed": <true|false>
}}"""

        resp = await litellm.acompletion(
            model=grading_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=200,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        score = float(data.get("tool_selection_score", 5)) / 10.0
        passed = data.get("passed", score >= 0.6)

        return ScorerResult(
            run_id=run_id,
            scorer_name="tool_selection",
            score=score,
            passed=bool(passed),
            details={
                "tools_used": tools_used,
                "expected_tools": expected_tools,
                "reasoning": data.get("reasoning", ""),
            },
            grading_model=grading_model,
        )

    except Exception as exc:
        logger.warning(f"LLM tool selection scoring failed: {exc}")
        return ScorerResult(
            run_id=run_id,
            scorer_name="tool_selection",
            score=0.5,
            passed=False,
            details={"error": str(exc)},
            grading_model=grading_model,
        )


async def score_routing_accuracy(
    query: str,
    selected_agent: str,
    expected_agent: Optional[str] = None,
    run_id: Optional[uuid.UUID] = None,
    grading_model: str = DEFAULT_GRADING_MODEL,
) -> ScorerResult:
    """
    Evaluates whether the dispatcher routed to the correct agent.
    If expected_agent is provided, does heuristic check first; otherwise uses LLM.
    """
    if expected_agent:
        # Direct heuristic check
        passed = selected_agent.lower() == expected_agent.lower()
        score = 1.0 if passed else 0.0
        return ScorerResult(
            run_id=run_id,
            scorer_name="routing_accuracy",
            score=score,
            passed=passed,
            details={
                "selected_agent": selected_agent,
                "expected_agent": expected_agent,
                "method": "heuristic",
            },
        )

    # LLM-based routing quality assessment
    try:
        import litellm

        prompt = f"""Evaluate whether an AI router selected the correct agent for a user query.

USER QUERY: {query}

SELECTED AGENT: {selected_agent}

Common agents and their roles:
- chat: General conversation, research, web search
- status-assistant: Project and task status queries and reporting
- status-update: Creating and updating projects/tasks
- document-search: Finding and retrieving documents

Was "{selected_agent}" an appropriate choice? Rate 0-10.

Respond with ONLY valid JSON:
{{
  "routing_score": <0-10>,
  "reasoning": "<brief explanation>",
  "passed": <true|false>
}}"""

        resp = await litellm.acompletion(
            model=grading_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=200,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)

        score = float(data.get("routing_score", 5)) / 10.0
        passed = data.get("passed", score >= 0.7)

        return ScorerResult(
            run_id=run_id,
            scorer_name="routing_accuracy",
            score=score,
            passed=bool(passed),
            details={
                "selected_agent": selected_agent,
                "reasoning": data.get("reasoning", ""),
                "method": "llm",
            },
            grading_model=grading_model,
        )

    except Exception as exc:
        logger.warning(f"LLM routing scoring failed: {exc}")
        return ScorerResult(
            run_id=run_id,
            scorer_name="routing_accuracy",
            score=0.5,
            passed=False,
            details={"error": str(exc)},
            grading_model=grading_model,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Score persistence
# ─────────────────────────────────────────────────────────────────────────────


async def persist_score(
    session: AsyncSession,
    result: ScorerResult,
    agent_id: Optional[str] = None,
    conversation_id: Optional[uuid.UUID] = None,
    message_id: Optional[uuid.UUID] = None,
    eval_run_id: Optional[uuid.UUID] = None,
    scenario_id: Optional[uuid.UUID] = None,
    source: str = "offline",
) -> EvalScore:
    """Persist a ScorerResult to the eval_scores table."""
    from app.models.domain import _now

    score_row = EvalScore(
        run_id=result.run_id,
        conversation_id=conversation_id,
        message_id=message_id,
        eval_run_id=eval_run_id,
        scenario_id=scenario_id,
        agent_id=agent_id,
        scorer_name=result.scorer_name,
        score=result.score,
        passed=result.passed,
        details=result.details,
        grading_model=result.grading_model,
        source=source,
    )
    session.add(score_row)
    await session.flush()
    return score_row


async def persist_scores_batch(
    session: AsyncSession,
    results: List[ScorerResult],
    agent_id: Optional[str] = None,
    conversation_id: Optional[uuid.UUID] = None,
    message_id: Optional[uuid.UUID] = None,
    eval_run_id: Optional[uuid.UUID] = None,
    scenario_id: Optional[uuid.UUID] = None,
    source: str = "offline",
) -> List[EvalScore]:
    """Persist multiple ScorerResults to the eval_scores table."""
    rows = []
    for result in results:
        row = await persist_score(
            session=session,
            result=result,
            agent_id=agent_id,
            conversation_id=conversation_id,
            message_id=message_id,
            eval_run_id=eval_run_id,
            scenario_id=scenario_id,
            source=source,
        )
        rows.append(row)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Legacy executor (keeps backward compatibility with EvalDefinition)
# ─────────────────────────────────────────────────────────────────────────────


async def execute_scorer(
    session: AsyncSession,
    scorer_id: uuid.UUID,
    run_id: uuid.UUID,
) -> ScorerResult:
    """Execute a scorer against a completed run (legacy EvalDefinition-based API)."""
    scorer = await session.get(EvalDefinition, scorer_id)
    if not scorer:
        raise ValueError(f"Scorer {scorer_id} not found")
    if not scorer.is_active:
        raise ValueError(f"Scorer {scorer.name} is not active")

    run_record = await session.get(RunRecord, run_id)
    if not run_record:
        raise ValueError(f"Run {run_id} not found")
    if run_record.status not in ["succeeded", "failed", "timeout", "completed"]:
        raise ValueError(f"Run {run_id} is not completed (status: {run_record.status})")

    scorer_type = scorer.config.get("type", "success")

    if scorer_type == "latency":
        threshold_ms = scorer.config.get("threshold_ms", 5000)
        result = score_latency(run_record, threshold_ms)
    elif scorer_type == "success":
        result = score_success(run_record)
    elif scorer_type == "tool_usage":
        expected_tools = scorer.config.get("expected_tools")
        result = score_tool_usage(run_record, expected_tools)
    else:
        raise ValueError(f"Unknown scorer type: {scorer_type}")

    logger.info(
        f"Scored run {run_id} with {scorer.name}: score={result.score:.2f}, passed={result.passed}"
    )
    return result


async def get_score_aggregates(
    session: AsyncSession,
    agent_id: Optional[uuid.UUID] = None,
    scorer_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Get aggregated score statistics from the eval_scores table."""
    from sqlalchemy import and_

    conditions = []
    if agent_id:
        conditions.append(EvalScore.agent_id == str(agent_id))
    if scorer_name:
        conditions.append(EvalScore.scorer_name == scorer_name)

    where = and_(*conditions) if conditions else True

    total_result = await session.execute(
        select(func.count(EvalScore.id)).where(where)
    )
    total = total_result.scalar() or 0

    if total == 0:
        # Fall back to run_records count for backward compatibility
        stmt = select(func.count(RunRecord.id)).where(RunRecord.status.in_(["succeeded", "completed"]))
        if agent_id:
            stmt = stmt.where(RunRecord.agent_id == agent_id)
        success_count = (await session.execute(stmt)).scalar() or 0

        total_stmt = select(func.count(RunRecord.id))
        if agent_id:
            total_stmt = total_stmt.where(RunRecord.agent_id == agent_id)
        total_runs = (await session.execute(total_stmt)).scalar() or 0

        return {
            "total_scores": 0,
            "total_runs": total_runs,
            "successful_runs": success_count,
            "success_rate": success_count / total_runs if total_runs > 0 else 0.0,
            "agent_id": str(agent_id) if agent_id else None,
            "scorer_name": scorer_name,
        }

    passed_result = await session.execute(
        select(func.count(EvalScore.id)).where(
            and_(where, EvalScore.passed == True)  # noqa: E712
        )
    )
    passed = passed_result.scalar() or 0

    avg_result = await session.execute(
        select(func.avg(EvalScore.score)).where(where)
    )
    avg_score = float(avg_result.scalar() or 0.0)

    return {
        "total_scores": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": passed / total if total > 0 else 0.0,
        "avg_score": avg_score,
        "agent_id": str(agent_id) if agent_id else None,
        "scorer_name": scorer_name,
    }
