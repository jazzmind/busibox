"""
Core App Executor - Core Application Deployment
================================================

This module handles deployment of CORE APPS (busibox-portal, busibox-agents, etc.)
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
- Lightweight core-apps container with supervisord
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
CONTAINER_PREFIX = os.environ.get("CONTAINER_PREFIX", "").strip()
if not CONTAINER_PREFIX:
    raise RuntimeError("CONTAINER_PREFIX must be set for deploy-api")
CORE_APPS_CONTAINER = f"{CONTAINER_PREFIX}-core-apps"
PROXY_CONTAINER = f"{CONTAINER_PREFIX}-proxy"

# Docs content directory where docs-api reads from
# In Docker: /app/docs (inside docs-api container)
# In Proxmox: /srv/docs/docs (on docs-lxc or apps-lxc)
DOCS_CONTENT_DIR = os.environ.get("DOCS_CONTENT_DIR", "/srv/docs/docs")
DOCS_API_CONTAINER = f"{CONTAINER_PREFIX}-docs-api"

# All core apps live in the jazzmind/busibox-frontend monorepo.
MONOREPO = "jazzmind/busibox-frontend"

CORE_APPS = {
    "busibox-portal": {
        "github_repo": MONOREPO,
        "monorepo_app_dir": "apps/portal",
        "default_port": 3000,
        "base_path": "/portal",
        "health_endpoint": "/api/health",
    },
    "busibox-admin": {
        "github_repo": MONOREPO,
        "monorepo_app_dir": "apps/admin",
        "default_port": 3002,
        "base_path": "/admin",
        "health_endpoint": "/api/health",
    },
    "busibox-agents": {
        "github_repo": MONOREPO,
        "monorepo_app_dir": "apps/agents",
        "default_port": 3001,
        "base_path": "/agents",
        "health_endpoint": "/api/health",
    },
    "busibox-chat": {
        "github_repo": MONOREPO,
        "monorepo_app_dir": "apps/chat",
        "default_port": 3003,
        "base_path": "/chat",
        "health_endpoint": "/api/health",
    },
    "busibox-appbuilder": {
        "github_repo": MONOREPO,
        "monorepo_app_dir": "apps/appbuilder",
        "default_port": 3004,
        "base_path": "/builder",
        "health_endpoint": "/api/health",
    },
    "busibox-media": {
        "github_repo": MONOREPO,
        "monorepo_app_dir": "apps/media",
        "default_port": 3005,
        "base_path": "/media",
        "health_endpoint": "/api/health",
    },
    "busibox-documents": {
        "github_repo": MONOREPO,
        "monorepo_app_dir": "apps/documents",
        "default_port": 3006,
        "base_path": "/documents",
        "health_endpoint": "/api/health",
    },
}

MONOREPO_CLONE_DIR = "/srv/apps/busibox-frontend"


def is_core_app(app_id: str) -> bool:
    """
    Determine if an app is a core app.
    
    Core apps are deployed via this executor (docker exec / SSH)
    from the busibox-frontend monorepo.
    Non-core apps are deployed via container_executor.py (user-apps container).
    """
    return app_id.lower() in CORE_APPS


def is_k8s_environment() -> bool:
    """Check if running on Kubernetes backend."""
    return config.is_k8s_backend()


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


async def execute_docker_proxy_command(command: str, timeout: int = 600) -> Tuple[str, str, int]:
    """Execute command in proxy container via docker exec."""
    docker_command = [
        'docker', 'exec', PROXY_CONTAINER,
        '/bin/sh', '-c', command
    ]

    cmd_preview = command[:100] + "..." if len(command) > 100 else command
    logger.debug(f"Executing in proxy: {cmd_preview}")

    proc = await asyncio.create_subprocess_exec(
        *docker_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(), stderr.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        logger.error(f"Proxy command timed out after {timeout}s: {cmd_preview}")
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


async def sync_app_docs(
    app_id: str,
    logs: Optional[List[str]] = None,
    environment: str = "docker"
) -> bool:
    """
    Sync app documentation to the docs-api content directory.
    
    After an app is deployed, checks if it has a docs/portal/ directory.
    If so, copies those markdown files to the docs content directory under
    apps/<app_id>/ so the docs-api can serve them.
    
    Works for both Docker (docker exec into docs-api container) and
    Proxmox (SSH to apps-lxc where docs content lives).
    
    Args:
        app_id: App identifier (e.g., "busibox-agents")
        logs: List to append log messages
        environment: Target environment
        
    Returns:
        True if docs were synced (or no docs to sync), False on error
    """
    if logs is None:
        logs = []
    
    if is_docker_environment():
        # Docker: app is in core-apps container at /srv/apps/<app_id>
        # docs-api reads from /app/docs which is a volume mount
        # We need to copy docs into the docs-api container's docs volume
        # The shared volume 'app-docs' is mounted at /app/docs/apps in docs-api
        # and accessible from deploy-api at /app/app-docs
        app_docs_dir = f"/srv/apps/{app_id}/docs/portal"
        target_dir = "/app/app-docs"  # Shared volume mount point in deploy-api
        
        # Check if docs/portal exists in the app (in core-apps container)
        check_cmd = f"test -d {app_docs_dir} && echo 'EXISTS' || echo 'NONE'"
        stdout, stderr, code = await execute_docker_command(check_cmd, timeout=10)
        
        if 'EXISTS' not in stdout:
            logger.debug(f"No docs/portal/ directory in {app_id} - skipping docs sync")
            return True
        
        logs.append(f"📚 Syncing {app_id} documentation to docs-api...")
        
        # Copy docs from core-apps container to a temp location,
        # then into the shared app-docs volume
        # First, clean up old docs for this app
        sync_cmd = f"""
            set -e
            # Copy docs from core-apps to deploy-api's shared volume
            # The app-docs volume is mounted at {target_dir} in deploy-api
            mkdir -p {target_dir}/{app_id}
            rm -rf {target_dir}/{app_id}/*
            
            # We need to get files from core-apps container
            # Use docker cp to extract, then place in shared volume
            TMPDIR=$(mktemp -d)
            docker cp {CORE_APPS_CONTAINER}:{app_docs_dir}/. "$TMPDIR/"
            cp -r "$TMPDIR"/. {target_dir}/{app_id}/
            rm -rf "$TMPDIR"
            echo "DOCS_SYNCED"
        """
        
        # This runs on the host (deploy-api container), not inside core-apps
        proc = await asyncio.create_subprocess_exec(
            '/bin/bash', '-c', sync_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            stdout_str = stdout.decode()
            stderr_str = stderr.decode()
            
            if proc.returncode == 0 and 'DOCS_SYNCED' in stdout_str:
                logs.append(f"📚 Documentation synced for {app_id}")
                return True
            else:
                logs.append(f"⚠️ Docs sync had issues: {stderr_str}")
                return True  # Non-fatal
        except asyncio.TimeoutError:
            logs.append(f"⚠️ Docs sync timed out for {app_id}")
            return True  # Non-fatal
    else:
        # Proxmox: app is at /srv/apps/<app_id> on apps-lxc
        # docs content lives at DOCS_CONTENT_DIR (e.g., /srv/docs/docs on docs-lxc)
        # We need to SSH to apps-lxc to read docs, then SSH to the docs host to write
        app_docs_dir = f"/srv/apps/{app_id}/docs/portal"
        target_dir = f"{DOCS_CONTENT_DIR}/apps/{app_id}"
        
        # Check and sync docs (runs on apps-lxc via SSH)
        sync_cmd = f"""
            set -e
            if [ -d "{app_docs_dir}" ]; then
                echo "Found docs/portal/ in {app_id}"
                # Ensure target directory exists
                mkdir -p {target_dir}
                # Clean old docs and copy new ones
                rm -rf {target_dir}/*
                cp -r {app_docs_dir}/. {target_dir}/
                echo "DOCS_SYNCED: $(ls {target_dir}/ | wc -l) files"
            else
                echo "NO_DOCS"
            fi
        """
        
        stdout, stderr, code = await execute_in_core_apps(
            sync_cmd,
            environment=environment,
            timeout=30
        )
        
        if 'NO_DOCS' in stdout:
            logger.debug(f"No docs/portal/ directory in {app_id} - skipping docs sync")
            return True
        
        if 'DOCS_SYNCED' in stdout:
            logs.append(f"📚 Documentation synced for {app_id}")
            return True
        
        if code != 0:
            logs.append(f"⚠️ Docs sync had issues: {stderr}")
        
        return True  # Non-fatal - don't fail deployment over docs


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
        app_id: App identifier (e.g., "busibox-portal", "busibox-agents")
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
    
    # K8s backend: delegate to k8s_executor
    if is_k8s_environment():
        from .k8s_executor import deploy_core_app as k8s_deploy_core_app
        app_info = CORE_APPS[app_id]
        return await k8s_deploy_core_app(
            app_id=app_id,
            github_repo=app_info["github_repo"],
            github_ref=github_ref,
            port=app_info["default_port"],
            base_path=app_info["base_path"],
            health_endpoint=app_info["health_endpoint"],
            github_token=github_token,
            env_vars=env_vars,
            logs=logs,
        )
    
    # Determine deployment command based on environment
    if is_docker_environment():
        # Docker: monorepo is volume-mounted at /srv/busibox-frontend.
        # Build the shared lib, then build the specific app, then restart via
        # the app-manager control API.
        app_info = CORE_APPS[app_id]
        monorepo_app_dir = app_info.get('monorepo_app_dir', '')
        short_name = monorepo_app_dir.replace("apps/", "")  # e.g. "portal"
        
        command = f"""
            set -e
            cd /srv/busibox-frontend
            echo "=== Cleaning stale node_modules ==="
            rm -rf node_modules/.cache 2>/dev/null || true
            pnpm install --no-frozen-lockfile
            echo "=== Building shared package ==="
            pnpm --filter @jazzmind/busibox-app build
            echo "=== Building {app_id} ==="
            cd /srv/busibox-frontend/{monorepo_app_dir}
            rm -rf .next 2>/dev/null || true
            NODE_ENV=production pnpm run build
            echo "=== Restarting {short_name} via app-manager ==="
            curl -s -X POST http://localhost:9999/build -H 'Content-Type: application/json' -d '{{"app":"{short_name}"}}' || true
            curl -s -X POST http://localhost:9999/restart -H 'Content-Type: application/json' -d '{{"app":"{short_name}"}}' || true
            echo "=== Deploy complete ==="
        """
    else:
        # Proxmox: Monorepo deployment
        # All core apps share a single clone of busibox-frontend.
        # We clone/update the monorepo once, install deps at the root,
        # then build the specific app with pnpm --filter.
        app_info = CORE_APPS[app_id]
        repo = app_info['github_repo']
        monorepo_app_dir = app_info.get('monorepo_app_dir', '')
        
        if github_token:
            logs.append(f"🔐 Using GitHub token for authenticated clone")
            repo_url = f"https://{github_token}@github.com/{repo}.git"
        else:
            logs.append(f"⚠️ No GitHub token - attempting public clone")
            repo_url = f"https://github.com/{repo}.git"
        
        npmrc_commands = ""
        if github_token:
            token_preview = f"{github_token[:4]}...{github_token[-4:]}" if len(github_token) > 8 else "***"
            logs.append(f"📝 Setting up .npmrc with token: {token_preview}")
            npmrc_commands = f"""
            echo "=== NPMRC SETUP START ==="
            cat > /root/.npmrc << 'NPMRC_EOF'
//npm.pkg.github.com/:_authToken={github_token}
@jazzmind:registry=https://npm.pkg.github.com
NPMRC_EOF
            echo "npmrc created at /root/.npmrc"
            echo "=== NPMRC SETUP END ==="
            """
        else:
            npmrc_commands = """
            echo "=== WARNING: No GitHub token provided - private packages will fail! ==="
            """
        
        env_exports = ""
        if env_vars:
            logs.append(f"📦 Setting {len(env_vars)} environment variables for build")
            env_export_lines = []
            for key, value in env_vars.items():
                escaped_value = shlex.quote(value) if value else "''"
                env_export_lines.append(f"export {key}={escaped_value}")
            env_exports = "\n            ".join(env_export_lines)
            env_exports = f"""
            echo "Setting {len(env_vars)} environment variables..."
            {env_exports}
            echo "Environment variables set: {', '.join(sorted(env_vars.keys()))}"
            """
        
        # pnpm filter name from the monorepo (e.g., "@busibox/portal")
        pnpm_pkg_name = monorepo_app_dir.replace("apps/", "@busibox/")
        
        command = f"""
            set -e
            
            echo "=== MONOREPO DEPLOY START ==="
            echo "App: {app_id}"
            echo "Monorepo subdir: {monorepo_app_dir}"
            echo "pnpm filter: {pnpm_pkg_name}"
            echo "Environment: {environment}"
            echo "GitHub ref: {github_ref}"
            echo "Token provided: {'yes' if github_token else 'no'}"
            {env_exports}
            mkdir -p /srv/apps
            
            MONO_DIR="{MONOREPO_CLONE_DIR}"
            APP_DIR="$MONO_DIR/{monorepo_app_dir}"
            {npmrc_commands}
            
            # Clone or update the shared monorepo
            if [ -d "$MONO_DIR/.git" ]; then
                echo "DEBUG: Updating existing monorepo at $MONO_DIR"
                cd "$MONO_DIR"
                echo "DEBUG: git fetch origin..."
                git fetch origin 2>&1
                echo "DEBUG: git fetch done (exit $?)"
                echo "DEBUG: git checkout {github_ref}..."
                git checkout {github_ref} 2>&1
                echo "DEBUG: git checkout done (exit $?)"
                echo "DEBUG: git reset --hard origin/{github_ref}..."
                git reset --hard origin/{github_ref} 2>&1
                echo "DEBUG: git reset done (exit $?)"
            else
                echo "DEBUG: Cloning monorepo from GitHub..."
                rm -rf "$MONO_DIR"
                git clone {repo_url} "$MONO_DIR" 2>&1
                echo "DEBUG: git clone done (exit $?)"
                cd "$MONO_DIR"
                git checkout {github_ref} 2>&1
                echo "DEBUG: git checkout done (exit $?)"
            fi
            
            cd "$MONO_DIR"
            echo "DEBUG: pwd=$(pwd), git branch=$(git branch --show-current)"
            
            # Install pnpm if not available
            if ! command -v pnpm &>/dev/null; then
                echo "DEBUG: Installing pnpm..."
                npm install -g pnpm 2>&1
                echo "DEBUG: pnpm install done (exit $?)"
            else
                echo "DEBUG: pnpm already available at $(which pnpm) version $(pnpm --version)"
            fi
            
            # Set GITHUB_AUTH_TOKEN for .npmrc interpolation
            export GITHUB_AUTH_TOKEN='{github_token}'
            
            # Clean stale node_modules to prevent duplicate React instances
            echo "=== CLEANING STALE DEPS ==="
            rm -rf node_modules/.cache 2>/dev/null || true
            for app_dir in apps/*/node_modules; do
                rm -rf "$app_dir/.cache" 2>/dev/null || true
            done
            
            # Install all workspace dependencies from root
            echo "=== PNPM INSTALL START ==="
            NODE_ENV=development pnpm install --frozen-lockfile 2>&1 || NODE_ENV=development pnpm install 2>&1
            echo "=== PNPM INSTALL END (exit $?) ==="
            
            # Build shared packages then the app
            echo "=== BUILD SHARED PACKAGES ==="
            pnpm --filter @jazzmind/busibox-app run build 2>&1
            echo "=== BUILD SHARED PACKAGES DONE (exit $?) ==="
            
            cd "$APP_DIR"
            echo "=== BUILD START ({pnpm_pkg_name}) ==="
            echo "DEBUG: APP_DIR=$(pwd)"
            rm -rf .next 2>/dev/null || true
            NODE_ENV=production pnpm run build 2>&1
            echo "=== BUILD END (exit $?) ==="
            
            # Copy standalone assets.
            # In a monorepo, Next.js nests the standalone output under the
            # app's relative path from the tracing root, e.g.:
            #   .next/standalone/apps/portal/server.js
            # We locate server.js and copy assets next to it.
            if [ -d ".next/standalone" ]; then
                echo "=== STANDALONE ASSETS START ==="
                SERVER_JS=$(find .next/standalone -name server.js -maxdepth 4 | head -1)
                if [ -n "$SERVER_JS" ]; then
                    SERVER_DIR=$(dirname "$SERVER_JS")
                    echo "Found server.js at: $SERVER_JS"
                    cp -r public "$SERVER_DIR/public" 2>/dev/null || true
                    mkdir -p "$SERVER_DIR/.next"
                    cp -r .next/static "$SERVER_DIR/.next/static"
                else
                    echo "WARNING: server.js not found in .next/standalone/"
                fi
                echo "=== STANDALONE ASSETS END ==="
            fi
            
            # Create a symlink at /srv/apps/<app-id> pointing into the monorepo
            # so systemd services, .env files, and health checks find the app
            LINK="/srv/apps/{app_id}"
            if [ -L "$LINK" ]; then
                rm "$LINK"
            elif [ -d "$LINK" ]; then
                echo "Migrating legacy app dir to monorepo layout..."
                rm -rf "$LINK"
            fi
            ln -sf "$APP_DIR" "$LINK"
            echo "Symlink: $LINK -> $APP_DIR"
            
            # Restart with systemd
            echo "Restarting service..."
            systemctl restart {app_id} || echo "Service restart skipped (may not exist yet)"
            
            echo "=== MONOREPO DEPLOY END ==="
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
        parts = []
        if stderr:
            parts.append(f"STDERR:\n{stderr.strip()}")
        if stdout:
            last_lines = "\n".join(stdout.strip().split("\n")[-30:])
            parts.append(f"STDOUT (last 30 lines):\n{last_lines}")
        combined_output = "\n---\n".join(parts) if parts else "(no output)"
        return False, f"Deployment failed (exit {code}):\n{combined_output}"
    
    # Sync app documentation to docs-api content directory
    await sync_app_docs(app_id, logs, environment)
    
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
        # Parse supervisorctl output: "busibox-portal   RUNNING   pid 1234, uptime 0:01:23"
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
    if is_docker_environment():
        stdout, stderr, code = await execute_docker_proxy_command(
            test_command,
            timeout=10
        )
    else:
        stdout, stderr, code = await execute_ssh_command(
            config.nginx_host,
            test_command,
            timeout=10
        )
    
    if code != 0:
        logs.append(f"❌ Nginx config test failed: {stderr}")
        return False, f"Nginx config test failed: {stderr}"
    
    # Reload nginx
    reload_command = "nginx -s reload"
    if is_docker_environment():
        stdout, stderr, code = await execute_docker_proxy_command(
            reload_command,
            timeout=10
        )
    else:
        stdout, stderr, code = await execute_ssh_command(
            config.nginx_host,
            reload_command,
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
