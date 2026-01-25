"""
Container Executor

Executes commands in containers via SSH (Proxmox LXC) or docker exec (Docker).
Handles the full app deployment lifecycle: git clone, npm install, build, migrations, systemd.
"""

import asyncio
import logging
import os
from typing import Tuple, List, Optional
from .models import BusiboxManifest, DeploymentConfig
from .config import config

logger = logging.getLogger(__name__)

# Container name for user apps in Docker
USER_APPS_CONTAINER = "local-user-apps"


def is_docker_environment() -> bool:
    """Check if running in Docker (local development)"""
    # In Docker, POSTGRES_HOST is typically 'postgres' (container name) not an IP
    return not config.postgres_host.startswith('10.')


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


async def execute_docker_command(command: str, timeout: int = 300) -> Tuple[str, str, int]:
    """Execute command in Docker user-apps container via docker exec"""
    docker_command = [
        'docker', 'exec', USER_APPS_CONTAINER,
        '/bin/bash', '-c', command
    ]
    
    proc = await asyncio.create_subprocess_exec(
        *docker_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(), stderr.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        return "", "Command timed out", 1


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


async def install_dependencies(app_path: str, logs: List[str]) -> bool:
    """Install npm dependencies"""
    logs.append(f"📦 Installing dependencies...")
    
    # Check for package.json
    check_cmd = f"test -f {app_path}/package.json"
    _, _, exists = await execute_in_container(check_cmd)
    
    if exists != 0:
        logs.append("⚠️ No package.json found, skipping npm install")
        return True
    
    # Check for package-lock.json to decide between npm ci and npm install
    check_lock_cmd = f"test -f {app_path}/package-lock.json"
    _, _, lock_exists = await execute_in_container(check_lock_cmd)
    
    if lock_exists == 0:
        # Has package-lock.json, use npm ci for reproducible builds
        logs.append("📦 Using npm ci (package-lock.json found)")
        command = f"""
cd {app_path} && \
npm ci --legacy-peer-deps 2>&1
"""
    else:
        # No package-lock.json, use npm install
        logs.append("📦 Using npm install (no package-lock.json)")
        command = f"""
cd {app_path} && \
npm install --legacy-peer-deps 2>&1
"""
    
    stdout, stderr, code = await execute_in_container(command, timeout=600)
    
    if code != 0:
        logs.append(f"❌ npm install failed: {stderr or stdout}")
        return False
    
    logs.append("✅ Dependencies installed")
    return True


async def run_build(app_path: str, build_command: str, logs: List[str]) -> bool:
    """Run build command"""
    logs.append(f"🔨 Building application...")
    
    command = f"""
cd {app_path} && \
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
        
        # Stop any existing process
        stop_command = f"pkill -f 'node.*{app_id}' || true"
        await execute_in_container(stop_command)
        
        # Build environment export statements
        env_exports = "\n".join([f'export {k}="{v}"' for k, v in (env_vars or {}).items()])
        
        # Start new process in background with nohup
        command = f"""
cd {app_path}
{env_exports}
nohup {start_command} > /tmp/{app_id}.log 2>&1 &
echo $! > /tmp/{app_id}.pid
"""
        
        stdout, stderr, code = await execute_in_container(command, timeout=10)
        
        if code != 0:
            logs.append(f"❌ Failed to start app: {stderr}")
            return False
        
        # Give it a moment to start
        await asyncio.sleep(2)
        
        # Check if process is running
        check_cmd = f"ps aux | grep -v grep | grep -q 'node.*{app_id}' && echo 'running' || echo 'not running'"
        stdout, _, _ = await execute_in_container(check_cmd)
        
        if 'running' in stdout:
            logs.append(f"✅ Application started (PID file: /tmp/{app_id}.pid, log: /tmp/{app_id}.log)")
            return True
        else:
            logs.append(f"⚠️ Application may not have started - check /tmp/{app_id}.log")
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


async def stop_app(app_id: str, logs: List[str]) -> bool:
    """Stop app via systemd"""
    logs.append("🛑 Stopping application...")
    
    command = f"systemctl stop {app_id}.service 2>/dev/null || true"
    
    await execute_in_container(command)
    logs.append("✅ Application stopped")
    return True


async def check_app_health(app_id: str, port: int, health_endpoint: str, logs: List[str]) -> bool:
    """Check if app is healthy"""
    logs.append("🔍 Checking application health...")
    
    max_attempts = 30
    
    for attempt in range(max_attempts):
        command = f"curl -sf http://localhost:{port}{health_endpoint} > /dev/null 2>&1"
        _, _, code = await execute_in_container(command)
        
        if code == 0:
            logs.append(f"✅ Health check passed on attempt {attempt + 1}")
            return True
        
        if attempt < max_attempts - 1:
            await asyncio.sleep(2)
    
    logs.append(f"❌ Health check failed after {max_attempts} attempts")
    return False


async def deploy_app(
    manifest: BusiboxManifest,
    deploy_config: DeploymentConfig,
    database_url: Optional[str],
    logs: List[str]
) -> bool:
    """
    Full deployment flow:
    1. Clone/update repo (or use dev-apps path)
    2. Install dependencies (skip for dev mode)
    3. Build (skip for dev mode)
    4. Run migrations
    5. Create/update systemd service
    6. Start app
    7. Health check
    """
    
    is_dev_mode = deploy_config.devMode
    
    # Check if Docker environment - log appropriate context
    if is_docker_environment():
        logs.append("📦 Docker/local environment detected")
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
    
    # Step 2: Install dependencies (skip for dev mode - local dev should have node_modules)
    if is_dev_mode:
        logs.append("⏭️ Skipping npm install (dev mode - use local node_modules)")
    else:
        if not await install_dependencies(app_path, logs):
            return False
    
    # Step 3: Build (skip for dev mode - use local dev server)
    if is_dev_mode:
        logs.append("⏭️ Skipping build (dev mode - will run dev server)")
    else:
        if not await run_build(app_path, manifest.buildCommand, logs):
            return False
    
    # Step 4: Migrations
    if not await run_migrations(app_path, manifest, database_url, logs):
        return False
    
    # Build environment variables
    env_vars = {
        "NODE_ENV": "production" if deploy_config.environment == "production" else "development",
        "PORT": str(manifest.defaultPort),
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
    if not await check_app_health(manifest.id, manifest.defaultPort, manifest.healthEndpoint, logs):
        logs.append("⚠️ App started but health check failed - check logs")
        # Don't fail deployment for health check - app might just be slow to start
    
    logs.append(f"🎉 Deployment completed! App available at {manifest.defaultPath}")
    return True
