#!/usr/bin/env bash
# =============================================================================
# Busibox GPU Burst Window Manager
# =============================================================================
#
# Execution Context: Admin workstation
# Purpose: Provision a GPU node, deploy vLLM, run time-sensitive AI tasks,
#          then deprovision to minimize costs.
#
# Architecture:
#   1. Terraform provisions a GPU node pool on Rackspace Spot
#   2. vLLM deployment scaled to 1 replica (schedules on GPU node)
#   3. LiteLLM automatically routes "gpu-agent" model to local vLLM
#   4. Tasks run using agents configured with "gpu-agent" model
#   5. After window expires, vLLM scaled to 0, GPU node deprovisioned
#   6. LiteLLM falls back to cloud providers for "gpu-agent"
#
# Usage:
#   bash scripts/k8s/gpu-burst.sh --up                    # Provision GPU + start vLLM
#   bash scripts/k8s/gpu-burst.sh --down                  # Deprovision GPU
#   bash scripts/k8s/gpu-burst.sh --status                # Show GPU status
#   bash scripts/k8s/gpu-burst.sh --window 60             # 60-minute burst window (auto-down after)
#   bash scripts/k8s/gpu-burst.sh --window 60 --tasks tasks.json  # Run tasks then shutdown
#
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TF_DIR="${REPO_ROOT}/k8s/terraform"
KUBECONFIG_FILE="${KUBECONFIG:-${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml}"
NAMESPACE="busibox"

# Source UI library
if [[ -f "${REPO_ROOT}/scripts/lib/ui.sh" ]]; then
    source "${REPO_ROOT}/scripts/lib/ui.sh"
else
    info() { echo "[INFO] $*"; }
    success() { echo "[OK] $*"; }
    error() { echo "[ERROR] $*" >&2; }
    warn() { echo "[WARN] $*"; }
fi

# Kubectl wrapper
kctl() {
    kubectl --kubeconfig="${KUBECONFIG_FILE}" "$@"
}

# ============================================================================
# GPU Node Provisioning (via Terraform)
# ============================================================================

gpu_provision() {
    info "Provisioning GPU node via Terraform..."

    if [[ ! -f "${TF_DIR}/terraform.tfvars" ]]; then
        error "terraform.tfvars not found. Copy terraform.tfvars.example and configure."
        exit 1
    fi

    cd "$TF_DIR"

    # Initialize if needed
    if [[ ! -d ".terraform" ]]; then
        terraform init
    fi

    # Apply with GPU enabled
    terraform apply -var="gpu_enabled=true" -auto-approve

    local status
    status=$(terraform output -raw gpu_nodepool_status 2>/dev/null || echo "unknown")
    info "GPU nodepool bid status: ${status}"

    # Wait for node to appear in cluster
    info "Waiting for GPU node to join cluster..."
    local max_wait=300  # 5 minutes
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local gpu_nodes
        gpu_nodes=$(kctl get nodes -l "busibox/role=gpu-burst" --no-headers 2>/dev/null | wc -l | tr -d ' ')
        if [[ "$gpu_nodes" -gt 0 ]]; then
            success "GPU node joined the cluster!"
            kctl get nodes -l "busibox/role=gpu-burst"
            return 0
        fi
        echo "  Waiting... (${waited}s / ${max_wait}s)"
        sleep 15
        waited=$((waited + 15))
    done

    warn "GPU node hasn't appeared yet. It may still be provisioning."
    warn "Check with: make k8s-gpu-status"
    return 1
}

gpu_deprovision() {
    info "Deprovisioning GPU node..."

    # First, scale down vLLM
    vllm_stop

    cd "$TF_DIR"

    if [[ ! -d ".terraform" ]]; then
        terraform init
    fi

    terraform apply -var="gpu_enabled=false" -auto-approve

    success "GPU node deprovisioned. Cost meter stopped."
}

# ============================================================================
# vLLM Management
# ============================================================================

vllm_start() {
    info "Starting vLLM on GPU node..."

    # Scale vLLM to 1 replica
    kctl scale deployment/vllm -n "${NAMESPACE}" --replicas=1

    # Wait for it to be ready
    info "Waiting for vLLM to load model (this may take 2-5 minutes)..."
    if kctl rollout status deployment/vllm -n "${NAMESPACE}" --timeout=300s 2>/dev/null; then
        success "vLLM is running and ready!"

        # Verify it's serving
        local vllm_pod
        vllm_pod=$(kctl get pods -n "${NAMESPACE}" -l app=vllm -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        if [[ -n "$vllm_pod" ]]; then
            info "vLLM pod: ${vllm_pod}"
            kctl exec -n "${NAMESPACE}" "$vllm_pod" -- curl -s http://localhost:8000/v1/models 2>/dev/null | head -5 || true
        fi
    else
        warn "vLLM deployment timed out. Check pod status:"
        kctl get pods -n "${NAMESPACE}" -l app=vllm
        kctl describe pods -n "${NAMESPACE}" -l app=vllm | tail -20
    fi
}

vllm_stop() {
    info "Stopping vLLM..."
    kctl scale deployment/vllm -n "${NAMESPACE}" --replicas=0 2>/dev/null || true
    success "vLLM scaled to 0"
}

# ============================================================================
# Burst Window
# ============================================================================

run_window() {
    local duration_minutes="${1:-60}"
    local tasks_file="${2:-}"

    info "Starting ${duration_minutes}-minute GPU burst window..."
    echo ""

    # Step 1: Provision GPU
    gpu_provision

    # Step 2: Start vLLM
    vllm_start

    echo ""
    success "GPU burst window is ACTIVE"
    echo ""
    echo "  Model 'gpu-agent' is now routed to local vLLM (GPU-accelerated)"
    echo "  Window duration: ${duration_minutes} minutes"
    echo "  Auto-shutdown at: $(date -v+${duration_minutes}M '+%H:%M:%S' 2>/dev/null || date -d "+${duration_minutes} minutes" '+%H:%M:%S' 2>/dev/null || echo 'unknown')"
    echo ""

    # Step 3: Run tasks if provided
    if [[ -n "$tasks_file" && -f "$tasks_file" ]]; then
        info "Running tasks from ${tasks_file}..."
        # TODO: Implement task runner that triggers agent jobs
        # For now, just wait for the window
        warn "Task runner not yet implemented - waiting for window duration"
    fi

    # Step 4: Wait for window to expire
    info "Window active. Ctrl+C to end early and shutdown."
    trap 'echo ""; warn "Interrupted - shutting down GPU..."; gpu_down; exit 0' INT

    local remaining=$((duration_minutes * 60))
    while [[ $remaining -gt 0 ]]; do
        local mins=$((remaining / 60))
        local secs=$((remaining % 60))
        printf "\r  Time remaining: %02d:%02d  " "$mins" "$secs"
        sleep 10
        remaining=$((remaining - 10))
    done
    echo ""

    # Step 5: Shutdown
    info "Burst window expired. Shutting down GPU..."
    gpu_down
}

# ============================================================================
# Combined Operations
# ============================================================================

gpu_up() {
    gpu_provision
    vllm_start
    echo ""
    success "GPU burst is ACTIVE. Model 'gpu-agent' routes to local vLLM."
    echo "Run 'make k8s-gpu-down' when done to stop costs."
}

gpu_down() {
    vllm_stop
    gpu_deprovision
    echo ""
    success "GPU burst is DOWN. Model 'gpu-agent' falls back to cloud providers."
}

show_status() {
    echo ""
    echo "=== GPU Burst Status ==="
    echo ""

    # Check for GPU nodes
    echo "--- GPU Nodes ---"
    local gpu_nodes
    gpu_nodes=$(kctl get nodes -l "busibox/role=gpu-burst" --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$gpu_nodes" -gt 0 ]]; then
        echo "  GPU nodes: ${gpu_nodes} (ACTIVE)"
        kctl get nodes -l "busibox/role=gpu-burst" 2>/dev/null
    else
        echo "  GPU nodes: 0 (not provisioned)"
    fi
    echo ""

    # Check vLLM
    echo "--- vLLM ---"
    local vllm_replicas
    vllm_replicas=$(kctl get deployment/vllm -n "${NAMESPACE}" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")
    local vllm_ready
    vllm_ready=$(kctl get deployment/vllm -n "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    echo "  Desired replicas: ${vllm_replicas}"
    echo "  Ready replicas: ${vllm_ready:-0}"

    if [[ "${vllm_ready:-0}" -gt 0 ]]; then
        echo "  Status: RUNNING"
        kctl get pods -n "${NAMESPACE}" -l app=vllm 2>/dev/null
    else
        echo "  Status: not running"
    fi
    echo ""

    # LiteLLM model routing
    echo "--- Model Routing ---"
    echo "  'gpu-agent' model:"
    if [[ "${vllm_ready:-0}" -gt 0 ]]; then
        echo "    Primary: vLLM (local GPU, low latency)"
        echo "    Fallback: Cloud provider (if vLLM fails)"
    else
        echo "    Active: Cloud provider (vLLM not running)"
    fi
    echo ""

    # Terraform state
    if [[ -f "${TF_DIR}/terraform.tfstate" ]]; then
        echo "--- Terraform ---"
        cd "$TF_DIR"
        local gpu_status
        gpu_status=$(terraform output -raw gpu_nodepool_status 2>/dev/null || echo "no state")
        echo "  GPU nodepool: ${gpu_status}"
    fi
    echo ""
}

# ============================================================================
# Main
# ============================================================================

case "${1:-}" in
    --up)
        gpu_up
        ;;
    --down)
        gpu_down
        ;;
    --status)
        show_status
        ;;
    --window)
        duration="${2:-60}"
        tasks="${4:-}"  # --tasks file
        if [[ "${3:-}" == "--tasks" ]]; then
            tasks="$4"
        fi
        run_window "$duration" "$tasks"
        ;;
    --provision-only)
        gpu_provision
        ;;
    --deprovision-only)
        gpu_deprovision
        ;;
    --vllm-start)
        vllm_start
        ;;
    --vllm-stop)
        vllm_stop
        ;;
    *)
        echo "Busibox GPU Burst Window Manager"
        echo ""
        echo "Usage: $0 <command>"
        echo ""
        echo "Commands:"
        echo "  --up                        Provision GPU node + start vLLM"
        echo "  --down                      Stop vLLM + deprovision GPU node"
        echo "  --status                    Show GPU burst status"
        echo "  --window MINUTES            Run a timed burst window (auto-shutdown)"
        echo "  --window MINUTES --tasks F  Run tasks during burst window"
        echo ""
        echo "Advanced:"
        echo "  --provision-only            Provision GPU node only (no vLLM)"
        echo "  --deprovision-only          Deprovision GPU node only"
        echo "  --vllm-start                Scale vLLM to 1 (node must exist)"
        echo "  --vllm-stop                 Scale vLLM to 0"
        echo ""
        echo "Cost: GPU node is billed per-second while provisioned."
        echo "      Always run --down when done!"
        ;;
esac
