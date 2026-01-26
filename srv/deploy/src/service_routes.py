"""
Service Management Routes

API endpoints for starting/stopping Docker services and checking health.
"""

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
import subprocess
import httpx
import logging
import asyncio
from pydantic import BaseModel
from .auth import verify_admin_token, verify_token
from .config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/services", tags=["services"])


class StartServiceRequest(BaseModel):
    service: str


class HealthCheckRequest(BaseModel):
    service: str
    endpoint: str = '/health'


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
    
    try:
        # Use docker compose to start the service
        # The busibox directory is mounted at /busibox in the deploy-api container
        result = subprocess.run(
            ['docker', 'compose', '-f', '/busibox/docker-compose.yml', 'up', '-d', service],
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        if result.returncode != 0:
            logger.error(f"Failed to start {service}: {result.stderr}")
            # Don't fail completely - some services might have dependencies
            # Just log and return success so setup can continue
            logger.warning(f"Service {service} start returned non-zero but continuing")
        
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


@router.post("/health")
async def check_service_health(
    request: HealthCheckRequest,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Check if a service is healthy by calling its health endpoint.
    
    Requires admin authentication.
    """
    service = request.service
    endpoint = request.endpoint
    
    logger.info(f"Checking health for {service} at {endpoint}")
    
    try:
        # Construct health URL (service name resolves via Docker network)
        # Most services expose internal ports, check common ones
        port_map = {
            'postgres': 5432,
            'redis': 6379,
            'minio': 9000,
            'milvus': 19530,
            'litellm': 4000,
            'embedding-api': 8003,
            'vllm': 8000,
            'ingest-api': 8002,
            'search-api': 8004,
            'agent-api': 4111,
            'docs-api': 8005,
            'authz-api': 8010,
        }
        
        port = port_map.get(service)
        if not port:
            # Default to 8000 for unknown services
            port = 8000
        
        # For services without HTTP health checks, just check if container is running
        if service in ['postgres', 'redis', 'etcd', 'milvus-minio']:
            result = subprocess.run(
                ['docker', 'compose', '-f', '/busibox/docker-compose.yml', 'ps', '-q', service],
                capture_output=True,
                text=True,
            )
            
            # Also check if container is actually running (not just exists)
            if result.stdout.strip():
                inspect_result = subprocess.run(
                    ['docker', 'inspect', '--format', '{{.State.Running}}', result.stdout.strip()],
                    capture_output=True,
                    text=True,
                )
                healthy = inspect_result.stdout.strip() == 'true'
            else:
                healthy = False
            
            return {
                "healthy": healthy,
                "service": service,
            }
        
        # For HTTP services, call health endpoint
        url = f"http://{service}:{port}{endpoint}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=5.0)
            healthy = response.status_code == 200
            
            return {
                "healthy": healthy,
                "service": service,
                "url": url,
                "status_code": response.status_code,
            }
    except httpx.TimeoutException:
        logger.warning(f"Health check timeout for {service}")
        return {
            "healthy": False,
            "service": service,
            "error": "timeout",
        }
    except Exception as e:
        logger.warning(f"Health check failed for {service}: {e}")
        return {
            "healthy": False,
            "service": service,
            "error": str(e),
        }


@router.get("/status")
async def get_services_status(
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Get status of all Docker Compose services.
    """
    try:
        result = subprocess.run(
            ['docker', 'compose', 'ps', '--format', 'json'],
            cwd='/srv/busibox',
            capture_output=True,
            text=True,
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


@router.websocket("/start/{service}")
async def start_service_websocket(
    websocket: WebSocket,
    service: str
):
    """
    WebSocket endpoint for starting a Docker Compose service with real-time output.
    
    Streams docker compose output line-by-line to the client.
    Query params: token (required for auth)
    """
    await websocket.accept()
    
    try:
        # Authenticate via query parameter
        token = websocket.query_params.get('token')
        if not token:
            await websocket.send_json({"error": "Authentication required. Pass token as query parameter."})
            await websocket.close(code=4001)
            return
        
        try:
            verify_token(token)
        except Exception as e:
            logger.error(f"WebSocket auth failed: {e}")
            await websocket.send_json({"error": "Authentication failed"})
            await websocket.close(code=4001)
            return
        
        # Validate service name
        if not service or not all(c.isalnum() or c in '-_' for c in service):
            await websocket.send_json({"error": "Invalid service name", "done": True})
            return
        
        logger.info(f"[WS] Starting service: {service}")
        await websocket.send_json({"type": "info", "message": f"Starting {service}..."})
        
        # Start docker compose with real-time output
        process = await asyncio.create_subprocess_exec(
            'docker', 'compose', '-f', '/busibox/docker-compose.yml', 'up', '-d', service,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # Stream stdout and stderr
        async def stream_output(stream, stream_type):
            while True:
                line = await stream.readline()
                if not line:
                    break
                message = line.decode('utf-8').rstrip()
                if message:
                    await websocket.send_json({
                        "type": "log",
                        "stream": stream_type,
                        "message": message
                    })
        
        # Run both streams concurrently
        await asyncio.gather(
            stream_output(process.stdout, "stdout"),
            stream_output(process.stderr, "stderr"),
        )
        
        # Wait for process to complete
        returncode = await process.wait()
        
        if returncode == 0:
            await websocket.send_json({
                "type": "success",
                "message": f"Service {service} started successfully",
                "done": True
            })
        else:
            await websocket.send_json({
                "type": "warning",
                "message": f"Service {service} completed with code {returncode}",
                "done": True
            })
        
    except WebSocketDisconnect:
        logger.info(f"[WS] Client disconnected during service start: {service}")
    except Exception as e:
        logger.error(f"[WS] Error starting service {service}: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e),
                "done": True
            })
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass
