#!/usr/bin/env bash
#
# Debug GPU Usage in LXC Container
#
# EXECUTION CONTEXT: Proxmox host (as root) OR inside container
# PURPOSE: Diagnose why applications aren't using GPU
#
# USAGE:
#   # From Proxmox host:
#   bash provision/pct/check-gpu-usage.sh <container-id>
#
#   # Or inside container:
#   bash check-gpu-usage.sh
#
# WHAT IT CHECKS:
#   1. GPU devices are visible in container
#   2. NVIDIA driver is loaded and working
#   3. GPU compute mode is correct
#   4. Application can see GPU
#   5. GPU processes are running
#   6. Environment variables are set correctly
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

error() {
    echo -e "${RED}[✗]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

section() {
    echo -e "\n${CYAN}═══════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
}

# Check if running inside container or from host
if [ $# -eq 1 ]; then
    # Running from host
    CONTAINER_ID="$1"
    EXEC_PREFIX="pct exec $CONTAINER_ID --"
    info "Running diagnostics for container $CONTAINER_ID from Proxmox host"
else
    # Running inside container
    EXEC_PREFIX=""
    info "Running diagnostics inside container"
fi

echo ""
section "1. GPU Device Visibility"

# Check if GPU devices exist
if [ -n "$EXEC_PREFIX" ]; then
    # Running from host - need to quote to prevent local expansion
    GPU_DEVICES=$($EXEC_PREFIX bash -c "ls -la /dev/nvidia* 2>/dev/null" || echo "")
    GPU_CHECK=$($EXEC_PREFIX bash -c "test -e /dev/nvidia0 && echo 'exists' || echo 'missing'" 2>/dev/null)
else
    # Running inside container
    GPU_DEVICES=$(ls -la /dev/nvidia* 2>/dev/null || echo "")
    GPU_CHECK=$(test -e /dev/nvidia0 && echo 'exists' || echo 'missing')
fi

echo "$GPU_DEVICES"
echo ""

if [ "$GPU_CHECK" = "exists" ]; then
    success "GPU devices are visible in container"
else
    error "GPU devices NOT visible in container"
    echo ""
    echo "Fix: Configure GPU passthrough first"
    echo "  bash provision/pct/configure-gpu-passthrough.sh <container-id> <gpu-numbers>"
    exit 1
fi

echo ""
section "2. NVIDIA Driver Status"

# Check nvidia-smi works
if $EXEC_PREFIX nvidia-smi &>/dev/null; then
    success "NVIDIA driver is working"
    echo ""
    $EXEC_PREFIX nvidia-smi
else
    error "NVIDIA driver is NOT working"
    echo ""
    echo "Fix: Install NVIDIA drivers"
    echo "  bash provision/pct/install-nvidia-drivers.sh <container-id>"
    exit 1
fi

echo ""
section "3. GPU Compute Mode"

# Check GPU compute mode (should not be "Prohibited")
COMPUTE_MODE=$($EXEC_PREFIX nvidia-smi --query-gpu=compute_mode --format=csv,noheader | head -1)
if [[ "$COMPUTE_MODE" == "Default" ]] || [[ "$COMPUTE_MODE" == "Exclusive_Process" ]]; then
    success "GPU compute mode: $COMPUTE_MODE (OK)"
else
    warn "GPU compute mode: $COMPUTE_MODE"
    if [[ "$COMPUTE_MODE" == "Prohibited" ]]; then
        error "GPU compute is PROHIBITED!"
        echo ""
        echo "Fix: Change compute mode"
        echo "  $EXEC_PREFIX nvidia-smi -c DEFAULT"
    fi
fi

echo ""
section "4. Current GPU Processes"

# Check if any processes are using GPU
GPU_PROCESSES=$($EXEC_PREFIX nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null || echo "")

if [ -n "$GPU_PROCESSES" ]; then
    success "Processes using GPU:"
    echo "$GPU_PROCESSES"
else
    warn "NO processes currently using GPU"
    echo ""
    echo "This could mean:"
    echo "  - Application is not running"
    echo "  - Application is not configured to use GPU"
    echo "  - Application is using CPU fallback"
fi

echo ""
section "5. GPU Utilization"

# Show current GPU utilization
info "Current GPU utilization (5-second snapshot):"
echo ""
for i in {1..5}; do
    UTIL=$($EXEC_PREFIX nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -1)
    TEMP=$($EXEC_PREFIX nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader | head -1)
    POWER=$($EXEC_PREFIX nvidia-smi --query-gpu=power.draw --format=csv,noheader | head -1)
    echo "  Sample $i: GPU Utilization: ${UTIL}%, Temp: ${TEMP}°C, Power: ${POWER}"
    sleep 1
done

echo ""
UTIL=$($EXEC_PREFIX nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -1)
if [ "$UTIL" -gt 5 ]; then
    success "GPU is being used (${UTIL}% utilization)"
else
    warn "GPU utilization is very low (${UTIL}%)"
    echo ""
    echo "Possible causes:"
    echo "  1. Application is using CPU instead"
    echo "  2. No inference/compute task is currently running"
    echo "  3. Application needs configuration to enable GPU"
fi

echo ""
section "6. CUDA Environment Check"

# Check CUDA environment variables
info "Checking CUDA environment variables..."
echo ""

CUDA_VISIBLE_DEVICES=$($EXEC_PREFIX bash -c 'echo ${CUDA_VISIBLE_DEVICES:-not_set}')
if [ "$CUDA_VISIBLE_DEVICES" = "not_set" ]; then
    warn "CUDA_VISIBLE_DEVICES not set (will use all GPUs)"
else
    success "CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
fi

LD_LIBRARY_PATH=$($EXEC_PREFIX bash -c 'echo ${LD_LIBRARY_PATH:-not_set}')
if echo "$LD_LIBRARY_PATH" | grep -q "cuda"; then
    success "LD_LIBRARY_PATH includes CUDA paths"
else
    warn "LD_LIBRARY_PATH doesn't include CUDA paths: $LD_LIBRARY_PATH"
fi

echo ""
section "7. PyTorch CUDA Check (if available)"

# Try to check PyTorch CUDA availability
if $EXEC_PREFIX python3 -c "import torch" 2>/dev/null; then
    info "PyTorch is installed, checking CUDA availability..."
    echo ""
    
    TORCH_CUDA=$($EXEC_PREFIX python3 -c "import torch; print(torch.cuda.is_available())" 2>/dev/null || echo "error")
    if [ "$TORCH_CUDA" = "True" ]; then
        success "PyTorch can see CUDA"
        
        GPU_COUNT=$($EXEC_PREFIX python3 -c "import torch; print(torch.cuda.device_count())" 2>/dev/null)
        success "PyTorch sees $GPU_COUNT GPU(s)"
        
        echo ""
        info "GPU Details:"
        $EXEC_PREFIX python3 -c "import torch; [print(f'  GPU {i}: {torch.cuda.get_device_name(i)}') for i in range(torch.cuda.device_count())]" 2>/dev/null || true
    else
        error "PyTorch CANNOT see CUDA"
        echo ""
        echo "This could mean:"
        echo "  1. CUDA toolkit not installed"
        echo "  2. PyTorch installed without CUDA support"
        echo "  3. Library mismatch"
        echo ""
        echo "Fix: Reinstall PyTorch with CUDA support"
        echo "  pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121"
    fi
else
    warn "PyTorch not installed (skipping PyTorch checks)"
fi

echo ""
section "8. Application-Specific Checks"

# Check for common LLM applications
info "Checking for LLM applications..."
echo ""

# Check Ollama
if $EXEC_PREFIX pgrep -f ollama &>/dev/null; then
    success "Ollama is running"
    
    # Check Ollama environment
    OLLAMA_LOGS=$($EXEC_PREFIX bash -c "journalctl -u ollama --no-pager -n 20 2>/dev/null || echo 'no_logs'" | grep -i "cuda\|gpu\|nvidia" || echo "no_gpu_mentions")
    
    if [ "$OLLAMA_LOGS" != "no_gpu_mentions" ]; then
        success "Ollama logs mention GPU/CUDA"
        echo "$OLLAMA_LOGS"
    else
        warn "Ollama logs don't mention GPU usage"
        echo ""
        echo "Check Ollama is using GPU:"
        echo "  1. Verify model is loaded with GPU support"
        echo "  2. Check: journalctl -u ollama -f"
        echo "  3. Restart Ollama: systemctl restart ollama"
    fi
else
    info "Ollama not running"
fi

# Check for Python processes that might be using ML
PYTHON_PROCS=$($EXEC_PREFIX pgrep -fa python | grep -v "grep\|pgrep" || echo "")
if [ -n "$PYTHON_PROCS" ]; then
    info "Python processes running:"
    echo "$PYTHON_PROCS"
else
    info "No Python processes running"
fi

# Check Open WebUI specifically
if $EXEC_PREFIX pgrep -f "open-webui\|webui" &>/dev/null; then
    success "Open WebUI process detected"
    
    # Check environment variables for Open WebUI
    WEBUI_PID=$($EXEC_PREFIX pgrep -f "open-webui\|webui" | head -1)
    if [ -n "$WEBUI_PID" ]; then
        info "Checking Open WebUI environment (PID: $WEBUI_PID)..."
        WEBUI_ENV=$($EXEC_PREFIX cat /proc/$WEBUI_PID/environ 2>/dev/null | tr '\0' '\n' | grep -i "cuda\|gpu\|device" || echo "no_gpu_env")
        
        if [ "$WEBUI_ENV" != "no_gpu_env" ]; then
            echo "$WEBUI_ENV"
        else
            warn "Open WebUI doesn't have GPU-related environment variables"
        fi
    fi
else
    warn "Open WebUI not detected as running"
fi

echo ""
section "9. Recommendations"

echo ""
info "Based on the diagnostics above, here are recommendations:"
echo ""

# If no GPU processes
if [ -z "$GPU_PROCESSES" ]; then
    echo "📋 No GPU processes detected:"
    echo "   1. Make sure your application is running"
    echo "   2. Check application logs for GPU initialization"
    echo "   3. Verify application is configured to use GPU"
    echo ""
    echo "   For Ollama:"
    echo "     - Check: journalctl -u ollama -f"
    echo "     - Verify model supports GPU"
    echo "     - Set: OLLAMA_NUM_GPU=1 (or number of GPUs)"
    echo ""
    echo "   For Open WebUI + Ollama:"
    echo "     - Ensure Ollama backend is using GPU"
    echo "     - Check Ollama connection in Open WebUI settings"
    echo "     - Verify model is GPU-capable"
    echo ""
fi

# If low GPU utilization
if [ "$UTIL" -lt 5 ]; then
    echo "📊 Low GPU utilization detected:"
    echo "   1. Generate a request/inference to see GPU spike"
    echo "   2. Monitor with: watch -n 1 nvidia-smi"
    echo "   3. Or use interactive: nvtop"
    echo ""
fi

# Common fixes
echo "🔧 Common fixes for GPU not being used:"
echo ""
echo "   1. Restart the application:"
echo "      systemctl restart ollama"
echo "      systemctl restart open-webui"
echo ""
echo "   2. Set explicit GPU environment variables:"
echo "      export CUDA_VISIBLE_DEVICES=0"
echo "      export NVIDIA_VISIBLE_DEVICES=all"
echo ""
echo "   3. For Ollama specifically:"
echo "      # Edit /etc/systemd/system/ollama.service"
echo "      [Service]"
echo "      Environment=\"OLLAMA_NUM_GPU=1\""
echo "      Environment=\"CUDA_VISIBLE_DEVICES=0\""
echo ""
echo "   4. Test GPU with simple PyTorch script:"
echo "      python3 -c 'import torch; x=torch.rand(1000,1000).cuda(); print(x@x)'"
echo ""
echo "   5. Monitor in real-time while making a request:"
echo "      watch -n 0.5 nvidia-smi"
echo ""

section "Diagnostics Complete"
echo ""


