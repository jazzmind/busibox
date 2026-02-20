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
from .core_app_executor import is_docker_environment, execute_ssh_command

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
        'test', 'fast', 'agent', 'chat', 'frontier', 'default', 'tool_calling',
        'image', 'transcribe', 'voice'
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
                'data-api': ['data-api', 'data-worker'],  # Data needs both API and worker
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
        'busibox-agents': {'path': '/agents', 'health_endpoint': '/api/health'},
        'busibox-portal': {'path': '/portal', 'health_endpoint': '/api/health'},
        'busibox-appbuilder': {'path': '/builder', 'health_endpoint': '/api/health'},
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
        
        # When NGINX_PUBLIC_URL is set (e.g. https://staging.ai.jaycashman.com), use Host header
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
            # deploy-api runs on authz container - use localhost for self-check
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
                        'docs-api': ('docs-api', 'docs-api'),  # milvus-lxc
                        'docs': ('docs-api', 'docs-api'),  # alias
                        'bridge-api': ('bridge-api', 'bridge'),  # bridge-lxc
                        'bridge': ('bridge-api', 'bridge'),  # alias
                        'busibox-portal': ('core-apps', 'busibox-portal'),  # apps-lxc
                        'busibox-agents': ('core-apps', 'busibox-agents'),  # apps-lxc
                        'busibox-appbuilder': ('core-apps', 'busibox-appbuilder'),  # apps-lxc
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
                    # Frontend apps run inside the shared core-apps service
                    'busibox-portal': ['core-apps'],
                    'busibox-agents': ['core-apps'],
                    'busibox-appbuilder': ['core-apps'],
                }
                services_to_start = service_groups.get(service, [service])
                
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
    host_agent_headers = {'Content-Type': 'application/json'}
    if HOST_AGENT_TOKEN:
        host_agent_headers['Authorization'] = f'Bearer {HOST_AGENT_TOKEN}'
    
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
        host_agent_headers = {'Content-Type': 'application/json'}
        if HOST_AGENT_TOKEN:
            host_agent_headers['Authorization'] = f'Bearer {HOST_AGENT_TOKEN}'
        
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
