"""
Shared fixtures for integration tests.

Worker behavior:
- If LOCAL_WORKER=1 env var is set, the test runner started a worker externally
- Otherwise, tests that need a worker will skip with a helpful message
- Use @pytest.mark.requires_worker to mark tests that need the worker

To run tests with a local worker:
    make test-local SERVICE=ingest WORKER=1 FAST=0
"""
import os
import sys
import subprocess
import time
import signal
from pathlib import Path

import pytest
import redis
from dotenv import load_dotenv

# Load .env.local first (local test overrides), then .env
ingest_dir = Path(__file__).parent.parent.parent
env_local = ingest_dir / ".env.local"
if env_local.exists():
    load_dotenv(env_local)
    print(f"Loaded environment from {env_local}")

busibox_root = ingest_dir.parent.parent
env_file = busibox_root / ".env"
if env_file.exists():
    load_dotenv(env_file, override=False)  # Don't override .env.local
    print(f"Loaded environment from {env_file}")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from src.shared.config import Config


def check_worker_running():
    """Check if a worker is consuming from the Redis stream."""
    try:
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        stream_name = os.getenv("REDIS_STREAM", "jobs:ingestion")
        consumer_group = os.getenv("REDIS_CONSUMER_GROUP", "workers")
        
        r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        
        # Check if consumer group exists and has consumers
        try:
            groups = r.xinfo_groups(stream_name)
            for group in groups:
                if group.get("name") == consumer_group:
                    consumers = group.get("consumers", 0)
                    if consumers > 0:
                        return True
        except redis.ResponseError:
            pass  # Stream or group doesn't exist
        
        return False
    except Exception:
        return False


@pytest.fixture(scope="session")
def config():
    """Load configuration from environment variables as a dictionary."""
    return Config().to_dict()


@pytest.fixture(scope="session")
def test_user_id():
    """Generate a test user ID for integration tests."""
    import uuid
    return str(uuid.uuid4())


@pytest.fixture(scope="session")
def worker_available():
    """
    Check if a worker is available for tests.
    
    Returns True if:
    - LOCAL_WORKER=1 is set (test runner started a worker)
    - A worker is actively consuming from Redis
    """
    # Check if test runner started a worker
    if os.getenv("LOCAL_WORKER", "0") == "1":
        # Wait for worker to be ready
        for _ in range(10):
            if check_worker_running():
                return True
            time.sleep(1)
        print("WARNING: LOCAL_WORKER=1 but no worker found consuming from Redis")
        return False
    
    # Check if an external worker is running
    return check_worker_running()


@pytest.fixture(scope="session")
def require_worker(worker_available):
    """
    Fixture that skips if no worker is available.
    
    Usage:
        def test_something(require_worker, async_client):
            # This test will be skipped if no worker is running
            ...
    """
    if not worker_available:
        pytest.skip(
            "No worker available. Run with WORKER=1:\n"
            "  make test-local SERVICE=ingest WORKER=1 FAST=0"
        )


@pytest.fixture(scope="session")
def worker_process(config, worker_available):
    """
    Legacy fixture for backward compatibility.
    
    If a worker is already running externally, just return None.
    Otherwise, start a worker subprocess.
    """
    if worker_available:
        # Worker already running (started by test runner or external)
        yield None
        return
    
    # No external worker - start one ourselves
    worker_script = Path(__file__).parent.parent.parent / "src" / "worker.py"
    
    if not worker_script.exists():
        pytest.skip(f"Worker script not found at {worker_script}")
    
    print(f"\n{'='*80}")
    print("Starting ingestion worker for tests...")
    print(f"{'='*80}")
    
    # Start worker process
    process = subprocess.Popen(
        [sys.executable, str(worker_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # Line buffered
        env=os.environ.copy(),
    )
    
    # Wait for worker to initialize
    time.sleep(5)
    
    # Check if worker started successfully
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        pytest.fail(f"Worker failed to start:\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
    
    print(f"Worker started (PID: {process.pid})")
    print(f"{'='*80}\n")
    
    yield process
    
    # Cleanup: Stop worker
    print(f"\n{'='*80}")
    print(f"Stopping worker (PID: {process.pid})...")
    print(f"{'='*80}")
    
    try:
        # Send SIGTERM for graceful shutdown
        process.send_signal(signal.SIGTERM)
        
        # Wait up to 10 seconds for graceful shutdown
        try:
            process.wait(timeout=10)
            print("Worker stopped gracefully")
        except subprocess.TimeoutExpired:
            print("Worker did not stop gracefully, forcing...")
            process.kill()
            process.wait()
            print("Worker killed")
    except Exception as e:
        print(f"Error stopping worker: {e}")
        try:
            process.kill()
        except:
            pass
    
    print(f"{'='*80}\n")


@pytest.fixture(autouse=True)
async def cleanup_test_data(config, test_user_id):
    """Clean up test data after each test."""
    yield
    # Cleanup will be handled by individual tests
    pass

