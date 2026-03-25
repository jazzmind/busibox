"""
Custom Service Executor - Multi-Service App Deployment
======================================================

Deploys custom (non-Next.js) applications that bring their own Docker Compose
stack. Each custom service gets its own isolated Docker Compose project on
the busibox-net network, with access to platform services (authz, data-api,
agent-api, etc.).

Unlike user-apps (which share a single node:20-slim container), custom services
run in their own containers with their own runtime, database, and process model.

Docker Backend:
  - Clone repo to /srv/custom-services/{app_id} on the host
  - Copy busibox_common shared library into the build context
  - Generate .env with busibox service endpoints
  - docker compose -p {prefix}-custom-{app_id} build && up -d
  - Connect containers to busibox-net for platform service access
  - Health-check each service endpoint

LXC Backend:
  - SSH to target container
  - Clone repo, generate .env, run docker compose or systemd services

Project naming: {CONTAINER_PREFIX}-custom-{app_id}
"""

import asyncio
import json
import logging
import os
import shutil
from typing import Dict, List, Optional, Tuple

from .config import config
from .env_generator import get_service_endpoints
from .models import BusiboxManifest, DeploymentConfig

logger = logging.getLogger(__name__)

CONTAINER_PREFIX = os.environ.get("CONTAINER_PREFIX", "dev")
BUSIBOX_HOST_PATH = os.environ.get("BUSIBOX_HOST_PATH", "/busibox")
CUSTOM_SERVICES_BASE = os.environ.get(
    "CUSTOM_SERVICES_DIR",
    os.path.join(BUSIBOX_HOST_PATH, "custom-services"),
)
BUSIBOX_COMMON_SRC = os.path.join(BUSIBOX_HOST_PATH, "srv", "shared")
BUSIBOX_NET = f"{CONTAINER_PREFIX}-busibox-net"

CUSTOM_SERVICES_REGISTRY = "/tmp/busibox_custom_services_registry.json"


def _project_name(app_id: str) -> str:
    return f"{CONTAINER_PREFIX}-custom-{app_id}"


def _app_dir(app_id: str) -> str:
    return os.path.join(CUSTOM_SERVICES_BASE, app_id)


# ---------------------------------------------------------------------------
# Registry helpers (survives deploy-api restarts)
# ---------------------------------------------------------------------------

def _load_registry() -> Dict:
    try:
        with open(CUSTOM_SERVICES_REGISTRY) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_registry(data: Dict) -> None:
    try:
        with open(CUSTOM_SERVICES_REGISTRY, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Failed to write custom services registry: %s", e)


def register_custom_service(app_id: str, app_path: str, project_name: str, manifest_dict: dict) -> None:
    reg = _load_registry()
    reg[app_id] = {
        "app_path": app_path,
        "project_name": project_name,
        "manifest": manifest_dict,
    }
    _save_registry(reg)


def unregister_custom_service(app_id: str) -> None:
    reg = _load_registry()
    reg.pop(app_id, None)
    _save_registry(reg)


def get_custom_service_info(app_id: str) -> Optional[Dict]:
    return _load_registry().get(app_id)


def is_custom_service(app_id: str) -> bool:
    return app_id in _load_registry()


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

async def _run(cmd: List[str], cwd: Optional[str] = None, timeout: int = 600,
               env: Optional[Dict[str, str]] = None) -> Tuple[str, str, int]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=merged_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(), stderr.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        return "", "Command timed out", 1


async def _run_shell(cmd_str: str, cwd: Optional[str] = None, timeout: int = 600,
                     env: Optional[Dict[str, str]] = None) -> Tuple[str, str, int]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = await asyncio.create_subprocess_shell(
        cmd_str,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=merged_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(), stderr.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        return "", "Command timed out", 1


# ---------------------------------------------------------------------------
# SSH helpers (for LXC backend)
# ---------------------------------------------------------------------------

async def _ssh(host: str, command: str, timeout: int = 300) -> Tuple[str, str, int]:
    ssh_cmd = [
        "ssh",
        "-F", "/dev/null",
        "-i", config.ssh_key_path,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR",
        f"root@{host}",
        command,
    ]
    proc = await asyncio.create_subprocess_exec(
        *ssh_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(), stderr.decode(), proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        return "", "SSH command timed out", 1


# ---------------------------------------------------------------------------
# Docker backend implementation
# ---------------------------------------------------------------------------

def is_docker_environment() -> bool:
    return config.is_docker_backend()


async def clone_or_update_repo(
    manifest: BusiboxManifest,
    deploy_config: DeploymentConfig,
    logs: List[str],
) -> Tuple[bool, str]:
    """Clone or update the custom service repository on the host filesystem."""
    app_id = manifest.id
    app_path = _app_dir(app_id)

    if deploy_config.devMode and deploy_config.localDevDir:
        dev_path = os.path.join(BUSIBOX_HOST_PATH, "dev-apps", deploy_config.localDevDir)
        logs.append(f"Dev mode: using local source at {dev_path}")
        return True, dev_path

    if not deploy_config.githubRepoOwner or not deploy_config.githubRepoName:
        logs.append("No GitHub repository configured")
        return False, app_path

    repo_url = f"https://github.com/{deploy_config.githubRepoOwner}/{deploy_config.githubRepoName}.git"
    if deploy_config.githubToken:
        repo_url = f"https://{deploy_config.githubToken}@github.com/{deploy_config.githubRepoOwner}/{deploy_config.githubRepoName}.git"

    os.makedirs(CUSTOM_SERVICES_BASE, exist_ok=True)

    if os.path.isdir(os.path.join(app_path, ".git")):
        logs.append("Updating existing repository...")
        cmd = f"cd {app_path} && git remote set-url origin {repo_url} && git fetch origin && git checkout {deploy_config.githubBranch} && git reset --hard origin/{deploy_config.githubBranch}"
    else:
        logs.append(f"Cloning repository from {deploy_config.githubRepoOwner}/{deploy_config.githubRepoName}...")
        cmd = f"git clone --branch {deploy_config.githubBranch} --depth 1 {repo_url} {app_path}"

    stdout, stderr, code = await _run_shell(cmd, timeout=600)
    if code != 0:
        combined = "\n".join(filter(None, [stderr.strip(), stdout.strip()]))
        logs.append(f"Git operation failed: {combined or 'no output'}")
        return False, app_path

    logs.append(f"Repository ready at {app_path}")
    return True, app_path


def generate_custom_env(
    manifest: BusiboxManifest,
    deploy_config: DeploymentConfig,
) -> Dict[str, str]:
    """Generate environment variables for a custom service."""
    env: Dict[str, str] = {}

    endpoints = get_service_endpoints(deploy_config.environment)
    for key, value in endpoints.items():
        env[key] = value

    if manifest.auth:
        env["AUTHZ_JWKS_URL"] = f"{endpoints.get('AUTHZ_BASE_URL', 'http://authz-api:8010')}/.well-known/jwks.json"
        env["AUTHZ_TOKEN_URL"] = f"{endpoints.get('AUTHZ_BASE_URL', 'http://authz-api:8010')}/api/v1/auth/token"
        env["AUTHZ_AUDIENCE"] = manifest.auth.audience

    portal_url = os.environ.get("NEXT_PUBLIC_BUSIBOX_PORTAL_URL", "")
    if portal_url:
        env["BUSIBOX_PORTAL_URL"] = portal_url

    for var in manifest.requiredEnvVars:
        if var in endpoints:
            env[var] = endpoints[var]
        elif var in deploy_config.secrets:
            env[var] = deploy_config.secrets[var]

    for var in manifest.optionalEnvVars:
        if var in endpoints:
            env[var] = endpoints[var]
        elif var in deploy_config.secrets:
            env[var] = deploy_config.secrets[var]

    for key, value in deploy_config.secrets.items():
        if key not in env:
            env[key] = value

    for key, value in deploy_config.envVars.items():
        env[key] = value

    return env


def write_env_file(app_path: str, env_vars: Dict[str, str]) -> None:
    """Write a .env file for docker compose."""
    lines = ["# Auto-generated by Busibox Deploy Service", ""]
    for key, value in sorted(env_vars.items()):
        if any(c in value for c in [" ", '"', "'", "$", "\n"]):
            value = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}="{value}"')
        else:
            lines.append(f"{key}={value}")
    env_path = os.path.join(app_path, ".env")
    with open(env_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def copy_busibox_common(app_path: str, logs: List[str]) -> bool:
    """Copy busibox_common shared library into the app's build context."""
    src = os.path.join(BUSIBOX_COMMON_SRC, "busibox_common")
    if not os.path.isdir(src):
        logs.append(f"busibox_common not found at {src}, skipping copy")
        return True

    dest = os.path.join(app_path, "shared", "busibox_common")
    try:
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copytree(src, dest)

        setup_py = os.path.join(BUSIBOX_COMMON_SRC, "setup.py")
        pyproject = os.path.join(BUSIBOX_COMMON_SRC, "pyproject.toml")
        for f in [setup_py, pyproject]:
            if os.path.exists(f):
                shutil.copy2(f, os.path.join(app_path, "shared", os.path.basename(f)))

        logs.append("Copied busibox_common shared library")
        return True
    except Exception as e:
        logs.append(f"Failed to copy busibox_common: {e}")
        return False


async def compose_build(
    app_path: str,
    compose_file: str,
    project_name: str,
    logs: List[str],
) -> bool:
    """Build the custom service compose project."""
    logs.append("Building custom service containers...")
    cmd = ["docker", "compose", "-p", project_name, "-f", compose_file, "build"]
    stdout, stderr, code = await _run(cmd, cwd=app_path, timeout=900)
    if code != 0:
        combined = "\n".join(filter(None, [stderr.strip(), stdout.strip()]))
        if len(combined) > 2000:
            combined = "...(truncated)...\n" + combined[-2000:]
        logs.append(f"Build failed: {combined or 'no output'}")
        return False
    logs.append("Build completed")
    return True


async def compose_up(
    app_path: str,
    compose_file: str,
    project_name: str,
    logs: List[str],
) -> bool:
    """Start the custom service compose project and connect to busibox-net."""
    logs.append("Starting custom service containers...")
    cmd = ["docker", "compose", "-p", project_name, "-f", compose_file, "up", "-d"]
    stdout, stderr, code = await _run(cmd, cwd=app_path, timeout=120)
    if code != 0:
        combined = "\n".join(filter(None, [stderr.strip(), stdout.strip()]))
        logs.append(f"Failed to start containers: {combined or 'no output'}")
        return False

    await asyncio.sleep(2)

    # Connect containers to the busibox network
    ps_cmd = ["docker", "compose", "-p", project_name, "-f", compose_file, "ps", "-q"]
    stdout, stderr, code = await _run(ps_cmd, cwd=app_path, timeout=30)
    if code == 0 and stdout.strip():
        container_ids = [cid.strip() for cid in stdout.strip().split("\n") if cid.strip()]
        for cid in container_ids:
            connect_cmd = ["docker", "network", "connect", BUSIBOX_NET, cid]
            _, cerr, ccode = await _run(connect_cmd, timeout=15)
            if ccode != 0 and "already exists" not in cerr:
                logs.append(f"Warning: Could not connect {cid[:12]} to {BUSIBOX_NET}: {cerr.strip()}")

    logs.append("Custom service containers started")
    return True


async def compose_down(
    app_path: str,
    compose_file: str,
    project_name: str,
    logs: List[str],
) -> bool:
    """Stop and remove the custom service compose project."""
    logs.append("Stopping custom service containers...")
    cmd = ["docker", "compose", "-p", project_name, "-f", compose_file, "down", "--remove-orphans"]
    stdout, stderr, code = await _run(cmd, cwd=app_path, timeout=120)
    if code != 0:
        combined = "\n".join(filter(None, [stderr.strip(), stdout.strip()]))
        logs.append(f"Warning: compose down had issues: {combined or 'no output'}")
    else:
        logs.append("Custom service containers stopped")
    return True


async def check_custom_service_health(
    manifest: BusiboxManifest,
    project_name: str,
    logs: List[str],
) -> bool:
    """Health-check each service endpoint defined in the manifest."""
    if not manifest.services:
        return True

    all_healthy = True
    for svc in manifest.services:
        container_name = f"{project_name}-{svc.name}"
        health_url = f"http://localhost:{svc.port}{svc.healthEndpoint}"

        logs.append(f"Checking health of {svc.name} at {health_url}...")

        # Use docker exec on the service container to check health internally
        healthy = False
        for attempt in range(10):
            cmd = [
                "docker", "exec", container_name,
                "sh", "-c",
                f"curl -sf --max-time 5 http://localhost:{svc.port}{svc.healthEndpoint} 2>&1 || wget -qO- --timeout=5 http://localhost:{svc.port}{svc.healthEndpoint} 2>&1 || true",
            ]
            stdout, stderr, code = await _run(cmd, timeout=15)
            # If docker exec fails (container name mismatch), try via compose
            if code != 0 and ("No such container" in stderr or "not found" in stderr):
                # Fall back to trying via the busibox-net network from deploy-api
                cmd2 = f"curl -sf --max-time 5 http://{container_name}:{svc.port}{svc.healthEndpoint} 2>&1"
                stdout, stderr, code = await _run_shell(cmd2, timeout=15)

            if code == 0 and stdout.strip():
                logs.append(f"  {svc.name}: healthy (attempt {attempt + 1})")
                healthy = True
                break
            if attempt < 9:
                await asyncio.sleep(3)

        if not healthy:
            logs.append(f"  {svc.name}: health check failed after 10 attempts")
            all_healthy = False

    return all_healthy


# ---------------------------------------------------------------------------
# Main deployment pipeline (Docker)
# ---------------------------------------------------------------------------

async def deploy_custom_service(
    manifest: BusiboxManifest,
    deploy_config: DeploymentConfig,
    logs: List[str],
) -> bool:
    """Full deployment pipeline for a custom service (Docker backend)."""
    app_id = manifest.id
    project = _project_name(app_id)
    runtime = manifest.runtime
    compose_file = runtime.composeFile if runtime else "docker-compose.yml"

    logs.append(f"Deploying custom service: {manifest.name}")
    logs.append(f"  Project: {project}")
    logs.append(f"  Compose file: {compose_file}")

    # Step 1: Stop any existing deployment
    existing = get_custom_service_info(app_id)
    if existing:
        logs.append("Stopping existing deployment...")
        await compose_down(
            existing["app_path"],
            compose_file,
            existing["project_name"],
            logs,
        )

    # Step 2: Clone/update repo
    success, app_path = await clone_or_update_repo(manifest, deploy_config, logs)
    if not success:
        return False

    # Step 3: Copy busibox_common shared library
    copy_busibox_common(app_path, logs)

    # Step 4: Generate .env
    env_vars = generate_custom_env(manifest, deploy_config)
    write_env_file(app_path, env_vars)
    logs.append(f"Generated .env with {len(env_vars)} variables")

    # Step 5: Build
    if not await compose_build(app_path, compose_file, project, logs):
        return False

    # Step 6: Start
    if not await compose_up(app_path, compose_file, project, logs):
        return False

    # Step 7: Health check
    healthy = await check_custom_service_health(manifest, project, logs)
    if not healthy:
        logs.append("Warning: Some service endpoints failed health checks")

    # Step 8: Register
    register_custom_service(app_id, app_path, project, manifest.model_dump())
    logs.append(f"Custom service {manifest.name} deployed at {manifest.defaultPath}")
    return True


# ---------------------------------------------------------------------------
# Lifecycle management
# ---------------------------------------------------------------------------

async def stop_custom_service(app_id: str, logs: List[str]) -> bool:
    """Stop a custom service's compose project."""
    info = get_custom_service_info(app_id)
    if not info:
        logs.append(f"No custom service found for {app_id}")
        return True

    app_path = info["app_path"]
    project = info["project_name"]
    manifest_data = info.get("manifest", {})
    compose_file = manifest_data.get("runtime", {}).get("composeFile", "docker-compose.yml")

    cmd = ["docker", "compose", "-p", project, "-f", compose_file, "stop"]
    stdout, stderr, code = await _run(cmd, cwd=app_path, timeout=60)
    if code != 0:
        logs.append(f"Warning: compose stop had issues: {stderr.strip() or stdout.strip()}")
    else:
        logs.append(f"Custom service {app_id} stopped")
    return True


async def restart_custom_service(app_id: str, logs: List[str]) -> bool:
    """Restart a custom service's compose project."""
    info = get_custom_service_info(app_id)
    if not info:
        logs.append(f"No custom service found for {app_id}")
        return False

    app_path = info["app_path"]
    project = info["project_name"]
    manifest_data = info.get("manifest", {})
    compose_file = manifest_data.get("runtime", {}).get("composeFile", "docker-compose.yml")

    cmd = ["docker", "compose", "-p", project, "-f", compose_file, "restart"]
    stdout, stderr, code = await _run(cmd, cwd=app_path, timeout=120)
    if code != 0:
        logs.append(f"Restart failed: {stderr.strip() or stdout.strip()}")
        return False

    logs.append(f"Custom service {app_id} restarted")
    return True


async def undeploy_custom_service(app_id: str, logs: List[str]) -> bool:
    """Fully undeploy a custom service: compose down + nginx cleanup + deregister."""
    info = get_custom_service_info(app_id)
    if not info:
        logs.append(f"No custom service found for {app_id}")
        return True

    app_path = info["app_path"]
    project = info["project_name"]
    manifest_data = info.get("manifest", {})
    compose_file = manifest_data.get("runtime", {}).get("composeFile", "docker-compose.yml")

    # Compose down
    await compose_down(app_path, compose_file, project, logs)

    # Remove nginx config
    if is_docker_environment():
        nginx_config = os.path.join(BUSIBOX_HOST_PATH, "config", "nginx-sites", "apps", f"{app_id}.conf")
        if os.path.exists(nginx_config):
            try:
                os.remove(nginx_config)
                logs.append(f"Removed nginx config: {nginx_config}")
            except Exception as e:
                logs.append(f"Warning: Could not remove nginx config: {e}")

    # Deregister
    unregister_custom_service(app_id)
    logs.append(f"Custom service {app_id} undeployed")
    return True


async def get_custom_service_status(app_id: str) -> Optional[Dict]:
    """Get the status of a custom service's containers."""
    info = get_custom_service_info(app_id)
    if not info:
        return None

    project = info["project_name"]
    manifest_data = info.get("manifest", {})
    compose_file = manifest_data.get("runtime", {}).get("composeFile", "docker-compose.yml")
    app_path = info["app_path"]

    cmd = ["docker", "compose", "-p", project, "-f", compose_file, "ps", "--format", "json"]
    stdout, stderr, code = await _run(cmd, cwd=app_path, timeout=15)
    if code != 0:
        return {"status": "error", "error": stderr.strip()}

    try:
        services = []
        for line in stdout.strip().split("\n"):
            if line.strip():
                services.append(json.loads(line))
        return {"status": "running" if services else "stopped", "services": services}
    except json.JSONDecodeError:
        return {"status": "unknown", "raw_output": stdout.strip()}


# ---------------------------------------------------------------------------
# LXC backend implementation
# ---------------------------------------------------------------------------

async def deploy_custom_service_lxc(
    manifest: BusiboxManifest,
    deploy_config: DeploymentConfig,
    logs: List[str],
    target_host: Optional[str] = None,
) -> bool:
    """Deploy a custom service to a Proxmox LXC container via SSH.

    The target LXC is expected to have Docker installed (many Proxmox LXC
    setups run Docker inside unprivileged containers). The executor SSHs
    in, clones the repo, generates .env, and runs docker compose.
    """
    app_id = manifest.id
    runtime = manifest.runtime
    compose_file = runtime.composeFile if runtime else "docker-compose.yml"
    project = _project_name(app_id)
    remote_base = "/srv/custom-services"
    remote_path = f"{remote_base}/{app_id}"

    host = target_host or os.environ.get("CUSTOM_SERVICES_HOST", "")
    if not host:
        logs.append("CUSTOM_SERVICES_HOST not configured for LXC deployment")
        return False

    logs.append(f"Deploying custom service {manifest.name} to {host} via SSH")

    # Step 1: Ensure base directory exists
    _, _, code = await _ssh(host, f"mkdir -p {remote_base}")
    if code != 0:
        logs.append("Failed to create base directory on target host")
        return False

    # Step 2: Clone or update repo
    if not deploy_config.githubRepoOwner or not deploy_config.githubRepoName:
        logs.append("No GitHub repository configured for LXC deployment")
        return False

    repo_url = f"https://github.com/{deploy_config.githubRepoOwner}/{deploy_config.githubRepoName}.git"
    if deploy_config.githubToken:
        repo_url = f"https://{deploy_config.githubToken}@github.com/{deploy_config.githubRepoOwner}/{deploy_config.githubRepoName}.git"

    check_stdout, _, check_code = await _ssh(host, f"test -d {remote_path}/.git && echo exists")
    if check_code == 0 and "exists" in check_stdout:
        clone_cmd = f"cd {remote_path} && git remote set-url origin {repo_url} && git fetch origin && git checkout {deploy_config.githubBranch} && git reset --hard origin/{deploy_config.githubBranch}"
        logs.append("Updating existing repository on remote host...")
    else:
        clone_cmd = f"git clone --branch {deploy_config.githubBranch} --depth 1 {repo_url} {remote_path}"
        logs.append("Cloning repository on remote host...")

    stdout, stderr, code = await _ssh(host, clone_cmd, timeout=600)
    if code != 0:
        logs.append(f"Git operation failed on remote: {stderr.strip() or stdout.strip()}")
        return False

    # Step 3: Generate .env on remote
    env_vars = generate_custom_env(manifest, deploy_config)
    env_lines = []
    for key, value in sorted(env_vars.items()):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        env_lines.append(f'{key}="{escaped}"')
    env_content = "\n".join(env_lines)

    write_cmd = f"cat > {remote_path}/.env << 'ENVEOF'\n{env_content}\nENVEOF"
    _, stderr, code = await _ssh(host, write_cmd)
    if code != 0:
        logs.append(f"Failed to write .env on remote: {stderr.strip()}")
        return False
    logs.append(f"Generated .env with {len(env_vars)} variables on remote")

    # Step 4: Build and start
    compose_cmd = f"cd {remote_path} && docker compose -p {project} -f {compose_file} build && docker compose -p {project} -f {compose_file} up -d"
    logs.append("Building and starting containers on remote host...")
    stdout, stderr, code = await _ssh(host, compose_cmd, timeout=900)
    if code != 0:
        combined = "\n".join(filter(None, [stderr.strip(), stdout.strip()]))
        if len(combined) > 2000:
            combined = "...(truncated)...\n" + combined[-2000:]
        logs.append(f"Remote deployment failed: {combined or 'no output'}")
        return False

    # Step 5: Register
    register_custom_service(app_id, remote_path, project, manifest.model_dump())
    logs.append(f"Custom service {manifest.name} deployed on {host}")
    return True


async def stop_custom_service_lxc(app_id: str, logs: List[str], target_host: Optional[str] = None) -> bool:
    """Stop a custom service on a remote LXC host."""
    info = get_custom_service_info(app_id)
    if not info:
        logs.append(f"No custom service found for {app_id}")
        return True

    host = target_host or os.environ.get("CUSTOM_SERVICES_HOST", "")
    if not host:
        logs.append("CUSTOM_SERVICES_HOST not configured")
        return False

    project = info["project_name"]
    remote_path = info["app_path"]
    compose_file = info.get("manifest", {}).get("runtime", {}).get("composeFile", "docker-compose.yml")

    cmd = f"cd {remote_path} && docker compose -p {project} -f {compose_file} stop"
    _, stderr, code = await _ssh(host, cmd, timeout=60)
    if code != 0:
        logs.append(f"Warning: remote stop had issues: {stderr.strip()}")
    else:
        logs.append(f"Custom service {app_id} stopped on remote")
    return True


async def undeploy_custom_service_lxc(app_id: str, logs: List[str], target_host: Optional[str] = None) -> bool:
    """Undeploy a custom service from a remote LXC host."""
    info = get_custom_service_info(app_id)
    if not info:
        logs.append(f"No custom service found for {app_id}")
        return True

    host = target_host or os.environ.get("CUSTOM_SERVICES_HOST", "")
    if not host:
        logs.append("CUSTOM_SERVICES_HOST not configured")
        return False

    project = info["project_name"]
    remote_path = info["app_path"]
    compose_file = info.get("manifest", {}).get("runtime", {}).get("composeFile", "docker-compose.yml")

    cmd = f"cd {remote_path} && docker compose -p {project} -f {compose_file} down --remove-orphans"
    await _ssh(host, cmd, timeout=120)

    unregister_custom_service(app_id)
    logs.append(f"Custom service {app_id} undeployed from remote")
    return True
