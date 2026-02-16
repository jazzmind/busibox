"""
Bridge Executor
===============

Executes trusted deployment commands via the host bridge script.
Used for core app deployments that require host access (Ansible, SSH, etc.)

Security Model:
- Bridge script only allows approved make commands
- Provides isolation between deploy-api container and host
- All executions are logged for audit trail
"""

import asyncio
import logging
import os
import subprocess
from typing import List, Optional, Tuple, AsyncGenerator

logger = logging.getLogger(__name__)

# Path to bridge script (mounted from host)
BRIDGE_SCRIPT = os.environ.get(
    "BRIDGE_SCRIPT_PATH",
    "/busibox/scripts/bridge/execute.sh"
)

# Alternative path if running in different mount configuration
BUSIBOX_HOST_PATH = os.environ.get("BUSIBOX_HOST_PATH", "")


def get_bridge_script_path() -> str:
    """Get the path to the bridge script."""
    # Try the configured path first
    if os.path.exists(BRIDGE_SCRIPT):
        return BRIDGE_SCRIPT
    
    # Try BUSIBOX_HOST_PATH
    if BUSIBOX_HOST_PATH:
        alt_path = os.path.join(BUSIBOX_HOST_PATH, "scripts/bridge/execute.sh")
        if os.path.exists(alt_path):
            return alt_path
    
    # Fallback: assume we can execute make directly (for local dev)
    return None


def is_core_app(app_id: str) -> bool:
    """
    Determine if an app is a core app (trusted) or user app (untrusted).
    
    Core apps are deployed via the bridge script (Ansible/Makefile).
    User apps are deployed via docker exec into user-apps container.
    """
    CORE_APPS = {
        "busibox-portal",
        "busibox-agents",
        "authz-api",
        "data-api",
        "search-api",
        "agent-api",
        "docs-api",
        "deploy-api",
        "embedding-api",
        "litellm",
    }
    return app_id.lower() in CORE_APPS


async def execute_via_bridge(
    command: str,
    args: List[str] = None,
    env: dict = None,
    timeout: int = 600,
    stream_output: bool = False
) -> Tuple[bool, str, List[str]]:
    """
    Execute a command via the bridge script.
    
    Args:
        command: The make command to execute (e.g., "make deploy-busibox-portal")
        args: Additional arguments to pass
        env: Additional environment variables
        timeout: Command timeout in seconds
        stream_output: If True, yield output lines as they arrive
        
    Returns:
        Tuple of (success, output, logs)
    """
    bridge_path = get_bridge_script_path()
    logs = []
    
    # Build the full command
    if bridge_path:
        cmd_parts = [bridge_path] + command.split()
    else:
        # No bridge script - execute directly (local dev mode)
        cmd_parts = command.split()
    
    if args:
        cmd_parts.extend(args)
    
    # Prepare environment
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)
    
    logger.info(f"Bridge executor: running '{' '.join(cmd_parts)}'")
    logs.append(f"Executing: {' '.join(cmd_parts)}")
    
    try:
        # Create subprocess
        process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=cmd_env,
            cwd=BUSIBOX_HOST_PATH or None
        )
        
        # Collect output
        output_lines = []
        
        async def read_output():
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded = line.decode('utf-8', errors='replace').rstrip()
                output_lines.append(decoded)
                logs.append(decoded)
                logger.debug(f"Bridge: {decoded}")
        
        # Wait for completion with timeout
        try:
            await asyncio.wait_for(read_output(), timeout=timeout)
            await process.wait()
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            logs.append(f"Command timed out after {timeout}s")
            return False, "\n".join(output_lines), logs
        
        success = process.returncode == 0
        output = "\n".join(output_lines)
        
        if success:
            logs.append("Command completed successfully")
        else:
            logs.append(f"Command failed with exit code {process.returncode}")
        
        return success, output, logs
        
    except FileNotFoundError:
        error_msg = f"Bridge script not found at {bridge_path}"
        logger.error(error_msg)
        logs.append(f"Error: {error_msg}")
        return False, "", logs
        
    except Exception as e:
        error_msg = f"Bridge execution error: {str(e)}"
        logger.error(error_msg, exc_info=True)
        logs.append(f"Error: {error_msg}")
        return False, "", logs


async def stream_via_bridge(
    command: str,
    args: List[str] = None,
    env: dict = None,
    timeout: int = 600
) -> AsyncGenerator[str, None]:
    """
    Execute a command via bridge and stream output lines.
    
    Yields output lines as they arrive for real-time streaming.
    """
    bridge_path = get_bridge_script_path()
    
    # Build the full command
    if bridge_path:
        cmd_parts = [bridge_path] + command.split()
    else:
        cmd_parts = command.split()
    
    if args:
        cmd_parts.extend(args)
    
    # Prepare environment
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)
    
    logger.info(f"Bridge stream: running '{' '.join(cmd_parts)}'")
    yield f"Executing: {' '.join(cmd_parts)}"
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=cmd_env,
            cwd=BUSIBOX_HOST_PATH or None
        )
        
        # Stream output
        start_time = asyncio.get_event_loop().time()
        
        while True:
            # Check timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                process.kill()
                yield f"Command timed out after {timeout}s"
                break
            
            try:
                line = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=1.0
                )
                if not line:
                    break
                yield line.decode('utf-8', errors='replace').rstrip()
            except asyncio.TimeoutError:
                # Check if process is still running
                if process.returncode is not None:
                    break
                continue
        
        await process.wait()
        
        if process.returncode == 0:
            yield "Command completed successfully"
        else:
            yield f"Command failed with exit code {process.returncode}"
            
    except Exception as e:
        yield f"Error: {str(e)}"


async def deploy_core_app(
    app_name: str,
    environment: str = "docker",
    branch: str = None,
    force_rebuild: bool = False,
    logs: List[str] = None
) -> Tuple[bool, str]:
    """
    Deploy a core application via Makefile/Ansible.
    
    Args:
        app_name: Name of the app (e.g., "busibox-portal", "busibox-agents")
        environment: Target environment (docker, staging, production)
        branch: Branch to deploy from (optional)
        force_rebuild: Force rebuild even if up to date
        logs: List to append log messages to
        
    Returns:
        Tuple of (success, message)
    """
    if logs is None:
        logs = []
    
    # Build make command
    cmd = f"make deploy-{app_name}"
    args = []
    
    # Add environment-specific arguments
    if environment in ("staging", "production"):
        args.append(f"INV=inventory/{environment}")
    elif environment == "docker":
        args.append("USE_ANSIBLE=1")
    
    if branch:
        args.append(f"BRANCH={branch}")
    
    if force_rebuild:
        args.append("REBUILD=1")
    
    logs.append(f"Deploying {app_name} to {environment}")
    
    success, output, exec_logs = await execute_via_bridge(
        cmd,
        args=args,
        timeout=900  # 15 minutes for full deployment
    )
    
    logs.extend(exec_logs)
    
    if success:
        return True, f"{app_name} deployed successfully"
    else:
        return False, f"Deployment failed: {output[-500:]}"  # Last 500 chars


async def undeploy_core_app(
    app_name: str,
    environment: str = "docker",
    logs: List[str] = None
) -> Tuple[bool, str]:
    """
    Undeploy/stop a core application.
    
    For core apps, this typically means stopping the service,
    not removing it entirely (that requires manual intervention).
    """
    if logs is None:
        logs = []
    
    # For Docker, we can stop the container
    # For LXC, we stop the systemd service
    
    logs.append(f"Stopping {app_name} in {environment}")
    
    if environment == "docker":
        cmd = "make docker-down"
        args = [f"SERVICE={app_name}"]
    else:
        # For Proxmox, we'd need a different approach
        # This is a placeholder
        logs.append("Proxmox undeploy not yet implemented via bridge")
        return False, "Not implemented for Proxmox"
    
    success, output, exec_logs = await execute_via_bridge(cmd, args=args)
    logs.extend(exec_logs)
    
    if success:
        return True, f"{app_name} stopped successfully"
    else:
        return False, f"Stop failed: {output}"
