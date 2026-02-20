#!/usr/bin/env python3
"""
Busibox Host Agent

A lightweight FastAPI service running on the host that allows deploy-api (in Docker)
to control MLX and other host-native services.

This agent:
1. Listens on localhost:8089 (accessible from Docker via host.docker.internal)
2. Exposes endpoints for MLX management
3. Authenticated via shared secret from .env file
4. Whitelists allowed commands for security

Usage:
    python3 host-agent.py
    
Or install as launchd service:
    bash install-host-agent.sh
"""

import asyncio
import logging
import os
import subprocess
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("host-agent")

# Find busibox root (parent of scripts/host-agent)
SCRIPT_DIR = Path(__file__).parent.resolve()
BUSIBOX_ROOT = SCRIPT_DIR.parent.parent.resolve()

# Configuration
HOST = os.getenv("HOST_AGENT_HOST", "127.0.0.1")
PORT = int(os.getenv("HOST_AGENT_PORT", "8089"))
TOKEN = os.getenv("HOST_AGENT_TOKEN", "")

# Load token from .env file if not in environment
def load_token_from_env():
    global TOKEN
    if TOKEN:
        return
    
    # Try to find .env.dev or .env.demo file
    for env_name in ["dev", "demo", "staging", "prod"]:
        env_file = BUSIBOX_ROOT / f".env.{env_name}"
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    if line.startswith("HOST_AGENT_TOKEN="):
                        TOKEN = line.split("=", 1)[1].strip()
                        logger.info(f"Loaded token from {env_file}")
                        return

load_token_from_env()

# ---------------------------------------------------------------------------
# Lifespan — start/stop the background MLX health-check loop
# ---------------------------------------------------------------------------
_healthcheck_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _healthcheck_task
    _healthcheck_task = asyncio.create_task(_mlx_healthcheck_loop())
    yield
    _healthcheck_task.cancel()
    try:
        await _healthcheck_task
    except asyncio.CancelledError:
        pass


# FastAPI app
app = FastAPI(
    title="Busibox Host Agent",
    description="Host-native service control for MLX and other services",
    version="1.0.0",
    lifespan=lifespan,
)

# Models
class StartMLXRequest(BaseModel):
    model_type: str = "agent"  # fast, agent, frontier, or specific model name


class DownloadModelRequest(BaseModel):
    model: str  # Full model name like "mlx-community/Qwen2.5-7B-Instruct-4bit"


class MLXStatusResponse(BaseModel):
    running: bool
    pid: Optional[int] = None
    port: Optional[int] = None
    model: Optional[str] = None
    healthy: bool = False


# Authentication
async def verify_token(authorization: str = Header(None)):
    """Verify the authorization token."""
    if not TOKEN:
        # No token configured - allow all requests (dev mode)
        logger.warning("No HOST_AGENT_TOKEN configured - running in dev mode (no auth)")
        return True
    
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    # Support both "Bearer <token>" and raw token
    token = authorization
    if authorization.startswith("Bearer "):
        token = authorization[7:]
    
    if token != TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")
    
    return True


# MLX helper functions
MLX_PID_FILE = Path("/tmp/mlx-lm-server.pid")
MLX_LOG_FILE = Path("/tmp/mlx-lm-server.log")
MLX_PORT = int(os.getenv("MLX_PORT", "8080"))


def get_mlx_status() -> MLXStatusResponse:
    """Check if MLX server is running."""
    if not MLX_PID_FILE.exists():
        return MLXStatusResponse(running=False)
    
    try:
        pid = int(MLX_PID_FILE.read_text().strip())
        # Check if process is running
        os.kill(pid, 0)  # Raises OSError if not running
        
        # Check if responding to HTTP
        import httpx
        try:
            response = httpx.get(f"http://localhost:{MLX_PORT}/v1/models", timeout=2.0)
            healthy = response.status_code == 200
            # Try to extract model name from response
            model = None
            if healthy:
                try:
                    data = response.json()
                    if "data" in data and len(data["data"]) > 0:
                        model = data["data"][0].get("id")
                except:
                    pass
            return MLXStatusResponse(
                running=True,
                pid=pid,
                port=MLX_PORT,
                model=model,
                healthy=healthy,
            )
        except:
            return MLXStatusResponse(running=True, pid=pid, port=MLX_PORT, healthy=False)
    except (OSError, ValueError):
        # Process not running or invalid PID
        MLX_PID_FILE.unlink(missing_ok=True)
        return MLXStatusResponse(running=False)


def stop_mlx_server():
    """Stop the MLX server."""
    status = get_mlx_status()
    if not status.running:
        return {"success": True, "message": "Server not running"}
    
    try:
        os.kill(status.pid, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(status.pid, 0)
            except OSError:
                break
        else:
            try:
                os.kill(status.pid, signal.SIGKILL)
            except OSError:
                pass
        
        MLX_PID_FILE.unlink(missing_ok=True)
        return {"success": True, "message": "Server stopped"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# Routes
@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "host-agent"}


@app.get("/mlx/status")
async def mlx_status(_: bool = Depends(verify_token)):
    """Get MLX server status."""
    return get_mlx_status()


@app.post("/mlx/start")
async def mlx_start(
    request: StartMLXRequest,
    _: bool = Depends(verify_token)
):
    """
    Start the MLX server with the specified model type.
    
    Returns a streaming response with startup logs.
    """
    # Check if already running
    status = get_mlx_status()
    if status.running and status.healthy:
        return {"success": True, "message": "MLX server already running", "status": status}
    
    # Stop if running but unhealthy
    if status.running:
        stop_mlx_server()
    
    # Build command
    mlx_script = BUSIBOX_ROOT / "scripts" / "llm" / "start-mlx-server.sh"
    if not mlx_script.exists():
        raise HTTPException(status_code=500, detail=f"MLX start script not found: {mlx_script}")
    
    async def generate():
        """Stream startup output."""
        import json
        
        yield f"data: {json.dumps({'type': 'info', 'message': f'Starting MLX server with {request.model_type} model...'})}\n\n"
        
        # Run the start script
        process = await asyncio.create_subprocess_exec(
            "bash", str(mlx_script), request.model_type,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BUSIBOX_ROOT),
        )
        
        # Stream output from both stdout and stderr concurrently.
        # Reading them sequentially can deadlock: if stderr's pipe buffer fills
        # while we're still draining stdout, the subprocess blocks on stderr writes
        # and stops producing stdout, creating a deadlock.
        output_queue: asyncio.Queue = asyncio.Queue()
        
        async def drain_stream(stream, stream_type):
            """Read all lines from a stream and put them on the shared queue."""
            while True:
                line = await stream.readline()
                if not line:
                    break
                message = line.decode('utf-8', errors='replace').rstrip()
                if message:
                    await output_queue.put(
                        f"data: {json.dumps({'type': 'log', 'stream': stream_type, 'message': message})}\n\n"
                    )
            # Signal this stream is done
            await output_queue.put(None)
        
        # Start both drainers concurrently
        stdout_task = asyncio.create_task(drain_stream(process.stdout, "stdout"))
        stderr_task = asyncio.create_task(drain_stream(process.stderr, "stderr"))
        
        # Yield messages as they arrive from either stream
        streams_done = 0
        while streams_done < 2:
            msg = await output_queue.get()
            if msg is None:
                streams_done += 1
            else:
                yield msg
        
        # Ensure both tasks are finished
        await stdout_task
        await stderr_task
        
        returncode = await process.wait()
        
        if returncode == 0:
            # Wait for server to be healthy
            for i in range(30):
                status = get_mlx_status()
                if status.healthy:
                    yield f"data: {json.dumps({'type': 'success', 'message': 'MLX server started and healthy', 'done': True})}\n\n"
                    return
                await asyncio.sleep(1)
                if i % 5 == 0:
                    yield f"data: {json.dumps({'type': 'info', 'message': 'Waiting for server to be healthy...'})}\n\n"
            
            yield f"data: {json.dumps({'type': 'warning', 'message': 'Server started but health check timed out', 'done': True})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'message': f'MLX start failed with code {returncode}', 'done': True})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/mlx/stop")
async def mlx_stop(_: bool = Depends(verify_token)):
    """Stop the MLX server."""
    return stop_mlx_server()


@app.get("/mlx/models")
async def mlx_list_models(_: bool = Depends(verify_token)):
    """List available/cached models."""
    import yaml
    
    # Read tier models from config
    models_config = BUSIBOX_ROOT / "config" / "demo-models.yaml"
    if not models_config.exists():
        raise HTTPException(status_code=500, detail="Models config not found")
    
    with open(models_config) as f:
        config = yaml.safe_load(f)
    
    # Get cached models from HuggingFace cache
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    cached_models = set()
    
    if cache_dir.exists():
        for model_dir in cache_dir.iterdir():
            if model_dir.name.startswith("models--"):
                model_name = model_dir.name[8:].replace("--", "/")
                cached_models.add(model_name)
    
    # Build response
    tiers = []
    for tier_name, tier_config in config.get("tiers", {}).items():
        mlx_models = tier_config.get("mlx", {})
        tier_info = {
            "name": tier_name,
            "description": tier_config.get("description", ""),
            "min_ram_gb": tier_config.get("min_ram_gb", 0),
            "models": {}
        }
        for role, model in mlx_models.items():
            tier_info["models"][role] = {
                "name": model,
                "cached": model in cached_models,
            }
        tiers.append(tier_info)
    
    return {
        "tiers": tiers,
        "cached_count": len(cached_models),
    }


@app.post("/mlx/models/download")
async def mlx_download_model(
    request: DownloadModelRequest,
    _: bool = Depends(verify_token)
):
    """
    Download a model from HuggingFace.
    
    Returns a streaming response with download progress.
    """
    import json
    
    async def generate():
        yield f"data: {json.dumps({'type': 'info', 'message': f'Downloading model: {request.model}'})}\n\n"
        
        # Run huggingface_hub download
        process = await asyncio.create_subprocess_exec(
            "python3", "-c", f"""
from huggingface_hub import snapshot_download
import sys

model = '{request.model}'
print(f'Starting download of {{model}}...', flush=True)
try:
    path = snapshot_download(model, local_dir_use_symlinks=True)
    print(f'Downloaded to: {{path}}', flush=True)
    print('SUCCESS', flush=True)
except Exception as e:
    print(f'ERROR: {{e}}', flush=True)
    sys.exit(1)
""",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        async def read_stream(stream, stream_type):
            while True:
                line = await stream.readline()
                if not line:
                    break
                message = line.decode('utf-8', errors='replace').rstrip()
                if message:
                    if message == "SUCCESS":
                        yield f"data: {json.dumps({'type': 'success', 'message': f'Model {request.model} downloaded', 'done': True})}\n\n"
                    elif message.startswith("ERROR:"):
                        yield f"data: {json.dumps({'type': 'error', 'message': message, 'done': True})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'log', 'stream': stream_type, 'message': message})}\n\n"
        
        async for msg in read_stream(process.stdout, "stdout"):
            yield msg
        async for msg in read_stream(process.stderr, "stderr"):
            yield msg
        
        returncode = await process.wait()
        if returncode != 0:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Download failed with code {returncode}', 'done': True})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.get("/models/cached")
async def list_cached_models(_: bool = Depends(verify_token)):
    """List all cached HuggingFace models."""
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    cached_models = []
    
    if cache_dir.exists():
        for model_dir in cache_dir.iterdir():
            if model_dir.name.startswith("models--"):
                model_name = model_dir.name[8:].replace("--", "/")
                # Get size
                total_size = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
                cached_models.append({
                    "name": model_name,
                    "size_bytes": total_size,
                    "size_human": f"{total_size / (1024**3):.1f}GB" if total_size > 1024**3 else f"{total_size / (1024**2):.1f}MB",
                })
    
    return {"models": cached_models}


# ---------------------------------------------------------------------------
# Proactive MLX health check — auto-restarts MLX if it dies or hangs
# ---------------------------------------------------------------------------
MLX_HEALTHCHECK_INTERVAL = int(os.getenv("MLX_HEALTHCHECK_INTERVAL", "30"))
# Run inference probe every N health-check cycles (default: every 4th = ~2min)
MLX_INFERENCE_PROBE_EVERY = int(os.getenv("MLX_INFERENCE_PROBE_EVERY", "4"))
MLX_INFERENCE_PROBE_TIMEOUT = float(os.getenv("MLX_INFERENCE_PROBE_TIMEOUT", "15"))


def _inference_probe() -> bool:
    """
    Send a tiny completion request to MLX and verify it responds.

    This catches the case where /v1/models returns 200 but Metal/GPU
    inference is hung (e.g. after display-off power management changes).
    Uses the smallest available model with max_tokens=1 to be fast.
    """
    import httpx as _httpx
    try:
        resp = _httpx.post(
            f"http://localhost:{MLX_PORT}/v1/chat/completions",
            json={
                "model": "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            },
            timeout=MLX_INFERENCE_PROBE_TIMEOUT,
        )
        if resp.status_code == 200:
            body = resp.json()
            choices = body.get("choices", [])
            if choices and choices[0].get("message", {}).get("content") is not None:
                return True
        logger.warning(f"Inference probe got status {resp.status_code}")
        return False
    except Exception as exc:
        logger.warning(f"Inference probe failed: {exc}")
        return False


async def _mlx_healthcheck_loop():
    """Periodically check MLX and restart it if it's down or inference is hung."""
    await asyncio.sleep(10)
    logger.info(
        f"MLX health-check loop started "
        f"(interval={MLX_HEALTHCHECK_INTERVAL}s, "
        f"inference probe every {MLX_INFERENCE_PROBE_EVERY} cycles)"
    )
    inference_failures = 0
    cycle = 0
    was_running = False
    while True:
        try:
            status = get_mlx_status()

            if status.running and status.healthy:
                was_running = True
                cycle += 1
                if cycle >= MLX_INFERENCE_PROBE_EVERY:
                    cycle = 0
                    logger.info("Running inference probe...")
                    probe_ok = await asyncio.get_event_loop().run_in_executor(
                        None, _inference_probe
                    )
                    logger.info(
                        f"Inference probe result: {'OK' if probe_ok else 'FAILED'}"
                    )
                    if probe_ok:
                        if inference_failures > 0:
                            logger.info("MLX inference recovered")
                        inference_failures = 0
                    else:
                        inference_failures += 1
                        logger.warning(
                            f"MLX models endpoint OK but inference hung "
                            f"({inference_failures}/2 before restart)"
                        )
                        if inference_failures >= 2:
                            logger.error(
                                "MLX inference confirmed hung — restarting"
                            )
                            stop_mlx_server()
                            await _trigger_mlx_start()
                            inference_failures = 0
                            cycle = 0

            elif status.running and not status.healthy:
                was_running = True
                cycle = 0
                logger.info(
                    "MLX process running but /v1/models not responding — "
                    "will retry next cycle"
                )

            else:
                cycle = 0
                should_restart = (
                    MLX_PID_FILE.exists()
                    or was_running
                )
                if should_restart:
                    logger.warning(
                        "MLX not running (was previously started) — "
                        "triggering restart"
                    )
                    was_running = False
                    await _trigger_mlx_start()
                    inference_failures = 0
        except Exception:
            logger.exception("Error in MLX health-check loop")
        await asyncio.sleep(MLX_HEALTHCHECK_INTERVAL)


async def _trigger_mlx_start():
    """Start MLX via the start script (async subprocess)."""
    mlx_script = BUSIBOX_ROOT / "scripts" / "llm" / "start-mlx-server.sh"
    if not mlx_script.exists():
        logger.error(f"MLX start script not found: {mlx_script}")
        return
    try:
        process = await asyncio.create_subprocess_exec(
            "bash", str(mlx_script), "agent",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BUSIBOX_ROOT),
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
        if process.returncode == 0:
            logger.info("MLX auto-restart succeeded")
        else:
            logger.error(
                f"MLX auto-restart failed (code {process.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')[:500]}"
            )
    except asyncio.TimeoutError:
        logger.error("MLX auto-restart timed out after 180s")
    except Exception:
        logger.exception("Failed to auto-restart MLX")


if __name__ == "__main__":
    logger.info(f"Starting Busibox Host Agent on {HOST}:{PORT}")
    logger.info(f"Busibox root: {BUSIBOX_ROOT}")
    
    if not TOKEN:
        logger.warning("No HOST_AGENT_TOKEN configured - running without authentication")
    
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
    )
