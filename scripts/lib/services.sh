#!/usr/bin/env bash
#
# Busibox Service Registry Library
#
# Defines all services with their metadata: container IDs, repos, health endpoints
# Used by status checking and display systems.
#
# Compatible with bash 3.2+ (macOS default)
#
# Usage: source "$(dirname "$0")/lib/services.sh"

# Get script directory for sourcing other libraries
_SERVICES_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ============================================================================
# Service Definitions (bash 3.2 compatible - no associative arrays)
# ============================================================================
# Format: "container_id:repo:path:health_endpoint:port"
# - container_id: Base container ID (200 for prod, 300 for staging)
#   NOTE: Container IDs are only used as fallback when DNS is unavailable.
#   Primary resolution uses hostnames (e.g., 'deploy-api') via /etc/hosts.
# - repo: Git repository (busibox, ai-portal, agent-manager, etc.)
# - path: Path within repo (srv/authz, etc.) - empty for external repos
# - health_endpoint: Health check path
# - port: Service port
#
# DNS RESOLUTION (PREFERRED):
# Services are primarily resolved via DNS hostnames defined in the internal_dns
# Ansible role. The /etc/hosts file on each container (and Proxmox host) contains
# mappings like: "10.96.201.210  deploy-api deploy authz-api authz"
# This allows using hostnames like 'deploy-api' regardless of network configuration.
# See: provision/ansible/roles/internal_dns/templates/hosts.j2

# Service definitions as simple variables
_SERVICE_authz="210:busibox:srv/authz:/health/live:8010"
_SERVICE_authz_api="210:busibox:srv/authz:/health/live:8010"  # Alias for consistency
# postgres/redis: no HTTP - use "tcp" so get_service_health_url returns empty; manage.sh falls back to ping
_SERVICE_postgres="203:busibox::tcp:5432"
_SERVICE_redis="206:busibox::tcp:6379"
_SERVICE_milvus="204:milvus::/healthz:9091"
_SERVICE_minio="205:minio::/minio/health/live:9000"
_SERVICE_data="206:busibox:srv/data:/health:8002"  # Placeholder for consolidated data display
_SERVICE_data_api="206:busibox:srv/data:/health:8002"
_SERVICE_data_worker="206:busibox:srv/data::8002"
_SERVICE_search_api="204:busibox:srv/search:/health:8003"
_SERVICE_agent_api="202:busibox:srv/agent:/health:8000"
_SERVICE_deploy_api="210:busibox:srv/deploy:/health/live:8011"
_SERVICE_docs_api="202:busibox:srv/docs:/health/live:8004"
_SERVICE_litellm="207:litellm::/health:4000"
_SERVICE_vllm="208:vllm::/health:8000"
_SERVICE_mlx="211:mlx::/v1/models:8080"
_SERVICE_host_agent="0:busibox:scripts/host-agent:/health:8089"
_SERVICE_embedding="208:busibox:srv/embedding:/health:8005"
_SERVICE_embedding_api="208:busibox:srv/embedding:/health:8005"  # Alias for consistency
_SERVICE_nginx="200:busibox:provision/ansible/roles/nginx:/:80"
_SERVICE_ai_portal="201:ai-portal::/portal/api/health:3000"
_SERVICE_agent_manager="201:agent-manager::/agents/api/health:3001"
# user-apps: TCP-only (no single HTTP health) - use tcp so manage.sh falls back to ping
_SERVICE_user_apps="212:busibox::tcp:80"

# Service display names
_NAME_authz="AuthZ"
_NAME_postgres="PostgreSQL"
_NAME_redis="Redis"
_NAME_milvus="Milvus"
_NAME_minio="MinIO"
_NAME_data="Data API & Worker"  # Consolidated display name
_NAME_data_api="Data API"
_NAME_data_worker="Data Worker"
_NAME_search_api="Search API"
_NAME_agent_api="Agent API"
_NAME_litellm="LiteLLM"
_NAME_vllm="vLLM"
_NAME_mlx="MLX"
_NAME_host_agent="Host Agent"
_NAME_embedding="Embedding API"
_NAME_nginx="Nginx"
_NAME_ai_portal="AI Portal"
_NAME_agent_manager="Agent Manager"
_NAME_docs_api="Docs API"
_NAME_authz_api="AuthZ API"
_NAME_deploy_api="Deploy API"
_NAME_user_apps="User Apps"

# Service categories (reorganized per user request)
# Core: authz, postgres, redis, milvus, minio
# LLM: litellm, mlx/vllm (platform-dependent), embedding
# API: deploy, data, search, agent, docs
# App: nginx, ai-portal, agent-manager
_CORE_SERVICES="authz postgres redis milvus minio"
# LLM services - mlx or vllm depends on platform (detected at runtime)
_LLM_SERVICES_BASE="litellm"
_LLM_SERVICES_GPU="vllm"     # For Linux with NVIDIA GPU
_LLM_SERVICES_APPLE="mlx"    # For Apple Silicon
_LLM_SERVICES_SUFFIX="embedding"
_API_SERVICES="deploy-api data search-api agent-api docs-api"
_APP_SERVICES="nginx ai-portal agent-manager"

# All services combined (includes individual services for status checking)
# Note: "data" is used for display, but we check "data-api" and "data-worker" individually
ALL_SERVICES="authz postgres redis milvus minio nginx litellm vllm mlx embedding data-api data-worker search-api agent-api deploy-api docs-api ai-portal agent-manager"

# ============================================================================
# Service Metadata Functions
# ============================================================================

# Get service definition
# Usage: _get_service_def "authz"
_get_service_def() {
    local service=$1
    local var_name="_SERVICE_${service//-/_}"
    eval echo "\$$var_name"
}

# Get service metadata field
# Usage: get_service_field "authz" "container_id" "staging"
get_service_field() {
    local service=$1
    local field=$2
    local env=${3:-production}
    
    # Get service definition
    local service_def=$(_get_service_def "$service")
    
    if [[ -z "$service_def" ]]; then
        return 1
    fi
    
    # Parse definition: "container_id:repo:path:health_endpoint:port"
    local container_id=$(echo "$service_def" | cut -d: -f1)
    local repo=$(echo "$service_def" | cut -d: -f2)
    local path=$(echo "$service_def" | cut -d: -f3)
    local health_endpoint=$(echo "$service_def" | cut -d: -f4)
    local port=$(echo "$service_def" | cut -d: -f5)
    
    # Adjust container ID for environment
    if [[ "$env" == "staging" ]]; then
        container_id=$((container_id + 100))
    fi
    
    case "$field" in
        container_id) echo "$container_id" ;;
        repo) echo "$repo" ;;
        path) echo "$path" ;;
        health_endpoint) echo "$health_endpoint" ;;
        port) echo "$port" ;;
        *) return 1 ;;
    esac
}

# Get container ID for service in environment
# Usage: get_service_container_id "authz" "staging"
get_service_container_id() {
    local service=$1
    local env=${2:-production}
    get_service_field "$service" "container_id" "$env"
}

# Get git repository for service
# Usage: get_service_repo "authz"
get_service_repo() {
    local service=$1
    get_service_field "$service" "repo"
}

# Get path within repo for service
# Usage: get_service_path "authz"
get_service_path() {
    local service=$1
    get_service_field "$service" "path"
}

# Get health check endpoint for service
# Usage: get_service_health_endpoint "authz"
get_service_health_endpoint() {
    local service=$1
    get_service_field "$service" "health_endpoint"
}

# Get service port
# Usage: get_service_port "authz"
get_service_port() {
    local service=$1
    get_service_field "$service" "port"
}

# Get service display name
# Usage: get_service_display_name "authz"
get_service_display_name() {
    local service=$1
    local var_name="_NAME_${service//-/_}"
    local name=$(eval echo "\${$var_name:-}" 2>/dev/null)
    echo "${name:-$service}"
}

# Get service display name with env suffix (for vllm: "vllm (prod)" or "vllm (staging)" when in staging)
# Usage: get_service_display_name_for_env "vllm" "staging"
get_service_display_name_for_env() {
    local service=$1
    local env=${2:-production}
    local base_name
    base_name=$(get_service_display_name "$service")
    
    if [[ "$service" == "vllm" && "$env" == "staging" ]]; then
        if _staging_uses_production_vllm; then
            echo "vllm (prod)"
        else
            echo "vllm (staging)"
        fi
    else
        echo "$base_name"
    fi
}

# Get service hostname (DNS alias)
# Usage: get_service_hostname "deploy_api"
# Returns: deploy-api (matches /etc/hosts entries from internal_dns role)
get_service_hostname() {
    local service=$1
    
    # Map service names to DNS hostnames (from internal_dns role)
    case "$service" in
        authz|authz_api)     echo "authz-api" ;;
        deploy|deploy_api)   echo "deploy-api" ;;
        docs|docs_api)       echo "docs-api" ;;
        data|data_api)       echo "data-api" ;;
        search|search_api)   echo "search-api" ;;
        agent|agent_api)     echo "agent-api" ;;
        embedding|embedding_api) echo "embedding-api" ;;
        postgres|pg)         echo "postgres" ;;
        redis)               echo "redis" ;;
        milvus)              echo "milvus" ;;
        minio|files)         echo "minio" ;;
        nginx|proxy)         echo "nginx" ;;
        litellm)             echo "litellm" ;;
        vllm)                echo "vllm" ;;
        mlx)                 echo "mlx" ;;
        ai_portal)           echo "ai-portal" ;;
        agent_manager)       echo "agent-manager" ;;
        user_apps)           echo "user-apps" ;;
        *)                   echo "$service" ;;
    esac
}

# Check if staging uses production vLLM (reads from Ansible group_vars)
# Usage: _staging_uses_production_vllm
# Returns 0 if true, 1 if false. Requires REPO_ROOT or SCRIPT_DIR to locate vars.
_staging_uses_production_vllm() {
    local repo_root="${REPO_ROOT:-}"
    if [[ -z "$repo_root" && -n "${_SERVICES_SCRIPT_DIR:-}" ]]; then
        repo_root="$(cd "${_SERVICES_SCRIPT_DIR}/../.." && pwd)"
    fi
    local staging_vars="${repo_root}/provision/ansible/inventory/staging/group_vars/all/00-main.yml"
    [[ -f "$staging_vars" ]] && grep -q "use_production_vllm: true" "$staging_vars" 2>/dev/null
}

# Get container IP for service
# Usage: get_service_ip "authz" "staging" "proxmox"
# 
# For Proxmox: Uses DNS hostnames from /etc/hosts (set by internal_dns role)
# For Docker: Uses localhost
#
# NOTE: The Proxmox host must have /etc/hosts configured with these entries.
# Run 'make install SERVICE=internal_dns' or ensure hosts file is set up.
# Special case: staging may use production vLLM (use_production_vllm: true) - use 10.96.200.208
get_service_ip() {
    local service=$1
    local env=${2:-production}
    local backend=${3:-proxmox}
    
    if [[ "$backend" == "docker" ]]; then
        # Docker uses localhost
        echo "localhost"
        return 0
    fi
    
    # vLLM special case: staging often uses production vLLM (saves GPU memory)
    if [[ "$service" == "vllm" && "$env" == "staging" ]]; then
        if _staging_uses_production_vllm; then
            echo "10.96.200.208"
            return 0
        fi
    fi
    
    # Proxmox: Use DNS hostname (resolved via /etc/hosts)
    # This avoids hardcoding IP addresses and network octets
    local hostname
    hostname=$(get_service_hostname "$service")
    
    # Check if hostname is resolvable, fall back to computed IP if not
    if getent hosts "$hostname" &>/dev/null 2>&1; then
        echo "$hostname"
    else
        # Fallback: compute IP from container ID (legacy behavior)
        # This handles cases where /etc/hosts isn't set up yet
        local container_id
        container_id=$(get_service_container_id "$service" "$env")
        
        if [[ "$env" == "staging" ]]; then
            echo "10.96.201.$((container_id - 100))"
        else
            echo "10.96.200.$container_id"
        fi
    fi
}

# Get full health check URL for service
# Usage: get_service_health_url "authz" "staging" "proxmox"
# Returns empty for TCP-only services (postgres, redis, user-apps) - caller should use ping fallback
get_service_health_url() {
    local service=$1
    local env=${2:-production}
    local backend=${3:-proxmox}
    
    local endpoint=$(get_service_health_endpoint "$service")
    # TCP-only services: no HTTP health check - return empty so manage.sh uses ping fallback
    if [[ "$endpoint" == "tcp" || -z "$endpoint" ]]; then
        echo ""
        return 0
    fi
    
    local ip=$(get_service_ip "$service" "$env" "$backend")
    local port=$(get_service_port "$service")
    
    echo "http://${ip}:${port}${endpoint}"
}

# Check if service is in a category
# Usage: is_core_service "authz"
is_core_service() {
    local service=$1
    echo "$_CORE_SERVICES" | grep -q "\<$service\>"
}

is_api_service() {
    local service=$1
    echo "$_API_SERVICES" | grep -q "\<$service\>"
}

is_app_service() {
    local service=$1
    echo "$_APP_SERVICES" | grep -q "\<$service\>"
}

# Get service category
# Usage: get_service_category "authz"
get_service_category() {
    local service=$1
    
    if is_core_service "$service"; then
        echo "core"
    elif is_api_service "$service"; then
        echo "api"
    elif is_app_service "$service"; then
        echo "app"
    else
        echo "unknown"
    fi
}

# Detect which LLM backend to use based on platform
# Returns: "mlx" for Apple Silicon, "vllm" for NVIDIA GPU, empty for cloud-only
_detect_llm_backend() {
    local os arch
    os=$(uname -s)
    arch=$(uname -m)
    
    if [[ "$os" == "Darwin" && ("$arch" == "arm64" || "$arch" == "aarch64") ]]; then
        echo "mlx"
    elif command -v nvidia-smi &>/dev/null; then
        local gpu_count
        gpu_count=$(nvidia-smi -L 2>/dev/null | wc -l || echo "0")
        if [[ $gpu_count -gt 0 ]]; then
            echo "vllm"
        fi
    fi
    # Returns empty for cloud-only setups
}

# Get all services in a category
# Usage: get_services_in_category "core"
get_services_in_category() {
    local category=$1
    
    case "$category" in
        core)
            echo "$_CORE_SERVICES"
            ;;
        llm)
            # Dynamically determine LLM services based on platform
            local llm_backend
            llm_backend=$(_detect_llm_backend)
            local llm_services="$_LLM_SERVICES_BASE"
            if [[ "$llm_backend" == "mlx" ]]; then
                llm_services="$llm_services mlx host-agent"
            elif [[ "$llm_backend" == "vllm" ]]; then
                llm_services="$llm_services vllm"
            fi
            llm_services="$llm_services $_LLM_SERVICES_SUFFIX"
            echo "$llm_services"
            ;;
        api)
            echo "$_API_SERVICES"
            ;;
        app)
            echo "$_APP_SERVICES"
            ;;
        all)
            echo "$ALL_SERVICES"
            ;;
        *)
            return 1
            ;;
    esac
}
