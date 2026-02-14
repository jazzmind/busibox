#!/usr/bin/env bash
# =============================================================================
# Busibox Kubernetes Backend
# =============================================================================
#
# K8s-specific status checks and service actions via kubectl.
# Source common.sh BEFORE this file.
#
# Implements the unified backend interface:
#   - backend_get_service_status SERVICE
#   - backend_service_action SERVICE ACTION
#   - backend_start_all
#   - backend_stop_all
#   - backend_restart_all
#   - backend_detect_installation ENV
#   - backend_get_tunnel_status
#
# =============================================================================

[[ -n "${_BACKEND_K8S_LOADED:-}" ]] && return 0
_BACKEND_K8S_LOADED=1

# ============================================================================
# Configuration
# ============================================================================

K8S_NAMESPACE="busibox"
K8S_KUBECONFIG="${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml"
K8S_PID_FILE="${REPO_ROOT}/.k8s-connect.pid"
K8S_DOMAIN="${DOMAIN:-busibox.local}"

# kubectl wrapper with kubeconfig
_kctl() {
    KUBECONFIG="$K8S_KUBECONFIG" kubectl "$@"
}

# Verify kubectl + kubeconfig are available
_k8s_check_prereqs() {
    if ! command -v kubectl &>/dev/null; then
        error "kubectl not installed"
        return 1
    fi
    if [[ ! -f "$K8S_KUBECONFIG" ]]; then
        error "Kubeconfig not found: ${K8S_KUBECONFIG}"
        return 1
    fi
    return 0
}

# ============================================================================
# Status
# ============================================================================

# Get status of a single K8s service
# Returns: running, stopped, pending, failed, missing, unknown
backend_get_service_status() {
    local service="$1"

    if ! _k8s_check_prereqs 2>/dev/null; then
        echo "unknown"
        return
    fi

    local deployment
    deployment=$(get_k8s_deployment_name "$service")
    if [[ -z "$deployment" ]]; then
        echo "unknown"
        return
    fi

    # Check if deployment exists
    local replicas ready
    replicas=$(_kctl get deployment "$deployment" -n "$K8S_NAMESPACE" \
        -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "")

    if [[ -z "$replicas" ]]; then
        echo "missing"
        return
    fi

    # Scaled to 0 = stopped
    if [[ "$replicas" == "0" ]]; then
        echo "stopped"
        return
    fi

    # Check ready replicas
    ready=$(_kctl get deployment "$deployment" -n "$K8S_NAMESPACE" \
        -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    ready="${ready:-0}"

    if [[ "$ready" -ge "$replicas" ]]; then
        echo "running"
    elif [[ "$ready" -gt 0 ]]; then
        echo "running"  # partially ready but functional
    else
        # Check pod phase for more detail
        local phase
        phase=$(_kctl get pods -n "$K8S_NAMESPACE" -l "app=${deployment}" \
            -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "")
        case "$phase" in
            Pending) echo "pending" ;;
            Failed) echo "failed" ;;
            *) echo "pending" ;;
        esac
    fi
}

# ============================================================================
# Actions
# ============================================================================

backend_service_action() {
    local service="$1"
    local action="$2"

    if ! _k8s_check_prereqs; then
        return 1
    fi

    local deployment
    deployment=$(get_k8s_deployment_name "$service")
    if [[ -z "$deployment" ]]; then
        error "Unknown K8s deployment for service: ${service}"
        return 1
    fi

    case "$action" in
        status)
            info "K8s status for ${service}:"
            echo ""
            _kctl get deployment "$deployment" -n "$K8S_NAMESPACE" -o wide 2>/dev/null || echo "  Deployment not found"
            echo ""
            _kctl get pods -n "$K8S_NAMESPACE" -l "app=${deployment}" -o wide 2>/dev/null || echo "  No pods found"
            ;;

        logs)
            info "Showing logs for ${service} (Ctrl+C to exit)..."
            echo ""
            _kctl logs -n "$K8S_NAMESPACE" -l "app=${deployment}" -f --tail=100 2>/dev/null || error "No logs available"
            ;;

        restart)
            info "Restarting ${service}..."
            _kctl rollout restart deployment/"$deployment" -n "$K8S_NAMESPACE" 2>/dev/null || {
                error "Failed to restart ${service}"
                return 1
            }
            success "Rollout restart triggered for ${service}"
            ;;

        stop)
            info "Scaling ${service} to 0 replicas..."
            _kctl scale deployment/"$deployment" --replicas=0 -n "$K8S_NAMESPACE" 2>/dev/null || {
                error "Failed to stop ${service}"
                return 1
            }
            success "Service ${service} scaled to 0"
            ;;

        start)
            info "Scaling ${service} to 1 replica..."
            _kctl scale deployment/"$deployment" --replicas=1 -n "$K8S_NAMESPACE" 2>/dev/null || {
                error "Failed to start ${service}"
                return 1
            }
            success "Service ${service} scaled to 1"
            ;;

        redeploy)
            info "Redeploying ${service} (sync + build + push + rollout)..."
            echo ""

            local image_name
            image_name=$(get_k8s_image_name "$service")

            if [[ -n "$image_name" ]]; then
                local k8s_deploy="${REPO_ROOT}/scripts/k8s/deploy.sh"
                if [[ ! -f "$k8s_deploy" ]]; then
                    error "K8s deploy script not found: ${k8s_deploy}"
                    return 1
                fi

                info "Syncing code and rebuilding image: ${image_name}..."
                bash "$k8s_deploy" --sync --build --service "$image_name" --kubeconfig "$K8S_KUBECONFIG"
            else
                info "Service ${service} uses upstream image - skipping build"
            fi

            # Rollout restart to pick up any changes
            _kctl rollout restart deployment/"$deployment" -n "$K8S_NAMESPACE" 2>/dev/null || true
            success "Service ${service} redeployed"
            ;;
    esac
}

# ============================================================================
# Bulk Actions
# ============================================================================

backend_start_all() {
    if ! _k8s_check_prereqs; then return 1; fi

    info "Scaling all deployments to 1 replica..."
    local deployments
    deployments=$(_kctl get deployments -n "$K8S_NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

    for dep in $deployments; do
        _kctl scale deployment/"$dep" --replicas=1 -n "$K8S_NAMESPACE" 2>/dev/null || true
    done
    success "All deployments scaled to 1"
}

backend_stop_all() {
    if ! _k8s_check_prereqs; then return 1; fi

    info "Scaling all deployments to 0 replicas..."
    local deployments
    deployments=$(_kctl get deployments -n "$K8S_NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

    for dep in $deployments; do
        _kctl scale deployment/"$dep" --replicas=0 -n "$K8S_NAMESPACE" 2>/dev/null || true
    done
    success "All deployments scaled to 0"
}

backend_restart_all() {
    if ! _k8s_check_prereqs; then return 1; fi

    info "Rolling restart all deployments..."
    local deployments
    deployments=$(_kctl get deployments -n "$K8S_NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

    for dep in $deployments; do
        _kctl rollout restart deployment/"$dep" -n "$K8S_NAMESPACE" 2>/dev/null || true
    done
    success "All deployments restarted"
}

# ============================================================================
# Installation Detection
# ============================================================================

backend_detect_installation() {
    local env="${1:-staging}"

    if ! command -v kubectl &>/dev/null || [[ ! -f "$K8S_KUBECONFIG" ]]; then
        echo "not_installed"
        return
    fi

    local running_pods
    running_pods=$(_kctl get pods -n "$K8S_NAMESPACE" --field-selector=status.phase=Running \
        --no-headers 2>/dev/null | wc -l | tr -d ' ')

    if [[ "$running_pods" -ge 3 ]]; then
        echo "installed"
    elif [[ "$running_pods" -gt 0 ]]; then
        echo "partial"
    else
        echo "not_installed"
    fi
}

# ============================================================================
# Tunnel (Connect/Disconnect)
# ============================================================================

# Check if the kubectl port-forward tunnel is active
# Returns: "active" or "inactive"
backend_get_tunnel_status() {
    if [[ -f "$K8S_PID_FILE" ]]; then
        local pid
        pid=$(cat "$K8S_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "active"
            return
        fi
    fi
    echo "inactive"
}

# Get human-readable tunnel status string (with color)
backend_get_tunnel_status_string() {
    local status
    status=$(backend_get_tunnel_status)

    if [[ "$status" == "active" ]]; then
        echo "${GREEN}ACTIVE${NC} - https://${K8S_DOMAIN}/portal"
    else
        echo "${DIM}inactive${NC} ${DIM}(run 'make connect')${NC}"
    fi
}

# Start the tunnel via make connect
backend_connect() {
    cd "$REPO_ROOT"
    make connect
}

# Stop the tunnel via make disconnect
backend_disconnect() {
    cd "$REPO_ROOT"
    make disconnect
}
