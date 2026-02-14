#!/usr/bin/env bash
# =============================================================================
# Busibox Kubernetes Deployment (In-Cluster Build Server)
# =============================================================================
#
# Execution Context: Admin workstation
# Purpose: Sync code to in-cluster build server, build images natively on x86,
#          push to in-cluster registry, and deploy to Kubernetes.
#
# Usage:
#   bash scripts/k8s/deploy.sh [--overlay rackspace-spot] [--sync] [--build] [--apply]
#   bash scripts/k8s/deploy.sh --all                              # Sync, build, push, apply
#   bash scripts/k8s/deploy.sh --apply                            # Apply manifests only
#   bash scripts/k8s/deploy.sh --sync --build                     # Sync and build all
#   bash scripts/k8s/deploy.sh --sync --build --service authz-api # Build one service
#   bash scripts/k8s/deploy.sh --status                           # Show deployment status
#   bash scripts/k8s/deploy.sh --delete                           # Delete all resources
#
# Architecture:
#   1. kubectl cp syncs source code to the in-cluster build-server pod
#   2. build-server (DinD) runs docker build natively on x86
#   3. build-server pushes images to in-cluster registry (localhost:30500)
#   4. kubectl apply deploys manifests; pods pull from localhost:30500
#
# Prerequisites:
#   - kubectl configured (KUBECONFIG or --kubeconfig)
#   - Build server + registry pods running in cluster
#
# =============================================================================
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries if available
if [[ -f "${REPO_ROOT}/scripts/lib/ui.sh" ]]; then
    source "${REPO_ROOT}/scripts/lib/ui.sh"
else
    # Minimal UI fallback
    info() { echo "[INFO] $*"; }
    success() { echo "[OK] $*"; }
    error() { echo "[ERROR] $*" >&2; }
    warn() { echo "[WARN] $*"; }
fi

# Source profiles library if available
if [[ -f "${REPO_ROOT}/scripts/lib/profiles.sh" ]]; then
    source "${REPO_ROOT}/scripts/lib/profiles.sh"
    profile_init 2>/dev/null || true
fi

# ============================================================================
# Configuration
# ============================================================================

OVERLAY="${OVERLAY:-rackspace-spot}"
NAMESPACE="busibox"
REGISTRY="localhost:30500"
REGISTRY_INTERNAL="registry.busibox.svc.cluster.local:5000"

# Determine kubeconfig: profile > KUBECONFIG env > fallback
_k8s_kubeconfig=""
if type profile_get_active &>/dev/null; then
    _k8s_active=$(profile_get_active 2>/dev/null)
    if [[ -n "$_k8s_active" ]]; then
        _k8s_kubeconfig=$(profile_get_kubeconfig "$_k8s_active" 2>/dev/null)
    fi
fi
KUBECONFIG_FILE="${KUBECONFIG:-${_k8s_kubeconfig:-${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml}}"
TAG="${TAG:-$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo 'latest')}"
BUILD_SERVER_POD="build-server"

# Services that need image builds (custom Dockerfiles)
# Format: name:dockerfile:context
BUILDABLE_SERVICES=(
    "authz-api:srv/authz/Dockerfile:srv"
    "data-api:srv/data/Dockerfile:srv"
    "search-api:srv/search/Dockerfile:srv"
    "agent-api:srv/agent/Dockerfile:srv"
    "bridge-api:srv/bridge/Dockerfile:srv/bridge"
    "docs-api:srv/docs/Dockerfile:srv"
    "deploy-api:srv/deploy/Dockerfile:srv"
    "embedding-api:srv/embedding/Dockerfile:srv/embedding"
    "milvus-init:provision/docker/milvus-init.Dockerfile:."
)

# Flags
DO_SYNC=false
DO_BUILD=false
DO_APPLY=false
DO_STATUS=false
DO_DELETE=false
DO_SECRETS=false
DO_CLEAN_STORAGE=false
SERVICE_FILTER=""  # Empty = all services, otherwise specific image name

# ============================================================================
# Parse Arguments
# ============================================================================

while [[ $# -gt 0 ]]; do
    case "$1" in
        --overlay) OVERLAY="$2"; shift 2 ;;
        --tag) TAG="$2"; shift 2 ;;
        --kubeconfig) KUBECONFIG_FILE="$2"; shift 2 ;;
        --sync) DO_SYNC=true; shift ;;
        --build) DO_BUILD=true; shift ;;
        --apply) DO_APPLY=true; shift ;;
        --service) SERVICE_FILTER="$2"; shift 2 ;;
        --all) DO_SYNC=true; DO_BUILD=true; DO_APPLY=true; DO_SECRETS=true; shift ;;
        --clean-storage) DO_CLEAN_STORAGE=true; shift ;;
        --status) DO_STATUS=true; shift ;;
        --delete) DO_DELETE=true; shift ;;
        --secrets) DO_SECRETS=true; shift ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --overlay NAME     Kustomize overlay (default: rackspace-spot)"
            echo "  --tag TAG          Image tag (default: git short SHA)"
            echo "  --kubeconfig PATH  Kubeconfig file path"
            echo "  --sync             Sync source code to in-cluster build server"
            echo "  --build            Build images on in-cluster build server"
            echo "  --apply            Apply Kubernetes manifests"
            echo "  --service NAME     Build only a specific service (e.g., authz-api)"
            echo "  --secrets          Generate and apply secrets from vault"
            echo "  --all              Sync, build, generate secrets, and apply"
            echo "  --clean-storage    Delete deployments + PVCs (for resizing/migrating volumes)"
            echo "  --status           Show deployment status"
            echo "  --delete           Delete all busibox resources"
            echo "  -h, --help         Show this help"
            exit 0
            ;;
        *) error "Unknown option: $1"; exit 1 ;;
    esac
done

# If no action specified, show help
if ! $DO_SYNC && ! $DO_BUILD && ! $DO_APPLY && ! $DO_STATUS && ! $DO_DELETE && ! $DO_SECRETS && ! $DO_CLEAN_STORAGE; then
    echo "No action specified. Use --help for usage."
    exit 1
fi

# ============================================================================
# Kubectl wrapper
# ============================================================================

kctl() {
    kubectl --kubeconfig="${KUBECONFIG_FILE}" "$@"
}

# ============================================================================
# Progress-reporting wait helpers
# ============================================================================

# Get a compact status line for a resource's pods
# Usage: pod_status_line "deployment/authz-api"
pod_status_line() {
    local resource="$1"
    local name="${resource##*/}"

    # Use kubectl get with wide output for a quick summary
    local pod_lines
    pod_lines=$(kctl get pods -n "${NAMESPACE}" -l "app=${name}" \
        --no-headers -o wide 2>/dev/null || echo "")

    if [[ -z "$pod_lines" ]]; then
        echo "  ${name}: no pods found"
        return
    fi

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        local pod_name status restarts age
        pod_name=$(echo "$line" | awk '{print $1}')
        status=$(echo "$line" | awk '{print $3}')
        restarts=$(echo "$line" | awk '{print $4}')
        age=$(echo "$line" | awk '{print $5}')

        local detail="${status}"
        [[ "$restarts" != "0" ]] && detail="${detail}, ${restarts} restarts"

        # For non-Running pods, get the reason from container status
        if [[ "$status" != "Running" && "$status" != "Completed" ]]; then
            local reason
            reason=$(kctl get pod "$pod_name" -n "${NAMESPACE}" \
                -o jsonpath='{.status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || echo "")
            [[ -z "$reason" ]] && reason=$(kctl get pod "$pod_name" -n "${NAMESPACE}" \
                -o jsonpath='{.status.initContainerStatuses[0].state.waiting.reason}' 2>/dev/null || echo "")
            [[ -n "$reason" ]] && detail="${detail} (${reason})"
        fi

        echo "  ${name}: ${detail} [${age}]"
    done <<< "$pod_lines"
}

# Wait for a group of resources with periodic progress reporting
# Usage: wait_for_rollout "Group Name" timeout_seconds resource1 resource2 ...
wait_for_rollout() {
    local group_name="$1"
    local timeout="$2"
    shift 2
    local resources=("$@")

    info "Waiting for ${group_name}..."

    local start_time=$SECONDS
    local all_ready=false
    # Track which resources we've already printed as ready
    declare -A already_ready

    while [[ $(( SECONDS - start_time )) -lt $timeout ]]; do
        local pending=()

        for resource in "${resources[@]}"; do
            local name="${resource##*/}"
            # Skip if already reported ready
            [[ -n "${already_ready[$name]:-}" ]] && continue
            # Quick check: is rollout complete?
            if kctl rollout status "$resource" -n "${NAMESPACE}" --timeout=2s &>/dev/null; then
                echo "  ✓ ${name}"
                already_ready[$name]=1
            else
                pending+=("$resource")
            fi
        done

        # If all ready, done
        if [[ ${#pending[@]} -eq 0 ]]; then
            all_ready=true
            break
        fi

        # Print status of pending ones
        for resource in "${pending[@]}"; do
            pod_status_line "$resource"
        done

        # Show recent warning/error events for the namespace
        local elapsed=$(( SECONDS - start_time ))
        if [[ $elapsed -gt 15 ]]; then
            local recent_events
            recent_events=$(kctl get events -n "${NAMESPACE}" \
                --sort-by='.lastTimestamp' \
                --field-selector="type!=Normal" \
                -o custom-columns='REASON:.reason,OBJECT:.involvedObject.name,MESSAGE:.message' \
                --no-headers 2>/dev/null | tail -3)
            if [[ -n "$recent_events" ]]; then
                echo "  Recent warnings:"
                echo "$recent_events" | while IFS= read -r evt; do
                    echo "    $evt"
                done
            fi
        fi

        echo "  ... elapsed ${elapsed}s / ${timeout}s timeout"
        sleep 10
    done

    if $all_ready; then
        success "${group_name} ready"
    else
        warn "${group_name}: some services not ready after ${timeout}s (continuing anyway)"
        for resource in "${resources[@]}"; do
            local name="${resource##*/}"
            [[ -z "${already_ready[$name]:-}" ]] && pod_status_line "$resource"
        done
    fi
    echo ""
}

# ============================================================================
# Build Server Helpers
# ============================================================================

get_build_server_pod() {
    # Get the running build-server pod name
    local pod_name
    pod_name=$(kctl get pods -n "${NAMESPACE}" -l app=build-server -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [[ -z "$pod_name" ]]; then
        error "Build server pod not found. Deploy build infrastructure first:"
        echo "  kctl apply -k k8s/base/build/"
        exit 1
    fi
    echo "$pod_name"
}

wait_for_build_server() {
    info "Waiting for build server to be ready..."
    local pod_name
    pod_name=$(get_build_server_pod)
    
    # Wait for pod to be running
    kctl wait --for=condition=Ready "pod/${pod_name}" -n "${NAMESPACE}" --timeout=120s
    
    # Wait for Docker daemon inside the pod
    local retries=20
    local i=0
    while [[ $i -lt $retries ]]; do
        if kctl exec "${pod_name}" -n "${NAMESPACE}" -- docker info &>/dev/null; then
            success "Build server Docker daemon ready"
            return 0
        fi
        ((i++))
        sleep 3
    done
    
    error "Build server Docker daemon not ready after ${retries} attempts"
    exit 1
}

build_server_exec() {
    # Execute a command on the build server pod
    local pod_name
    pod_name=$(get_build_server_pod)
    kctl exec "${pod_name}" -n "${NAMESPACE}" -- "$@"
}

# ============================================================================
# Sync Functions
# ============================================================================

sync_to_build_server() {
    info "Syncing source code to build server..."
    echo ""
    
    local pod_name
    pod_name=$(get_build_server_pod)
    
    # Create workspace directory on build server
    build_server_exec mkdir -p /workspace/srv /workspace/provision/docker /workspace/k8s
    
    # Determine which directories to sync based on service filter
    local dirs_to_sync=()
    
    if [[ -n "$SERVICE_FILTER" ]]; then
        # Only sync what's needed for this service
        for service_spec in "${BUILDABLE_SERVICES[@]}"; do
            IFS=':' read -r name dockerfile context <<< "$service_spec"
            if [[ "$name" == "$SERVICE_FILTER" ]]; then
                # Add the context directory
                dirs_to_sync+=("$context")
                # Add the Dockerfile directory if different from context
                local df_dir
                df_dir=$(dirname "$dockerfile")
                if [[ "$df_dir" != "$context" ]]; then
                    dirs_to_sync+=("$df_dir")
                fi
                break
            fi
        done
    else
        # Sync everything needed for all builds
        dirs_to_sync=("srv" "provision/docker")
    fi
    
    # Sync each directory using tar + pipe for efficiency
    for dir in "${dirs_to_sync[@]}"; do
        local src_path="${REPO_ROOT}/${dir}"
        if [[ ! -d "$src_path" ]]; then
            warn "Directory not found: ${src_path} - skipping"
            continue
        fi
        
        info "  Syncing ${dir}/ ..."
        
        # Use tar pipe to copy files efficiently
        # This is faster than kubectl cp for large directories as it avoids
        # per-file overhead and handles deletions properly
        tar -C "${REPO_ROOT}" -cf - "${dir}" | \
            kctl exec -i "${pod_name}" -n "${NAMESPACE}" -- \
            tar -C /workspace -xf -
    done
    
    echo ""
    success "Source code synced to build server"
}

# ============================================================================
# Build Functions
# ============================================================================

build_images_remote() {
    info "Building images on build server (tag: ${TAG})..."
    echo ""
    
    wait_for_build_server
    
    local built=0
    for service_spec in "${BUILDABLE_SERVICES[@]}"; do
        IFS=':' read -r name dockerfile context <<< "$service_spec"
        
        # Skip if --service filter is set and doesn't match
        if [[ -n "$SERVICE_FILTER" && "$name" != "$SERVICE_FILTER" ]]; then
            continue
        fi
        
        local image_tag="${REGISTRY}/${name}:${TAG}"
        local image_latest="${REGISTRY}/${name}:latest"
        
        info "Building ${name}..."
        
        # Build on the build-server pod using the internal registry address for push
        # But tag with localhost:30500 so K8s nodes can pull via NodePort
        build_server_exec docker build \
            -t "${image_tag}" \
            -t "${image_latest}" \
            -f "/workspace/${dockerfile}" \
            "/workspace/${context}"
        
        success "  Built ${name} (${image_tag})"
        ((built++))
    done
    
    if [[ $built -eq 0 && -n "$SERVICE_FILTER" ]]; then
        warn "No buildable service found matching '${SERVICE_FILTER}'"
    else
        echo ""
        success "Built ${built} image(s) on build server"
    fi
}

push_images_remote() {
    info "Pushing images to in-cluster registry..."
    echo ""
    
    local pushed=0
    for service_spec in "${BUILDABLE_SERVICES[@]}"; do
        IFS=':' read -r name _ _ <<< "$service_spec"
        
        # Skip if --service filter is set and doesn't match
        if [[ -n "$SERVICE_FILTER" && "$name" != "$SERVICE_FILTER" ]]; then
            continue
        fi
        
        info "  Pushing ${name}..."
        build_server_exec docker push "${REGISTRY}/${name}:${TAG}"
        build_server_exec docker push "${REGISTRY}/${name}:latest"
        ((pushed++))
    done
    
    echo ""
    success "Pushed ${pushed} image(s) to in-cluster registry"
}

# ============================================================================
# Secrets
# ============================================================================

generate_secrets() {
    info "Generating Kubernetes secrets from vault..."

    local secrets_file="${REPO_ROOT}/k8s/secrets/secrets.yaml"

    # Try to get secrets from vault
    local postgres_password="devpassword"
    local minio_access_key="minioadmin"
    local minio_secret_key="minioadmin"
    local authz_master_key="local-master-key-change-in-production"
    local litellm_api_key=""
    local litellm_master_key=""
    local admin_emails=""
    local openai_api_key=""
    local anthropic_api_key=""
    local bedrock_api_key=""
    local aws_region_name="us-east-1"
    local github_token="${GITHUB_TOKEN:-}"

    if [[ -f "${REPO_ROOT}/scripts/lib/vault.sh" ]]; then
        source "${REPO_ROOT}/scripts/lib/vault.sh"
        # Determine vault environment from active profile or fallback to state files.
        local vault_env="prod"
        if type profile_get_active &>/dev/null; then
            local _active
            _active=$(profile_get_active 2>/dev/null)
            if [[ -n "$_active" ]]; then
                vault_env=$(profile_get_vault_prefix "$_active" 2>/dev/null)
                vault_env="${vault_env:-prod}"
            fi
        fi
        
        # Fallback: scan legacy state files if profile didn't provide vault env
        if [[ "$vault_env" == "prod" ]] && ! type profile_get_active &>/dev/null; then
            for state_prefix in prod staging dev demo; do
                local state_file="${REPO_ROOT}/.busibox-state-${state_prefix}"
                if [[ -f "$state_file" ]]; then
                    local state_backend
                    state_backend=$(grep "^BACKEND_.*=k8s" "$state_file" 2>/dev/null | head -1)
                    if [[ -n "$state_backend" ]]; then
                        vault_env="$state_prefix"
                        break
                    fi
                fi
            done
        fi
        info "Using vault environment: ${vault_env}"
        set_vault_environment "$vault_env" 2>/dev/null || true
        if ensure_vault_access 2>/dev/null; then
            postgres_password=$(get_vault_secret "secrets.postgresql.password" 2>/dev/null || echo "$postgres_password")
            minio_access_key=$(get_vault_secret "secrets.minio.root_user" 2>/dev/null || echo "$minio_access_key")
            minio_secret_key=$(get_vault_secret "secrets.minio.root_password" 2>/dev/null || echo "$minio_secret_key")
            authz_master_key=$(get_vault_secret "secrets.authz_master_key" 2>/dev/null || echo "$authz_master_key")
            litellm_api_key=$(get_vault_secret "secrets.litellm_api_key" 2>/dev/null || echo "$litellm_api_key")
            litellm_master_key=$(get_vault_secret "secrets.litellm_master_key" 2>/dev/null || echo "${litellm_api_key}")
            admin_emails=$(get_vault_secret "secrets.admin_emails" 2>/dev/null || echo "$admin_emails")
            openai_api_key=$(get_vault_secret "secrets.openai_api_key" 2>/dev/null || echo "$openai_api_key")
            anthropic_api_key=$(get_vault_secret "secrets.anthropic_api_key" 2>/dev/null || echo "$anthropic_api_key")
            bedrock_api_key=$(get_vault_secret "secrets.bedrock.api_key" 2>/dev/null || echo "$bedrock_api_key")
            aws_region_name=$(get_vault_secret "secrets.bedrock.region" 2>/dev/null || echo "$aws_region_name")
            [[ -z "$github_token" ]] && github_token=$(get_vault_secret "secrets.github.personal_access_token" 2>/dev/null || echo "")
            info "Secrets loaded from vault"
        else
            warn "Could not access vault - using defaults"
        fi
    fi

    cat > "$secrets_file" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: busibox-secrets
  namespace: busibox
  labels:
    app.kubernetes.io/part-of: busibox
type: Opaque
stringData:
  POSTGRES_PASSWORD: "${postgres_password}"
  MINIO_ACCESS_KEY: "${minio_access_key}"
  MINIO_SECRET_KEY: "${minio_secret_key}"
  AUTHZ_MASTER_KEY: "${authz_master_key}"
  LITELLM_API_KEY: "${litellm_api_key}"
  LITELLM_MASTER_KEY: "${litellm_master_key}"
  ADMIN_EMAILS: "${admin_emails}"
  OPENAI_API_KEY: "${openai_api_key}"
  ANTHROPIC_API_KEY: "${anthropic_api_key}"
  BEDROCK_API_KEY: "${bedrock_api_key}"
  AWS_REGION_NAME: "${aws_region_name}"
  GITHUB_TOKEN: "${github_token}"
EOF

    success "Secrets written to ${secrets_file}"

    # Ensure namespace exists before applying secrets
    info "Ensuring namespace '${NAMESPACE}' exists..."
    kctl apply -f "${REPO_ROOT}/k8s/base/namespace.yaml"

    # Apply secrets
    info "Applying secrets to cluster..."
    kctl apply -f "${secrets_file}"
    success "Secrets applied to cluster"
}

# ============================================================================
# Apply Manifests
# ============================================================================

apply_manifests() {
    info "Applying Kubernetes manifests (overlay: ${OVERLAY}, tag: ${TAG})..."
    echo ""

    local overlay_dir="${REPO_ROOT}/k8s/overlays/${OVERLAY}"

    if [[ ! -d "$overlay_dir" ]]; then
        error "Overlay directory not found: ${overlay_dir}"
        exit 1
    fi

    # Ensure namespace exists
    info "Creating namespace..."
    kctl apply -f "${REPO_ROOT}/k8s/base/namespace.yaml"

    # Generate and apply secrets if not already done
    if ! kctl get secret busibox-secrets -n "${NAMESPACE}" &>/dev/null; then
        warn "Secret 'busibox-secrets' not found - generating from vault..."
        generate_secrets
    fi

    # Apply with kustomize
    info "Applying kustomized manifests..."
    kctl apply -k "$overlay_dir"

    echo ""

    # ---- Wait with progress reporting ----
    wait_for_rollout "Build Infrastructure" 120 \
        "deployment/registry" \
        "deployment/build-server"

    wait_for_rollout "Infrastructure Services" 180 \
        "deployment/postgres" \
        "deployment/redis" \
        "deployment/minio"

    # Run init jobs
    info "Running init jobs..."
    # Delete old jobs if they exist (jobs are immutable)
    kctl delete job minio-init -n "${NAMESPACE}" --ignore-not-found 2>/dev/null || true
    kctl delete job milvus-init -n "${NAMESPACE}" --ignore-not-found 2>/dev/null || true
    # Re-apply to recreate jobs
    kctl apply -k "$overlay_dir"

    wait_for_rollout "Milvus" 180 \
        "deployment/etcd" \
        "deployment/milvus-minio" \
        "deployment/milvus"

    wait_for_rollout "API Services" 300 \
        "deployment/embedding-api" \
        "deployment/authz-api" \
        "deployment/litellm"

    echo ""
    success "Deployment applied successfully!"
    echo ""
    show_status
}

# ============================================================================
# Rollout restart (for updating a running service)
# ============================================================================

rollout_service() {
    local service="$1"
    info "Rolling out ${service}..."
    kctl rollout restart "deployment/${service}" -n "${NAMESPACE}"
    kctl rollout status "deployment/${service}" -n "${NAMESPACE}" --timeout=120s
    success "${service} rolled out"
}

# ============================================================================
# Status
# ============================================================================

show_status() {
    echo ""
    info "=== Busibox Deployment Status (${OVERLAY}) ==="
    echo ""

    echo "--- Namespace ---"
    kctl get namespace "${NAMESPACE}" 2>/dev/null || echo "Namespace not found"
    echo ""

    echo "--- Pods ---"
    kctl get pods -n "${NAMESPACE}" -o wide 2>/dev/null || echo "No pods found"
    echo ""

    echo "--- Services ---"
    kctl get svc -n "${NAMESPACE}" 2>/dev/null || echo "No services found"
    echo ""

    echo "--- PersistentVolumeClaims ---"
    kctl get pvc -n "${NAMESPACE}" 2>/dev/null || echo "No PVCs found"
    echo ""

    echo "--- Jobs ---"
    kctl get jobs -n "${NAMESPACE}" 2>/dev/null || echo "No jobs found"
    echo ""

    # Show node external IP for NodePort access
    echo "--- Access ---"
    local node_ip
    node_ip=$(kctl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}' 2>/dev/null || echo "")
    if [[ -z "$node_ip" ]]; then
        node_ip=$(kctl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "unknown")
    fi
    echo "  Node IP:   ${node_ip}"
    echo "  HTTP:      http://${node_ip}:30080"
    echo "  HTTPS:     https://${node_ip}:30443  (NodePort, requires SSL setup)"
    echo "  Registry:  http://${node_ip}:30500/v2/_catalog"
    echo ""
    echo "  Local access via HTTPS tunnel:"
    echo "    make connect                    # HTTPS tunnel to https://busibox.local/portal"
    echo "    make connect DOMAIN=my.local    # Custom domain"
    echo "    make disconnect                 # Stop tunnel"
    echo ""
}

# ============================================================================
# Clean Storage (delete StatefulSets + PVCs for resizing)
# ============================================================================

clean_storage() {
    warn "This will delete all Deployments with PVCs and all PVCs in namespace '${NAMESPACE}'!"
    warn "Data in persistent volumes will be LOST."
    echo ""
    read -p "Are you sure? (y/N) " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Cancelled."
        return 1
    fi

    # Delete any remaining StatefulSets from previous deployments
    info "Deleting StatefulSets (legacy)..."
    kctl delete statefulsets --all -n "${NAMESPACE}" --ignore-not-found 2>/dev/null || true

    info "Deleting Deployments that use persistent storage..."
    for dep in postgres etcd milvus-minio milvus neo4j minio; do
        kctl delete deployment "$dep" -n "${NAMESPACE}" --ignore-not-found 2>/dev/null || true
    done

    info "Deleting all PVCs..."
    kctl delete pvc --all -n "${NAMESPACE}" --ignore-not-found 2>/dev/null || true

    # Wait for PVCs to be fully deleted
    info "Waiting for PVC cleanup..."
    local retries=30
    local i=0
    while [[ $i -lt $retries ]]; do
        local remaining
        remaining=$(kctl get pvc -n "${NAMESPACE}" --no-headers 2>/dev/null | wc -l | tr -d ' ')
        if [[ "$remaining" == "0" ]]; then
            break
        fi
        echo "  ${remaining} PVCs remaining..."
        sleep 2
        ((i++))
    done

    success "Storage cleaned. Re-run with --apply to recreate with new sizes."
    echo ""
}

# ============================================================================
# Delete
# ============================================================================

delete_all() {
    warn "This will delete ALL Busibox resources from namespace '${NAMESPACE}'!"
    read -p "Are you sure? (y/N) " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Cancelled."
        return
    fi

    info "Deleting all Busibox resources..."

    # Delete kustomized resources
    local overlay_dir="${REPO_ROOT}/k8s/overlays/${OVERLAY}"
    if [[ -d "$overlay_dir" ]]; then
        kctl delete -k "$overlay_dir" --ignore-not-found 2>/dev/null || true
    fi

    # Delete secrets
    kctl delete secret busibox-secrets -n "${NAMESPACE}" --ignore-not-found 2>/dev/null || true

    # Delete PVCs (data!)
    read -p "Also delete persistent data (PVCs)? (y/N) " confirm_pvcs
    if [[ "$confirm_pvcs" == "y" || "$confirm_pvcs" == "Y" ]]; then
        kctl delete pvc --all -n "${NAMESPACE}" 2>/dev/null || true
    fi

    # Delete namespace
    read -p "Delete namespace '${NAMESPACE}'? (y/N) " confirm_ns
    if [[ "$confirm_ns" == "y" || "$confirm_ns" == "Y" ]]; then
        kctl delete namespace "${NAMESPACE}" --ignore-not-found 2>/dev/null || true
    fi

    success "Cleanup complete"
}

# ============================================================================
# Main
# ============================================================================

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         Busibox K8s Deployment (In-Cluster Build)          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Overlay:    ${OVERLAY}"
echo "║  Tag:        ${TAG}"
echo "║  Kubeconfig: ${KUBECONFIG_FILE}"
echo "║  Registry:   ${REGISTRY} (in-cluster)"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Verify kubeconfig exists
if [[ ! -f "$KUBECONFIG_FILE" ]]; then
    error "Kubeconfig not found: ${KUBECONFIG_FILE}"
    echo "Place your kubeconfig at k8s/kubeconfig-rackspace-spot.yaml"
    echo "Or set KUBECONFIG env var"
    exit 1
fi

# Verify cluster connectivity
if ! kctl cluster-info &>/dev/null; then
    error "Cannot connect to cluster. Check kubeconfig and network."
    exit 1
fi
info "Cluster connection verified"

# Execute requested actions (order matters: clean -> sync -> build -> push -> secrets -> apply)
$DO_CLEAN_STORAGE && clean_storage
$DO_SYNC && sync_to_build_server
if $DO_BUILD; then
    # Sync is required before build if not explicitly done
    if ! $DO_SYNC; then
        warn "Building without --sync. Ensure code is already synced."
    fi
    build_images_remote
    push_images_remote
fi
$DO_SECRETS && generate_secrets
if $DO_APPLY; then
    apply_manifests
    # Rollout restart services if we built new images
    if $DO_BUILD; then
        if [[ -n "$SERVICE_FILTER" ]]; then
            rollout_service "$SERVICE_FILTER"
        else
            info "Rolling out updated services..."
            for service_spec in "${BUILDABLE_SERVICES[@]}"; do
                IFS=':' read -r name _ _ <<< "$service_spec"
                rollout_service "$name" || warn "Failed to rollout ${name}"
            done
        fi
    fi
fi
$DO_STATUS && show_status
$DO_DELETE && delete_all

echo ""
success "Done!"
