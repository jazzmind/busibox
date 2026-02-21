"""
Test Runner Routes

API endpoints for running pytest tests against Docker containers.

Only available on non-production environments (development, staging).
Production environment returns 403 for all endpoints.

The deploy-api is the correct place to run tests because:
- It has /var/run/docker.sock mounted (needed for `make test-docker`)
- It has the busibox repo mounted at BUSIBOX_HOST_PATH
- It already has SSE streaming infrastructure
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
import subprocess
import asyncio
import os
import json
import logging
from typing import Optional
from pydantic import BaseModel
from .auth import verify_admin_token
from .config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tests", tags=["tests"])

# Python services available for Docker-based pytest testing
PYTHON_SERVICES = ["authz", "agent", "bridge", "data", "deploy", "search"]

# Services and their default test args
TEST_SUITES = [
    {
        "id": "busibox-agent",
        "name": "Agent Service",
        "project": "busibox",
        "service": "agent",
        "type": "all",
        "framework": "pytest",
        "makeArgs": "SERVICE=agent",
        "description": "Agent/chat service tests (unit, integration, pvt)",
        "estimatedDuration": 120,
    },
    {
        "id": "busibox-data",
        "name": "Data Service",
        "project": "busibox",
        "service": "data",
        "type": "all",
        "framework": "pytest",
        "makeArgs": "SERVICE=data",
        "description": "Data/ingest service tests (unit, integration, pvt)",
        "estimatedDuration": 180,
    },
    {
        "id": "busibox-search",
        "name": "Search Service",
        "project": "busibox",
        "service": "search",
        "type": "all",
        "framework": "pytest",
        "makeArgs": "SERVICE=search",
        "description": "Search service tests (unit, integration, pvt)",
        "estimatedDuration": 60,
    },
    {
        "id": "busibox-authz",
        "name": "Authz Service",
        "project": "busibox",
        "service": "authz",
        "type": "all",
        "framework": "pytest",
        "makeArgs": "SERVICE=authz",
        "description": "Authorization service tests (unit, integration, pvt)",
        "estimatedDuration": 45,
    },
    {
        "id": "busibox-bridge",
        "name": "Bridge Service",
        "project": "busibox",
        "service": "bridge",
        "type": "all",
        "framework": "pytest",
        "makeArgs": "SERVICE=bridge",
        "description": "Bridge service tests (unit, integration)",
        "estimatedDuration": 30,
    },
    {
        "id": "busibox-deploy",
        "name": "Deploy Service",
        "project": "busibox",
        "service": "deploy",
        "type": "all",
        "framework": "pytest",
        "makeArgs": "SERVICE=deploy",
        "description": "Deploy API service tests",
        "estimatedDuration": 30,
    },
]


def _is_production() -> bool:
    """Return True only if explicitly configured as a production (non-Docker) environment.

    The deploy-api docker-compose.yml does NOT set BUSIBOX_ENV or ENVIRONMENT,
    so config.busibox_env defaults to 'production'.  We must not treat Docker dev
    as production — check is_docker_backend() first.

    Production = BUSIBOX_ENV explicitly set to 'production' AND backend is not Docker.
    """
    if config.is_docker_backend():
        # Docker local dev is never production for test purposes
        return False
    env = config.busibox_env.lower()
    return env == "production"


def _get_busibox_path() -> str:
    """Get the busibox repo path available inside this container."""
    return config.busibox_host_path


def _check_not_production():
    """Raise 403 if running in production."""
    if _is_production():
        raise HTTPException(
            status_code=403,
            detail="Test runner is not available in production environments"
        )


# =============================================================================
# GET /api/v1/tests/suites
# =============================================================================

@router.get("/suites")
async def list_test_suites(
    user=Depends(verify_admin_token),
):
    """
    List all available test suites.
    Only available on non-production environments.
    """
    _check_not_production()

    busibox_path = _get_busibox_path()

    suites = []
    for suite in TEST_SUITES:
        suites.append({
            **suite,
            "path": busibox_path,
            "available": os.path.isdir(busibox_path),
        })

    return {
        "testSuites": suites,
        "environment": config.busibox_env,
        "busiboxPath": busibox_path,
    }


# =============================================================================
# GET /api/v1/tests/list
# =============================================================================

@router.get("/list")
async def list_test_files(
    service: str,
    category: Optional[str] = None,
    detail: Optional[str] = None,
    user=Depends(verify_admin_token),
):
    """
    List test files for a given service, optionally filtered by category.
    
    - service=agent                    -> list all files by category
    - service=agent&category=unit      -> list unit test files
    - service=agent&category=unit&detail=full -> collect test IDs from Docker container
    
    Only available on non-production environments.
    """
    _check_not_production()

    if service not in PYTHON_SERVICES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown service '{service}'. Available: {', '.join(PYTHON_SERVICES)}"
        )

    busibox_path = _get_busibox_path()
    list_script = os.path.join(busibox_path, "scripts", "test", "list-tests.sh")

    if not os.path.exists(list_script):
        raise HTTPException(
            status_code=500,
            detail=f"list-tests.sh not found at {list_script}"
        )

    # Build command args: service [category] [detail]
    cmd_args = [service]
    if category:
        cmd_args.append(category)
    if detail:
        cmd_args.append(detail)

    try:
        result = subprocess.run(
            ["bash", list_script] + cmd_args,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=busibox_path,
        )
        raw_output = result.stdout + result.stderr

        # Parse output into structured format
        files = []
        categories = {}
        markers = []

        current_section = None
        for line in raw_output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("Categories:"):
                current_section = "categories"
            elif stripped.startswith("All test files:") or stripped.startswith(f"Test files (tests/{category}"):
                current_section = "files"
            elif stripped.startswith("Available markers:"):
                current_section = "markers"
            elif stripped.startswith("Collecting test IDs"):
                current_section = "ids"
            elif current_section == "categories" and stripped.endswith(")"):
                # Format: "unit/ (27 files)"
                parts = stripped.split("/ (")
                if len(parts) == 2:
                    cat_name = parts[0]
                    try:
                        count = int(parts[1].rstrip(" files)"))
                    except ValueError:
                        count = 0
                    categories[cat_name] = count
            elif current_section in ("files", "ids") and stripped.startswith("tests/"):
                files.append(stripped)
            elif current_section == "markers" and stripped.startswith("@pytest.mark."):
                markers.append(stripped)

        return {
            "service": service,
            "category": category,
            "files": files,
            "categories": categories,
            "markers": markers,
            "rawOutput": raw_output,
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Test listing timed out")
    except Exception as e:
        logger.error(f"[TESTS] list_test_files error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# POST /api/v1/tests/run  (SSE streaming)
# =============================================================================

class TestRunRequest(BaseModel):
    suiteId: str
    service: str
    makeArgs: str  # e.g. "SERVICE=agent ARGS='tests/unit/test_base_agent.py'"
    isSecurity: bool = False


def _parse_make_args(make_args: str, service: str) -> tuple[str, str]:
    """Parse makeArgs string into (service, pytest_args).

    makeArgs formats:
      "SERVICE=agent"
      "SERVICE=agent ARGS='tests/unit/foo.py'"
      "SERVICE=agent ARGS='-m pvt'"

    Returns (service_name, pytest_args_string).
    """
    import re

    # Extract SERVICE= override (may differ from body.service)
    svc_match = re.search(r'SERVICE=(\S+)', make_args)
    resolved_service = svc_match.group(1) if svc_match else service

    # Extract ARGS='...' or ARGS="..." or ARGS=value
    args_match = re.search(r"ARGS='([^']*)'", make_args)
    if not args_match:
        args_match = re.search(r'ARGS="([^"]*)"', make_args)
    if not args_match:
        args_match = re.search(r'ARGS=(\S+)', make_args)

    pytest_args = args_match.group(1) if args_match else ""
    return resolved_service, pytest_args


@router.post("/run")
async def run_tests(
    body: TestRunRequest,
    user=Depends(verify_admin_token),
):
    """
    Run tests for a given service, streaming output as SSE.

    Uses run-local-tests.sh directly (make is not available in the deploy-api container).
    Only available on non-production environments.
    """
    _check_not_production()

    busibox_path = _get_busibox_path()

    if not os.path.isdir(busibox_path):
        raise HTTPException(
            status_code=400,
            detail=f"Busibox path not found: {busibox_path}"
        )

    run_script = os.path.join(busibox_path, "scripts", "test", "run-local-tests.sh")
    if not os.path.exists(run_script):
        raise HTTPException(
            status_code=500,
            detail=f"run-local-tests.sh not found at {run_script}"
        )

    # Parse makeArgs into service + pytest args
    resolved_service, pytest_args = _parse_make_args(body.makeArgs, body.service)

    # Build shell command: bash run-local-tests.sh <service> docker [pytest_args]
    # Quote pytest_args so spaces inside paths/markers are preserved
    if pytest_args:
        # Pass pytest_args as a single positional argument so the script receives it as $3
        # which becomes PYTEST_ARGS in the script
        bash_cmd = f"bash '{run_script}' '{resolved_service}' docker '{pytest_args}'"
    else:
        bash_cmd = f"bash '{run_script}' '{resolved_service}' docker"

    logger.info(f"[TESTS] Running: {bash_cmd} in {busibox_path}")

    async def event_generator():
        def sse(event_type: str, data: str, done: bool = False) -> str:
            return f"data: {json.dumps({'type': event_type, 'data': data, 'done': done})}\n\n"

        yield sse("start", f"Starting: {bash_cmd}")

        try:
            proc = await asyncio.create_subprocess_shell(
                bash_cmd,
                cwd=busibox_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={
                    **os.environ,
                    "PYTHONUNBUFFERED": "1",
                    "FORCE_COLOR": "1",
                    "MAKEFLAGS": "--output-sync=none",
                },
            )

            # Stream output line by line
            while True:
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=300.0,
                    )
                except asyncio.TimeoutError:
                    yield sse("stderr", "Test execution timed out (5 minutes)", done=True)
                    proc.kill()
                    return

                if not line:
                    break

                yield sse("stdout", line.decode("utf-8", errors="replace"))

            await proc.wait()
            exit_code = proc.returncode if proc.returncode is not None else 0
            success = exit_code == 0

            yield f"data: {json.dumps({'type': 'complete', 'exitCode': exit_code, 'success': success, 'data': f'Exit code: {exit_code}', 'done': True})}\n\n"

        except Exception as e:
            logger.error(f"[TESTS] run_tests streaming error: {e}")
            yield sse("error", str(e), done=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
