#!/usr/bin/env bash
#
# Configure GPU Allocation for Busibox Services
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Configure GPU passthrough, drivers, and allocation for ingest and vLLM containers
#
# This script:
# 1. Configures GPU passthrough for ingest and vLLM containers
# 2. Installs NVIDIA drivers (CUDA toolkit) in both containers
# 3. Configures GPU allocation (CUDA_VISIBLE_DEVICES) for services
# 4. Calculates model sizes and validates GPU memory fits
# 5. Configures vLLM model-to-GPU routing
#
# USAGE:
#   bash configure-gpu-allocation.sh [--interactive] [--ingest-gpus=0] [--vllm-gpus=1,2] [--validate-only]
#
# EXAMPLES:
#   # Interactive mode (recommended)
#   bash configure-gpu-allocation.sh --interactive
#
#   # Configure specific GPU allocation
#   bash configure-gpu-allocation.sh --ingest-gpus=0 --vllm-gpus=1,2
#
#   # Validate current configuration only
#   bash configure-gpu-allocation.sh --validate-only
#
# REQUIREMENTS:
#   - NVIDIA drivers installed on Proxmox host
#   - Containers must exist (ingest-lxc, vllm-lxc)
#   - Scripts: configure-gpu-passthrough.sh, install-nvidia-drivers.sh
#
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source container IDs from vars.env
if [ -f "${PCT_DIR}/vars.env" ]; then
    source "${PCT_DIR}/vars.env"
    CT_INGEST="${CT_INGEST:-206}"
    CT_VLLM="${CT_VLLM:-208}"
else
    # Defaults if vars.env not found
    CT_INGEST="206"
    CT_VLLM="208"
fi

# Default GPU allocation
INGEST_GPUS="${INGEST_GPUS:-0}"
VLLM_GPUS="${VLLM_GPUS:-}"
INTERACTIVE=false
VALIDATE_ONLY=false

# Model size database (parameters -> approximate GPU memory in GB)
# Format: model_name:size_gb
declare -A MODEL_SIZES=(
    ["microsoft/Phi-4-multimodal-instruct"]="12"      # 6B params, bfloat16
    ["Qwen/Qwen3-Embedding-8B"]="16"                   # 8B params, bfloat16
    ["Qwen/Qwen3-30B-A3B-Instruct-2507"]="60"         # 30B params, bfloat16
    ["Qwen/Qwen3-VL-8B-Instruct"]="16"                # 8B params, bfloat16
    ["vidore/colpali-v1.3"]="15"                       # 3B base + LoRA, bfloat16
)

# GPU memory sizes (default 24GB for RTX 3090/4090)
declare -A GPU_MEMORY=()

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

section() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
}

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Configure GPU allocation for Busibox services (ingest and vLLM).

OPTIONS:
    --interactive          Interactive mode - prompts for GPU allocation
    --ingest-gpus=GPUS     GPUs for ingest container (e.g., "0" or "0,1")
    --vllm-gpus=GPUS       GPUs for vLLM container (e.g., "1,2" or "1-3")
    --validate-only        Only validate current configuration, don't make changes
    --help                 Show this help message

EXAMPLES:
    # Interactive mode (recommended)
    $0 --interactive

    # Configure specific allocation
    $0 --ingest-gpus=0 --vllm-gpus=1,2

    # Validate only
    $0 --validate-only

GPU ALLOCATION STRATEGY:
    Standard (2+ GPUs):
      - GPU 0: Ingest (Marker + ColPali) - ~18GB total
      - GPU 1+: vLLM (LLM models) - model-dependent

    Minimum (2 GPUs):
      - GPU 0: Ingest (Marker + ColPali)
      - GPU 1: vLLM (single GPU, limited parallelism)

MODEL MEMORY REQUIREMENTS:
    Small models (phi-4, qwen3-embedding): ~12-16GB per GPU
    Medium models (qwen3-30b): ~60GB (requires 2+ GPUs with tensor parallelism)
    Large models (70B+): ~140GB+ (requires 4+ GPUs)

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --interactive)
            INTERACTIVE=true
            shift
            ;;
        --ingest-gpus=*)
            INGEST_GPUS="${1#*=}"
            shift
            ;;
        --vllm-gpus=*)
            VLLM_GPUS="${1#*=}"
            shift
            ;;
        --validate-only)
            VALIDATE_ONLY=true
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Detect available GPUs on host
detect_gpus() {
    if ! command -v nvidia-smi &>/dev/null; then
        error "nvidia-smi not found. Install NVIDIA drivers on Proxmox host first."
        exit 1
    fi
    
    info "Detecting available GPUs..."
    GPU_COUNT=$(nvidia-smi -L | wc -l)
    
    if [ "$GPU_COUNT" -eq 0 ]; then
        error "No GPUs detected on host"
        exit 1
    fi
    
    success "Found $GPU_COUNT GPU(s) on host"
    
    # Get GPU memory sizes
    for i in $(seq 0 $((GPU_COUNT - 1))); do
        MEMORY=$(nvidia-smi -i "$i" --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
        MEMORY_GB=$((MEMORY / 1024))
        GPU_MEMORY["$i"]="$MEMORY_GB"
        info "  GPU $i: ${MEMORY_GB}GB"
    done
    
    echo ""
}

# Calculate model memory requirement
calculate_model_size() {
    local model_name="$1"
    local dtype="${2:-bfloat16}"  # bfloat16, float16, or float32
    
    # Check if model is in database
    if [ -n "${MODEL_SIZES[$model_name]:-}" ]; then
        echo "${MODEL_SIZES[$model_name]}"
        return 0
    fi
    
    # Try to extract parameter count from model name
    # Format: ModelName-XB or ModelName-X.XB
    if [[ "$model_name" =~ ([0-9]+\.?[0-9]*)B ]]; then
        local params="${BASH_REMATCH[1]}"
        local params_num=$(echo "$params" | awk '{print int($1)}')
        
        # Estimate: params * bytes_per_param (bfloat16 = 2 bytes)
        local size_gb=$((params_num * 2))
        echo "$size_gb"
        return 0
    fi
    
    # Default estimate for unknown models
    warn "Unknown model: $model_name, using default estimate"
    echo "20"  # Default 20GB estimate
}

# Check if model fits on GPU(s)
check_model_fits() {
    local model_name="$1"
    local gpu_list="$2"
    local tensor_parallel="${3:-1}"
    
    local model_size=$(calculate_model_size "$model_name")
    info "Model $model_name requires ~${model_size}GB"
    
    # Parse GPU list
    local gpus=()
    if [[ "$gpu_list" =~ ^[0-9]+-[0-9]+$ ]]; then
        # Range format: 1-3
        IFS='-' read -r START END <<< "$gpu_list"
        for ((i=START; i<=END; i++)); do
            gpus+=("$i")
        done
    elif [[ "$gpu_list" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
        # Comma-separated: 1,2,3
        IFS=',' read -ra gpus <<< "$gpu_list"
    else
        error "Invalid GPU list format: $gpu_list"
        return 1
    fi
    
    # Calculate total GPU memory
    local total_memory=0
    for gpu in "${gpus[@]}"; do
        if [ -z "${GPU_MEMORY[$gpu]:-}" ]; then
            error "GPU $gpu not found in detected GPUs"
            return 1
        fi
        total_memory=$((total_memory + ${GPU_MEMORY[$gpu]}))
    done
    
    # Account for tensor parallelism (model is sharded across GPUs)
    local available_memory=$((total_memory * 90 / 100))  # 90% utilization
    
    info "Total GPU memory: ${total_memory}GB (${available_memory}GB usable)"
    info "Tensor parallelism: $tensor_parallel GPU(s)"
    
    if [ "$model_size" -le "$available_memory" ]; then
        success "Model fits on GPU(s) ${gpu_list[*]}"
        return 0
    else
        error "Model ($model_size GB) does not fit on GPU(s) ${gpu_list[*]} (${available_memory}GB available)"
        return 1
    fi
}

# Configure GPU passthrough for container
configure_gpu_passthrough() {
    local container_id="$1"
    local gpu_spec="$2"
    local container_name="$3"
    
    info "Configuring GPU passthrough for $container_name (container $container_id)..."
    
    if [ ! -f "${SCRIPT_DIR}/configure-gpu-passthrough.sh" ]; then
        error "configure-gpu-passthrough.sh not found at ${SCRIPT_DIR}/configure-gpu-passthrough.sh"
        return 1
    fi
    
    # Check if GPU passthrough already configured
    local conf_file="/etc/pve/lxc/${container_id}.conf"
    if grep -q "# GPU Passthrough" "$conf_file" 2>/dev/null; then
        warn "GPU passthrough already configured for container $container_id"
        if [ "$VALIDATE_ONLY" = false ]; then
            read -p "Reconfigure? (y/N): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                info "Skipping GPU passthrough configuration"
                return 0
            fi
            bash "${SCRIPT_DIR}/configure-gpu-passthrough.sh" "$container_id" "$gpu_spec" --force
        fi
    else
        if [ "$VALIDATE_ONLY" = false ]; then
            bash "${SCRIPT_DIR}/configure-gpu-passthrough.sh" "$container_id" "$gpu_spec"
        else
            warn "GPU passthrough not configured (validation only)"
        fi
    fi
}

# Install NVIDIA drivers in container
install_drivers() {
    local container_id="$1"
    local container_name="$2"
    
    info "Installing NVIDIA drivers in $container_name (container $container_id)..."
    
    if [ ! -f "${SCRIPT_DIR}/install-nvidia-drivers.sh" ]; then
        error "install-nvidia-drivers.sh not found at ${SCRIPT_DIR}/install-nvidia-drivers.sh"
        return 1
    fi
    
    # Check if container is running
    if ! pct status "$container_id" | grep -q "running"; then
        warn "Container $container_id is not running, starting it..."
        pct start "$container_id" || {
            error "Failed to start container $container_id"
            return 1
        }
        sleep 5
    fi
    
    # Check if drivers already installed
    if pct exec "$container_id" -- bash -c "command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null" 2>/dev/null; then
        local installed_version=$(pct exec "$container_id" -- nvidia-smi | grep "Driver Version" | awk '{print $3}' | head -1)
        local host_version=$(nvidia-smi | grep "Driver Version" | awk '{print $3}' | head -1)
        
        if [ "$installed_version" = "$host_version" ]; then
            success "NVIDIA drivers already installed (version $installed_version)"
            return 0
        else
            warn "Driver version mismatch: container=$installed_version, host=$host_version"
        fi
    fi
    
    if [ "$VALIDATE_ONLY" = false ]; then
        bash "${SCRIPT_DIR}/install-nvidia-drivers.sh" "$container_id"
    else
        warn "NVIDIA drivers not installed (validation only)"
    fi
}

# Interactive GPU allocation
interactive_allocation() {
    section "Interactive GPU Allocation"
    
    detect_gpus
    
    echo "Current GPU allocation:"
    echo "  Ingest: GPU(s) $INGEST_GPUS"
    echo "  vLLM: GPU(s) ${VLLM_GPUS:-auto (1+)}"
    echo ""
    
    read -p "Configure ingest container GPUs (default: $INGEST_GPUS): " input_ingest
    if [ -n "$input_ingest" ]; then
        INGEST_GPUS="$input_ingest"
    fi
    
    read -p "Configure vLLM container GPUs (default: auto 1+): " input_vllm
    if [ -n "$input_vllm" ]; then
        VLLM_GPUS="$input_vllm"
    fi
    
    # Auto-configure vLLM if not specified
    if [ -z "$VLLM_GPUS" ]; then
        if [ "$GPU_COUNT" -gt 1 ]; then
            if [ "$GPU_COUNT" -eq 2 ]; then
                VLLM_GPUS="1"
            else
                END_GPU=$((GPU_COUNT - 1))
                VLLM_GPUS="1-${END_GPU}"
            fi
        else
            error "Only 1 GPU detected. vLLM needs at least GPU 1 (GPU 0 for ingest)"
            exit 1
        fi
    fi
    
    echo ""
    info "Allocation summary:"
    echo "  Ingest: GPU(s) $INGEST_GPUS"
    echo "  vLLM: GPU(s) $VLLM_GPUS"
    echo ""
    
    read -p "Continue with this allocation? (Y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        info "Cancelled"
        exit 0
    fi
}

# Main execution
main() {
    section "GPU Allocation Configuration"
    
    # Detect GPUs
    detect_gpus
    
    # Interactive mode
    if [ "$INTERACTIVE" = true ]; then
        interactive_allocation
    elif [ -z "$VLLM_GPUS" ]; then
        # Auto-configure vLLM GPUs
        if [ "$GPU_COUNT" -gt 1 ]; then
            if [ "$GPU_COUNT" -eq 2 ]; then
                VLLM_GPUS="1"
            else
                END_GPU=$((GPU_COUNT - 1))
                VLLM_GPUS="1-${END_GPU}"
            fi
            info "Auto-configured vLLM GPUs: $VLLM_GPUS"
        else
            error "Only 1 GPU detected. Specify vLLM GPUs manually or use --interactive"
            exit 1
        fi
    fi
    
    # Validate GPU allocation doesn't overlap
    # Parse GPU lists and check for overlaps
    local ingest_gpus_array=()
    local vllm_gpus_array=()
    
    # Parse ingest GPUs
    if [[ "$INGEST_GPUS" =~ ^[0-9]+-[0-9]+$ ]]; then
        IFS='-' read -r START END <<< "$INGEST_GPUS"
        for ((i=START; i<=END; i++)); do
            ingest_gpus_array+=("$i")
        done
    elif [[ "$INGEST_GPUS" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
        IFS=',' read -ra ingest_gpus_array <<< "$INGEST_GPUS"
    else
        ingest_gpus_array=("$INGEST_GPUS")
    fi
    
    # Parse vLLM GPUs
    if [[ "$VLLM_GPUS" =~ ^[0-9]+-[0-9]+$ ]]; then
        IFS='-' read -r START END <<< "$VLLM_GPUS"
        for ((i=START; i<=END; i++)); do
            vllm_gpus_array+=("$i")
        done
    elif [[ "$VLLM_GPUS" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
        IFS=',' read -ra vllm_gpus_array <<< "$VLLM_GPUS"
    else
        vllm_gpus_array=("$VLLM_GPUS")
    fi
    
    # Check for overlaps
    for ingest_gpu in "${ingest_gpus_array[@]}"; do
        for vllm_gpu in "${vllm_gpus_array[@]}"; do
            if [ "$ingest_gpu" = "$vllm_gpu" ]; then
                error "GPU overlap detected: GPU $ingest_gpu is allocated to both ingest and vLLM"
                exit 1
            fi
        done
    done
    
    success "GPU allocation validated (no overlaps)"
    
    # Configure ingest container
    section "Configuring Ingest Container"
    configure_gpu_passthrough "$CT_INGEST" "$INGEST_GPUS" "ingest-lxc"
    install_drivers "$CT_INGEST" "ingest-lxc"
    
    # Configure vLLM container
    section "Configuring vLLM Container"
    configure_gpu_passthrough "$CT_VLLM" "$VLLM_GPUS" "vllm-lxc"
    install_drivers "$CT_VLLM" "vllm-lxc"
    
    # Model size validation
    section "Validating Model Sizes"
    
    # Check common models
    info "Validating model memory requirements..."
    
    # Small models on GPU 1 (if available)
    if [[ " ${vllm_gpus_array[@]} " =~ " 1 " ]]; then
        check_model_fits "microsoft/Phi-4-multimodal-instruct" "1" "1" || warn "Phi-4 may not fit on GPU 1"
        check_model_fits "Qwen/Qwen3-Embedding-8B" "1" "1" || warn "Qwen3-Embedding may not fit on GPU 1"
    fi
    
    # Large models on multiple GPUs
    if [ ${#vllm_gpus_array[@]} -ge 2 ]; then
        local gpu_list=$(IFS=','; echo "${vllm_gpus_array[*]}")
        check_model_fits "Qwen/Qwen3-30B-A3B-Instruct-2507" "$gpu_list" "${#vllm_gpus_array[@]}" || warn "Qwen3-30B may need more GPUs"
    fi
    
    # Summary
    section "Configuration Summary"
    success "GPU allocation configured:"
    echo "  Ingest container ($CT_INGEST): GPU(s) $INGEST_GPUS"
    echo "  vLLM container ($CT_VLLM): GPU(s) $VLLM_GPUS"
    echo ""
    info "Next steps:"
    echo "  1. Update Ansible variables:"
    echo "     ingest_cuda_visible_devices: \"$INGEST_GPUS\""
    echo "     vllm_cuda_visible_devices: \"$VLLM_GPUS\""
    echo "     colpali_cuda_visible_devices: \"0\"  # Shares GPU 0 with Marker"
    echo ""
    echo "  2. Redeploy services:"
    echo "     cd provision/ansible"
    echo "     ansible-playbook -i inventory/production/hosts.yml site.yml --tags ingest,vllm"
    echo ""
    echo "  3. Verify GPU access:"
    echo "     ssh root@<ingest-ip> nvidia-smi"
    echo "     ssh root@<vllm-ip> nvidia-smi"
}

# Run main function
main

