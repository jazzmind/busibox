"""
Integration tests for evaluator CRUD operations (User Story 3).

Tests:
- GET /agents/evals/{eval_id} returns evaluator
- PUT /agents/evals/{eval_id} updates and increments version
- DELETE evaluator returns 204
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import EvalDefinition


@pytest.fixture
async def custom_eval_id(db_session: AsyncSession, mock_user_id: str) -> uuid.UUID:
    """Create a custom evaluator for testing."""
    evaluator = EvalDefinition(
        name="custom_test_eval",
        description="Custom evaluator for testing",
        config={
            "criteria": "Accuracy",
            "pass_threshold": 0.8,
            "model": "agent"
        },
        is_active=True,
        created_by=mock_user_id,
    )
    db_session.add(evaluator)
    await db_session.commit()
    await db_session.refresh(evaluator)
    return evaluator.id


@pytest.mark.asyncio
async def test_get_evaluator_by_id(
    client: AsyncClient,
    custom_eval_id: uuid.UUID,
    mock_token: str
):
    """
    Test: GET /agents/evals/{eval_id} returns evaluator.
    """
    response = await client.get(
        f"/agents/evals/{custom_eval_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    evaluator = response.json()
    assert evaluator["id"] == str(custom_eval_id)
    assert evaluator["name"] == "custom_test_eval"


@pytest.mark.asyncio
async def test_update_evaluator_increments_version(
    client: AsyncClient,
    custom_eval_id: uuid.UUID,
    mock_token: str
):
    """
    Test: PUT /agents/evals/{eval_id} updates and increments version.
    """
    # Get initial version
    response = await client.get(
        f"/agents/evals/{custom_eval_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    initial_version = response.json()["version"]
    
    # Update evaluator
    response = await client.put(
        f"/agents/evals/{custom_eval_id}",
        json={
            "description": "Updated evaluator description",
            "config": {
                "criteria": "Updated criteria",
                "pass_threshold": 0.9,
                "model": "agent"
            }
        },
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    updated_eval = response.json()
    
    assert updated_eval["description"] == "Updated evaluator description"
    assert updated_eval["config"]["pass_threshold"] == 0.9
    assert updated_eval["version"] == initial_version + 1


@pytest.mark.asyncio
async def test_delete_evaluator_returns_204(
    client: AsyncClient,
    custom_eval_id: uuid.UUID,
    mock_token: str,
    db_session: AsyncSession
):
    """
    Test: DELETE evaluator returns 204.
    """
    response = await client.delete(
        f"/agents/evals/{custom_eval_id}",
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 204
    
    # Verify evaluator is soft-deleted
    evaluator = await db_session.get(EvalDefinition, custom_eval_id)
    assert evaluator.is_active is False








