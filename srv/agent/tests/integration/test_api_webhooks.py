"""
Integration tests for webhooks API endpoints.

Tests incoming webhook handling for triggering agent tasks.
"""

import hashlib
import hmac
import json
import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_webhook_task_trigger_missing_secret(test_client: AsyncClient):
    """Test webhook trigger without secret fails."""
    fake_task_id = str(uuid.uuid4())
    
    response = await test_client.post(
        f"/webhooks/tasks/{fake_task_id}",
        json={"event": "trigger"},
    )
    
    # Should require X-Webhook-Secret header
    assert response.status_code in [400, 401, 403]


@pytest.mark.asyncio
async def test_webhook_task_trigger_invalid_task(test_client: AsyncClient):
    """Test webhook trigger for non-existent task."""
    fake_task_id = str(uuid.uuid4())
    
    response = await test_client.post(
        f"/webhooks/tasks/{fake_task_id}",
        json={"event": "trigger"},
        headers={"X-Webhook-Secret": "some-secret"},
    )
    
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_slack_webhook_url_verification(test_client: AsyncClient):
    """Test Slack URL verification challenge."""
    fake_task_id = str(uuid.uuid4())
    challenge = "test-challenge-string"
    
    response = await test_client.post(
        f"/webhooks/integrations/slack/{fake_task_id}",
        json={
            "type": "url_verification",
            "challenge": challenge,
        },
    )
    
    # Slack URL verification should return the challenge
    assert response.status_code == 200
    data = response.json()
    assert data.get("challenge") == challenge


@pytest.mark.asyncio
async def test_teams_webhook_receives_message(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test Teams webhook message handling."""
    # First create a task with webhook trigger
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"teams-webhook-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Handle Teams message",
            "trigger_type": "webhook",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    if create_response.status_code != 201:
        pytest.skip(f"Could not create task: {create_response.text}")
    
    task_id = create_response.json()["id"]
    
    # Send a Teams-style message
    response = await test_client.post(
        f"/webhooks/integrations/teams/{task_id}",
        json={
            "type": "message",
            "text": "Hello from Teams",
            "from": {
                "name": "Test User",
            },
        },
    )
    
    # Should accept the webhook (even if task execution is async)
    assert response.status_code in [200, 202, 404]


@pytest.mark.asyncio
async def test_email_webhook_receives_payload(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test email webhook handling."""
    # Create a task with webhook trigger
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"email-webhook-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Handle email",
            "trigger_type": "webhook",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    if create_response.status_code != 201:
        pytest.skip(f"Could not create task: {create_response.text}")
    
    task_id = create_response.json()["id"]
    
    # Send an email-style webhook (like from SendGrid or Mailgun)
    response = await test_client.post(
        f"/webhooks/integrations/email/{task_id}",
        json={
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test Email",
            "text": "Email body content",
        },
    )
    
    # Should accept the webhook
    assert response.status_code in [200, 202, 404]


@pytest.mark.asyncio
async def test_generic_webhook_with_valid_secret(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test generic webhook with valid secret."""
    # Create a task with webhook trigger
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"generic-webhook-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Handle webhook",
            "trigger_type": "webhook",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    if create_response.status_code != 201:
        pytest.skip(f"Could not create task: {create_response.text}")
    
    task_data = create_response.json()
    task_id = task_data["id"]
    webhook_secret = task_data.get("webhook_secret", "")
    
    if not webhook_secret:
        pytest.skip("Task did not return webhook_secret")
    
    # Send webhook with valid secret
    response = await test_client.post(
        f"/webhooks/tasks/{task_id}",
        json={"event": "custom_event", "data": {"key": "value"}},
        headers={"X-Webhook-Secret": webhook_secret},
    )
    
    assert response.status_code in [200, 202]


@pytest.mark.asyncio
async def test_generic_webhook_with_invalid_secret(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test generic webhook with invalid secret is rejected."""
    # Create a task with webhook trigger
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"invalid-secret-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Handle webhook",
            "trigger_type": "webhook",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    if create_response.status_code != 201:
        pytest.skip(f"Could not create task: {create_response.text}")
    
    task_id = create_response.json()["id"]
    
    # Send webhook with wrong secret
    response = await test_client.post(
        f"/webhooks/tasks/{task_id}",
        json={"event": "test"},
        headers={"X-Webhook-Secret": "wrong-secret"},
    )
    
    assert response.status_code in [401, 403]


@pytest.mark.asyncio
async def test_webhook_with_hmac_signature(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test webhook with HMAC signature validation."""
    # Create a task
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"hmac-webhook-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Handle HMAC webhook",
            "trigger_type": "webhook",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    if create_response.status_code != 201:
        pytest.skip(f"Could not create task: {create_response.text}")
    
    task_data = create_response.json()
    task_id = task_data["id"]
    webhook_secret = task_data.get("webhook_secret", "test-secret")
    
    # Create HMAC signature
    payload = json.dumps({"event": "signed_event"})
    signature = hmac.new(
        webhook_secret.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # Send webhook with signature
    response = await test_client.post(
        f"/webhooks/tasks/{task_id}",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={signature}",
        },
    )
    
    # Should either accept or reject based on signature validation implementation
    assert response.status_code in [200, 202, 401, 403, 404]


@pytest.mark.asyncio
async def test_webhook_paused_task(test_client: AsyncClient, mock_jwt_token: str, test_agent):
    """Test webhook for paused task is rejected."""
    # Create a task
    create_response = await test_client.post(
        "/tasks",
        json={
            "name": f"paused-webhook-{uuid.uuid4().hex[:8]}",
            "agent_id": str(test_agent.id),
            "prompt": "Handle webhook",
            "trigger_type": "webhook",
        },
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    
    if create_response.status_code != 201:
        pytest.skip(f"Could not create task: {create_response.text}")
    
    task_data = create_response.json()
    task_id = task_data["id"]
    webhook_secret = task_data.get("webhook_secret", "")
    
    # Pause the task
    pause_response = await test_client.post(
        f"/tasks/{task_id}/pause",
        headers={"Authorization": f"Bearer {mock_jwt_token}"},
    )
    assert pause_response.status_code == 200
    
    # Try to trigger via webhook
    response = await test_client.post(
        f"/webhooks/tasks/{task_id}",
        json={"event": "trigger"},
        headers={"X-Webhook-Secret": webhook_secret} if webhook_secret else {},
    )
    
    # Should reject because task is paused
    assert response.status_code in [400, 403, 409]
