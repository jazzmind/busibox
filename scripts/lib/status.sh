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
                ai-portal)
                    # Check if something is listening on port 3000
                    if lsof -i :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    return
                    ;;
                agent-manager)
                    # Check if something is listening on port 3001
                    if lsof -i :3001 -sTCP:LISTEN -t >/dev/null 2>&1; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    return
                    ;;
            esac
            
            # Check Docker container status
            # Map service names to Docker container names
            local container_name
            case "$service" in
                authz) container_name="local-authz-api" ;;
                postgres) container_name="local-postgres" ;;
                redis) container_name="local-redis" ;;
                milvus) container_name="local-milvus" ;;
                minio) container_name="local-minio" ;;
                ingest-api) container_name="local-ingest-api" ;;
                search-api) container_name="local-search-api" ;;
                agent-api) container_name="local-agent-api" ;;
                litellm) container_name="local-litellm" ;;
                nginx) container_name="local-nginx" ;;
                docs-api) container_name="local-docs-api" ;;
                *) container_name="local-${service}" ;;
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
                agent-manager)
                    # Agent manager is systemd but service name might be agent-client (legacy)
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "systemctl is-active agent-manager 2>/dev/null || systemctl is-active agent-client 2>/dev/null" | grep -q "^active$"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
                ai-portal)
                    # AI Portal runs as systemd service
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "systemctl is-active ai-portal" 2>/dev/null | grep -q "^active$"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
                ingest-api|search-api|agent-api|docs-api)
                    # API services use systemd with the exact service name
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "systemctl is-active ${service}" 2>/dev/null | grep -q "^active$"; then
                        echo "up"
                    else
                        echo "down"
                    fi
                    ;;
                ingest-worker)
                    # Ingest worker uses systemd
                    if timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no "root@${container_ip}" "systemctl is-active ingest-worker" 2>/dev/null | grep -q "^active$"; then
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
    
    local health_url=$(get_service_health_url "$service" "$env" "$backend")
    
    # Measure response time (use perl for milliseconds on macOS)
    local start_time
    if command -v perl &>/dev/null; then
        start_time=$(perl -MTime::HiRes=time -e 'printf "%.0f\n", time * 1000')
    else
        start_time=$(($(date +%s) * 1000))
    fi
    
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        --max-time $HEALTH_TIMEOUT \
        --connect-timeout 2 \
        "$health_url" 2>/dev/null)
    
    local end_time
    if command -v perl &>/dev/null; then
        end_time=$(perl -MTime::HiRes=time -e 'printf "%.0f\n", time * 1000')
    else
        end_time=$(($(date +%s) * 1000))
    fi
    HEALTH_RESPONSE_TIME=$((end_time - start_time))
    
    # Evaluate health based on HTTP code
    case "$http_code" in
        200|301|302)
            # 200 = OK, 301/302 = redirect (still responding)
            if [[ $HEALTH_RESPONSE_TIME -gt 1000 ]]; then
                echo "degraded"
            else
                echo "healthy"
            fi
            ;;
        401|403)
            # Auth required but service is up
            echo "healthy"
            ;;
        000)
            # Connection failed
            echo "down"
            ;;
        *)
            # Other codes (404, 500, etc.)
            echo "unknown"
            ;;
    esac
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
            # Handle external services (return both package version and config version)
            case "$service" in
                milvus)
                    local pkg_ver=$(docker inspect local-milvus --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://' || echo "unknown")
                    local cfg_ver=$(docker inspect local-milvus --format '{{.Config.Labels.config_version}}' 2>/dev/null || echo "unknown")
                    [[ "$cfg_ver" == "<no value>" ]] && cfg_ver="unknown"
                    echo "${pkg_ver}@${cfg_ver}"
                    return
                    ;;
                minio)
                    local pkg_ver=$(docker inspect local-minio --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://' || echo "unknown")
                    local cfg_ver=$(docker inspect local-minio --format '{{.Config.Labels.config_version}}' 2>/dev/null || echo "unknown")
                    [[ "$cfg_ver" == "<no value>" ]] && cfg_ver="unknown"
                    echo "${pkg_ver}@${cfg_ver}"
                    return
                    ;;
                litellm)
                    local pkg_ver=$(docker inspect local-litellm --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://' || echo "unknown")
                    local cfg_ver=$(docker inspect local-litellm --format '{{.Config.Labels.config_version}}' 2>/dev/null || echo "unknown")
                    [[ "$cfg_ver" == "<no value>" ]] && cfg_ver="unknown"
                    echo "${pkg_ver}@${cfg_ver}"
                    return
                    ;;
                postgres)
                    local pkg_ver=$(docker inspect local-postgres --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://' || echo "unknown")
                    local cfg_ver=$(docker inspect local-postgres --format '{{.Config.Labels.config_version}}' 2>/dev/null || echo "unknown")
                    [[ "$cfg_ver" == "<no value>" ]] && cfg_ver="unknown"
                    echo "${pkg_ver}@${cfg_ver}"
                    return
                    ;;
                redis)
                    local pkg_ver=$(docker inspect local-redis --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://' || echo "unknown")
                    local cfg_ver=$(docker inspect local-redis --format '{{.Config.Labels.config_version}}' 2>/dev/null || echo "unknown")
                    [[ "$cfg_ver" == "<no value>" ]] && cfg_ver="unknown"
                    echo "${pkg_ver}@${cfg_ver}"
                    return
                    ;;
                nginx)
                    local pkg_ver=$(docker inspect local-nginx --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://' || echo "unknown")
                    local cfg_ver=$(docker inspect local-nginx --format '{{.Config.Labels.config_version}}' 2>/dev/null || echo "unknown")
                    [[ "$cfg_ver" == "<no value>" ]] && cfg_ver="unknown"
                    echo "${pkg_ver}@${cfg_ver}"
                    return
                    ;;
            esac
            
            # Handle Next.js apps (volume-mounted in Docker, so read from host)
            case "$service" in
                ai-portal)
                    # Apps are volume-mounted, so read from host repo
                    if [[ -d "${REPO_ROOT}/../ai-portal/.git" ]]; then
                        version=$(cd "${REPO_ROOT}/../ai-portal" && git rev-parse --short HEAD 2>/dev/null)
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
                agent-manager)
                    # Apps are volume-mounted, so read from host repo
                    if [[ -d "${REPO_ROOT}/../agent-manager/.git" ]]; then
                        version=$(cd "${REPO_ROOT}/../agent-manager" && git rev-parse --short HEAD 2>/dev/null)
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
            
            # Map service names to Docker container names
            local container_name
            case "$service" in
                authz) container_name="local-authz-api" ;;
                postgres) container_name="local-postgres" ;;
                redis) container_name="local-redis" ;;
                milvus) container_name="local-milvus" ;;
                minio) container_name="local-minio" ;;
                ingest-api) container_name="local-ingest-api" ;;
                search-api) container_name="local-search-api" ;;
                agent-api) container_name="local-agent-api" ;;
                litellm) container_name="local-litellm" ;;
                nginx) container_name="local-nginx" ;;
                docs-api) container_name="local-docs-api" ;;
                *) container_name="local-${service}" ;;
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
                ingest-api) service_path="ingest-api" ;;
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
                # Python API services - try .deploy_version, fallback to git in /srv
                authz|ingest-api|ingest-worker|search-api|agent-api|docs-api)
                    local service_path deploy_path
                    case "$service" in
                        authz) service_path="authz"; deploy_path="/srv/authz" ;;
                        ingest-api) service_path="ingest"; deploy_path="/srv/ingest" ;;
                        ingest-worker) service_path="ingest"; deploy_path="/srv/ingest" ;;
                        search-api) service_path="search"; deploy_path="/srv/search" ;;
                        agent-api) service_path="agent"; deploy_path="/srv/agent" ;;
                        docs-api) service_path="docs"; deploy_path="/srv/docs" ;;
                    esac
                    
                    # Try .deploy_version first (if it exists)
                    version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                        "root@${container_ip}" \
                        "cat /opt/${service_path}/.deploy_version 2>/dev/null" 2>/dev/null | jq -r '.commit // empty' 2>/dev/null)
                    
                    # Fallback: try git in deploy path
                    if [[ -z "$version" ]]; then
                        version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                            "root@${container_ip}" \
                            "cd ${deploy_path} && git rev-parse --short HEAD 2>/dev/null" 2>/dev/null)
                    fi
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
                    # MinIO often uses "latest" tag
                    version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                        "root@${container_ip}" \
                        "docker inspect minio --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://'" 2>/dev/null)
                    [[ "$version" == "" ]] && version="latest"
                    ;;
                    
                litellm)
                    # LiteLLM runs in Docker - use pct exec to check image
                    local container_id=$(get_service_container "$service")
                    if [[ -n "$container_id" ]]; then
                        version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                            root@proxmox.local \
                            "pct exec ${container_id} -- docker inspect litellm --format '{{.Config.Image}}' 2>/dev/null | sed 's/.*://'" 2>/dev/null)
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
                    
                # Next.js apps - try .version file, fallback to git
                ai-portal|agent-manager)
                    # Apps are deployed to /srv/apps/{service-name}
                    local app_path="/srv/apps/${service}"
                    
                    # Try .version file first
                    version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                        "root@${container_ip}" \
                        "cat ${app_path}/.version 2>/dev/null" 2>/dev/null)
                    
                    # Fallback: try git
                    if [[ -z "$version" ]]; then
                        version=$(timeout $SSH_TIMEOUT ssh -o ConnectTimeout=$SSH_TIMEOUT -o StrictHostKeyChecking=no \
                            "root@${container_ip}" \
                            "cd ${app_path} && git rev-parse --short HEAD 2>/dev/null" 2>/dev/null)
                    fi
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
    
    # For external services, get expected version from docker-compose.yml (package@config)
    case "$service" in
        milvus)
            local pkg_ver=$(grep -A 1 "milvus:" "${REPO_ROOT}/docker-compose.local.yml" | grep "image:" | sed 's/.*milvus://' || echo "unknown")
            local cfg_ver=$(cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
            echo "${pkg_ver}@${cfg_ver}"
            return
            ;;
        minio)
            local pkg_ver=$(grep "image: minio/minio:latest" "${REPO_ROOT}/docker-compose.local.yml" | head -1 | sed 's/.*://' || echo "unknown")
            local cfg_ver=$(cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
            echo "${pkg_ver}@${cfg_ver}"
            return
            ;;
        litellm)
            local pkg_ver=$(grep "image: ghcr.io/berriai/litellm:" "${REPO_ROOT}/docker-compose.local.yml" | sed 's/.*://' || echo "unknown")
            local cfg_ver=$(cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
            echo "${pkg_ver}@${cfg_ver}"
            return
            ;;
        postgres)
            local pkg_ver=$(grep "image: postgres:" "${REPO_ROOT}/docker-compose.local.yml" | head -1 | sed 's/.*://' || echo "unknown")
            local cfg_ver=$(cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
            echo "${pkg_ver}@${cfg_ver}"
            return
            ;;
        redis)
            local pkg_ver=$(grep "image: redis:" "${REPO_ROOT}/docker-compose.local.yml" | sed 's/.*://' || echo "unknown")
            local cfg_ver=$(cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
            echo "${pkg_ver}@${cfg_ver}"
            return
            ;;
        nginx)
            local pkg_ver=$(grep "image: nginx:" "${REPO_ROOT}/docker-compose.local.yml" | head -1 | sed 's/.*://' || echo "unknown")
            local cfg_ver=$(cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
            echo "${pkg_ver}@${cfg_ver}"
            return
            ;;
    esac
    
    # Handle apps with dashes in their names (ai-portal, agent-manager)
    case "$service" in
        ai-portal)
            if [[ -d "${REPO_ROOT}/../ai-portal/.git" ]]; then
                (cd "${REPO_ROOT}/../ai-portal" && git rev-parse --short HEAD 2>/dev/null) || echo "unknown"
            else
                echo "unknown"
            fi
            return
            ;;
        agent-manager)
            if [[ -d "${REPO_ROOT}/../agent-manager/.git" ]]; then
                (cd "${REPO_ROOT}/../agent-manager" && git rev-parse --short HEAD 2>/dev/null) || echo "unknown"
            else
                echo "unknown"
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
        ai-portal)
            git_dir="${_STATUS_SCRIPT_DIR}/../../../ai-portal"
            ;;
        agent-manager)
            git_dir="${_STATUS_SCRIPT_DIR}/../../../agent-manager"
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
    elif [[ "$deployed" == "unknown" || "$current" == "unknown" ]]; then
        echo "unknown"
    elif [[ "$deployed" == "$current" ]]; then
        echo "synced"
    else
        # For external services with package@config format, check if either part differs
        if [[ "$deployed" == *"@"* && "$current" == *"@"* ]]; then
            local dep_pkg="${deployed%%@*}"
            local dep_cfg="${deployed##*@}"
            local cur_pkg="${current%%@*}"
            local cur_cfg="${current##*@}"
            
            # If either package or config is behind, show as behind
            if [[ "$dep_pkg" != "$cur_pkg" || "$dep_cfg" != "$cur_cfg" ]]; then
                echo "behind"
            else
                echo "synced"
            fi
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
