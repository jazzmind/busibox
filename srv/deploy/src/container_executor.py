"""
Container Executor - User App Deployment (Sandboxed)
====================================================

This module handles deployment of USER APPS (untrusted/external applications).
All operations are executed inside the user-apps container for security isolation.

For CORE APPS (ai-portal, agent-manager, etc.), use bridge_executor.py instead,
which delegates to Makefile/Ansible with full host access.

Security Model:
- User apps run in isolated user-apps container
- No direct host access from user app code
- GitHub clone happens inside container
- npm install/build happens inside container
- Prevents untrusted code from infecting host

Execution Methods:
- Docker: Uses `docker exec` to run commands in user-apps container
- LXC: Uses SSH to run commands in user-apps-lxc container

Volume Management for Dev Apps (Docker):
----------------------------------------
For local dev apps, we need to handle the platform mismatch between host (macOS/Windows)
and container (Linux). The host's node_modules contains native binaries for the wrong platform.

Solution: Use Docker named volumes to shadow node_modules and .next directories.
These volumes persist across container restarts and contain Linux-native binaries.

Volume naming: {CONTAINER_PREFIX}-{app_id}-node-modules, {CONTAINER_PREFIX}-{app_id}-next-cache
"""

import asyncio
import logging
import os
import json
from typing import Tuple, List, Optional, Dict, Set
from .models import BusiboxManifest, DeploymentConfig
from .config import config

logger = logging.getLogger(__name__)

# Container name for user apps in Docker
# Matches docker-compose.yml: ${CONTAINER_PREFIX:-dev}-user-apps
CONTAINER_PREFIX = os.environ.get("CONTAINER_PREFIX", "dev")
USER_APPS_CONTAINER = f"{CONTAINER_PREFIX}-user-apps"

# Track if we've already ensured the container is running this session
_user_apps_container_checked = False

# Track which app volumes are currently mounted
_mounted_app_volumes: Set[str] = set()

# Registry of running apps - stored in deploy service's filesystem
# so it survives both deploy service restarts AND container recreation
# Format: {app_id: {"app_path": str, "start_command": str, "env_vars": dict}}
APPS_REGISTRY_FILE = "/tmp/busibox_running_apps_registry.json"


def register_running_app(app_id: str, app_path: str, start_command: str, env_vars: dict):
    """Register an app as running so it can be restarted after container recreation.
    
    Stores registry in deploy service's filesystem (not inside container).
    """
    registry = get_running_apps_registry()
    registry[app_id] = {
        "app_path": app_path,
        "start_command": start_command,
        "env_vars": env_vars
    }
    
    # Write to local file (deploy service filesystem)
    try:
        with open(APPS_REGISTRY_FILE, 'w') as f:
            json.dump(registry, f)
        logger.info(f"Registered running app: {app_id}")
    except Exception as e:
        logger.error(f"Failed to write apps registry: {e}")


def unregister_running_app(app_id: str):
    """Remove an app from the running apps registry."""
    registry = get_running_apps_registry()
    if app_id in registry:
        del registry[app_id]
        try:
            with open(APPS_REGISTRY_FILE, 'w') as f:
                json.dump(registry, f)
            logger.info(f"Unregistered app: {app_id}")
        except Exception as e:
            logger.error(f"Failed to write apps registry: {e}")


def get_running_apps_registry() -> Dict[str, Dict]:
    """Get the running apps registry from local file."""
    try:
        with open(APPS_REGISTRY_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def reset_user_apps_container_check():
    """Reset the container check flag - call this if container needs to be recreated."""
    global _user_apps_container_checked
    _user_apps_container_checked = False


def get_app_volume_names(app_id: str) -> Dict[str, str]:
    """Get the Docker volume names for an app's node_modules and .next cache."""
    return {
        'node_modules': f"{CONTAINER_PREFIX}-{app_id}-node-modules",
        'next_cache': f"{CONTAINER_PREFIX}-{app_id}-next-cache",
    }


def is_docker_environment() -> bool:
    """Check if running in Docker (local development)"""
    # In Docker, POSTGRES_HOST is typically 'postgres' (container name) not an IP
    return not config.postgres_host.startswith('10.')


async def create_app_volumes(app_id: str, logs: List[str]) -> bool:
    """
    Create Docker volumes for an app's node_modules and .next directories.
    These volumes will contain Linux-native binaries, solving the platform mismatch.
    
    Returns True if volumes are ready (created or already exist).
    """
    volume_names = get_app_volume_names(app_id)
    
    for volume_type, volume_name in volume_names.items():
        # Check if volume exists
        check_cmd = ['docker', 'volume', 'inspect', volume_name]
        proc = await asyncio.create_subprocess_exec(
            *check_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        
        if proc.returncode == 0:
            logger.info(f"Volume {volume_name} already exists")
            continue
        
        # Create volume
        logs.append(f"📦 Creating volume: {volume_name}")
        create_cmd = ['docker', 'volume', 'create', volume_name]
        proc = await asyncio.create_subprocess_exec(
            *create_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logs.append(f"❌ Failed to create volume {volume_name}: {error_msg}")
            return False
        
        logger.info(f"Created volume: {volume_name}")
    
    return True


async def get_mounted_dev_apps() -> Set[str]:
    """
    Get the set of dev app IDs that currently have volumes mounted in user-apps container.
    Reads from container labels or inspects current mounts.
    """
    # Check container's current volume mounts
    inspect_cmd = ['docker', 'inspect', USER_APPS_CONTAINER, '--format', '{{json .Mounts}}']
    proc = await asyncio.create_subprocess_exec(
        *inspect_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        return set()
    
    try:
        mounts = json.loads(stdout.decode().strip())
        mounted_apps = set()
        
        for mount in mounts:
            if mount.get('Type') == 'volume':
                name = mount.get('Name', '')
                # Parse volume name: {prefix}-{app_id}-node-modules or {prefix}-{app_id}-next-cache
                if name.startswith(f"{CONTAINER_PREFIX}-") and name.endswith('-node-modules'):
                    app_id = name[len(f"{CONTAINER_PREFIX}-"):-len('-node-modules')]
                    mounted_apps.add(app_id)
        
        return mounted_apps
    except (json.JSONDecodeError, KeyError):
        return set()


async def recreate_user_apps_with_volumes(dev_app_ids: Set[str], logs: List[str]) -> Tuple[bool, str]:
    """
    Recreate the user-apps container with volume mounts for the specified dev apps.
    
    This is needed because Docker doesn't support adding volumes to a running container.
    We have to stop, remove, and recreate the container with the new mounts.
    
    Args:
        dev_app_ids: Set of app IDs that need volume mounts for node_modules/.next
        logs: Log list for deployment progress
    
    Returns:
        Tuple of (success, message)
    """
    global _user_apps_container_checked
    
    logger.info(f"Recreating user-apps container with volumes for: {dev_app_ids}")
    
    # Get busibox host path for docker compose
    busibox_host_path = os.environ.get("BUSIBOX_HOST_PATH", "")
    if not busibox_host_path:
        return False, "BUSIBOX_HOST_PATH environment variable not set"
    
    # Get DEV_APPS_DIR for the bind mount
    dev_apps_dir_host = os.environ.get("DEV_APPS_DIR_HOST") or os.environ.get("DEV_APPS_DIR", "")
    if not dev_apps_dir_host:
        # Try to resolve relative to busibox path
        dev_apps_dir_host = os.path.join(busibox_host_path, "dev-apps")
    
    # Network name is explicitly set in docker-compose.yml: ${CONTAINER_PREFIX:-dev}-busibox-net
    network_name = f"{CONTAINER_PREFIX}-busibox-net"
    
    # Stop existing container (don't remove - let docker compose handle it)
    logs.append(f"🔄 Stopping {USER_APPS_CONTAINER} for volume update...")
    stop_cmd = ['docker', 'stop', USER_APPS_CONTAINER]
    proc = await asyncio.create_subprocess_exec(
        *stop_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    # Ignore errors - container might not exist
    
    # Remove container to recreate with new volumes
    rm_cmd = ['docker', 'rm', '-f', USER_APPS_CONTAINER]
    proc = await asyncio.create_subprocess_exec(
        *rm_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    
    # Build volume mount arguments for docker run
    volume_args = [
        # Base volumes from docker-compose
        '-v', 'user_apps_data:/srv/apps',
        '-v', f'{dev_apps_dir_host}:/srv/dev-apps',
    ]
    
    # Add per-app volume mounts for node_modules and .next
    for app_id in dev_app_ids:
        volumes = get_app_volume_names(app_id)
        volume_args.extend([
            '-v', f'{volumes["node_modules"]}:/srv/dev-apps/{app_id}/node_modules',
            '-v', f'{volumes["next_cache"]}:/srv/dev-apps/{app_id}/.next',
        ])
    
    # Run new container with all volume mounts
    logs.append(f"🚀 Starting {USER_APPS_CONTAINER} with app volumes...")
    
    # Get GitHub token for npm authentication (needed for @jazzmind/busibox-app)
    github_token = os.environ.get("GITHUB_AUTH_TOKEN", "")
    
    # Build compose project name for labels
    compose_project = os.environ.get("COMPOSE_PROJECT_NAME", f"{CONTAINER_PREFIX}-busibox")
    
    # Build environment args
    env_args = ['-e', 'NODE_ENV=development']
    if github_token:
        env_args.extend(['-e', f'GITHUB_AUTH_TOKEN={github_token}'])
    
    # Entrypoint script that sets up npm auth, installs deps, and tails app logs
    # The tail -F /tmp/*.log streams all app logs to container stdout for Docker Desktop
    # Using tail -F (capital F) follows by name, so it picks up new log files as apps start
    entrypoint_script = (
        'if [ -n "$GITHUB_AUTH_TOKEN" ]; then '
        'echo "//npm.pkg.github.com/:_authToken=$GITHUB_AUTH_TOKEN" > /root/.npmrc && '
        'echo "@jazzmind:registry=https://npm.pkg.github.com" >> /root/.npmrc; '
        'fi && '
        'apt-get update && apt-get install -y git curl procps && '
        # Create a marker log file so tail -F has something to follow initially
        'touch /tmp/user-apps.log && '
        'echo "[user-apps] Container ready, waiting for app deployments..." > /tmp/user-apps.log && '
        # Use exec to replace shell with tail, streaming all .log files to stdout
        'exec tail -F /tmp/*.log'
    )
    
    run_cmd = [
        'docker', 'run', '-d',
        '--name', USER_APPS_CONTAINER,
        '--hostname', 'user-apps',
        '--network', network_name,
        '--restart', 'unless-stopped',
        # Labels to associate with compose project
        '--label', f'com.docker.compose.project={compose_project}',
        '--label', f'com.docker.compose.service=user-apps',
        # Environment variables
        *env_args,
        *volume_args,
        'node:20-slim',
        '/bin/bash', '-c', entrypoint_script
    ]
    
    logger.info(f"Running: {' '.join(run_cmd)}")
    
    proc = await asyncio.create_subprocess_exec(
        *run_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        error_msg = stderr.decode().strip()
        stdout_msg = stdout.decode().strip()
        logs.append(f"❌ Failed to start container: {error_msg}")
        if stdout_msg:
            logs.append(f"   stdout: {stdout_msg}")
        logger.error(f"docker run failed: returncode={proc.returncode}, stderr={error_msg}, stdout={stdout_msg}")
        return False, f"Failed to start container: {error_msg}"
    
    container_id = stdout.decode().strip()[:12]
    logs.append(f"   Container ID: {container_id}")
    
    # Wait for container to be ready
    await asyncio.sleep(2)
    
    # Verify container is running
    check_cmd = ['docker', 'inspect', '-f', '{{.State.Running}}', USER_APPS_CONTAINER]
    proc = await asyncio.create_subprocess_exec(
        *check_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode == 0 and stdout.decode().strip() == 'true':
        _user_apps_container_checked = True
        logs.append(f"✅ {USER_APPS_CONTAINER} running with {len(dev_app_ids)} app volumes")
        
        # Restart previously running apps after container recreation
        # The container was destroyed, so all apps need to be restarted
        running_apps = get_running_apps_registry()
        logger.info(f"Running apps registry contains: {list(running_apps.keys())}")
        logger.info(f"dev_app_ids (apps needing volumes): {list(dev_app_ids)}")
        if running_apps:
            logs.append(f"🔄 Restarting {len(running_apps)} previously running apps...")
            for app_id_to_restart, app_info in running_apps.items():
                try:
                    # Wait a moment for container to stabilize
                    await asyncio.sleep(1)
                    logs.append(f"  ↪ Restarting {app_id_to_restart}...")
                    restart_logs: List[str] = []
                    success = await start_app(
                        app_id_to_restart,
                        restart_logs,
                        app_path=app_info["app_path"],
                        start_command=app_info["start_command"],
                        env_vars=app_info["env_vars"]
                    )
                    if success:
                        logs.append(f"  ✅ Restarted {app_id_to_restart}")
                    else:
                        logs.append(f"  ⚠️ Failed to restart {app_id_to_restart}: {'; '.join(restart_logs[-2:])}")
                except Exception as e:
                    logs.append(f"  ❌ Error restarting {app_id_to_restart}: {e}")
        
        return True, "Container recreated with volumes"
    
    return False, "Container failed to start"


async def ensure_app_volumes_mounted(app_id: str, logs: List[str]) -> bool:
    """
    Ensure that the volumes for a specific dev app are mounted in the user-apps container.
    If not, recreate the container with the necessary mounts.
    
    Returns True if volumes are mounted and ready.
    """
    # First, create the volumes if they don't exist
    if not await create_app_volumes(app_id, logs):
        logs.append(f"❌ Failed to create volumes for {app_id}")
        return False
    
    # Check what apps currently have volumes mounted
    current_mounted = await get_mounted_dev_apps()
    logger.info(f"Current mounted apps: {current_mounted}, checking for: {app_id}")
    
    if app_id in current_mounted:
        logs.append(f"✅ Volume mounts already present for {app_id}")
        logger.info(f"Volume mounts already present for {app_id}, returning True")
        return True
    
    # Need to add this app's volumes - recreate container with all mounted apps plus new one
    new_mounted = current_mounted | {app_id}
    
    logs.append(f"📦 Adding volume mounts for {app_id}...")
    logs.append(f"   Current mounted apps: {list(current_mounted) if current_mounted else 'none'}")
    logs.append(f"   Will mount: {list(new_mounted)}")
    
    success, msg = await recreate_user_apps_with_volumes(new_mounted, logs)
    
    if not success:
        logs.append(f"❌ Failed to mount volumes: {msg}")
    
    return success


async def ensure_user_apps_container_running(force_check: bool = False) -> Tuple[bool, str]:
    """
    Ensure the user-apps container is running before executing commands.
    Uses docker compose to start/build the container if needed.
    
    Args:
        force_check: If True, always check even if we've checked before this session.
                     Use this after operations that might have stopped the container.
    
    Returns:
        Tuple of (success, message)
    """
    global _user_apps_container_checked
    
    # Skip if we've already checked this session (unless force_check is True)
    if _user_apps_container_checked and not force_check:
        # Still verify the container is actually running (quick check)
        check_cmd = ['docker', 'inspect', '-f', '{{.State.Running}}', USER_APPS_CONTAINER]
        proc = await asyncio.create_subprocess_exec(
            *check_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        
        if proc.returncode == 0 and stdout.decode().strip() == 'true':
            return True, "Container running (verified)"
        else:
            # Container stopped unexpectedly - reset flag and continue to start it
            logger.warning(f"{USER_APPS_CONTAINER} container stopped unexpectedly, will restart")
            _user_apps_container_checked = False
    
    logger.info(f"Checking if {USER_APPS_CONTAINER} container is running...")
    
    # Check if container exists and is running
    check_cmd = ['docker', 'inspect', '-f', '{{.State.Running}}', USER_APPS_CONTAINER]
    proc = await asyncio.create_subprocess_exec(
        *check_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode == 0 and stdout.decode().strip() == 'true':
        logger.info(f"{USER_APPS_CONTAINER} is already running")
        _user_apps_container_checked = True
        return True, "Container already running"
    
    # Container doesn't exist or isn't running - need to start it via docker compose
    logger.info(f"{USER_APPS_CONTAINER} not running, starting via docker compose...")
    
    # First, remove any stopped container with the same name (prevents "name already in use" error)
    rm_cmd = ['docker', 'rm', '-f', USER_APPS_CONTAINER]
    proc = await asyncio.create_subprocess_exec(
        *rm_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    # Ignore errors - container might not exist at all
    logger.debug(f"Removed old {USER_APPS_CONTAINER} container (if any)")
    
    # Get busibox host path for docker compose
    busibox_host_path = os.environ.get("BUSIBOX_HOST_PATH", "")
    if not busibox_host_path:
        logger.error("BUSIBOX_HOST_PATH not set - cannot start user-apps container")
        return False, "BUSIBOX_HOST_PATH environment variable not set"
    
    # Build compose command
    compose_files = os.environ.get("COMPOSE_FILES", "-f docker-compose.yml -f docker-compose.local-dev.yml")
    compose_project = os.environ.get("COMPOSE_PROJECT_NAME", f"{CONTAINER_PREFIX}-busibox")
    
    # Split compose files into separate -f arguments
    compose_args = compose_files.split()
    
    # Build environment for docker compose - pass through required variables
    # These are needed for volume mounts and container naming
    compose_env = os.environ.copy()
    
    # Ensure DEV_APPS_DIR is set for the user-apps container volume mount
    dev_apps_dir = os.environ.get("DEV_APPS_DIR_HOST") or os.environ.get("DEV_APPS_DIR", "")
    if dev_apps_dir:
        compose_env["DEV_APPS_DIR"] = dev_apps_dir
        logger.info(f"Using DEV_APPS_DIR={dev_apps_dir} for user-apps volume mount")
    else:
        logger.warning("DEV_APPS_DIR not set - user-apps will use default ./dev-apps")
    
    # Ensure CONTAINER_PREFIX is set
    compose_env["CONTAINER_PREFIX"] = CONTAINER_PREFIX
    
    # Start user-apps service via docker compose
    compose_cmd = [
        'docker', 'compose',
        '-p', compose_project,
        *compose_args,
        'up', '-d', 'user-apps'
    ]
    
    logger.info(f"Running: {' '.join(compose_cmd)} in {busibox_host_path}")
    
    proc = await asyncio.create_subprocess_exec(
        *compose_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=busibox_host_path,
        env=compose_env
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        error_msg = stderr.decode().strip() if stderr else stdout.decode().strip()
        logger.error(f"Failed to start user-apps container: {error_msg}")
        return False, f"Failed to start user-apps: {error_msg}"
    
    # Wait a moment for container to be ready
    await asyncio.sleep(2)
    
    # Verify container is now running
    proc = await asyncio.create_subprocess_exec(
        *check_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode == 0 and stdout.decode().strip() == 'true':
        logger.info(f"{USER_APPS_CONTAINER} started successfully")
        _user_apps_container_checked = True
        return True, "Container started successfully"
    
    return False, "Container failed to start after docker compose up"


async def execute_ssh_command(host: str, command: str, timeout: int = 300) -> Tuple[str, str, int]:
    """Execute command on remote host via SSH (for LXC production)"""
    ssh_command = [
        'ssh',
        '-F', '/dev/null',  # Ignore user SSH config
        '-i', config.ssh_key_path,
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-o', 'ConnectTimeout=10',
        f'root@{host}',
        command
    ]
    
    proc = await asyncio.create_subprocess_exec(
        *ssh_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(), stderr.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        return "", "Command timed out", 1


async def execute_docker_command(command: str, timeout: int = 300, _retry: bool = True) -> Tuple[str, str, int]:
    """Execute command in Docker user-apps container via docker exec
    
    Args:
        command: Shell command to execute
        timeout: Timeout in seconds
        _retry: Internal flag to prevent infinite retry loops
    """
    docker_command = [
        'docker', 'exec', USER_APPS_CONTAINER,
        '/bin/bash', '-c', command
    ]
    
    # Log short commands, truncate long ones
    cmd_preview = command[:100] + "..." if len(command) > 100 else command
    logger.debug(f"Executing in container: {cmd_preview}")
    
    proc = await asyncio.create_subprocess_exec(
        *docker_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_str = stdout.decode()
        stderr_str = stderr.decode()
        returncode = proc.returncode or 0
        
        # Check if container was not running (common Docker error)
        if returncode != 0 and _retry and ("is not running" in stderr_str or "No such container" in stderr_str):
            logger.warning(f"Container not running, attempting to restart...")
            success, msg = await ensure_user_apps_container_running(force_check=True)
            if success:
                logger.info("Container restarted, retrying command...")
                return await execute_docker_command(command, timeout, _retry=False)
            else:
                logger.error(f"Failed to restart container: {msg}")
                return "", f"Container not running and restart failed: {msg}", 1
        
        logger.debug(f"Command completed with code {returncode}")
        return stdout_str, stderr_str, returncode
    except asyncio.TimeoutError:
        logger.error(f"Command timed out after {timeout}s: {cmd_preview}")
        proc.kill()
        return "", f"Command timed out after {timeout}s", 1


async def execute_in_container(
    command: str,
    host: Optional[str] = None,
    environment: str = 'production',
    timeout: int = 300
) -> Tuple[str, str, int]:
    """Execute command in container - routes to SSH or docker exec based on environment"""
    if is_docker_environment():
        return await execute_docker_command(command, timeout)
    else:
        # Use user_apps container for external app deployments
        if environment == 'staging':
            default_host = config.user_apps_container_ip_staging
        else:
            default_host = config.user_apps_container_ip
        target_host = host or default_host
        return await execute_ssh_command(target_host, command, timeout)


async def check_dev_app_exists(app_id: str) -> bool:
    """Check if app exists in dev-apps directory (takes precedence over git clone)"""
    command = f"test -d /srv/dev-apps/{app_id}"
    stdout, stderr, code = await execute_in_container(command)
    return code == 0


async def get_app_path(app_id: str) -> str:
    """Get the app directory path - dev-apps takes precedence over apps"""
    if await check_dev_app_exists(app_id):
        logger.info(f"Dev mode detected - using /srv/dev-apps/{app_id}")
        return f"/srv/dev-apps/{app_id}"
    return f"/srv/apps/{app_id}"


async def clone_or_update_repo(
    manifest: BusiboxManifest,
    deploy_config: DeploymentConfig,
    logs: List[str]
) -> Tuple[bool, str]:
    """Clone or update git repository. Skips if dev mode or dev-apps path exists."""
    
    app_id = manifest.id
    
    # Check for explicit dev mode from deployment config
    if deploy_config.devMode and deploy_config.localDevDir:
        dev_path = f"/srv/dev-apps/{deploy_config.localDevDir}"
        logs.append(f"📦 Dev mode: using local source at {dev_path}")
        return True, dev_path
    
    # Also check for implicit dev mode (dev-apps directory exists for this app)
    if await check_dev_app_exists(app_id):
        logs.append(f"📦 Dev mode detected: using local source at /srv/dev-apps/{app_id}")
        return True, f"/srv/dev-apps/{app_id}"
    
    # GitHub mode - clone or update repo
    app_path = f"/srv/apps/{app_id}"
    
    # Validate we have GitHub repo info
    if not deploy_config.githubRepoOwner or not deploy_config.githubRepoName:
        logs.append("❌ No GitHub repository configured and no local dev directory found")
        return False, app_path
    
    repo_url = f"https://github.com/{deploy_config.githubRepoOwner}/{deploy_config.githubRepoName}.git"
    
    # If GitHub token provided, use authenticated URL
    if deploy_config.githubToken:
        repo_url = f"https://{deploy_config.githubToken}@github.com/{deploy_config.githubRepoOwner}/{deploy_config.githubRepoName}.git"
    
    # Check if repo already exists
    check_cmd = f"test -d {app_path}/.git"
    _, _, exists = await execute_in_container(check_cmd)
    
    if exists == 0:
        # Update existing repo
        logs.append(f"📥 Updating existing repository...")
        command = f"""
cd {app_path} && \
git fetch origin && \
git checkout {deploy_config.githubBranch} && \
git reset --hard origin/{deploy_config.githubBranch}
"""
    else:
        # Clone new repo
        logs.append(f"📥 Cloning repository from {deploy_config.githubRepoOwner}/{deploy_config.githubRepoName}...")
        command = f"""
mkdir -p /srv/apps && \
git clone --branch {deploy_config.githubBranch} --depth 1 {repo_url} {app_path}
"""
    
    stdout, stderr, code = await execute_in_container(command, timeout=600)
    
    if code != 0:
        logs.append(f"❌ Git operation failed: {stderr}")
        return False, app_path
    
    logs.append(f"✅ Repository ready at {app_path}")
    return True, app_path


async def install_dependencies(app_path: str, logs: List[str], github_token: Optional[str] = None) -> bool:
    """Install npm dependencies
    
    Args:
        app_path: Path to the app directory
        logs: List to append log messages
        github_token: Optional GitHub token for npm authentication with GitHub Package Registry
    """
    logs.append(f"📦 Installing dependencies...")
    
    # Check for package.json
    check_cmd = f"test -f {app_path}/package.json"
    _, _, exists = await execute_in_container(check_cmd)
    
    if exists != 0:
        logs.append("⚠️ No package.json found, skipping npm install")
        return True
    
    # Set up npm authentication for GitHub Package Registry if token provided
    # This is needed for private packages like @jazzmind/busibox-app
    if github_token:
        logs.append("🔐 Setting up npm authentication for GitHub Package Registry...")
        # Create .npmrc with GitHub token for authentication
        npmrc_setup = f"""
echo "//npm.pkg.github.com/:_authToken={github_token}" > /root/.npmrc && \
echo "@jazzmind:registry=https://npm.pkg.github.com" >> /root/.npmrc
"""
        stdout, stderr, code = await execute_in_container(npmrc_setup)
        if code != 0:
            logs.append(f"⚠️ Failed to set up npm auth: {stderr or stdout}")
            # Continue anyway - might not need private packages
        else:
            logs.append("✅ npm auth configured")
    else:
        # Fall back to environment variable if no token passed
        env_token = os.environ.get("GITHUB_AUTH_TOKEN", "")
        if env_token:
            logs.append("🔐 Using GITHUB_AUTH_TOKEN from environment for npm auth...")
            npmrc_setup = f"""
echo "//npm.pkg.github.com/:_authToken={env_token}" > /root/.npmrc && \
echo "@jazzmind:registry=https://npm.pkg.github.com" >> /root/.npmrc
"""
            stdout, stderr, code = await execute_in_container(npmrc_setup)
            if code != 0:
                logs.append(f"⚠️ Failed to set up npm auth: {stderr or stdout}")
            else:
                logs.append("✅ npm auth configured from environment")
        else:
            logs.append("⚠️ No GitHub token available for npm auth - private packages may fail")
    
    # Check for package-lock.json to decide between npm ci and npm install
    check_lock_cmd = f"test -f {app_path}/package-lock.json"
    _, _, lock_exists = await execute_in_container(check_lock_cmd)
    
    # Determine which token to use for npm auth (project .npmrc uses ${GITHUB_AUTH_TOKEN} env var)
    effective_token = github_token or os.environ.get("GITHUB_AUTH_TOKEN", "")
    
    # Build the npm command with GITHUB_AUTH_TOKEN env var
    # This is needed because project-level .npmrc files reference ${GITHUB_AUTH_TOKEN}
    token_env = f'GITHUB_AUTH_TOKEN="{effective_token}" ' if effective_token else ''
    
    if lock_exists == 0:
        # Has package-lock.json, use npm ci for reproducible builds
        # IMPORTANT: Clear node_modules contents first because it's a Docker volume
        # npm ci tries to remove node_modules entirely which fails with ENOTEMPTY
        # when node_modules is a volume mount point
        logs.append("📦 Using npm ci (package-lock.json found)")
        logs.append("🧹 Clearing node_modules (Docker volume)...")
        clear_cmd = f"rm -rf {app_path}/node_modules/* {app_path}/node_modules/.* 2>/dev/null || true"
        await execute_in_container(clear_cmd)
        
        # Use --include=dev to ensure devDependencies are installed (needed for build tools like tailwindcss)
        command = f"""
cd {app_path} && \
{token_env}npm ci --legacy-peer-deps --include=dev 2>&1
"""
    else:
        # No package-lock.json, use npm install
        # Use --include=dev to ensure devDependencies are installed (needed for build tools like tailwindcss)
        logs.append("📦 Using npm install (no package-lock.json)")
        command = f"""
cd {app_path} && \
{token_env}npm install --legacy-peer-deps --include=dev 2>&1
"""
    
    stdout, stderr, code = await execute_in_container(command, timeout=600)
    
    if code != 0:
        logs.append(f"❌ npm install failed: {stderr or stdout}")
        return False
    
    logs.append("✅ Dependencies installed")
    return True


async def run_build(
    app_path: str,
    build_command: str,
    logs: List[str],
    env_vars: Optional[Dict[str, str]] = None,
) -> bool:
    """Run build command
    
    Important: Next.js reads `next.config.*` at build time. For apps that use
    `process.env.NEXT_PUBLIC_BASE_PATH` in `next.config.ts`, we must ensure
    `NEXT_PUBLIC_BASE_PATH` is present during build as well as at runtime,
    otherwise the generated output may target the wrong path.
    """
    logs.append(f"🔨 Building application...")

    export_lines = ""
    if env_vars:
        # Only export string values; wrap in double quotes.
        export_lines = "\n".join([f'export {k}="{v}"' for k, v in env_vars.items()])

    command = f"""
cd {app_path}
{export_lines}
{build_command} 2>&1
"""
    
    stdout, stderr, code = await execute_in_container(command, timeout=900)
    
    if code != 0:
        logs.append(f"❌ Build failed: {stderr or stdout}")
        return False
    
    # For Next.js standalone mode, copy static files
    copy_static = f"""
if [ -d "{app_path}/.next/standalone" ] && [ -d "{app_path}/.next/static" ]; then
    mkdir -p {app_path}/.next/standalone/.next
    cp -r {app_path}/.next/static {app_path}/.next/standalone/.next/
    if [ -d "{app_path}/public" ]; then
        cp -r {app_path}/public {app_path}/.next/standalone/
    fi
    echo "Static files copied for standalone mode"
fi
"""
    await execute_in_container(copy_static)
    
    logs.append("✅ Build completed")
    return True


async def run_migrations(
    app_path: str,
    manifest: BusiboxManifest,
    database_url: Optional[str],
    logs: List[str]
) -> bool:
    """Run database migrations if required"""
    
    if not manifest.database or not manifest.database.required:
        logs.append("ℹ️ No database required, skipping migrations")
        return True
    
    if not database_url:
        logs.append("⚠️ Database URL not provided, skipping migrations")
        return True
    
    logs.append("🗄️ Running database migrations...")
    
    schema_management = manifest.database.schemaManagement
    
    if schema_management == 'prisma':
        command = f"""
cd {app_path} && \
export DATABASE_URL="{database_url}" && \
npx prisma generate && \
npx prisma db push 2>&1
"""
    elif schema_management == 'migrations':
        command = f"""
cd {app_path} && \
export DATABASE_URL="{database_url}" && \
npm run migrate 2>&1
"""
    else:
        logs.append("ℹ️ Manual schema management - skipping automatic migrations")
        return True
    
    stdout, stderr, code = await execute_in_container(command, timeout=300)
    
    if code != 0:
        logs.append(f"❌ Migrations failed: {stderr or stdout}")
        return False
    
    logs.append("✅ Migrations completed")
    
    # Run seed command if specified
    seed_command = manifest.database.seedCommand if manifest.database else None
    if seed_command:
        logs.append("🌱 Running database seed...")
        seed_cmd = f"""
cd {app_path} && \
export DATABASE_URL="{database_url}" && \
{seed_command} 2>&1
"""
        stdout, stderr, code = await execute_in_container(seed_cmd, timeout=300)
        
        if code != 0:
            logs.append(f"⚠️ Seed command failed (non-fatal): {stderr or stdout}")
            # Don't fail deployment for seed errors - database might already have data
        else:
            logs.append("✅ Database seeded")
    
    return True


async def create_systemd_service(
    manifest: BusiboxManifest,
    app_path: str,
    env_vars: dict,
    logs: List[str],
    dev_mode: bool = False
) -> bool:
    """Create systemd service file for the app"""
    logs.append("🔧 Creating systemd service...")
    
    app_id = manifest.id
    app_name = manifest.name
    port = manifest.defaultPort
    
    # Use dev command for dev mode, otherwise use the manifest's start command
    if dev_mode:
        start_command = "npm run dev"
        logs.append("📝 Using dev server: npm run dev")
    else:
        start_command = manifest.startCommand
    
    # Build environment section
    env_lines = [f'Environment="{k}={v}"' for k, v in env_vars.items()]
    env_lines.append(f'Environment="PORT={port}"')
    env_section = "\n".join(env_lines)
    
    service_content = f"""[Unit]
Description={app_name} Application
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory={app_path}

# Environment variables
{env_section}

# Start command
ExecStart=/bin/bash -c '{start_command}'

# Restart configuration
Restart=always
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=3

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier={app_id}

[Install]
WantedBy=multi-user.target
"""
    
    # Write service file
    service_path = f"/etc/systemd/system/{app_id}.service"
    
    # Escape for shell
    escaped_content = service_content.replace("'", "'\\''")
    
    command = f"""
cat > {service_path} << 'SERVICEEOF'
{service_content}
SERVICEEOF
chmod 644 {service_path}
"""
    
    stdout, stderr, code = await execute_in_container(command)
    
    if code != 0:
        logs.append(f"❌ Failed to create service file: {stderr}")
        return False
    
    logs.append(f"✅ Systemd service created at {service_path}")
    return True


async def start_app(app_id: str, logs: List[str], app_path: str = None, start_command: str = None, env_vars: dict = None) -> bool:
    """Start/restart app via systemd (LXC) or nohup (Docker)"""
    logs.append("🚀 Starting application...")
    
    if is_docker_environment():
        # Docker: Use nohup to run in background
        if not app_path or not start_command:
            logs.append("❌ app_path and start_command required for Docker deployment")
            return False
        
        # Get the port from env_vars for process detection
        port = env_vars.get("PORT", "3000") if env_vars else "3000"
        
        # Stop any existing process for THIS app only (by PID file or port)
        # IMPORTANT: Don't use pkill -f 'next.*dev' as it kills ALL next processes including other apps
        pid_file = f"/tmp/{app_id}.pid"
        stop_command = f"""
if [ -f {pid_file} ]; then
    OLD_PID=$(cat {pid_file})
    kill -9 $OLD_PID 2>/dev/null || true
    rm -f {pid_file}
fi
# Also try to kill any process on this specific port
if command -v lsof &>/dev/null; then
    lsof -ti:{port} | xargs kill -9 2>/dev/null || true
elif command -v fuser &>/dev/null; then
    fuser -k {port}/tcp 2>/dev/null || true
fi
"""
        await execute_in_container(stop_command)
        
        # Give a moment for port to be released
        await asyncio.sleep(1)
        
        # Build environment variables for the subshell
        # We need to pass these to the bash -c subshell, so build them as inline env assignments
        env_inline = " ".join([f'{k}="{v}"' for k, v in (env_vars or {}).items()])
        
        log_file = f"/tmp/{app_id}.log"
        pid_file = f"/tmp/{app_id}.pid"
        
        # Start new process in background with nohup
        # Redirect stdout/stderr to log file with app_id prefix for identification
        # The container's main process (tail -F /tmp/*.log) will stream these to docker logs
        # Using a simple pipe with sed to prefix each line with the app_id
        # IMPORTANT: Use env to pass environment variables into the bash -c subshell
        command = f"""
cd {app_path}
rm -f {log_file} {pid_file}
touch {log_file}
echo "[{app_id}] Starting: {start_command}" >> {log_file}
echo "[{app_id}] Environment: NEXT_PUBLIC_BASE_PATH={env_vars.get('NEXT_PUBLIC_BASE_PATH', 'not set') if env_vars else 'none'}" >> {log_file}
nohup env {env_inline} bash -c '{start_command} 2>&1 | while IFS= read -r line; do echo "[{app_id}] $line"; done >> {log_file}' &
APP_PID=$!
echo $APP_PID > {pid_file}
sleep 2
echo $APP_PID
"""
        
        stdout, stderr, code = await execute_in_container(command, timeout=20)
        
        if code != 0:
            logs.append(f"❌ Failed to start app: {stderr}")
            return False
        
        # Extract just the PID from the last line of output
        pid = stdout.strip().split('\n')[-1].strip()
        logs.append(f"📝 Started process with PID: {pid}")

        # If we didn't get a sane PID, fail early with context
        if not pid.isdigit():
            logs.append("❌ Failed to start app: invalid PID returned from start command")
            if stdout.strip():
                logs.append("📋 Start command output:")
                for line in stdout.strip().split('\n')[-20:]:
                    logs.append(f"   {line}")
            if stderr.strip():
                logs.append("📋 Start command stderr:")
                for line in stderr.strip().split('\n')[-20:]:
                    logs.append(f"   {line}")
            return False
        
        # Register this app so it can be restarted after container recreation
        register_running_app(app_id, app_path, start_command, env_vars or {})
        
        # Give it a moment to start and check if still running
        await asyncio.sleep(3)
        
        # Check if process is running
        # Use multiple methods since lsof may not be available in slim containers
        # First try: check if PID exists in /proc (works on Linux)
        # Second try: use ps command
        # Third try: check if port is listening using netstat or ss
        check_cmd = f"""
if [ -d /proc/{pid} ]; then
    echo "running"
elif ps -p {pid} -o pid= 2>/dev/null | grep -q .; then
    echo "running"
elif ss -tln 2>/dev/null | grep -q ":{port} "; then
    echo "running"
elif netstat -tln 2>/dev/null | grep -q ":{port} "; then
    echo "running"
else
    echo ""
fi
"""
        stdout, _, check_code = await execute_in_container(check_cmd)
        
        if stdout.strip() == "running":
            logs.append(f"✅ Application started (PID: {pid}, log: {log_file})")
            
            # Show first few lines of log for debugging
            log_cmd = f"head -20 {log_file} 2>/dev/null || echo 'No log output yet'"
            log_stdout, _, _ = await execute_in_container(log_cmd)
            if log_stdout.strip() and log_stdout.strip() != 'No log output yet':
                logs.append(f"📋 Initial log output:")
                for line in log_stdout.strip().split('\n')[:10]:
                    logs.append(f"   {line}")
            
            return True
        else:
            logs.append(f"❌ Application failed to start (PID: {pid}, port: {port})")

            # Lightweight diagnostics that work in slim containers (no lsof required)
            diag_cmd = f"""
echo "[diag] /proc/{pid}: $([ -d /proc/{pid} ] && echo yes || echo no)"
ps -p {pid} -o pid=,cmd= 2>/dev/null || true
# Test whether port is accepting TCP connections (bash /dev/tcp)
bash -c 'echo > /dev/tcp/127.0.0.1/{port}' >/dev/null 2>&1 && echo "[diag] port {port}: open" || echo "[diag] port {port}: closed"
"""
            diag_out, _, _ = await execute_in_container(diag_cmd)
            if diag_out.strip():
                logs.append("📋 Diagnostics:")
                for line in diag_out.strip().split('\n')[-20:]:
                    logs.append(f"   {line}")

            # Show log output to help diagnose
            log_cmd = f"cat {log_file} 2>/dev/null | tail -30"
            log_stdout, _, _ = await execute_in_container(log_cmd)
            if log_stdout.strip():
                logs.append(f"📋 Log output:")
                for line in log_stdout.strip().split('\n'):
                    logs.append(f"   {line}")
            else:
                logs.append("📋 Log output: (empty)")
            return False
    else:
        # LXC: Use systemd
        command = f"""
systemctl daemon-reload && \
systemctl enable {app_id}.service && \
systemctl restart {app_id}.service
"""
        
        stdout, stderr, code = await execute_in_container(command)
        
        if code != 0:
            logs.append(f"❌ Failed to start service: {stderr}")
            return False
        
        logs.append(f"✅ Application started (systemctl status {app_id})")
        return True


async def stop_app(app_id: str, logs: List[str], port: int = None) -> bool:
    """Stop app via systemd (LXC) or kill process (Docker)
    
    Args:
        app_id: Application ID
        logs: List to append log messages
        port: Optional port to kill any process listening on (ensures no port conflicts)
    """
    logs.append("🛑 Stopping application...")
    
    if is_docker_environment():
        # Docker: Kill process by app_id, PID file, and optionally by port
        pid_file = f"/tmp/{app_id}.pid"
        
        # Build kill command - handle case where lsof/fuser might not be available
        port_kill_cmd = ""
        if port:
            port_kill_cmd = f"""
# Kill any process on port {port} using various methods
# Try ss + awk (most reliable on minimal containers)
PID_ON_PORT=$(ss -tlnp 2>/dev/null | grep ':{port}' | grep -oP 'pid=\\K\\d+' | head -1)
if [ -n "$PID_ON_PORT" ]; then
    echo "Killing process $PID_ON_PORT on port {port}"
    kill -9 $PID_ON_PORT 2>/dev/null || true
fi
# Fallback: try fuser if available
if command -v fuser &>/dev/null; then
    fuser -k {port}/tcp 2>/dev/null || true
fi
# Fallback: try lsof if available
if command -v lsof &>/dev/null; then
    lsof -ti:{port} | xargs kill -9 2>/dev/null || true
fi
"""
        
        command = f"""
if [ -f {pid_file} ]; then
    PID=$(cat {pid_file})
    echo "Killing PID $PID from pidfile"
    kill -9 $PID 2>/dev/null || true
    rm -f {pid_file}
fi
# Also kill any process with app_id in command line
pkill -9 -f '{app_id}' 2>/dev/null || true
{port_kill_cmd}
# Give a moment for port to be released
sleep 1
"""
        stdout, stderr, code = await execute_in_container(command)
        if stdout:
            logs.append(stdout.strip())
    else:
        # LXC: Use systemd
        command = f"systemctl stop {app_id}.service 2>/dev/null || true"
        await execute_in_container(command)
        
        # Also kill by port if specified
        if port:
            port_cmd = f"fuser -k {port}/tcp 2>/dev/null || true"
            await execute_in_container(port_cmd)
    
    # Unregister app from running apps registry
    unregister_running_app(app_id)
    
    logs.append("✅ Application stopped")
    return True


async def cleanup_app_volumes(app_id: str, logs: List[str]) -> bool:
    """
    Remove Docker volumes associated with an app.
    Call this when completely removing an app (not just stopping).
    
    Note: This requires the container to be recreated without these volumes,
    which happens automatically on next deploy of any remaining dev apps.
    """
    if not is_docker_environment():
        return True  # No volumes to clean up in LXC mode
    
    volume_names = get_app_volume_names(app_id)
    
    for volume_type, volume_name in volume_names.items():
        logs.append(f"🗑️ Removing volume: {volume_name}")
        
        rm_cmd = ['docker', 'volume', 'rm', '-f', volume_name]
        proc = await asyncio.create_subprocess_exec(
            *rm_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            # Volume might be in use - just log and continue
            logger.warning(f"Failed to remove volume {volume_name}: {stderr.decode()}")
    
    logs.append(f"✅ Volumes cleaned up for {app_id}")
    return True


async def undeploy_app(app_id: str, logs: List[str], remove_volumes: bool = True) -> bool:
    """
    Fully undeploy an app, cleaning up all resources.
    
    This removes:
    1. Running process (via stop_app)
    2. Docker volumes (node_modules and .next cache)
    3. nginx configuration (if in Docker mode)
    4. App source directory (only for non-local-dev apps)
    
    Args:
        app_id: Application ID
        logs: List to append log messages
        remove_volumes: Whether to remove Docker volumes (default True)
    
    Returns:
        True if undeploy succeeded, False otherwise
    """
    logs.append(f"🗑️ Starting undeploy for {app_id}...")
    
    try:
        # Step 1: Stop the application
        logs.append("Step 1: Stopping application...")
        await stop_app(app_id, logs)
        
        # Step 2: Clean up Docker volumes (if applicable)
        if remove_volumes:
            logs.append("Step 2: Cleaning up Docker volumes...")
            await cleanup_app_volumes(app_id, logs)
        
        # Step 3: Remove nginx config (Docker mode - config is on host)
        if is_docker_environment():
            logs.append("Step 3: Removing nginx configuration...")
            
            # Import here to avoid circular dependency
            from .config import config
            
            nginx_config_path = f"{config.busibox_host_path}/config/nginx-sites/apps/{app_id}.conf"
            
            # Remove using host filesystem (we're in deploy container with access)
            import os
            if os.path.exists(nginx_config_path):
                try:
                    os.remove(nginx_config_path)
                    logs.append(f"✅ Removed nginx config: {nginx_config_path}")
                except Exception as e:
                    logs.append(f"⚠️ Failed to remove nginx config: {e}")
            else:
                logs.append(f"ℹ️ No nginx config found at {nginx_config_path}")
        else:
            # LXC mode: nginx config removal handled by NginxConfigurator via SSH
            logs.append("Step 3: Nginx config removal requires NginxConfigurator (LXC mode)")
        
        # Step 4: Clean up .next and node_modules directories in the source
        # This helps resolve the "cannot remove directory" issues
        if is_docker_environment():
            logs.append("Step 4: Cleaning up build artifacts...")
            
            dev_path = f"/srv/dev-apps/{app_id}"
            apps_path = f"/srv/apps/{app_id}"
            
            # Check which path exists and clean up artifacts
            cleanup_script = f"""
# Clean up dev-apps location
if [ -d "{dev_path}" ]; then
    echo "Cleaning artifacts in {dev_path}"
    rm -rf "{dev_path}/.next" 2>/dev/null || true
    rm -rf "{dev_path}/node_modules" 2>/dev/null || true
fi

# Clean up apps location (cloned from GitHub)
if [ -d "{apps_path}" ]; then
    echo "Cleaning artifacts in {apps_path}"
    rm -rf "{apps_path}/.next" 2>/dev/null || true
    rm -rf "{apps_path}/node_modules" 2>/dev/null || true
fi
"""
            stdout, stderr, code = await execute_in_container(cleanup_script)
            if stdout:
                logs.append(stdout)
            if code == 0:
                logs.append("✅ Build artifacts cleaned up")
            else:
                logs.append(f"⚠️ Artifact cleanup had issues: {stderr}")
        
        logs.append(f"✅ Undeploy completed for {app_id}")
        return True
        
    except Exception as e:
        logger.error(f"Undeploy failed for {app_id}: {e}")
        logs.append(f"❌ Undeploy failed: {str(e)}")
        return False


async def check_app_health(app_id: str, port: int, health_endpoint: str, logs: List[str]) -> bool:
    """Check if app is healthy
    
    For dev mode, Next.js takes time to compile on first request.
    We use fewer attempts with shorter waits for faster feedback.
    """
    logs.append(f"🔍 Checking application health at localhost:{port}{health_endpoint}...")
    
    # First, check if the port is listening at all (fast check)
    port_check = f"lsof -ti:{port} 2>/dev/null"
    port_stdout, _, port_code = await execute_in_container(port_check)
    
    if not port_stdout.strip():
        logs.append(f"⏳ Port {port} not yet listening, waiting for app to start...")
        # Show last lines of log to help debug
        log_cmd = f"tail -10 /tmp/{app_id}.log 2>/dev/null || echo 'No log file yet'"
        log_stdout, _, _ = await execute_in_container(log_cmd)
        if log_stdout.strip() and 'No log file yet' not in log_stdout:
            logs.append(f"📋 Recent log output:")
            for line in log_stdout.strip().split('\n')[-5:]:
                logs.append(f"   {line}")
    else:
        logs.append(f"✅ Port {port} is listening (PID: {port_stdout.strip()})")
    
    # Use fewer attempts for faster feedback - Next.js dev startup can be slow
    max_attempts = 15  # 15 attempts x 2 seconds = 30 seconds max
    
    for attempt in range(max_attempts):
        # Use curl with shorter timeout - try both with and without basePath
        # The health endpoint might be at /api/health or /myapp/api/health depending on basePath
        command = f"curl -sf --max-time 5 http://localhost:{port}{health_endpoint} 2>&1"
        stdout, stderr, code = await execute_in_container(command)
        
        if code == 0:
            logs.append(f"✅ Health check passed on attempt {attempt + 1}")
            return True
        
        # Log progress every 5 attempts with more info
        if (attempt + 1) % 5 == 0:
            logs.append(f"⏳ Health check attempt {attempt + 1}/{max_attempts}...")
            # Check if port is still listening
            port_stdout, _, _ = await execute_in_container(port_check)
            if not port_stdout.strip():
                logs.append(f"⚠️ Port {port} stopped listening - app may have crashed")
                # Show recent log
                log_cmd = f"tail -15 /tmp/{app_id}.log 2>/dev/null"
                log_stdout, _, _ = await execute_in_container(log_cmd)
                if log_stdout.strip():
                    logs.append(f"📋 Recent log output:")
                    for line in log_stdout.strip().split('\n')[-10:]:
                        logs.append(f"   {line}")
                break
        
        if attempt < max_attempts - 1:
            await asyncio.sleep(2)
    
    logs.append(f"❌ Health check failed after {max_attempts} attempts (30 seconds)")
    
    # Show diagnostic info
    # Check what's on the port
    port_stdout, _, _ = await execute_in_container(f"lsof -ti:{port} 2>/dev/null")
    if port_stdout.strip():
        logs.append(f"📊 Process on port {port}: PID {port_stdout.strip()}")
        # Try to get response body
        curl_cmd = f"curl -s --max-time 3 http://localhost:{port}{health_endpoint} 2>&1 | head -5"
        curl_stdout, _, _ = await execute_in_container(curl_cmd)
        if curl_stdout.strip():
            logs.append(f"📋 Response: {curl_stdout.strip()[:200]}")
    else:
        logs.append(f"⚠️ No process listening on port {port}")
    
    # Show last lines of log
    log_cmd = f"tail -20 /tmp/{app_id}.log 2>/dev/null"
    log_stdout, _, _ = await execute_in_container(log_cmd)
    if log_stdout.strip():
        logs.append(f"📋 App log (last 20 lines):")
        for line in log_stdout.strip().split('\n'):
            logs.append(f"   {line}")
    
    return False


async def deploy_app(
    manifest: BusiboxManifest,
    deploy_config: DeploymentConfig,
    database_url: Optional[str],
    logs: List[str]
) -> bool:
    """
    Full deployment flow:
    0. Stop any existing instance (prevent port conflicts)
    1. Clone/update repo (or use dev-apps path)
    2. Install dependencies (skip for dev mode)
    3. Build (skip for dev mode)
    4. Run migrations
    5. Create/update systemd service
    6. Start app
    7. Health check
    """
    
    is_dev_mode = deploy_config.devMode
    
    # Step 0: Stop any existing instance to prevent port conflicts
    # This is important for re-deployments
    logs.append(f"🛑 Stopping any existing instance of {manifest.id}...")
    await stop_app(manifest.id, logs, port=manifest.defaultPort)
    
    # Check if Docker environment - log appropriate context
    if is_docker_environment():
        logs.append("📦 Docker/local environment detected")
        
        # Ensure user-apps container is running before we try to deploy
        logs.append(f"🔄 Ensuring {USER_APPS_CONTAINER} container is running...")
        container_ok, container_msg = await ensure_user_apps_container_running()
        if not container_ok:
            logs.append(f"❌ Failed to start user-apps container: {container_msg}")
            return False
        logs.append(f"✅ {USER_APPS_CONTAINER} container ready")
        
        if is_dev_mode:
            logs.append(f"🔧 DEV MODE: {manifest.name} (using local source)")
        else:
            logs.append(f"🎯 Deploying {manifest.name} to user-apps container")
    else:
        logs.append(f"🎯 Deploying {manifest.name} to {deploy_config.environment}")
    
    # Step 1: Clone/update repo (or get dev-apps path)
    success, app_path = await clone_or_update_repo(manifest, deploy_config, logs)
    if not success:
        return False

    # Portal URL for auth redirects (used both at build-time and runtime)
    portal_url = os.environ.get("NEXT_PUBLIC_AI_PORTAL_URL", "")
    if not portal_url and is_docker_environment():
        # Docker dev default - goes through nginx at /portal
        portal_url = "https://localhost/portal"
    
    # Step 1.5: For Docker dev mode, set up dynamic volumes for node_modules and .next
    # This solves the platform mismatch (macOS host vs Linux container) by keeping
    # node_modules in a Docker volume with Linux-native binaries
    if is_dev_mode and is_docker_environment():
        # Use the actual directory name for volumes (localDevDir), not the app ID
        # The app path is /srv/dev-apps/{localDevDir}, so extract the dir name
        dev_app_dir = app_path.split('/')[-1]  # e.g., "app-template" from "/srv/dev-apps/app-template"
        
        logs.append(f"📦 Setting up app volumes for {dev_app_dir} (node_modules and .next cache)...")
        if not await ensure_app_volumes_mounted(dev_app_dir, logs):
            logs.append("❌ Failed to set up app volumes")
            return False
        
        # Clear the entire .next cache to prevent Turbopack corruption errors
        # Turbopack uses SST files that can become corrupted and cause panics
        logs.append("🧹 Clearing .next cache (prevents Turbopack corruption)...")
        logger.info(f"Clearing .next cache for {app_path}")
        clear_cache_cmd = f"rm -rf {app_path}/.next/* 2>/dev/null || true"
        await execute_in_container(clear_cache_cmd)
        logger.info("Cache cleared successfully")
    
    # Step 2: Install dependencies
    # In Docker dev mode, we MUST run npm install because the host's node_modules
    # may have native binaries (e.g., lightningcss) built for a different platform.
    # The Docker container is Linux, but the host may be macOS/Windows.
    # With dynamic volumes, npm install writes to the Docker volume, not the host.
    # Pass GitHub token for npm authentication with GitHub Package Registry
    github_token = deploy_config.githubToken if deploy_config else None
    if is_dev_mode and is_docker_environment():
        logs.append("📦 Installing dependencies to Docker volume (Linux-native binaries)...")
        if not await install_dependencies(app_path, logs, github_token=github_token):
            return False
    elif is_dev_mode:
        logs.append("⏭️ Skipping npm install (dev mode - use local node_modules)")
    else:
        if not await install_dependencies(app_path, logs, github_token=github_token):
            return False
    
    # Step 3: Build (skip for dev mode - use local dev server)
    if is_dev_mode:
        logs.append("⏭️ Skipping build (dev mode - will run dev server)")
    else:
        build_env = {
            # Ensure basePath/assetPrefix match the proxy path at build time
            "NEXT_PUBLIC_BASE_PATH": manifest.defaultPath,
            # Ensure auth redirect targets are consistent
            "NEXT_PUBLIC_AI_PORTAL_URL": portal_url,
            # Ensure audience claim validation stays consistent
            "APP_NAME": manifest.name,
        }
        if not await run_build(app_path, manifest.buildCommand, logs, env_vars=build_env):
            return False
    
    # Step 4: Migrations
    if not await run_migrations(app_path, manifest, database_url, logs):
        return False
    
    # Build environment variables
    env_vars = {
        "NODE_ENV": "production" if deploy_config.environment == "production" else "development",
        "PORT": str(manifest.defaultPort),
        # CRITICAL: Next.js needs NEXT_PUBLIC_BASE_PATH to serve assets correctly
        # when the app is accessed via nginx reverse proxy at a subpath
        "NEXT_PUBLIC_BASE_PATH": manifest.defaultPath,
        # Portal URL for auth redirects
        "NEXT_PUBLIC_AI_PORTAL_URL": portal_url,
        # APP_NAME must match the audience in SSO tokens issued by AI Portal
        # AI Portal uses app.name as the audience when requesting tokens from authz
        "APP_NAME": manifest.name,
    }
    if database_url:
        env_vars["DATABASE_URL"] = database_url
    
    # Add any additional secrets
    env_vars.update(deploy_config.secrets)
    
    # Determine start command
    if is_dev_mode:
        start_command = "npm run dev"
    else:
        start_command = manifest.startCommand
    
    # Step 5: Create systemd service (only for LXC, not Docker)
    if not is_docker_environment():
        if not await create_systemd_service(manifest, app_path, env_vars, logs, dev_mode=is_dev_mode):
            return False
    else:
        logs.append("⏭️ Skipping systemd service (Docker - will use nohup)")
    
    # Step 6: Start app
    if not await start_app(manifest.id, logs, app_path=app_path, start_command=start_command, env_vars=env_vars):
        return False
    
    # Step 7: Health check
    # IMPORTANT: For internal/direct requests to the Next.js dev server, we do NOT include basePath.
    # basePath is only used by nginx to route external requests to the correct app.
    # Direct requests to the container (curl http://localhost:PORT/api/health) bypass nginx.
    health_endpoint = manifest.healthEndpoint  # e.g., "/api/health" - no basePath prefix
    
    if not await check_app_health(manifest.id, manifest.defaultPort, health_endpoint, logs):
        logs.append("⚠️ App started but health check failed - check logs")
        # Don't fail deployment for health check - app might just be slow to start
    
    logs.append(f"🎉 Deployment completed! App available at {manifest.defaultPath}")
    return True
