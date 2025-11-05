"""
Shared fixtures for integration tests.
"""
import os
import sys
import subprocess
import time
import signal
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env from busibox root directory
busibox_root = Path(__file__).parent.parent.parent.parent.parent
env_file = busibox_root / ".env"
if env_file.exists():
    load_dotenv(env_file)
    print(f"Loaded environment from {env_file}")
else:
    print(f"Warning: .env file not found at {env_file}")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from shared.config import Config


@pytest.fixture(scope="session")
def config():
    """Load configuration from environment variables."""
    return Config()


@pytest.fixture(scope="session")
def test_user_id():
    """Generate a test user ID for integration tests."""
    import uuid
    return str(uuid.uuid4())


@pytest.fixture(scope="session")
def worker_process(config):
    """
    Start the ingestion worker process for the test session.
    
    The worker will process jobs from Redis during tests.
    Automatically stops the worker when tests complete.
    """
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
    time.sleep(3)
    
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

