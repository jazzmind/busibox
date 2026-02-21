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
from typing import Optional, Dict, Any, List

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
    model_type: str = "agent"  # fast, agent/default (dual), frontier, dual/all, or specific model name


class DownloadModelRequest(BaseModel):
    model: str  # Full model name like "mlx-community/Qwen2.5-7B-Instruct-4bit"


class MLXStatusResponse(BaseModel):
    running: bool
    pid: Optional[int] = None
    port: Optional[int] = None
    model: Optional[str] = None
    healthy: bool = False


class MediaToggleRequest(BaseModel):
    server: str  # "transcribe", "voice", or "image"


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
MLX_FAST_PID_FILE = Path("/tmp/mlx-lm-fast-server.pid")
MLX_FAST_LOG_FILE = Path("/tmp/mlx-lm-fast-server.log")
MLX_PORT = int(os.getenv("MLX_PORT", "8080"))
MLX_FAST_PORT = int(os.getenv("MLX_FAST_PORT", "18081"))

# Media server config
MEDIA_SCRIPT = BUSIBOX_ROOT / "scripts" / "llm" / "start-mlx-media-servers.sh"
TRANSCRIBE_PORT = int(os.getenv("TRANSCRIBE_PORT", "8081"))
VOICE_PORT = int(os.getenv("VOICE_PORT", "8082"))
IMAGE_PORT = int(os.getenv("IMAGE_PORT", "8083"))
MEDIA_SERVERS = {
    "transcribe": {
        "pid_file": Path("/tmp/mlx-openai-transcribe.pid"),
        "port": TRANSCRIBE_PORT,
        "kind": "on-demand",
        "label": "Transcribe / STT",
        "memory_estimate_mb": 3072,
    },
    "voice": {
        "pid_file": Path("/tmp/mlx-openai-voice.pid"),
        "port": VOICE_PORT,
        "kind": "always-on",
        "label": "Voice / TTS",
        "memory_estimate_mb": 205,
    },
    "image": {
        "pid_file": Path("/tmp/mlx-openai-image.pid"),
        "port": IMAGE_PORT,
        "kind": "on-demand",
        "label": "Image Generation",
        "memory_estimate_mb": 4096,
    },
}


def get_mlx_status_for_target(target: str = "primary") -> MLXStatusResponse:
    """Check if target MLX server is running."""
    pid_file = MLX_FAST_PID_FILE if target == "fast" else MLX_PID_FILE
    port = MLX_FAST_PORT if target == "fast" else MLX_PORT

    if not pid_file.exists():
        return MLXStatusResponse(running=False)
    
    try:
        pid = int(pid_file.read_text().strip())
        # Check if process is running
        os.kill(pid, 0)  # Raises OSError if not running
        
        # Check if responding to HTTP
        import httpx
        try:
            response = httpx.get(f"http://localhost:{port}/v1/models", timeout=2.0)
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
                port=port,
                model=model,
                healthy=healthy,
            )
        except:
            return MLXStatusResponse(running=True, pid=pid, port=port, healthy=False)
    except (OSError, ValueError):
        # Process not running or invalid PID
        pid_file.unlink(missing_ok=True)
        return MLXStatusResponse(running=False)


def get_mlx_status() -> MLXStatusResponse:
    """Backward-compatible primary MLX status helper."""
    return get_mlx_status_for_target("primary")


def get_all_mlx_status() -> Dict[str, Any]:
    """Get status for both primary and fast MLX servers."""
    primary = get_mlx_status_for_target("primary")
    fast = get_mlx_status_for_target("fast")
    return {
        # Backward-compatible top-level fields (primary)
        "running": primary.running,
        "pid": primary.pid,
        "port": primary.port,
        "model": primary.model,
        "healthy": primary.healthy,
        # Expanded dual-server status
        "primary": primary.model_dump(),
        "fast": fast.model_dump(),
        "all_running": primary.running and fast.running,
        "all_healthy": primary.healthy and fast.healthy,
    }


def _get_process_memory_mb(pid: int) -> Optional[float]:
    """Return RSS memory in MB for a given PID, or None on failure."""
    try:
        import psutil
        return psutil.Process(pid).memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    # Fallback: use `ps` on macOS/Linux
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            rss_kb = int(result.stdout.strip())
            return rss_kb / 1024
    except Exception:
        pass
    return None


def get_media_server_status(name: str) -> Dict[str, Any]:
    """Return status dict for a named media server (transcribe, voice, image)."""
    cfg = MEDIA_SERVERS.get(name)
    if not cfg:
        return {"name": name, "running": False, "error": "Unknown server"}

    pid_file: Path = cfg["pid_file"]
    port: int = cfg["port"]

    base = {
        "name": name,
        "label": cfg["label"],
        "kind": cfg["kind"],
        "port": port,
        "running": False,
        "healthy": False,
        "pid": None,
        "model": None,
        "memory_mb": None,
        "memory_estimate_mb": cfg["memory_estimate_mb"],
    }

    if not pid_file.exists():
        return base

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        pid_file.unlink(missing_ok=True)
        return base

    base["running"] = True
    base["pid"] = pid
    base["memory_mb"] = _get_process_memory_mb(pid)

    try:
        import httpx
        resp = httpx.get(f"http://localhost:{port}/v1/models", timeout=2.0)
        if resp.status_code == 200:
            base["healthy"] = True
            data = resp.json()
            models = data.get("data", [])
            if models:
                base["model"] = models[0].get("id")
    except Exception:
        pass

    return base


def get_all_media_status() -> Dict[str, Any]:
    """Return status for all three media servers plus aggregate memory."""
    statuses = {name: get_media_server_status(name) for name in MEDIA_SERVERS}

    mlx_servers = [
        ("primary", MLX_PID_FILE, MLX_PORT),
        ("fast", MLX_FAST_PID_FILE, MLX_FAST_PORT),
    ]
    mlx_memory: List[Dict[str, Any]] = []
    for label, pid_file, port in mlx_servers:
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                mem = _get_process_memory_mb(pid)
                mlx_memory.append({"name": label, "port": port, "memory_mb": mem})
            except (OSError, ValueError):
                pass

    total_media_mb = sum(
        (s.get("memory_mb") or 0) for s in statuses.values()
    )
    total_llm_mb = sum((m.get("memory_mb") or 0) for m in mlx_memory)
    total_mlx_mb = total_media_mb + total_llm_mb

    return {
        "servers": statuses,
        "llm_servers": mlx_memory,
        "total_media_memory_mb": total_media_mb,
        "total_llm_memory_mb": total_llm_mb,
        "total_mlx_memory_mb": total_mlx_mb,
    }


def _get_system_memory() -> Dict[str, Any]:
    """Return total and available system memory in MB."""
    result: Dict[str, Any] = {
        "total_mb": None,
        "available_mb": None,
        "used_mb": None,
    }
    try:
        import psutil
        vm = psutil.virtual_memory()
        result["total_mb"] = vm.total / (1024 * 1024)
        result["available_mb"] = vm.available / (1024 * 1024)
        result["used_mb"] = vm.used / (1024 * 1024)
        return result
    except Exception:
        pass
    # Fallback for macOS using sysctl
    try:
        r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            result["total_mb"] = int(r.stdout.strip()) / (1024 * 1024)
    except Exception:
        pass
    return result


def _stop_mlx_target(target: str) -> Dict[str, Any]:
    status = get_mlx_status_for_target(target)
    if not status.running:
        return {"success": True, "message": f"{target} server not running"}
    
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
        
        if target == "fast":
            MLX_FAST_PID_FILE.unlink(missing_ok=True)
        else:
            MLX_PID_FILE.unlink(missing_ok=True)
        return {"success": True, "message": f"{target} server stopped"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def stop_mlx_server(target: str = "all") -> Dict[str, Any]:
    """Stop primary, fast, or both MLX servers."""
    if target == "primary":
        return _stop_mlx_target("primary")
    if target == "fast":
        return _stop_mlx_target("fast")

    primary_res = _stop_mlx_target("primary")
    fast_res = _stop_mlx_target("fast")
    return {
        "success": bool(primary_res.get("success")) and bool(fast_res.get("success")),
        "primary": primary_res,
        "fast": fast_res,
    }


# Routes
@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "host-agent"}


@app.get("/mlx/status")
async def mlx_status(target: str = "primary", _: bool = Depends(verify_token)):
    """Get MLX server status (primary, fast, or all)."""
    if target == "all":
        return get_all_mlx_status()
    if target == "fast":
        return get_mlx_status_for_target("fast")
    return get_mlx_status_for_target("primary")


@app.post("/mlx/start")
async def mlx_start(
    request: StartMLXRequest,
    _: bool = Depends(verify_token)
):
    """
    Start the MLX server with the specified model type.
    
    Returns a streaming response with startup logs.
    """
    requested_type = (request.model_type or "agent").strip().lower()
    dual_mode = requested_type in {"agent", "default", "dual", "all"}
    status = get_all_mlx_status() if dual_mode else get_mlx_status_for_target("primary")

    if dual_mode:
        if status.get("all_healthy"):
            return {"success": True, "message": "MLX primary+fast servers already running", "status": status}
        if status.get("all_running") and not status.get("all_healthy"):
            stop_mlx_server("all")
    else:
        if status.running and status.healthy:
            return {"success": True, "message": "MLX server already running", "status": status}
        if status.running:
            stop_mlx_server("primary")
    
    # Build command
    mlx_script = BUSIBOX_ROOT / "scripts" / "llm" / "start-mlx-server.sh"
    if not mlx_script.exists():
        raise HTTPException(status_code=500, detail=f"MLX start script not found: {mlx_script}")
    
    async def generate():
        """Stream startup output."""
        import json
        
        start_arg = "--dual" if dual_mode else request.model_type
        mode_label = "primary+fast dual mode" if dual_mode else f"{request.model_type} mode"
        yield f"data: {json.dumps({'type': 'info', 'message': f'Starting MLX server with {mode_label}...'})}\n\n"
        
        # Run the start script
        process = await asyncio.create_subprocess_exec(
            "bash", str(mlx_script), start_arg,
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
                if dual_mode:
                    dual_status = get_all_mlx_status()
                    if dual_status.get("all_healthy"):
                        yield f"data: {json.dumps({'type': 'success', 'message': 'MLX primary+fast servers started and healthy', 'done': True})}\n\n"
                        return
                else:
                    single_status = get_mlx_status_for_target("primary")
                    if single_status.healthy:
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
async def mlx_stop(target: str = "all", _: bool = Depends(verify_token)):
    """Stop primary, fast, or all MLX servers."""
    return stop_mlx_server(target)


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


@app.get("/media/status")
async def media_status(_: bool = Depends(verify_token)):
    """Get status for all MLX media servers (transcribe, voice, image) with memory info."""
    return get_all_media_status()


@app.post("/media/toggle")
async def media_toggle(request: MediaToggleRequest, _: bool = Depends(verify_token)):
    """
    Toggle a media server (start if stopped, stop if running).

    Only 'transcribe' and 'image' can be toggled; 'voice' is always-on.
    Returns the new status of the server.
    """
    server = request.server.lower()
    if server not in MEDIA_SERVERS:
        raise HTTPException(status_code=400, detail=f"Unknown media server: {server}. Valid: transcribe, voice, image")

    if not MEDIA_SCRIPT.exists():
        raise HTTPException(status_code=500, detail=f"Media server script not found: {MEDIA_SCRIPT}")

    action_map = {
        "transcribe": "transcribe",
        "image": "image",
        "voice": "start",  # always-on; hitting toggle just ensures it's running
    }
    action = action_map[server]

    try:
        result = await asyncio.create_subprocess_exec(
            "bash", str(MEDIA_SCRIPT), action,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BUSIBOX_ROOT),
        )
        stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=120)
        rc = result.returncode
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Media server toggle timed out after 120s")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to toggle media server: {e}")

    await asyncio.sleep(1)
    new_status = get_media_server_status(server)

    return {
        "success": rc == 0,
        "server": server,
        "action": action,
        "stdout": stdout.decode("utf-8", errors="replace")[-500:] if stdout else "",
        "stderr": stderr.decode("utf-8", errors="replace")[-500:] if stderr else "",
        "status": new_status,
    }


@app.get("/system/memory")
async def system_memory(_: bool = Depends(verify_token)):
    """Return system memory stats and per-process MLX memory breakdown."""
    sys_mem = _get_system_memory()
    media = get_all_media_status()

    return {
        "system": sys_mem,
        "mlx": {
            "llm_servers": media["llm_servers"],
            "media_servers": list(media["servers"].values()),
            "total_media_memory_mb": media["total_media_memory_mb"],
            "total_llm_memory_mb": media["total_llm_memory_mb"],
            "total_mlx_memory_mb": media["total_mlx_memory_mb"],
        },
    }


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
        models_resp = _httpx.get(
            f"http://localhost:{MLX_PORT}/v1/models",
            timeout=MLX_INFERENCE_PROBE_TIMEOUT,
        )
        models_resp.raise_for_status()
        model_data = models_resp.json().get("data", [])
        model_id = model_data[0].get("id") if model_data else None
        if not model_id:
            logger.warning("Inference probe could not determine loaded model id")
            return False

        resp = _httpx.post(
            f"http://localhost:{MLX_PORT}/v1/chat/completions",
            json={
                "model": model_id,
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
    """Periodically check MLX servers and restart if down or inference is hung."""
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
            status = get_all_mlx_status()
            primary_status = MLXStatusResponse.model_validate(status["primary"])
            fast_status = MLXStatusResponse.model_validate(status["fast"])

            if primary_status.running and primary_status.healthy and fast_status.running and fast_status.healthy:
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
                                "MLX inference confirmed hung — restarting dual servers"
                            )
                            stop_mlx_server("all")
                            await _trigger_mlx_start()
                            inference_failures = 0
                            cycle = 0

            elif primary_status.running and not primary_status.healthy:
                was_running = True
                cycle = 0
                logger.info(
                    "MLX primary process running but /v1/models not responding — "
                    "will retry next cycle"
                )
            elif primary_status.running and primary_status.healthy and not fast_status.running:
                was_running = True
                cycle = 0
                logger.warning("MLX fast server is down while primary is healthy — triggering dual restart")
                stop_mlx_server("all")
                await _trigger_mlx_start()
                inference_failures = 0

            else:
                cycle = 0
                should_restart = (
                    MLX_PID_FILE.exists()
                    or MLX_FAST_PID_FILE.exists()
                    or was_running
                )
                if should_restart:
                    logger.warning(
                        "MLX primary/fast servers not running (was previously started) — "
                        "triggering dual restart"
                    )
                    was_running = False
                    await _trigger_mlx_start()
                    inference_failures = 0
        except Exception:
            logger.exception("Error in MLX health-check loop")
        await asyncio.sleep(MLX_HEALTHCHECK_INTERVAL)


async def _trigger_mlx_start():
    """Start MLX primary+fast servers via the dual start script mode."""
    mlx_script = BUSIBOX_ROOT / "scripts" / "llm" / "start-mlx-server.sh"
    if not mlx_script.exists():
        logger.error(f"MLX start script not found: {mlx_script}")
        return
    try:
        process = await asyncio.create_subprocess_exec(
            "bash", str(mlx_script), "--dual",
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
