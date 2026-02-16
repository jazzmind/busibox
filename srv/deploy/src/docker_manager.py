"""
Docker Container and Compose Management

Manages Docker containers and compose services for Busibox.
"""

import os
import asyncio
import logging
import subprocess
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class DockerManager:
    """Manages Docker containers and compose services."""
    
    def __init__(self):
        self.container_prefix = os.environ.get("CONTAINER_PREFIX", "").strip()
        if not self.container_prefix:
            raise RuntimeError("CONTAINER_PREFIX must be set for deploy-api")
        self.project_name = os.environ.get("COMPOSE_PROJECT_NAME", "local-busibox")
        self.repo_root = os.environ.get("BUSIBOX_REPO_ROOT", "/busibox")
    
    async def list_services(self) -> List[Dict[str, Any]]:
        """
        List all containers in the busibox project.
        
        Returns:
            List of container info dictionaries.
        """
        try:
            # Use docker ps with filters for our project label
            result = await self._run_docker_command([
                "docker", "ps", "-a",
                "--filter", f"label=com.docker.compose.project={self.project_name}",
                "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.State}}\t{{.Label \"busibox.tier\"}}"
            ])
            
            containers = []
            for line in result.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 4:
                    containers.append({
                        "id": parts[0][:12],
                        "name": parts[1],
                        "status": parts[2],
                        "state": parts[3],
                        "tier": parts[4] if len(parts) > 4 else "unknown",
                    })
            
            return containers
        except Exception as e:
            logger.error(f"Failed to list services: {e}")
            return []
    
    async def get_service_status(self, service: str) -> Dict[str, Any]:
        """
        Get status of a specific service.
        
        Args:
            service: Service name (e.g., "postgres", "authz-api").
        
        Returns:
            Service status dictionary.
        """
        container_name = self._get_container_name(service)
        
        try:
            result = await self._run_docker_command([
                "docker", "inspect",
                "--format", "{{.State.Status}}\t{{.State.Health.Status}}\t{{.State.StartedAt}}",
                container_name
            ])
            
            parts = result.strip().split("\t")
            return {
                "name": service,
                "container": container_name,
                "status": parts[0] if len(parts) > 0 else "unknown",
                "health": parts[1] if len(parts) > 1 else None,
                "started_at": parts[2] if len(parts) > 2 else None,
            }
        except Exception as e:
            logger.warning(f"Failed to get status for {service}: {e}")
            return {
                "name": service,
                "container": container_name,
                "status": "not_found",
                "health": None,
                "started_at": None,
            }
    
    async def start_service(self, service: str) -> Dict[str, Any]:
        """
        Start a stopped container.
        
        Args:
            service: Service name to start.
        
        Returns:
            Result dictionary with success status.
        """
        container_name = self._get_container_name(service)
        
        try:
            await self._run_docker_command(["docker", "start", container_name])
            status = await self.get_service_status(service)
            return {"success": True, **status}
        except Exception as e:
            logger.error(f"Failed to start {service}: {e}")
            return {"success": False, "error": str(e)}
    
    async def stop_service(self, service: str) -> Dict[str, Any]:
        """
        Stop a running container.
        
        Args:
            service: Service name to stop.
        
        Returns:
            Result dictionary with success status.
        """
        container_name = self._get_container_name(service)
        
        try:
            await self._run_docker_command(["docker", "stop", container_name])
            return {"success": True, "status": "stopped"}
        except Exception as e:
            logger.error(f"Failed to stop {service}: {e}")
            return {"success": False, "error": str(e)}
    
    async def restart_service(self, service: str) -> Dict[str, Any]:
        """
        Restart a container.
        
        Args:
            service: Service name to restart.
        
        Returns:
            Result dictionary with success status.
        """
        container_name = self._get_container_name(service)
        
        try:
            await self._run_docker_command(["docker", "restart", container_name])
            status = await self.get_service_status(service)
            return {"success": True, **status}
        except Exception as e:
            logger.error(f"Failed to restart {service}: {e}")
            return {"success": False, "error": str(e)}
    
    async def compose_up(
        self,
        services: Optional[List[str]] = None,
        compose_file: str = "docker-compose.yml",
        detach: bool = True
    ) -> Dict[str, Any]:
        """
        Start services via docker compose.
        
        Args:
            services: Optional list of specific services to start.
            compose_file: Compose file to use.
            detach: Whether to run in detached mode.
        
        Returns:
            Result dictionary with success status.
        """
        cmd = [
            "docker", "compose",
            "-f", compose_file,
            "-p", self.project_name,
            "up"
        ]
        
        if detach:
            cmd.append("-d")
        
        if services:
            cmd.extend(services)
        
        try:
            result = await self._run_docker_command(cmd, cwd=self.repo_root)
            return {
                "success": True,
                "output": result,
                "services": services or "all",
            }
        except Exception as e:
            logger.error(f"Failed to compose up: {e}")
            return {"success": False, "error": str(e)}
    
    async def compose_down(
        self,
        compose_file: str = "docker-compose.yml",
        remove_volumes: bool = False
    ) -> Dict[str, Any]:
        """
        Stop and remove compose services.
        
        Args:
            compose_file: Compose file to use.
            remove_volumes: Whether to remove volumes.
        
        Returns:
            Result dictionary with success status.
        """
        cmd = [
            "docker", "compose",
            "-f", compose_file,
            "-p", self.project_name,
            "down"
        ]
        
        if remove_volumes:
            cmd.append("-v")
        
        try:
            result = await self._run_docker_command(cmd, cwd=self.repo_root)
            return {"success": True, "output": result}
        except Exception as e:
            logger.error(f"Failed to compose down: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_service_logs(
        self,
        service: str,
        lines: int = 100,
        follow: bool = False
    ) -> Dict[str, Any]:
        """
        Get logs for a service.
        
        Args:
            service: Service name.
            lines: Number of lines to retrieve.
            follow: Whether to follow logs (not supported in async).
        
        Returns:
            Dictionary with logs.
        """
        container_name = self._get_container_name(service)
        
        try:
            result = await self._run_docker_command([
                "docker", "logs",
                "--tail", str(lines),
                container_name
            ])
            return {"success": True, "logs": result}
        except Exception as e:
            logger.error(f"Failed to get logs for {service}: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_system_health(self) -> Dict[str, Any]:
        """
        Get overall system health status.
        
        Returns:
            System health dictionary with service statuses.
        """
        services = await self.list_services()
        
        # Core bootstrap services
        bootstrap_services = ["postgres", "authz-api", "deploy-api", "busibox-portal", "nginx"]
        
        # Check each service
        service_status = {}
        healthy_count = 0
        total_count = 0
        
        for svc_name in bootstrap_services:
            status = await self.get_service_status(svc_name)
            service_status[svc_name] = status
            
            if status["status"] == "running":
                if status["health"] in [None, "healthy"]:
                    healthy_count += 1
            total_count += 1
        
        return {
            "status": "healthy" if healthy_count == total_count else "degraded" if healthy_count > 0 else "unhealthy",
            "healthy_services": healthy_count,
            "total_services": total_count,
            "services": service_status,
        }
    
    def _get_container_name(self, service: str) -> str:
        """
        Get container name for a service.
        
        The container name is {prefix}-{service} based on compose project.
        """
        # Docker now uses a dedicated proxy container; keep nginx alias compatibility.
        resolved_service = "proxy" if service == "nginx" else service
        return f"{self.container_prefix}-{resolved_service}"
    
    async def _run_docker_command(
        self,
        cmd: List[str],
        cwd: Optional[str] = None,
        timeout: int = 60
    ) -> str:
        """
        Run a docker command asynchronously.
        
        Args:
            cmd: Command and arguments.
            cwd: Working directory.
            timeout: Command timeout in seconds.
        
        Returns:
            Command stdout.
        
        Raises:
            Exception: If command fails.
        """
        logger.debug(f"Running command: {' '.join(cmd)}")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            raise Exception(f"Command timed out after {timeout}s")
        
        if process.returncode != 0:
            error_msg = stderr.decode().strip() if stderr else "Unknown error"
            raise Exception(f"Command failed: {error_msg}")
        
        return stdout.decode().strip() if stdout else ""
