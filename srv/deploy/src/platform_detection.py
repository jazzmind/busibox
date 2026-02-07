"""
Platform Detection Module

Detects LLM backend capability (MLX for Apple Silicon, vLLM for NVIDIA GPU)
and memory tier for model selection.
"""

import platform
import subprocess
import logging

logger = logging.getLogger(__name__)


def detect_backend() -> str:
    """
    Detect available LLM backend.
    
    Returns:
        "mlx" for Apple Silicon
        "vllm" for NVIDIA GPU
        "cloud" if no local AI hardware
    """
    import os
    
    # First check environment variable (set during install)
    # This is needed because deploy-api runs in Docker and can't detect host hardware
    llm_backend = os.getenv('LLM_BACKEND', '').lower()
    if llm_backend in ('mlx', 'vllm', 'cloud'):
        logger.info(f"Using LLM backend from environment: {llm_backend}")
        return llm_backend
    
    os_name = platform.system()
    arch = platform.machine()
    
    # Check for Apple Silicon (only works when running natively on macOS)
    if os_name == "Darwin" and (arch == "arm64" or arch == "aarch64"):
        logger.info("Detected Apple Silicon - using MLX backend")
        return "mlx"
    
    # Check for NVIDIA GPU
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            gpu_count = len([line for line in result.stdout.strip().split('\n') if line.strip()])
            if gpu_count > 0:
                logger.info(f"Detected NVIDIA GPU ({gpu_count} GPUs) - using vLLM backend")
                return "vllm"
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        # nvidia-smi not available or timed out
        pass
    
    # No local AI hardware available
    logger.info("No local AI hardware detected - using cloud backend")
    return "cloud"


def get_memory_tier(backend: str = None) -> str:
    """
    Get model tier based on available RAM/VRAM.
    
    Args:
        backend: Optional backend type ("mlx", "vllm", or None to auto-detect)
    
    Returns:
        "minimal", "standard", "enhanced", "professional", "enterprise", "ultra", or "cloud"
    """
    if backend is None:
        backend = detect_backend()
    
    if backend == "cloud":
        return "cloud"
    
    ram_gb = 0
    
    if backend == "mlx":
        # Apple Silicon - use unified memory
        try:
            if platform.system() == "Darwin":
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    ram_bytes = int(result.stdout.strip())
                    ram_gb = ram_bytes // (1024 ** 3)
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError, ValueError):
            logger.warning("Failed to detect RAM on macOS")
    
    elif backend == "vllm":
        # NVIDIA - use VRAM (sum of all GPUs)
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                total_vram_mb = sum(
                    int(line.strip()) for line in result.stdout.strip().split('\n') if line.strip().isdigit()
                )
                ram_gb = total_vram_mb // 1024
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError, ValueError):
            logger.warning("Failed to detect VRAM on NVIDIA GPU")
    
    # Determine tier based on RAM/VRAM
    if ram_gb >= 256:
        return "ultra"
    elif ram_gb >= 128:
        return "enterprise"
    elif ram_gb >= 96:
        return "professional"
    elif ram_gb >= 48:
        return "enhanced"
    elif ram_gb >= 24:
        return "standard"
    else:
        return "minimal"


def get_platform_info() -> dict:
    """
    Get complete platform information.
    
    Returns:
        Dictionary with backend, tier, ram_gb, environment, use_production_vllm, 
        and other platform details
    """
    import os
    
    backend = detect_backend()
    tier = get_memory_tier(backend)
    
    # Get environment from config
    environment = os.getenv('BUSIBOX_ENV', os.getenv('ENVIRONMENT', 'production'))
    
    # Get use_production_vllm flag (staging uses production vLLM by default)
    use_production_vllm = os.getenv('USE_PRODUCTION_VLLM', 'false').lower() == 'true'
    
    info = {
        "backend": backend,
        "tier": tier,
        "os": platform.system(),
        "arch": platform.machine(),
        "environment": environment,
        "use_production_vllm": use_production_vllm,
    }
    
    # Add RAM/VRAM info if available
    if backend == "mlx":
        try:
            if platform.system() == "Darwin":
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    ram_bytes = int(result.stdout.strip())
                    info["ram_gb"] = ram_bytes // (1024 ** 3)
        except Exception:
            pass
    elif backend == "vllm":
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                total_vram_mb = sum(
                    int(line.strip()) for line in result.stdout.strip().split('\n') if line.strip().isdigit()
                )
                info["vram_gb"] = total_vram_mb // 1024
        except Exception:
            pass
    
    return info
