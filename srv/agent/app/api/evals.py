"""
Evals & Observability API.

Provides:
- EvalDefinition CRUD (legacy)
- EvalDataset / EvalScenario CRUD
- Batch eval run execution
- Score queries and trend aggregation
- Observability: agent metrics, traces, routing accuracy
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_principal
from app.core.logging import get_logger
from app.db.session import SessionLocal, get_session
from app.models.domain import (
    AgentDefinition,
    EvalDataset,
    EvalDefinition,
    EvalRun,
    EvalScenario,
    EvalScore,
    Message,
    RunRecord,
    _now,
)
from app.schemas.auth import Principal
from app.schemas.definitions import EvalDefinitionRead, EvalDefinitionUpdate

router = APIRouter(prefix="/evals", tags=["evals"])
logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas (inline to avoid adding a separate schema file)
# ─────────────────────────────────────────────────────────────────────────────


class DatasetCreate(BaseModel):
    name: str = Field(..., max_length=120)
    description: Optional[str] = None
    agent_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class DatasetUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    agent_id: Optional[str] = None
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None


class DatasetRead(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    agent_id: Optional[str]
    tags: List[str]
    is_active: bool
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime
    scenario_count: int = 0

    model_config = {"from_attributes": True}


class ScenarioCreate(BaseModel):
    name: str = Field(..., max_length=255)
    query: str
    expected_agent: Optional[str] = None
    expected_tools: Optional[List[str]] = None
    expected_output_contains: Optional[List[str]] = None
    expected_outcome: Optional[str] = None
    scenario_metadata: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)


class ScenarioUpdate(BaseModel):
    name: Optional[str] = None
    query: Optional[str] = None
    expected_agent: Optional[str] = None
    expected_tools: Optional[List[str]] = None
    expected_output_contains: Optional[List[str]] = None
    expected_outcome: Optional[str] = None
    scenario_metadata: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None


class ScenarioRead(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    name: str
    query: str
    expected_agent: Optional[str]
    expected_tools: Optional[List[str]]
    expected_output_contains: Optional[List[str]]
    expected_outcome: Optional[str]
    scenario_metadata: Dict[str, Any]
    tags: List[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EvalRunRequest(BaseModel):
    dataset_id: uuid.UUID
    scorers: List[str] = Field(
        default=["success", "llm_quality"],
        description="Scorer names: success, latency, llm_quality, tool_selection, routing_accuracy, output_contains",
    )
    model_override: Optional[str] = None
    name: Optional[str] = None


class EvalRunRead(BaseModel):
    id: uuid.UUID
    dataset_id: Optional[uuid.UUID]
    name: Optional[str]
    status: str
    scorers: List[str]
    model_override: Optional[str]
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    avg_score: Optional[float]
    duration_seconds: Optional[float]
    error: Optional[str]
    created_by: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class ScoreRead(BaseModel):
    id: uuid.UUID
    agent_id: Optional[str]
    scorer_name: str
    score: float
    passed: bool
    details: Dict[str, Any]
    grading_model: Optional[str]
    source: str
    scenario_id: Optional[uuid.UUID]
    eval_run_id: Optional[uuid.UUID]
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# Legacy EvalDefinition endpoints (kept for backward compatibility)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/definitions/{eval_id}", response_model=EvalDefinitionRead)
async def get_evaluator(
    eval_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> EvalDefinitionRead:
    """Get individual evaluator by ID."""
    evaluator = await session.get(EvalDefinition, eval_id)
    if not evaluator or not evaluator.is_active:
        raise HTTPException(status_code=404, detail="Evaluator not found")
    return EvalDefinitionRead.model_validate(evaluator)


@router.put("/definitions/{eval_id}", response_model=EvalDefinitionRead)
async def update_evaluator(
    eval_id: uuid.UUID,
    payload: EvalDefinitionUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> EvalDefinitionRead:
    """Update an evaluator definition."""
    evaluator = await session.get(EvalDefinition, eval_id)
    if not evaluator or not evaluator.is_active:
        raise HTTPException(status_code=404, detail="Evaluator not found")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(evaluator, key, value)
    evaluator.version += 1
    evaluator.updated_at = _now()

    await session.commit()
    await session.refresh(evaluator)
    return EvalDefinitionRead.model_validate(evaluator)


@router.delete("/definitions/{eval_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evaluator(
    eval_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-delete an evaluator."""
    evaluator = await session.get(EvalDefinition, eval_id)
    if not evaluator or not evaluator.is_active:
        raise HTTPException(status_code=404, detail="Evaluator not found")
    evaluator.is_active = False
    await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Datasets
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/datasets", response_model=List[DatasetRead])
async def list_datasets(
    agent_id: Optional[str] = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[DatasetRead]:
    """List all eval datasets."""
    stmt = (
        select(EvalDataset)
        .where(EvalDataset.is_active == True)  # noqa: E712
        .options(selectinload(EvalDataset.scenarios))
        .order_by(desc(EvalDataset.created_at))
    )
    if agent_id:
        stmt = stmt.where(EvalDataset.agent_id == agent_id)
    result = await session.execute(stmt)
    datasets = result.scalars().all()

    out = []
    for ds in datasets:
        d = DatasetRead.model_validate(ds)
        d.scenario_count = len([s for s in ds.scenarios if s.is_active])
        out.append(d)
    return out


@router.post("/datasets", response_model=DatasetRead, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    payload: DatasetCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> DatasetRead:
    """Create a new eval dataset."""
    dataset = EvalDataset(
        name=payload.name,
        description=payload.description,
        agent_id=payload.agent_id,
        tags=payload.tags,
        created_by=principal.sub,
    )
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    d = DatasetRead.model_validate(dataset)
    d.scenario_count = 0
    return d


@router.get("/datasets/{dataset_id}", response_model=DatasetRead)
async def get_dataset(
    dataset_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> DatasetRead:
    """Get a dataset with its scenarios."""
    result = await session.execute(
        select(EvalDataset)
        .where(EvalDataset.id == dataset_id)
        .options(selectinload(EvalDataset.scenarios))
    )
    dataset = result.scalar_one_or_none()
    if not dataset or not dataset.is_active:
        raise HTTPException(status_code=404, detail="Dataset not found")
    d = DatasetRead.model_validate(dataset)
    d.scenario_count = len([s for s in dataset.scenarios if s.is_active])
    return d


@router.patch("/datasets/{dataset_id}", response_model=DatasetRead)
async def update_dataset(
    dataset_id: uuid.UUID,
    payload: DatasetUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> DatasetRead:
    """Update a dataset."""
    result = await session.execute(
        select(EvalDataset)
        .where(EvalDataset.id == dataset_id)
        .options(selectinload(EvalDataset.scenarios))
    )
    dataset = result.scalar_one_or_none()
    if not dataset or not dataset.is_active:
        raise HTTPException(status_code=404, detail="Dataset not found")

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(dataset, key, value)
    dataset.updated_at = _now()

    await session.commit()
    await session.refresh(dataset)
    d = DatasetRead.model_validate(dataset)
    d.scenario_count = len([s for s in dataset.scenarios if s.is_active])
    return d


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-delete a dataset."""
    dataset = await session.get(EvalDataset, dataset_id)
    if not dataset or not dataset.is_active:
        raise HTTPException(status_code=404, detail="Dataset not found")
    dataset.is_active = False
    await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/datasets/{dataset_id}/scenarios", response_model=List[ScenarioRead])
async def list_scenarios(
    dataset_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[ScenarioRead]:
    """List all scenarios in a dataset."""
    result = await session.execute(
        select(EvalScenario)
        .where(EvalScenario.dataset_id == dataset_id, EvalScenario.is_active == True)  # noqa: E712
        .order_by(asc(EvalScenario.created_at))
    )
    return [ScenarioRead.model_validate(s) for s in result.scalars().all()]


@router.post(
    "/datasets/{dataset_id}/scenarios",
    response_model=ScenarioRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_scenario(
    dataset_id: uuid.UUID,
    payload: ScenarioCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ScenarioRead:
    """Add a scenario to a dataset."""
    dataset = await session.get(EvalDataset, dataset_id)
    if not dataset or not dataset.is_active:
        raise HTTPException(status_code=404, detail="Dataset not found")

    scenario = EvalScenario(
        dataset_id=dataset_id,
        name=payload.name,
        query=payload.query,
        expected_agent=payload.expected_agent,
        expected_tools=payload.expected_tools,
        expected_output_contains=payload.expected_output_contains,
        expected_outcome=payload.expected_outcome,
        scenario_metadata=payload.scenario_metadata,
        tags=payload.tags,
    )
    session.add(scenario)
    await session.commit()
    await session.refresh(scenario)
    return ScenarioRead.model_validate(scenario)


@router.patch("/scenarios/{scenario_id}", response_model=ScenarioRead)
async def update_scenario(
    scenario_id: uuid.UUID,
    payload: ScenarioUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ScenarioRead:
    """Update a scenario."""
    scenario = await session.get(EvalScenario, scenario_id)
    if not scenario or not scenario.is_active:
        raise HTTPException(status_code=404, detail="Scenario not found")

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(scenario, key, value)
    scenario.updated_at = _now()

    await session.commit()
    await session.refresh(scenario)
    return ScenarioRead.model_validate(scenario)


@router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scenario(
    scenario_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-delete a scenario."""
    scenario = await session.get(EvalScenario, scenario_id)
    if not scenario or not scenario.is_active:
        raise HTTPException(status_code=404, detail="Scenario not found")
    scenario.is_active = False
    await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Eval Runs
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/run", response_model=EvalRunRead, status_code=status.HTTP_202_ACCEPTED)
async def trigger_eval_run(
    payload: EvalRunRequest,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> EvalRunRead:
    """
    Trigger a batch eval run against a dataset.

    Creates the EvalRun record immediately and executes asynchronously.
    Poll GET /evals/runs/{id} to check status.
    """
    dataset = await session.get(EvalDataset, payload.dataset_id)
    if not dataset or not dataset.is_active:
        raise HTTPException(status_code=404, detail="Dataset not found")

    # Create pending run record immediately so caller gets an ID
    eval_run = EvalRun(
        dataset_id=payload.dataset_id,
        name=payload.name or f"{dataset.name} — {_now().strftime('%Y-%m-%d %H:%M')}",
        status="pending",
        scorers=payload.scorers,
        model_override=payload.model_override,
        created_by=principal.sub,
    )
    session.add(eval_run)
    await session.commit()
    await session.refresh(eval_run)

    run_id = eval_run.id

    async def _run_in_background() -> None:
        from app.services.eval_runner import run_eval_batch

        async with SessionLocal() as bg_session:
            try:
                await run_eval_batch(
                    session=bg_session,
                    dataset_id=payload.dataset_id,
                    scorers=payload.scorers,
                    user_id=principal.sub,
                    principal=principal,
                    session_factory=SessionLocal,
                    grading_model="fast",
                    model_override=payload.model_override,
                    eval_run_name=payload.name,
                )
                await bg_session.commit()
            except Exception as exc:
                logger.error(f"Eval run {run_id} failed: {exc}", exc_info=True)
                # Mark run as failed
                run_row = await bg_session.get(EvalRun, run_id)
                if run_row:
                    run_row.status = "failed"
                    run_row.error = str(exc)[:2000]
                    run_row.completed_at = _now()
                    await bg_session.commit()

    background_tasks.add_task(_run_in_background)

    return EvalRunRead.model_validate(eval_run)


@router.get("/runs", response_model=List[EvalRunRead])
async def list_eval_runs(
    dataset_id: Optional[uuid.UUID] = None,
    limit: int = Query(default=20, le=100),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[EvalRunRead]:
    """List eval runs with optional dataset filter."""
    stmt = select(EvalRun).order_by(desc(EvalRun.created_at)).limit(limit)
    if dataset_id:
        stmt = stmt.where(EvalRun.dataset_id == dataset_id)
    result = await session.execute(stmt)
    return [EvalRunRead.model_validate(r) for r in result.scalars().all()]


@router.get("/runs/{run_id}", response_model=EvalRunRead)
async def get_eval_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> EvalRunRead:
    """Get eval run details."""
    run = await session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return EvalRunRead.model_validate(run)


@router.get("/runs/{run_id}/scores", response_model=List[ScoreRead])
async def get_run_scores(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[ScoreRead]:
    """Get all scores for an eval run, grouped by scenario."""
    result = await session.execute(
        select(EvalScore)
        .where(EvalScore.eval_run_id == run_id)
        .order_by(EvalScore.created_at.asc())
    )
    return [ScoreRead.model_validate(s) for s in result.scalars().all()]


# ─────────────────────────────────────────────────────────────────────────────
# Score queries
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/scores", response_model=List[ScoreRead])
async def query_scores(
    agent_id: Optional[str] = None,
    scorer_name: Optional[str] = None,
    source: Optional[str] = None,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=100, le=500),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[ScoreRead]:
    """Query scores filtered by agent, scorer, source, and time window."""
    since = _now() - timedelta(days=days)

    conditions = [EvalScore.created_at >= since]
    if agent_id:
        conditions.append(EvalScore.agent_id == agent_id)
    if scorer_name:
        conditions.append(EvalScore.scorer_name == scorer_name)
    if source:
        conditions.append(EvalScore.source == source)

    result = await session.execute(
        select(EvalScore)
        .where(and_(*conditions))
        .order_by(desc(EvalScore.created_at))
        .limit(limit)
    )
    return [ScoreRead.model_validate(s) for s in result.scalars().all()]


@router.get("/scores/trends")
async def get_score_trends(
    agent_id: Optional[str] = None,
    scorer_name: str = "llm_quality",
    days: int = Query(default=14, ge=1, le=90),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Time-series aggregates for dashboard charts.
    Returns daily average scores for the requested window.
    """
    since = _now() - timedelta(days=days)

    conditions = [
        EvalScore.created_at >= since,
        EvalScore.scorer_name == scorer_name,
    ]
    if agent_id:
        conditions.append(EvalScore.agent_id == agent_id)

    # Use database date truncation for daily buckets
    from sqlalchemy import cast, Date, text

    result = await session.execute(
        select(
            cast(EvalScore.created_at, Date).label("day"),
            func.avg(EvalScore.score).label("avg_score"),
            func.count(EvalScore.id).label("count"),
            func.sum(func.cast(EvalScore.passed, type_=func.count().type)).label("passed"),
        )
        .where(and_(*conditions))
        .group_by(cast(EvalScore.created_at, Date))
        .order_by(cast(EvalScore.created_at, Date))
    )
    rows = result.fetchall()

    return {
        "scorer_name": scorer_name,
        "agent_id": agent_id,
        "days": days,
        "data": [
            {
                "day": str(row.day),
                "avg_score": round(float(row.avg_score), 3) if row.avg_score else None,
                "count": row.count,
            }
            for row in rows
        ],
    }


@router.get("/scores/by-agent/{agent_id}")
async def get_agent_score_summary(
    agent_id: str,
    days: int = Query(default=7, ge=1, le=90),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Get score summary for a specific agent across all scorers."""
    since = _now() - timedelta(days=days)

    result = await session.execute(
        select(
            EvalScore.scorer_name,
            func.avg(EvalScore.score).label("avg_score"),
            func.count(EvalScore.id).label("total"),
        )
        .where(
            and_(
                EvalScore.agent_id == agent_id,
                EvalScore.created_at >= since,
            )
        )
        .group_by(EvalScore.scorer_name)
    )
    rows = result.fetchall()

    return {
        "agent_id": agent_id,
        "days": days,
        "scorers": {
            row.scorer_name: {
                "avg_score": round(float(row.avg_score), 3) if row.avg_score else None,
                "total": row.total,
            }
            for row in rows
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Observability
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/observability/agent/{agent_id}/metrics")
async def get_agent_metrics(
    agent_id: str,
    window_hours: int = Query(default=24, ge=1, le=168),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Aggregated metrics for an agent over a time window:
    - Total runs, success rate, avg latency
    - Tool usage counts
    - Quality scores (if online evals exist)
    """
    since = _now() - timedelta(hours=window_hours)

    # Look up agent UUID if name given
    agent_uuid: Optional[uuid.UUID] = None
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        agent_row = (
            await session.execute(
                select(AgentDefinition).where(AgentDefinition.name == agent_id)
            )
        ).scalar_one_or_none()
        if agent_row:
            agent_uuid = agent_row.id

    # Run metrics
    run_conditions = [RunRecord.created_at >= since]
    if agent_uuid:
        run_conditions.append(RunRecord.agent_id == agent_uuid)

    total_result = await session.execute(
        select(func.count(RunRecord.id)).where(and_(*run_conditions))
    )
    total_runs = total_result.scalar() or 0

    success_result = await session.execute(
        select(func.count(RunRecord.id)).where(
            and_(*run_conditions, RunRecord.status.in_(["succeeded", "completed"]))
        )
    )
    successful_runs = success_result.scalar() or 0

    # Quality scores from online eval
    score_conditions = [
        EvalScore.created_at >= since,
        EvalScore.source == "online",
        EvalScore.scorer_name == "llm_quality",
    ]
    if agent_id:
        score_conditions.append(EvalScore.agent_id == agent_id)

    quality_result = await session.execute(
        select(func.avg(EvalScore.score), func.count(EvalScore.id)).where(
            and_(*score_conditions)
        )
    )
    quality_avg, quality_count = quality_result.one()

    return {
        "agent_id": agent_id,
        "window_hours": window_hours,
        "runs": {
            "total": total_runs,
            "successful": successful_runs,
            "success_rate": successful_runs / total_runs if total_runs > 0 else None,
        },
        "quality": {
            "avg_score": round(float(quality_avg), 3) if quality_avg else None,
            "sample_count": quality_count,
        },
    }


@router.get("/observability/agent/{agent_id}/traces")
async def get_agent_traces(
    agent_id: str,
    limit: int = Query(default=20, le=100),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Recent run traces for an agent with full event timelines.
    """
    agent_uuid: Optional[uuid.UUID] = None
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        agent_row = (
            await session.execute(
                select(AgentDefinition).where(AgentDefinition.name == agent_id)
            )
        ).scalar_one_or_none()
        if agent_row:
            agent_uuid = agent_row.id

    conditions = []
    if agent_uuid:
        conditions.append(RunRecord.agent_id == agent_uuid)

    result = await session.execute(
        select(RunRecord)
        .where(and_(*conditions) if conditions else True)
        .order_by(desc(RunRecord.created_at))
        .limit(limit)
    )
    runs = result.scalars().all()

    return {
        "agent_id": agent_id,
        "traces": [
            {
                "run_id": str(r.id),
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "input_preview": (r.input or {}).get("prompt", "")[:200],
                "output_preview": ((r.output or {}).get("response", "")[:200]) if r.output else "",
                "events": r.events[-20:] if r.events else [],  # Last 20 events
            }
            for r in runs
        ],
    }


@router.get("/observability/routing/accuracy")
async def get_routing_accuracy(
    days: int = Query(default=7, ge=1, le=30),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Dispatcher routing accuracy from eval_scores (routing_accuracy scorer).
    Also returns distribution of selected agents from messages metadata.
    """
    since = _now() - timedelta(days=days)

    # Routing accuracy from scored evals
    result = await session.execute(
        select(
            func.avg(EvalScore.score).label("avg_score"),
            func.count(EvalScore.id).label("total"),
            func.sum(func.cast(EvalScore.passed, type_=func.count().type)).label("passed"),
        ).where(
            and_(
                EvalScore.scorer_name == "routing_accuracy",
                EvalScore.created_at >= since,
            )
        )
    )
    row = result.one()

    # Agent selection distribution from message routing_decision metadata
    messages_result = await session.execute(
        select(Message.routing_decision)
        .where(
            and_(
                Message.created_at >= since,
                Message.role == "assistant",
                Message.routing_decision.isnot(None),
            )
        )
        .limit(500)
    )
    agent_counts: Dict[str, int] = {}
    for (rd,) in messages_result.fetchall():
        if isinstance(rd, dict):
            agents = rd.get("selected_agents", [])
            for a in agents:
                agent_counts[a] = agent_counts.get(a, 0) + 1

    return {
        "days": days,
        "routing_accuracy": {
            "avg_score": round(float(row.avg_score), 3) if row.avg_score else None,
            "total_scored": row.total,
            "passed": row.passed or 0,
            "pass_rate": (row.passed or 0) / row.total if row.total > 0 else None,
        },
        "agent_distribution": agent_counts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Failure analysis (Phase 4)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/analysis")
async def get_run_analysis(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Analyze failures in an eval run and return LLM-driven improvement suggestions.
    """
    from app.services.eval_analysis import analyze_failures

    run = await session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")

    return await analyze_failures(session=session, eval_run_id=run_id)


# ─────────────────────────────────────────────────────────────────────────────
# Quality Alerts (Phase 4b)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/observability/quality-alerts")
async def get_quality_alerts(
    threshold: float = Query(default=0.4, ge=0.0, le=1.0),
    agent_id: Optional[str] = None,
    days: int = Query(default=1, ge=1, le=30),
    limit: int = Query(default=50, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Return online eval scores that fell below the quality threshold.

    These correspond to production conversations where the agent's response
    was graded as poor quality by the LLM judge.
    """
    since = _now() - timedelta(days=days)

    conditions = [
        EvalScore.created_at >= since,
        EvalScore.source == "online",
        EvalScore.scorer_name == "llm_quality",
        EvalScore.score < threshold,
    ]
    if agent_id:
        conditions.append(EvalScore.agent_id == agent_id)

    result = await session.execute(
        select(EvalScore)
        .where(and_(*conditions))
        .order_by(desc(EvalScore.created_at))
        .limit(limit)
    )
    alerts = result.scalars().all()

    return {
        "threshold": threshold,
        "agent_id": agent_id,
        "days": days,
        "total_alerts": len(alerts),
        "alerts": [
            {
                "id": str(a.id),
                "agent_id": a.agent_id,
                "score": a.score,
                "details": a.details,
                "conversation_id": str(a.conversation_id) if a.conversation_id else None,
                "message_id": str(a.message_id) if a.message_id else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in alerts
        ],
    }
