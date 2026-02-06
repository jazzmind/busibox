"""
Core App Executor - Core Application Deployment
================================================

This module handles deployment of CORE APPS (ai-portal, agent-manager, etc.)
using the runtime installation pattern (apps deployed at runtime, not baked in).

This replaces the bridge_executor.py which used Makefile/Ansible to rebuild
Docker images. Instead, we execute commands inside the running core-apps container.

Security Model:
- Core apps run in the core-apps container (trusted)
- Uses supervisord for process management
- Apps persist in Docker volumes across container restarts
- In LXC/Proxmox, uses SSH to execute commands in apps-lxc

Execution Methods:
- Docker: Uses `docker exec` to run commands in core-apps container
- LXC: Uses SSH to run commands in apps-lxc container

Architecture matches Proxmox pattern:
- Lightweight container with nginx + supervisord
- Apps cloned and built at runtime
- No container rebuild needed for app updates
"""

import asyncio
import logging
import os
import shlex
from typing import Tuple, List, Optional, Dict

from .config import config

logger = logging.getLogger(__name__)

# Container names
CONTAINER_PREFIX = os.environ.get("CONTAINER_PREFIX", "local")
CORE_APPS_CONTAINER = f"{CONTAINER_PREFIX}-core-apps"

# Core app definitions
CORE_APPS = {
    "ai-portal": {
        "github_repo": "jazzmind/ai-portal",
        "default_port": 3000,
        "base_path": "/portal",
        "health_endpoint": "/api/health",
    },
    "agent-manager": {
        "github_repo": "jazzmind/agent-manager",
        "default_port": 3001,
        "base_path": "/agents",
        "health_endpoint": "/api/health",
    },
}


def is_core_app(app_id: str) -> bool:
    """
    Determine if an app is a core app.
    
    Core apps are deployed via this executor (docker exec / SSH).
    Non-core apps are deployed via container_executor.py (user-apps container).
    """
    # Frontend core apps that run in core-apps container
    FRONTEND_CORE_APPS = {"ai-portal", "agent-manager"}
    
    # Backend core apps (deployed differently, not via this executor)
    BACKEND_CORE_APPS = {
        "authz-api", "data-api", "search-api", "agent-api",
        "docs-api", "deploy-api", "embedding-api", "litellm"
    }
    
    return app_id.lower() in FRONTEND_CORE_APPS


def is_docker_environment() -> bool:
    """Check if running in Docker (local development).
    
    Uses config.is_docker_backend() which checks:
    1. DEPLOYMENT_BACKEND environment variable (explicit setting)
    2. Auto-detection based on POSTGRES_HOST and other indicators
    
    Returns:
        True if Docker backend, False if Proxmox/LXC
    """
    return config.is_docker_backend()


async def execute_docker_command(command: str, timeout: int = 600) -> Tuple[str, str, int]:
    """Execute command in core-apps container via docker exec."""
    docker_command = [
        'docker', 'exec', CORE_APPS_CONTAINER,
        '/bin/bash', '-c', command
    ]
    
    # Log short commands, truncate long ones
    cmd_preview = command[:100] + "..." if len(command) > 100 else command
    logger.debug(f"Executing in core-apps: {cmd_preview}")
    
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
        
        logger.debug(f"Command completed with code {returncode}")
        return stdout_str, stderr_str, returncode
    except asyncio.TimeoutError:
        logger.error(f"Command timed out after {timeout}s: {cmd_preview}")
        proc.kill()
        return "", f"Command timed out after {timeout}s", 1


async def execute_ssh_command(host: str, command: str, timeout: int = 600) -> Tuple[str, str, int]:
    """Execute command on apps-lxc via SSH (for Proxmox)."""
    ssh_command = [
        'ssh',
        '-F', '/dev/null',
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


async def execute_in_core_apps(
    command: str,
    host: Optional[str] = None,
    environment: str = 'production',
    timeout: int = 600
) -> Tuple[str, str, int]:
    """Execute command in core-apps container/LXC - routes to docker exec or SSH."""
    if is_docker_environment():
        return await execute_docker_command(command, timeout)
    else:
        # Proxmox LXC - use SSH
        target_host = host or config.apps_container_ip
        return await execute_ssh_command(target_host, command, timeout)


async def deploy_core_app(
    app_id: str,
    github_ref: str = "main",
    logs: Optional[List[str]] = None,
    environment: str = "docker",
    github_token: Optional[str] = None,
    env_vars: Optional[Dict[str, str]] = None
) -> Tuple[bool, str]:
    """
    Deploy a core app by executing inside the running core-apps container.
    
    This calls the entrypoint script's deploy command, which:
    1. Stops the app via supervisorctl
    2. Clones the repo from GitHub
    3. Installs dependencies (npm ci)
    4. Builds the app (npm run build)
    5. Starts the app via supervisorctl
    
    Args:
        app_id: App identifier (e.g., "ai-portal", "agent-manager")
        github_ref: Git branch or tag to deploy
        logs: List to append log messages
        environment: Target environment (docker, staging, production)
        env_vars: Environment variables to set for build and runtime
        
    Returns:
        Tuple of (success, message)
    """
    if logs is None:
        logs = []
    
    if app_id not in CORE_APPS:
        logs.append(f"❌ Unknown core app: {app_id}")
        return False, f"Unknown core app: {app_id}"
    
    logs.append(f"🚀 Deploying {app_id} (ref: {github_ref})...")
    
    # Determine deployment command based on environment
    if is_docker_environment():
        # Docker: Use entrypoint script
        command = f"/usr/local/bin/entrypoint.sh deploy {app_id} {github_ref}"
    else:
        # Proxmox: Execute deployment steps directly
        app_info = CORE_APPS[app_id]
        repo = app_info['github_repo']
        
        # Use authenticated URL if token provided (for private repos)
        if github_token:
            logs.append(f"🔐 Using GitHub token for authenticated clone")
            repo_url = f"https://{github_token}@github.com/{repo}.git"
        else:
            logs.append(f"⚠️ No GitHub token - attempting public clone")
            repo_url = f"https://github.com/{repo}.git"
        
        # Build npmrc setup commands only if we have a token
        # CRITICAL: Use /root/.npmrc explicitly (not ~) since SSH may not expand ~ correctly
        npmrc_commands = ""
        if github_token:
            # Mask token in logs (show first 4 and last 4 chars)
            token_preview = f"{github_token[:4]}...{github_token[-4:]}" if len(github_token) > 8 else "***"
            logs.append(f"📝 Setting up .npmrc with token: {token_preview}")
            
            npmrc_commands = f"""
            # Configure npm for GitHub Package Registry (for @jazzmind packages)
            echo "=== NPMRC SETUP START ==="
            echo "Configuring npm for GitHub Package Registry..."
            echo "Home directory: $HOME"
            echo "Current user: $(whoami)"
            
            # Create .npmrc in /root explicitly (SSH runs as root on LXC)
            cat > /root/.npmrc << 'NPMRC_EOF'
//npm.pkg.github.com/:_authToken={github_token}
@jazzmind:registry=https://npm.pkg.github.com
NPMRC_EOF
            
            echo "npmrc created at /root/.npmrc"
            echo "npmrc contents (masked):"
            head -1 /root/.npmrc | sed 's/=.*/=***MASKED***/'
            tail -1 /root/.npmrc
            ls -la /root/.npmrc
            echo "=== NPMRC SETUP END ==="
            """
        else:
            npmrc_commands = """
            echo "=== WARNING: No GitHub token provided - private packages will fail! ==="
            """
        
        # Build environment variable export commands
        # These are needed for build-time (prisma generate) and runtime
        env_exports = ""
        if env_vars:
            logs.append(f"📦 Setting {len(env_vars)} environment variables for build")
            env_export_lines = []
            for key, value in env_vars.items():
                # Escape the value for shell (handle special chars, quotes, etc.)
                escaped_value = shlex.quote(value) if value else "''"
                env_export_lines.append(f"export {key}={escaped_value}")
            env_exports = "\n            ".join(env_export_lines)
            env_exports = f"""
            # === ENVIRONMENT VARIABLES (from Ansible) ===
            echo "Setting {len(env_vars)} environment variables..."
            {env_exports}
            echo "Environment variables set: {', '.join(sorted(env_vars.keys()))}"
            """
        
        command = f"""
            set -e
            
            echo "=== DEPLOY SCRIPT START ==="
            echo "App: {app_id}"
            echo "Environment: {environment}"
            echo "GitHub ref: {github_ref}"
            echo "Token provided: {'yes' if github_token else 'no'}"
            {env_exports}
            # Ensure /srv/apps directory exists
            mkdir -p /srv/apps
            
            APP_DIR="/srv/apps/{app_id}"
            {npmrc_commands}
            # Clone or update repository
            if [ -d "$APP_DIR/.git" ]; then
                echo "Updating existing repository..."
                cd "$APP_DIR"
                git fetch origin
                git checkout {github_ref}
                # Use reset --hard to discard local changes (e.g., package-lock.json from npm install)
                git reset --hard origin/{github_ref}
            else
                echo "Cloning repository from GitHub..."
                rm -rf "$APP_DIR"  # Clean up any partial clone
                git clone {repo_url} "$APP_DIR"
                cd "$APP_DIR"
                git checkout {github_ref}
            fi
            
            cd "$APP_DIR"
            
            # Install dependencies
            echo "=== NPM INSTALL START ==="
            echo "Current directory: $(pwd)"
            
            # The project .npmrc uses $GITHUB_AUTH_TOKEN env var for authentication
            # We need to export this env var, not modify the .npmrc file
            export GITHUB_AUTH_TOKEN='{github_token}'
            echo "GITHUB_AUTH_TOKEN env var set (length: $(echo -n "$GITHUB_AUTH_TOKEN" | wc -c))"
            
            echo "Project .npmrc contents:"
            cat .npmrc 2>/dev/null || echo "  (no .npmrc found)"
            
            echo "npm config list:"
            npm config list 2>&1 | head -20 || echo "  (failed to get config)"
            echo ""
            echo "Running: npm install"
            npm install
            echo "=== NPM INSTALL END ==="
            
            # Regenerate Prisma client if prisma is present (ensures fresh client after schema changes)
            if [ -f "prisma/schema.prisma" ] || [ -f "prisma/schema" ]; then
                echo "=== PRISMA GENERATE START ==="
                # Clean stale Prisma client to force regeneration
                rm -rf node_modules/.prisma 2>/dev/null || true
                npx prisma generate
                echo "=== PRISMA GENERATE END ==="
            fi
            
            # Build application
            # Always build in production mode regardless of NODE_ENV setting
            echo "=== NPM BUILD START ==="
            NODE_ENV=production npm run build
            echo "=== NPM BUILD END ==="
            
            # Restart with systemd (on Proxmox, apps run as systemd services)
            echo "Restarting service..."
            systemctl restart {app_id} || echo "Service restart skipped (may not exist yet)"
        """
    
    stdout, stderr, code = await execute_in_core_apps(
        command,
        environment=environment,
        timeout=900  # 15 minutes for clone + install + build
    )
    
    # ALWAYS append stdout to logs (even on failure) so we can see debug output
    if stdout:
        logs.append("=== STDOUT BEGIN ===")
        for line in stdout.strip().split('\n'):
            if line.strip():
                logs.append(line)
        logs.append("=== STDOUT END ===")
    
    # Also append stderr if present
    if stderr:
        logs.append("=== STDERR BEGIN ===")
        for line in stderr.strip().split('\n'):
            if line.strip():
                logs.append(line)
        logs.append("=== STDERR END ===")
    
    if code != 0:
        logs.append(f"❌ Deployment failed with exit code {code}")
        # Combine stdout and stderr for the error message
        combined_output = stderr or stdout
        return False, f"Deployment failed: {combined_output}"
    
    logs.append(f"✅ {app_id} deployed successfully")
    return True, f"{app_id} deployed successfully"


async def stop_core_app(
    app_id: str,
    logs: Optional[List[str]] = None,
    environment: str = "docker"
) -> Tuple[bool, str]:
    """Stop a core app via supervisorctl."""
    if logs is None:
        logs = []
    
    logs.append(f"🛑 Stopping {app_id}...")
    
    if is_docker_environment():
        command = f"supervisorctl stop {app_id}"
    else:
        # Proxmox uses systemd
        command = f"systemctl stop {app_id}.service"
    
    stdout, stderr, code = await execute_in_core_apps(
        command,
        environment=environment,
        timeout=30
    )
    
    if code != 0 and "no such process" not in stderr.lower():
        logs.append(f"⚠️ Stop may have failed: {stderr}")
    else:
        logs.append(f"✅ {app_id} stopped")
    
    return True, f"{app_id} stopped"


async def start_core_app(
    app_id: str,
    logs: Optional[List[str]] = None,
    environment: str = "docker"
) -> Tuple[bool, str]:
    """Start a core app via supervisorctl."""
    if logs is None:
        logs = []
    
    logs.append(f"🚀 Starting {app_id}...")
    
    if is_docker_environment():
        command = f"supervisorctl start {app_id}"
    else:
        # Proxmox uses systemd
        command = f"systemctl start {app_id}.service"
    
    stdout, stderr, code = await execute_in_core_apps(
        command,
        environment=environment,
        timeout=30
    )
    
    if code != 0:
        logs.append(f"❌ Failed to start: {stderr}")
        return False, f"Failed to start {app_id}: {stderr}"
    
    logs.append(f"✅ {app_id} started")
    return True, f"{app_id} started"


async def restart_core_app(
    app_id: str,
    logs: Optional[List[str]] = None,
    environment: str = "docker"
) -> Tuple[bool, str]:
    """Restart a core app via supervisorctl."""
    if logs is None:
        logs = []
    
    logs.append(f"🔄 Restarting {app_id}...")
    
    if is_docker_environment():
        command = f"supervisorctl restart {app_id}"
    else:
        # Proxmox uses systemd
        command = f"systemctl restart {app_id}.service"
    
    stdout, stderr, code = await execute_in_core_apps(
        command,
        environment=environment,
        timeout=60
    )
    
    if code != 0:
        logs.append(f"❌ Failed to restart: {stderr}")
        return False, f"Failed to restart {app_id}: {stderr}"
    
    logs.append(f"✅ {app_id} restarted")
    return True, f"{app_id} restarted"


async def get_core_app_status(
    app_id: str,
    environment: str = "docker"
) -> dict:
    """Get status of a core app."""
    if is_docker_environment():
        command = f"supervisorctl status {app_id}"
    else:
        command = f"systemctl is-active {app_id}.service"
    
    stdout, stderr, code = await execute_in_core_apps(
        command,
        environment=environment,
        timeout=10
    )
    
    if is_docker_environment():
        # Parse supervisorctl output: "ai-portal   RUNNING   pid 1234, uptime 0:01:23"
        parts = stdout.strip().split()
        if len(parts) >= 2:
            return {
                "app_id": app_id,
                "status": parts[1].lower(),
                "details": stdout.strip()
            }
    else:
        return {
            "app_id": app_id,
            "status": stdout.strip(),
            "details": stdout.strip()
        }
    
    return {
        "app_id": app_id,
        "status": "unknown",
        "details": stderr or stdout
    }


async def reload_nginx(
    logs: Optional[List[str]] = None,
    environment: str = "docker"
) -> Tuple[bool, str]:
    """Reload nginx configuration without restart."""
    if logs is None:
        logs = []
    
    logs.append("🔄 Reloading nginx...")
    
    # Test config first
    test_command = "nginx -t"
    stdout, stderr, code = await execute_in_core_apps(
        test_command,
        environment=environment,
        timeout=10
    )
    
    if code != 0:
        logs.append(f"❌ Nginx config test failed: {stderr}")
        return False, f"Nginx config test failed: {stderr}"
    
    # Reload nginx
    reload_command = "nginx -s reload"
    stdout, stderr, code = await execute_in_core_apps(
        reload_command,
        environment=environment,
        timeout=10
    )
    
    if code != 0:
        logs.append(f"❌ Nginx reload failed: {stderr}")
        return False, f"Nginx reload failed: {stderr}"
    
    logs.append("✅ Nginx reloaded")
    return True, "Nginx reloaded successfully"


async def check_core_app_health(
    app_id: str,
    logs: Optional[List[str]] = None,
    environment: str = "docker"
) -> bool:
    """Check if a core app is healthy by hitting its health endpoint."""
    if logs is None:
        logs = []
    
    app_config = CORE_APPS.get(app_id)
    if not app_config:
        return False
    
    port = app_config["default_port"]
    health_endpoint = app_config["health_endpoint"]
    
    logs.append(f"🔍 Checking health at localhost:{port}{health_endpoint}...")
    
    command = f"curl -sf --max-time 5 http://localhost:{port}{health_endpoint}"
    
    max_attempts = 10
    for attempt in range(max_attempts):
        stdout, stderr, code = await execute_in_core_apps(
            command,
            environment=environment,
            timeout=10
        )
        
        if code == 0:
            logs.append(f"✅ Health check passed (attempt {attempt + 1})")
            return True
        
        if attempt < max_attempts - 1:
            await asyncio.sleep(3)
    
    logs.append(f"❌ Health check failed after {max_attempts} attempts")
    return False


async def undeploy_core_app(
    app_id: str,
    logs: Optional[List[str]] = None,
    environment: str = "docker"
) -> Tuple[bool, str]:
    """
    Stop a core app. Does not remove the app code (use for temporary stops).
    
    For full removal, the persistent volume would need to be cleared manually.
    """
    if logs is None:
        logs = []
    
    logs.append(f"🗑️ Undeploying {app_id}...")
    
    # Stop the app
    success, msg = await stop_core_app(app_id, logs, environment)
    
    if success:
        logs.append(f"✅ {app_id} stopped (code remains in volume)")
    
    return success, msg
