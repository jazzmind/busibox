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

# Service group display order (manage menu)
BACKEND_SERVICE_GROUP_ORDER=("Frontend" "APIs" "LLM" "Infrastructure")

# Detect if the LLM backend is MLX (Apple Silicon).
# Checks LLM_BACKEND env var first (set by manager-run.sh to forward host
# platform info into the manager container), then falls back to uname.
is_apple_silicon() {
    if [[ -n "${LLM_BACKEND:-}" ]]; then
        [[ "$LLM_BACKEND" == "mlx" ]]
        return
    fi
    local os arch
    os="${HOST_OS:-$(uname -s)}"
    arch="${HOST_ARCH:-$(uname -m)}"
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
                # Keep etcd visible for K8s operations.
                echo "nginx redis minio postgres milvus neo4j etcd"
            else
                echo "nginx redis minio postgres milvus neo4j"
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
            echo "deploy authz agent data embedding search bridge docs"
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
                echo "core-apps user-apps"
            elif [[ "$backend" == "k8s" ]]; then
                echo "proxy"
            else
                echo "core-apps user-apps"
            fi
            ;;
        "User Apps"|"User_Apps")
            # Legacy group kept for compatibility; user-apps now lives under Frontend.
            echo ""
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
        # Keep Build group in K8s while matching requested primary ordering.
        echo "Frontend APIs LLM Infrastructure Build"
    else
        echo "Frontend APIs LLM Infrastructure"
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
        ingest|data|data-api) echo "data-api" ;;
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
        core-apps|apps|busibox-portal|busibox-agents|busibox-appbuilder) echo "core-apps" ;;
        nginx|proxy) echo "proxy" ;;

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
        core-apps|apps|busibox-portal|busibox-agents|busibox-appbuilder) echo "core-apps" ;;
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
        ingest|data|data-api) echo "data" ;;
        data-worker) echo "data" ;;
        search|search-api) echo "search-api" ;;
        deploy|deploy-api) echo "deploy-api" ;;
        bridge|bridge-api) echo "bridge" ;;
        docs|docs-api) echo "docs" ;;
        embedding|embedding-api) echo "embedding-api" ;;
        litellm) echo "litellm" ;;
        vllm) echo "vllm" ;;
        core-apps|apps) echo "apps" ;;
        busibox-portal) echo "deploy-busibox-portal" ;;
        busibox-agents) echo "deploy-busibox-agents" ;;
        busibox-appbuilder) echo "deploy-busibox-appbuilder" ;;
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

# Check if we're running inside a container (manager or other)
_is_inside_container() {
    [[ -f /.dockerenv ]] || grep -q 'docker\|lxc' /proc/1/cgroup 2>/dev/null
}

# Get the host-agent base URL. From inside a container we reach the host
# via host.docker.internal; on the host itself via localhost.
_host_agent_url() {
    local port="${HOST_AGENT_PORT:-8089}"
    if _is_inside_container; then
        echo "http://host.docker.internal:${port}"
    else
        echo "http://localhost:${port}"
    fi
}

# Read HOST_AGENT_TOKEN from environment or .env file
_host_agent_token() {
    if [[ -n "${HOST_AGENT_TOKEN:-}" ]]; then
        echo "$HOST_AGENT_TOKEN"
        return
    fi
    local env_file="${REPO_ROOT}/.env.${CONTAINER_PREFIX:-dev}"
    if [[ -f "$env_file" ]]; then
        awk -F= '/^HOST_AGENT_TOKEN=/{val=substr($0, index($0,$2))} END{print val}' "$env_file" 2>/dev/null | tr -d '\r\n'
    fi
}

# Execute action on a host-native service.
# When running inside the manager container, delegates to the host-agent
# HTTP API (since host processes aren't visible). On the host, falls back
# to the make targets which use ps/pkill directly.
host_native_action() {
    local service="$1"
    local action="$2"

    # If we're inside a container, use host-agent API for MLX operations
    if _is_inside_container && [[ "$service" == "mlx" ]]; then
        _host_native_mlx_via_api "$action"
        return $?
    fi

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
        sync)
            cd "$REPO_ROOT"
            make mlx-sync || { error "Failed to sync models"; return 1; }
            ;;
    esac
}

_display_media_status() {
    local base_url="$1"
    local auth_header="$2"

    echo "=== Media Servers ==="
    local media_response
    media_response=$(eval curl -sf --max-time 5 ${auth_header} "'${base_url}/media/status'" 2>/dev/null)
    if [[ -n "$media_response" ]]; then
        echo "$media_response" | jq -r '
            .servers | to_entries[] |
            "  \(.value.label // .key) (\(.key)):" +
            if .value.running then
                " Running (port \(.value.port // "?"))" +
                if .value.healthy then " [healthy]" else " [unhealthy]" end +
                if .value.model then "\n    Model: \(.value.model)" else "" end
            else
                " Stopped" +
                if .value.model then " (model: \(.value.model))" else "" end
            end
        ' 2>/dev/null || echo "$media_response"
    else
        echo "  (could not query media status)"
    fi
}

# MLX management via host-agent HTTP API (used from inside containers)
_host_native_mlx_via_api() {
    local action="$1"
    local base_url
    base_url=$(_host_agent_url)
    local token
    token=$(_host_agent_token)

    local auth_header=""
    if [[ -n "$token" ]]; then
        auth_header="-H 'Authorization: Bearer ${token}'"
    fi

    # Check host-agent reachability first
    if ! curl -sf --max-time 2 "${base_url}/health" >/dev/null 2>&1; then
        error "Host-agent is not running at ${base_url}"
        info "Start it on the host with: make host-agent-start"
        return 1
    fi

    case "$action" in
        status)
            echo ""
            echo "=== MLX Server Status (via host-agent) ==="
            local response
            response=$(eval curl -sf --max-time 5 ${auth_header} "'${base_url}/mlx/status?target=all'" 2>/dev/null)
            if [[ -n "$response" ]]; then
                # Parse and display primary/fast status
                local primary_running fast_running primary_model fast_model
                primary_running=$(echo "$response" | jq -r '.primary.running // false' 2>/dev/null)
                fast_running=$(echo "$response" | jq -r '.fast.running // false' 2>/dev/null)
                primary_model=$(echo "$response" | jq -r '.primary.model // "none"' 2>/dev/null)
                fast_model=$(echo "$response" | jq -r '.fast.model // "none"' 2>/dev/null)

                if [[ "$primary_running" == "true" ]]; then
                    echo "MLX Primary: Running (port 8080)"
                    echo "  Active model: ${primary_model}"
                    echo "  Health: Healthy"
                else
                    echo "MLX Primary: Not running"
                fi

                local fast_port="${MLX_FAST_PORT:-18081}"
                if [[ "$fast_running" == "true" ]]; then
                    echo "MLX Fast: Running (port ${fast_port})"
                    echo "  Active model: ${fast_model}"
                    echo "  Health: Healthy"
                else
                    echo "MLX Fast: Not running (port ${fast_port})"
                fi
            else
                error "Failed to get MLX status from host-agent"
                return 1
            fi

            echo ""
            _display_media_status "$base_url" "$auth_header"

            echo ""
            echo "=== Host Agent ==="
            echo "Host Agent: Running (${base_url})"
            ;;
        start)
            info "Starting MLX via host-agent..."
            local response
            response=$(eval curl -sf --max-time 30 -X POST ${auth_header} \
                -H "'Content-Type: application/json'" \
                -d "'{\"model_type\": \"dual\"}'" \
                "'${base_url}/mlx/start'" 2>/dev/null)
            if [[ -n "$response" ]]; then
                echo "$response" | bash "${REPO_ROOT}/scripts/lib/host-agent-format.sh" && \
                    success "MLX server started" || error "Failed to start MLX"
            else
                error "No response from host-agent"
                return 1
            fi
            ;;
        stop)
            info "Stopping MLX via host-agent..."
            local response
            response=$(eval curl -sf --max-time 10 -X POST ${auth_header} \
                "'${base_url}/mlx/stop?target=all'" 2>/dev/null)
            if [[ -n "$response" ]]; then
                echo "$response" | bash "${REPO_ROOT}/scripts/lib/host-agent-format.sh" && \
                    success "MLX server stopped" || error "Failed to stop MLX"
            else
                error "No response from host-agent"
                return 1
            fi
            ;;
        restart)
            _host_native_mlx_via_api "stop"
            sleep 2
            _host_native_mlx_via_api "start"
            echo ""
            _display_media_status "$base_url" "$auth_header"
            ;;
        logs)
            info "Logs not available for MLX via host-agent."
            info "Use status to check current state."
            ;;
        redeploy)
            _host_native_mlx_via_api "restart"
            ;;
        sync)
            echo ""
            echo "=== Model Cache Status ==="
            local sync_response
            sync_response=$(eval curl -sf --max-time 15 ${auth_header} "'${base_url}/models/required'" 2>/dev/null)
            if [[ -z "$sync_response" ]]; then
                error "Failed to query model status from host-agent"
                return 1
            fi

            local tier
            tier=$(echo "$sync_response" | jq -r '.tier // "unknown"' 2>/dev/null)

            echo ""
            echo "  LLM Models (tier: ${tier}):"
            echo "$sync_response" | jq -r '
                .models[] | select(.category == "llm") |
                if .cached then
                    "    [cached]  \(.role):\t\(.name) (\(.size_human // "?"))"
                else
                    "    [MISSING] \(.role):\t\(.name)"
                end
            ' 2>/dev/null

            echo ""
            echo "  Media Models:"
            echo "$sync_response" | jq -r '
                .models[] | select(.category == "media") |
                if .cached then
                    "    [cached]  \(.role):\t\(.name) (\(.size_human // "?"))"
                else
                    "    [MISSING] \(.role):\t\(.name)"
                end
            ' 2>/dev/null

            local missing_count
            missing_count=$(echo "$sync_response" | jq -r '.missing_count' 2>/dev/null)

            echo ""
            if [[ "$missing_count" == "0" ]]; then
                success "All models are cached."
            else
                echo "${missing_count} model(s) missing."
                echo -n "Download now? [y/N]: "
                read -r answer
                if [[ "$answer" =~ ^[Yy]$ ]]; then
                    local missing_models
                    missing_models=$(echo "$sync_response" | jq -r '.models[] | select(.cached == false) | .name' 2>/dev/null)
                    while IFS= read -r model; do
                        [[ -z "$model" ]] && continue
                        info "Downloading ${model}..."
                        eval curl -sf --max-time 600 -N -X POST ${auth_header} \
                            -H "'Content-Type: application/json'" \
                            -d "'{\"model\": \"${model}\"}'" \
                            "'${base_url}/mlx/models/download'" 2>/dev/null | while IFS= read -r line; do
                            local msg
                            msg=$(echo "$line" | sed 's/^data: //' | jq -r '.message // empty' 2>/dev/null)
                            [[ -n "$msg" ]] && echo "  $msg"
                        done
                    done <<< "$missing_models"
                    echo ""
                    success "Download complete."
                fi
            fi
            ;;
    esac
}

# ============================================================================
# Validate Action
# ============================================================================

validate_action() {
    local action="$1"
    case "$action" in
        start|stop|restart|logs|status|redeploy|sync) return 0 ;;
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
