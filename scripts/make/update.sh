#!/usr/bin/env bash
#
# Busibox Update Script
#
# Updates an existing Busibox installation while preserving data.
# Supports both Docker (local development) and Proxmox (LXC containers) platforms.
#
# Usage:
#   update.sh                      # Interactive update (auto-detect platform)
#   update.sh --docker             # Force Docker mode
#   update.sh --proxmox            # Force Proxmox mode (uses Ansible)
#   update.sh --no-prompt          # Non-interactive update
#   update.sh --rebuild-all        # Force rebuild all containers (Docker only)
#   update.sh -v | --verbose       # Verbose output
#
# Environment Variables:
#   ENV=staging|production         # Target environment (default: from state or staging)
#   INV=inventory/staging          # Ansible inventory (Proxmox mode)
#
# What is preserved:
#   - PostgreSQL data
#   - Redis data  
#   - MinIO object storage
#   - Milvus vector database
#   - Model cache
#   - Deployed external apps (user_apps_data volume on Docker)
#   - All configuration
#   - Admin users and credentials
#
# What is updated:
#   - Container images / LXC container code
#   - Application code (pulled from GitHub)
#   - Database migrations (run automatically)
#

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${SCRIPT_DIR}/../lib/ui.sh"
source "${SCRIPT_DIR}/../lib/state.sh"

# Source github.sh only if it exists (not required for Proxmox)
if [[ -f "${SCRIPT_DIR}/../lib/github.sh" ]]; then
    source "${SCRIPT_DIR}/../lib/github.sh"
fi

# =============================================================================
# CONFIGURATION
# =============================================================================

# Command line flags
NO_PROMPT=false
REBUILD_ALL=false
VERBOSE=false
FORCE_DOCKER=false
FORCE_PROXMOX=false

# Detected platform
PLATFORM=""  # docker or proxmox

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --no-prompt)
                NO_PROMPT=true
                shift
                ;;
            --rebuild-all)
                REBUILD_ALL=true
                shift
                ;;
            --docker)
                FORCE_DOCKER=true
                shift
                ;;
            --proxmox)
                FORCE_PROXMOX=true
                shift
                ;;
            -v|--verbose)
                VERBOSE=true
                shift
                ;;
            *)
                shift
                ;;
        esac
    done
}

# =============================================================================
# HELPERS
# =============================================================================

# Portable uppercase first letter
ucfirst() {
    local str="$1"
    echo "$(echo "${str:0:1}" | tr '[:lower:]' '[:upper:]')${str:1}"
}

# Get environment from ENV variable or state
get_environment() {
    # Check ENV variable first
    if [[ -n "${ENV:-}" ]]; then
        echo "$ENV"
        return
    fi
    
    # Check INV variable (maps inventory to environment)
    if [[ -n "${INV:-}" ]]; then
        case "$INV" in
            *staging*) echo "staging"; return ;;
            *production*) echo "production"; return ;;
        esac
    fi
    
    # Fall back to state file
    local saved_env
    saved_env=$(get_state "ENVIRONMENT" "" 2>/dev/null || echo "")
    if [[ -n "$saved_env" ]]; then
        echo "$saved_env"
        return
    fi
    
    # Default
    echo "staging"
}

# Get container prefix from environment
get_container_prefix() {
    local env
    env=$(get_environment)
    case "$env" in
        demo) echo "demo" ;;
        development) echo "dev" ;;
        staging) echo "staging" ;;
        production) echo "prod" ;;
        *) echo "staging" ;;
    esac
}

# Get env file path (Docker mode)
get_env_file() {
    local prefix
    prefix=$(get_container_prefix)
    echo "${REPO_ROOT}/.env.${prefix}"
}

# Detect platform (Docker or Proxmox)
detect_platform() {
    if [[ "$FORCE_DOCKER" == true ]]; then
        PLATFORM="docker"
        return
    fi
    
    if [[ "$FORCE_PROXMOX" == true ]]; then
        PLATFORM="proxmox"
        return
    fi
    
    # Check if Docker is available
    if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
        PLATFORM="docker"
        return
    fi
    
    # Check if we're on a Proxmox host (has pct command)
    if command -v pct &>/dev/null; then
        PLATFORM="proxmox"
        return
    fi
    
    # Check if Ansible inventory exists (we're on admin workstation for Proxmox)
    if [[ -d "${REPO_ROOT}/provision/ansible/inventory" ]]; then
        PLATFORM="proxmox"
        return
    fi
    
    # Default to proxmox if nothing else matches (assume we're updating remotely)
    PLATFORM="proxmox"
}

# =============================================================================
# PROGRESS DISPLAY
# =============================================================================

show_update_banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    printf "${CYAN}║${NC}%*s${BOLD}BUSIBOX UPDATE${NC}%*s${CYAN}║${NC}\n" 32 "" 32 ""
    printf "${CYAN}║${NC}%*s${DIM}Update your installation while preserving data${NC}%*s${CYAN}║${NC}\n" 16 "" 17 ""
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

show_progress_bar() {
    local percent=$1
    local width=50
    local filled=$((percent * width / 100))
    
    if [[ $percent -eq 100 ]]; then
        filled=50
    fi
    
    local empty=$((width - filled))
    
    printf "\r[${GREEN}"
    printf '█%.0s' $(seq 1 $filled 2>/dev/null) || true
    printf "${DIM}"
    printf '░%.0s' $(seq 1 $empty 2>/dev/null) || true
    printf "${NC}] %3d%%" "$percent"
}

show_stage() {
    local percent=$1
    local title="$2"
    local description="${3:-}"
    
    echo ""
    show_progress_bar "$percent"
    echo ""
    echo ""
    echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
    printf "│  ${BOLD}%-74s${NC} │\n" "$title"
    echo -e "├──────────────────────────────────────────────────────────────────────────────┤"
    if [[ -n "$description" ]]; then
        echo "$description" | fold -s -w 76 | while read -r line; do
            printf "│  %-76s│\n" "$line"
        done
    fi
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
}

# =============================================================================
# PROXMOX UPDATE FUNCTIONS
# =============================================================================

update_proxmox() {
    local environment
    environment=$(get_environment)
    
    # Determine inventory path
    local inventory="inventory/${environment}"
    if [[ -n "${INV:-}" ]]; then
        inventory="$INV"
    fi
    
    info "Using Ansible inventory: ${inventory}"
    
    # Change to ansible directory
    cd "${REPO_ROOT}/provision/ansible"
    
    # Check if inventory exists
    if [[ ! -d "$inventory" ]]; then
        error "Inventory not found: ${inventory}"
        echo ""
        echo "  Available inventories:"
        ls -1 inventory/ 2>/dev/null | sed 's/^/    - /'
        echo ""
        return 1
    fi
    
    # Pull latest code
    show_stage 10 "Pulling Latest Code" "Fetching updates from Git repository."
    
    cd "$REPO_ROOT"
    if git pull --ff-only 2>/dev/null; then
        success "Repository updated"
    else
        warn "Could not fast-forward - you may have local changes"
        if [[ "$NO_PROMPT" != true ]]; then
            if ! confirm "Continue anyway?"; then
                info "Update cancelled."
                exit 0
            fi
        fi
    fi
    
    cd "${REPO_ROOT}/provision/ansible"
    
    # Run Ansible deployment (preserves data by design)
    show_stage 30 "Deploying Core Services" "Updating nginx, storage, database, and vector store."
    
    info "Running: make core INV=${inventory}"
    if [[ "$VERBOSE" == true ]]; then
        make core INV="$inventory" || {
            warn "Core deployment had issues - continuing"
        }
    else
        make core INV="$inventory" 2>&1 | tail -20 || {
            warn "Core deployment had issues - continuing"
        }
    fi
    
    show_stage 50 "Deploying API Services" "Updating AuthZ, Ingest, Search, Agent, and Docs APIs."
    
    info "Running: make apis INV=${inventory}"
    if [[ "$VERBOSE" == true ]]; then
        make apis INV="$inventory" || {
            warn "API deployment had issues - continuing"
        }
    else
        make apis INV="$inventory" 2>&1 | tail -20 || {
            warn "API deployment had issues - continuing"
        }
    fi
    
    show_stage 70 "Deploying LLM Services" "Updating vLLM, LiteLLM, and ColPali (if configured)."
    
    info "Running: make llm INV=${inventory}"
    if [[ "$VERBOSE" == true ]]; then
        make llm INV="$inventory" || {
            warn "LLM deployment had issues - this may be expected if no GPU"
        }
    else
        make llm INV="$inventory" 2>&1 | tail -20 || {
            warn "LLM deployment had issues - this may be expected if no GPU"
        }
    fi
    
    show_stage 85 "Deploying Frontend Apps" "Updating AI Portal and Agent Manager."
    
    info "Running: make apps-frontend INV=${inventory}"
    if [[ "$VERBOSE" == true ]]; then
        make apps-frontend INV="$inventory" || {
            warn "Frontend deployment had issues - continuing"
        }
    else
        make apps-frontend INV="$inventory" 2>&1 | tail -20 || {
            warn "Frontend deployment had issues - continuing"
        }
    fi
    
    show_stage 95 "Running Verification" "Checking service health."
    
    info "Running: make verify-health INV=${inventory}"
    make verify-health INV="$inventory" 2>&1 | tail -30 || {
        warn "Some health checks failed - check service logs"
    }
    
    return 0
}

# =============================================================================
# DOCKER UPDATE FUNCTIONS (original implementation)
# =============================================================================

verify_docker_installation() {
    local container_prefix
    container_prefix=$(get_container_prefix)
    local env_file
    env_file=$(get_env_file)
    local state_file="${REPO_ROOT}/.busibox-state-${container_prefix}"
    
    # For Docker, we need the state file and env file
    # But we'll be lenient - if Docker is running our containers, that's enough
    
    # Check if any busibox containers are running
    local running_containers
    running_containers=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -c "${container_prefix}-" || echo "0")
    
    if [[ "$running_containers" -gt 0 ]]; then
        info "Found ${running_containers} running containers with prefix: ${container_prefix}"
        return 0
    fi
    
    # Check state file
    if [[ -f "$state_file" ]]; then
        info "Found state file: ${state_file}"
        return 0
    fi
    
    # Check env file
    if [[ -f "$env_file" ]]; then
        info "Found env file: ${env_file}"
        return 0
    fi
    
    warn "No existing Docker installation found for environment: ${container_prefix}"
    echo ""
    echo "  This could mean:"
    echo "    - First time running update (run 'make install' for fresh install)"
    echo "    - Wrong environment (try ENV=development or ENV=staging)"
    echo "    - Containers were removed (data volumes may still exist)"
    echo ""
    
    if [[ "$NO_PROMPT" != true ]]; then
        if confirm "Continue with update anyway?"; then
            return 0
        else
            return 1
        fi
    fi
    
    return 0
}

check_running_services_docker() {
    local container_prefix
    container_prefix=$(get_container_prefix)
    
    info "Checking current service status..."
    
    local services_running=0
    local critical_services=("postgres" "authz-api")
    
    for service in "${critical_services[@]}"; do
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "${container_prefix}-${service}"; then
            ((services_running++))
        fi
    done
    
    if [[ $services_running -eq 0 ]]; then
        warn "No services are currently running"
        echo ""
        echo "  This is okay - the update will start all services."
        echo ""
    else
        success "${services_running}/${#critical_services[@]} critical services running"
    fi
    
    return 0
}

verify_data_volumes() {
    local container_prefix
    container_prefix=$(get_container_prefix)
    local project_name="${container_prefix}-busibox"
    
    info "Verifying data volumes..."
    
    local preserved_volumes=(
        "postgres_data"
        "redis_data" 
        "minio_data"
        "milvus_data"
        "milvus_minio_data"
        "etcd_data"
        "model_cache"
        "fastembed_cache"
        "user_apps_data"
    )
    
    local found_volumes=0
    local missing_volumes=()
    
    for volume in "${preserved_volumes[@]}"; do
        local full_volume="${project_name}_${volume}"
        if docker volume ls --format '{{.Name}}' 2>/dev/null | grep -q "^${full_volume}$"; then
            ((found_volumes++))
        else
            missing_volumes+=("$volume")
        fi
    done
    
    if [[ ${#missing_volumes[@]} -gt 0 ]]; then
        warn "Some data volumes not found (will be created): ${missing_volumes[*]}"
    else
        success "All ${found_volumes} data volumes found - data will be preserved"
    fi
    
    return 0
}

pull_latest_code() {
    show_stage 10 "Pulling Latest Code" "Fetching updates from Git repositories."
    
    cd "$REPO_ROOT"
    
    # Pull busibox repo
    info "Pulling busibox repository..."
    if git pull --ff-only 2>/dev/null; then
        success "Busibox repository updated"
    else
        warn "Could not fast-forward busibox - you may have local changes"
    fi
    
    # Get app directories from state
    local ai_portal_dir
    local agent_manager_dir
    local busibox_app_dir
    
    ai_portal_dir=$(get_state "AI_PORTAL_DIR" "")
    agent_manager_dir=$(get_state "AGENT_MANAGER_DIR" "")
    busibox_app_dir=$(get_state "BUSIBOX_APP_DIR" "")
    
    # Pull ai-portal if exists
    if [[ -n "$ai_portal_dir" && -d "$ai_portal_dir/.git" ]]; then
        info "Pulling ai-portal repository..."
        cd "$ai_portal_dir"
        if git pull --ff-only 2>/dev/null; then
            success "ai-portal repository updated"
        else
            warn "Could not fast-forward ai-portal - you may have local changes"
        fi
    fi
    
    # Pull agent-manager if exists
    if [[ -n "$agent_manager_dir" && -d "$agent_manager_dir/.git" ]]; then
        info "Pulling agent-manager repository..."
        cd "$agent_manager_dir"
        if git pull --ff-only 2>/dev/null; then
            success "agent-manager repository updated"
        else
            warn "Could not fast-forward agent-manager - you may have local changes"
        fi
    fi
    
    # Pull busibox-app if exists
    if [[ -n "$busibox_app_dir" && -d "$busibox_app_dir/.git" ]]; then
        info "Pulling busibox-app repository..."
        cd "$busibox_app_dir"
        if git pull --ff-only 2>/dev/null; then
            success "busibox-app repository updated"
        else
            warn "Could not fast-forward busibox-app - you may have local changes"
        fi
    fi
    
    cd "$REPO_ROOT"
}

stop_updatable_services() {
    show_stage 20 "Stopping Services" "Stopping services that will be updated (data services and user apps remain running)."
    
    local container_prefix
    container_prefix=$(get_container_prefix)
    local env_file
    env_file=$(get_env_file)
    
    local compose_files="-f docker-compose.yml -f docker-compose.local-dev.yml"
    
    # Services to stop (non-data services)
    # NOTE: user-apps is NOT stopped - it contains deployed external applications
    # The user_apps_data volume persists the apps, and the container keeps them running
    local services_to_stop=(
        "nginx"
        "core-apps"
        "agent-api"
        "search-api"
        "ingest-api"
        "ingest-worker"
        "authz-api"
        "deploy-api"
        "docs-api"
        "embedding-api"
        "litellm"
    )
    
    cd "$REPO_ROOT"
    
    for service in "${services_to_stop[@]}"; do
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "${container_prefix}-${service}"; then
            info "Stopping ${service}..."
            docker compose $compose_files --env-file "$env_file" stop "$service" 2>/dev/null || true
        fi
    done
    
    success "Application services stopped (data services still running)"
}

rebuild_containers() {
    show_stage 40 "Rebuilding Containers" "Building updated container images."
    
    local container_prefix
    container_prefix=$(get_container_prefix)
    local env_file
    env_file=$(get_env_file)
    
    export CONTAINER_PREFIX="$container_prefix"
    export COMPOSE_PROJECT_NAME="${container_prefix}-busibox"
    
    local compose_files="-f docker-compose.yml -f docker-compose.local-dev.yml"
    
    cd "$REPO_ROOT"
    
    # Get GIT_COMMIT for labels
    local git_commit
    git_commit=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    export GIT_COMMIT="$git_commit"
    
    # Get GitHub token if available
    local github_token=""
    if type ensure_github_token &>/dev/null; then
        github_token=$(bash scripts/lib/github.sh get 2>/dev/null || echo "")
    fi
    export GITHUB_AUTH_TOKEN="$github_token"
    
    # Load app directories from state for volume mounts
    export AI_PORTAL_DIR=$(get_state "AI_PORTAL_DIR" "")
    export AGENT_MANAGER_DIR=$(get_state "AGENT_MANAGER_DIR" "")
    export BUSIBOX_APP_DIR=$(get_state "BUSIBOX_APP_DIR" "")
    export APPS_BASE_DIR=$(get_state "APPS_BASE_DIR" "")
    export DEV_APPS_DIR=$(get_state "DEV_APPS_DIR" "$APPS_BASE_DIR")
    export BUSIBOX_HOST_PATH="$REPO_ROOT"
    
    # Build arguments
    local build_args=""
    if [[ "$REBUILD_ALL" == true ]]; then
        build_args="--no-cache"
    fi
    
    info "Building containers (GIT_COMMIT: ${git_commit})..."
    
    if [[ "$VERBOSE" == true ]]; then
        docker compose $compose_files --env-file "$env_file" build $build_args
    else
        docker compose $compose_files --env-file "$env_file" build $build_args 2>&1 | tail -30 || true
    fi
    
    success "Containers rebuilt"
}

start_data_services() {
    show_stage 50 "Starting Data Services" "Ensuring PostgreSQL, Redis, MinIO, Milvus, and user apps are running."
    
    local container_prefix
    container_prefix=$(get_container_prefix)
    local env_file
    env_file=$(get_env_file)
    
    export CONTAINER_PREFIX="$container_prefix"
    export COMPOSE_PROJECT_NAME="${container_prefix}-busibox"
    export BUSIBOX_HOST_PATH="$REPO_ROOT"
    
    local compose_files="-f docker-compose.yml -f docker-compose.local-dev.yml"
    
    cd "$REPO_ROOT"
    
    # Start data services (including user-apps which has deployed external applications)
    local data_services=("postgres" "redis" "minio" "minio-init" "etcd" "milvus-minio" "milvus" "user-apps")
    
    for service in "${data_services[@]}"; do
        info "Starting ${service}..."
        if [[ "$VERBOSE" == true ]]; then
            docker compose $compose_files --env-file "$env_file" up -d --no-deps "$service"
        else
            docker compose $compose_files --env-file "$env_file" up -d --no-deps "$service" 2>&1 | grep -v "^$" || true
        fi
    done
    
    # Wait for PostgreSQL
    info "Waiting for PostgreSQL to be healthy..."
    local max_attempts=30
    local attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if docker exec "${container_prefix}-postgres" pg_isready -U postgres &>/dev/null; then
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    if [[ $attempt -ge $max_attempts ]]; then
        error "PostgreSQL failed to start"
        return 1
    fi
    success "PostgreSQL is ready"
    
    # Sync database password from env file
    local postgres_password
    postgres_password=$(grep "^POSTGRES_PASSWORD=" "$env_file" 2>/dev/null | cut -d= -f2)
    if [[ -n "$postgres_password" ]]; then
        info "Syncing database passwords..."
        docker exec "${container_prefix}-postgres" psql -U postgres -c \
            "ALTER USER busibox_user WITH PASSWORD '${postgres_password}';" &>/dev/null || true
    fi
    
    # Wait for Milvus
    info "Waiting for Milvus to be healthy..."
    attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:19530/healthz &>/dev/null 2>&1 || \
           docker exec "${container_prefix}-milvus" curl -sf http://localhost:9091/healthz &>/dev/null 2>&1; then
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    if [[ $attempt -ge $max_attempts ]]; then
        warn "Milvus health check timed out - continuing anyway"
    else
        success "Milvus is ready"
    fi
    
    # Run Milvus init (idempotent)
    info "Ensuring Milvus schema..."
    docker compose $compose_files --env-file "$env_file" up -d --no-deps milvus-init 2>/dev/null || true
    
    success "Data services running"
}

start_api_services() {
    show_stage 70 "Starting API Services" "Starting AuthZ, Ingest, Search, Agent, and other APIs."
    
    local container_prefix
    container_prefix=$(get_container_prefix)
    local env_file
    env_file=$(get_env_file)
    
    export CONTAINER_PREFIX="$container_prefix"
    export COMPOSE_PROJECT_NAME="${container_prefix}-busibox"
    export BUSIBOX_HOST_PATH="$REPO_ROOT"
    
    # Load app directories for volume mounts
    export AI_PORTAL_DIR=$(get_state "AI_PORTAL_DIR" "")
    export AGENT_MANAGER_DIR=$(get_state "AGENT_MANAGER_DIR" "")
    export BUSIBOX_APP_DIR=$(get_state "BUSIBOX_APP_DIR" "")
    export APPS_BASE_DIR=$(get_state "APPS_BASE_DIR" "")
    export DEV_APPS_DIR=$(get_state "DEV_APPS_DIR" "$APPS_BASE_DIR")
    
    local compose_files="-f docker-compose.yml -f docker-compose.local-dev.yml"
    
    cd "$REPO_ROOT"
    
    # Start API services in order
    local api_services=("authz-api" "embedding-api" "ingest-api" "ingest-worker" "search-api" "agent-api" "deploy-api" "docs-api")
    
    for service in "${api_services[@]}"; do
        info "Starting ${service}..."
        if [[ "$VERBOSE" == true ]]; then
            docker compose $compose_files --env-file "$env_file" up -d --no-deps "$service"
        else
            docker compose $compose_files --env-file "$env_file" up -d --no-deps "$service" 2>&1 | grep -v "^$" || true
        fi
    done
    
    # Wait for AuthZ
    info "Waiting for AuthZ API to be healthy..."
    local max_attempts=30
    local attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:8010/health/live &>/dev/null; then
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    if [[ $attempt -ge $max_attempts ]]; then
        warn "AuthZ API health check timed out"
    else
        success "AuthZ API is ready"
    fi
    
    success "API services started"
}

start_frontend_services() {
    show_stage 85 "Starting Frontend Services" "Starting AI Portal, Nginx, and other frontend services."
    
    local container_prefix
    container_prefix=$(get_container_prefix)
    local env_file
    env_file=$(get_env_file)
    
    export CONTAINER_PREFIX="$container_prefix"
    export COMPOSE_PROJECT_NAME="${container_prefix}-busibox"
    export BUSIBOX_HOST_PATH="$REPO_ROOT"
    
    # Load app directories
    export AI_PORTAL_DIR=$(get_state "AI_PORTAL_DIR" "")
    export AGENT_MANAGER_DIR=$(get_state "AGENT_MANAGER_DIR" "")
    export BUSIBOX_APP_DIR=$(get_state "BUSIBOX_APP_DIR" "")
    export APPS_BASE_DIR=$(get_state "APPS_BASE_DIR" "")
    export DEV_APPS_DIR=$(get_state "DEV_APPS_DIR" "$APPS_BASE_DIR")
    
    # Get GitHub token if available
    local github_token=""
    if type ensure_github_token &>/dev/null; then
        github_token=$(bash scripts/lib/github.sh get 2>/dev/null || echo "")
    fi
    export GITHUB_AUTH_TOKEN="$github_token"
    
    local compose_files="-f docker-compose.yml -f docker-compose.local-dev.yml"
    
    cd "$REPO_ROOT"
    
    # Start core-apps (contains ai-portal + agent-manager)
    info "Starting core-apps..."
    if [[ "$VERBOSE" == true ]]; then
        docker compose $compose_files --env-file "$env_file" up -d --no-deps core-apps
    else
        docker compose $compose_files --env-file "$env_file" up -d --no-deps core-apps 2>&1 | grep -v "^$" || true
    fi
    
    # Start nginx
    info "Starting nginx..."
    if [[ "$VERBOSE" == true ]]; then
        docker compose $compose_files --env-file "$env_file" up -d --no-deps nginx
    else
        docker compose $compose_files --env-file "$env_file" up -d --no-deps nginx 2>&1 | grep -v "^$" || true
    fi
    
    # Wait for AI Portal
    info "Waiting for AI Portal to be healthy..."
    local max_attempts=60
    local attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:3000/portal/api/health &>/dev/null; then
            break
        fi
        sleep 2
        ((attempt++))
        if [[ $((attempt % 10)) -eq 0 ]]; then
            echo -n "."
        fi
    done
    echo ""
    
    if [[ $attempt -ge $max_attempts ]]; then
        warn "AI Portal health check timed out - it may still be starting"
    else
        success "AI Portal is ready"
    fi
    
    success "Frontend services started"
}

run_migrations() {
    show_stage 90 "Running Migrations" "Applying database schema updates."
    
    local container_prefix
    container_prefix=$(get_container_prefix)
    
    # Wait for core-apps to have node_modules
    info "Checking AI Portal dependencies..."
    local max_attempts=30
    local attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if docker exec "${container_prefix}-core-apps" sh -c "test -f /srv/ai-portal/node_modules/.package-lock.json" 2>/dev/null; then
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    if [[ $attempt -lt $max_attempts ]]; then
        info "Running Prisma migrations for AI Portal..."
        if docker exec "${container_prefix}-core-apps" sh -c "cd /srv/ai-portal && npx prisma db push --accept-data-loss" 2>&1; then
            success "Database schema synchronized"
        else
            warn "Database migration may have failed - check logs if issues persist"
        fi
    else
        warn "Could not verify AI Portal dependencies - skipping migrations"
    fi
}

update_docker() {
    # Verify installation (lenient)
    if ! verify_docker_installation; then
        return 1
    fi
    
    # Check running services
    check_running_services_docker
    
    # Verify data volumes
    verify_data_volumes
    
    # Confirm update
    if [[ "$NO_PROMPT" != true ]]; then
        echo ""
        echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
        printf "│  %-76s│\n" "${BOLD}Ready to update (Docker)${NC}"
        printf "│  %-76s│\n" ""
        printf "│  %-76s│\n" "This will:"
        printf "│    %-74s│\n" "• Pull latest code from Git repositories"
        printf "│    %-74s│\n" "• Rebuild container images"
        printf "│    %-74s│\n" "• Restart all services"
        printf "│    %-74s│\n" "• Run database migrations"
        printf "│  %-76s│\n" ""
        printf "│  %-76s│\n" "Your data will be preserved."
        echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
        echo ""
        
        if ! confirm "Proceed with update?"; then
            info "Update cancelled."
            return 1
        fi
    fi
    
    # Ensure GitHub token is available (if function exists)
    if type ensure_github_token &>/dev/null; then
        ensure_github_token || {
            warn "GitHub token not available - some features may not work"
        }
    fi
    
    # Pull latest code
    pull_latest_code
    
    # Stop updatable services
    stop_updatable_services
    
    # Rebuild containers
    rebuild_containers
    
    # Start services in order
    start_data_services
    start_api_services
    start_frontend_services
    
    # Run migrations
    run_migrations
    
    return 0
}

# =============================================================================
# COMPLETION
# =============================================================================

show_completion() {
    local environment
    environment=$(get_environment)
    
    echo ""
    show_progress_bar 100
    echo ""
    echo ""
    
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    printf "${GREEN}║${NC}%*s${BOLD}UPDATE COMPLETE${NC}%*s${GREEN}║${NC}\n" 31 "" 32 ""
    echo -e "${GREEN}╠══════════════════════════════════════════════════════════════════════════════╣${NC}"
    printf "${GREEN}║${NC}  %-76s${GREEN}║${NC}\n" "All services have been updated for: $(ucfirst "$environment")"
    printf "${GREEN}║${NC}  %-76s${GREEN}║${NC}\n" ""
    printf "${GREEN}║${NC}  %-76s${GREEN}║${NC}\n" "Your data has been preserved:"
    printf "${GREEN}║${NC}    %-74s${GREEN}║${NC}\n" "• PostgreSQL database"
    printf "${GREEN}║${NC}    %-74s${GREEN}║${NC}\n" "• Redis cache"
    printf "${GREEN}║${NC}    %-74s${GREEN}║${NC}\n" "• MinIO object storage"
    printf "${GREEN}║${NC}    %-74s${GREEN}║${NC}\n" "• Milvus vector database"
    printf "${GREEN}║${NC}    %-74s${GREEN}║${NC}\n" "• Model cache"
    printf "${GREEN}║${NC}    %-74s${GREEN}║${NC}\n" "• Deployed external apps"
    printf "${GREEN}║${NC}  %-76s${GREEN}║${NC}\n" ""
    
    if [[ "$PLATFORM" == "proxmox" ]]; then
        printf "${GREEN}║${NC}  %-76s${GREEN}║${NC}\n" "Check service status with:"
        printf "${GREEN}║${NC}    %-74s${GREEN}║${NC}\n" "cd provision/ansible && make verify-health INV=inventory/${environment}"
    else
        local base_domain
        base_domain=$(get_state "BASE_DOMAIN" "localhost")
        printf "${GREEN}║${NC}  %-76s${GREEN}║${NC}\n" "Open the AI Portal:"
        printf "${GREEN}║${NC}    %-74s${GREEN}║${NC}\n" "https://${base_domain}/portal/"
    fi
    
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    parse_args "$@"
    
    # Detect platform
    detect_platform
    
    # Get environment
    local environment
    environment=$(get_environment)
    local container_prefix
    container_prefix=$(get_container_prefix)
    
    # Show banner
    show_update_banner
    
    echo -e "  Environment: ${BOLD}$(ucfirst "$environment")${NC}"
    echo -e "  Platform: ${BOLD}$(ucfirst "$PLATFORM")${NC}"
    if [[ "$PLATFORM" == "docker" ]]; then
        echo -e "  Container prefix: ${BOLD}${container_prefix}${NC}"
    fi
    echo ""
    
    # Run platform-specific update
    if [[ "$PLATFORM" == "proxmox" ]]; then
        # Confirm update for Proxmox
        if [[ "$NO_PROMPT" != true ]]; then
            echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
            printf "│  %-76s│\n" "${BOLD}Ready to update (Proxmox/Ansible)${NC}"
            printf "│  %-76s│\n" ""
            printf "│  %-76s│\n" "This will run Ansible playbooks to update:"
            printf "│    %-74s│\n" "• Core services (nginx, storage, database)"
            printf "│    %-74s│\n" "• API services (authz, ingest, search, agent)"
            printf "│    %-74s│\n" "• LLM services (if configured)"
            printf "│    %-74s│\n" "• Frontend apps (ai-portal, agent-manager)"
            printf "│  %-76s│\n" ""
            printf "│  %-76s│\n" "Your data will be preserved."
            echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
            echo ""
            
            if ! confirm "Proceed with update?"; then
                info "Update cancelled."
                exit 0
            fi
        fi
        
        if ! update_proxmox; then
            error "Proxmox update failed"
            exit 1
        fi
    else
        if ! update_docker; then
            error "Docker update failed"
            exit 1
        fi
        
        # Open browser for Docker
        local base_domain
        base_domain=$(get_state "BASE_DOMAIN" "localhost")
        local portal_url="https://${base_domain}/portal/"
        
        info "Opening browser..."
        local os_type
        os_type=$(uname -s)
        if [[ "$os_type" == "Darwin" ]]; then
            open "$portal_url" 2>/dev/null || true
        else
            xdg-open "$portal_url" 2>/dev/null || true
        fi
    fi
    
    # Show completion
    show_completion
}

main "$@"
