"""
Service Management Routes

API endpoints for starting/stopping Docker services and checking health.

Path Conventions:
-----------------
This service runs inside a Docker container with BUSIBOX_HOST_PATH mounted at itself:

BUSIBOX_HOST_PATH (e.g., /Users/wes/Code/busibox)
   - The absolute path on the HOST where busibox lives
   - Busibox is mounted at this SAME path inside the container
   - This ensures paths work for BOTH buildx (client-side) AND Docker daemon (server-side)
   - Must be set when starting the container (via Makefile or install script)

Example docker compose usage:
   docker compose -p dev-busibox \\
       -f $BUSIBOX_HOST_PATH/docker-compose.yml \\
       -f $BUSIBOX_HOST_PATH/docker-compose.local-dev.yml \\
       up -d embedding-api

The key insight: by mounting busibox at its actual host path, we avoid the mismatch
between buildx (which runs client-side and sees container filesystem) and Docker daemon
(which needs host paths for volume mounts).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
import subprocess
import httpx
import logging
import asyncio
import os
import json
import re
import yaml
from pydantic import BaseModel
from .auth import verify_admin_token
from .config import config
from .platform_detection import get_platform_info

# Import token exchange for agent-api calls
from busibox_common import exchange_token_zero_trust

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/services", tags=["services"])

# =============================================================================
# Model Registry Integration
# =============================================================================
# Loads model configuration from provision/ansible/group_vars/all/model_registry.yml
# Uses environment-aware model purposes (model_purposes_dev for dev, model_purposes for prod)

def load_model_registry(busibox_path: str = None) -> dict:
    """Load the model registry YAML file."""
    if busibox_path is None:
        busibox_path = os.getenv('BUSIBOX_HOST_PATH', '/busibox')
    
    registry_path = f"{busibox_path}/provision/ansible/group_vars/all/model_registry.yml"
    
    try:
        with open(registry_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning(f"Model registry not found at {registry_path}")
        return {}
    except Exception as e:
        logger.error(f"Failed to load model registry: {e}")
        return {}


def get_model_purposes(registry: dict, environment: str = None) -> dict:
    """Get the appropriate model_purposes based on environment.
    
    - development/demo: uses model_purposes_dev (MLX models)
    - staging/production: uses model_purposes (vLLM + Bedrock)
    """
    if environment is None:
        environment = os.getenv('ENVIRONMENT', os.getenv('NODE_ENV', 'development'))
    
    if environment in ('development', 'demo', 'dev'):
        purposes = registry.get('model_purposes_dev', {})
        if purposes:
            return purposes
    
    # Fallback to standard model_purposes
    return registry.get('model_purposes', {})


def resolve_model_name(registry: dict, model_key: str) -> tuple[str, dict]:
    """Resolve a model key to its full model_name and config.
    
    Returns (model_name, model_config) tuple.
    """
    available = registry.get('available_models', {})
    
    if model_key in available:
        config = available[model_key]
        return config.get('model_name', model_key), config
    
    # If not found, return the key as-is
    return model_key, {}


def generate_litellm_config_from_registry(
    registry: dict,
    environment: str = None,
    llm_backend: str = None,
    api_base: str = None
) -> str:
    """Generate LiteLLM config YAML from the model registry.
    
    Args:
        registry: Loaded model registry dict
        environment: development/staging/production
        llm_backend: mlx, vllm, or cloud
        api_base: API base URL for LLM requests
    
    Returns:
        LiteLLM config as YAML string
    """
    if llm_backend is None:
        llm_backend = os.getenv('LLM_BACKEND', 'mlx')
    
    if api_base is None:
        if llm_backend == 'mlx':
            api_base = 'http://host.docker.internal:8080/v1'
        elif llm_backend == 'vllm':
            api_base = 'http://vllm:8000/v1'
        else:
            api_base = None  # Cloud models don't need api_base
    
    purposes = get_model_purposes(registry, environment)
    available = registry.get('available_models', {})
    
    # Define which purposes map to LiteLLM model names
    # These are the model names that services request from LiteLLM
    litellm_purposes = ['test', 'fast', 'agent', 'chat', 'frontier', 'default', 'tool_calling']
    
    model_list = []
    
    for purpose in litellm_purposes:
        model_key = purposes.get(purpose)
        if not model_key:
            continue
        
        model_name, model_config = resolve_model_name(registry, model_key)
        provider = model_config.get('provider', 'mlx')
        
        # Build litellm_params based on provider
        litellm_params = {}
        
        if provider == 'bedrock':
            litellm_params['model'] = f"bedrock/{model_name}"
        elif provider in ('mlx', 'vllm'):
            litellm_params['model'] = f"openai/{model_name}"
            if api_base:
                litellm_params['api_base'] = api_base
                litellm_params['api_key'] = 'local'
        else:
            litellm_params['model'] = model_name
        
        model_entry = {
            'model_name': purpose,
            'litellm_params': litellm_params,
        }
        
        # Add model_info if we have a description
        description = model_config.get('description')
        if description:
            model_entry['model_info'] = {'description': description}
        
        model_list.append(model_entry)
    
    # Build the full config
    config = {
        'model_list': model_list,
        'general_settings': {
            'debug': True,
            'master_key': 'os.environ/LITELLM_MASTER_KEY',
        },
        'router_settings': {
            'enable_cache': True,
            'timeout': 120,
        },
        'litellm_settings': {
            'drop_params': True,
            'request_timeout': 120,
        },
    }
    
    # Generate YAML with header
    environment_str = environment or os.getenv('ENVIRONMENT', 'development')
    header = f"""# LiteLLM Configuration - Generated from model_registry.yml
# Environment: {environment_str}
# Backend: {llm_backend}
# DO NOT EDIT - regenerate via deploy-api or scripts/llm/generate-litellm-config.sh

"""
    
    return header + yaml.dump(config, default_flow_style=False, sort_keys=False)

# Docker Compose configuration from environment
COMPOSE_PROJECT_NAME = os.getenv("COMPOSE_PROJECT_NAME", "dev-busibox")
COMPOSE_FILES_STR = os.getenv("COMPOSE_FILES", "-f docker-compose.yml -f docker-compose.local-dev.yml")
# Parse compose files string into list
COMPOSE_FILES = COMPOSE_FILES_STR.split()

def get_docker_compose_base_cmd(busibox_host_path: str) -> list:
    """Build the base docker compose command with env file and compose files.
    
    This ensures all docker compose calls use the correct env file for the environment.
    """
    container_prefix = os.getenv('CONTAINER_PREFIX', 'dev')
    env_file = f'{busibox_host_path}/.env.{container_prefix}'
    
    return [
        'docker', 'compose',
        '-p', COMPOSE_PROJECT_NAME,
        '--env-file', env_file,
        '-f', f'{busibox_host_path}/docker-compose.yml',
        '-f', f'{busibox_host_path}/docker-compose.local-dev.yml'
    ]

# Host Agent configuration (for MLX control on Apple Silicon)
# The host-agent runs on the host machine and is accessible via host.docker.internal
HOST_AGENT_URL = os.getenv("HOST_AGENT_URL", "http://host.docker.internal:8089")
HOST_AGENT_TOKEN = os.getenv("HOST_AGENT_TOKEN", "")

# Lock mechanism to prevent concurrent deployments of the same service
# Maps service name to asyncio.Lock
_deployment_locks: dict[str, asyncio.Lock] = {}
_locks_lock = asyncio.Lock()  # Lock for accessing the _deployment_locks dict itself
_deploying_services: set[str] = set()  # Track which services are currently being deployed

async def get_deployment_lock(service: str) -> asyncio.Lock:
    """Get or create a lock for a specific service deployment."""
    async with _locks_lock:
        if service not in _deployment_locks:
            _deployment_locks[service] = asyncio.Lock()
        return _deployment_locks[service]

async def is_service_deploying(service: str) -> bool:
    """Check if a service is currently being deployed (thread-safe)."""
    async with _locks_lock:
        return service in _deploying_services

async def mark_service_deploying(service: str, is_deploying: bool):
    """Mark a service as deploying or not deploying (thread-safe)."""
    async with _locks_lock:
        if is_deploying:
            _deploying_services.add(service)
        else:
            _deploying_services.discard(service)


class StartServiceRequest(BaseModel):
    service: str


class HealthCheckRequest(BaseModel):
    service: str
    endpoint: str = '/health'
    bustCache: bool = False  # Ignored by backend, but accepted from frontend


@router.post("/start")
async def start_service(
    request: StartServiceRequest,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Start a Docker Compose service.
    
    Requires admin authentication.
    """
    service = request.service
    
    # Validate service name (security)
    if not service or not all(c.isalnum() or c in '-_' for c in service):
        raise HTTPException(status_code=400, detail="Invalid service name")
    
    logger.info(f"Starting service: {service}")
    
    # Check if already deploying (thread-safe check)
    if await is_service_deploying(service):
        logger.warning(f"Service {service} is already being deployed - rejecting duplicate request")
        raise HTTPException(
            status_code=409,
            detail=f"Service {service} is already being deployed. Please wait for the current deployment to complete."
        )
    
    # Acquire lock for this service to prevent concurrent deployments
    lock = await get_deployment_lock(service)
    
    async with lock:
        try:
            # Mark as deploying
            await mark_service_deploying(service, True)
            
            # Check platform - if MLX backend and service is vllm, start MLX instead
            platform_info = get_platform_info()
            backend = platform_info.get("backend", "cloud")
            
            if service == "vllm" and backend == "mlx":
                # Start MLX server via host-agent (runs on host, not in Docker)
                logger.info("Starting MLX via host-agent...")
                
                # Call host-agent to start MLX
                headers = {}
                if HOST_AGENT_TOKEN:
                    headers["Authorization"] = f"Bearer {HOST_AGENT_TOKEN}"
                
                try:
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            f"{HOST_AGENT_URL}/mlx/start",
                            json={"model_type": "agent"},
                            headers=headers,
                            timeout=30.0,  # Initial response timeout
                        )
                        
                        if response.status_code == 200:
                            logger.info("MLX start initiated via host-agent")
                            return {
                                "success": True,
                                "service": service,
                                "backend": "mlx",
                                "message": "MLX server start initiated via host-agent",
                            }
                        else:
                            error_msg = f"Host-agent returned {response.status_code}"
                            try:
                                error_data = response.json()
                                error_msg = error_data.get("detail", error_msg)
                            except:
                                pass
                            logger.error(f"Failed to start MLX via host-agent: {error_msg}")
                            raise HTTPException(status_code=500, detail=error_msg)
                            
                except httpx.ConnectError:
                    logger.error("Host-agent not reachable - is it running?")
                    raise HTTPException(
                        status_code=503,
                        detail="Host-agent not reachable. Start it with: scripts/host-agent/install-host-agent.sh"
                    )
                except httpx.TimeoutException:
                    logger.error("Host-agent request timed out")
                    raise HTTPException(
                        status_code=504,
                        detail="Host-agent request timed out"
                    )
            
            # Regular Docker service deployment
            # Use docker compose to start the service
            # BUSIBOX_HOST_PATH is the actual host path, and busibox is mounted at this same path
            # inside the container, so buildx can access files and Docker daemon gets correct paths
            busibox_host_path = os.getenv('BUSIBOX_HOST_PATH')
            
            if not busibox_host_path:
                logger.error("BUSIBOX_HOST_PATH not set - cannot start services with volume mounts")
                raise HTTPException(
                    status_code=500,
                    detail="BUSIBOX_HOST_PATH environment variable not set. Restart with 'make docker-up' or set BUSIBOX_HOST_PATH."
                )
            
            # Use explicit file paths with host path - busibox is mounted at BUSIBOX_HOST_PATH
            cmd = get_docker_compose_base_cmd(busibox_host_path)
            
            # vllm requires the demo-vllm profile
            if service == 'vllm':
                cmd.extend(['--profile', 'demo-vllm'])
            
            # Some services require multiple containers to be started together
            # Map logical service names to actual container(s) to start
            service_groups = {
                'ingest-api': ['ingest-api', 'ingest-worker'],  # Ingest needs both API and worker
            }
            services_to_start = service_groups.get(service, [service])
            
            # --no-deps: don't restart dependent services (avoids breaking already-running services)
            cmd.extend(['up', '-d', '--no-deps'] + services_to_start)
            logger.info(f"Running command: {' '.join(cmd)}")
            
            env = os.environ.copy()
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
                cwd=busibox_host_path,
            )
            
            if result.returncode != 0:
                logger.error(f"Failed to start {service}: {result.stderr}")
                # Don't fail completely - some services might have dependencies
                # Just log and return success so setup can continue
                logger.warning(f"Service {service} start returned non-zero but continuing")
            
            # Ensure service is connected to busibox network
            # This is necessary because some services may not be automatically connected
            try:
                network_name = f"{COMPOSE_PROJECT_NAME}-net"
                container_name = f"{os.getenv('CONTAINER_PREFIX', 'dev')}-{service}"
                
                # Check if already connected
                check_cmd = ['docker', 'network', 'inspect', network_name, '--format', '{{range .Containers}}{{.Name}} {{end}}']
                check_result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=5)
                
                if container_name not in check_result.stdout:
                    logger.info(f"Connecting {container_name} to network {network_name}")
                    connect_cmd = ['docker', 'network', 'connect', network_name, container_name]
                    connect_result = subprocess.run(connect_cmd, capture_output=True, text=True, timeout=5)
                    
                    if connect_result.returncode == 0:
                        logger.info(f"Successfully connected {container_name} to {network_name}")
                    else:
                        # Don't fail if already connected (error code 1 with "already connected" message)
                        if "already connected" not in connect_result.stderr.lower():
                            logger.warning(f"Failed to connect to network: {connect_result.stderr}")
                else:
                    logger.debug(f"{container_name} already connected to {network_name}")
            except Exception as e:
                logger.warning(f"Error connecting {service} to network: {e}")
            
            logger.info(f"Service {service} start command executed")
            return {
                "success": True,
                "service": service,
                "message": f"Service {service} start initiated",
                "output": result.stdout if result.stdout else None,
            }
        except subprocess.TimeoutExpired:
            logger.error(f"Service {service} start timeout")
            # Return success anyway to allow setup to continue
            return {
                "success": True,
                "service": service,
                "message": f"Service {service} start timeout (may still be starting)",
            }
        except Exception as e:
            logger.error(f"Error starting service {service}: {e}")
            # Return success anyway to allow setup to continue
            return {
                "success": True,
                "service": service,
                "message": f"Service {service} start error: {str(e)}",
            }
        finally:
            # Always unmark service when done (success or failure)
            await mark_service_deploying(service, False)


@router.post("/health")
async def check_service_health(
    request: HealthCheckRequest,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Check if a service is healthy using the same method as install script.
    
    For each service:
    1. Check if container is running using docker compose ps
    2. For services with health endpoints, try HTTP health check
    3. For PostgreSQL, use pg_isready
    4. For Redis and other services without HTTP endpoints, just check container status
    
    Requires admin authentication.
    """
    service = request.service
    endpoint = request.endpoint or '/health'
    
    logger.info(f"Checking health for {service}")
    
    busibox_host_path = os.getenv('BUSIBOX_HOST_PATH', '/busibox')
    
    try:
        # Step 1: Check if container is running (same as install script)
        cmd = get_docker_compose_base_cmd(busibox_host_path) + ['ps', '--status', 'running', '-q', service]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=busibox_host_path,
        )
        
        container_running = bool(result.stdout.strip())
        
        if not container_running:
            logger.info(f"Container {service} is not running")
            return {
                "healthy": False,
                "service": service,
                "reason": "container_not_running",
            }
        
        container_id = result.stdout.strip()
        logger.info(f"Container {service} is running (ID: {container_id[:12]})")
        
        # Step 2: Service-specific health checks (matching install script)
        
        # PostgreSQL: Use pg_isready (same as install script)
        if service == 'postgres':
            pg_result = subprocess.run(
                ['docker', 'exec', container_id, 'pg_isready', '-U', 'postgres'],
                capture_output=True,
                text=True,
            )
            healthy = pg_result.returncode == 0
            logger.info(f"PostgreSQL pg_isready: {healthy}")
            return {
                "healthy": healthy,
                "service": service,
                "reason": "pg_isready" if healthy else "pg_not_ready",
            }
        
        # Redis: Just check if container is running (no HTTP endpoint)
        if service == 'redis':
            return {
                "healthy": True,
                "service": service,
                "reason": "container_running",
            }
        
        # MinIO: Just check if container is running (like status script does)
        # The container has a healthcheck, so if it's running, it's healthy
        if service == 'minio':
            return {
                "healthy": container_running,
                "service": service,
                "reason": "container_running",
            }
        
        # Milvus: Just check if container is running (like status script does)
        # The container has a healthcheck, so if it's running, it's healthy
        if service == 'milvus':
            return {
                "healthy": container_running,
                "service": service,
                "reason": "container_running",
            }
        
        # vLLM/MLX: Check based on platform
        if service == 'vllm':
            platform_info = get_platform_info()
            backend = platform_info.get("backend", "cloud")
            
            if backend == "mlx":
                # MLX runs on host - check via host-agent first, fallback to direct check
                headers = {}
                if HOST_AGENT_TOKEN:
                    headers["Authorization"] = f"Bearer {HOST_AGENT_TOKEN}"
                
                try:
                    # Try host-agent status endpoint
                    async with httpx.AsyncClient() as client:
                        response = await client.get(
                            f"{HOST_AGENT_URL}/mlx/status",
                            headers=headers,
                            timeout=5.0,
                        )
                        if response.status_code == 200:
                            status_data = response.json()
                            return {
                                "healthy": status_data.get("healthy", False),
                                "service": service,
                                "backend": "mlx",
                                "running": status_data.get("running", False),
                                "model": status_data.get("model"),
                                "reason": "host_agent_status",
                            }
                except Exception as e:
                    logger.warning(f"Host-agent status check failed: {e}")
                
                # Fallback: direct MLX health check
                mlx_urls = [
                    "http://host.docker.internal:8080/v1/models",  # Docker Desktop
                    "http://localhost:8080/v1/models",  # Direct host access
                ]
                
                for url in mlx_urls:
                    try:
                        async with httpx.AsyncClient() as client:
                            response = await client.get(url, timeout=5.0)
                            if response.status_code == 200:
                                return {
                                    "healthy": True,
                                    "service": service,
                                    "backend": "mlx",
                                    "url": url,
                                    "reason": "mlx_direct_check",
                                }
                    except Exception:
                        continue  # Try next URL
                
                # All checks failed
                logger.warning(f"MLX health check failed for all methods")
                return {
                    "healthy": False,
                    "service": service,
                    "backend": "mlx",
                    "reason": "mlx_not_responding",
                }
            else:
                # vLLM in Docker - check container
                return {
                    "healthy": container_running,
                    "service": service,
                    "backend": "vllm",
                    "reason": "container_running",
                }
        
        # Services with HTTP health endpoints
        # Map service names to their internal ports and health endpoints
        # These must match the docker-compose.yml healthcheck configurations
        health_checks = {
            'litellm': {'port': 4000, 'endpoint': '/health/liveliness'},  # /health requires auth, /health/liveliness doesn't
            'embedding-api': {'port': 8005, 'endpoint': '/health'},
            'vllm': {'port': 8000, 'endpoint': '/health'},
            'ingest-api': {'port': 8002, 'endpoint': '/health'},
            'search-api': {'port': 8003, 'endpoint': '/'},  # Uses root endpoint per docker-compose healthcheck
            'agent-api': {'port': 8000, 'endpoint': '/health'},  # Port 8000, not 4111
            'docs-api': {'port': 8004, 'endpoint': '/health/live'},  # Uses /health/live per docker-compose
            'authz-api': {'port': 8010, 'endpoint': '/health/live'},
        }
        
        if service in health_checks:
            check_config = health_checks[service]
            # Use the endpoint from request if provided, otherwise use default
            health_endpoint = endpoint if endpoint != '/health' else check_config['endpoint']
            port = check_config['port']
            
            # Try HTTP health check (same as install script uses curl)
            url = f"http://{service}:{port}{health_endpoint}"
            logger.info(f"Checking HTTP health endpoint: {url}")
            
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=5.0)
                    healthy = response.status_code == 200
                    logger.info(f"HTTP health check for {service}: {healthy} (status: {response.status_code})")
                    return {
                        "healthy": healthy,
                        "service": service,
                        "url": url,
                        "status_code": response.status_code,
                        "reason": "http_health_check",
                    }
            except httpx.TimeoutException:
                logger.warning(f"Health check timeout for {service} at {url}")
                return {
                    "healthy": False,
                    "service": service,
                    "url": url,
                    "error": "timeout",
                    "reason": "http_timeout",
                }
            except Exception as e:
                logger.warning(f"HTTP health check failed for {service}: {e}")
                return {
                    "healthy": False,
                    "service": service,
                    "url": url,
                    "error": str(e),
                    "reason": "http_error",
                }
        
        # Unknown service - just check if container is running
        logger.info(f"Unknown service {service}, checking container status only")
        return {
            "healthy": container_running,
            "service": service,
            "reason": "container_check_only",
        }
        
    except Exception as e:
        logger.error(f"Health check error for {service}: {e}")
        return {
            "healthy": False,
            "service": service,
            "error": str(e),
            "reason": "check_error",
        }


@router.get("/status")
async def get_services_status(
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Get status of all Docker Compose services.
    """
    busibox_host_path = os.getenv('BUSIBOX_HOST_PATH', '/busibox')
    
    try:
        cmd = get_docker_compose_base_cmd(busibox_host_path) + ['ps', '--format', 'json']
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=busibox_host_path,
        )
        
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to get service status")
        
        import json
        services = []
        for line in result.stdout.strip().split('\n'):
            if line:
                services.append(json.loads(line))
        
        return {
            "services": services,
            "total": len(services),
        }
    except Exception as e:
        logger.error(f"Error getting service status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/platform")
async def get_platform_info_endpoint(
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Get platform information (backend, tier, memory).
    
    Used by AI Portal to determine which LLM runtime to use (MLX vs vLLM).
    """
    try:
        platform_info = get_platform_info()
        return platform_info
    except Exception as e:
        logger.error(f"Error getting platform info: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/start/{service}")
async def start_service_sse(
    service: str,
    request: Request,
):
    """
    SSE endpoint for starting a Docker Compose service with real-time output.
    
    Streams docker compose output line-by-line to the client via Server-Sent Events.
    Query params: token (required for auth)
    """
    logger.info(f"[SSE] Received request to start service: {service}")
    
    # Get token from query params (EventSource doesn't support custom headers)
    token = request.query_params.get('token')
    logger.info(f"[SSE] Token present: {bool(token)}")
    if not token:
        async def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Authentication required. Pass token as query parameter.', 'done': True})}\n\n"
        return StreamingResponse(
            error_gen(),
            media_type="text/event-stream",
            status_code=200,  # Return 200 so EventSource doesn't fail, error is in SSE format
        )
    
    # Verify token manually
    try:
        from .auth import verify_token
        token_payload = verify_token(token)
    except HTTPException as e:
        logger.error(f"[SSE] Token verification failed: {e.detail}")
        async def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'message': f'Authentication failed: {e.detail}', 'done': True})}\n\n"
        return StreamingResponse(
            error_gen(),
            media_type="text/event-stream",
            status_code=200,  # Return 200 so EventSource doesn't fail, error is in SSE format
        )
    except Exception as e:
        logger.error(f"[SSE] Token verification error: {e}", exc_info=True)
        async def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Authentication failed', 'done': True})}\n\n"
        return StreamingResponse(
            error_gen(),
            media_type="text/event-stream",
            status_code=200,  # Return 200 so EventSource doesn't fail, error is in SSE format
        )
    
    # Check for admin role
    roles = token_payload.get('roles', [])
    is_admin = any(
        (r.get('name') if isinstance(r, dict) else r) == 'Admin' 
        for r in roles
    ) if isinstance(roles, list) else False
    
    if not is_admin:
        logger.warning(f"[SSE] Non-admin user attempted to start service: {service}")
        async def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Admin role required', 'done': True})}\n\n"
        return StreamingResponse(
            error_gen(),
            media_type="text/event-stream",
            status_code=200,  # Return 200 so EventSource doesn't fail, error is in SSE format
        )
    async def event_generator():
        # Check if already deploying (thread-safe check)
        if await is_service_deploying(service):
            logger.warning(f"[SSE] Service {service} is already being deployed - rejecting duplicate request")
            yield f"data: {json.dumps({'type': 'error', 'message': f'Service {service} is already being deployed. Please wait for the current deployment to complete.', 'done': True})}\n\n"
            return
        
        # Acquire lock for this service to prevent concurrent deployments
        lock = await get_deployment_lock(service)
        
        async with lock:
            try:
                # Mark as deploying
                await mark_service_deploying(service, True)
                # Validate service name
                if not service or not all(c.isalnum() or c in '-_' for c in service):
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Invalid service name', 'done': True})}\n\n"
                    return
                
                logger.info(f"[SSE] Starting service: {service}")
                
                # Check platform - if MLX backend and service is vllm, start MLX instead
                platform_info = get_platform_info()
                backend = platform_info.get("backend", "cloud")
                
                if service == "vllm" and backend == "mlx":
                    # Start MLX server via host-agent (runs on host, not in Docker)
                    yield f"data: {json.dumps({'type': 'info', 'message': 'Starting MLX server for Apple Silicon via host-agent...'})}\n\n"
                    tier = platform_info.get('tier', 'unknown')
                    ram_gb = platform_info.get('ram_gb', 'unknown')
                    yield f"data: {json.dumps({'type': 'info', 'message': f'Platform: {tier} tier, {ram_gb}GB RAM'})}\n\n"
                    
                    # Connect to host-agent SSE endpoint
                    headers = {}
                    if HOST_AGENT_TOKEN:
                        headers["Authorization"] = f"Bearer {HOST_AGENT_TOKEN}"
                    
                    try:
                        async with httpx.AsyncClient() as client:
                            # Stream from host-agent
                            async with client.stream(
                                "POST",
                                f"{HOST_AGENT_URL}/mlx/start",
                                json={"model_type": "agent"},
                                headers=headers,
                                timeout=httpx.Timeout(10.0, read=300.0),  # 5 min read timeout
                            ) as response:
                                if response.status_code != 200:
                                    error_msg = f"Host-agent returned {response.status_code}"
                                    yield f"data: {json.dumps({'type': 'error', 'message': error_msg, 'done': True})}\n\n"
                                    return
                                
                                # Forward SSE events from host-agent
                                async for line in response.aiter_lines():
                                    if line.startswith("data: "):
                                        # Forward the SSE data directly
                                        yield f"{line}\n\n"
                                        # Check if done
                                        try:
                                            data = json.loads(line[6:])
                                            if data.get("done"):
                                                return
                                        except:
                                            pass
                                    elif line:
                                        # Non-SSE line, wrap it
                                        yield f"data: {json.dumps({'type': 'log', 'message': line})}\n\n"
                                        
                    except httpx.ConnectError:
                        yield f"data: {json.dumps({'type': 'error', 'message': 'Host-agent not reachable. Start it with: scripts/host-agent/install-host-agent.sh', 'done': True})}\n\n"
                        return
                    except httpx.TimeoutException:
                        yield f"data: {json.dumps({'type': 'error', 'message': 'Host-agent request timed out', 'done': True})}\n\n"
                        return
                    except Exception as e:
                        logger.error(f"[SSE] Error communicating with host-agent: {e}")
                        yield f"data: {json.dumps({'type': 'error', 'message': f'Host-agent error: {str(e)}', 'done': True})}\n\n"
                        return
                    
                    yield f"data: {json.dumps({'type': 'success', 'message': 'MLX server started via host-agent', 'done': True})}\n\n"
                    return
                
                # Regular Docker service deployment
                yield f"data: {json.dumps({'type': 'info', 'message': f'Starting {service}...'})}\n\n"
                
                # Start docker compose with real-time output
                # Pass environment variables so docker compose can validate all services
                env = os.environ.copy()
                
                # Check if GITHUB_AUTH_TOKEN is available (needed for core-apps service validation)
                github_token = env.get('GITHUB_AUTH_TOKEN')
                if not github_token:
                    logger.warning("[SSE] GITHUB_AUTH_TOKEN not found in environment - docker compose may fail if core-apps is referenced")
                    # Try to get it from the compose file's context if available
                    # For now, we'll let docker compose fail with a clear error
                else:
                    logger.info("[SSE] GITHUB_AUTH_TOKEN found in environment")
                
                # Build docker compose command
                # Get host path - busibox is mounted at this same path inside the container
                # This allows buildx to find files and Docker to mount volumes correctly
                busibox_host_path = os.getenv('BUSIBOX_HOST_PATH')
                if not busibox_host_path:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'BUSIBOX_HOST_PATH not set. Restart deploy-api with make docker-up.', 'done': True})}\n\n"
                    return
                
                # Use explicit file paths - busibox is mounted at BUSIBOX_HOST_PATH inside container
                # This ensures:
                # 1. Buildx can access files (it runs on client side, sees container filesystem)
                # 2. Docker daemon gets correct host paths for volume mounts
                # 3. Relative paths in compose files resolve correctly
                compose_cmd = get_docker_compose_base_cmd(busibox_host_path)
                
                # vllm requires the demo-vllm profile
                if service == 'vllm':
                    compose_cmd.extend(['--profile', 'demo-vllm'])
                    yield f"data: {json.dumps({'type': 'info', 'message': 'Note: vLLM requires NVIDIA GPU. On Apple Silicon, use MLX instead (runs on host).'})}\n\n"
                
                # Services that have critical infrastructure dependencies that must be started
                # (etcd, milvus-minio for milvus; minio for files; etc.)
                services_with_infra_deps = {'milvus', 'minio', 'postgres'}
                
                # Some services require multiple containers to be started together
                # Map logical service names to actual container(s) to start
                service_groups = {
                    'ingest-api': ['ingest-api', 'ingest-worker'],  # Ingest needs both API and worker
                }
                services_to_start = service_groups.get(service, [service])
                
                # For services with infra deps, let docker compose start dependencies
                # For API services, use --no-deps to avoid restarting already-running services
                if service in services_with_infra_deps:
                    compose_cmd.extend(['up', '-d'] + services_to_start)
                else:
                    compose_cmd.extend(['up', '-d', '--no-deps'] + services_to_start)
                
                process = await asyncio.create_subprocess_exec(
                    *compose_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    # cwd must be the busibox directory - mounted at BUSIBOX_HOST_PATH
                    cwd=busibox_host_path,
                )
                
                # Stream stdout and stderr using a queue
                queue = asyncio.Queue()
                
                async def read_stream(stream, stream_type):
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        message = line.decode('utf-8', errors='replace').rstrip()
                        if message:
                            await queue.put({
                                'type': 'log',
                                'stream': stream_type,
                                'message': message
                            })
                    await queue.put(None)  # Sentinel to signal done
                
                # Start reading both streams
                stdout_task = asyncio.create_task(read_stream(process.stdout, "stdout"))
                stderr_task = asyncio.create_task(read_stream(process.stderr, "stderr"))
                
                # Yield messages from queue
                done_count = 0
                while done_count < 2:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=0.1)
                        if msg is None:
                            done_count += 1
                        else:
                            yield f"data: {json.dumps(msg)}\n\n"
                    except asyncio.TimeoutError:
                        # Check if tasks are done
                        if stdout_task.done() and stderr_task.done():
                            break
                        continue
                
                # Wait for process to complete
                returncode = await process.wait()
                
                if returncode == 0:
                    yield f"data: {json.dumps({'type': 'success', 'message': f'Service {service} started successfully'})}\n\n"
                    
                    # Ensure service is connected to busibox network for inter-service communication
                    try:
                        network_name = f"{COMPOSE_PROJECT_NAME}-net"  # e.g., dev-busibox-net
                        container_prefix = os.getenv('CONTAINER_PREFIX', 'dev')
                        container_name = f"{container_prefix}-{service}"
                        
                        # Check if already connected
                        check_cmd = ['docker', 'network', 'inspect', network_name, '--format', '{{range .Containers}}{{.Name}} {{end}}']
                        check_result = await asyncio.create_subprocess_exec(
                            *check_cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, _ = await check_result.communicate()
                        
                        if container_name not in stdout.decode():
                            logger.info(f"[SSE] Connecting {container_name} to network {network_name}")
                            connect_cmd = ['docker', 'network', 'connect', network_name, container_name]
                            connect_result = await asyncio.create_subprocess_exec(
                                *connect_cmd,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                            )
                            _, stderr = await connect_result.communicate()
                            
                            if connect_result.returncode == 0:
                                logger.info(f"[SSE] Successfully connected {container_name} to {network_name}")
                                yield f"data: {json.dumps({'type': 'info', 'message': f'Connected {service} to network'})}\n\n"
                            else:
                                if "already connected" not in stderr.decode().lower():
                                    logger.warning(f"[SSE] Failed to connect to network: {stderr.decode()}")
                        else:
                            logger.debug(f"[SSE] {container_name} already connected to {network_name}")
                    except Exception as e:
                        logger.warning(f"[SSE] Error connecting {service} to network: {e}")
                    
                    # Start init container if one exists for this service
                    # Docker Compose's depends_on with condition: service_healthy will wait for the service to be healthy
                    init_containers = {
                        'minio': 'minio-init',
                        'milvus': 'milvus-init',
                    }
                    
                    if service in init_containers:
                        init_service = init_containers[service]
                        yield f"data: {json.dumps({'type': 'info', 'message': f'Starting init container {init_service}...'})}\n\n"
                        
                        # Remove existing init container if it exists (to avoid name conflicts)
                        rm_process = await asyncio.create_subprocess_exec(
                            'docker', 'compose', 
                            '-p', COMPOSE_PROJECT_NAME,
                            '-f', f'{busibox_host_path}/docker-compose.yml',
                            '-f', f'{busibox_host_path}/docker-compose.local-dev.yml',
                            'rm', '-f', init_service,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=env,
                            cwd=busibox_host_path,
                        )
                        await rm_process.wait()  # Don't care about return code - container might not exist
                        
                        # Start init container (depends_on will ensure service is healthy first)
                        # Use --force-recreate to ensure we get a fresh container
                        init_process = await asyncio.create_subprocess_exec(
                            'docker', 'compose',
                            '-p', COMPOSE_PROJECT_NAME,
                            '-f', f'{busibox_host_path}/docker-compose.yml',
                            '-f', f'{busibox_host_path}/docker-compose.local-dev.yml',
                            'up', '--no-deps', '--force-recreate', init_service,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=env,
                            cwd=busibox_host_path,
                        )
                        
                        # Stream init container output
                        init_queue = asyncio.Queue()
                        
                        async def read_init_stream(stream, stream_type):
                            while True:
                                line = await stream.readline()
                                if not line:
                                    break
                                message = line.decode('utf-8', errors='replace').rstrip()
                                if message:
                                    await init_queue.put({
                                        'type': 'log',
                                        'stream': stream_type,
                                        'message': f'[{init_service}] {message}'
                                    })
                            await init_queue.put(None)
                        
                        init_stdout_task = asyncio.create_task(read_init_stream(init_process.stdout, "stdout"))
                        init_stderr_task = asyncio.create_task(read_init_stream(init_process.stderr, "stderr"))
                        
                        init_done_count = 0
                        while init_done_count < 2:
                            try:
                                msg = await asyncio.wait_for(init_queue.get(), timeout=0.1)
                                if msg is None:
                                    init_done_count += 1
                                else:
                                    yield f"data: {json.dumps(msg)}\n\n"
                            except asyncio.TimeoutError:
                                if init_stdout_task.done() and init_stderr_task.done():
                                    break
                                continue
                        
                        init_returncode = await init_process.wait()
                        if init_returncode == 0:
                            yield f"data: {json.dumps({'type': 'success', 'message': f'Init container {init_service} completed successfully', 'done': True})}\n\n"
                        else:
                            yield f"data: {json.dumps({'type': 'warning', 'message': f'Init container {init_service} completed with code {init_returncode}', 'done': True})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'success', 'message': f'Service {service} started successfully', 'done': True})}\n\n"
                else:
                    error_msg = f'Service {service} failed to start (exit code {returncode})'
                    # Check if it's a GITHUB_AUTH_TOKEN error
                    if not github_token:
                        error_msg += '. GITHUB_AUTH_TOKEN is missing - restart deploy-api container after setting it in docker-compose.yml'
                    yield f"data: {json.dumps({'type': 'error', 'message': error_msg, 'done': True})}\n\n"
                
            except Exception as e:
                logger.error(f"[SSE] Error starting service {service}: {e}", exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e), 'done': True})}\n\n"
            finally:
                # Always unmark service when done (success or failure)
                await mark_service_deploying(service, False)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


# =============================================================================
# LiteLLM Configuration Generation
# =============================================================================

@router.post("/llm/configure")
async def configure_litellm(
    admin: bool = Depends(verify_admin_token)
):
    """
    Generate LiteLLM configuration from model_registry.yml.
    
    This updates config/litellm-config.yaml based on:
    - Environment (development uses model_purposes_dev, staging/prod uses model_purposes)
    - LLM backend (mlx, vllm, or cloud)
    
    After calling this endpoint, restart LiteLLM to pick up the new config.
    """
    try:
        # Get busibox host path for config file
        busibox_host_path = os.getenv('BUSIBOX_HOST_PATH')
        if not busibox_host_path:
            raise HTTPException(
                status_code=500,
                detail="BUSIBOX_HOST_PATH not set"
            )
        
        config_path = f"{busibox_host_path}/config/litellm-config.yaml"
        
        # Detect environment and backend
        environment = os.getenv('ENVIRONMENT', os.getenv('NODE_ENV', 'development'))
        llm_backend = os.getenv('LLM_BACKEND', 'mlx')
        
        # Load model registry
        registry = load_model_registry(busibox_host_path)
        
        if registry:
            # Generate config from registry
            config_content = generate_litellm_config_from_registry(
                registry=registry,
                environment=environment,
                llm_backend=llm_backend,
            )
            logger.info(f"Generated LiteLLM config from registry for {environment}/{llm_backend}")
        else:
            # Fallback to hardcoded config if registry not available
            logger.warning("Model registry not found, using fallback config")
            platform_info = get_platform_info()
            backend = platform_info.get("backend", llm_backend)
            
            if backend == "mlx":
                api_base = "http://host.docker.internal:8080/v1"
                config_content = f'''# LiteLLM Configuration - Fallback (registry not found)
# Backend: MLX

model_list:
  - model_name: test
    litellm_params:
      model: openai/mlx-community/Qwen3-0.6B-4bit
      api_base: {api_base}
      api_key: local
  - model_name: fast
    litellm_params:
      model: openai/mlx-community/Qwen2.5-3B-Instruct-4bit
      api_base: {api_base}
      api_key: local
  - model_name: agent
    litellm_params:
      model: openai/mlx-community/Qwen2.5-7B-Instruct-4bit
      api_base: {api_base}
      api_key: local
  - model_name: chat
    litellm_params:
      model: openai/mlx-community/Qwen2.5-7B-Instruct-4bit
      api_base: {api_base}
      api_key: local
  - model_name: frontier
    litellm_params:
      model: openai/mlx-community/Qwen2.5-14B-Instruct-4bit
      api_base: {api_base}
      api_key: local

general_settings:
  debug: true
  master_key: os.environ/LITELLM_MASTER_KEY

router_settings:
  enable_cache: true
  timeout: 120

litellm_settings:
  drop_params: true
  request_timeout: 120
'''
            else:
                config_content = '''# LiteLLM Configuration - Fallback (registry not found)

model_list:
  - model_name: test
    litellm_params:
      model: bedrock/anthropic.claude-3-haiku-20240307-v1:0
  - model_name: fast
    litellm_params:
      model: bedrock/anthropic.claude-3-haiku-20240307-v1:0
  - model_name: agent
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
  - model_name: chat
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
  - model_name: frontier
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0

general_settings:
  debug: true
  master_key: os.environ/LITELLM_MASTER_KEY

router_settings:
  enable_cache: true
  timeout: 120

litellm_settings:
  drop_params: true
  request_timeout: 120
'''
        
        # Write the config file
        with open(config_path, 'w') as f:
            f.write(config_content)
        
        logger.info(f"Generated LiteLLM config for {environment}/{llm_backend}")
        
        return {
            "success": True,
            "backend": backend,
            "tier": tier,
            "config_path": config_path,
            "message": f"LiteLLM config generated for {backend}. Restart LiteLLM to apply."
        }
        
    except Exception as e:
        logger.error(f"Error generating LiteLLM config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# LLM Chain Validation Endpoint
# =============================================================================
# Validates the complete LLM chain: Direct LLM (MLX/vLLM) -> LiteLLM -> Agent API

# LLM Service URLs from environment
LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
AGENT_API_URL = os.getenv("AGENT_API_URL", "http://agent-api:8000")
MLX_SERVER_URL = os.getenv("MLX_SERVER_URL", "http://host.docker.internal:8080")
LLM_BACKEND = os.getenv("LLM_BACKEND", "")  # mlx, vllm, or cloud


@router.get("/llm/validate")
async def validate_llm_chain(
    request: Request,
    admin: dict = Depends(verify_admin_token)
):
    """
    SSE endpoint for validating the complete LLM chain.
    
    Tests:
    1. Direct LLM (MLX or vLLM) - if available
    2. LiteLLM gateway
    3. Agent API
    
    Returns Server-Sent Events with validation progress and results.
    Requires admin authentication.
    """
    # Extract raw token for token exchange
    auth_header = request.headers.get("authorization", "")
    subject_token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    user_id = admin.get("user_id", "unknown")
    
    async def event_generator():
        # Use deterministic prompts that allow us to verify correctness
        # Math: 2+2=4, Letter counting: "hello" has 5 letters
        test_prompt_math = "What is 2 + 2? Reply with ONLY the number, nothing else."
        test_prompt_letters = "How many letters are in the word 'hello'? Reply with ONLY the number, nothing else."
        test_prompt = test_prompt_math  # Primary test uses simple math
        tests_run = 0
        tests_passed = 0
        
        def validate_llm_response(response_text: str, expected_answer: str = "4") -> tuple[bool, str]:
            """
            Validate LLM response against expected answer.
            Returns (is_valid, cleaned_response).
            
            Handles Qwen3's <think> reasoning mode - looks for answer after </think> or in reasoning.
            """
            if not response_text:
                return False, "Empty response"
            
            # Clean the response
            cleaned = response_text.strip().lower()
            
            # Handle Qwen3's <think> reasoning mode
            # Extract content after </think> if present (the actual answer)
            if '</think>' in cleaned:
                parts = cleaned.split('</think>')
                # The answer is typically after </think>
                answer_part = parts[-1].strip() if len(parts) > 1 else cleaned
                thinking_part = parts[0] if len(parts) > 1 else ""
                
                # Check answer part first
                numbers_in_answer = re.findall(r'\b\d+\b', answer_part)
                if numbers_in_answer and expected_answer in numbers_in_answer:
                    return True, expected_answer
                
                # Check thinking part for the answer (e.g., "2 + 2 equals 4")
                numbers_in_thinking = re.findall(r'\b\d+\b', thinking_part)
                if expected_answer in numbers_in_thinking:
                    return True, expected_answer
            
            # Check for common error patterns (but not in <think> reasoning)
            error_patterns = [
                "wasn't able to",
                "unable to",
                "failed",
                "cannot",
                "i'm sorry",
                "apologize",
            ]
            # Only check for errors outside of <think> blocks
            text_without_think = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL)
            for pattern in error_patterns:
                if pattern in text_without_think:
                    return False, f"Error response detected: {response_text[:100]}"
            
            # Try to extract the number from the full response
            # Note: re module is imported at module level
            numbers = re.findall(r'\b\d+\b', cleaned)
            if numbers:
                if expected_answer in numbers:
                    return True, expected_answer
                else:
                    # For 2+2, we might see "2" multiple times before "4"
                    # Check if 4 appears anywhere
                    if expected_answer in numbers:
                        return True, expected_answer
                    return False, f"Got numbers {numbers[:5]}, expected '{expected_answer}'"
            
            # Check if the expected answer appears anywhere in the response
            if expected_answer in cleaned:
                return True, expected_answer
            
            return False, f"No valid answer found in: {response_text[:80]}"
        
        def sse_event(event_type: str, message: str, done: bool = False) -> str:
            return f"data: {json.dumps({'type': event_type, 'message': message, 'done': done})}\n\n"
        
        yield sse_event('info', 'Starting LLM chain validation...')
        
        # =====================================================================
        # Test 1: Check LLM backend (MLX or vLLM)
        # =====================================================================
        yield sse_event('info', 'Test 1/3: Checking LLM backend...')
        tests_run += 1
        
        llm_backend = LLM_BACKEND or 'unknown'
        llm_url = ''
        
        # Build headers for host-agent (requires auth)
        host_agent_headers = {'Content-Type': 'application/json'}
        if HOST_AGENT_TOKEN:
            host_agent_headers['Authorization'] = f'Bearer {HOST_AGENT_TOKEN}'
        
        # Try to detect/start MLX via host-agent
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                yield sse_event('info', f'Checking host-agent at {HOST_AGENT_URL}...')
                response = await client.get(
                    f'{HOST_AGENT_URL}/mlx/status',
                    headers=host_agent_headers
                )
                
                if response.status_code == 200:
                    mlx_status = response.json()
                    if mlx_status.get('running'):
                        llm_backend = 'mlx'
                        llm_url = MLX_SERVER_URL
                        model = mlx_status.get('model', 'unknown')
                        yield sse_event('info', f'MLX server is running (model: {model})')
                    else:
                        # Try to start MLX
                        yield sse_event('info', 'MLX not running, attempting to start...')
                        start_response = await client.post(
                            f'{HOST_AGENT_URL}/mlx/start',
                            headers=host_agent_headers,
                            json={'model': 'mlx-community/Qwen3-0.6B-4bit'}
                        )
                        if start_response.status_code == 200:
                            await asyncio.sleep(5)  # Wait for startup
                            llm_backend = 'mlx'
                            llm_url = MLX_SERVER_URL
                            yield sse_event('success', 'MLX server started')
                elif response.status_code in (401, 403):
                    yield sse_event('warning', 'Host agent requires authentication - check HOST_AGENT_TOKEN')
            except Exception as e:
                yield sse_event('info', f'Host agent check failed: {str(e)}')
            
            # Try MLX directly if not already detected
            if not llm_url:
                try:
                    yield sse_event('info', f'Checking MLX server directly at {MLX_SERVER_URL}...')
                    response = await client.get(f'{MLX_SERVER_URL}/v1/models', timeout=5.0)
                    if response.status_code == 200:
                        llm_backend = 'mlx'
                        llm_url = MLX_SERVER_URL
                        yield sse_event('info', 'MLX server is running (direct check)')
                except Exception:
                    pass
            
            # Try vLLM if MLX not available
            if not llm_url:
                try:
                    vllm_url = 'http://vllm:8000'
                    response = await client.get(f'{vllm_url}/health', timeout=5.0)
                    if response.status_code == 200:
                        llm_backend = 'vllm'
                        llm_url = vllm_url
                        yield sse_event('info', 'vLLM server is available')
                except Exception:
                    pass
            
            if not llm_url:
                yield sse_event('warning', 'No local LLM backend available (MLX or vLLM)')
                yield sse_event('info', 'Will test LiteLLM with cloud fallback if configured...')
            else:
                # Test direct LLM inference
                try:
                    model_name = 'mlx-community/Qwen2.5-0.5B-Instruct-4bit' if llm_backend == 'mlx' else 'default'
                    response = await client.post(
                        f'{llm_url}/v1/chat/completions',
                        json={
                            'model': model_name,
                            'messages': [{'role': 'user', 'content': test_prompt}],
                            'max_tokens': 250,  # Qwen3 needs room for <think> reasoning
                        },
                        timeout=30.0
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        reply = data.get('choices', [{}])[0].get('message', {}).get('content', 'No response')
                        is_valid, validation_msg = validate_llm_response(reply, "4")
                        if is_valid:
                            yield sse_event('success', f'Direct {llm_backend.upper()}: "2+2={reply.strip()}" ✓')
                            tests_passed += 1
                        else:
                            yield sse_event('warning', f'Direct {llm_backend.upper()} response invalid: {validation_msg}')
                    else:
                        yield sse_event('warning', f'Direct {llm_backend.upper()} test failed: {response.text}')
                except Exception as e:
                    yield sse_event('warning', f'Direct {llm_backend.upper()} test error: {str(e)}')
            
            # =================================================================
            # Configure LiteLLM for detected backend
            # =================================================================
            if llm_backend in ('mlx', 'vllm'):
                yield sse_event('info', f'Configuring LiteLLM for {llm_backend.upper()}...')
                
                try:
                    # Generate config file for this backend
                    platform_info = get_platform_info()
                    tier = platform_info.get("tier", "standard")
                    busibox_host_path = os.getenv('BUSIBOX_HOST_PATH', '/busibox')
                    config_path = f"{busibox_host_path}/config/litellm-config.yaml"
                    
                    if llm_backend == "mlx":
                        api_base = "http://host.docker.internal:8080/v1"
                        models = {
                            "micro": {
                                "fast": "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
                                "agent": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
                                "frontier": "mlx-community/Qwen2.5-3B-Instruct-4bit",
                            },
                            "standard": {
                                "fast": "mlx-community/Qwen2.5-3B-Instruct-4bit",
                                "agent": "mlx-community/Qwen2.5-7B-Instruct-4bit",
                                "frontier": "mlx-community/Qwen2.5-14B-Instruct-4bit",
                            },
                            "pro": {
                                "fast": "mlx-community/Qwen2.5-3B-Instruct-4bit",
                                "agent": "mlx-community/Qwen2.5-14B-Instruct-4bit",
                                "frontier": "mlx-community/Qwen2.5-32B-Instruct-4bit",
                            },
                        }
                        tier_models = models.get(tier, models["standard"])
                        
                        config_content = f'''# LiteLLM Configuration - Auto-generated for MLX ({tier} tier)
model_list:
  - model_name: fast
    litellm_params:
      model: openai/{tier_models["fast"]}
      api_base: {api_base}
      api_key: local
  - model_name: agent
    litellm_params:
      model: openai/{tier_models["agent"]}
      api_base: {api_base}
      api_key: local
  - model_name: chat
    litellm_params:
      model: openai/{tier_models["agent"]}
      api_base: {api_base}
      api_key: local
  - model_name: frontier
    litellm_params:
      model: openai/{tier_models["frontier"]}
      api_base: {api_base}
      api_key: local

general_settings:
  debug: true
  master_key: os.environ/LITELLM_MASTER_KEY

router_settings:
  enable_cache: true
  timeout: 120

litellm_settings:
  drop_params: true
  request_timeout: 120
'''
                    else:  # vllm
                        api_base = "http://vllm:8000/v1"
                        config_content = f'''# LiteLLM Configuration - Auto-generated for vLLM
model_list:
  - model_name: agent
    litellm_params:
      model: openai/local-model
      api_base: {api_base}
      api_key: local
  - model_name: fast
    litellm_params:
      model: openai/local-model
      api_base: {api_base}
      api_key: local
  - model_name: chat
    litellm_params:
      model: openai/local-model
      api_base: {api_base}
      api_key: local
  - model_name: frontier
    litellm_params:
      model: openai/local-model
      api_base: {api_base}
      api_key: local

general_settings:
  debug: true
  master_key: os.environ/LITELLM_MASTER_KEY

router_settings:
  enable_cache: true
  timeout: 120

litellm_settings:
  drop_params: true
  request_timeout: 120
'''
                    
                    # Write config
                    with open(config_path, 'w') as f:
                        f.write(config_content)
                    
                    yield sse_event('info', f'LiteLLM config updated for {llm_backend.upper()}')
                    
                    # Restart LiteLLM to pick up new config
                    yield sse_event('info', 'Restarting LiteLLM to apply new configuration...')
                    
                    restart_cmd = [
                        'docker', 'compose',
                        '-p', COMPOSE_PROJECT_NAME,
                        '-f', f'{busibox_host_path}/docker-compose.yml',
                        '-f', f'{busibox_host_path}/docker-compose.local-dev.yml',
                        'restart', 'litellm'
                    ]
                    
                    process = await asyncio.create_subprocess_exec(
                        *restart_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=busibox_host_path,
                    )
                    stdout, stderr = await process.communicate()
                    
                    if process.returncode == 0:
                        yield sse_event('info', 'LiteLLM restarted, waiting for it to be ready...')
                        # Wait for LiteLLM to be ready using /health/liveliness (no auth required)
                        for _ in range(30):  # 30 second timeout
                            try:
                                response = await client.get(f'{LITELLM_URL}/health/liveliness', timeout=2.0)
                                if response.status_code == 200:
                                    yield sse_event('success', 'LiteLLM is ready with new configuration')
                                    break
                            except:
                                pass
                            await asyncio.sleep(1)
                    else:
                        yield sse_event('warning', f'LiteLLM restart failed: {stderr.decode()}')
                        
                except Exception as e:
                    yield sse_event('warning', f'Failed to configure LiteLLM: {str(e)}')
            
            # =================================================================
            # Test 2: LiteLLM Gateway
            # =================================================================
            yield sse_event('info', 'Test 2/3: Testing LiteLLM gateway...')
            tests_run += 1
            
            # Get LiteLLM master key for authentication
            # LITELLM_MASTER_KEY is the preferred key (used in config.yaml)
            litellm_api_key = os.getenv('LITELLM_MASTER_KEY', os.getenv('LITELLM_API_KEY', 'sk-local-dev-key'))
            yield sse_event('info', f'Using LiteLLM key: {litellm_api_key[:10]}...{litellm_api_key[-4:]}')
            
            litellm_headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {litellm_api_key}',
            }
            
            try:
                # Health check - use /health/liveliness which doesn't require auth or DB
                response = await client.get(f'{LITELLM_URL}/health/liveliness', timeout=5.0)
                if response.status_code != 200:
                    raise Exception(f'LiteLLM health check failed: {response.status_code}')
                
                yield sse_event('info', 'LiteLLM is healthy, testing chat completion...')
                
                # Chat completion test with auth - use 'test' model for fast validation
                response = await client.post(
                    f'{LITELLM_URL}/v1/chat/completions',
                    headers=litellm_headers,
                    json={
                        'model': 'test',  # Use test model (Qwen3-0.6B) for validation
                        'messages': [{'role': 'user', 'content': test_prompt}],
                        'max_tokens': 250,  # Qwen3 needs room for <think> reasoning
                    },
                    timeout=60.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    reply = data.get('choices', [{}])[0].get('message', {}).get('content', 'No response')
                    is_valid, validation_msg = validate_llm_response(reply, "4")
                    if is_valid:
                        yield sse_event('success', f'LiteLLM → LLM: "2+2={reply.strip()}" ✓')
                        tests_passed += 1
                    else:
                        yield sse_event('warning', f'LiteLLM response invalid: {validation_msg}')
                else:
                    # Provide more detail on auth failures
                    if response.status_code == 401:
                        yield sse_event('warning', f'LiteLLM auth failed (401) - key may not be registered in DB')
                        yield sse_event('info', f'Hint: LiteLLM may need database migration or use master_key in config')
                    yield sse_event('warning', f'LiteLLM test failed: {response.status_code} - {response.text[:300]}')
            except Exception as e:
                yield sse_event('warning', f'LiteLLM test error: {str(e)}')
            
            # =================================================================
            # Test 3: Agent API
            # =================================================================
            yield sse_event('info', 'Test 3/3: Testing Agent API...')
            tests_run += 1
            
            try:
                # Health check - verify agent-api is running
                response = await client.get(f'{AGENT_API_URL}/health', timeout=5.0)
                if response.status_code != 200:
                    raise Exception(f'Agent API health check failed: {response.status_code}')
                
                yield sse_event('info', 'Agent API is healthy, exchanging token...')
                
                # Exchange our token for an agent-api scoped token
                agent_api_token = None
                if subject_token:
                    try:
                        token_result = await exchange_token_zero_trust(
                            subject_token=subject_token,
                            target_audience="agent-api",
                            user_id=user_id,
                            scopes="agent.execute",
                        )
                        if token_result:
                            agent_api_token = token_result.access_token
                            yield sse_event('info', 'Token exchanged, testing chat endpoint...')
                    except Exception as e:
                        yield sse_event('warning', f'Token exchange failed: {str(e)}')
                
                if agent_api_token:
                    # Test the chat endpoint with proper auth
                    response = await client.post(
                        f'{AGENT_API_URL}/chat/message',
                        headers={
                            'Content-Type': 'application/json',
                            'Authorization': f'Bearer {agent_api_token}',
                        },
                        json={
                            'conversation_id': None,
                            'message': test_prompt,
                            'model': 'test',  # Use test model (Qwen 0.5B) for validation
                            'enable_web_search': False,
                            'enable_doc_search': False,
                        },
                        timeout=60.0
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        reply = data.get('response') or data.get('content') or data.get('message') or str(data)[:100]
                        reply_str = str(reply).strip()
                        
                        # Validate the response is a real LLM answer, not an error message
                        is_valid, validation_msg = validate_llm_response(reply_str, "4")
                        if is_valid:
                            yield sse_event('success', f'Agent API chat: "2+2={reply_str}" ✓')
                            tests_passed += 1
                        else:
                            yield sse_event('warning', f'Agent API returned error response: {validation_msg}')
                            yield sse_event('info', f'Full response: {reply_str[:150]}')
                    else:
                        yield sse_event('warning', f'Agent API chat failed: {response.status_code} - {response.text[:200]}')
                else:
                    # No token exchange, just verify health
                    yield sse_event('success', 'Agent API is healthy (no token available for chat test)')
                    tests_passed += 1
            except Exception as e:
                yield sse_event('warning', f'Agent API test error: {str(e)}')
        
        # =====================================================================
        # Summary
        # =====================================================================
        if tests_passed == tests_run:
            yield sse_event('success', f'All {tests_passed}/{tests_run} LLM chain tests passed!', done=True)
        elif tests_passed > 0:
            yield sse_event('warning', f'{tests_passed}/{tests_run} tests passed - partial functionality', done=True)
        else:
            yield sse_event('error', 'All LLM chain tests failed - check service logs', done=True)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
