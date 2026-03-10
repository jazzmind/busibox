"""
System Management Routes for Busibox

Provides API endpoints for managing installation state, Docker services,
and system health. Protected by busibox-admin scope verification.
"""

import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import verify_admin_token
from .state import read_state, write_state, get_state_value
from .docker_manager import DockerManager
from .core_app_executor import is_docker_environment, execute_ssh_command, execute_in_core_apps

logger = logging.getLogger(__name__)

# Core-app sub-services that run inside the core-apps container (Docker)
# or as separate systemd units (Proxmox). Maps service ID to app-manager name.
CORE_APP_SERVICES = {
    'busibox-portal': 'portal',
    'busibox-agents': 'agents',
    'busibox-appbuilder': 'appbuilder',
}

APP_MANAGER_PORT = 9999


# =============================================================================
# Proxmox Service Mapping
# =============================================================================
# Maps service names to (dns_hostname, systemd_service_name) for Proxmox/LXC environments
# DNS hostnames are resolved via /etc/hosts (set by internal_dns Ansible role)
PROXMOX_SERVICE_MAP = {
    'redis': ('redis', 'redis-server'),  # data-lxc
    'postgres': ('postgres', 'postgresql'),  # pg-lxc
    'milvus': ('milvus', 'milvus'),  # milvus-lxc
    'minio': ('minio', 'minio'),  # files-lxc
    'neo4j': ('neo4j', 'neo4j'),  # milvus-lxc (runs as Docker container)
    'litellm': ('litellm', 'litellm'),  # litellm-lxc
    'authz-api': ('authz-api', 'authz'),  # authz-lxc
    'authz': ('authz-api', 'authz'),  # alias
    'data-api': ('data-api', 'data-api'),  # data-lxc
    'data': ('data-api', 'data-api'),  # alias
    'data-worker': ('data-api', 'data-worker'),  # data-lxc
    'search-api': ('search-api', 'search-api'),  # milvus-lxc
    'search': ('search-api', 'search-api'),  # alias
    'agent-api': ('agent-api', 'agent-api'),  # agent-lxc
    'agent': ('agent-api', 'agent-api'),  # alias
    'embedding-api': ('embedding-api', 'embedding'),  # data-lxc
    'embedding': ('embedding-api', 'embedding'),  # alias
    'deploy-api': ('deploy-api', 'deploy-api'),  # authz-lxc
    'deploy': ('deploy-api', 'deploy-api'),  # alias
    'docs-api': ('docs-api', 'docs-api'),  # milvus-lxc
    'docs': ('docs-api', 'docs-api'),  # alias
    'nginx': ('nginx', 'nginx'),  # proxy-lxc
    'busibox-portal': ('busibox-portal', 'busibox-portal'),  # core-apps-lxc
    'busibox-agents': ('busibox-portal', 'busibox-agents'),  # core-apps-lxc (same host)
    'busibox-appbuilder': ('busibox-portal', 'busibox-appbuilder'),  # core-apps-lxc (same host)
}

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
    
    For Docker: Uses docker start, or app-manager for core-app sub-services.
    For Proxmox: Uses SSH + systemctl to start the service on the appropriate container.
    """
    if not is_docker_environment():
        return await _proxmox_service_action(service, "start")
    
    if service in CORE_APP_SERVICES:
        return await _core_app_action(service, "restart")
    
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
    
    For Docker: Uses docker stop, or app-manager for core-app sub-services.
    For Proxmox: Uses SSH + systemctl to stop the service on the appropriate container.
    """
    if not is_docker_environment():
        return await _proxmox_service_action(service, "stop")
    
    if service in CORE_APP_SERVICES:
        return await _core_app_action(service, "stop")
    
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
    
    For Docker: Uses docker restart, or app-manager for core-app sub-services.
    For Proxmox: Uses SSH + systemctl to restart the service on the appropriate container.
    """
    if not is_docker_environment():
        return await _proxmox_service_action(service, "restart")
    
    if service in CORE_APP_SERVICES:
        return await _core_app_action(service, "restart")
    
    manager = DockerManager()
    result = await manager.restart_service(service)
    
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to restart service"))
    
    return result


async def _proxmox_service_action(service: str, action: str) -> dict:
    """
    Execute a systemctl action on a Proxmox/LXC service via SSH.
    
    Args:
        service: Service name (e.g., "authz-api", "postgres").
        action: systemctl action ("start", "stop", "restart").
    
    Returns:
        Result dictionary with success status.
    
    Raises:
        HTTPException: If the service is unrecognized or the command fails.
    """
    if service not in PROXMOX_SERVICE_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Service '{service}' is not recognized. Available services: {', '.join(sorted(set(PROXMOX_SERVICE_MAP.keys())))}"
        )
    
    container_host, systemd_service = PROXMOX_SERVICE_MAP[service]
    
    try:
        command = f"systemctl {action} {systemd_service}"
        stdout, stderr, code = await execute_ssh_command(container_host, command, timeout=60)
        
        if code != 0:
            logger.error(f"Failed to {action} {service} via SSH: {stderr}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to {action} service: {stderr}"
            )
        
        return {
            "success": True,
            "service": service,
            "action": action,
            "host": container_host,
            "output": stdout,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SSH error during {action} for {service}: {e}")
        raise HTTPException(status_code=500, detail=f"SSH error: {str(e)}")


async def _core_app_action(service: str, action: str) -> dict:
    """
    Execute an action on a core-app sub-service via the app-manager.
    
    Core-app sub-services (busibox-portal, busibox-agents, busibox-appbuilder)
    run inside the core-apps container and are managed by the app-manager
    control API on port 9999. This function routes start/stop/restart actions
    through that API.
    
    Args:
        service: Service ID (e.g., "busibox-portal").
        action: Action to perform ("restart" or "stop").
    
    Returns:
        Result dictionary from the app-manager.
    """
    app_name = CORE_APP_SERVICES.get(service)
    if not app_name:
        raise HTTPException(status_code=400, detail=f"Unknown core-app service: {service}")
    
    body = json.dumps({"app": app_name})
    curl_cmd = f"curl -sf -X POST -d '{body}' -H 'Content-Type: application/json' http://localhost:{APP_MANAGER_PORT}/{action}"
    
    try:
        stdout, stderr, code = await execute_in_core_apps(curl_cmd, timeout=60)
        
        if code != 0:
            raise HTTPException(
                status_code=502,
                detail=f"app-manager unreachable: {stderr or stdout}",
            )
        
        try:
            result = json.loads(stdout)
            result["success"] = True
            return result
        except json.JSONDecodeError:
            return {"success": True, "output": stdout, "service": service, "action": action}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to {action} core-app {service}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to {action} {service}: {str(e)}")


@router.get("/services/{service}/logs")
async def get_service_logs(
    service: str,
    lines: int = 100,
    token: dict = Depends(verify_admin_token)
):
    """
    Get logs for a specific service.
    
    For Docker: Uses docker logs command.
    For Proxmox: Uses SSH + journalctl to fetch logs from the appropriate container.
    """
    # Check if we're on Proxmox/LXC (not Docker)
    if not is_docker_environment():
        # Proxmox: Use SSH + journalctl
        if service not in PROXMOX_SERVICE_MAP:
            raise HTTPException(
                status_code=400, 
                detail=f"Service '{service}' is not recognized. Available services: {', '.join(sorted(set(PROXMOX_SERVICE_MAP.keys())))}"
            )
        
        container_host, systemd_service = PROXMOX_SERVICE_MAP[service]
        
        try:
            # Use journalctl to get logs (--no-pager for non-interactive output)
            command = f"journalctl -u {systemd_service} -n {lines} --no-pager"
            stdout, stderr, code = await execute_ssh_command(container_host, command, timeout=30)
            
            if code != 0:
                logger.error(f"Failed to get logs for {service} via SSH: {stderr}")
                raise HTTPException(
                    status_code=500, 
                    detail=f"Failed to get logs: {stderr}"
                )
            
            return {"success": True, "logs": stdout, "service": service, "host": container_host}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"SSH error getting logs for {service}: {e}")
            raise HTTPException(status_code=500, detail=f"SSH error: {str(e)}")
    
    # Docker: Use DockerManager
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
