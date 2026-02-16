#!/usr/bin/env bash
# =============================================================================
# Busibox Backend Common Library
# =============================================================================
#
# Shared helpers used by all backend implementations (Docker, Proxmox, K8s).
# Source this BEFORE the backend-specific file.
#
# Provides:
#   - Service group definitions
#   - Service name/tag mappings
#   - Companion service logic
#   - Host-native service handling (MLX, host-agent)
#   - Backend loader function
#
# =============================================================================

# Guard against double-sourcing
[[ -n "${_BACKEND_COMMON_LOADED:-}" ]] && return 0
_BACKEND_COMMON_LOADED=1

# ============================================================================
# Service Groups
# ============================================================================

# Service group display order
BACKEND_SERVICE_GROUP_ORDER=("Infrastructure" "APIs" "LLM" "Frontend" "User Apps")

# Detect if running on Apple Silicon
is_apple_silicon() {
    local os arch
    os=$(uname -s)
    arch=$(uname -m)
    [[ "$os" == "Darwin" && ("$arch" == "arm64" || "$arch" == "aarch64") ]]
}

# Get services for a group, parameterized by backend
# Usage: backend_get_services_for_group "APIs" "docker"
backend_get_services_for_group() {
    local group="$1"
    local backend="${2:-docker}"

    case "$group" in
        "Infrastructure")
            if [[ "$backend" == "k8s" ]]; then
                echo "postgres redis minio milvus neo4j etcd"
            else
                echo "postgres redis minio milvus neo4j"
            fi
            ;;
        "Build")
            # Only K8s has build infrastructure
            if [[ "$backend" == "k8s" ]]; then
                echo "build-server registry"
            else
                echo ""
            fi
            ;;
        "APIs")
            echo "authz-api agent-api data-api search-api deploy-api bridge-api docs-api embedding-api"
            ;;
        "LLM")
            if [[ "$backend" == "k8s" ]]; then
                echo "litellm"
            elif is_apple_silicon; then
                echo "litellm mlx"
            else
                echo "litellm vllm"
            fi
            ;;
        "Frontend")
            if [[ "$backend" == "docker" ]]; then
                echo "proxy core-apps"
            elif [[ "$backend" == "k8s" ]]; then
                echo "proxy busibox-portal busibox-agents"
            else
                echo "nginx core-apps"  # Proxmox has separate nginx
            fi
            ;;
        "User Apps"|"User_Apps")
            if [[ "$backend" == "k8s" ]]; then
                echo ""  # K8s user apps managed via deploy-api
            else
                echo "user-apps"
            fi
            ;;
        *)
            echo ""
            ;;
    esac
}

# Get the group order for a backend (K8s has Build group)
# Uses underscores for multi-word names to avoid word-splitting issues.
# Callers should convert "User_Apps" to "User Apps" for display.
backend_get_group_order() {
    local backend="${1:-docker}"
    if [[ "$backend" == "k8s" ]]; then
        echo "Infrastructure Build APIs LLM Frontend"
    else
        echo "Infrastructure APIs LLM Frontend User_Apps"
    fi
}

# ============================================================================
# Service Name Mapping
# ============================================================================

# Map user-facing service name to Docker container name (without prefix)
get_container_for_service() {
    local service="$1"
    case "$service" in
        # Infrastructure
        postgres|pg) echo "postgres" ;;
        redis) echo "redis" ;;
        minio|files) echo "minio" ;;
        milvus) echo "milvus" ;;
        neo4j|graph) echo "neo4j" ;;
        etcd) echo "etcd" ;;

        # APIs
        authz|authz-api) echo "authz-api" ;;
        agent|agent-api) echo "agent-api" ;;
        ingest|data-api) echo "data-api" ;;
        data-worker) echo "data-worker" ;;
        search|search-api) echo "search-api" ;;
        deploy|deploy-api) echo "deploy-api" ;;
        bridge|bridge-api) echo "bridge-api" ;;
        docs|docs-api) echo "docs-api" ;;
        embedding|embedding-api) echo "embedding-api" ;;

        # LLM
        litellm) echo "litellm" ;;
        vllm) echo "vllm" ;;

        # Host-native
        mlx) echo "mlx" ;;
        host-agent) echo "host-agent" ;;

        # Frontend
        core-apps|apps|busibox-portal|busibox-agents) echo "core-apps" ;;
        nginx|proxy) echo "nginx" ;;

        # User apps
        user-apps) echo "user-apps" ;;

        # Build (K8s only)
        build-server) echo "build-server" ;;
        registry) echo "registry" ;;

        # Unknown
        *) echo "" ;;
    esac
}

# Check if service name is valid
is_valid_service() {
    local service="$1"
    local container
    container=$(get_container_for_service "$service")
    [[ -n "$container" ]]
}

# Map service to K8s buildable image name
get_k8s_image_name() {
    local service="$1"
    case "$service" in
        authz|authz-api) echo "authz-api" ;;
        agent|agent-api) echo "agent-api" ;;
        ingest|data|data-api) echo "data-api" ;;
        search|search-api) echo "search-api" ;;
        deploy|deploy-api) echo "deploy-api" ;;
        bridge|bridge-api) echo "bridge-api" ;;
        docs|docs-api) echo "docs-api" ;;
        embedding|embedding-api) echo "embedding-api" ;;
        *) echo "" ;;
    esac
}

# Map service to K8s deployment name
get_k8s_deployment_name() {
    local service="$1"
    case "$service" in
        # Most K8s deployments match the container name
        data-worker) echo "data-worker" ;;
        build-server) echo "build-server" ;;
        *) get_container_for_service "$service" ;;
    esac
}

# Map service to Ansible tag (for Docker deployment)
get_ansible_tag() {
    local service="$1"
    case "$service" in
        authz*) echo "authz" ;;
        agent*) echo "agent" ;;
        ingest*|data*) echo "data" ;;
        search*) echo "search" ;;
        deploy*) echo "deploy" ;;
        docs*) echo "docs" ;;
        embedding*) echo "embedding" ;;
        postgres|pg) echo "postgres" ;;
        redis) echo "redis" ;;
        minio|files) echo "minio" ;;
        milvus) echo "milvus" ;;
        neo4j|graph) echo "neo4j" ;;
        core-apps|apps) echo "core-apps" ;;
        nginx|proxy) echo "nginx" ;;
        *) echo "$service" ;;
    esac
}

# Map service to Proxmox make target
get_proxmox_make_target() {
    local service="$1"
    case "$service" in
        postgres|pg) echo "pg" ;;
        minio|files) echo "files" ;;
        milvus) echo "milvus" ;;
        neo4j|graph) echo "neo4j" ;;
        redis) echo "data" ;;
        authz|authz-api) echo "authz" ;;
        agent|agent-api) echo "agent" ;;
        ingest|data-api) echo "data" ;;
        data-worker) echo "data" ;;
        search|search-api) echo "search-api" ;;
        deploy|deploy-api) echo "deploy-api" ;;
        bridge|bridge-api) echo "bridge" ;;
        docs|docs-api) echo "docs" ;;
        embedding|embedding-api) echo "embedding-api" ;;
        litellm) echo "litellm" ;;
        vllm) echo "vllm" ;;
        core-apps|apps) echo "apps" ;;
        nginx|proxy) echo "nginx" ;;
        *) echo "$service" ;;
    esac
}

# ============================================================================
# Companion Services
# ============================================================================

# Get companion services that should be managed together
get_companion_services() {
    local service="$1"
    case "$service" in
        data-api|ingest|data)
            echo "data-worker"
            ;;
    esac
}

# ============================================================================
# Host-Native Services
# ============================================================================

# Check if a service runs on the host (not in any container/pod)
is_host_native_service() {
    local service="$1"
    case "$service" in
        mlx|host-agent) return 0 ;;
        *) return 1 ;;
    esac
}

# Execute action on a host-native service
host_native_action() {
    local service="$1"
    local action="$2"

    case "$action" in
        start)
            info "Starting ${service}..."
            cd "$REPO_ROOT"
            make "${service}-start" || { error "Failed to start ${service}"; return 1; }
            ;;
        stop)
            info "Stopping ${service}..."
            cd "$REPO_ROOT"
            make "${service}-stop" || { error "Failed to stop ${service}"; return 1; }
            ;;
        restart)
            info "Restarting ${service}..."
            cd "$REPO_ROOT"
            make "${service}-restart" || { error "Failed to restart ${service}"; return 1; }
            ;;
        status)
            cd "$REPO_ROOT"
            make "${service}-status" || { error "Failed to get status for ${service}"; return 1; }
            ;;
        logs)
            info "Logs not available for host-native service ${service}."
            info "Use 'make ${service}-status' for current status."
            ;;
        redeploy)
            info "Redeploying ${service}..."
            cd "$REPO_ROOT"
            make "${service}-restart" || { error "Failed to redeploy ${service}"; return 1; }
            ;;
    esac
}

# ============================================================================
# Validate Action
# ============================================================================

validate_action() {
    local action="$1"
    case "$action" in
        start|stop|restart|logs|status|redeploy) return 0 ;;
        *) return 1 ;;
    esac
}

# ============================================================================
# Backend Loader
# ============================================================================

# Source the appropriate backend file based on backend type.
# Usage: load_backend "docker"  (or "proxmox" or "k8s")
load_backend() {
    local backend="$1"
    local backends_dir
    backends_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    case "$backend" in
        docker)
            source "${backends_dir}/docker.sh"
            ;;
        proxmox)
            source "${backends_dir}/proxmox.sh"
            ;;
        k8s)
            source "${backends_dir}/k8s.sh"
            ;;
        *)
            error "Unknown backend: $backend"
            return 1
            ;;
    esac
}
