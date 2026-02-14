#!/usr/bin/env bash
# =============================================================================
# Rackspace Spot Node Manager
# =============================================================================
#
# Execution Context: Admin workstation
# Purpose: Monitor Spot market prices, swap node classes, adjust bid prices.
#          Persistent volumes (db-ssd, objects-store) survive node swaps.
#
# Usage:
#   bash scripts/k8s/spot-manager.sh check                     # Check market prices
#   bash scripts/k8s/spot-manager.sh swap --class mh.vs1.large-iad  # Swap node class
#   bash scripts/k8s/spot-manager.sh price --bid 0.04          # Adjust bid price
#
# Prerequisites:
#   - SPOT_TOKEN env var or token in k8s/terraform/terraform.tfvars
#   - kubectl configured (for swap operations)
#   - jq installed
#
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source UI library
if [[ -f "${REPO_ROOT}/scripts/lib/ui.sh" ]]; then
    source "${REPO_ROOT}/scripts/lib/ui.sh"
else
    info() { echo "[INFO] $*"; }
    success() { echo "[OK] $*"; }
    error() { echo "[ERROR] $*" >&2; }
    warn() { echo "[WARN] $*"; }
fi

# Source profiles library
if [[ -f "${REPO_ROOT}/scripts/lib/profiles.sh" ]]; then
    source "${REPO_ROOT}/scripts/lib/profiles.sh"
    profile_init 2>/dev/null || true
fi

# ============================================================================
# Configuration
# ============================================================================

SPOT_API_BASE="https://spot.rackspace.com/apis/ngpc.rxt.io/v1"
SPOT_AUTH_URL="https://spot.rackspace.com/identity/v1/oauth2/token"
NAMESPACE="busibox"

# Minimum requirements for Busibox workload
MIN_CPU="${MIN_CPU:-4}"
MIN_RAM_GB="${MIN_RAM_GB:-32}"
PREFERRED_REGION="${PREFERRED_REGION:-us-east-iad}"
MAX_BID_PRICE="${MAX_BID_PRICE:-0.10}"

# Kubeconfig
_k8s_kubeconfig=""
if type profile_get_active &>/dev/null; then
    _k8s_active=$(profile_get_active 2>/dev/null)
    if [[ -n "$_k8s_active" ]]; then
        _k8s_kubeconfig=$(profile_get_kubeconfig "$_k8s_active" 2>/dev/null)
    fi
fi
KUBECONFIG_FILE="${KUBECONFIG:-${_k8s_kubeconfig:-${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml}}"

# ============================================================================
# Helpers
# ============================================================================

kctl() {
    kubectl --kubeconfig="${KUBECONFIG_FILE}" "$@"
}

require_jq() {
    if ! command -v jq &>/dev/null; then
        error "jq is required. Install with: brew install jq (macOS) or apt install jq (Linux)"
        exit 1
    fi
}

# Get Spot API token from environment or terraform.tfvars
get_spot_token() {
    # 1. Environment variable
    if [[ -n "${SPOT_TOKEN:-}" ]]; then
        echo "$SPOT_TOKEN"
        return
    fi

    # 2. Terraform tfvars file
    local tfvars="${REPO_ROOT}/k8s/terraform/terraform.tfvars"
    if [[ -f "$tfvars" ]]; then
        local token
        token=$(grep 'rackspace_spot_token' "$tfvars" 2>/dev/null | sed 's/.*= *"\(.*\)"/\1/' | head -1)
        if [[ -n "$token" && "$token" != "YOUR_TOKEN_HERE" ]]; then
            echo "$token"
            return
        fi
    fi

    # 3. Vault
    if [[ -f "${REPO_ROOT}/scripts/lib/vault.sh" ]]; then
        source "${REPO_ROOT}/scripts/lib/vault.sh"
        if ensure_vault_access 2>/dev/null; then
            local token
            token=$(get_vault_secret "secrets.rackspace_spot_token" 2>/dev/null || echo "")
            if [[ -n "$token" ]]; then
                echo "$token"
                return
            fi
        fi
    fi

    error "No Spot API token found. Set SPOT_TOKEN env var or configure k8s/terraform/terraform.tfvars"
    exit 1
}

# Get cloudspace name from terraform.tfvars or profile
get_cloudspace_name() {
    local tfvars="${REPO_ROOT}/k8s/terraform/terraform.tfvars"
    if [[ -f "$tfvars" ]]; then
        local name
        name=$(grep 'cloudspace_name' "$tfvars" 2>/dev/null | sed 's/.*= *"\(.*\)"/\1/' | head -1)
        if [[ -n "$name" ]]; then
            echo "$name"
            return
        fi
    fi
    echo "sonnenreich-dev"
}

# Get current node pool name from the cloudspace
get_nodepool_name() {
    local token="$1"
    local cloudspace="$2"

    local response
    response=$(curl -s -H "Authorization: Bearer ${token}" \
        "${SPOT_API_BASE}/namespaces/${cloudspace}/spotnodepools" 2>/dev/null || echo "")

    if [[ -z "$response" ]]; then
        error "Failed to query node pools"
        return 1
    fi

    # Get the first (base) node pool name
    echo "$response" | jq -r '.items[0].metadata.name // empty' 2>/dev/null
}

# ============================================================================
# Commands
# ============================================================================

# check - Show market prices and compare with current setup
cmd_check() {
    require_jq
    info "Fetching Rackspace Spot server classes..."
    echo ""

    # Server classes API is unauthenticated
    local classes_json
    classes_json=$(curl -s "${SPOT_API_BASE}/serverclasses" 2>/dev/null || echo "")

    if [[ -z "$classes_json" || "$classes_json" == "null" ]]; then
        error "Failed to fetch server classes from Spot API"
        exit 1
    fi

    # Get current node info from kubectl
    local current_class="unknown"
    local current_cpu="?"
    local current_ram="?"
    if [[ -f "$KUBECONFIG_FILE" ]] && kctl cluster-info &>/dev/null; then
        local node_labels
        node_labels=$(kctl get nodes -o json 2>/dev/null | jq -r '.items[0].metadata.labels // {}' 2>/dev/null)
        current_class=$(echo "$node_labels" | jq -r '.["node.kubernetes.io/instance-type"] // "unknown"' 2>/dev/null)

        # Get actual node capacity
        local node_info
        node_info=$(kctl get nodes -o json 2>/dev/null | jq '.items[0].status.capacity // {}' 2>/dev/null)
        current_cpu=$(echo "$node_info" | jq -r '.cpu // "?"' 2>/dev/null)
        current_ram=$(echo "$node_info" | jq -r '.memory // "?"' 2>/dev/null)
    fi

    echo "Current node: ${current_class} (${current_cpu} CPU, ${current_ram} RAM)"
    echo "Minimum requirements: ${MIN_CPU} CPU, ${MIN_RAM_GB}GB RAM"
    echo "Region filter: ${PREFERRED_REGION}*"
    echo "Max bid price: \$${MAX_BID_PRICE}/hr"
    echo ""

    # Filter and display server classes
    printf "%-35s %6s %8s %12s %10s\n" "SERVER CLASS" "CPU" "RAM" "DISK" "STATUS"
    printf "%-35s %6s %8s %12s %10s\n" "-----------------------------------" "------" "--------" "------------" "----------"

    echo "$classes_json" | jq -r --arg region "$PREFERRED_REGION" --argjson min_cpu "$MIN_CPU" --argjson min_ram "$MIN_RAM_GB" '
        .items // [] | .[] |
        select(.metadata.name | test($region)) |
        select((.spec.cpu // 0) >= $min_cpu) |
        select(((.spec.memory // 0) / 1024) >= $min_ram) |
        [
            .metadata.name,
            (.spec.cpu // 0 | tostring),
            (((.spec.memory // 0) / 1024) | floor | tostring) + "GB",
            (((.spec.disk // 0) / 1024) | floor | tostring) + "GB",
            (.status.availability // "unknown")
        ] | @tsv
    ' 2>/dev/null | while IFS=$'\t' read -r name cpu ram disk avail; do
        local marker=""
        [[ "$name" == "$current_class" ]] && marker=" <-- current"
        printf "%-35s %6s %8s %12s %10s%s\n" "$name" "$cpu" "$ram" "$disk" "$avail" "$marker"
    done

    echo ""

    # Try to get pricing info (may need auth for detailed pricing)
    info "Tip: Check https://spot.rackspace.com for current market prices"
    info "Use 'spot-swap' to change node class, 'spot-price' to adjust bid"
}

# swap - Change to a different server class
cmd_swap() {
    require_jq
    local new_class=""
    local new_bid=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --class) new_class="$2"; shift 2 ;;
            --bid) new_bid="$2"; shift 2 ;;
            *) error "Unknown option: $1"; exit 1 ;;
        esac
    done

    if [[ -z "$new_class" ]]; then
        error "Usage: spot-manager.sh swap --class <server-class> [--bid <price>]"
        exit 1
    fi

    local token cloudspace nodepool_name
    token=$(get_spot_token)
    cloudspace=$(get_cloudspace_name)

    info "Cloudspace: ${cloudspace}"
    info "New server class: ${new_class}"

    # Validate the new class exists and meets requirements
    local class_info
    class_info=$(curl -s "${SPOT_API_BASE}/serverclasses" 2>/dev/null | \
        jq -r --arg name "$new_class" '.items[] | select(.metadata.name == $name)' 2>/dev/null)

    if [[ -z "$class_info" ]]; then
        error "Server class '${new_class}' not found"
        echo "Run 'make spot-check' to see available classes"
        exit 1
    fi

    local class_cpu class_ram
    class_cpu=$(echo "$class_info" | jq -r '.spec.cpu // 0')
    class_ram=$(echo "$class_info" | jq -r '((.spec.memory // 0) / 1024) | floor')

    if [[ "$class_cpu" -lt "$MIN_CPU" ]]; then
        error "Server class has ${class_cpu} CPUs, minimum is ${MIN_CPU}"
        exit 1
    fi
    if [[ "$class_ram" -lt "$MIN_RAM_GB" ]]; then
        error "Server class has ${class_ram}GB RAM, minimum is ${MIN_RAM_GB}GB"
        exit 1
    fi

    info "Class specs: ${class_cpu} CPU, ${class_ram}GB RAM"

    # Get current node pool
    nodepool_name=$(get_nodepool_name "$token" "$cloudspace")
    if [[ -z "$nodepool_name" ]]; then
        error "Could not find node pool in cloudspace '${cloudspace}'"
        exit 1
    fi
    info "Node pool: ${nodepool_name}"

    # Build patch payload
    local patch_body
    if [[ -n "$new_bid" ]]; then
        patch_body=$(jq -n --arg sc "$new_class" --argjson bp "$new_bid" \
            '{"spec": {"serverClass": $sc, "bidPrice": $bp}}')
    else
        patch_body=$(jq -n --arg sc "$new_class" \
            '{"spec": {"serverClass": $sc}}')
    fi

    echo ""
    warn "This will swap the node class for pool '${nodepool_name}'."
    warn "Expect ~5-10 minutes of downtime while the node is replaced."
    warn "Persistent volumes (db-ssd, objects-store) will re-attach automatically."
    echo ""
    read -p "Proceed? (y/N) " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Cancelled."
        return 0
    fi

    # Cordon current node (if kubectl is available)
    if [[ -f "$KUBECONFIG_FILE" ]] && kctl cluster-info &>/dev/null; then
        local node_name
        node_name=$(kctl get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
        if [[ -n "$node_name" ]]; then
            info "Cordoning current node: ${node_name}..."
            kctl cordon "$node_name" 2>/dev/null || warn "Could not cordon node"
        fi
    fi

    # Patch the node pool via Spot API
    info "Updating node pool server class via Spot API..."
    local response
    response=$(curl -s -w "\n%{http_code}" \
        -X PATCH \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/merge-patch+json" \
        -d "$patch_body" \
        "${SPOT_API_BASE}/namespaces/${cloudspace}/spotnodepools/${nodepool_name}" 2>/dev/null)

    local http_code body
    http_code=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)

    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        success "Node pool updated successfully (HTTP ${http_code})"
    else
        error "Failed to update node pool (HTTP ${http_code})"
        echo "$body" | jq . 2>/dev/null || echo "$body"
        exit 1
    fi

    echo ""
    info "Node swap initiated. Monitoring progress..."
    echo ""

    # Monitor the transition
    local max_wait=600  # 10 minutes
    local elapsed=0
    local new_node_ready=false

    while [[ $elapsed -lt $max_wait ]]; do
        if [[ -f "$KUBECONFIG_FILE" ]] && kctl cluster-info &>/dev/null; then
            local ready_nodes
            ready_nodes=$(kctl get nodes --no-headers 2>/dev/null | grep -c " Ready" || echo "0")

            if [[ "$ready_nodes" -gt 0 ]]; then
                local node_type
                node_type=$(kctl get nodes -o jsonpath='{.items[0].metadata.labels.node\.kubernetes\.io/instance-type}' 2>/dev/null || echo "unknown")
                if [[ "$node_type" == "$new_class" ]]; then
                    new_node_ready=true
                    break
                fi
                echo "  Node type: ${node_type} (waiting for ${new_class})..."
            else
                echo "  No ready nodes yet..."
            fi
        else
            echo "  Waiting for cluster to become reachable..."
        fi

        sleep 15
        elapsed=$((elapsed + 15))
        echo "  ... ${elapsed}s / ${max_wait}s"
    done

    if $new_node_ready; then
        success "New node is ready with class: ${new_class}"
        echo ""

        # Wait for pods to reschedule
        info "Waiting for pods to reschedule..."
        sleep 10

        # Check pod status
        kctl get pods -n "${NAMESPACE}" -o wide 2>/dev/null || true
        echo ""

        # Check PVC status
        info "PVC status:"
        kctl get pvc -n "${NAMESPACE}" 2>/dev/null || true
        echo ""

        success "Node swap complete!"
    else
        warn "Node swap is still in progress after ${max_wait}s"
        warn "Check status with: make k8s-status"
    fi
}

# price - Adjust bid price without changing class
cmd_price() {
    require_jq
    local new_bid=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --bid) new_bid="$2"; shift 2 ;;
            *) error "Unknown option: $1"; exit 1 ;;
        esac
    done

    if [[ -z "$new_bid" ]]; then
        error "Usage: spot-manager.sh price --bid <price>"
        echo "Example: spot-manager.sh price --bid 0.04"
        exit 1
    fi

    local token cloudspace nodepool_name
    token=$(get_spot_token)
    cloudspace=$(get_cloudspace_name)

    nodepool_name=$(get_nodepool_name "$token" "$cloudspace")
    if [[ -z "$nodepool_name" ]]; then
        error "Could not find node pool in cloudspace '${cloudspace}'"
        exit 1
    fi

    info "Cloudspace: ${cloudspace}"
    info "Node pool: ${nodepool_name}"
    info "New bid price: \$${new_bid}/hr"
    echo ""

    # Patch bid price
    local patch_body
    patch_body=$(jq -n --argjson bp "$new_bid" '{"spec": {"bidPrice": $bp}}')

    local response
    response=$(curl -s -w "\n%{http_code}" \
        -X PATCH \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/merge-patch+json" \
        -d "$patch_body" \
        "${SPOT_API_BASE}/namespaces/${cloudspace}/spotnodepools/${nodepool_name}" 2>/dev/null)

    local http_code body
    http_code=$(echo "$response" | tail -1)
    body=$(echo "$response" | head -n -1)

    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        success "Bid price updated to \$${new_bid}/hr"
        echo ""
        echo "Note: If the new bid is below market price, the node may be preempted."
        echo "Run 'make spot-check' to see current market conditions."
    else
        error "Failed to update bid price (HTTP ${http_code})"
        echo "$body" | jq . 2>/dev/null || echo "$body"
        exit 1
    fi
}

# ============================================================================
# Main
# ============================================================================

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  check                          Check market prices and available server classes"
    echo "  swap --class <class> [--bid N]  Swap to a different server class"
    echo "  price --bid <price>            Adjust bid price (USD/hr)"
    echo ""
    echo "Examples:"
    echo "  $0 check"
    echo "  $0 swap --class mh.vs1.large-iad"
    echo "  $0 swap --class mh.vs1.large-iad --bid 0.04"
    echo "  $0 price --bid 0.03"
    echo ""
    echo "Environment:"
    echo "  SPOT_TOKEN       Rackspace Spot API token (or set in terraform.tfvars)"
    echo "  MIN_CPU          Minimum CPUs (default: ${MIN_CPU})"
    echo "  MIN_RAM_GB       Minimum RAM in GB (default: ${MIN_RAM_GB})"
    echo "  PREFERRED_REGION Region filter (default: ${PREFERRED_REGION})"
    echo "  MAX_BID_PRICE    Max bid price (default: ${MAX_BID_PRICE})"
    exit 1
fi

COMMAND="$1"
shift

case "$COMMAND" in
    check) cmd_check "$@" ;;
    swap) cmd_swap "$@" ;;
    price) cmd_price "$@" ;;
    *)
        error "Unknown command: ${COMMAND}"
        echo "Run '$0' without arguments for usage."
        exit 1
        ;;
esac
