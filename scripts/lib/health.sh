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
            
            # Check vault - can be in inventory group_vars or in secrets role
            # Staging and production share the same secrets vault structure
            local vault_found=0
            local vault_encrypted=0
            
            # Check inventory-level vault first (preferred location)
            local inv_vault_file="${REPO_ROOT}/provision/ansible/inventory/${env}/group_vars/all/vault.yml"
            if [[ -f "$inv_vault_file" ]]; then
                vault_found=1
                if head -1 "$inv_vault_file" | grep -q '^\$ANSIBLE_VAULT'; then
                    vault_encrypted=1
                fi
            fi
            
            # Fallback: check secrets role vault (shared between environments)
            local role_vault_file="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.yml"
            if [[ $vault_found -eq 0 ]] && [[ -f "$role_vault_file" ]]; then
                vault_found=1
                if head -1 "$role_vault_file" | grep -q '^\$ANSIBLE_VAULT'; then
                    vault_encrypted=1
                fi
            fi
            
            if [[ $vault_found -eq 1 ]]; then
                if [[ $vault_encrypted -eq 1 ]]; then
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

# Quick TCP port check with very short timeout
# Usage: quick_port_check "localhost" "5432"
# Returns: 0 if port is open, 1 if not
quick_port_check() {
    local host="$1"
    local port="$2"
    
    # Use nc (netcat) if available for faster check
    if command -v nc &>/dev/null; then
        nc -z -w 1 "$host" "$port" 2>/dev/null
    else
        # Fallback to bash /dev/tcp with short timeout
        timeout 1 bash -c "echo > /dev/tcp/$host/$port" 2>/dev/null
    fi
}

# Check if a service is healthy
# Usage: check_service_health "postgres" "localhost" "5432"
# Returns: 0 if healthy, 1 if not
check_service_health() {
    local service="$1"
    local host="$2"
    local port="$3"
    
    # Fast path: just check if port is open (1 second timeout)
    quick_port_check "$host" "$port"
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
                "docs-api:localhost:8004"
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
                "docs-api:${network_base}.201:8004"
                "litellm:${network_base}.207:4000"
                "nginx:${network_base}.200:443"
            )
            # Note: docs-api runs on apps container (.201)
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

# Docker-specific status levels (for local environment)
STATUS_DOCKER_NOT_RUNNING="docker_not_running"
STATUS_CONTAINERS_NOT_RUNNING="containers_not_running"

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

# Run a minimal/fast health check for initial menu load
# Only checks dependencies and config, skips service health checks
# Usage: run_quick_health_check "local" "docker"
run_quick_health_check() {
    local env="$1"
    local backend="$2"
    
    # Only check dependencies and config - skip service checks for speed
    check_all_dependencies "$env" "$backend"
    check_configuration "$env" "$backend"
    
    # Quick service check - just check if Docker is running containers
    SERVICES_HEALTHY=()
    SERVICES_UNHEALTHY=()
    SERVICES_OK=1
    
    # Track Docker-specific state for local environment
    DOCKER_DAEMON_RUNNING=0
    DOCKER_CONTAINERS_EXIST=0
    DOCKER_CONTAINERS_RUNNING=0
    
    if [[ "$backend" == "docker" ]]; then
        # Check if Docker daemon is running
        if docker info &>/dev/null 2>&1; then
            DOCKER_DAEMON_RUNNING=1
            
            # Check if busibox containers exist (created but maybe not running)
            local existing=$(docker compose -f "${REPO_ROOT}/docker-compose.local.yml" ps -q 2>/dev/null | wc -l | tr -d ' ')
            if [[ "$existing" -gt 0 ]]; then
                DOCKER_CONTAINERS_EXIST=1
                
                # Check if any are running
                local running=$(docker compose -f "${REPO_ROOT}/docker-compose.local.yml" ps -q --status running 2>/dev/null | wc -l | tr -d ' ')
                if [[ "$running" -gt 0 ]]; then
                    DOCKER_CONTAINERS_RUNNING=1
                    SERVICES_HEALTHY+=("docker:$running containers running")
                    SERVICES_OK=1
                fi
            fi
        fi
    else
        # For Proxmox, just check if we can reach the network
        local network_base
        if [[ "$env" == "production" ]]; then
            network_base="10.96.200"
        else
            network_base="10.96.201"
        fi
        # Quick ping check to proxy (gateway)
        if ping -c 1 -W 1 "${network_base}.200" &>/dev/null; then
            SERVICES_HEALTHY+=("network:reachable")
            SERVICES_OK=1
        fi
    fi
    
    # Determine status based on deps, config, and Docker state
    if [[ $DEPS_OK -eq 0 ]]; then
        HEALTH_STATUS="$STATUS_NOT_INSTALLED"
    elif [[ $CONFIG_OK -eq 0 ]]; then
        HEALTH_STATUS="$STATUS_INSTALLED"
    elif [[ "$backend" == "docker" ]]; then
        # Docker-specific status logic
        if [[ $DOCKER_DAEMON_RUNNING -eq 0 ]]; then
            HEALTH_STATUS="$STATUS_DOCKER_NOT_RUNNING"
        elif [[ $DOCKER_CONTAINERS_RUNNING -eq 0 ]]; then
            HEALTH_STATUS="$STATUS_CONTAINERS_NOT_RUNNING"
        else
            HEALTH_STATUS="$STATUS_DEPLOYED"
        fi
    elif [[ ${#SERVICES_HEALTHY[@]} -eq 0 ]]; then
        HEALTH_STATUS="$STATUS_CONFIGURED"
    else
        HEALTH_STATUS="$STATUS_DEPLOYED"
    fi
    
    # Update state file
    set_install_status "$HEALTH_STATUS"
}
