"""
Evaluator CRUD API endpoints.

Provides:
- Individual evaluator retrieval by ID
- Evaluator updates with version increment
- Evaluator soft deletion
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.domain import EvalDefinition
from app.schemas.auth import Principal
from app.schemas.definitions import EvalDefinitionRead, EvalDefinitionUpdate

router = APIRouter(prefix="/agents/evals", tags=["evaluators"])
logger = get_logger(__name__)


@router.get("/{eval_id}", response_model=EvalDefinitionRead)
async def get_evaluator(
    eval_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> EvalDefinitionRead:
    """
    Get individual evaluator by ID.
    
    Returns:
        Evaluator definition if found and active
        
    Raises:
        HTTPException: 404 if evaluator not found or inactive
    """
    evaluator = await session.get(EvalDefinition, eval_id)
    
    if not evaluator or not evaluator.is_active:
        logger.warning(
            "evaluator_not_found",
            eval_id=str(eval_id),
            user_id=principal.sub
        )
        raise HTTPException(status_code=404, detail="Evaluator not found")
    
    logger.info(
        "evaluator_retrieved",
        eval_id=str(eval_id),
        eval_name=evaluator.name,
        user_id=principal.sub
    )
    
    return EvalDefinitionRead.model_validate(evaluator)


@router.put("/{eval_id}", response_model=EvalDefinitionRead)
async def update_evaluator(
    eval_id: uuid.UUID,
    payload: EvalDefinitionUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> EvalDefinitionRead:
    """
    Update an evaluator definition.
    
    Version number increments automatically.
    
    Args:
        eval_id: Evaluator UUID
        payload: Fields to update
        principal: Authenticated user
        session: Database session
        
    Returns:
        Updated evaluator definition
        
    Raises:
        HTTPException:
            - 404 if evaluator not found
            - 400 if config validation fails
    """
    evaluator = await session.get(EvalDefinition, eval_id)
    
    if not evaluator or not evaluator.is_active:
        raise HTTPException(status_code=404, detail="Evaluator not found")
    
    # Check ownership
    if evaluator.created_by and evaluator.created_by != principal.sub:
        logger.warning(
            "evaluator_update_unauthorized",
            eval_id=str(eval_id),
            eval_name=evaluator.name,
            owner=evaluator.created_by,
            user_id=principal.sub
        )
        raise HTTPException(status_code=404, detail="Evaluator not found")
    
    # Update fields
    update_data = payload.model_dump(exclude_unset=True)
    
    # Validate config if provided
    if "config" in update_data:
        config = update_data["config"]
        required_fields = ["criteria", "pass_threshold", "model"]
        missing_fields = [f for f in required_fields if f not in config]
        if missing_fields:
            raise HTTPException(
                status_code=400,
                detail=f"Config missing required fields: {', '.join(missing_fields)}"
            )
        
        # Validate pass_threshold
        if not (0.0 <= config["pass_threshold"] <= 1.0):
            raise HTTPException(
                status_code=400,
                detail="Config pass_threshold must be between 0.0 and 1.0"
            )
    
    for key, value in update_data.items():
        setattr(evaluator, key, value)
    
    # Increment version and update timestamp
    evaluator.version += 1
    evaluator.updated_at = datetime.now(timezone.utc)
    
    await session.commit()
    await session.refresh(evaluator)
    
    logger.info(
        "evaluator_updated",
        eval_id=str(eval_id),
        eval_name=evaluator.name,
        new_version=evaluator.version,
        user_id=principal.sub,
        updated_fields=list(update_data.keys())
    )
    
    return EvalDefinitionRead.model_validate(evaluator)


@router.delete("/{eval_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evaluator(
    eval_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """
    Soft-delete an evaluator.
    
    Args:
        eval_id: Evaluator UUID
        principal: Authenticated user
        session: Database session
        
    Raises:
        HTTPException: 404 if evaluator not found
    """
    evaluator = await session.get(EvalDefinition, eval_id)
    
    if not evaluator or not evaluator.is_active:
        raise HTTPException(status_code=404, detail="Evaluator not found")
    
    # Check ownership
    if evaluator.created_by and evaluator.created_by != principal.sub:
        logger.warning(
            "evaluator_delete_unauthorized",
            eval_id=str(eval_id),
            eval_name=evaluator.name,
            owner=evaluator.created_by,
            user_id=principal.sub
        )
        raise HTTPException(status_code=404, detail="Evaluator not found")
    
    # Soft delete
    evaluator.is_active = False
    await session.commit()
    
    logger.info(
        "evaluator_deleted",
        eval_id=str(eval_id),
        eval_name=evaluator.name,
        user_id=principal.sub
    )





