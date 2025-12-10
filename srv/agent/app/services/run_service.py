import asyncio
import logging
import uuid
from typing import Any, Dict

from pydantic_ai import Agent
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.core import BusiboxDeps
from app.clients.busibox import BusiboxClient
from app.models.domain import RunRecord
from app.schemas.auth import Principal
from app.services.agent_registry import agent_registry
from app.services.token_service import get_or_exchange_token

logger = logging.getLogger(__name__)

# Tiered execution limits (timeout in seconds)
AGENT_TIMEOUTS = {
    "simple": 30,
    "complex": 300,  # 5 minutes
    "batch": 1800,  # 30 minutes
}


def get_agent_timeout(agent_tier: str = "simple") -> int:
    """Get timeout for agent tier (Simple: 30s, Complex: 5min, Batch: 30min)"""
    return AGENT_TIMEOUTS.get(agent_tier, AGENT_TIMEOUTS["simple"])


async def create_run(
    session: AsyncSession,
    principal: Principal,
    agent_id: uuid.UUID,
    payload: Dict[str, Any],
    scopes: list[str],
    purpose: str,
    agent_tier: str = "simple",
) -> RunRecord:
    """
    Execute an agent run with error handling and tiered timeouts.
    
    Args:
        session: Database session
        principal: Authenticated user principal
        agent_id: Agent UUID to execute
        payload: Input payload with 'prompt' and other fields
        scopes: OAuth scopes for token exchange
        purpose: Token purpose for exchange
        agent_tier: Execution tier (simple/complex/batch) for timeout limits
    
    Returns:
        RunRecord with execution results
    """
    # Create run record
    run_record = RunRecord(
        agent_id=agent_id,
        status="running",
        input=payload,
        created_by=principal.sub,
        events=[],
    )
    session.add(run_record)
    await session.commit()
    await session.refresh(run_record)

    try:
        # Exchange token for downstream services
        token = await get_or_exchange_token(session, principal, scopes=scopes, purpose=purpose)
        client = BusiboxClient(token.access_token)
        
        # Get agent from registry
        try:
            agent: Agent[BusiboxDeps, object] = agent_registry.get(agent_id)
        except KeyError as e:
            logger.error(f"Agent {agent_id} not found in registry: {e}")
            run_record.status = "failed"
            run_record.output = {"error": f"Agent not found: {agent_id}"}
            await session.commit()
            await session.refresh(run_record)
            return run_record
        
        deps = BusiboxDeps(principal=principal, busibox_client=client)
        
        # Execute agent with timeout
        timeout = get_agent_timeout(agent_tier)
        logger.info(f"Executing agent {agent_id} with {timeout}s timeout (tier: {agent_tier})")
        
        try:
            result = await asyncio.wait_for(
                agent.run(payload.get("prompt", ""), deps=deps),
                timeout=timeout
            )
            
            # Success - extract output
            run_record.status = "succeeded"
            if hasattr(result, "output"):
                run_record.output = result.output
            elif hasattr(result, "data"):
                run_record.output = {"data": result.data}
            else:
                run_record.output = {"result": str(result)}
            
            logger.info(f"Agent {agent_id} run {run_record.id} succeeded")
            
        except asyncio.TimeoutError:
            logger.warning(f"Agent {agent_id} run {run_record.id} timed out after {timeout}s")
            run_record.status = "timeout"
            run_record.output = {
                "error": f"Execution exceeded {timeout}s timeout limit for {agent_tier} tier",
                "timeout": timeout,
                "tier": agent_tier
            }
        
        except Exception as e:
            # Tool call failure or agent execution error
            logger.error(f"Agent {agent_id} run {run_record.id} failed: {e}", exc_info=True)
            run_record.status = "failed"
            run_record.output = {
                "error": str(e),
                "error_type": type(e).__name__,
            }
    
    except Exception as e:
        # Token exchange or setup failure
        logger.error(f"Run {run_record.id} setup failed: {e}", exc_info=True)
        run_record.status = "failed"
        run_record.output = {
            "error": f"Setup failed: {str(e)}",
            "error_type": type(e).__name__,
        }
    
    # Persist final state
    await session.commit()
    await session.refresh(run_record)
    return run_record
