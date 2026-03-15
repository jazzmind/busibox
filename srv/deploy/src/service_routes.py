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
import uuid
import yaml
from pydantic import BaseModel
from .auth import verify_admin_token, verify_service_or_admin_token
from .config import config
from .platform_detection import get_platform_info
from .core_app_executor import (
    is_docker_environment, execute_ssh_command,
    execute_docker_command, execute_in_core_apps,
)
from .model_manager import (
    load_registry_with_overrides,
    load_model_config,
    save_model_config,
    detect_vllm_gpus,
    list_cached_models,
    auto_assign_models,
    get_assignments,
    update_assignment,
    unassign_model,
    list_active_models,
    run_make_install_litellm,
    remote_download_model,
    restart_vllm_service,
    vllm_host,
)

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

    mlx_fast_port = os.getenv("MLX_FAST_PORT", "18081")
    mlx_fast_api_base = f"http://host.docker.internal:{mlx_fast_port}/v1"

    def purpose_api_base(purpose: str) -> str | None:
        """Get per-purpose API base for backends that use multiple local servers."""
        if llm_backend == 'mlx':
            if purpose in {'fast', 'test', 'classify'}:
                return mlx_fast_api_base
            if purpose == 'transcribe':
                return 'http://host.docker.internal:8081/v1'
            if purpose == 'voice':
                return 'http://host.docker.internal:8082/v1'
            if purpose == 'image':
                return 'http://host.docker.internal:8083/v1'
        return api_base
    
    purposes = get_model_purposes(registry, environment)
    available = registry.get('available_models', {})
    
    # Define which purposes map to LiteLLM model names
    # These are the model names that services request from LiteLLM
    litellm_purposes = [
        'test', 'fast', 'classify', 'cleanup', 'parsing', 'agent', 'chat', 'frontier', 'default',
        'tool_calling', 'video', 'image', 'transcribe', 'voice'
    ]
    
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
            resolved_api_base = purpose_api_base(purpose)
            if resolved_api_base:
                litellm_params['api_base'] = resolved_api_base
                litellm_params['api_key'] = 'local'
        else:
            litellm_params['model'] = model_name
        
        model_entry = {
            'model_name': purpose,
            'litellm_params': litellm_params,
        }
        
        # Add model_info if we have metadata
        description = model_config.get('description')
        mode = model_config.get('mode')
        if description or mode:
            model_entry['model_info'] = {}
            if description:
                model_entry['model_info']['description'] = description
            if mode:
                model_entry['model_info']['mode'] = mode
        
        model_list.append(model_entry)

    # Also register concrete model aliases (e.g. qwen3-4b) for every unique
    # purpose-backed registry model. This allows admin UI purpose assignment to
    # target explicit model entries, not only purpose aliases.
    unique_model_keys = []
    for purpose in litellm_purposes:
        model_key = purposes.get(purpose)
        if model_key and model_key not in unique_model_keys:
            unique_model_keys.append(model_key)

    for model_key in unique_model_keys:
        model_name, model_config = resolve_model_name(registry, model_key)
        provider = model_config.get('provider', 'mlx')

        # Pick API base from the first purpose that points to this model key.
        representative_purpose = next(
            (p for p in litellm_purposes if purposes.get(p) == model_key),
            'default',
        )

        litellm_params = {}
        if provider == 'bedrock':
            litellm_params['model'] = f"bedrock/{model_name}"
        elif provider in ('mlx', 'vllm'):
            litellm_params['model'] = f"openai/{model_name}"
            resolved_api_base = purpose_api_base(representative_purpose)
            if resolved_api_base:
                litellm_params['api_base'] = resolved_api_base
                litellm_params['api_key'] = 'local'
        else:
            litellm_params['model'] = model_name

        model_entry = {
            'model_name': model_key,
            'litellm_params': litellm_params,
        }

        description = model_config.get('description')
        mode = model_config.get('mode')
        if description or mode:
            model_entry['model_info'] = {}
            if description:
                model_entry['model_info']['description'] = description
            if mode:
                model_entry['model_info']['mode'] = mode

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
            'context_window_fallbacks': [
                {"agent": ["frontier"]},
                {"tool_calling": ["frontier"]},
                {"research": ["frontier"]},
            ],
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
    """Build the base docker compose command with compose files.
    
    Environment variables are passed through from the deploy-api container environment.
    """
    return [
        'docker', 'compose',
        '-p', COMPOSE_PROJECT_NAME,
        '-f', f'{busibox_host_path}/docker-compose.yml',
        '-f', f'{busibox_host_path}/docker-compose.local-dev.yml'
    ]


def _required_env_for_service(service: str) -> list[str]:
    """Required env vars that must be present to avoid compose fallback defaults."""
    logical = service
    if service in {"busibox-portal", "busibox-agents", "busibox-appbuilder"}:
        logical = "core-apps"
    required = {
        "litellm": ["POSTGRES_PASSWORD", "LITELLM_MASTER_KEY", "LITELLM_SALT_KEY"],
        # GITHUB_AUTH_TOKEN no longer required - repos are public
    }
    return required.get(logical, [])

# Host Agent configuration (for MLX control on Apple Silicon)
# The host-agent runs on the host machine and is accessible via host.docker.internal
HOST_AGENT_URL = os.getenv("HOST_AGENT_URL", "http://host.docker.internal:8089")
_HOST_AGENT_TOKEN_ENV = os.getenv("HOST_AGENT_TOKEN", "")

def _resolve_host_agent_token() -> str:
    """Resolve the host-agent token dynamically.
    
    During initial install the host-agent token is generated AFTER the
    deploy-api container starts, so the env-var captured at import time may
    be empty/stale.  The busibox repo root is volume-mounted at
    BUSIBOX_HOST_PATH, so we can read the current value from the .env file.
    """
    if _HOST_AGENT_TOKEN_ENV:
        return _HOST_AGENT_TOKEN_ENV
    busibox_path = os.getenv("BUSIBOX_HOST_PATH", "")
    prefix = os.getenv("CONTAINER_PREFIX", "dev")
    if busibox_path:
        env_file = os.path.join(busibox_path, f".env.{prefix}")
        try:
            with open(env_file) as f:
                for line in f:
                    if line.startswith("HOST_AGENT_TOKEN="):
                        token = line.split("=", 1)[1].strip()
                        if token:
                            return token
        except FileNotFoundError:
            pass
    return ""

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
                _token = _resolve_host_agent_token()
                if _token:
                    headers["Authorization"] = f"Bearer {_token}"
                
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
                'data-api': ['data-api', 'data-worker'],  # Data needs both API and worker
            }
            services_to_start = service_groups.get(service, [service])
            required_env = _required_env_for_service(service)
            missing = [k for k in required_env if not os.environ.get(k)]
            if missing:
                missing_str = ", ".join(missing)
                logger.error(f"Refusing to start {service}: missing required env vars: {missing_str}")
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"Missing required deploy-api environment variables for {service}: {missing_str}. "
                        "Refusing to use docker-compose fallback defaults."
                    ),
                )
            
            # Check if container already exists so we force-recreate with current env
            container_prefix = os.getenv('CONTAINER_PREFIX', 'dev')
            force_recreate = False
            for svc_name in services_to_start:
                cname = f"{container_prefix}-{svc_name}"
                try:
                    chk = subprocess.run(
                        ['docker', 'inspect', '--format', '{{.State.Status}}', cname],
                        capture_output=True, text=True, timeout=5,
                    )
                    if chk.returncode == 0 and chk.stdout.strip() in ('running', 'created', 'exited', 'paused'):
                        force_recreate = True
                        break
                except Exception:
                    pass

            up_args = ['up', '-d']
            if force_recreate:
                up_args.append('--force-recreate')
            up_args.append('--no-deps')
            up_args.extend(services_to_start)
            cmd.extend(up_args)
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
    Check if a service is healthy by hitting its actual health endpoint.
    
    Works for both Docker and Proxmox - services are reachable by hostname.
    
    Health check types:
    - Redis: TCP connection + PING command
    - PostgreSQL: TCP port check
    - MinIO: HTTP /minio/health/live
    - Milvus: HTTP /healthz
    - API services: HTTP /health on their respective ports
    - LiteLLM: HTTP /health/liveliness
    - Nginx: HTTPS /health
    
    Requires admin authentication.
    """
    service = request.service
    endpoint = request.endpoint or '/health'
    
    logger.info(f"Checking health for {service}")
    
    busibox_host_path = os.getenv('BUSIBOX_HOST_PATH', '/busibox')
    
    # Apps proxied behind nginx (accessed via nginx, not direct container)
    # These are Next.js apps running in the core-apps container or separately
    # They don't have their own containers, so we check them via nginx BEFORE container check
    nginx_apps = {
        'busibox-portal': {'path': '/portal', 'health_endpoint': '/api/health'},
        'busibox-admin': {'path': '/admin', 'health_endpoint': '/api/health'},
        'busibox-agents': {'path': '/agents', 'health_endpoint': '/api/health'},
        'busibox-chat': {'path': '/chat', 'health_endpoint': '/api/health'},
        'busibox-appbuilder': {'path': '/builder', 'health_endpoint': '/api/health'},
        'busibox-media': {'path': '/media', 'health_endpoint': '/api/health'},
        'busibox-documents': {'path': '/documents', 'health_endpoint': '/api/health'},
    }
    
    # Check nginx apps first (they don't have their own containers)
    if service in nginx_apps:
        app_config = nginx_apps[service]
        # Use endpoint from request if it looks like a full path, otherwise use default
        if endpoint.startswith(app_config['path']):
            # Full path provided (e.g., /agents/api/health)
            health_path = endpoint
        else:
            # Just the health endpoint provided, prepend app path
            health_path = f"{app_config['path']}{app_config['health_endpoint']}"
        
        # Determine nginx host based on environment
        # - NGINX_HOST env var (explicit override)
        # - In Docker: nginx runs in dedicated proxy container (hostname: nginx alias)
        # - In Proxmox: nginx runs in its own container
        # Default to 'nginx' which works for both Docker (via alias) and Proxmox
        nginx_host = os.getenv('NGINX_HOST', 'nginx')
        
        # When NGINX_PUBLIC_URL is set (e.g. https://staging.busibox.com), use Host header
        # so the request hits the domain server block (which has /health and proper app routing).
        # Without this, Host: nginx hits the default server which may route to wrong app.
        nginx_public_url = os.getenv('NGINX_PUBLIC_URL', '')
        headers = {}
        if nginx_public_url:
            from urllib.parse import urlparse
            parsed = urlparse(nginx_public_url)
            if parsed.netloc:
                headers['Host'] = parsed.netloc
        
        # Check via nginx container (HTTPS with self-signed cert, verify=False like curl -k)
        url = f"https://{nginx_host}{health_path}"
        logger.info(f"Checking nginx-proxied app health: {url}" + (f" (Host: {headers.get('Host', '')})" if headers else ""))
        
        try:
            # verify=False to ignore self-signed SSL cert (equivalent to curl -k)
            async with httpx.AsyncClient(verify=False) as client:
                response = await client.get(url, headers=headers or None, timeout=5.0)
                healthy = response.status_code == 200
                logger.info(f"Nginx app health check for {service}: {healthy} (status: {response.status_code})")
                return {
                    "healthy": healthy,
                    "service": service,
                    "url": url,
                    "status_code": response.status_code,
                    "reason": "nginx_app_health_check",
                }
        except httpx.TimeoutException:
            logger.warning(f"Health check timeout for nginx app {service} at {url}")
            return {
                "healthy": False,
                "service": service,
                "url": url,
                "error": "timeout",
                "reason": "nginx_timeout",
            }
        except Exception as e:
            logger.warning(f"Nginx app health check failed for {service}: {e}")
            return {
                "healthy": False,
                "service": service,
                "url": url,
                "error": str(e),
                "reason": "nginx_error",
            }
    
    try:
        # =================================================================
        # Real health checks - hit actual service endpoints
        # Works for both Docker and Proxmox - services are reachable by hostname
        # =================================================================
        
        # Service health check configuration
        # Format: service -> (hostname, port, endpoint, protocol, check_type)
        # check_type: 'http', 'tcp', 'redis', 'postgres'
        health_config = {
            # Infrastructure services
            'redis': ('redis', 6379, None, None, 'redis'),
            'postgres': ('postgres', 5432, None, None, 'postgres'),
            'minio': ('minio', 9000, '/minio/health/live', 'http', 'http'),
            'milvus': ('milvus', 9091, '/healthz', 'http', 'http'),
            'etcd': ('etcd', 2379, '/health', 'http', 'http'),
            'milvus-minio': ('milvus-minio', 9000, '/minio/health/live', 'http', 'http'),
            'neo4j': ('neo4j', 7474, '/', 'http', 'http'),
            'nginx': ('nginx', 443, '/health', 'https', 'http'),
            
            # API services
            'authz-api': ('authz-api', 8010, '/health/live', 'http', 'http'),
            'config-api': ('config-api' if is_docker_environment() else '127.0.0.1', 8012, '/health/live', 'http', 'http'),
            'deploy-api': ('127.0.0.1', 8011, '/health/live', 'http', 'http'),
            'data-api': ('data-api', 8002, '/health', 'http', 'http'),
            'data-worker': ('data-api', 8002, '/health', 'http', 'http'),  # Worker runs alongside data-api
            'search-api': ('search-api', 8003, '/health', 'http', 'http'),
            'agent-api': ('agent-api', 8000, '/health', 'http', 'http'),
            'embedding-api': ('embedding-api', 8005, '/health', 'http', 'http'),
            'docs-api': ('docs-api', 8004, '/health/live', 'http', 'http'),
            'bridge-api': ('bridge-api', 8081, '/health', 'http', 'http'),
            
            # LLM services
            'litellm': ('litellm', 4000, '/health/liveliness', 'http', 'http'),
            'vllm': ('vllm', 8000, '/health', 'http', 'http'),
            'vllm-verify': ('vllm', 8000, '/health', 'http', 'http'),  # Same as vllm, used for staging verification
            'mlx': ('host.docker.internal', 8080, '/health', 'http', 'http'),  # MLX runs on host
            'host-agent': ('host.docker.internal', 8089, '/health', 'http', 'http'),  # Host agent runs on host
        }
        
        if service not in health_config:
            # Unknown service but endpoint looks like a routable path — try via nginx
            # (handles deployed external apps that are proxied through nginx)
            if endpoint and endpoint.startswith('/'):
                nginx_host = os.getenv('NGINX_HOST', 'nginx')
                nginx_public_url = os.getenv('NGINX_PUBLIC_URL', '')
                headers = {}
                if nginx_public_url:
                    from urllib.parse import urlparse
                    parsed = urlparse(nginx_public_url)
                    if parsed.netloc:
                        headers['Host'] = parsed.netloc
                url = f"https://{nginx_host}{endpoint}"
                logger.info(f"Unknown service {service}, trying nginx fallback: {url}")
                try:
                    async with httpx.AsyncClient(verify=False) as client:
                        response = await client.get(url, headers=headers or None, timeout=5.0)
                        healthy = response.status_code == 200
                        return {
                            "healthy": healthy,
                            "service": service,
                            "url": url,
                            "status_code": response.status_code,
                            "reason": "nginx_fallback",
                        }
                except Exception as e:
                    logger.warning(f"Nginx fallback health check failed for {service}: {e}")
                    return {
                        "healthy": False,
                        "service": service,
                        "url": url,
                        "error": str(e),
                        "reason": "nginx_fallback_error",
                    }

            logger.warning(f"Unknown service {service}, no health check configured")
            return {
                "healthy": False,
                "service": service,
                "reason": "unknown_service",
            }
        
        hostname, port, endpoint, protocol, check_type = health_config[service]
        
        # Redis health check - use redis-cli ping
        if check_type == 'redis':
            import socket
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex((hostname, port))
                sock.close()
                
                if result == 0:
                    # Port is open, try PING command
                    try:
                        import redis as redis_lib
                        r = redis_lib.Redis(host=hostname, port=port, socket_timeout=5)
                        response = r.ping()
                        healthy = response == True
                        logger.info(f"Redis PING: {healthy}")
                        return {
                            "healthy": healthy,
                            "service": service,
                            "reason": "redis_ping" if healthy else "redis_ping_failed",
                        }
                    except ImportError:
                        # redis library not available, just check TCP
                        logger.info(f"Redis port {port} is open (redis library not available for PING)")
                        return {
                            "healthy": True,
                            "service": service,
                            "reason": "redis_port_open",
                        }
                    except Exception as e:
                        logger.warning(f"Redis PING failed: {e}")
                        return {
                            "healthy": False,
                            "service": service,
                            "reason": "redis_ping_failed",
                            "error": str(e),
                        }
                else:
                    logger.info(f"Redis port {port} not reachable on {hostname}")
                    return {
                        "healthy": False,
                        "service": service,
                        "reason": "redis_port_closed",
                    }
            except Exception as e:
                logger.warning(f"Redis health check failed: {e}")
                return {
                    "healthy": False,
                    "service": service,
                    "reason": "redis_check_error",
                    "error": str(e),
                }
        
        # PostgreSQL health check - try TCP connection to port
        if check_type == 'postgres':
            import socket
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex((hostname, port))
                sock.close()
                
                if result == 0:
                    logger.info(f"PostgreSQL port {port} is open on {hostname}")
                    return {
                        "healthy": True,
                        "service": service,
                        "reason": "postgres_port_open",
                    }
                else:
                    logger.info(f"PostgreSQL port {port} not reachable on {hostname}")
                    return {
                        "healthy": False,
                        "service": service,
                        "reason": "postgres_port_closed",
                    }
            except Exception as e:
                logger.warning(f"PostgreSQL health check failed: {e}")
                return {
                    "healthy": False,
                    "service": service,
                    "reason": "postgres_check_error",
                    "error": str(e),
                }
        
        # HTTP/HTTPS health check
        if check_type == 'http':
            url = f"{protocol}://{hostname}:{port}{endpoint}"
            # For nginx, use Host header from NGINX_PUBLIC_URL so request hits domain server block
            http_headers = {}
            if service == 'nginx':
                nginx_public_url = os.getenv('NGINX_PUBLIC_URL', '')
                if nginx_public_url:
                    from urllib.parse import urlparse
                    parsed = urlparse(nginx_public_url)
                    if parsed.netloc:
                        http_headers['Host'] = parsed.netloc
            logger.info(f"Checking health at {url}" + (f" (Host: {http_headers.get('Host', '')})" if http_headers else ""))
            
            try:
                async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                    response = await client.get(url, headers=http_headers or None)
                    # 200 = healthy, 401/403 = service is up but needs auth
                    healthy = response.status_code in (200, 401, 403)
                    logger.info(f"HTTP health check for {service} at {url}: status={response.status_code}, healthy={healthy}")
                    return {
                        "healthy": healthy,
                        "service": service,
                        "url": url,
                        "status_code": response.status_code,
                        "reason": "http_ok" if healthy else "http_error",
                    }
            except httpx.ConnectError as e:
                logger.info(f"HTTP health check failed for {service} at {url}: connection refused")
                return {
                    "healthy": False,
                    "service": service,
                    "url": url,
                    "reason": "connection_refused",
                    "error": str(e),
                }
            except httpx.TimeoutException:
                logger.info(f"HTTP health check timeout for {service} at {url}")
                return {
                    "healthy": False,
                    "service": service,
                    "url": url,
                    "reason": "timeout",
                }
            except Exception as e:
                logger.warning(f"HTTP health check failed for {service}: {e}")
                return {
                    "healthy": False,
                    "service": service,
                    "url": url,
                    "reason": "http_error",
                    "error": str(e),
                }
        
        # Should not reach here - all services should have a check_type
        logger.warning(f"No health check logic for {service} with check_type={check_type}")
        return {
            "healthy": False,
            "service": service,
            "reason": "no_check_logic",
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
    
    Used by Busibox Portal to determine which LLM runtime to use (MLX vs vLLM).
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
    Query params:
      - token (required for auth)
      - rebuild (optional, boolean): if true, rebuilds the container before starting
    """
    logger.info(f"[SSE] Received request to start service: {service}")
    
    # Check if rebuild is requested
    rebuild = request.query_params.get('rebuild', '').lower() == 'true'
    logger.info(f"[SSE] Rebuild requested: {rebuild}")
    
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
                
                # Handle vllm-verify service (for staging that uses production vLLM)
                if service == "vllm-verify":
                    yield f"data: {json.dumps({'type': 'info', 'message': 'Verifying production vLLM availability...'})}\n\n"
                    
                    # Check vLLM health (DNS resolves to production vLLM when use_production_vllm=true)
                    try:
                        async with httpx.AsyncClient() as client:
                            response = await client.get("http://vllm:8000/health", timeout=10.0)
                            if response.status_code == 200:
                                yield f"data: {json.dumps({'type': 'success', 'message': 'Production vLLM is running and healthy'})}\n\n"
                                
                                # Also check what models are loaded
                                try:
                                    models_response = await client.get("http://vllm:8000/v1/models", timeout=5.0)
                                    if models_response.status_code == 200:
                                        models_data = models_response.json()
                                        models_list = models_data.get('data', [])
                                        if models_list:
                                            model_names = [m.get('id', 'unknown') for m in models_list]
                                            yield f"data: {json.dumps({'type': 'info', 'message': f'Available models: {model_names}'})}\n\n"
                                except Exception:
                                    pass  # Model list is optional info
                                
                                yield f"data: {json.dumps({'type': 'success', 'message': 'Staging is using production vLLM', 'done': True})}\n\n"
                            else:
                                yield f"data: {json.dumps({'type': 'warning', 'message': f'Production vLLM returned status {response.status_code}'})}\n\n"
                                yield f"data: {json.dumps({'type': 'warning', 'message': 'Production vLLM may not be fully ready', 'done': True})}\n\n"
                    except httpx.ConnectError:
                        yield f"data: {json.dumps({'type': 'warning', 'message': 'Could not connect to production vLLM'})}\n\n"
                        yield f"data: {json.dumps({'type': 'info', 'message': 'Ensure production vLLM is running and accessible from staging'})}\n\n"
                        yield f"data: {json.dumps({'type': 'warning', 'message': 'Production vLLM not available', 'done': True})}\n\n"
                    except httpx.TimeoutException:
                        yield f"data: {json.dumps({'type': 'warning', 'message': 'Connection to production vLLM timed out'})}\n\n"
                        yield f"data: {json.dumps({'type': 'warning', 'message': 'Production vLLM may be starting up or overloaded', 'done': True})}\n\n"
                    except Exception as e:
                        logger.error(f"[SSE] Error checking production vLLM: {e}")
                        yield f"data: {json.dumps({'type': 'warning', 'message': f'Error checking production vLLM: {str(e)}', 'done': True})}\n\n"
                    return
                
                # Handle vllm service when use_production_vllm is true (staging uses production)
                use_production_vllm = platform_info.get("use_production_vllm", False)
                environment = platform_info.get("environment", "production")
                
                if service == "vllm" and use_production_vllm:
                    yield f"data: {json.dumps({'type': 'info', 'message': 'Staging environment configured to use production vLLM'})}\n\n"
                    yield f"data: {json.dumps({'type': 'info', 'message': 'Verifying production vLLM availability instead of installing...'})}\n\n"
                    
                    # Check vLLM health (DNS resolves to production vLLM)
                    try:
                        async with httpx.AsyncClient() as client:
                            response = await client.get("http://vllm:8000/health", timeout=10.0)
                            if response.status_code == 200:
                                yield f"data: {json.dumps({'type': 'success', 'message': 'Production vLLM is running and healthy'})}\n\n"
                                yield f"data: {json.dumps({'type': 'success', 'message': 'Staging will use production vLLM', 'done': True})}\n\n"
                            else:
                                yield f"data: {json.dumps({'type': 'warning', 'message': f'Production vLLM returned status {response.status_code}'})}\n\n"
                                yield f"data: {json.dumps({'type': 'warning', 'message': 'Production vLLM may not be fully ready', 'done': True})}\n\n"
                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'warning', 'message': f'Could not verify production vLLM: {str(e)}'})}\n\n"
                        yield f"data: {json.dumps({'type': 'info', 'message': 'LiteLLM can still work with cloud fallback if configured'})}\n\n"
                        yield f"data: {json.dumps({'type': 'warning', 'message': 'Production vLLM not verified', 'done': True})}\n\n"
                    return
                
                if service == "vllm" and backend == "mlx":
                    # Start MLX server via host-agent (runs on host, not in Docker)
                    yield f"data: {json.dumps({'type': 'info', 'message': 'Starting MLX server for Apple Silicon via host-agent...'})}\n\n"
                    tier = platform_info.get('tier', 'unknown')
                    ram_gb = platform_info.get('ram_gb', 'unknown')
                    yield f"data: {json.dumps({'type': 'info', 'message': f'Platform: {tier} tier, {ram_gb}GB RAM'})}\n\n"
                    
                    # Connect to host-agent SSE endpoint
                    headers = {}
                    _token = _resolve_host_agent_token()
                    if _token:
                        headers["Authorization"] = f"Bearer {_token}"
                    
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
                
                # Check if we're on Proxmox/LXC (not Docker)
                if not is_docker_environment():
                    yield f"data: {json.dumps({'type': 'info', 'message': f'Starting {service} on Proxmox via systemd...'})}\n\n"
                    
                    # Map service names to DNS hostnames and systemd service names
                    # DNS hostnames are resolved via /etc/hosts (set by internal_dns Ansible role)
                    # Format: service_name -> (dns_hostname, systemd_service_name)
                    proxmox_service_map = {
                        'redis': ('redis', 'redis-server'),  # data-lxc
                        'postgres': ('postgres', 'postgresql'),  # pg-lxc
                        'milvus': ('milvus', 'milvus'),  # milvus-lxc
                        'minio': ('minio', 'minio'),  # files-lxc
                        'litellm': ('litellm', 'litellm'),  # litellm-lxc
                        'authz-api': ('authz-api', 'authz'),  # authz-lxc
                        'authz': ('authz-api', 'authz'),  # alias
                        'data-api': ('data-api', 'data-api'),  # data-lxc
                        'data': ('data-api', 'data-api'),  # alias
                        'search-api': ('search-api', 'search-api'),  # milvus-lxc
                        'search': ('search-api', 'search-api'),  # alias
                        'agent-api': ('agent-api', 'agent-api'),  # agent-lxc
                        'agent': ('agent-api', 'agent-api'),  # alias
                        'embedding-api': ('embedding-api', 'embedding'),  # data-lxc
                        'embedding': ('embedding-api', 'embedding'),  # alias
                        'deploy-api': ('deploy-api', 'deploy-api'),  # authz-lxc
                        'deploy': ('deploy-api', 'deploy-api'),  # alias
                        'config-api': ('authz-api', 'config-api'),  # authz-lxc (co-located)
                        'config': ('authz-api', 'config-api'),  # alias
                        'docs-api': ('docs-api', 'docs-api'),  # milvus-lxc
                        'docs': ('docs-api', 'docs-api'),  # alias
                        'bridge-api': ('bridge-api', 'bridge'),  # bridge-lxc
                        'bridge': ('bridge-api', 'bridge'),  # alias
                        'busibox-portal': ('core-apps', 'busibox-portal'),  # apps-lxc
                        'busibox-admin': ('core-apps', 'busibox-admin'),  # apps-lxc
                        'busibox-agents': ('core-apps', 'busibox-agents'),  # apps-lxc
                        'busibox-chat': ('core-apps', 'busibox-chat'),  # apps-lxc
                        'busibox-appbuilder': ('core-apps', 'busibox-appbuilder'),  # apps-lxc
                        'busibox-media': ('core-apps', 'busibox-media'),  # apps-lxc
                        'busibox-documents': ('core-apps', 'busibox-documents'),  # apps-lxc
                    }
                    
                    if service not in proxmox_service_map:
                        yield f"data: {json.dumps({'type': 'error', 'message': f'Service {service} is not supported on Proxmox. Use Ansible to deploy infrastructure services.', 'done': True})}\n\n"
                        return
                    
                    container_host, systemd_service = proxmox_service_map[service]
                    
                    try:
                        # First check if the service unit exists
                        yield f"data: {json.dumps({'type': 'info', 'message': f'Connecting to {container_host}...'})}\n\n"
                        
                        # Check if the systemd unit exists
                        check_cmd = f"systemctl list-unit-files {systemd_service}.service 2>/dev/null | grep -q {systemd_service}"
                        _, _, check_code = await execute_ssh_command(container_host, check_cmd, timeout=30)
                        
                        if check_code != 0:
                            # Service unit doesn't exist - needs to be installed first
                            yield f"data: {json.dumps({'type': 'warning', 'message': f'Service {service} is not installed on {container_host}.'})}\n\n"
                            yield f"data: {json.dumps({'type': 'info', 'message': f'Use the /install/{service} endpoint to install this service via Ansible first.'})}\n\n"
                            yield f"data: {json.dumps({'type': 'error', 'message': f'Service {service} not installed. Run installation first.', 'done': True, 'action': 'install_required', 'install_endpoint': f'/api/v1/services/install/{service}'})}\n\n"
                            return
                        
                        # Service exists, try to start it
                        command = f"systemctl start {systemd_service} && systemctl status {systemd_service} --no-pager"
                        yield f"data: {json.dumps({'type': 'info', 'message': f'Starting {systemd_service}...'})}\n\n"
                        
                        stdout, stderr, code = await execute_ssh_command(container_host, command, timeout=60)
                        
                        if code == 0:
                            yield f"data: {json.dumps({'type': 'log', 'message': stdout})}\n\n"
                            yield f"data: {json.dumps({'type': 'success', 'message': f'{service} started successfully', 'done': True})}\n\n"
                        else:
                            # Check if this is a "unit not found" error
                            if 'not found' in stderr.lower() or 'could not be found' in stderr.lower():
                                yield f"data: {json.dumps({'type': 'warning', 'message': f'Service unit {systemd_service} not found.'})}\n\n"
                                yield f"data: {json.dumps({'type': 'info', 'message': f'Use /install/{service} endpoint to install this service via Ansible.'})}\n\n"
                                yield f"data: {json.dumps({'type': 'error', 'message': f'Service {service} not installed. Run installation first.', 'done': True, 'action': 'install_required', 'install_endpoint': f'/api/v1/services/install/{service}'})}\n\n"
                            else:
                                yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to start {service}: {stderr}', 'done': True})}\n\n"
                        return
                        
                    except Exception as e:
                        logger.error(f"[SSE] Error starting service on Proxmox: {e}")
                        yield f"data: {json.dumps({'type': 'error', 'message': f'SSH error: {str(e)}', 'done': True})}\n\n"
                        return
                
                # Regular Docker service deployment
                yield f"data: {json.dumps({'type': 'info', 'message': f'Starting {service}...'})}\n\n"
                
                # If rebuild is requested, build the container first
                if rebuild:
                    yield f"data: {json.dumps({'type': 'info', 'message': f'Rebuilding {service} container...'})}\n\n"
                
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
                    'data-api': ['data-api', 'data-worker'],  # Data needs both API and worker
                    # Frontend apps run inside the shared core-apps service.
                    # portal+admin are pre-built; others are built on demand via docker exec.
                    'busibox-portal': ['core-apps'],
                    'busibox-admin': ['core-apps'],
                    'busibox-agents': ['core-apps'],
                    'busibox-chat': ['core-apps'],
                    'busibox-appbuilder': ['core-apps'],
                    'busibox-media': ['core-apps'],
                    'busibox-documents': ['core-apps'],
                }
                services_to_start = service_groups.get(service, [service])
                required_env = _required_env_for_service(service)
                missing = [k for k in required_env if not env.get(k)]
                if missing:
                    missing_str = ", ".join(missing)
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Missing required deploy-api env vars for {service}: {missing_str}. Refusing to use compose defaults.', 'done': True})}\n\n"
                    return
                
                # Core apps (busibox-*) run inside a shared core-apps container.
                # portal+admin are pre-built at initial container start.
                # Other apps (agents, chat, documents, media, appbuilder) are
                # built on demand via `docker exec` into the running container.
                core_app_names = {
                    'busibox-portal', 'busibox-admin', 'busibox-agents',
                    'busibox-chat', 'busibox-appbuilder', 'busibox-media',
                    'busibox-documents',
                }
                if service in core_app_names:
                    container_prefix = os.getenv('CONTAINER_PREFIX', 'dev')
                    core_container = f"{container_prefix}-core-apps"
                    short_name = service.replace('busibox-', '')
                    
                    # Check if core-apps container is running
                    try:
                        chk = await asyncio.create_subprocess_exec(
                            'docker', 'inspect', '--format', '{{.State.Status}}', core_container,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        chk_out, _ = await chk.communicate()
                        core_running = chk.returncode == 0 and chk_out.decode().strip() == 'running'
                    except Exception:
                        core_running = False
                    
                    if core_running:
                        yield f"data: {json.dumps({'type': 'info', 'message': f'Building and starting {short_name} inside core-apps container...'})}\n\n"
                        
                        # Runtime image uses entrypoint.sh deploy <name> (supervisord).
                        # Dev image uses the app-manager API on port 9999.
                        # Try runtime entrypoint first; if it doesn't exist, use app-manager.
                        deploy_cmd = [
                            'docker', 'exec', core_container,
                            'bash', '-c',
                            f'if [ -x /usr/local/bin/entrypoint.sh ]; then '
                            f'/usr/local/bin/entrypoint.sh deploy {short_name}; '
                            f'else '
                            f'cd /srv/busibox-frontend && '
                            f'pnpm --filter @jazzmind/busibox-app build && '
                            f'cd apps/{short_name} && rm -rf .next 2>/dev/null; '
                            f'NODE_ENV=production pnpm run build && '
                            f'curl -sf -X POST http://localhost:9999/restart '
                            f'-H "Content-Type: application/json" '
                            f'-d \'{{"app":"{short_name}"}}\'; fi',
                        ]
                        
                        deploy_process = await asyncio.create_subprocess_exec(
                            *deploy_cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        
                        deploy_queue = asyncio.Queue()
                        
                        async def read_deploy_stream(stream, stream_type):
                            while True:
                                line = await stream.readline()
                                if not line:
                                    break
                                message = line.decode('utf-8', errors='replace').rstrip()
                                if message:
                                    await deploy_queue.put({
                                        'type': 'log',
                                        'stream': stream_type,
                                        'message': message
                                    })
                            await deploy_queue.put(None)
                        
                        deploy_stdout_task = asyncio.create_task(read_deploy_stream(deploy_process.stdout, "stdout"))
                        deploy_stderr_task = asyncio.create_task(read_deploy_stream(deploy_process.stderr, "stderr"))
                        
                        deploy_done_count = 0
                        while deploy_done_count < 2:
                            try:
                                msg = await asyncio.wait_for(deploy_queue.get(), timeout=0.1)
                                if msg is None:
                                    deploy_done_count += 1
                                else:
                                    yield f"data: {json.dumps(msg)}\n\n"
                            except asyncio.TimeoutError:
                                if deploy_stdout_task.done() and deploy_stderr_task.done():
                                    break
                                continue
                        
                        deploy_returncode = await deploy_process.wait()
                        
                        if deploy_returncode == 0:
                            yield f"data: {json.dumps({'type': 'success', 'message': f'{service} built and started successfully', 'done': True})}\n\n"
                        else:
                            yield f"data: {json.dumps({'type': 'error', 'message': f'{service} deploy failed (exit code {deploy_returncode})', 'done': True})}\n\n"
                        return
                    
                    # core-apps not running yet - fall through to docker compose up
                    yield f"data: {json.dumps({'type': 'info', 'message': 'core-apps container not running, starting via docker compose...'})}\n\n"
                
                # If rebuild requested, build the container(s) first
                if rebuild:
                    build_cmd = compose_cmd.copy()
                    build_cmd.extend(['build'] + services_to_start)
                    
                    services_list = ', '.join(services_to_start)
                    yield f"data: {json.dumps({'type': 'info', 'message': f'Building container(s): {services_list}'})}\n\n"
                    
                    build_process = await asyncio.create_subprocess_exec(
                        *build_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env,
                        cwd=busibox_host_path,
                    )
                    
                    # Stream build output
                    build_queue = asyncio.Queue()
                    
                    async def read_build_stream(stream, stream_type):
                        while True:
                            line = await stream.readline()
                            if not line:
                                break
                            message = line.decode('utf-8', errors='replace').rstrip()
                            if message:
                                await build_queue.put({
                                    'type': 'log',
                                    'stream': stream_type,
                                    'message': message
                                })
                        await build_queue.put(None)
                    
                    build_stdout_task = asyncio.create_task(read_build_stream(build_process.stdout, "stdout"))
                    build_stderr_task = asyncio.create_task(read_build_stream(build_process.stderr, "stderr"))
                    
                    # Yield build messages
                    build_done_count = 0
                    while build_done_count < 2:
                        try:
                            msg = await asyncio.wait_for(build_queue.get(), timeout=0.1)
                            if msg is None:
                                build_done_count += 1
                            else:
                                yield f"data: {json.dumps(msg)}\n\n"
                        except asyncio.TimeoutError:
                            if build_stdout_task.done() and build_stderr_task.done():
                                break
                            continue
                    
                    # Wait for build to complete
                    build_returncode = await build_process.wait()
                    
                    if build_returncode != 0:
                        yield f"data: {json.dumps({'type': 'error', 'message': f'Build failed with exit code {build_returncode}', 'done': True})}\n\n"
                        return
                    
                    yield f"data: {json.dumps({'type': 'success', 'message': 'Build completed successfully'})}\n\n"
                
                # Check if the container is already running so we can force-recreate
                # to ensure it picks up current env vars (secrets).
                container_prefix = os.getenv('CONTAINER_PREFIX', 'dev')
                existing_containers = set()
                for svc_name in services_to_start:
                    cname = f"{container_prefix}-{svc_name}"
                    try:
                        check = await asyncio.create_subprocess_exec(
                            'docker', 'inspect', '--format', '{{.State.Status}}', cname,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout_bytes, _ = await check.communicate()
                        if check.returncode == 0 and stdout_bytes.decode().strip() in ('running', 'created', 'exited', 'paused'):
                            existing_containers.add(svc_name)
                    except Exception:
                        pass

                force_recreate = len(existing_containers) > 0

                if force_recreate:
                    existing_str = ', '.join(existing_containers)
                    yield f"data: {json.dumps({'type': 'info', 'message': f'Recreating existing container(s): {existing_str} to sync env vars'})}\n\n"

                # For services with infra deps, let docker compose start dependencies
                # For API services, use --no-deps to avoid restarting already-running services
                up_args = ['up', '-d']
                if force_recreate:
                    up_args.append('--force-recreate')
                if service not in services_with_infra_deps:
                    up_args.append('--no-deps')
                up_args.extend(services_to_start)
                compose_cmd.extend(up_args)
                
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
                        rm_cmd = get_docker_compose_base_cmd(busibox_host_path)
                        rm_cmd.extend(['rm', '-f', init_service])
                        rm_process = await asyncio.create_subprocess_exec(
                            *rm_cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=env,
                            cwd=busibox_host_path,
                        )
                        await rm_process.wait()  # Don't care about return code - container might not exist
                        
                        # Start init container (depends_on will ensure service is healthy first)
                        # Use --force-recreate to ensure we get a fresh container
                        init_cmd = get_docker_compose_base_cmd(busibox_host_path)
                        init_cmd.extend(['up', '--no-deps', '--force-recreate', init_service])
                        init_process = await asyncio.create_subprocess_exec(
                            *init_cmd,
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
# Infrastructure Service Installation (Proxmox - via Ansible)
# =============================================================================

@router.get("/install/{service}")
async def install_service_sse(
    service: str,
    request: Request,
):
    """
    SSE endpoint for installing an infrastructure service via Ansible on Proxmox.
    
    This endpoint runs the appropriate Ansible playbook to install and configure
    the service on its target container. Use this for initial service setup.
    
    For Docker environments, redirects to start_service_sse (docker compose up).
    For Proxmox environments, runs Ansible playbooks with streaming output.
    
    Query params:
      - token (required for auth)
      - environment (optional): 'staging' or 'production' (default: auto-detect)
    
    Supported services:
      - Infrastructure: redis, postgres, minio, milvus
      - LLM: litellm, vllm, embedding-api
      - APIs: data-api, search-api, agent-api, authz-api, docs-api, deploy-api
      - Other: nginx
    """
    from .ansible_executor import AnsibleExecutor, INFRASTRUCTURE_ANSIBLE_MAP
    from .state import read_state
    
    logger.info(f"[INSTALL] Received request to install service: {service}")
    
    # Get environment override from query params
    env_override = request.query_params.get('environment', '')
    
    # Get token from query params (EventSource doesn't support custom headers)
    token = request.query_params.get('token')
    logger.info(f"[INSTALL] Token present: {bool(token)}")
    if not token:
        async def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Authentication required. Pass token as query parameter.', 'done': True})}\n\n"
        return StreamingResponse(
            error_gen(),
            media_type="text/event-stream",
            status_code=200,
        )
    
    # Verify token manually
    try:
        from .auth import verify_token
        token_payload = verify_token(token)
    except HTTPException as e:
        logger.error(f"[INSTALL] Token verification failed: {e.detail}")
        async def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'message': f'Authentication failed: {e.detail}', 'done': True})}\n\n"
        return StreamingResponse(
            error_gen(),
            media_type="text/event-stream",
            status_code=200,
        )
    except Exception as e:
        logger.error(f"[INSTALL] Token verification error: {e}", exc_info=True)
        async def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Authentication failed', 'done': True})}\n\n"
        return StreamingResponse(
            error_gen(),
            media_type="text/event-stream",
            status_code=200,
        )
    
    # Check for admin role
    roles = token_payload.get('roles', [])
    is_admin = any(
        (r.get('name') if isinstance(r, dict) else r) == 'Admin' 
        for r in roles
    ) if isinstance(roles, list) else False
    
    if not is_admin:
        logger.warning(f"[INSTALL] Non-admin user attempted to install service: {service}")
        async def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Admin role required', 'done': True})}\n\n"
        return StreamingResponse(
            error_gen(),
            media_type="text/event-stream",
            status_code=200,
        )
    
    async def event_generator():
        # Check if we're on Docker - if so, redirect to start_service
        if is_docker_environment():
            yield f"data: {json.dumps({'type': 'info', 'message': 'Docker environment detected - using docker compose to install/start service...'})}\n\n"
            yield f"data: {json.dumps({'type': 'info', 'message': f'Redirecting to start endpoint for {service}...'})}\n\n"
            # In Docker, installation = starting via docker compose
            # We'll emit a redirect message and let the client retry with /start
            yield f"data: {json.dumps({'type': 'redirect', 'endpoint': f'/api/v1/services/start/{service}?token={token}', 'message': 'Use /start endpoint for Docker services'})}\n\n"
            yield f"data: {json.dumps({'type': 'info', 'message': 'Note: In Docker mode, services are installed via docker compose up. Use the /start endpoint instead.', 'done': True})}\n\n"
            return
        
        # Validate service name
        if not service or not all(c.isalnum() or c in '-_' for c in service):
            yield f"data: {json.dumps({'type': 'error', 'message': 'Invalid service name', 'done': True})}\n\n"
            return
        
        # Check if service is supported for Ansible installation
        if service not in INFRASTRUCTURE_ANSIBLE_MAP:
            supported = ', '.join(sorted(INFRASTRUCTURE_ANSIBLE_MAP.keys()))
            yield f"data: {json.dumps({'type': 'error', 'message': f'Service {service} is not supported for installation. Supported services: {supported}', 'done': True})}\n\n"
            return
        
        # Determine environment
        if env_override and env_override in ('staging', 'production'):
            environment = env_override
            yield f"data: {json.dumps({'type': 'info', 'message': f'Using specified environment: {environment}'})}\n\n"
        else:
            # Try to read from .busibox-state file
            try:
                state = await read_state()
                environment = state.get('ENVIRONMENT', 'staging')
                yield f"data: {json.dumps({'type': 'info', 'message': f'Auto-detected environment: {environment}'})}\n\n"
            except Exception as e:
                logger.warning(f"[INSTALL] Failed to read state file, defaulting to staging: {e}")
                environment = 'staging'
                yield f"data: {json.dumps({'type': 'info', 'message': f'Defaulting to environment: {environment}'})}\n\n"
        
        yield f"data: {json.dumps({'type': 'info', 'message': f'Installing {service} on Proxmox via Ansible...'})}\n\n"
        
        # Run Ansible installation
        executor = AnsibleExecutor()
        
        try:
            async for event in executor.install_infrastructure_service_stream(service, environment):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            logger.error(f"[INSTALL] Error during Ansible execution: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': f'Ansible execution error: {str(e)}', 'done': True})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/installable")
async def list_installable_services(
    admin: bool = Depends(verify_admin_token)
):
    """
    List all services that can be installed via Ansible.
    
    Returns a dictionary of service names to their descriptions.
    Only available on Proxmox environments.
    """
    from .ansible_executor import AnsibleExecutor
    
    if is_docker_environment():
        return {
            "mode": "docker",
            "message": "Docker environment - use /start endpoint with docker compose",
            "services": {}
        }
    
    from .ansible_executor import get_installation_order
    
    executor = AnsibleExecutor()
    return {
        "mode": "proxmox",
        "message": "Proxmox environment - services can be installed via Ansible",
        "services": executor.get_supported_services(),
        "installation_order": get_installation_order(),
        "installation_order_note": "Services within a group can be installed in parallel, but groups should be installed sequentially."
    }


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
# MLX Ensure Endpoint
# =============================================================================
# Ensures MLX server is running before LLM validation (Apple Silicon only)

# LLM Service URLs from environment
LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
AGENT_API_URL = os.getenv("AGENT_API_URL", "http://agent-api:8000")
MLX_SERVER_URL = os.getenv("MLX_SERVER_URL", "http://host.docker.internal:8080")
MLX_FAST_SERVER_URL = os.getenv("MLX_FAST_SERVER_URL", "http://host.docker.internal:18081")
VLLM_URL = os.getenv("VLLM_URL", "http://vllm:8000")  # Configured by Ansible on Proxmox
LLM_BACKEND = os.getenv("LLM_BACKEND", "")  # mlx, vllm, or cloud
DEPLOYMENT_BACKEND = os.getenv("DEPLOYMENT_BACKEND", "docker")  # docker or proxmox


@router.post("/mlx/ensure/quick")
async def ensure_mlx_quick(
    request: Request,
    admin: dict = Depends(verify_service_or_admin_token)
):
    """
    Quick (non-SSE) endpoint to ensure MLX server is running.
    
    Called automatically by agent-api and other services before LLM calls.
    Returns JSON with the current MLX status and whether a start was triggered.
    
    If MLX is already running, returns immediately.
    If MLX is not running, triggers a start via host-agent and returns
    immediately (caller should retry LLM call with normal timeout).
    
    Response:
        {
            "status": "running" | "starting" | "unavailable" | "skipped",
            "message": "...",
            "backend": "mlx" | "vllm" | "cloud" | "unknown"
        }
    """
    llm_backend = LLM_BACKEND or 'unknown'
    target = "primary"
    try:
        payload = await request.json()
        if isinstance(payload, dict):
            requested_target = str(payload.get("target", "")).strip().lower()
            if requested_target in {"primary", "fast"}:
                target = requested_target
    except Exception:
        pass
    target_url = MLX_FAST_SERVER_URL if target == "fast" else MLX_SERVER_URL
    
    # Skip if not MLX backend
    if llm_backend != 'mlx':
        return {
            "status": "skipped",
            "message": f"LLM backend is {llm_backend}, MLX ensure not needed",
            "backend": llm_backend
        }
    
    # Build headers for host-agent
    _token = _resolve_host_agent_token()
    host_agent_headers = {'Content-Type': 'application/json'}
    if _token:
        host_agent_headers['Authorization'] = f'Bearer {_token}'
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: Check if MLX is already running
        try:
            response = await client.get(f'{target_url}/v1/models', timeout=3.0)
            if response.status_code == 200:
                models_data = response.json()
                model_count = len(models_data.get('data', []))
                return {
                    "status": "running",
                    "message": f"MLX {target} server is running ({model_count} model(s))",
                    "backend": "mlx"
                }
        except Exception:
            pass  # MLX not responding, try host-agent
        
        # Step 2: Check host-agent and start MLX if needed
        try:
            status_response = await client.get(
                f'{HOST_AGENT_URL}/mlx/status?target=all',
                headers=host_agent_headers,
                timeout=5.0
            )
            
            if status_response.status_code == 200:
                mlx_status = status_response.json()
                target_status = mlx_status.get(target, {})
                if target_status.get('running'):
                    if target_status.get('healthy', False):
                        return {
                            "status": "running",
                            "message": f"MLX {target} server is running (model: {target_status.get('model', 'unknown')})",
                            "backend": "mlx"
                        }
                    else:
                        # Process running but not yet healthy — still loading
                        return {
                            "status": "starting",
                            "message": f"MLX {target} process running, waiting for model to load",
                            "backend": "mlx"
                        }
                
                # One or both MLX servers not running — trigger dual start
                logger.info(f"MLX {target} not running, triggering dual start via host-agent")
                try:
                    await client.post(
                        f'{HOST_AGENT_URL}/mlx/start',
                        headers=host_agent_headers,
                        json={'model_type': 'dual'},
                        timeout=10.0
                    )
                    return {
                        "status": "starting",
                        "message": f"MLX dual start triggered for {target} server",
                        "backend": "mlx"
                    }
                except Exception as e:
                    logger.warning(f"Failed to start MLX via host-agent: {e}")
                    return {
                        "status": "unavailable",
                        "message": f"Host-agent start failed: {str(e)[:100]}",
                        "backend": "mlx"
                    }
            else:
                return {
                    "status": "unavailable",
                    "message": f"Host-agent returned {status_response.status_code}",
                    "backend": "mlx"
                }
        except Exception as e:
            return {
                "status": "unavailable",
                "message": f"Host-agent unreachable: {str(e)[:100]}",
                "backend": "mlx"
            }


@router.get("/mlx/ensure")
async def ensure_mlx_running(
    request: Request,
    admin: dict = Depends(verify_admin_token)
):
    """
    SSE endpoint to ensure MLX server is running on Apple Silicon.
    
    This should be called before LLM validation to:
    1. Check if MLX is already running
    2. Start MLX via host-agent if not running
    3. Wait for MLX to be ready
    
    Returns Server-Sent Events with progress updates.
    Requires admin authentication.
    """
    async def event_generator():
        def sse_event(event_type: str, message: str, done: bool = False) -> str:
            return f"data: {json.dumps({'type': event_type, 'message': message, 'done': done})}\n\n"
        
        llm_backend = LLM_BACKEND or 'unknown'
        
        # Skip if not MLX backend
        if llm_backend != 'mlx':
            yield sse_event('info', f'LLM backend is {llm_backend}, MLX ensure not needed')
            yield sse_event('success', 'Skipped (not MLX)', done=True)
            return
        
        yield sse_event('info', 'Ensuring MLX server is running...')
        
        # Build headers for host-agent (requires auth)
        _token = _resolve_host_agent_token()
        host_agent_headers = {'Content-Type': 'application/json'}
        if _token:
            host_agent_headers['Authorization'] = f'Bearer {_token}'
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: Check if MLX is already running directly
            try:
                yield sse_event('info', f'Checking MLX server at {MLX_SERVER_URL}...')
                response = await client.get(f'{MLX_SERVER_URL}/v1/models', timeout=5.0)
                if response.status_code == 200:
                    models_data = response.json()
                    model_count = len(models_data.get('data', []))
                    yield sse_event('success', f'MLX server is already running ({model_count} model(s) loaded)', done=True)
                    return
            except Exception as e:
                yield sse_event('info', f'MLX not responding directly: {str(e)[:50]}')
            
            # Step 2: Try to check/start via host-agent
            try:
                yield sse_event('info', f'Checking host-agent at {HOST_AGENT_URL}...')
                response = await client.get(
                    f'{HOST_AGENT_URL}/mlx/status',
                    headers=host_agent_headers,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    mlx_status = response.json()
                    if mlx_status.get('running'):
                        model = mlx_status.get('model', 'unknown')
                        yield sse_event('success', f'MLX server is running (model: {model})', done=True)
                        return
                    else:
                        # MLX not running - try to start it
                        yield sse_event('info', 'MLX not running, starting server...')
                        
                        start_response = await client.post(
                            f'{HOST_AGENT_URL}/mlx/start',
                            headers=host_agent_headers,
                            json={'model': 'mlx-community/Qwen3-0.6B-4bit'},
                            timeout=30.0
                        )
                        
                        if start_response.status_code == 200:
                            yield sse_event('info', 'MLX start command sent, waiting for server...')
                        else:
                            yield sse_event('warning', f'MLX start request returned {start_response.status_code}')
                elif response.status_code in (401, 403):
                    yield sse_event('warning', 'Host agent requires authentication - check HOST_AGENT_TOKEN')
                else:
                    yield sse_event('warning', f'Host agent returned status {response.status_code}')
            except Exception as e:
                yield sse_event('warning', f'Host agent error: {str(e)[:50]}')
            
            # Step 3: Wait for MLX to be ready
            yield sse_event('info', 'Waiting for MLX server to be ready...')
            max_attempts = 30  # 60 seconds max
            attempt = 0
            
            while attempt < max_attempts:
                try:
                    response = await client.get(f'{MLX_SERVER_URL}/v1/models', timeout=5.0)
                    if response.status_code == 200:
                        models_data = response.json()
                        model_count = len(models_data.get('data', []))
                        yield sse_event('success', f'MLX server is ready ({model_count} model(s) loaded)', done=True)
                        return
                except Exception:
                    pass
                
                await asyncio.sleep(2)
                attempt += 1
                
                if attempt % 5 == 0:
                    yield sse_event('info', f'Still waiting for MLX... ({attempt * 2}s)')
            
            # Final failure
            yield sse_event('error', 'MLX server failed to start within timeout', done=True)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/mlx/setup")
async def setup_mlx_full(
    request: Request,
    admin: dict = Depends(verify_admin_token)
):
    """
    SSE endpoint that orchestrates first-time MLX setup via the host-agent.

    Steps:
      1. Install MLX Python deps (mlx-lm, huggingface_hub) into venv
      2. Download all required models (LLM + STT/TTS/image media models)
      3. Start MLX server in dual mode
      4. Verify MLX server is serving models

    If MLX is already running the endpoint short-circuits with success.
    Called by the setup wizard's mlx-ensure step during Phase 2 deployment.
    """
    async def event_generator():
        def sse_event(event_type: str, message: str, done: bool = False) -> str:
            return f"data: {json.dumps({'type': event_type, 'message': message, 'done': done})}\n\n"

        llm_backend = LLM_BACKEND or "unknown"
        if llm_backend != "mlx":
            yield sse_event("info", f"LLM backend is {llm_backend}, MLX setup not needed")
            yield sse_event("success", "Skipped (not MLX)", done=True)
            return

        _token = _resolve_host_agent_token()
        host_agent_headers: dict[str, str] = {"Content-Type": "application/json"}
        if _token:
            host_agent_headers["Authorization"] = f"Bearer {_token}"

        # Quick check: if MLX is already healthy, nothing to do.
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{MLX_SERVER_URL}/v1/models")
                if resp.status_code == 200:
                    model_count = len(resp.json().get("data", []))
                    yield sse_event("success", f"MLX already running ({model_count} model(s))", done=True)
                    return
        except Exception:
            pass

        # Verify host-agent is reachable.
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{HOST_AGENT_URL}/health", headers=host_agent_headers)
                if resp.status_code != 200:
                    yield sse_event("error", f"Host-agent health returned {resp.status_code}", done=True)
                    return
        except httpx.ConnectError:
            yield sse_event("error", "Host-agent not reachable. Run Phase 1 host setup first.", done=True)
            return
        except Exception as exc:
            yield sse_event("error", f"Host-agent error: {exc}", done=True)
            return

        yield sse_event("info", "Host-agent is reachable, starting MLX setup...")

        # Step 1: Install MLX Python dependencies via host-agent
        yield sse_event("info", "Step 1/4: Installing MLX Python dependencies...")
        step1_ok = False
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{HOST_AGENT_URL}/setup/mlx",
                    headers=host_agent_headers,
                    json={"packages": ["mlx-lm", "huggingface_hub"]},
                    timeout=httpx.Timeout(10.0, read=600.0),
                ) as resp:
                    if resp.status_code != 200:
                        yield sse_event("error", f"Host-agent /setup/mlx returned {resp.status_code}", done=True)
                        return
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                yield f"{line}\n\n"
                                if data.get("done"):
                                    step1_ok = data.get("type") == "success"
                            except Exception:
                                yield f"{line}\n\n"
        except Exception as exc:
            yield sse_event("error", f"Dependency install failed: {exc}", done=True)
            return

        if not step1_ok:
            yield sse_event("error", "MLX dependency installation failed", done=True)
            return
        yield sse_event("info", "MLX dependencies installed successfully")

        # Step 2: Download required models (LLM + media) via host-agent
        yield sse_event("info", "Step 2/4: Checking required models...")

        # Query host-agent for all required models (LLM + STT/TTS/image)
        models_to_download: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{HOST_AGENT_URL}/models/required",
                    headers=host_agent_headers,
                )
                if resp.status_code == 200:
                    required = resp.json()
                    for m in required.get("models", []):
                        if not m.get("cached"):
                            models_to_download.append(m["name"])
                    if models_to_download:
                        yield sse_event("info", f"{len(models_to_download)} model(s) to download: {', '.join(models_to_download)}")
                    else:
                        yield sse_event("info", "All required models already cached")
                else:
                    yield sse_event("warning", f"Could not query required models ({resp.status_code}), falling back to test model only")
                    models_to_download = ["mlx-community/Qwen3-0.6B-4bit"]
        except Exception as exc:
            yield sse_event("warning", f"Could not query required models ({exc}), falling back to test model only")
            models_to_download = ["mlx-community/Qwen3-0.6B-4bit"]

        # Download each missing model
        for idx, model_name in enumerate(models_to_download, 1):
            yield sse_event("info", f"Downloading model {idx}/{len(models_to_download)}: {model_name}...")
            model_ok = False
            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST",
                        f"{HOST_AGENT_URL}/mlx/models/download",
                        headers=host_agent_headers,
                        json={"model": model_name},
                        timeout=httpx.Timeout(10.0, read=600.0),
                    ) as resp:
                        if resp.status_code != 200:
                            yield sse_event("warning", f"Download of {model_name} returned {resp.status_code}, skipping")
                            model_ok = True
                        else:
                            async for line in resp.aiter_lines():
                                if line.startswith("data: "):
                                    try:
                                        data = json.loads(line[6:])
                                        yield f"{line}\n\n"
                                        if data.get("done"):
                                            model_ok = data.get("type") != "error"
                                    except Exception:
                                        yield f"{line}\n\n"
            except Exception as exc:
                yield sse_event("warning", f"Download of {model_name} failed: {exc}, skipping")
                model_ok = True

            if model_ok:
                yield sse_event("info", f"Model {model_name} ready")
            else:
                yield sse_event("warning", f"Model {model_name} download had issues, continuing")

        yield sse_event("info", "Model downloads complete")

        # Step 3: Start MLX server via host-agent
        yield sse_event("info", "Step 3/4: Starting MLX server...")
        mlx_start_ok = False
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{HOST_AGENT_URL}/mlx/start",
                    headers=host_agent_headers,
                    json={"model_type": "agent"},
                    timeout=httpx.Timeout(10.0, read=300.0),
                ) as resp:
                    if resp.status_code != 200:
                        yield sse_event("error", f"MLX start returned {resp.status_code}", done=True)
                        return
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                yield f"{line}\n\n"
                                if data.get("done"):
                                    mlx_start_ok = data.get("type") in ("success", "warning")
                            except Exception:
                                yield f"{line}\n\n"
        except httpx.ConnectError:
            yield sse_event("error", "Host-agent lost during MLX start", done=True)
            return
        except Exception as exc:
            yield sse_event("error", f"MLX start error: {exc}", done=True)
            return

        if not mlx_start_ok:
            yield sse_event("error", "MLX server failed to start", done=True)
            return

        # Step 4: Verify MLX is serving models
        yield sse_event("info", "Step 4/4: Verifying MLX server...")
        for attempt in range(10):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{MLX_SERVER_URL}/v1/models")
                    if resp.status_code == 200:
                        model_count = len(resp.json().get("data", []))
                        yield sse_event("success", f"MLX server verified — {model_count} model(s) loaded", done=True)
                        return
            except Exception:
                pass
            await asyncio.sleep(3)

        yield sse_event("warning", "MLX server started but not yet responding — it may need more time to load", done=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# LLM Chain Validation Endpoint
# =============================================================================
# Validates the complete LLM chain: Direct LLM (MLX/vLLM) -> LiteLLM -> Agent API


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
        # Math: 2+2=4 - simple enough for even the smallest models
        # Validation looks for "4" in response (including in <think> blocks)
        test_prompt_math = "This is a test to verify the model is working. 2+2=? Answer with just the number."
        test_prompt_letters = "How many letters in 'hello'? Answer with just the number."
        test_prompt = test_prompt_math  # Primary test uses simple math
        tests_run = 0
        tests_passed = 0
        
        def validate_llm_response(response_text: str, expected_answer: str = "4") -> tuple[bool, str]:
            """
            Validate LLM response against expected answer.
            Returns (is_valid, cleaned_response).
            
            With /no_think in the prompt, Qwen3 should return clean responses.
            Still handles <think> blocks as fallback for compatibility.
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
        _token = _resolve_host_agent_token()
        host_agent_headers = {'Content-Type': 'application/json'}
        if _token:
            host_agent_headers['Authorization'] = f'Bearer {_token}'
        
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
                yield sse_event('info', f'Checking vLLM at {VLLM_URL}...')
                try:
                    response = await client.get(f'{VLLM_URL}/health', timeout=10.0)
                    if response.status_code == 200:
                        llm_backend = 'vllm'
                        llm_url = VLLM_URL
                        yield sse_event('success', f'vLLM server is reachable at {VLLM_URL}')
                    else:
                        yield sse_event('error', f'vLLM health check failed: HTTP {response.status_code}')
                        yield sse_event('info', f'vLLM response: {response.text[:200]}')
                except httpx.ConnectError as e:
                    yield sse_event('error', f'vLLM not reachable at {VLLM_URL}: Connection refused')
                    yield sse_event('info', 'Check that vLLM is running on the target host (production or staging vLLM container)')
                except httpx.TimeoutException:
                    yield sse_event('error', f'vLLM health check timed out at {VLLM_URL}')
                except Exception as e:
                    yield sse_event('error', f'vLLM check failed: {type(e).__name__}: {str(e)}')
            
            if not llm_url:
                yield sse_event('warning', 'No local LLM backend available (MLX or vLLM)')
                yield sse_event('info', f'VLLM_URL configured as: {VLLM_URL}')
                yield sse_event('info', f'LLM_BACKEND configured as: {LLM_BACKEND or "not set"}')
                yield sse_event('info', 'Will test LiteLLM with cloud fallback if configured...')
            else:
                # Test direct LLM inference
                # First, query /v1/models to see what's actually loaded
                loaded_model = None
                try:
                    models_response = await client.get(f'{llm_url}/v1/models', timeout=5.0)
                    if models_response.status_code == 200:
                        models_data = models_response.json()
                        models_list = models_data.get('data', [])
                        if models_list:
                            loaded_model = models_list[0].get('id', None)
                            yield sse_event('info', f'[Direct {llm_backend.upper()}] Currently loaded model: {loaded_model}')
                            if len(models_list) > 1:
                                yield sse_event('info', f'[Direct {llm_backend.upper()}] Additional models: {[m.get("id") for m in models_list[1:]]}')
                except Exception as e:
                    yield sse_event('info', f'Could not query loaded models: {str(e)}')
                
                # MLX can only serve ONE model at a time
                # Use the actually loaded model, not a hardcoded one
                if llm_backend == 'mlx' and loaded_model:
                    model_name = loaded_model
                    yield sse_event('info', f'[Direct MLX] Using loaded model: {model_name}')
                else:
                    model_name = loaded_model or ('default' if llm_backend == 'vllm' else 'mlx-community/Qwen3-0.6B-4bit')
                    yield sse_event('info', f'[Direct {llm_backend.upper()}] Model: {model_name}')
                
                # Test with the loaded model
                try:
                    direct_prompt = test_prompt
                    yield sse_event('info', f'[Direct {llm_backend.upper()}] Prompt: "{direct_prompt}"')
                    
                    # Build request payload - vLLM uses standard OpenAI format, MLX supports Qwen extensions
                    request_payload = {
                        'model': model_name,
                        'messages': [{'role': 'user', 'content': direct_prompt}],
                        'max_tokens': 1000,
                    }
                    
                    # Add Qwen-specific parameters only for MLX
                    if llm_backend == 'mlx':
                        request_payload['reasoning_effort'] = 'minimal'
                        request_payload['verbosity'] = 'low'
                    
                    response = await client.post(
                        f'{llm_url}/v1/chat/completions',
                        json=request_payload,
                        timeout=30.0
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        reply = data.get('choices', [{}])[0].get('message', {}).get('content', 'No response')
                        actual_model = data.get('model', model_name)
                        yield sse_event('info', f'[Direct {llm_backend.upper()}] Model in response: {actual_model}')
                        yield sse_event('info', f'[Direct {llm_backend.upper()}] Response: "{reply[:300]}"')
                        is_valid, validation_msg = validate_llm_response(reply, "4")
                        if is_valid:
                            yield sse_event('success', f'Direct {llm_backend.upper()} ({actual_model}): "2+2=4" ✓')
                            tests_passed += 1
                        else:
                            yield sse_event('warning', f'Direct {llm_backend.upper()} response invalid: {validation_msg}')
                    else:
                        yield sse_event('error', f'Direct {llm_backend.upper()} test failed: HTTP {response.status_code}')
                        yield sse_event('info', f'[Direct {llm_backend.upper()}] Requested model: {model_name}')
                        yield sse_event('info', f'[Direct {llm_backend.upper()}] Error response: {response.text[:300]}')
                except Exception as e:
                    yield sse_event('warning', f'Direct {llm_backend.upper()} test error: {str(e)}')
            
            # =================================================================
            # Check LiteLLM config - only regenerate if needed (Docker only)
            # On Proxmox, LiteLLM config is managed by Ansible
            # =================================================================
            if llm_backend in ('mlx', 'vllm') and DEPLOYMENT_BACKEND == 'docker':
                try:
                    # Get paths
                    busibox_host_path = os.getenv('BUSIBOX_HOST_PATH', '/busibox')
                    config_path = f"{busibox_host_path}/config/litellm-config.yaml"
                    
                    # Generate expected config from registry
                    registry = load_model_registry()
                    if registry:
                        environment = os.getenv('ENVIRONMENT', 'development')
                        expected_config = generate_litellm_config_from_registry(
                            registry=registry,
                            environment=environment,
                            llm_backend=llm_backend
                        )
                    else:
                        # Fallback config
                        api_base = "http://host.docker.internal:8080/v1" if llm_backend == "mlx" else "http://vllm:8000/v1"
                        expected_config = f'''# LiteLLM Configuration - Fallback (registry not available)
model_list:
  - model_name: test
    litellm_params:
      model: openai/mlx-community/Qwen3-0.6B-4bit
      api_base: {api_base}
      api_key: local
    model_info:
      description: "Test model for LLM chain validation"
  - model_name: fast
    litellm_params:
      model: openai/mlx-community/Qwen2.5-3B-Instruct-4bit
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
                    
                    # Read existing config
                    existing_config = ""
                    try:
                        with open(config_path, 'r') as f:
                            existing_config = f.read()
                    except FileNotFoundError:
                        pass
                    
                    # Check if config needs updating by comparing model_list content
                    # (ignore comments and whitespace differences)
                    def extract_models(config: str) -> set:
                        """Extract model names from config for comparison."""
                        models = set()
                        for line in config.split('\n'):
                            if 'model_name:' in line:
                                parts = line.split('model_name:')
                                if len(parts) > 1:
                                    models.add(parts[1].strip())
                        return models
                    
                    existing_models = extract_models(existing_config)
                    expected_models = extract_models(expected_config)
                    
                    config_needs_update = existing_models != expected_models
                    
                    if config_needs_update:
                        yield sse_event('info', f'LiteLLM config needs update: existing={existing_models}, expected={expected_models}')
                        
                        # Write new config
                        with open(config_path, 'w') as f:
                            f.write(expected_config)
                        
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
                                        yield sse_event('success', 'LiteLLM is ready with updated configuration')
                                        break
                                except:
                                    pass
                                await asyncio.sleep(1)
                        else:
                            yield sse_event('warning', f'LiteLLM restart failed: {stderr.decode()}')
                    else:
                        # Config is already correct - just verify LiteLLM is healthy
                        yield sse_event('info', f'LiteLLM config is up-to-date (models: {existing_models})')
                        try:
                            response = await client.get(f'{LITELLM_URL}/health/liveliness', timeout=5.0)
                            if response.status_code == 200:
                                yield sse_event('success', 'LiteLLM is healthy with correct configuration')
                            else:
                                yield sse_event('warning', f'LiteLLM health check failed: {response.status_code}')
                        except Exception as e:
                            yield sse_event('warning', f'LiteLLM health check error: {str(e)}')
                        
                except Exception as e:
                    yield sse_event('warning', f'Failed to check/configure LiteLLM: {str(e)}')
            elif llm_backend in ('mlx', 'vllm') and DEPLOYMENT_BACKEND == 'proxmox':
                # On Proxmox, LiteLLM config is managed by Ansible - just verify it's healthy
                yield sse_event('info', 'Proxmox deployment: LiteLLM config is managed by Ansible')
                try:
                    response = await client.get(f'{LITELLM_URL}/health/liveliness', timeout=5.0)
                    if response.status_code == 200:
                        yield sse_event('success', f'LiteLLM is healthy at {LITELLM_URL}')
                    else:
                        yield sse_event('warning', f'LiteLLM health check returned: HTTP {response.status_code}')
                except httpx.ConnectError:
                    yield sse_event('error', f'LiteLLM not reachable at {LITELLM_URL}: Connection refused')
                except Exception as e:
                    yield sse_event('warning', f'LiteLLM health check failed: {str(e)}')
            
            # =================================================================
            # Test 2: LiteLLM Gateway
            # =================================================================
            yield sse_event('info', 'Test 2/3: Testing LiteLLM gateway...')
            tests_run += 1
            
            # Get LiteLLM master key for authentication
            # LITELLM_MASTER_KEY is the preferred key (used in config.yaml)
            litellm_api_key = os.getenv('LITELLM_MASTER_KEY', os.getenv('LITELLM_API_KEY', 'sk-local-dev-key'))
            key_source = 'LITELLM_MASTER_KEY' if os.getenv('LITELLM_MASTER_KEY') else ('LITELLM_API_KEY' if os.getenv('LITELLM_API_KEY') else 'default')
            yield sse_event('info', f'Using LiteLLM key from {key_source}: {litellm_api_key[:10]}...{litellm_api_key[-4:]}')
            
            if litellm_api_key == 'sk-local-dev-key' and DEPLOYMENT_BACKEND == 'proxmox':
                yield sse_event('warning', 'Using default LiteLLM key - ensure LITELLM_MASTER_KEY is set in deploy-api environment')
            
            litellm_headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {litellm_api_key}',
            }
            selected_litellm_model = 'test'
            
            try:
                # Health check - use /health/liveliness which doesn't require auth or DB
                response = await client.get(f'{LITELLM_URL}/health/liveliness', timeout=5.0)
                if response.status_code != 200:
                    raise Exception(f'LiteLLM health check failed: {response.status_code}')
                
                yield sse_event('info', 'LiteLLM is healthy, testing chat completion...')
                # Discover models available for this API key. Some keys cannot access "test".
                yield sse_event('info', f'[LiteLLM] Discovering available models for API key...')
                try:
                    models_response = await client.get(
                        f'{LITELLM_URL}/v1/models',
                        headers=litellm_headers,
                        timeout=10.0,
                    )
                    if models_response.status_code == 200:
                        models_data = models_response.json()
                        models_list = models_data.get('data', [])
                        available_models: list[str] = []
                        for model_item in models_list:
                            model_id = model_item.get('id') or model_item.get('model_name')
                            if model_id:
                                available_models.append(str(model_id))

                        if available_models:
                            preferred = ['test', 'agent', 'fast', 'default']
                            selected_litellm_model = next(
                                (candidate for candidate in preferred if candidate in available_models),
                                available_models[0],
                            )
                            yield sse_event('info', f'[LiteLLM] Available models for key: {available_models[:10]}')
                            yield sse_event('info', f'[LiteLLM] Selected model: {selected_litellm_model}')
                        else:
                            yield sse_event('warning', '[LiteLLM] /v1/models returned no models; falling back to model=test')
                            yield sse_event('info', f'[LiteLLM] Raw /v1/models response: {json.dumps(models_data)[:200]}')
                    else:
                        yield sse_event('warning', f'[LiteLLM] Could not list models: HTTP {models_response.status_code}')
                        yield sse_event('info', f'[LiteLLM] /v1/models error: {models_response.text[:300]}')
                        yield sse_event('info', f'[LiteLLM] Will try model=test (may fail if key lacks access)')
                except Exception as e:
                    yield sse_event('warning', f'[LiteLLM] Model discovery failed: {type(e).__name__}: {str(e)}')
                    yield sse_event('info', f'[LiteLLM] Will try model=test (may fail if key lacks access)')
                
                # Chat completion test with auth - use 'test' model for fast validation
                # /no_think is Qwen-specific - only use for MLX backend
                # For vLLM, use plain prompt (vLLM models don't support Qwen directives)
                if llm_backend == 'mlx':
                    litellm_prompt = '/no_think ' + test_prompt
                else:
                    litellm_prompt = test_prompt
                
                yield sse_event('info', f'[LiteLLM] Model: {selected_litellm_model}')
                yield sse_event('info', f'[LiteLLM] Backend: {llm_backend}')
                yield sse_event('info', f'[LiteLLM] Prompt: "{litellm_prompt}"')
                
                # Build request payload - remove Qwen-specific params if routing to vLLM
                # LiteLLM should handle this, but be explicit for clarity
                request_payload = {
                    'model': selected_litellm_model,
                    'messages': [{'role': 'user', 'content': litellm_prompt}],
                    'max_tokens': 1000,
                }
                
                # Only add Qwen params if we know we're using MLX (not vLLM)
                # LiteLLM will route based on model config, but vLLM doesn't support these
                if llm_backend == 'mlx':
                    request_payload['reasoning_effort'] = 'minimal'
                    request_payload['verbosity'] = 'low'
                
                response = await client.post(
                    f'{LITELLM_URL}/v1/chat/completions',
                    headers=litellm_headers,
                    json=request_payload,
                    timeout=60.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    reply = data.get('choices', [{}])[0].get('message', {}).get('content', 'No response')
                    model_used = data.get('model', 'unknown')
                    yield sse_event('info', f'[LiteLLM] Model used: {model_used}')
                    yield sse_event('info', f'[LiteLLM] Response: "{reply[:300]}"')
                    is_valid, validation_msg = validate_llm_response(reply, "4")
                    if is_valid:
                        yield sse_event('success', f'LiteLLM → LLM: "2+2=4" ✓')
                        tests_passed += 1
                    else:
                        yield sse_event('warning', f'LiteLLM response invalid: {validation_msg}')
                else:
                    # Provide more detail on auth failures
                    if response.status_code == 401:
                        yield sse_event('error', f'LiteLLM auth failed (401) - key "{litellm_api_key[:10]}...{litellm_api_key[-4:]}" not recognized')
                        yield sse_event('info', f'Key source: {key_source}')
                        yield sse_event('info', f'Hint: Ensure LITELLM_MASTER_KEY matches the master_key in LiteLLM config')
                        yield sse_event('info', f'Hint: On Proxmox, check deploy-api.env.j2 template sets LITELLM_MASTER_KEY from vault')
                    elif response.status_code == 400:
                        yield sse_event('error', f'LiteLLM request invalid (400) - check model configuration and request format')
                        yield sse_event('info', f'[LiteLLM] Request payload: {json.dumps(request_payload, indent=2)}')
                        yield sse_event('info', f'[LiteLLM] Selected model was: {selected_litellm_model}')
                        # Parse error response to show the actual error message
                        try:
                            error_data = response.json()
                            error_msg = error_data.get('error', {}).get('message', '') if isinstance(error_data.get('error'), dict) else error_data.get('error', '')
                            if error_msg:
                                yield sse_event('info', f'[LiteLLM] Error message: {error_msg}')
                        except:
                            pass
                    yield sse_event('error', f'LiteLLM test failed: HTTP {response.status_code}')
                    yield sse_event('info', f'[LiteLLM] Full error response: {response.text[:500]}')
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
                    # Use selected_agents to bypass dispatcher and directly use test-agent
                    agent_prompt = test_prompt
                    test_agent_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "busibox.builtin.test-agent"))
                    yield sse_event('info', f'[Agent API] Model: {selected_litellm_model}')
                    yield sse_event('info', f'[Agent API] Agents: test-agent ({test_agent_uuid})')
                    yield sse_event('info', f'[Agent API] Prompt: "{agent_prompt}"')
                    
                    response = await client.post(
                        f'{AGENT_API_URL}/chat/message',
                        headers={
                            'Content-Type': 'application/json',
                            'Authorization': f'Bearer {agent_api_token}',
                        },
                        json={
                            'conversation_id': None,
                            'message': agent_prompt,
                            'model': selected_litellm_model,
                            'selected_agents': [test_agent_uuid],  # Bypass dispatcher, use test agent
                            'enable_web_search': False,
                            'enable_doc_search': False,
                        },
                        timeout=60.0
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        # Debug: show full response structure
                        yield sse_event('info', f'[Agent API] Response keys: {list(data.keys())}')
                        
                        reply = data.get('response') or data.get('content') or data.get('message') or str(data)[:100]
                        reply_str = str(reply).strip()
                        
                        # Debug: show raw response
                        yield sse_event('info', f'[Agent API] Response: "{reply_str[:300]}"')
                        
                        # Validate the response is a real LLM answer, not an error message
                        is_valid, validation_msg = validate_llm_response(reply_str, "4")
                        if is_valid:
                            yield sse_event('success', f'Agent API chat: "2+2=4" ✓')
                            tests_passed += 1
                        else:
                            yield sse_event('warning', f'Agent API returned error response: {validation_msg}')
                            # Show more context about the failure
                            if 'error' in data:
                                yield sse_event('info', f'[Agent API] Error field: {data.get("error")}')
                    else:
                        yield sse_event('warning', f'Agent API chat failed: HTTP {response.status_code}')
                        yield sse_event('info', f'[Agent API] Error: {response.text[:300]}')
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


# =============================================================================
# Media Server Management (proxy to host-agent)
# =============================================================================

def _host_agent_headers() -> dict:
    """Build auth headers for host-agent requests."""
    headers = {}
    _token = _resolve_host_agent_token()
    if _token:
        headers["Authorization"] = f"Bearer {_token}"
    return headers


@router.get("/media/status")
async def media_server_status(_: dict = Depends(verify_admin_token)):
    """
    Return status for all MLX media servers (transcribe, voice, image) with per-process memory info.
    Proxies to host-agent GET /media/status.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{HOST_AGENT_URL}/media/status",
                headers=_host_agent_headers(),
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Host agent not reachable - not running on MLX backend")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Media status error: {e}")


class MediaToggleRequest(BaseModel):
    server: str  # "transcribe", "voice", or "image"


@router.post("/media/toggle")
async def media_server_toggle(request: MediaToggleRequest, _: dict = Depends(verify_admin_token)):
    """
    Toggle a media server on/off (start if stopped, stop if running).
    Proxies to host-agent POST /media/toggle.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{HOST_AGENT_URL}/media/toggle",
                json={"server": request.server},
                headers=_host_agent_headers(),
                timeout=130.0,  # allow up to ~2min for model loading
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Host agent not reachable - not running on MLX backend")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Media toggle error: {e}")


@router.get("/gpu/status")
async def gpu_status(_: dict = Depends(verify_admin_token)):
    """
    Return NVIDIA GPU VRAM utilization and model assignments from the vLLM host.
    Runs nvidia-smi over SSH and cross-references running vLLM processes with model_config.yml.
    Returns gracefully empty if not on a GPU backend.
    """
    import asyncio
    import csv
    import io

    vllm_host = os.environ.get("VLLM_HOST", "")
    if not vllm_host:
        return {"available": False, "gpus": [], "message": "No VLLM_HOST configured"}

    # Query GPU memory/utilization via ssh + nvidia-smi
    nvidia_cmd = (
        "nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu "
        "--format=csv,noheader,nounits"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
            vllm_host, nvidia_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except Exception as e:
        return {"available": False, "gpus": [], "message": f"SSH to vLLM host failed: {e}"}

    if proc.returncode != 0:
        return {"available": False, "gpus": [], "message": "nvidia-smi failed on vLLM host"}

    gpus = []
    reader = csv.reader(io.StringIO(stdout.decode("utf-8", errors="replace")))
    for row in reader:
        if len(row) < 6:
            continue
        try:
            index, name, mem_used, mem_total, util, temp = [r.strip() for r in row]
            gpus.append({
                "index": int(index),
                "name": name,
                "vram_used_mb": float(mem_used),
                "vram_total_mb": float(mem_total),
                "utilization_pct": float(util),
                "temp_c": float(temp) if temp not in ("[N/A]", "") else None,
                "models": [],
            })
        except (ValueError, TypeError):
            continue

    # Cross-reference running vLLM processes to attach model names to GPU indices
    try:
        ps_cmd = "bash -c \"ps aux | grep vllm | grep -v grep | grep -oP '\\-\\-model\\s+\\K\\S+' || true\""
        proc2 = await asyncio.create_subprocess_exec(
            "ssh", "-o", "StrictHostKeyChecking=no",
            vllm_host, ps_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        ps_out, _ = await asyncio.wait_for(proc2.communicate(), timeout=8.0)
        running_models = [m.strip() for m in ps_out.decode("utf-8", errors="replace").splitlines() if m.strip()]
        # Attach running models to GPUs with significant utilization or VRAM usage
        for gpu in gpus:
            if gpu["utilization_pct"] > 1 or gpu["vram_used_mb"] > 500:
                gpu["models"] = running_models
    except Exception:
        pass

    return {"available": True, "gpus": gpus}


@router.post("/media/ensure")
async def media_server_ensure(request: MediaToggleRequest, _: dict = Depends(verify_admin_token)):
    """
    Ensure a media server is running (idempotent: no-op if already healthy).
    Proxies to host-agent POST /media/ensure.
    Used by LiteLLM hook for auto-starting media servers before requests.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{HOST_AGENT_URL}/media/ensure",
                json={"server": request.server},
                headers=_host_agent_headers(),
                timeout=130.0,  # allow up to ~2min for model loading
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Host agent not reachable - not running on MLX backend")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Media ensure error: {e}")


_GPU_MEDIA_SERVICES = {
    "transcribe": {
        "service": "whisper-gpu",
        "port": 8006,
        "label": "Whisper GPU (STT)",
        "memory_estimate_gb": 3.1,
    },
    "voice": {
        "service": "kokoro-gpu",
        "port": 8007,
        "label": "Kokoro GPU (TTS)",
        "memory_estimate_gb": 0.5,
    },
    "image": {
        "service": "flux-gpu",
        "port": 8008,
        "label": "Flux GPU (Image Gen)",
        "memory_estimate_gb": 5.0,
    },
}


async def _ssh_systemctl(host: str, action: str, service: str) -> tuple[int, str]:
    """Run systemctl {action} {service} over SSH. Returns (returncode, output)."""
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
        host, "systemctl", action, service,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120.0)
    return proc.returncode, stdout.decode("utf-8", errors="replace")


async def _gpu_media_service_status(host: str, name: str) -> dict:
    """Get status of a GPU media service via SSH systemctl is-active."""
    import asyncio
    cfg = _GPU_MEDIA_SERVICES[name]
    service = cfg["service"]

    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
            host, "systemctl", "is-active", service,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        active = stdout.decode("utf-8", errors="replace").strip() == "active"
    except Exception:
        active = False

    # Check HTTP health if active
    healthy = False
    if active:
        try:
            import httpx
            vllm_host_ip = host.split("@")[-1] if "@" in host else host
            resp = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: httpx.get(f"http://{vllm_host_ip}:{cfg['port']}/health", timeout=3.0)
                ),
                timeout=5.0,
            )
            healthy = resp.status_code == 200
        except Exception:
            pass

    return {
        "name": name,
        "service": service,
        "label": cfg["label"],
        "port": cfg["port"],
        "running": active,
        "healthy": healthy,
        "memory_estimate_gb": cfg["memory_estimate_gb"],
    }


@router.get("/gpu-media/status")
async def gpu_media_status(_: dict = Depends(verify_admin_token)):
    """
    Return status for on-demand GPU media services (whisper-gpu, kokoro-gpu) on the vLLM host.
    Uses SSH + systemctl to check service state.
    """
    import asyncio

    vllm_host = os.environ.get("VLLM_HOST", "")
    if not vllm_host:
        return {"available": False, "servers": {}, "message": "No VLLM_HOST configured"}

    tasks = {name: _gpu_media_service_status(vllm_host, name) for name in _GPU_MEDIA_SERVICES}
    results = {}
    for name, coro in tasks.items():
        try:
            results[name] = await coro
        except Exception as e:
            results[name] = {"name": name, "running": False, "error": str(e)}

    return {"available": True, "servers": results}


class GPUMediaToggleRequest(BaseModel):
    server: str  # "transcribe" or "voice"
    action: str = "toggle"  # "start" | "stop" | "toggle"


@router.post("/gpu-media/toggle")
async def gpu_media_toggle(request: GPUMediaToggleRequest, _: dict = Depends(verify_admin_token)):
    """
    Start or stop an on-demand GPU media service (whisper-gpu or kokoro-gpu) via SSH systemctl.
    action: "start" | "stop" | "toggle" (default)
    """
    import asyncio

    server = request.server.lower()
    if server not in _GPU_MEDIA_SERVICES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown GPU media server: {server}. Valid: {list(_GPU_MEDIA_SERVICES.keys())}"
        )

    vllm_host = os.environ.get("VLLM_HOST", "")
    if not vllm_host:
        raise HTTPException(status_code=503, detail="No VLLM_HOST configured")

    cfg = _GPU_MEDIA_SERVICES[server]
    service = cfg["service"]

    action = request.action.lower()
    if action == "toggle":
        status = await _gpu_media_service_status(vllm_host, server)
        action = "stop" if status["running"] else "start"

    rc, output = await _ssh_systemctl(vllm_host, action, service)
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"systemctl {action} {service} failed: {output}")

    await asyncio.sleep(1)
    new_status = await _gpu_media_service_status(vllm_host, server)
    return {
        "success": True,
        "server": server,
        "action": action,
        "output": output[-500:],
        "status": new_status,
    }


@router.post("/gpu-media/ensure")
async def gpu_media_ensure(request: GPUMediaToggleRequest, _: dict = Depends(verify_admin_token)):
    """
    Ensure a GPU media service is running. No-op if already active.
    Used by LiteLLM hook for Proxmox backends.
    """
    import asyncio

    server = request.server.lower()
    if server not in _GPU_MEDIA_SERVICES:
        raise HTTPException(status_code=400, detail=f"Unknown GPU media server: {server}")

    vllm_host = os.environ.get("VLLM_HOST", "")
    if not vllm_host:
        raise HTTPException(status_code=503, detail="No VLLM_HOST configured")

    status = await _gpu_media_service_status(vllm_host, server)
    if status["running"] and status["healthy"]:
        return {"running": True, "started": False, "server": server, "status": status}

    cfg = _GPU_MEDIA_SERVICES[server]
    rc, output = await _ssh_systemctl(vllm_host, "start", cfg["service"])
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"Failed to start {cfg['service']}: {output}")

    await asyncio.sleep(2)
    new_status = await _gpu_media_service_status(vllm_host, server)
    return {"running": new_status.get("running", False), "started": True, "server": server, "status": new_status}


_VLLM_PORTS = list(range(8000, 8006))  # vllm-8000 through vllm-8005


async def _vllm_port_status(host: str, port: int) -> dict:
    """Check a single vLLM server instance on a given port."""
    service = f"vllm-{port}"
    result: dict = {"port": port, "service": service, "running": False, "healthy": False, "model": None, "gpu": None}

    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
            host, "systemctl", "is-active", service,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        result["running"] = stdout.decode("utf-8", errors="replace").strip() == "active"
    except Exception:
        return result

    if not result["running"]:
        return result

    vllm_ip = host.split("@")[-1] if "@" in host else host
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://{vllm_ip}:{port}/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                if models:
                    result["model"] = models[0].get("id")
                result["healthy"] = True
    except Exception:
        pass

    # Parse GPU assignment from systemd environment
    try:
        proc2 = await asyncio.create_subprocess_exec(
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
            host, "bash", "-c",
            f"grep -oP 'CUDA_VISIBLE_DEVICES=\\K.*' /etc/systemd/system/{service}.service 2>/dev/null || true",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        gpu_out, _ = await asyncio.wait_for(proc2.communicate(), timeout=8.0)
        gpu_val = gpu_out.decode("utf-8", errors="replace").strip()
        if gpu_val:
            result["gpu"] = gpu_val
    except Exception:
        pass

    return result


@router.get("/vllm/status")
async def vllm_status(_: dict = Depends(verify_admin_token)):
    """
    Comprehensive vLLM cluster status: per-port model servers, GPU VRAM, and media models.
    Combines nvidia-smi, systemctl, and /v1/models queries via SSH.
    """
    import csv
    import io

    vllm_host = os.environ.get("VLLM_HOST", "")
    if not vllm_host:
        return {"available": False, "message": "No VLLM_HOST configured"}

    # Run all checks concurrently
    port_tasks = [_vllm_port_status(vllm_host, p) for p in _VLLM_PORTS]
    media_tasks = [_gpu_media_service_status(vllm_host, name) for name in _GPU_MEDIA_SERVICES]

    nvidia_cmd = (
        "nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu "
        "--format=csv,noheader,nounits"
    )

    async def _get_gpus():
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
                vllm_host, nvidia_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            if proc.returncode != 0:
                return []
            gpus = []
            reader = csv.reader(io.StringIO(stdout.decode("utf-8", errors="replace")))
            for row in reader:
                if len(row) < 6:
                    continue
                try:
                    idx, name, mem_total, mem_used, mem_free, util = [r.strip() for r in row]
                    gpus.append({
                        "index": int(idx),
                        "name": name,
                        "memory_total_mb": float(mem_total),
                        "memory_used_mb": float(mem_used),
                        "memory_free_mb": float(mem_free),
                        "utilization_pct": float(util),
                    })
                except (ValueError, TypeError):
                    continue
            return gpus
        except Exception:
            return []

    all_results = await asyncio.gather(
        asyncio.gather(*port_tasks),
        asyncio.gather(*media_tasks),
        _get_gpus(),
        return_exceptions=True,
    )

    models_result = all_results[0] if not isinstance(all_results[0], Exception) else []
    media_result = all_results[1] if not isinstance(all_results[1], Exception) else []
    gpus_result = all_results[2] if not isinstance(all_results[2], Exception) else []

    vllm_host_ip = vllm_host.split("@")[-1] if "@" in vllm_host else vllm_host

    return {
        "available": True,
        "vllm_host": vllm_host_ip,
        "models": list(models_result),
        "media": list(media_result),
        "gpus": list(gpus_result),
    }


class VllmAssignmentRequest(BaseModel):
    model_key: str
    gpu_ids: list[int]
    port: int | None = None
    tensor_parallel: int | None = None


class ModelDownloadRequest(BaseModel):
    model_name: str


class ModelLoadRequest(BaseModel):
    port: int


@router.get("/models/browse")
async def models_browse(_: dict = Depends(verify_admin_token)):
    registry = load_registry_with_overrides()
    available = registry.get("available_models", {}) or {}
    cached = set(await list_cached_models())
    rows = []
    for model_key, entry in available.items():
        model_name = entry.get("model_name", model_key)
        provider = (entry.get("provider", "") or "").lower()
        rows.append(
            {
                "model_key": model_key,
                "model_name": model_name,
                "provider": provider,
                "description": entry.get("description"),
                "cached": model_name in cached,
            }
        )
    rows.sort(key=lambda x: (0 if x["cached"] else 1, x["model_key"]))
    return {"models": rows}


@router.get("/models/active")
async def models_active(_: dict = Depends(verify_admin_token)):
    return {"vllm_host": vllm_host(), "models": await list_active_models(vllm_host())}


@router.post("/models/analyze")
async def models_analyze(_: dict = Depends(verify_admin_token)):
    registry = load_registry_with_overrides()
    gpus = await detect_vllm_gpus()
    cached = await list_cached_models()
    current = load_model_config()
    proposed = auto_assign_models(registry, len(gpus), existing=current)
    return {
        "gpu_count": len(gpus),
        "gpus": gpus,
        "cached_models": cached,
        "current_assignments": get_assignments(current),
        "proposed_assignments": get_assignments(proposed),
    }


@router.get("/vllm/gpus")
async def vllm_gpus(_: dict = Depends(verify_admin_token)):
    return {"vllm_host": vllm_host(), "gpus": await detect_vllm_gpus()}


@router.get("/vllm/assignments")
async def vllm_assignments(_: dict = Depends(verify_admin_token)):
    config_data = load_model_config()
    return {"assignments": get_assignments(config_data), "model_config_path": str(config.busibox_host_path) + "/provision/ansible/group_vars/all/model_config.yml"}


@router.post("/vllm/assignments")
async def vllm_assign_model(req: VllmAssignmentRequest, _: dict = Depends(verify_admin_token)):
    try:
        registry = load_registry_with_overrides()
        config_data = load_model_config()
        updated = update_assignment(
            config_data,
            registry,
            req.model_key,
            req.gpu_ids,
            req.port,
            req.tensor_parallel,
        )
        save_model_config(updated)
        return {"success": True, "assignments": get_assignments(updated)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/vllm/assignments/{model_key}")
async def vllm_unassign_model(model_key: str, _: dict = Depends(verify_admin_token)):
    try:
        registry = load_registry_with_overrides()
        config_data = load_model_config()
        updated = unassign_model(config_data, registry, model_key)
        save_model_config(updated)
        return {"success": True, "assignments": get_assignments(updated)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/vllm/assignments/auto")
async def vllm_auto_assign(_: dict = Depends(verify_admin_token)):
    registry = load_registry_with_overrides()
    gpus = await detect_vllm_gpus()
    config_data = load_model_config()
    updated = auto_assign_models(registry, len(gpus), existing=config_data)
    save_model_config(updated)
    return {"success": True, "gpu_count": len(gpus), "assignments": get_assignments(updated)}


@router.post("/vllm/apply")
async def vllm_apply(_: dict = Depends(verify_admin_token)):
    async def event_stream():
        yield "event: info\ndata: Starting LiteLLM apply\n\n"
        code, output = await run_make_install_litellm()
        for line in output.splitlines()[-300:]:
            yield f"event: log\ndata: {line}\n\n"
        if code == 0:
            yield "event: done\ndata: apply complete\n\n"
        else:
            yield f"event: error\ndata: apply failed (exit {code})\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/models/download")
async def models_download(req: ModelDownloadRequest, _: dict = Depends(verify_admin_token)):
    async def event_stream():
        yield f"event: info\ndata: downloading {req.model_name}\n\n"
        code, output = await remote_download_model(req.model_name)
        for line in output.splitlines()[-300:]:
            yield f"event: log\ndata: {line}\n\n"
        if code == 0:
            yield "event: done\ndata: download complete\n\n"
        else:
            yield f"event: error\ndata: download failed (exit {code})\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/models/load")
async def models_load(req: ModelLoadRequest, _: dict = Depends(verify_admin_token)):
    async def event_stream():
        yield f"event: info\ndata: restarting vllm-{req.port}\n\n"
        code, output = await restart_vllm_service(req.port)
        for line in output.splitlines()[-100:]:
            yield f"event: log\ndata: {line}\n\n"
        if code == 0:
            yield "event: done\ndata: load complete\n\n"
        else:
            yield f"event: error\ndata: load failed (exit {code})\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/system/memory")
async def system_memory(_: dict = Depends(verify_admin_token)):
    """
    Return system memory and per-process MLX memory breakdown.
    Proxies to host-agent GET /system/memory.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{HOST_AGENT_URL}/system/memory",
                headers=_host_agent_headers(),
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Host agent not reachable - not running on MLX backend")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"System memory error: {e}")


# =============================================================================
# Core Apps - Per-App Dev Mode Control
# =============================================================================
# Proxies to the app-manager control API running inside the core-apps container
# on port 9999.  Works for Docker (docker exec + curl) and Proxmox (SSH + curl).

APP_MANAGER_PORT = 9999


async def _app_manager_request(method: str, path: str, body: dict | None = None) -> dict:
    """Send a request to the app-manager control API inside core-apps."""
    if body:
        body_arg = f"-d '{json.dumps(body)}' -H 'Content-Type: application/json'"
    else:
        body_arg = ""

    curl_cmd = f"curl -sf -X {method} {body_arg} http://localhost:{APP_MANAGER_PORT}{path}"
    stdout, stderr, code = await execute_in_core_apps(curl_cmd, timeout=300)

    if code != 0:
        raise HTTPException(
            status_code=502,
            detail=f"app-manager unreachable: {stderr or stdout}",
        )

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail=f"app-manager returned invalid JSON: {stdout[:500]}",
        )


class AppModeRequest(BaseModel):
    app: str | None = None
    mode: str | None = None
    allApps: str | None = None


class AppRestartRequest(BaseModel):
    app: str


@router.get("/core-apps/dev-mode")
async def get_core_apps_dev_mode(_: dict = Depends(verify_admin_token)):
    """Get per-app dev/prod mode status from the app-manager inside core-apps."""
    try:
        result = await _app_manager_request("GET", "/status")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get dev mode status: {e}")


@router.post("/core-apps/dev-mode")
async def set_core_apps_dev_mode(
    req: AppModeRequest,
    _: dict = Depends(verify_admin_token),
):
    """Toggle dev/prod mode for a specific app or all apps."""
    body = {}
    if req.allApps:
        body["allApps"] = req.allApps
    elif req.app and req.mode:
        body["app"] = req.app
        body["mode"] = req.mode
    else:
        raise HTTPException(status_code=400, detail="Provide {app, mode} or {allApps}")

    try:
        result = await _app_manager_request("POST", "/mode", body)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set dev mode: {e}")


@router.post("/core-apps/restart")
async def restart_core_app(
    req: AppRestartRequest,
    _: dict = Depends(verify_admin_token),
):
    """Restart a specific core app without changing its mode."""
    try:
        result = await _app_manager_request("POST", "/restart", {"app": req.app})
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to restart app: {e}")


@router.post("/core-apps/redeploy")
async def redeploy_core_apps(
    _: dict = Depends(verify_admin_token),
):
    """Clean caches, reinstall deps, and rebuild all core apps.
    
    Triggers a full reinstall cycle: stops all apps, cleans node_modules
    caches, runs pnpm install, rebuilds shared packages and all apps,
    then restarts them. Returns 202 immediately; poll GET /core-apps/redeploy
    for status.
    """
    try:
        result = await _app_manager_request("POST", "/reinstall")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start redeploy: {e}")


@router.get("/core-apps/redeploy")
async def get_redeploy_status(
    _: dict = Depends(verify_admin_token),
):
    """Check if a redeploy/reinstall is currently in progress."""
    try:
        result = await _app_manager_request("GET", "/reinstall")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to check redeploy status: {e}")
