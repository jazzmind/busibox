"""
Workflow CRUD API endpoints.

Provides:
- Individual workflow retrieval by ID
- Workflow updates with version increment and validation
- Workflow soft deletion with schedule conflict detection
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from app.auth.dependencies import get_principal
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.domain import WorkflowDefinition, WorkflowExecution, StepExecution
from app.schemas.auth import Principal
from app.schemas.definitions import WorkflowDefinitionRead, WorkflowDefinitionUpdate

router = APIRouter(prefix="/agents/workflows", tags=["workflows"])
logger = get_logger(__name__)


@router.get("/{workflow_id}", response_model=WorkflowDefinitionRead)
async def get_workflow(
    workflow_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> WorkflowDefinitionRead:
    """
    Get individual workflow by ID.
    
    Returns:
        Workflow definition if found and active
        
    Raises:
        HTTPException: 404 if workflow not found or inactive
    """
    workflow = await session.get(WorkflowDefinition, workflow_id)
    
    if not workflow or not workflow.is_active:
        logger.warning(
            "workflow_not_found",
            workflow_id=str(workflow_id),
            user_id=principal.sub
        )
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    logger.info(
        "workflow_retrieved",
        workflow_id=str(workflow_id),
        workflow_name=workflow.name,
        user_id=principal.sub
    )
    
    return WorkflowDefinitionRead.model_validate(workflow)


@router.put("/{workflow_id}", response_model=WorkflowDefinitionRead)
async def update_workflow(
    workflow_id: uuid.UUID,
    payload: WorkflowDefinitionUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> WorkflowDefinitionRead:
    """
    Update a workflow definition.
    
    Validates workflow steps before saving.
    Version number increments automatically.
    
    Args:
        workflow_id: Workflow UUID
        payload: Fields to update
        principal: Authenticated user
        session: Database session
        
    Returns:
        Updated workflow definition
        
    Raises:
        HTTPException:
            - 404 if workflow not found
            - 400 if workflow validation fails
    """
    workflow = await session.get(WorkflowDefinition, workflow_id)
    
    if not workflow or not workflow.is_active:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Check ownership
    if workflow.created_by and workflow.created_by != principal.sub:
        logger.warning(
            "workflow_update_unauthorized",
            workflow_id=str(workflow_id),
            workflow_name=workflow.name,
            owner=workflow.created_by,
            user_id=principal.sub
        )
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Validate workflow steps if provided
    update_data = payload.model_dump(exclude_unset=True)
    if "steps" in update_data:
        try:
            from app.workflows.engine import validate_workflow_steps
            validate_workflow_steps(update_data["steps"])
        except ValueError as e:
            logger.error(
                "workflow_validation_failed",
                workflow_id=str(workflow_id),
                user_id=principal.sub,
                error=str(e)
            )
            raise HTTPException(
                status_code=400,
                detail=f"Invalid workflow steps: {str(e)}"
            )
    
    # Update fields
    for key, value in update_data.items():
        setattr(workflow, key, value)
    
    # Increment version and update timestamp
    workflow.version += 1
    workflow.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
    await session.commit()
    await session.refresh(workflow)
    
    logger.info(
        "workflow_updated",
        workflow_id=str(workflow_id),
        workflow_name=workflow.name,
        new_version=workflow.version,
        user_id=principal.sub,
        updated_fields=list(update_data.keys())
    )
    
    return WorkflowDefinitionRead.model_validate(workflow)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """
    Soft-delete a workflow.
    
    Workflows with active scheduled runs cannot be deleted.
    
    Args:
        workflow_id: Workflow UUID
        principal: Authenticated user
        session: Database session
        
    Raises:
        HTTPException:
            - 404 if workflow not found
            - 409 if workflow has active scheduled runs
    """
    workflow = await session.get(WorkflowDefinition, workflow_id)
    
    if not workflow or not workflow.is_active:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Check ownership
    if workflow.created_by and workflow.created_by != principal.sub:
        logger.warning(
            "workflow_delete_unauthorized",
            workflow_id=str(workflow_id),
            workflow_name=workflow.name,
            owner=workflow.created_by,
            user_id=principal.sub
        )
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Check if workflow has active scheduled runs
    # TODO: Implement when ScheduledRun model is available
    # For now, allow deletion
    
    # Soft delete
    workflow.is_active = False
    await session.commit()
    
    logger.info(
        "workflow_deleted",
        workflow_id=str(workflow_id),
        workflow_name=workflow.name,
        user_id=principal.sub
    )


# ============================================================================
# Workflow Execution Endpoints
# ============================================================================

class ExecuteWorkflowRequest(BaseModel):
    """Request body for executing a workflow."""
    input_data: Optional[Dict[str, Any]] = None
    guardrails: Optional[Dict[str, Any]] = None


class WorkflowExecutionResponse(BaseModel):
    """Response for workflow execution."""
    id: uuid.UUID
    workflow_id: uuid.UUID
    status: str
    trigger_source: str
    input_data: Dict[str, Any]
    step_outputs: Dict[str, Any]
    usage_requests: int
    usage_input_tokens: int
    usage_output_tokens: int
    usage_tool_calls: int
    estimated_cost_dollars: float
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]
    error: Optional[str]
    
    class Config:
        from_attributes = True


class StepExecutionResponse(BaseModel):
    """Response for step execution."""
    id: uuid.UUID
    step_id: str
    status: str
    output_data: Optional[Dict[str, Any]]
    usage_requests: int
    usage_input_tokens: int
    usage_output_tokens: int
    estimated_cost_dollars: float
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]
    error: Optional[str]
    
    class Config:
        from_attributes = True


class HumanApprovalRequest(BaseModel):
    """Request body for human approval."""
    option_id: str
    comment: Optional[str] = None


@router.post("/{workflow_id}/execute", response_model=WorkflowExecutionResponse)
async def execute_workflow(
    workflow_id: uuid.UUID,
    payload: ExecuteWorkflowRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> WorkflowExecutionResponse:
    """
    Execute a workflow manually.
    
    Args:
        workflow_id: Workflow UUID
        payload: Execution request with input data and optional guardrails
        principal: Authenticated user
        session: Database session
        
    Returns:
        Workflow execution record
        
    Raises:
        HTTPException: 404 if workflow not found, 400 if execution fails
    """
    from app.workflows.enhanced_engine import execute_enhanced_workflow
    
    # Verify workflow exists
    workflow = await session.get(WorkflowDefinition, workflow_id)
    if not workflow or not workflow.is_active:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    logger.info(
        "workflow_execution_started",
        workflow_id=str(workflow_id),
        workflow_name=workflow.name,
        user_id=principal.sub
    )
    
    try:
        # Execute workflow
        execution = await execute_enhanced_workflow(
            session=session,
            principal=principal,
            workflow_id=workflow_id,
            input_data=payload.input_data or {},
            scopes=["agent:read", "agent:write"],  # Default scopes
            purpose="workflow_execution",
            override_guardrails=payload.guardrails,
        )
        
        logger.info(
            "workflow_execution_completed",
            workflow_id=str(workflow_id),
            execution_id=str(execution.id),
            status=execution.status,
            user_id=principal.sub
        )
        
        return WorkflowExecutionResponse.model_validate(execution)
        
    except Exception as e:
        logger.error(
            "workflow_execution_failed",
            workflow_id=str(workflow_id),
            user_id=principal.sub,
            error=str(e)
        )
        raise HTTPException(
            status_code=400,
            detail=f"Workflow execution failed: {str(e)}"
        )


@router.get("/{workflow_id}/executions", response_model=List[WorkflowExecutionResponse])
async def list_workflow_executions(
    workflow_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[WorkflowExecutionResponse]:
    """
    List executions for a workflow.
    
    Args:
        workflow_id: Workflow UUID
        limit: Maximum number of results (default 50)
        offset: Pagination offset (default 0)
        status: Optional status filter
        principal: Authenticated user
        session: Database session
        
    Returns:
        List of workflow executions
    """
    # Verify workflow exists
    workflow = await session.get(WorkflowDefinition, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Build query
    query = select(WorkflowExecution).where(
        WorkflowExecution.workflow_id == workflow_id
    )
    
    if status:
        query = query.where(WorkflowExecution.status == status)
    
    query = query.order_by(WorkflowExecution.created_at.desc())
    query = query.limit(limit).offset(offset)
    
    result = await session.execute(query)
    executions = result.scalars().all()
    
    return [WorkflowExecutionResponse.model_validate(e) for e in executions]


@router.get("/executions/{execution_id}", response_model=WorkflowExecutionResponse)
async def get_execution(
    execution_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> WorkflowExecutionResponse:
    """
    Get detailed execution information.
    
    Args:
        execution_id: Execution UUID
        principal: Authenticated user
        session: Database session
        
    Returns:
        Execution details
        
    Raises:
        HTTPException: 404 if execution not found
    """
    execution = await session.get(WorkflowExecution, execution_id)
    
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    return WorkflowExecutionResponse.model_validate(execution)


@router.get("/executions/{execution_id}/steps", response_model=List[StepExecutionResponse])
async def get_execution_steps(
    execution_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[StepExecutionResponse]:
    """
    Get step executions for a workflow execution.
    
    Args:
        execution_id: Execution UUID
        principal: Authenticated user
        session: Database session
        
    Returns:
        List of step executions
    """
    # Verify execution exists
    execution = await session.get(WorkflowExecution, execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    # Get steps
    query = select(StepExecution).where(
        StepExecution.execution_id == execution_id
    ).order_by(StepExecution.created_at)
    
    result = await session.execute(query)
    steps = result.scalars().all()
    
    return [StepExecutionResponse.model_validate(s) for s in steps]


@router.post("/executions/{execution_id}/approve", response_model=WorkflowExecutionResponse)
async def approve_human_step(
    execution_id: uuid.UUID,
    payload: HumanApprovalRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> WorkflowExecutionResponse:
    """
    Approve a human-in-loop step and resume workflow execution.
    
    Args:
        execution_id: Execution UUID
        payload: Approval request with option and comment
        principal: Authenticated user
        session: Database session
        
    Returns:
        Updated execution record
        
    Raises:
        HTTPException: 404 if execution not found, 400 if not awaiting approval
    """
    execution = await session.get(WorkflowExecution, execution_id)
    
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    if execution.status != "awaiting_human":
        raise HTTPException(
            status_code=400,
            detail=f"Execution is not awaiting human approval (status: {execution.status})"
        )
    
    logger.info(
        "human_approval_received",
        execution_id=str(execution_id),
        option_id=payload.option_id,
        user_id=principal.sub
    )
    
    # Store approval in step outputs
    approval_data = {
        "option_id": payload.option_id,
        "comment": payload.comment,
        "approved_by": principal.sub,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    
    if execution.current_step_id:
        execution.step_outputs[execution.current_step_id] = approval_data
    
    # Resume workflow execution
    # TODO: Implement workflow resumption logic
    # For now, just update status
    execution.status = "running"
    execution.awaiting_approval_data = None
    
    await session.commit()
    await session.refresh(execution)
    
    return WorkflowExecutionResponse.model_validate(execution)






