#!/usr/bin/env bash
#
# Busibox Health Check Library
#
# Comprehensive health checks for dependencies, configuration, and services.
# Used by the menu system to determine available actions.
#
# Usage: source "$(dirname "$0")/lib/health.sh"

# Get script directory for sourcing other libraries
_HEALTH_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source state library if not already loaded
if ! type get_environment &>/dev/null; then
    source "${_HEALTH_SCRIPT_DIR}/state.sh"
fi

# Source UI library if not already loaded
if ! type info &>/dev/null; then
    source "${_HEALTH_SCRIPT_DIR}/ui.sh"
fi

# Get repository root
_get_health_repo_root() {
    echo "$(cd "${_HEALTH_SCRIPT_DIR}/../.." && pwd)"
}

REPO_ROOT="$(_get_health_repo_root)"

# ============================================================================
# Dependency Checks
# ============================================================================

# Check if a command exists and get its version
# Usage: check_dependency "docker" "docker --version"
# Returns: 0 if found, 1 if not
# Sets: DEP_VERSION with version string
check_dependency() {
    local cmd="$1"
    local version_cmd="${2:-$cmd --version}"
    
    DEP_VERSION=""
    
    if ! command -v "$cmd" &>/dev/null; then
        return 1
    fi
    
    # Try to get version
    DEP_VERSION=$(eval "$version_cmd" 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || echo "installed")
    return 0
}

# Check all dependencies for a given environment/backend
# Usage: check_all_dependencies "local" "docker"
# Sets: DEPS_OK (0/1), DEPS_MISSING (array), DEPS_FOUND (array)
check_all_dependencies() {
    local env="$1"
    local backend="$2"
    
    DEPS_OK=1
    DEPS_MISSING=()
    DEPS_FOUND=()
    
    # Core dependencies (always needed)
    local core_deps=("bash" "curl" "grep" "sed")
    
    # Environment-specific dependencies
    local env_deps=()
    
    case "$backend" in
        docker)
            env_deps+=("docker:docker --version")
            env_deps+=("docker-compose:docker compose version")
            ;;
        proxmox)
            env_deps+=("ansible:ansible --version")
            env_deps+=("ssh:ssh -V 2>&1")
            ;;
    esac
    
    # Optional but recommended
    local optional_deps=("jq:jq --version" "python3:python3 --version")
    
    # Check core deps
    for dep in "${core_deps[@]}"; do
        if check_dependency "$dep"; then
            DEPS_FOUND+=("$dep:$DEP_VERSION:required")
        else
            DEPS_MISSING+=("$dep:required")
            DEPS_OK=0
        fi
    done
    
    # Check environment deps
    for dep_entry in "${env_deps[@]}"; do
        local dep="${dep_entry%%:*}"
        local version_cmd="${dep_entry#*:}"
        if check_dependency "$dep" "$version_cmd"; then
            DEPS_FOUND+=("$dep:$DEP_VERSION:required")
        else
            DEPS_MISSING+=("$dep:required")
            DEPS_OK=0
        fi
    done
    
    # Check optional deps
    for dep_entry in "${optional_deps[@]}"; do
        local dep="${dep_entry%%:*}"
        local version_cmd="${dep_entry#*:}"
        if check_dependency "$dep" "$version_cmd"; then
            DEPS_FOUND+=("$dep:$DEP_VERSION:optional")
        else
            DEPS_MISSING+=("$dep:optional")
            # Don't set DEPS_OK=0 for optional
        fi
    done
}

# ============================================================================
# Configuration Checks
# ============================================================================

# Check if configuration is present for environment
# Usage: check_configuration "local" "docker"
# Sets: CONFIG_OK (0/1), CONFIG_ISSUES (array), CONFIG_FOUND (array)
check_configuration() {
    local env="$1"
    local backend="$2"
    
    CONFIG_OK=1
    CONFIG_ISSUES=()
    CONFIG_FOUND=()
    
    case "$backend" in
        docker)
            # Check .env.local
            if [[ -f "${REPO_ROOT}/.env.local" ]]; then
                CONFIG_FOUND+=(".env.local")
            else
                if [[ -f "${REPO_ROOT}/env.local.example" ]]; then
                    CONFIG_ISSUES+=(".env.local missing (can be created from env.local.example)")
                else
                    CONFIG_ISSUES+=(".env.local missing")
                    CONFIG_OK=0
                fi
            fi
            
            # Check docker-compose file
            if [[ -f "${REPO_ROOT}/docker-compose.local.yml" ]]; then
                CONFIG_FOUND+=("docker-compose.local.yml")
            else
                CONFIG_ISSUES+=("docker-compose.local.yml missing")
                CONFIG_OK=0
            fi
            
            # Check SSL certificates
            if [[ -f "${REPO_ROOT}/ssl/localhost.crt" ]] && [[ -f "${REPO_ROOT}/ssl/localhost.key" ]]; then
                CONFIG_FOUND+=("SSL certificates")
            else
                CONFIG_ISSUES+=("SSL certificates missing (will be auto-generated)")
            fi
            ;;
            
        proxmox)
            # Check Ansible inventory
            local inv_dir="${REPO_ROOT}/provision/ansible/inventory/${env}"
            if [[ -d "$inv_dir" ]]; then
                CONFIG_FOUND+=("Ansible inventory ($env)")
            else
                CONFIG_ISSUES+=("Ansible inventory missing for $env")
                CONFIG_OK=0
            fi
            
            # Check vault
            local vault_file="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.yml"
            if [[ -f "$vault_file" ]]; then
                if head -1 "$vault_file" | grep -q '^\$ANSIBLE_VAULT'; then
                    CONFIG_FOUND+=("Ansible vault (encrypted)")
                else
                    CONFIG_ISSUES+=("Ansible vault not encrypted")
                fi
            else
                CONFIG_ISSUES+=("Ansible vault missing")
                CONFIG_OK=0
            fi
            
            # Check vault password file
            if [[ -f "$HOME/.vault_pass" ]]; then
                CONFIG_FOUND+=("Vault password file")
            else
                CONFIG_ISSUES+=("Vault password file missing (~/.vault_pass)")
            fi
            ;;
    esac
}

# ============================================================================
# Service Health Checks
# ============================================================================

# Check if a service is healthy
# Usage: check_service_health "postgres" "localhost" "5432"
# Returns: 0 if healthy, 1 if not
check_service_health() {
    local service="$1"
    local host="$2"
    local port="$3"
    
    case "$service" in
        postgres)
            # Try to connect to postgres
            if command -v pg_isready &>/dev/null; then
                pg_isready -h "$host" -p "$port" &>/dev/null
            else
                # Fallback to TCP check
                timeout 2 bash -c "echo > /dev/tcp/$host/$port" 2>/dev/null
            fi
            ;;
        redis)
            # Try TCP connection
            timeout 2 bash -c "echo > /dev/tcp/$host/$port" 2>/dev/null
            ;;
        milvus)
            # Check Milvus health endpoint
            curl -sf --connect-timeout 2 "http://${host}:9091/healthz" &>/dev/null
            ;;
        minio)
            # Check MinIO health
            curl -sf --connect-timeout 2 "http://${host}:${port}/minio/health/live" &>/dev/null
            ;;
        authz|authz-api)
            # Check AuthZ API health
            curl -sf --connect-timeout 2 "http://${host}:${port}/health" &>/dev/null
            ;;
        ingest|ingest-api)
            # Check Ingest API health
            curl -sf --connect-timeout 2 "http://${host}:${port}/health" &>/dev/null
            ;;
        search|search-api)
            # Check Search API health
            curl -sf --connect-timeout 2 "http://${host}:${port}/health" &>/dev/null
            ;;
        agent|agent-api)
            # Check Agent API health
            curl -sf --connect-timeout 2 "http://${host}:${port}/health" &>/dev/null
            ;;
        litellm)
            # Check LiteLLM health
            curl -sf --connect-timeout 2 "http://${host}:${port}/health" &>/dev/null
            ;;
        nginx)
            # Check nginx via HTTPS
            curl -sfk --connect-timeout 2 "https://${host}:${port}/" &>/dev/null
            ;;
        *)
            # Generic TCP check
            timeout 2 bash -c "echo > /dev/tcp/$host/$port" 2>/dev/null
            ;;
    esac
}

# Check all services for environment/backend
# Usage: check_all_services "local" "docker"
# Sets: SERVICES_HEALTHY (array), SERVICES_UNHEALTHY (array), SERVICES_OK (0/1)
check_all_services() {
    local env="$1"
    local backend="$2"
    
    SERVICES_HEALTHY=()
    SERVICES_UNHEALTHY=()
    SERVICES_OK=1
    
    # Define services and their endpoints based on backend
    local services=()
    
    case "$backend" in
        docker)
            # Docker services run on localhost
            services=(
                "postgres:localhost:5432"
                "redis:localhost:6379"
                "milvus:localhost:19530"
                "minio:localhost:9000"
                "authz-api:localhost:8010"
                "ingest-api:localhost:8002"
                "search-api:localhost:8003"
                "agent-api:localhost:8000"
                "litellm:localhost:4000"
                "nginx:localhost:443"
            )
            ;;
        proxmox)
            # Proxmox services have different IPs based on environment
            local network_base
            if [[ "$env" == "production" ]]; then
                network_base="10.96.200"
            else
                # staging environment
                network_base="10.96.201"
            fi
            services=(
                "postgres:${network_base}.203:5432"
                "milvus:${network_base}.204:19530"
                "minio:${network_base}.205:9000"
                "authz-api:${network_base}.210:8010"
                "ingest-api:${network_base}.206:8002"
                "search-api:${network_base}.204:8003"
                "agent-api:${network_base}.202:8000"
                "litellm:${network_base}.207:4000"
                "nginx:${network_base}.200:443"
            )
            ;;
    esac
    
    for service_entry in "${services[@]}"; do
        local service="${service_entry%%:*}"
        local rest="${service_entry#*:}"
        local host="${rest%%:*}"
        local port="${rest#*:}"
        
        if check_service_health "$service" "$host" "$port"; then
            SERVICES_HEALTHY+=("$service:$host:$port")
        else
            SERVICES_UNHEALTHY+=("$service:$host:$port")
        fi
    done
    
    # Check if core data services are healthy
    local core_services=("postgres" "redis" "milvus")
    for core in "${core_services[@]}"; do
        local found=0
        for healthy in "${SERVICES_HEALTHY[@]}"; do
            if [[ "$healthy" == "$core:"* ]]; then
                found=1
                break
            fi
        done
        if [[ $found -eq 0 ]]; then
            SERVICES_OK=0
        fi
    done
}

# ============================================================================
# Comprehensive Health Check
# ============================================================================

# Status levels
STATUS_NOT_INSTALLED="not_installed"
STATUS_INSTALLED="installed"
STATUS_CONFIGURED="configured"
STATUS_DEPLOYED="deployed"
STATUS_HEALTHY="healthy"

# Run full health check and determine system status
# Usage: run_health_check "local" "docker"
# Sets: HEALTH_STATUS, all DEPS_*, CONFIG_*, SERVICES_* variables
run_health_check() {
    local env="$1"
    local backend="$2"
    
    # Run all checks
    check_all_dependencies "$env" "$backend"
    check_configuration "$env" "$backend"
    check_all_services "$env" "$backend"
    
    # Determine overall status
    if [[ $DEPS_OK -eq 0 ]]; then
        HEALTH_STATUS="$STATUS_NOT_INSTALLED"
    elif [[ $CONFIG_OK -eq 0 ]]; then
        HEALTH_STATUS="$STATUS_INSTALLED"
    elif [[ ${#SERVICES_HEALTHY[@]} -eq 0 ]]; then
        HEALTH_STATUS="$STATUS_CONFIGURED"
    elif [[ ${#SERVICES_UNHEALTHY[@]} -gt 0 ]]; then
        HEALTH_STATUS="$STATUS_DEPLOYED"
    else
        HEALTH_STATUS="$STATUS_HEALTHY"
    fi
    
    # Update state file
    set_install_status "$HEALTH_STATUS"
}

# Display health check results
display_health_check() {
    local env="$1"
    local backend="$2"
    
    echo ""
    box "System Health Check - $env ($backend)" 70
    echo ""
    
    # Dependencies
    echo -e "  ${BOLD}Dependencies:${NC}"
    for dep_entry in "${DEPS_FOUND[@]}"; do
        local dep="${dep_entry%%:*}"
        local rest="${dep_entry#*:}"
        local version="${rest%%:*}"
        local type="${rest#*:}"
        echo -e "    ${GREEN}✓${NC} $dep ($version)"
    done
    for dep_entry in "${DEPS_MISSING[@]}"; do
        local dep="${dep_entry%%:*}"
        local type="${dep_entry#*:}"
        if [[ "$type" == "required" ]]; then
            echo -e "    ${RED}✗${NC} $dep (not installed)"
        else
            echo -e "    ${YELLOW}○${NC} $dep (optional, not installed)"
        fi
    done
    echo ""
    
    # Configuration
    echo -e "  ${BOLD}Configuration:${NC}"
    for config in "${CONFIG_FOUND[@]}"; do
        echo -e "    ${GREEN}✓${NC} $config"
    done
    for issue in "${CONFIG_ISSUES[@]}"; do
        if [[ "$issue" == *"missing"* ]] && [[ "$issue" != *"can be"* ]]; then
            echo -e "    ${RED}✗${NC} $issue"
        else
            echo -e "    ${YELLOW}○${NC} $issue"
        fi
    done
    echo ""
    
    # Services
    echo -e "  ${BOLD}Services:${NC}"
    for service_entry in "${SERVICES_HEALTHY[@]}"; do
        local service="${service_entry%%:*}"
        local rest="${service_entry#*:}"
        local host="${rest%%:*}"
        local port="${rest#*:}"
        echo -e "    ${GREEN}✓${NC} $service ($host:$port)"
    done
    for service_entry in "${SERVICES_UNHEALTHY[@]}"; do
        local service="${service_entry%%:*}"
        local rest="${service_entry#*:}"
        local host="${rest%%:*}"
        local port="${rest#*:}"
        echo -e "    ${YELLOW}○${NC} $service (not running)"
    done
    echo ""
    
    # Overall status
    separator 70
    echo ""
    case "$HEALTH_STATUS" in
        "$STATUS_NOT_INSTALLED")
            echo -e "  ${BOLD}Status:${NC} ${RED}NOT INSTALLED${NC}"
            echo -e "  ${BOLD}Available actions:${NC} Install"
            ;;
        "$STATUS_INSTALLED")
            echo -e "  ${BOLD}Status:${NC} ${YELLOW}INSTALLED${NC} (needs configuration)"
            echo -e "  ${BOLD}Available actions:${NC} Install, Configure"
            ;;
        "$STATUS_CONFIGURED")
            echo -e "  ${BOLD}Status:${NC} ${YELLOW}CONFIGURED${NC} (services not running)"
            echo -e "  ${BOLD}Available actions:${NC} Install, Configure, Deploy"
            ;;
        "$STATUS_DEPLOYED")
            echo -e "  ${BOLD}Status:${NC} ${CYAN}DEPLOYED${NC} (some services down)"
            echo -e "  ${BOLD}Available actions:${NC} All"
            ;;
        "$STATUS_HEALTHY")
            echo -e "  ${BOLD}Status:${NC} ${GREEN}HEALTHY${NC}"
            echo -e "  ${BOLD}Available actions:${NC} All + Quick Actions"
            ;;
    esac
    echo ""
}

# Quick status check (non-verbose)
# Usage: quick_health_check "local" "docker"
# Returns: status string
quick_health_check() {
    local env="$1"
    local backend="$2"
    
    run_health_check "$env" "$backend"
    echo "$HEALTH_STATUS"
}
