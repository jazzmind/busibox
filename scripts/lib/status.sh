#!/usr/bin/env bash
#
# Busibox Status Check Library
#
# Non-blocking, async status checking for all services.
# All checks run in background and update cache files.
# Display functions read from cache only (never block).
#
# Usage: source "$(dirname "$0")/lib/status.sh"

# Get script directory for sourcing other libraries
_STATUS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Set REPO_ROOT if not already set
if [[ -z "${REPO_ROOT:-}" ]]; then
    REPO_ROOT="$(cd "${_STATUS_SCRIPT_DIR}/../.." && pwd)"
fi

# Source dependencies
if ! type get_service_container_id &>/dev/null; then
    source "${_STATUS_SCRIPT_DIR}/services.sh"
fi

# Get Docker container prefix for environment
# Priority: 1) Environment variable 2) Detect from running containers 3) .env files 4) Default
# Usage: prefix=$(get_container_prefix_for_status "development")
get_container_prefix_for_status() {
    local env_name="${1:-}"
    local prefix
    
    # 1. Check environment variable (set by Makefile)
    if [[ -n "${CONTAINER_PREFIX:-}" ]]; then
        echo "$CONTAINER_PREFIX"
        return 0
    fi
    
    # 2. Detect from running containers (look for *-authz-api pattern)
    prefix=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '.-authz-api$' | sed 's/-authz-api$//' | head -1)
    if [[ -n "$prefix" ]]; then
        echo "$prefix"
        return 0
    fi
    
    # 3. Check .env files based on environment
    case "$env_name" in
        development|local|docker)
            # Check .env.dev first, then .env.local
            prefix=$(grep -E "^CONTAINER_PREFIX=" "${REPO_ROOT}/.env.dev" 2>/dev/null | cut -d'=' -f2 || echo "")
            if [[ -z "$prefix" ]]; then
                prefix=$(grep -E "^CONTAINER_PREFIX=" "${REPO_ROOT}/.env.local" 2>/dev/null | cut -d'=' -f2 || echo "")
            fi
            # Default for development is 'dev'
            echo "${prefix:-dev}"
            ;;
        demo)
            prefix=$(grep -E "^CONTAINER_PREFIX=" "${REPO_ROOT}/.env.demo" 2>/dev/null | cut -d'=' -f2 || echo "")
            echo "${prefix:-demo}"
            ;;
        staging)
            echo "staging"
            ;;
        production)
            echo "prod"
            ;;
        *)
            # Fallback: try to detect from any running container
            prefix=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '.-api$' | sed 's/-[^-]*-api$//' | head -1)
            echo "${prefix:-local}"
            ;;
    esac
}

# ============================================================================
# Configuration
# ============================================================================

CACHE_DIR="$HOME/.busibox/status-cache"
CACHE_MAX_AGE=30  # seconds
DEBUG_LOG="$HOME/.busibox/status-debug.log"

# Timeouts
SSH_TIMEOUT=2
HEALTH_TIMEOUT=3
TOTAL_CHECK_TIMEOUT=5

# GitHub token cache (loaded once per session)
_GITHUB_TOKEN=""

# Get GitHub token for private repo access
# Checks: environment variable, local file, apps container (Proxmox)
_get_github_token() {
    # Return cached token if already loaded
    if [[ -n "$_GITHUB_TOKEN" ]]; then
        echo "$_GITHUB_TOKEN"
        return
    fi
    
    # Try environment variable first
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        _GITHUB_TOKEN="$GITHUB_TOKEN"
        echo "$_GITHUB_TOKEN"
        return
    fi
    
    # Try local file
    if [[ -f "$HOME/.github_token" ]]; then
        _GITHUB_TOKEN=$(cat "$HOME/.github_token" 2>/dev/null)
        if [[ -n "$_GITHUB_TOKEN" ]]; then
            echo "$_GITHUB_TOKEN"
            return
        fi
    fi
    
    # Try apps container on Proxmox (container 301 for staging, 201 for prod)
    local apps_container=301
    if pct status $apps_container &>/dev/null; then
        _GITHUB_TOKEN=$(pct exec $apps_container -- cat /root/.github_token 2>/dev/null)
        if [[ -n "$_GITHUB_TOKEN" ]]; then
            echo "$_GITHUB_TOKEN"
            return
        fi
    fi
    
    # No token found
    echo ""
}

# Get GitHub repo name for an app from Ansible config
# Usage: _get_app_github_repo "busibox-portal"
# Returns: "owner/repo" or empty string
_get_app_github_repo() {
    local app_name=$1
    local apps_config="${REPO_ROOT}/provision/ansible/group_vars/all/apps.yml"
    
    if [[ ! -f "$apps_config" ]]; then
        return
    fi
    
    # Parse YAML to find github_repo for the app
    # Look for "- name: app_name" followed by "github_repo: owner/repo"
    awk -v app="$app_name" '
        /^[[:space:]]*- name:/ {
            # Extract app name, handling quotes
            gsub(/^[[:space:]]*- name:[[:space:]]*/, "")
            gsub(/["'"'"']/, "")
            gsub(/[[:space:]]*$/, "")
            current_app = $0
        }
        /^[[:space:]]+github_repo:/ && current_app == app {
            # Extract repo name
            gsub(/^[[:space:]]+github_repo:[[:space:]]*/, "")
            gsub(/["'"'"']/, "")
            gsub(/[[:space:]]*$/, "")
            print $0
            exit
        }
    ' "$apps_config"
}

# ============================================================================
# Cache Management
# ============================================================================

# Initialize cache directory
init_cache_dir() {
    mkdir -p "$CACHE_DIR"
}

# Get cache file path for service
# Usage: get_cache_file "authz" "staging"
get_cache_file() {
    local service=$1
    local env=$2
    echo "$CACHE_DIR/${env}-${service}.json"
}

# Read cached status for service
# Returns: 0 if cache valid, 1 if missing/stale
# Output: JSON status data
read_cached_status() {
    local service=$1
    local env=$2
    local cache_file=$(get_cache_file "$service" "$env")
    
    # Check if cache exists
    if [[ ! -f "$cache_file" ]]; then
        return 1
    fi
    
    # Check cache age (use stat -c on Linux, -f on macOS)
    local cache_mtime
    if [[ "$(uname)" == "Darwin" ]]; then
        cache_mtime=$(stat -f %m "$cache_file" 2>/dev/null || echo 0)
    else
        cache_mtime=$(stat -c %Y "$cache_file" 2>/dev/null || echo 0)
    fi
    
    local now=$(date +%s)
    local cache_age=$((now - cache_mtime))
    
    if [[ $cache_age -gt $CACHE_MAX_AGE ]]; then
        return 1  # Stale
    fi
    
    # Return cached data
    cat "$cache_file"
    return 0
}

# Write cache atomically
# Usage: write_cache "authz" "staging" '{"status":"up",...}'
write_cache() {
    local service=$1
    local env=$2
    local status_json=$3
    local cache_file=$(get_cache_file "$service" "$env")
    
    # Atomic write using temp file
    local temp_file="${cache_file}.tmp.$$"
    echo "$status_json" > "$temp_file"
    mv "$temp_file" "$cache_file" 2>/dev/null || rm -f "$temp_file"
}

# Log debug message
debug_log() {
    local message=$1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $message" >> "$DEBUG_LOG" 2>/dev/null
}

# ============================================================================
# Service Status Checks
# ============================================================================

# Check if service container/process is running
# Usage: check_service_status "authz" "staging" "proxmox"
# Returns: "up", "down", or "unknown"
check_service_status() {
    local service=$1
    local env=$2
    local backend=$3
    
    case "$backend" in
        docker)
            # Check if service runs on host (not in Docker)
            case "$service" in
                busibox-portal)
                    # Check if something is listening on port 3000
                    if lsof -i :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    return
                    ;;
                busibox-agents)
                    # Check if something is listening on port 3001
                    if lsof -i :3001 -sTCP:LISTEN -t >/dev/null 2>&1; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    return
                    ;;
                mlx)
                    # MLX runs on host via host-agent on Apple Silicon
                    # Check host-agent for MLX status, or directly check port 8080
                    local os arch
                    os=$(uname -s)
                    arch=$(uname -m)
                    if [[ "$os" == "Darwin" && ("$arch" == "arm64" || "$arch" == "aarch64") ]]; then
                        # Try host-agent first (more reliable)
                        local mlx_response
                        mlx_response=$(curl -sf --max-time 2 http://localhost:8089/mlx/status 2>/dev/null)
                        if [[ -n "$mlx_response" ]]; then
                            local mlx_running
                            mlx_running=$(echo "$mlx_response" | jq -r '.running // false' 2>/dev/null)
                            if [[ "$mlx_running" == "true" ]]; then
                                echo "up"
                            else
                                echo "down"
                            fi
                            return
                        fi
                        # Fallback: check MLX port 8080 directly
                        if lsof -i :8080 -sTCP:LISTEN -t >/dev/null 2>&1; then
                            echo "up"
                        else
                            echo "down"
                        fi
                    else
                        # MLX not applicable on non-Apple Silicon
                        echo "unknown"
                    fi
                    return
                    ;;
                host-agent)
                    # Host-agent runs on host (not in Docker)
                    if curl -sf --max-time 2 http://localhost:8089/health >/dev/null 2>&1; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    return
                    ;;
            esac
            
            # Check Docker container status
            # Use dynamic container prefix based on environment
            local container_prefix
            container_prefix=$(get_container_prefix_for_status "$env")
            
            # Map service names to Docker container names with environment prefix
            local container_name
            case "$service" in
                authz) container_name="${container_prefix}-authz-api" ;;
                postgres) container_name="${container_prefix}-postgres" ;;
                redis) container_name="${container_prefix}-redis" ;;
                milvus) container_name="${container_prefix}-milvus" ;;
                minio) container_name="${container_prefix}-minio" ;;
                data-api) container_name="${container_prefix}-data-api" ;;
                data-worker) container_name="${container_prefix}-data-worker" ;;
                search-api) container_name="${container_prefix}-search-api" ;;
                agent-api) container_name="${container_prefix}-agent-api" ;;
                deploy-api) container_name="${container_prefix}-deploy-api" ;;
                docs-api) container_name="${container_prefix}-docs-api" ;;
                litellm) container_name="${container_prefix}-litellm" ;;
                vllm) container_name="${container_prefix}-vllm" ;;
                mlx) container_name="${container_prefix}-mlx" ;;
                embedding) container_name="${container_prefix}-embedding-api" ;;
                nginx) container_name="${container_prefix}-nginx" ;;
                *) container_name="${container_prefix}-${service}" ;;
            esac
            
            if docker ps --filter "name=${container_name}" --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -q "^${container_name}$"; then
                echo "up"
            else
                echo "down"
            fi
            ;;
            
        proxmox)
            # Check service on container via SSH
            local container_ip=$(get_service_ip "$service" "$env" "$backend")
            
            # Some services run in Docker containers on Proxmox, others as systemd services
            case "$service" in
                milvus)
                    # Milvus runs in Docker
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "docker ps --filter 'name=milvus-standalone' --filter 'status=running' --format '{{.Names}}' 2>/dev/null" | grep -q "milvus-standalone"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
                minio)
                    # MinIO runs in Docker  
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "docker ps --filter 'name=minio' --filter 'status=running' --format '{{.Names}}' 2>/dev/null" | grep -q "minio"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
                postgres)
                    # PostgreSQL is systemd
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "systemctl is-active postgresql" 2>/dev/null | grep -q "^active$"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
                busibox-agents)
                    # Agent manager is systemd but service name might be agent-client (legacy)
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "systemctl is-active busibox-agents 2>/dev/null || systemctl is-active agent-client 2>/dev/null" | grep -q "^active$"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
                busibox-portal)
                    # Busibox Portal runs as systemd service
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "systemctl is-active busibox-portal" 2>/dev/null | grep -q "^active$"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
                data-api|search-api|agent-api|docs-api)
                    # API services use systemd with the exact service name
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "systemctl is-active ${service}" 2>/dev/null | grep -q "^active$"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
                data-worker)
                    # Data worker uses systemd
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "systemctl is-active data-worker" 2>/dev/null | grep -q "^active$"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
                *)
                    # Default: check systemd with service name (replace hyphens with underscores)
                    local service_name="${service//-/_}"
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "systemctl is-active ${service_name}" 2>/dev/null | grep -q "^active$"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
            esac
            ;;
            
        *)
            echo "unknown"
            ;;
    esac
}

# Check service health endpoint with timing
# Usage: check_service_health "authz" "staging" "proxmox"
# Returns: "healthy", "degraded", "down", or "unknown"
# Sets: HEALTH_RESPONSE_TIME (in ms)
check_service_health() {
    local service=$1
    local env=$2
    local backend=$3
    
    HEALTH_RESPONSE_TIME=0
    
    # Special handling for host-agent - runs on host (not in Docker)
    if [[ "$service" == "host-agent" && "$backend" == "docker" ]]; then
        local http_code
        http_code=$(curl -s -w "%{http_code}" --max-time 2 -o /dev/null "http://localhost:8089/health" 2>/dev/null)
        if [[ "$http_code" == "200" ]]; then
            echo "healthy"
        else
            echo "down"
        fi
        return 0
    fi
    
    # Special handling for MLX - runs on host via host-agent
    if [[ "$service" == "mlx" && "$backend" == "docker" ]]; then
        local os arch
        os=$(uname -s)
        arch=$(uname -m)
        if [[ "$os" == "Darwin" && ("$arch" == "arm64" || "$arch" == "aarch64") ]]; then
            # Check MLX health via host-agent
            local mlx_response
            mlx_response=$(curl -sf --max-time 2 http://localhost:8089/mlx/status 2>/dev/null)
            if [[ -n "$mlx_response" ]]; then
                local mlx_healthy
                mlx_healthy=$(echo "$mlx_response" | jq -r '.healthy // false' 2>/dev/null)
                if [[ "$mlx_healthy" == "true" ]]; then
                    echo "healthy"
                else
                    # Running but not healthy = degraded
                    local mlx_running
                    mlx_running=$(echo "$mlx_response" | jq -r '.running // false' 2>/dev/null)
                    if [[ "$mlx_running" == "true" ]]; then
                        echo "degraded"
                    else
                        echo "down"
                    fi
                fi
                return 0
            fi
            # Fallback: check MLX server directly
            local http_code
            http_code=$(curl -s -w "%{http_code}" --max-time 2 -o /dev/null "http://localhost:8080/v1/models" 2>/dev/null)
            if [[ "$http_code" == "200" ]]; then
                echo "healthy"
            else
                echo "down"
            fi
        else
            echo "unknown"
        fi
        return 0
    fi
    
    # For Docker backend with services that have JSON health endpoints,
    # check the actual endpoint to get degraded status (Docker only knows healthy/unhealthy)
    local check_json_health=false
    case "$service" in
        search-api|deploy-api|docs-api) check_json_health=true ;;
    esac
    
    # For Docker backend, use Docker's built-in health check for simple services
    if [[ "$backend" == "docker" && "$check_json_health" == "false" ]]; then
        local container_prefix
        container_prefix=$(get_container_prefix_for_status "$env")
        
        # Map service name to container name
        local container_name
        case "$service" in
            authz) container_name="${container_prefix}-authz-api" ;;
            *) container_name="${container_prefix}-${service}" ;;
        esac
        
        # Check Docker health status
        local docker_health=$(docker inspect --format='{{.State.Health.Status}}' "$container_name" 2>/dev/null)
        if [[ -n "$docker_health" && "$docker_health" != "<no value>" ]]; then
            case "$docker_health" in
                healthy) echo "healthy"; return 0 ;;
                unhealthy) echo "down"; return 0 ;;
                starting) echo "degraded"; return 0 ;;
            esac
        fi
        # If no Docker health check, fall through to HTTP check
    fi
    
    local health_url=$(get_service_health_url "$service" "$env" "$backend")
    
    # Measure response time (use perl for milliseconds on macOS)
    local start_time
    if command -v perl &>/dev/null; then
        start_time=$(perl -MTime::HiRes=time -e 'printf "%.0f\n", time * 1000')
    else
        start_time=$(($(date +%s) * 1000))
    fi
    
    # Get both HTTP code and response body
    local response_file=$(mktemp)
    local http_code
    http_code=$(curl -s -w "%{http_code}" \
        --max-time $HEALTH_TIMEOUT \
        --connect-timeout 2 \
        -o "$response_file" \
        "$health_url" 2>/dev/null)
    
    local end_time
    if command -v perl &>/dev/null; then
        end_time=$(perl -MTime::HiRes=time -e 'printf "%.0f\n", time * 1000')
    else
        end_time=$(($(date +%s) * 1000))
    fi
    HEALTH_RESPONSE_TIME=$((end_time - start_time))
    
    # Evaluate health based on HTTP code and response body
    local health_status="unknown"
    case "$http_code" in
        200|301|302)
            # Check if response contains JSON with status field
            if command -v jq &>/dev/null && [[ -s "$response_file" ]]; then
                local json_status=$(jq -r '.status // empty' "$response_file" 2>/dev/null)
                if [[ -n "$json_status" ]]; then
                    # Use the status from JSON response
                    case "$json_status" in
                        healthy|ok) health_status="healthy" ;;
                        degraded|warning) health_status="degraded" ;;
                        unhealthy|error) health_status="down" ;;
                        *) health_status="unknown" ;;
                    esac
                elif [[ $HEALTH_RESPONSE_TIME -gt 1000 ]]; then
                    health_status="degraded"
                else
                    health_status="healthy"
                fi
            elif [[ $HEALTH_RESPONSE_TIME -gt 1000 ]]; then
                health_status="degraded"
            else
                health_status="healthy"
            fi
            ;;
        401|403)
            # Auth required but service is up
            health_status="healthy"
            ;;
        000)
            # Connection failed
            health_status="down"
            ;;
        *)
            # Other codes (404, 500, etc.)
            health_status="unknown"
            ;;
    esac
    
    # Clean up temp file
    rm -f "$response_file"
    
    echo "$health_status"
}

# Get deployed version from container
# Usage: get_deployed_version "authz" "staging" "proxmox"
# Returns: git hash, image tag, or "unknown"
get_deployed_version() {
    local service=$1
    local env=$2
    local backend=$3
    
    case "$backend" in
        docker)
            # Get container prefix for environment
            local container_prefix
            container_prefix=$(get_container_prefix_for_status "$env")
            
            # Handle external services (return both package version and config version)
            case "$service" in
                milvus)
                    local pkg_ver=$(docker inspect "${container_prefix}-milvus" --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://' || echo "unknown")
                    local cfg_ver=$(docker inspect "${container_prefix}-milvus" --format '{{.Config.Labels.config_version}}' 2>/dev/null || echo "unknown")
                    [[ "$cfg_ver" == "<no value>" ]] && cfg_ver="unknown"
                    echo "${pkg_ver}@${cfg_ver}"
                    return
                    ;;
                minio)
                    # Get actual MinIO version from running container
                    local version=$(docker exec "${container_prefix}-minio" minio --version 2>/dev/null | grep -oE 'RELEASE\.[0-9TZ-]+(-cpuv[0-9]+)?' | head -1)
                    echo "${version:-unknown}"
                    return
                    ;;
                litellm)
                    # Get actual LiteLLM version from running container
                    local version=$(docker exec "${container_prefix}-litellm" pip show litellm 2>/dev/null | grep -oE 'Version: [0-9.]+' | sed 's/Version: //')
                    echo "${version:-unknown}"
                    return
                    ;;
                postgres)
                    local pkg_ver=$(docker inspect "${container_prefix}-postgres" --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://' || echo "unknown")
                    local cfg_ver=$(docker inspect "${container_prefix}-postgres" --format '{{.Config.Labels.config_version}}' 2>/dev/null || echo "unknown")
                    [[ "$cfg_ver" == "<no value>" ]] && cfg_ver="unknown"
                    echo "${pkg_ver}@${cfg_ver}"
                    return
                    ;;
                redis)
                    local pkg_ver=$(docker inspect "${container_prefix}-redis" --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://' || echo "unknown")
                    local cfg_ver=$(docker inspect "${container_prefix}-redis" --format '{{.Config.Labels.config_version}}' 2>/dev/null || echo "unknown")
                    [[ "$cfg_ver" == "<no value>" ]] && cfg_ver="unknown"
                    echo "${pkg_ver}@${cfg_ver}"
                    return
                    ;;
                nginx)
                    local pkg_ver=$(docker inspect "${container_prefix}-nginx" --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://' || echo "unknown")
                    local cfg_ver=$(docker inspect "${container_prefix}-nginx" --format '{{.Config.Labels.config_version}}' 2>/dev/null || echo "unknown")
                    [[ "$cfg_ver" == "<no value>" ]] && cfg_ver="unknown"
                    echo "${pkg_ver}@${cfg_ver}"
                    return
                    ;;
            esac
            
            # Handle Next.js apps (volume-mounted in Docker, so read from host)
            case "$service" in
                busibox-portal)
                    # Apps are volume-mounted, so read from host repo
                    if [[ -d "${REPO_ROOT}/../busibox-portal/.git" ]]; then
                        version=$(cd "${REPO_ROOT}/../busibox-portal" && git rev-parse --short HEAD 2>/dev/null)
                        if [[ -n "$version" ]]; then
                            echo "$version"
                        else
                            echo "unknown"
                        fi
                    else
                        echo "unknown"
                    fi
                    return
                    ;;
                busibox-agents)
                    # Apps are volume-mounted, so read from host repo
                    if [[ -d "${REPO_ROOT}/../busibox-agents/.git" ]]; then
                        version=$(cd "${REPO_ROOT}/../busibox-agents" && git rev-parse --short HEAD 2>/dev/null)
                        if [[ -n "$version" ]]; then
                            echo "$version"
                        else
                            echo "unknown"
                        fi
                    else
                        echo "unknown"
                    fi
                    return
                    ;;
            esac
            
            # Map service names to Docker container names with environment prefix
            local container_name
            case "$service" in
                authz) container_name="${container_prefix}-authz-api" ;;
                postgres) container_name="${container_prefix}-postgres" ;;
                redis) container_name="${container_prefix}-redis" ;;
                milvus) container_name="${container_prefix}-milvus" ;;
                minio) container_name="${container_prefix}-minio" ;;
                data-api) container_name="${container_prefix}-data-api" ;;
                data-worker) container_name="${container_prefix}-data-worker" ;;
                search-api) container_name="${container_prefix}-search-api" ;;
                agent-api) container_name="${container_prefix}-agent-api" ;;
                litellm) container_name="${container_prefix}-litellm" ;;
                nginx) container_name="${container_prefix}-nginx" ;;
                docs-api) container_name="${container_prefix}-docs-api" ;;
                *) container_name="${container_prefix}-${service}" ;;
            esac
            
            # Try label first (set during docker compose build with GIT_COMMIT)
            local version
            version=$(docker inspect "$container_name" --format '{{.Config.Labels.version}}' 2>/dev/null)
            
            if [[ -n "$version" && "$version" != "<no value>" && "$version" != "unknown" ]]; then
                echo "$version"
                return 0
            fi
            
            # Try reading .deploy_version file from container
            local service_path
            case "$service" in
                authz) service_path="authz" ;;
                data-api) service_path="data-api" ;;
                search-api) service_path="search-api" ;;
                agent-api) service_path="agent-api" ;;
                docs-api) service_path="docs-api" ;;
                *) service_path="$service" ;;
            esac
            
            version=$(docker exec "$container_name" cat /opt/${service_path}/.deploy_version 2>/dev/null | jq -r '.commit // empty' 2>/dev/null)
            
            if [[ -n "$version" ]]; then
                echo "$version"
            else
                # For local Docker, if no version info, return "local" to indicate it's a local build
                echo "local"
            fi
            ;;
            
        proxmox)
            # SSH to container and read version info
            local container_ip=$(get_service_ip "$service" "$env" "$backend")
            local version
            
            # Different version detection strategies based on service type
            case "$service" in
                # Python API services - read .deploy_version file from multiple possible locations
                authz|data-api|data-worker|search-api|agent-api|docs-api)
                    # Try multiple paths - different deployments use different locations
                    local version_paths
                    case "$service" in
                        authz) version_paths="/opt/authz/.deploy_version /srv/authz/.deploy_version" ;;
                        data-api|data-worker) version_paths="/srv/data/.deploy_version /srv/data-api/.deploy_version /opt/data-api/.deploy_version" ;;
                        search-api) version_paths="/opt/search-api/.deploy_version /srv/search-api/.deploy_version" ;;
                        agent-api) version_paths="/opt/agent-api/.deploy_version /srv/agent-api/.deploy_version" ;;
                        docs-api) version_paths="/opt/docs-api/.deploy_version /srv/docs-api/.deploy_version" ;;
                    esac
                    
                    # Try each path until one works
                    for path in $version_paths; do
                        version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                            "root@${container_ip}" \
                            "cat ${path} 2>/dev/null" 2>/dev/null | jq -r '.commit // empty' 2>/dev/null | cut -c1-7)
                        if [[ -n "$version" ]]; then
                            break
                        fi
                    done
                    ;;
                    
                # PostgreSQL - check installed version and format as package@config
                postgres)
                    local pg_ver=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                        "root@${container_ip}" \
                        "psql --version 2>/dev/null | grep -oE '[0-9]+' | head -1" 2>/dev/null)
                    if [[ -n "$pg_ver" ]]; then
                        # Format as "16-alpine" to match expected format
                        version="${pg_ver}-alpine"
                    fi
                    ;;
                    
                # Docker-based services - check container image tag
                milvus)
                    version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                        "root@${container_ip}" \
                        "docker inspect milvus-standalone --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://'" 2>/dev/null)
                    ;;
                    
                minio)
                    # Read minio_version from deploy file, fallback to docker exec
                    version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                        "root@${container_ip}" \
                        "cat /opt/minio/.deploy_version 2>/dev/null" 2>/dev/null | jq -r '.minio_version // empty' 2>/dev/null)
                    # If deploy file doesn't have version, get from running container
                    if [[ -z "$version" || "$version" == "unknown" ]]; then
                        version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                            "root@${container_ip}" \
                            "docker exec minio-minio-1 minio --version 2>/dev/null | grep -oE 'RELEASE\.[0-9TZ-]+(-cpuv[0-9]+)?' | head -1" 2>/dev/null)
                    fi
                    ;;
                    
                litellm)
                    # Read litellm_version from deploy file, fallback to pip version
                    version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                        "root@${container_ip}" \
                        "cat /opt/litellm/.deploy_version 2>/dev/null" 2>/dev/null | jq -r '.litellm_version // empty' 2>/dev/null)
                    # If deploy file doesn't have version, get from pip
                    if [[ -z "$version" || "$version" == "latest" ]]; then
                        version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                            "root@${container_ip}" \
                            "/opt/litellm/venv/bin/pip show litellm 2>/dev/null | grep -oE 'Version: [0-9.]+' | sed 's/Version: //'" 2>/dev/null)
                    fi
                    ;;
                    
                # Nginx - check version and format as "alpine" to match docker image naming
                nginx)
                    local nginx_ver=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                        "root@${container_ip}" \
                        "nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1" 2>/dev/null)
                    if [[ -n "$nginx_ver" ]]; then
                        # Format as "alpine" to match docker image tag
                        version="alpine"
                    fi
                    ;;
                    
                # Next.js apps - read from .deployed-version file created by deploywatch
                busibox-portal|busibox-agents)
                    # Apps are deployed to /srv/apps/{service-name}
                    local app_path="/srv/apps/${service}"
                    # Read .deployed-version and extract commit hash (first 7 chars)
                    version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                        "root@${container_ip}" \
                        "cat ${app_path}/.deployed-version 2>/dev/null | jq -r '.commit // empty' 2>/dev/null | cut -c1-7" 2>/dev/null)
                    ;;
                    
                *)
                    version=""
                    ;;
            esac
            
            if [[ -n "$version" ]]; then
                echo "$version"
            else
                echo "unknown"
            fi
            ;;
            
        *)
            echo "unknown"
            ;;
    esac
}

# Get current git version for service
# Usage: get_current_version "authz"
# Returns: git hash or "unknown"
get_current_version() {
    local service=$1
    
    # For infrastructure services, get expected version
    # For pinned versions: read from docker-compose
    # For "latest": query the actual latest version from registry/PyPI
    case "$service" in
        milvus)
            # Milvus has a pinned version in docker-compose
            local pkg_ver=$(grep -A 2 "milvus:" "${REPO_ROOT}/docker-compose.yml" | grep "image:" | sed 's/.*://' || echo "unknown")
            echo "$pkg_ver"
            return
            ;;
        minio)
            # Query Docker Hub for latest MinIO version
            local latest_ver=$(curl -s "https://hub.docker.com/v2/repositories/minio/minio/tags?page_size=1&name=RELEASE" 2>/dev/null | \
                jq -r '.results[0].name // empty' 2>/dev/null)
            echo "${latest_ver:-unknown}"
            return
            ;;
        litellm)
            # Query PyPI for latest LiteLLM version
            local latest_ver=$(curl -s "https://pypi.org/pypi/litellm/json" 2>/dev/null | \
                jq -r '.info.version // empty' 2>/dev/null)
            echo "${latest_ver:-unknown}"
            return
            ;;
        postgres)
            # PostgreSQL has a pinned version
            local pkg_ver=$(grep "image: postgres:" "${REPO_ROOT}/docker-compose.yml" | head -1 | sed 's/.*://' || echo "unknown")
            echo "$pkg_ver"
            return
            ;;
        redis)
            local pkg_ver=$(grep "image: redis:" "${REPO_ROOT}/docker-compose.yml" | sed 's/.*://' || echo "unknown")
            echo "$pkg_ver"
            return
            ;;
        nginx)
            local pkg_ver=$(grep "image: nginx:" "${REPO_ROOT}/docker-compose.yml" | head -1 | sed 's/.*://' || echo "unknown")
            echo "$pkg_ver"
            return
            ;;
    esac
    
    # Handle services with dashes in their names
    case "$service" in
        # Data services come from busibox repo
        data-api|data-worker)
            if [[ -d "${REPO_ROOT}/.git" ]]; then
                (cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null) || echo "unknown"
            else
                echo "unknown"
            fi
            return
            ;;
        # Apps come from their own repos - check local first, then remote with token
        busibox-portal|busibox-agents)
            if [[ -d "${REPO_ROOT}/../${service}/.git" ]]; then
                (cd "${REPO_ROOT}/../${service}" && git rev-parse --short HEAD 2>/dev/null) || echo "unknown"
            else
                # Fallback to remote with GitHub token for private repo
                local github_repo=$(_get_app_github_repo "$service")
                if [[ -n "$github_repo" ]]; then
                    local token=$(_get_github_token)
                    local remote_hash=""
                    if [[ -n "$token" ]]; then
                        remote_hash=$(git ls-remote "https://${token}@github.com/${github_repo}.git" HEAD 2>/dev/null | head -1 | cut -c1-7)
                    fi
                    echo "${remote_hash:-unknown}"
                else
                    echo "unknown"
                fi
            fi
            return
            ;;
    esac
    
    # Get repo and path
    local repo=$(get_service_repo "$service")
    local path=$(get_service_path "$service")
    
    # Determine git directory
    local git_dir
    case "$repo" in
        busibox)
            git_dir="${_STATUS_SCRIPT_DIR}/../.."
            ;;
        busibox-portal)
            git_dir="${_STATUS_SCRIPT_DIR}/../../../busibox-portal"
            ;;
        busibox-agents)
            git_dir="${_STATUS_SCRIPT_DIR}/../../../busibox-agents"
            ;;
        *)
            echo "unknown"
            return 0
            ;;
    esac
    
    # Get git hash
    if [[ -d "$git_dir/.git" ]]; then
        (cd "$git_dir" && git rev-parse --short HEAD 2>/dev/null) || echo "unknown"
    else
        echo "unknown"
    fi
}

# Compare deployed vs current version
# Usage: compare_versions "a1b2c3d" "a1b2c3d" OR "v2.6.5@a1b2c3d" "v2.6.5@b2c3d4e"
# Returns: "synced", "behind", "local", or "unknown"
compare_versions() {
    local deployed=$1
    local current=$2
    
    if [[ "$deployed" == "local" ]]; then
        # Local build without version tracking
        echo "local"
    elif [[ "$deployed" == "unknown" || "$current" == "unknown" || -z "$deployed" || -z "$current" ]]; then
        echo "unknown"
    elif [[ "$deployed" == "$current" ]]; then
        echo "synced"
    else
        # Normalize versions for comparison
        # - Strip @config suffix
        # - Strip CPU variant suffixes like -cpuv1
        local dep_base="${deployed%%@*}"
        local cur_base="${current%%@*}"
        dep_base="${dep_base%%-cpuv*}"
        cur_base="${cur_base%%-cpuv*}"
        
        # If normalized versions match, consider synced
        if [[ "$dep_base" == "$cur_base" ]]; then
            echo "synced"
        else
            echo "behind"
        fi
    fi
}

# ============================================================================
# Async Status Refresh
# ============================================================================

# Update cache for a single service (runs in background)
# Usage: update_service_cache "authz" "staging" "proxmox"
update_service_cache() {
    local service=$1
    local env=$2
    local backend=$3
    
    debug_log "Checking $service in $env ($backend)"
    
    local timestamp=$(date +%s)
    local status="unknown"
    local health="unknown"
    local version="unknown"
    local current_version="unknown"
    local sync_state="unknown"
    local response_time=0
    local error=""
    
    # Check service status first (fast check)
    status=$(check_service_status "$service" "$env" "$backend")
    debug_log "  $service status: $status"
    
    # Only check health and version if service is up
    if [[ "$status" == "up" ]]; then
        # For non-HTTP services (postgres, redis), skip health check
        if [[ "$service" == "postgres" || "$service" == "redis" ]]; then
            health="healthy"
            response_time=0
            debug_log "  $service health: $health (non-HTTP service)"
        else
            # Check health endpoint
            health=$(check_service_health "$service" "$env" "$backend")
            response_time=${HEALTH_RESPONSE_TIME:-0}
            debug_log "  $service health: $health (${response_time}ms)"
        fi
        
        # Get versions
        version=$(get_deployed_version "$service" "$env" "$backend")
        current_version=$(get_current_version "$service")
        sync_state=$(compare_versions "$version" "$current_version")
        debug_log "  $service version: $version (current: $current_version, sync: $sync_state)"
    else
        debug_log "  $service is down, skipping health/version checks"
        response_time=0
    fi
    
    # Build JSON cache entry
    local cache_json
    if command -v jq &>/dev/null; then
        cache_json=$(jq -n \
            --arg timestamp "$timestamp" \
            --arg service "$service" \
            --arg env "$env" \
            --arg status "$status" \
            --arg health "$health" \
            --arg version "$version" \
            --arg current_version "$current_version" \
            --arg sync_state "$sync_state" \
            --argjson response_time "$response_time" \
            --arg error "$error" \
            '{
                timestamp: $timestamp,
                service: $service,
                env: $env,
                status: $status,
                health: $health,
                version: $version,
                current_version: $current_version,
                sync_state: $sync_state,
                response_time_ms: $response_time,
                error: $error
            }')
    else
        # Fallback if jq not available
        cache_json="{\"timestamp\":$timestamp,\"service\":\"$service\",\"env\":\"$env\",\"status\":\"$status\",\"health\":\"$health\",\"version\":\"$version\",\"current_version\":\"$current_version\",\"sync_state\":\"$sync_state\",\"response_time_ms\":$response_time,\"error\":\"$error\"}"
    fi
    
    write_cache "$service" "$env" "$cache_json"
    debug_log "  $service cache updated"
}

# Refresh single service status asynchronously
# Usage: refresh_service_status_async "authz" "staging" "proxmox"
# Returns immediately, updates cache in background
refresh_service_status_async() {
    local service=$1
    local env=$2
    local backend=$3
    
    # Launch background process with timeout
    (
        # Set timeout for entire check
        (
            update_service_cache "$service" "$env" "$backend"
        ) &
        local pid=$!
        
        # Wait with timeout
        local count=0
        while kill -0 $pid 2>/dev/null && [[ $count -lt $TOTAL_CHECK_TIMEOUT ]]; do
            sleep 1
            ((count++))
        done
        
        # Kill if still running
        if kill -0 $pid 2>/dev/null; then
            kill -9 $pid 2>/dev/null
            debug_log "  $service check timed out after ${TOTAL_CHECK_TIMEOUT}s"
        fi
    ) &
}

# Refresh all services asynchronously
# Usage: refresh_all_services_async "staging" "proxmox"
# Returns immediately, all services checked in parallel
refresh_all_services_async() {
    local env=$1
    local backend=$2
    
    init_cache_dir
    debug_log "Starting async refresh for all services in $env ($backend)"
    
    # Launch parallel background jobs for each service
    for service in $ALL_SERVICES; do
        refresh_service_status_async "$service" "$env" "$backend"
    done
    
    debug_log "All service checks launched in background"
}

# ============================================================================
# Cache Reading (Non-Blocking)
# ============================================================================

# Get service status from cache (never blocks)
# Usage: get_service_status_from_cache "authz" "staging"
# Returns: JSON object or placeholder
get_service_status_from_cache() {
    local service=$1
    local env=$2
    
    local cached_status
    if cached_status=$(read_cached_status "$service" "$env" 2>/dev/null); then
        echo "$cached_status"
        return 0
    else
        # Return placeholder for checking state
        echo '{"status":"checking","health":"checking","version":"checking","sync_state":"checking","response_time_ms":0}'
        return 1
    fi
}
