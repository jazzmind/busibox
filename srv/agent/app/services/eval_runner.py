"""
Eval runner service for batch scenario execution against agents.

Supports:
- Running all scenarios in a dataset through the agentic pipeline
- Capturing full traces and responses
- Running multiple scorers against each result
- Persisting scores to the eval_scores table
- A/B testing between model configurations
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import EvalDataset, EvalRun, EvalScenario, EvalScore, _now
from app.services.scorer_service import (
    ScorerResult,
    persist_scores_batch,
    score_llm_quality,
    score_output_contains,
    score_routing_accuracy,
    score_tool_selection,
)

logger = logging.getLogger(__name__)

# Maximum concurrency for running scenarios in parallel
MAX_CONCURRENCY = 3


class ScenarioResult:
    """Result of running a single eval scenario."""

    def __init__(
        self,
        scenario: EvalScenario,
        response: str,
        selected_agent: Optional[str],
        tools_used: List[str],
        elapsed_ms: float,
        error: Optional[str] = None,
    ):
        self.scenario = scenario
        self.response = response
        self.selected_agent = selected_agent
        self.tools_used = tools_used
        self.elapsed_ms = elapsed_ms
        self.error = error
        self.scores: List[ScorerResult] = []


class EvalRunSummary:
    """Summary of a completed eval batch run."""

    def __init__(
        self,
        eval_run_id: uuid.UUID,
        dataset_id: uuid.UUID,
        scenario_results: List[ScenarioResult],
        duration_seconds: float,
    ):
        self.eval_run_id = eval_run_id
        self.dataset_id = dataset_id
        self.scenario_results = scenario_results
        self.duration_seconds = duration_seconds

    @property
    def total(self) -> int:
        return len(self.scenario_results)

    @property
    def passed(self) -> int:
        return sum(
            1
            for r in self.scenario_results
            if r.scores and all(s.passed for s in r.scores)
        )

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def avg_score(self) -> float:
        all_scores = [s.score for r in self.scenario_results for s in r.scores]
        return sum(all_scores) / len(all_scores) if all_scores else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eval_run_id": str(self.eval_run_id),
            "dataset_id": str(self.dataset_id),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.passed / self.total if self.total > 0 else 0.0,
            "avg_score": self.avg_score,
            "duration_seconds": self.duration_seconds,
        }


async def _run_single_scenario(
    scenario: EvalScenario,
    user_id: str,
    principal: Any,
    session_factory: Any,
    scorers: List[str],
    model_override: Optional[str] = None,
) -> ScenarioResult:
    """
    Execute a single scenario through the agentic dispatcher and score it.
    """
    start = time.monotonic()
    selected_agent: Optional[str] = None
    tools_used: List[str] = []
    response_chunks: List[str] = []
    error: Optional[str] = None

    try:
        from app.services.agentic_dispatcher import run_agentic_dispatcher
        from app.schemas.streaming import StreamEvent

        async with session_factory() as session:
            # Build a minimal dispatcher metadata dict
            metadata: Dict[str, Any] = {"eval_scenario_id": str(scenario.id)}
            if model_override:
                metadata["model_override"] = model_override

            cancel_event = asyncio.Event()

            async for event in run_agentic_dispatcher(
                query=scenario.query,
                user_id=user_id,
                session=session,
                cancel=cancel_event,
                available_agents=None,
                conversation_history=[],
                principal=principal,
                metadata=metadata,
                attachment_metadata=[],
            ):
                if event.type == "content":
                    response_chunks.append(event.message or "")
                elif event.type in ("tool_start", "thought"):
                    if event.source and event.source not in ("dispatcher", "Chat Agent"):
                        if event.type == "tool_start":
                            tools_used.append(event.source)
                elif event.data and isinstance(event.data, dict):
                    if "selected_agent" in event.data:
                        selected_agent = event.data["selected_agent"]

    except Exception as exc:
        logger.error(f"Scenario '{scenario.name}' execution failed: {exc}", exc_info=True)
        error = str(exc)

    elapsed_ms = (time.monotonic() - start) * 1000
    response = "".join(response_chunks) if response_chunks else ""

    return ScenarioResult(
        scenario=scenario,
        response=response,
        selected_agent=selected_agent,
        tools_used=tools_used,
        elapsed_ms=elapsed_ms,
        error=error,
    )


async def _score_scenario_result(
    result: ScenarioResult,
    scorers: List[str],
    grading_model: str = "fast",
) -> List[ScorerResult]:
    """
    Apply all requested scorers to a scenario result.
    """
    scores: List[ScorerResult] = []
    scenario = result.scenario

    for scorer_name in scorers:
        try:
            if scorer_name == "llm_quality":
                score = await score_llm_quality(
                    query=scenario.query,
                    response=result.response,
                    grading_model=grading_model,
                )
                scores.append(score)

            elif scorer_name == "tool_selection":
                score = await score_tool_selection(
                    query=scenario.query,
                    tools_used=result.tools_used,
                    expected_tools=scenario.expected_tools,
                    grading_model=grading_model,
                )
                scores.append(score)

            elif scorer_name == "routing_accuracy":
                if result.selected_agent:
                    score = await score_routing_accuracy(
                        query=scenario.query,
                        selected_agent=result.selected_agent,
                        expected_agent=scenario.expected_agent,
                        grading_model=grading_model,
                    )
                    scores.append(score)

            elif scorer_name == "output_contains":
                if scenario.expected_output_contains:
                    score = score_output_contains(
                        response_text=result.response,
                        expected_phrases=scenario.expected_output_contains,
                    )
                    scores.append(score)

            elif scorer_name == "success":
                # Success = no error during execution
                passed = result.error is None and len(result.response) > 10
                scores.append(
                    ScorerResult(
                        run_id=None,
                        scorer_name="success",
                        score=1.0 if passed else 0.0,
                        passed=passed,
                        details={"error": result.error, "response_length": len(result.response)},
                    )
                )

            elif scorer_name == "latency":
                threshold_ms = 10000
                passed = result.elapsed_ms <= threshold_ms
                score_val = max(0.0, 1.0 - max(0, result.elapsed_ms - threshold_ms) / threshold_ms)
                scores.append(
                    ScorerResult(
                        run_id=None,
                        scorer_name="latency",
                        score=score_val,
                        passed=passed,
                        details={"elapsed_ms": result.elapsed_ms, "threshold_ms": threshold_ms},
                    )
                )

        except Exception as exc:
            logger.warning(f"Scorer '{scorer_name}' failed for scenario '{scenario.name}': {exc}")

    return scores


async def run_single_eval(
    scenario: EvalScenario,
    scorers: List[str],
    user_id: str,
    principal: Any,
    session_factory: Any,
    grading_model: str = "fast",
    model_override: Optional[str] = None,
) -> ScenarioResult:
    """Execute and score a single scenario."""
    result = await _run_single_scenario(
        scenario=scenario,
        user_id=user_id,
        principal=principal,
        session_factory=session_factory,
        scorers=scorers,
        model_override=model_override,
    )
    result.scores = await _score_scenario_result(result, scorers, grading_model)
    return result


async def run_eval_batch(
    session: AsyncSession,
    dataset_id: uuid.UUID,
    scorers: List[str],
    user_id: str,
    principal: Any,
    session_factory: Any,
    grading_model: str = "fast",
    model_override: Optional[str] = None,
    eval_run_name: Optional[str] = None,
) -> EvalRunSummary:
    """
    Execute all scenarios in a dataset and persist scores.

    Args:
        session: DB session for writing EvalRun / EvalScore records
        dataset_id: UUID of the EvalDataset to run
        scorers: List of scorer names to apply to each scenario
        user_id: User ID executing the eval (for billing/audit)
        principal: Auth principal for making agentic calls
        session_factory: Factory to create new DB sessions for each scenario
        grading_model: LLM model alias for LLM-as-judge scorers
        model_override: Optional model name to override agent default
        eval_run_name: Optional display name for this eval run

    Returns:
        EvalRunSummary with aggregated results
    """
    from sqlalchemy import select

    # Validate dataset exists
    dataset = await session.get(EvalDataset, dataset_id)
    if not dataset:
        raise ValueError(f"EvalDataset {dataset_id} not found")

    # Load active scenarios
    result = await session.execute(
        select(EvalScenario)
        .where(EvalScenario.dataset_id == dataset_id, EvalScenario.is_active == True)  # noqa: E712
        .order_by(EvalScenario.created_at.asc())
    )
    scenarios = list(result.scalars().all())

    if not scenarios:
        raise ValueError(f"Dataset {dataset.name} has no active scenarios")

    # Create EvalRun record
    eval_run = EvalRun(
        dataset_id=dataset_id,
        name=eval_run_name or f"{dataset.name} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
        status="running",
        scorers=scorers,
        model_override=model_override,
        total_scenarios=len(scenarios),
        created_by=user_id,
        started_at=_now(),
    )
    session.add(eval_run)
    await session.flush()

    logger.info(
        f"Starting eval run {eval_run.id} for dataset '{dataset.name}' "
        f"({len(scenarios)} scenarios, scorers={scorers})"
    )

    start_time = time.monotonic()
    scenario_results: List[ScenarioResult] = []

    # Run scenarios with limited concurrency
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def run_with_semaphore(scenario: EvalScenario) -> ScenarioResult:
        async with semaphore:
            return await run_single_eval(
                scenario=scenario,
                scorers=scorers,
                user_id=user_id,
                principal=principal,
                session_factory=session_factory,
                grading_model=grading_model,
                model_override=model_override,
            )

    tasks = [run_with_semaphore(s) for s in scenarios]
    scenario_results = list(await asyncio.gather(*tasks, return_exceptions=False))

    duration_seconds = time.monotonic() - start_time

    # Persist all scores
    passed_count = 0
    for scenario_result in scenario_results:
        if scenario_result.scores:
            await persist_scores_batch(
                session=session,
                results=scenario_result.scores,
                eval_run_id=eval_run.id,
                scenario_id=scenario_result.scenario.id,
                source="offline",
            )
            if all(s.passed for s in scenario_result.scores):
                passed_count += 1

    # Update EvalRun with summary
    all_scores_flat = [s.score for r in scenario_results for s in r.scores]
    avg_score = sum(all_scores_flat) / len(all_scores_flat) if all_scores_flat else None

    eval_run.status = "completed"
    eval_run.passed_scenarios = passed_count
    eval_run.failed_scenarios = len(scenarios) - passed_count
    eval_run.avg_score = avg_score
    eval_run.duration_seconds = duration_seconds
    eval_run.completed_at = _now()

    await session.flush()

    summary = EvalRunSummary(
        eval_run_id=eval_run.id,
        dataset_id=dataset_id,
        scenario_results=scenario_results,
        duration_seconds=duration_seconds,
    )

    logger.info(
        f"Eval run {eval_run.id} complete: {summary.passed}/{summary.total} passed "
        f"(avg_score={summary.avg_score:.2f}, duration={duration_seconds:.1f}s)"
    )

    return summary


async def sample_online_eval(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    query: str,
    response: str,
    agent_id: Optional[str],
    user_id: str,
    sample_rate: float = 0.15,
    grading_model: str = "fast",
    quality_alert_threshold: float = 0.4,
) -> None:
    """
    Fire-and-forget online eval hook: samples N% of production conversations
    for async LLM quality grading. Persists scores and fires quality alerts.

    Designed to be called from chat.py after successful agentic completion.
    """
    import random

    if random.random() > sample_rate:
        return

    try:
        logger.info(
            f"Running online eval for message {message_id} (agent={agent_id})"
        )
        score = await score_llm_quality(
            query=query,
            response=response,
            grading_model=grading_model,
        )
        await persist_scores_batch(
            session=session,
            results=[score],
            agent_id=agent_id,
            conversation_id=conversation_id,
            message_id=message_id,
            source="online",
        )
        await session.flush()

        # Phase 4b: Production quality alerting
        if score.score < quality_alert_threshold:
            logger.warning(
                "QUALITY_ALERT: Agent response below quality threshold",
                extra={
                    "event": "quality_alert",
                    "agent_id": agent_id,
                    "message_id": str(message_id),
                    "conversation_id": str(conversation_id),
                    "score": score.score,
                    "threshold": quality_alert_threshold,
                    "dimensions": {
                        k: v
                        for k, v in score.details.items()
                        if k in ("relevance", "correctness", "helpfulness", "safety")
                    },
                    "reasoning": score.details.get("reasoning", ""),
                    "query_preview": query[:100],
                },
            )

        logger.info(
            f"Online eval complete for message {message_id}: "
            f"score={score.score:.2f}, passed={score.passed}"
        )
    except Exception as exc:
        logger.warning(f"Online eval failed for message {message_id}: {exc}")
