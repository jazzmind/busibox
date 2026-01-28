"""
Deployment Service Routes

API endpoints for app deployment operations.
"""

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse
import asyncio
import logging
import uuid
import json
from datetime import datetime
from typing import Dict
from pydantic import BaseModel
from .models import (
    DeployRequest,
    DeploymentResult,
    DeploymentStatus,
    DeploymentConfig,
    BusiboxManifest
)
from .auth import verify_admin_token
from .database import provision_database
from .container_executor import deploy_app as container_deploy_app, is_docker_environment, undeploy_app as container_undeploy_app, stop_app as container_stop_app
from .env_generator import generate_env_vars
from .nginx_config import NginxConfigurator
from .config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/deployment", tags=["deployment"])

# In-memory deployment status storage
# TODO: Move to Redis or database for persistence
deployment_statuses: Dict[str, DeploymentStatus] = {}

# WebSocket connections for log streaming
active_connections: Dict[str, list[WebSocket]] = {}

# Rate limiting: track last deployment per app
last_deployment_times: Dict[str, datetime] = {}

# Track log positions for SSE streaming
log_positions: Dict[str, int] = {}


def check_rate_limit(app_id: str) -> None:
    """Check if app can be deployed (rate limiting)"""
    if app_id in last_deployment_times:
        elapsed = (datetime.utcnow() - last_deployment_times[app_id]).total_seconds()
        limit_seconds = config.rate_limit_seconds
        if elapsed < limit_seconds:
            remaining = int(limit_seconds - elapsed)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Try again in {remaining} seconds."
            )


@router.post("/deploy", response_model=DeploymentResult)
async def deploy_app(
    request: DeployRequest,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Deploy an app from manifest.
    
    Requires admin authentication.
    """
    manifest = request.manifest
    deploy_config = request.config
    
    # Check rate limit
    check_rate_limit(manifest.id)
    
    deployment_id = str(uuid.uuid4())
    
    logger.info(f"Starting deployment {deployment_id} for {manifest.name} by user {token_payload.get('user_id')}")
    
    # Update rate limit
    last_deployment_times[manifest.id] = datetime.utcnow()
    
    # Initialize status
    status = DeploymentStatus(
        deploymentId=deployment_id,
        status='pending',
        progress=0,
        currentStep='Initializing deployment',
        startedAt=datetime.utcnow(),
        logs=[]
    )
    deployment_statuses[deployment_id] = status
    active_connections[deployment_id] = []
    
    # Start deployment in background
    asyncio.create_task(execute_deployment(deployment_id, manifest, deploy_config))
    
    return DeploymentResult(
        deploymentId=deployment_id,
        status='pending',
        appUrl=f"https://yourdomain.com{manifest.defaultPath}"
    )


async def execute_deployment(
    deployment_id: str,
    manifest: BusiboxManifest,
    deploy_config: DeploymentConfig
):
    """
    Execute deployment asynchronously using the new container executor.
    
    Flow:
    1. Provision database (if required)
    2. Deploy app via container_executor (git clone, npm install, build, migrations, systemd)
    3. Configure nginx routing
    """
    
    status = deployment_statuses[deployment_id]
    database_url = None
    
    try:
        # Step 1: Provision database if required
        if manifest.database and manifest.database.required:
            status.status = 'provisioning_db'
            status.progress = 10
            status.currentStep = 'Provisioning database'
            status.logs.append(f"[{datetime.utcnow().isoformat()}] Provisioning database...")
            await broadcast_status(deployment_id)
            
            db_result = await provision_database(manifest)
            
            if not db_result.success:
                raise Exception(f"Database provisioning failed: {db_result.error}")
            
            status.logs.append(f"[{datetime.utcnow().isoformat()}] Database provisioned: {db_result.databaseName}")
            await broadcast_status(deployment_id)
            
            database_url = db_result.databaseUrl
            if database_url:
                deploy_config.secrets['DATABASE_URL'] = database_url
        
        # Step 2: Deploy app via container executor
        status.status = 'deploying'
        status.progress = 20
        status.currentStep = 'Deploying application'
        status.logs.append(f"[{datetime.utcnow().isoformat()}] Starting deployment...")
        await broadcast_status(deployment_id)
        
        logger.info(f"Calling container_deploy_app for {manifest.name}")
        
        # Collect logs from container executor
        deploy_logs = []
        try:
            success = await container_deploy_app(
                manifest,
                deploy_config,
                database_url,
                deploy_logs
            )
            logger.info(f"container_deploy_app returned: success={success}, logs={len(deploy_logs)}")
        except Exception as e:
            logger.error(f"container_deploy_app raised exception: {e}", exc_info=True)
            raise
        
        # Add logs to status
        for log in deploy_logs:
            status.logs.append(f"[{datetime.utcnow().isoformat()}] {log}")
        await broadcast_status(deployment_id)
        
        if not success:
            # Find the last error log (starts with ❌)
            error_logs = [log for log in deploy_logs if '❌' in log]
            error_detail = error_logs[-1] if error_logs else "Unknown error - check logs"
            raise Exception(f"Deployment failed: {error_detail}")
        
        status.progress = 80
        await broadcast_status(deployment_id)
        
        # Step 3: Configure nginx routing
        status.status = 'configuring_nginx'
        status.currentStep = 'Configuring nginx'
        status.logs.append(f"[{datetime.utcnow().isoformat()}] Configuring nginx routing...")
        await broadcast_status(deployment_id)
        
        configurator = NginxConfigurator()
        
        if is_docker_environment():
            # Docker: use service name
            nginx_success, nginx_msg = await configurator.configure_app(manifest, None)
        else:
            # LXC: use container IP
            if deploy_config.environment == 'staging':
                container_ip = config.user_apps_container_ip_staging
            else:
                container_ip = config.user_apps_container_ip
            nginx_success, nginx_msg = await configurator.configure_app(manifest, container_ip)
        
        status.logs.append(f"[{datetime.utcnow().isoformat()}] {nginx_msg}")
        await broadcast_status(deployment_id)
        
        if not nginx_success:
            raise Exception(f"Nginx configuration failed: {nginx_msg}")
        
        # Step 4: Complete
        status.status = 'completed'
        status.progress = 100
        status.currentStep = 'Deployment completed'
        status.completedAt = datetime.utcnow()
        status.logs.append(f"[{datetime.utcnow().isoformat()}] ✅ {manifest.name} deployed successfully at {manifest.defaultPath}")
        await broadcast_status(deployment_id)
        
        logger.info(f"Deployment {deployment_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Deployment {deployment_id} failed: {e}")
        status.status = 'failed'
        status.currentStep = 'Deployment failed'
        status.error = str(e)
        status.completedAt = datetime.utcnow()
        status.logs.append(f"[{datetime.utcnow().isoformat()}] ❌ Error: {str(e)}")
        await broadcast_status(deployment_id)


async def broadcast_status(deployment_id: str):
    """Broadcast status update to all connected WebSocket clients"""
    if deployment_id not in active_connections:
        return
    
    status = deployment_statuses.get(deployment_id)
    if not status:
        return
    
    # Serialize status
    status_dict = status.model_dump()
    status_dict['startedAt'] = status.startedAt.isoformat()
    if status.completedAt:
        status_dict['completedAt'] = status.completedAt.isoformat()
    
    # Remove disconnected clients and broadcast
    to_remove = []
    for i, websocket in enumerate(active_connections[deployment_id]):
        try:
            await websocket.send_json(status_dict)
        except Exception as e:
            logger.warning(f"Failed to send to websocket: {e}")
            to_remove.append(i)
    
    # Remove failed connections
    for i in reversed(to_remove):
        active_connections[deployment_id].pop(i)


@router.get("/deploy/{deployment_id}/status", response_model=DeploymentStatus)
async def get_deployment_status(
    deployment_id: str,
    token_payload: dict = Depends(verify_admin_token)
):
    """Get deployment status"""
    status = deployment_statuses.get(deployment_id)
    
    if not status:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    return status


@router.get("/deploy/{deployment_id}/stream")
async def stream_deployment_status(
    deployment_id: str,
    request: Request,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    SSE endpoint for real-time deployment status streaming.
    
    Returns Server-Sent Events with deployment status updates.
    Much more reliable than WebSockets and works well with Next.js.
    
    Usage: EventSource('/api/v1/deployment/deploy/{id}/stream?token=...')
    """
    
    async def event_generator():
        """Generate SSE events for deployment status"""
        last_log_count = 0
        last_status = None
        
        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break
            
            status = deployment_statuses.get(deployment_id)
            
            if not status:
                yield f"event: error\ndata: {json.dumps({'error': 'Deployment not found'})}\n\n"
                break
            
            # Check if there are new logs
            current_log_count = len(status.logs)
            current_status = status.status
            
            # Send update if logs changed or status changed
            if current_log_count > last_log_count or current_status != last_status:
                # Send only new logs
                new_logs = status.logs[last_log_count:]
                
                data = {
                    'deploymentId': status.deploymentId,
                    'status': status.status,
                    'progress': status.progress,
                    'currentStep': status.currentStep,
                    'logs': new_logs,  # Only send new logs
                    'totalLogs': current_log_count,
                    'completedAt': status.completedAt.isoformat() if status.completedAt else None,
                    'error': status.error
                }
                
                yield f"data: {json.dumps(data)}\n\n"
                
                last_log_count = current_log_count
                last_status = current_status
            
            # Check if deployment is complete
            if status.status in ['completed', 'failed']:
                # Send one final update with all logs
                data = {
                    'deploymentId': status.deploymentId,
                    'status': status.status,
                    'progress': status.progress,
                    'currentStep': status.currentStep,
                    'logs': status.logs,  # Send all logs on completion
                    'totalLogs': len(status.logs),
                    'completedAt': status.completedAt.isoformat() if status.completedAt else None,
                    'error': status.error,
                    'final': True
                }
                yield f"event: complete\ndata: {json.dumps(data)}\n\n"
                break
            
            # Wait a short time before checking again (500ms for responsive updates)
            await asyncio.sleep(0.5)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@router.websocket("/deploy/{deployment_id}/logs")
async def deployment_logs_websocket(
    websocket: WebSocket,
    deployment_id: str
):
    """
    WebSocket endpoint for streaming deployment logs.
    
    Authentication can be done via query parameter: ?token=<jwt>
    """
    await websocket.accept()
    
    # Get token from query parameter
    token = websocket.query_params.get('token')
    if not token:
        await websocket.send_json({"error": "Authentication required. Pass token as query parameter."})
        await websocket.close(code=4001)
        return
    
    # Validate token via authz service
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{config.authz_url}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0
            )
            if response.status_code != 200:
                await websocket.send_json({"error": "Invalid token"})
                await websocket.close(code=4001)
                return
    except Exception as e:
        logger.error(f"WebSocket auth failed: {e}")
        await websocket.send_json({"error": "Authentication failed"})
        await websocket.close(code=4001)
        return
    
    if deployment_id not in deployment_statuses:
        await websocket.send_json({"error": "Deployment not found"})
        await websocket.close(code=4004)
        return
    
    # Add to active connections
    if deployment_id not in active_connections:
        active_connections[deployment_id] = []
    active_connections[deployment_id].append(websocket)
    
    logger.info(f"WebSocket connected for deployment {deployment_id}")
    
    try:
        # Send current status immediately
        status = deployment_statuses[deployment_id]
        status_dict = status.model_dump()
        status_dict['startedAt'] = status.startedAt.isoformat()
        if status.completedAt:
            status_dict['completedAt'] = status.completedAt.isoformat()
        await websocket.send_json(status_dict)
        
        # Keep connection alive until deployment completes or client disconnects
        while True:
            try:
                # Wait for messages from client (ping/pong or close)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == 'ping':
                    await websocket.send_text('pong')
            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await websocket.send_json({"heartbeat": True})
                except:
                    break
            
            # Check if deployment is complete
            status = deployment_statuses.get(deployment_id)
            if status and status.status in ['completed', 'failed']:
                status_dict = status.model_dump()
                status_dict['startedAt'] = status.startedAt.isoformat()
                if status.completedAt:
                    status_dict['completedAt'] = status.completedAt.isoformat()
                await websocket.send_json({"final": True, "status": status_dict})
                break
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for deployment {deployment_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Remove from active connections
        if deployment_id in active_connections:
            try:
                active_connections[deployment_id].remove(websocket)
            except ValueError:
                pass


@router.get("/health")
async def deployment_service_health():
    """Health check endpoint"""
    return {
        "service": "deployment",
        "status": "healthy",
        "port": config.port,
        "activeDeployments": len([
            s for s in deployment_statuses.values() 
            if s.status not in ['completed', 'failed']
        ])
    }


@router.get("/deployments")
async def list_deployments(
    token_payload: dict = Depends(verify_admin_token)
):
    """List recent deployments"""
    deployments = []
    for status in deployment_statuses.values():
        status_dict = status.model_dump()
        status_dict['startedAt'] = status.startedAt.isoformat()
        if status.completedAt:
            status_dict['completedAt'] = status.completedAt.isoformat()
        deployments.append(status_dict)
    
    # Sort by startedAt descending
    deployments.sort(key=lambda x: x['startedAt'], reverse=True)
    return deployments[:50]  # Last 50 deployments


class LocalDevApp(BaseModel):
    """A local dev app with its manifest."""
    dirName: str
    manifest: dict


class ListLocalDevAppsResponse(BaseModel):
    """Response for listing local dev apps."""
    devAppsDir: str
    apps: list[LocalDevApp]
    error: str | None = None


@router.get("/list-local-dev-apps", response_model=ListLocalDevAppsResponse)
async def list_local_dev_apps(
    token_payload: dict = Depends(verify_admin_token)
):
    """
    List all local dev directories that contain valid busibox.json manifests.
    
    Scans /srv/dev-apps (the mounted DEV_APPS_DIR) for subdirectories
    and returns those that have valid manifest files.
    """
    import json
    import os
    
    dev_apps_path = "/srv/dev-apps"
    
    # Check if dev-apps is mounted
    if not os.path.isdir(dev_apps_path):
        return ListLocalDevAppsResponse(
            devAppsDir="(not configured)",
            apps=[],
            error="DEV_APPS_DIR is not configured. Run 'make configure' -> Docker Configuration -> Configure Dev Apps Directory."
        )
    
    # Get the actual host path from environment if available
    # DEV_APPS_DIR_HOST is passed through docker-compose with the host path
    # DEV_APPS_DIR is the container path (/srv/dev-apps)
    host_dev_apps_dir = os.environ.get("DEV_APPS_DIR_HOST") or os.environ.get("DEV_APPS_DIR", dev_apps_path)
    
    apps: list[LocalDevApp] = []
    
    try:
        # List all subdirectories
        for entry in os.listdir(dev_apps_path):
            entry_path = os.path.join(dev_apps_path, entry)
            
            # Skip non-directories
            if not os.path.isdir(entry_path):
                continue
            
            # Skip hidden directories
            if entry.startswith('.'):
                continue
            
            # Check for busibox.json
            manifest_path = os.path.join(entry_path, "busibox.json")
            if not os.path.isfile(manifest_path):
                continue
            
            # Try to parse manifest
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                
                # Basic validation - must have required fields
                required_fields = ['name', 'id', 'version', 'defaultPath', 'defaultPort']
                if all(f in manifest for f in required_fields):
                    apps.append(LocalDevApp(
                        dirName=entry,
                        manifest=manifest
                    ))
            except (json.JSONDecodeError, IOError):
                # Skip invalid manifests
                continue
    except Exception as e:
        logger.error(f"Error listing local dev apps: {e}")
        return ListLocalDevAppsResponse(
            devAppsDir=host_dev_apps_dir,
            apps=[],
            error=f"Failed to scan dev apps directory: {str(e)}"
        )
    
    # Sort by app name
    apps.sort(key=lambda a: a.manifest.get('name', a.dirName).lower())
    
    logger.info(f"Found {len(apps)} local dev apps in {dev_apps_path}")
    
    return ListLocalDevAppsResponse(
        devAppsDir=host_dev_apps_dir,
        apps=apps
    )


class LocalDevValidateRequest(BaseModel):
    dirName: str


class LocalDevValidateResponse(BaseModel):
    valid: bool
    manifest: dict | None = None
    dirPath: str | None = None
    error: str | None = None


@router.post("/validate-local-dev", response_model=LocalDevValidateResponse)
async def validate_local_dev(
    request: LocalDevValidateRequest,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Validate a local dev directory contains a valid busibox.json manifest.
    
    In Docker, the dev-apps directory is mounted directly at /srv/dev-apps,
    so we can check the filesystem directly without docker exec.
    """
    import json
    import os
    
    dir_name = request.dirName
    
    # Security: validate dir name
    if not dir_name or not all(c.isalnum() or c in '-_' for c in dir_name):
        return LocalDevValidateResponse(
            valid=False,
            error="Invalid directory name. Use only letters, numbers, hyphens, and underscores."
        )
    
    dev_path = f"/srv/dev-apps/{dir_name}"
    manifest_path = f"{dev_path}/busibox.json"
    
    # Check if dev-apps is mounted and directory exists
    if not os.path.isdir("/srv/dev-apps"):
        return LocalDevValidateResponse(
            valid=False,
            error="DEV_APPS_DIR is not configured. Run 'make configure' -> Docker Configuration -> Configure Dev Apps Directory."
        )
    
    if not os.path.isdir(dev_path):
        # List available directories for helpful error
        try:
            available = [d for d in os.listdir("/srv/dev-apps") if os.path.isdir(f"/srv/dev-apps/{d}")]
            if available:
                return LocalDevValidateResponse(
                    valid=False,
                    error=f"Directory '{dir_name}' not found. Available directories: {', '.join(available[:5])}"
                )
        except Exception:
            pass
        
        return LocalDevValidateResponse(
            valid=False,
            error=f"Directory not found: {dir_name}. Make sure DEV_APPS_DIR points to a directory containing '{dir_name}'."
        )
    
    # Check if manifest exists
    if not os.path.isfile(manifest_path):
        return LocalDevValidateResponse(
            valid=False,
            error=f"No busibox.json found in {dir_name}/. Create a manifest file for your app."
        )
    
    # Read and parse manifest
    try:
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        return LocalDevValidateResponse(
            valid=False,
            error=f"Invalid JSON in busibox.json: {e}"
        )
    except Exception as e:
        return LocalDevValidateResponse(
            valid=False,
            error=f"Failed to read manifest: {e}"
        )
    
    # Basic manifest validation
    required_fields = ['name', 'id', 'version', 'defaultPath', 'defaultPort', 'healthEndpoint']
    missing = [f for f in required_fields if f not in manifest]
    if missing:
        return LocalDevValidateResponse(
            valid=False,
            error=f"Missing required fields in manifest: {', '.join(missing)}"
        )
    
    logger.info(f"Validated local dev directory: {dev_path} for app {manifest.get('name')}")
    
    return LocalDevValidateResponse(
        valid=True,
        manifest=manifest,
        dirPath=dev_path
    )


class VersionCheckRequest(BaseModel):
    githubOwner: str
    githubRepo: str
    currentVersion: str | None = None
    githubToken: str | None = None


class VersionCheckResponse(BaseModel):
    latestVersion: str
    latestReleaseUrl: str | None = None
    latestReleaseNotes: str | None = None
    publishedAt: str | None = None
    updateAvailable: bool


@router.post("/version-check", response_model=VersionCheckResponse)
async def check_version(
    request: VersionCheckRequest,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Check for latest version of a GitHub repo.
    
    Looks for GitHub releases first, falls back to latest commit on main/master.
    """
    import httpx
    
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Busibox-Deploy-Service"
    }
    if request.githubToken:
        headers["Authorization"] = f"token {request.githubToken}"
    
    try:
        async with httpx.AsyncClient() as client:
            # Try releases first
            releases_url = f"https://api.github.com/repos/{request.githubOwner}/{request.githubRepo}/releases/latest"
            response = await client.get(releases_url, headers=headers, timeout=10.0)
            
            if response.status_code == 200:
                release = response.json()
                latest_version = release.get("tag_name", "")
                
                # Determine if update is available
                update_available = False
                if request.currentVersion and latest_version:
                    # Simple version comparison (removes 'v' prefix if present)
                    current = request.currentVersion.lstrip('v')
                    latest = latest_version.lstrip('v')
                    update_available = current != latest
                
                return VersionCheckResponse(
                    latestVersion=latest_version,
                    latestReleaseUrl=release.get("html_url"),
                    latestReleaseNotes=release.get("body", "")[:500] if release.get("body") else None,
                    publishedAt=release.get("published_at"),
                    updateAvailable=update_available
                )
            
            # No releases, check latest commit
            commits_url = f"https://api.github.com/repos/{request.githubOwner}/{request.githubRepo}/commits?per_page=1"
            response = await client.get(commits_url, headers=headers, timeout=10.0)
            
            if response.status_code == 200:
                commits = response.json()
                if commits:
                    commit = commits[0]
                    commit_sha = commit.get("sha", "")[:7]
                    
                    update_available = False
                    if request.currentVersion:
                        update_available = request.currentVersion != commit_sha
                    
                    return VersionCheckResponse(
                        latestVersion=commit_sha,
                        latestReleaseUrl=commit.get("html_url"),
                        latestReleaseNotes=commit.get("commit", {}).get("message", "")[:200],
                        publishedAt=commit.get("commit", {}).get("author", {}).get("date"),
                        updateAvailable=update_available
                    )
            
            raise HTTPException(status_code=404, detail="Could not fetch version information")
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="GitHub API timeout")
    except Exception as e:
        logger.error(f"Version check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class UndeployRequest(BaseModel):
    """Request to undeploy an app."""
    appId: str
    removeVolumes: bool = True


class UndeployResponse(BaseModel):
    """Response from undeploy operation."""
    success: bool
    appId: str
    logs: list[str]
    error: str | None = None


@router.post("/undeploy", response_model=UndeployResponse)
async def undeploy_app(
    request: UndeployRequest,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Undeploy an app, removing all associated resources.
    
    This endpoint:
    1. Stops the running application
    2. Removes Docker volumes (node_modules, .next cache)
    3. Removes nginx configuration
    4. Cleans up build artifacts
    
    Use this when:
    - You need to fix deployment issues (package-lock.json sync errors)
    - You want to completely remove an app from the system
    - You're troubleshooting volume mount issues
    
    After undeploying, you can redeploy the app with a fresh state.
    """
    app_id = request.appId
    logs: list[str] = []
    
    logger.info(f"Starting undeploy for {app_id} by user {token_payload.get('user_id')}")
    
    try:
        success = await container_undeploy_app(
            app_id=app_id,
            logs=logs,
            remove_volumes=request.removeVolumes
        )
        
        if success:
            logger.info(f"Undeploy completed successfully for {app_id}")
            return UndeployResponse(
                success=True,
                appId=app_id,
                logs=logs
            )
        else:
            logger.error(f"Undeploy failed for {app_id}")
            return UndeployResponse(
                success=False,
                appId=app_id,
                logs=logs,
                error="Undeploy operation failed. Check logs for details."
            )
            
    except Exception as e:
        logger.error(f"Undeploy error for {app_id}: {e}")
        logs.append(f"❌ Error: {str(e)}")
        return UndeployResponse(
            success=False,
            appId=app_id,
            logs=logs,
            error=str(e)
        )


@router.post("/stop/{app_id}")
async def stop_app_endpoint(
    app_id: str,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Stop a running app without removing volumes or configuration.
    
    This is a lighter operation than undeploy - it just stops the process.
    Use undeploy if you need to clean up volumes and configurations.
    """
    logs: list[str] = []
    
    logger.info(f"Stopping app {app_id} by user {token_payload.get('user_id')}")
    
    try:
        success = await container_stop_app(app_id, logs)
        
        return {
            "success": success,
            "appId": app_id,
            "logs": logs
        }
    except Exception as e:
        logger.error(f"Stop error for {app_id}: {e}")
        return {
            "success": False,
            "appId": app_id,
            "logs": logs,
            "error": str(e)
        }
