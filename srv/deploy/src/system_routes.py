"""
System Management Routes for Busibox

Provides API endpoints for managing installation state, Docker services,
and system health. Protected by busibox-admin scope verification.
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import verify_admin_token
from .state import read_state, write_state, get_state_value
from .docker_manager import DockerManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


# =============================================================================
# Request/Response Models
# =============================================================================

class StateUpdateRequest(BaseModel):
    """Request to update installation state."""
    updates: dict


class ComposeUpRequest(BaseModel):
    """Request to start compose services."""
    services: Optional[List[str]] = None
    compose_file: str = "docker-compose.yml"


class ComposeDownRequest(BaseModel):
    """Request to stop compose services."""
    compose_file: str = "docker-compose.yml"
    remove_volumes: bool = False


class ServiceLogsRequest(BaseModel):
    """Request to get service logs."""
    lines: int = 100


# =============================================================================
# State Management Endpoints
# =============================================================================

@router.get("/state/setup-complete")
async def get_setup_complete_status():
    """
    Public endpoint to check if initial setup is complete.
    
    Used by middleware to decide whether to redirect to setup wizard.
    No authentication required - only returns boolean setup status.
    
    Returns setupComplete=true ONLY if SETUP_COMPLETE is explicitly set to "true".
    
    Note: INSTALL_PHASE tracks Ansible/infrastructure deployment, while
    SETUP_COMPLETE tracks the user-facing setup wizard (passkey registration,
    portal customization, etc.). The portal should only be accessible after
    SETUP_COMPLETE=true, regardless of INSTALL_PHASE.
    
    If state file is missing or empty, returns false to ensure setup is completed.
    """
    state = await read_state()
    
    # If state file is empty (doesn't exist), default to NOT complete
    # This ensures users must complete setup before accessing the portal
    if not state:
        logger.info("State file empty or missing, defaulting setupComplete to false (setup required)")
        return {"setupComplete": False}
    
    # ONLY check SETUP_COMPLETE flag - this is set by the setup wizard
    # after user completes passkey registration and portal configuration
    setup_complete = state.get("SETUP_COMPLETE") == "true"
    
    logger.debug(f"Setup complete check: SETUP_COMPLETE={state.get('SETUP_COMPLETE')}, returning {setup_complete}")
    
    return {
        "setupComplete": setup_complete,
    }


@router.get("/state")
async def get_install_state(token: dict = Depends(verify_admin_token)):
    """
    Read current installation state from .busibox-state file.
    
    Returns structured state information for the setup wizard.
    Note: Does NOT include raw state to avoid exposing secrets.
    """
    state = await read_state()
    
    return {
        "phase": state.get("INSTALL_PHASE", "bootstrap"),
        "status": state.get("INSTALL_STATUS", "unknown"),
        "environment": state.get("ENVIRONMENT"),
        "platform": state.get("PLATFORM"),
        "llmBackend": state.get("LLM_BACKEND"),
        "llmTier": state.get("LLM_TIER"),
        "adminEmail": state.get("ADMIN_EMAIL"),
        "baseDomain": state.get("BASE_DOMAIN"),
        "adminUserId": state.get("ADMIN_USER_ID"),
    }


@router.put("/state")
async def update_install_state(
    request: StateUpdateRequest,
    token: dict = Depends(verify_admin_token)
):
    """
    Update installation state.
    
    Merges provided updates with existing state.
    """
    try:
        await write_state(request.updates)
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to update state: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/state/{key}")
async def update_state_key(
    key: str,
    value: str,
    token: dict = Depends(verify_admin_token)
):
    """
    Update a single state key.
    """
    try:
        await write_state({key: value})
        return {"success": True, "key": key, "value": value}
    except Exception as e:
        logger.error(f"Failed to update state key {key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Service Management Endpoints
# =============================================================================

@router.get("/services")
async def list_services(token: dict = Depends(verify_admin_token)):
    """
    List all busibox services and their status.
    """
    manager = DockerManager()
    services = await manager.list_services()
    return {"services": services}


@router.get("/services/{service}")
async def get_service_status(
    service: str,
    token: dict = Depends(verify_admin_token)
):
    """
    Get status of a specific service.
    """
    manager = DockerManager()
    status = await manager.get_service_status(service)
    return status


@router.post("/services/{service}/start")
async def start_service(
    service: str,
    token: dict = Depends(verify_admin_token)
):
    """
    Start a specific service.
    """
    manager = DockerManager()
    result = await manager.start_service(service)
    
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to start service"))
    
    return result


@router.post("/services/{service}/stop")
async def stop_service(
    service: str,
    token: dict = Depends(verify_admin_token)
):
    """
    Stop a specific service.
    """
    manager = DockerManager()
    result = await manager.stop_service(service)
    
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to stop service"))
    
    return result


@router.post("/services/{service}/restart")
async def restart_service(
    service: str,
    token: dict = Depends(verify_admin_token)
):
    """
    Restart a specific service.
    """
    manager = DockerManager()
    result = await manager.restart_service(service)
    
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to restart service"))
    
    return result


@router.get("/services/{service}/logs")
async def get_service_logs(
    service: str,
    lines: int = 100,
    token: dict = Depends(verify_admin_token)
):
    """
    Get logs for a specific service.
    """
    manager = DockerManager()
    result = await manager.get_service_logs(service, lines=lines)
    
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to get logs"))
    
    return result


# =============================================================================
# Compose Management Endpoints
# =============================================================================

@router.post("/compose/up")
async def compose_up(
    request: ComposeUpRequest,
    token: dict = Depends(verify_admin_token)
):
    """
    Start services via docker compose.
    """
    manager = DockerManager()
    result = await manager.compose_up(
        services=request.services,
        compose_file=request.compose_file
    )
    
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to start services"))
    
    return result


@router.post("/compose/down")
async def compose_down(
    request: ComposeDownRequest,
    token: dict = Depends(verify_admin_token)
):
    """
    Stop and remove compose services.
    """
    manager = DockerManager()
    result = await manager.compose_down(
        compose_file=request.compose_file,
        remove_volumes=request.remove_volumes
    )
    
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to stop services"))
    
    return result


# =============================================================================
# Health Endpoints
# =============================================================================

@router.get("/health")
async def system_health():
    """
    Overall system health check.
    
    This endpoint is public for monitoring purposes.
    """
    manager = DockerManager()
    health = await manager.get_system_health()
    return health


@router.get("/health/detailed")
async def system_health_detailed(token: dict = Depends(verify_admin_token)):
    """
    Detailed system health with service information.
    
    Requires authentication.
    """
    manager = DockerManager()
    health = await manager.get_system_health()
    
    # Add state information
    state = await read_state()
    health["installState"] = {
        "phase": state.get("INSTALL_PHASE", "bootstrap"),
        "status": state.get("INSTALL_STATUS", "unknown"),
        "environment": state.get("ENVIRONMENT"),
    }
    
    return health
