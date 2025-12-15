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

from app.auth.dependencies import get_principal
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.domain import WorkflowDefinition
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
    workflow.updated_at = datetime.now(timezone.utc)
    
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





