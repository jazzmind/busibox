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

# Source vault.sh for secure secret access
if [[ -f "${SCRIPT_DIR}/../lib/vault.sh" ]]; then
    source "${SCRIPT_DIR}/../lib/vault.sh"
fi

# Source github.sh only if it exists (not required for Proxmox)
if [[ -f "${SCRIPT_DIR}/../lib/github.sh" ]]; then
    source "${SCRIPT_DIR}/../lib/github.sh"
fi

# Source versions.sh for version tracking
if [[ -f "${SCRIPT_DIR}/../lib/versions.sh" ]]; then
    source "${SCRIPT_DIR}/../lib/versions.sh"
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

# Normalize environment name
# Maps legacy names to current names (test -> staging)
normalize_environment() {
    local env="$1"
    case "$env" in
        test) echo "staging" ;;  # Legacy name
        prod) echo "production" ;;
        *) echo "$env" ;;
    esac
}

# Get environment from ENV variable or state
# Valid environments for Proxmox: staging, production
# Valid environments for Docker: staging, production, development, demo
# DEFAULT: staging (if nothing else specified)
get_environment() {
    local env=""
    
    # Check ENV variable first (highest priority)
    if [[ -n "${ENV:-}" ]]; then
        env=$(normalize_environment "$ENV")
        echo "$env"
        return
    fi
    
    # Check INV variable (maps inventory to environment)
    if [[ -n "${INV:-}" ]]; then
        case "$INV" in
            *staging*|*test*) echo "staging"; return ;;
            *production*) echo "production"; return ;;
            *local*) echo "development"; return ;;
        esac
    fi
    
    # Check state file for saved environment
    local saved_env
    saved_env=$(get_state "ENVIRONMENT" "" 2>/dev/null || echo "")
    if [[ -n "$saved_env" ]]; then
        echo "$(normalize_environment "$saved_env")"
        return
    fi
    
    # DEFAULT: staging for all platforms
    # This ensures a safe default that doesn't accidentally affect production
    echo "staging"
}

# Get container prefix from environment
# For Docker: demo, dev, staging, prod
# For Proxmox: staging, prod (maps to LXC container names)
get_container_prefix() {
    local env
    env=$(get_environment)
    case "$env" in
        demo) echo "demo" ;;
        development) echo "dev" ;;
        staging) echo "staging" ;;
        production) echo "prod" ;;
        *) echo "staging" ;;  # Default to staging
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
# BOX DRAWING UTILITIES
# =============================================================================

# Standard box width (inner content width, not including borders)
BOX_WIDTH=78

# Define ANSI codes using $'...' syntax for proper escape interpretation
# These will work correctly with printf %s (no need for %b)
_BOLD=$'\033[1m'
_DIM=$'\033[2m'
_NC=$'\033[0m'
_CYAN=$'\033[0;36m'
_GREEN=$'\033[0;32m'
_RED=$'\033[0;31m'
_YELLOW=$'\033[1;33m'

# Strip ANSI codes from a string for length calculation
strip_ansi() {
    printf '%s' "$1" | sed $'s/\033\\[[0-9;]*m//g'
}

# Get visible length of a string (excluding ANSI codes)
visible_length() {
    local stripped
    stripped=$(strip_ansi "$1")
    printf '%d' "${#stripped}"
}

# Print a horizontal border line
# Usage: box_border "top" | "middle" | "bottom" [color]
box_border() {
    local type="${1:-top}"
    local color="${2:-$_CYAN}"
    local line=""
    
    # Build the line of ═ characters
    for ((i=0; i<BOX_WIDTH; i++)); do
        line+="═"
    done
    
    case "$type" in
        top)    printf '%s╔%s╗%s\n' "$color" "$line" "$_NC" ;;
        middle) printf '%s╠%s╣%s\n' "$color" "$line" "$_NC" ;;
        bottom) printf '%s╚%s╝%s\n' "$color" "$line" "$_NC" ;;
    esac
}

# Print a simple box border (single line)
# Usage: simple_border "top" | "bottom"
simple_border() {
    local type="${1:-top}"
    local line=""
    
    for ((i=0; i<BOX_WIDTH; i++)); do
        line+="─"
    done
    
    case "$type" in
        top)    printf '┌%s┐\n' "$line" ;;
        bottom) printf '└%s┘\n' "$line" ;;
    esac
}

# Print a box line with text
# Usage: box_line "text" [align] [color] [indent]
#   align: "left" (default), "center"
#   color: border color (default: cyan)
#   indent: number of spaces to indent (default: 0, use 2 for content, 4 for bullets)
box_line() {
    local text="$1"
    local align="${2:-left}"
    local color="${3:-$_CYAN}"
    local indent="${4:-0}"
    
    # Calculate visible text length (excluding ANSI codes)
    local visible_len
    visible_len=$(visible_length "$text")
    
    # Add indent to visible length for padding calculation
    local content_len=$((visible_len + indent))
    
    # Calculate padding
    local total_padding=$((BOX_WIDTH - content_len))
    
    # Build indent spaces
    local indent_spaces=""
    for ((i=0; i<indent; i++)); do indent_spaces+=" "; done
    
    # Build padding spaces
    if [[ "$align" == "center" ]]; then
        local left_pad=$((total_padding / 2))
        local right_pad=$((total_padding - left_pad))
        local left_spaces="" right_spaces=""
        for ((i=0; i<left_pad; i++)); do left_spaces+=" "; done
        for ((i=0; i<right_pad; i++)); do right_spaces+=" "; done
        printf '%s║%s%s%s%s%s║%s\n' "$color" "$_NC" "$left_spaces" "$text" "$right_spaces" "$color" "$_NC"
    else
        # Left align with optional indent
        local right_spaces=""
        for ((i=0; i<total_padding; i++)); do right_spaces+=" "; done
        printf '%s║%s%s%s%s%s║%s\n' "$color" "$_NC" "$indent_spaces" "$text" "$right_spaces" "$color" "$_NC"
    fi
}

# Print an empty box line
# Usage: box_empty [color]
box_empty() {
    local color="${1:-$_CYAN}"
    local spaces=""
    for ((i=0; i<BOX_WIDTH; i++)); do
        spaces+=" "
    done
    printf '%s║%s%s%s║%s\n' "$color" "$_NC" "$spaces" "$color" "$_NC"
}

# Print a simple box line (single border)
# Usage: simple_line "text" [indent]
simple_line() {
    local text="$1"
    local indent="${2:-2}"
    
    local visible_len
    visible_len=$(visible_length "$text")
    local content_len=$((visible_len + indent))
    local right_pad=$((BOX_WIDTH - content_len))
    
    # Build indent and padding spaces
    local indent_spaces="" right_spaces=""
    for ((i=0; i<indent; i++)); do indent_spaces+=" "; done
    for ((i=0; i<right_pad; i++)); do right_spaces+=" "; done
    
    printf '│%s%s%s│\n' "$indent_spaces" "$text" "$right_spaces"
}

# Print a simple empty line
simple_empty() {
    local spaces=""
    for ((i=0; i<BOX_WIDTH; i++)); do
        spaces+=" "
    done
    printf '│%s│\n' "$spaces"
}

# =============================================================================
# PROGRESS DISPLAY
# =============================================================================

show_update_banner() {
    echo ""
    box_border "top" "$_CYAN"
    box_line "${_BOLD}BUSIBOX UPDATE${_NC}" "center" "$_CYAN"
    box_line "${_DIM}Update your installation while preserving data${_NC}" "center" "$_CYAN"
    box_border "bottom" "$_CYAN"
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
    
    printf "\r["
    printf '%s' "$_GREEN"
    for ((i=0; i<filled; i++)); do printf '█'; done
    printf '%s' "$_DIM"
    for ((i=0; i<empty; i++)); do printf '░'; done
    printf '%s] %3d%%' "$_NC" "$percent"
}

show_stage() {
    local percent=$1
    local title="$2"
    local description="${3:-}"
    
    echo ""
    show_progress_bar "$percent"
    echo ""
    echo ""
    simple_border "top"
    simple_line "${_BOLD}${title}${_NC}" 2
    if [[ -n "$description" ]]; then
        # Use fold to wrap long descriptions, then print each line
        echo "$description" | fold -s -w $((BOX_WIDTH - 4)) | while IFS= read -r line; do
            simple_line "$line" 2
        done
    fi
    simple_border "bottom"
}

# =============================================================================
# PROXMOX UPDATE FUNCTIONS
# =============================================================================

# Check for missing containers in Proxmox mode
# Uses the staging inventory to determine expected containers
check_missing_containers_proxmox() {
    local environment
    environment=$(get_environment)
    
    # Only check on Proxmox host (has pct command)
    if ! command -v pct &>/dev/null; then
        # We're on admin workstation, can't check container existence
        return 0
    fi
    
    info "Checking for expected LXC containers..."
    
    # Expected containers for staging (STAGE-) or production (no prefix)
    local prefix=""
    local expected_containers=()
    
    if [[ "$environment" == "staging" ]]; then
        prefix="STAGE-"
        expected_containers=(
            "300:${prefix}proxy-lxc"
            "301:${prefix}core-apps-lxc"
            "302:${prefix}agent-lxc"
            "303:${prefix}pg-lxc"
            "304:${prefix}milvus-lxc"
            "305:${prefix}files-lxc"
            "306:${prefix}ingest-lxc"
            "307:${prefix}litellm-lxc"
            "310:${prefix}authz-lxc"
            "312:${prefix}user-apps-lxc"
        )
    else
        # Production
        expected_containers=(
            "200:proxy-lxc"
            "201:core-apps-lxc"
            "202:agent-lxc"
            "203:pg-lxc"
            "204:milvus-lxc"
            "205:files-lxc"
            "206:ingest-lxc"
            "207:litellm-lxc"
            "210:authz-lxc"
            "212:user-apps-lxc"
        )
    fi
    
    local missing=()
    local existing=()
    
    for entry in "${expected_containers[@]}"; do
        local ctid="${entry%%:*}"
        local name="${entry##*:}"
        
        if pct status "$ctid" &>/dev/null; then
            existing+=("$name ($ctid)")
        else
            missing+=("$name ($ctid)")
        fi
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        warn "Missing containers:"
        for m in "${missing[@]}"; do
            echo "  - $m"
        done
        echo ""
        echo "  These containers need to be created before deployment."
        echo ""
        
        if [[ "$NO_PROMPT" != true ]]; then
            echo "  To create missing containers, run:"
            echo "    cd provision/pct/containers"
            echo "    bash create_lxc_base.sh ${environment}"
            echo ""
            
            if ! confirm "Continue with update anyway? (some services will fail)"; then
                return 1
            fi
        else
            warn "Continuing with missing containers - some services may fail"
        fi
    else
        success "All ${#existing[@]} expected containers found"
    fi
    
    return 0
}

update_proxmox() {
    local environment
    environment=$(get_environment)
    
    # Ensure vault access (for Proxmox, we always need vault)
    if type ensure_vault_access &>/dev/null; then
        show_stage 5 "Vault Access" "Checking Ansible vault configuration."
        
        if ! ensure_vault_access; then
            error "Cannot access vault. Update requires vault secrets."
            echo ""
            echo "  The vault contains secrets needed by Ansible:"
            echo "    - Database passwords"
            echo "    - Auth secrets (JWT, session keys)"
            echo "    - MinIO credentials"
            echo ""
            echo "  To set up vault:"
            echo "    1. Copy example: cp provision/ansible/roles/secrets/vars/vault.example.yml provision/ansible/roles/secrets/vars/vault.yml"
            echo "    2. Edit with your secrets"
            echo "    3. Encrypt: ansible-vault encrypt provision/ansible/roles/secrets/vars/vault.yml"
            return 1
        fi
    fi
    
    # Change to ansible directory FIRST (inventory paths are relative to this)
    cd "${REPO_ROOT}/provision/ansible"
    
    # Determine inventory path (relative to provision/ansible/)
    local inventory="inventory/${environment}"
    if [[ -n "${INV:-}" ]]; then
        # INV can be "staging", "inventory/staging", etc.
        # Normalize to inventory/<env> format
        case "$INV" in
            inventory/*) inventory="$INV" ;;
            *) inventory="inventory/$INV" ;;
        esac
    fi
    
    info "Using Ansible inventory: ${inventory}"
    
    # Check if inventory exists
    if [[ ! -d "$inventory" ]]; then
        error "Inventory not found: ${inventory}"
        echo ""
        echo "  Available inventories:"
        ls -1 inventory/ 2>/dev/null | sed 's/^/    - /'
        echo ""
        return 1
    fi
    
    # Check for missing containers (on Proxmox host)
    cd "$REPO_ROOT"
    if ! check_missing_containers_proxmox; then
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
        simple_border "top"
        simple_line "${_BOLD}Ready to update (Docker)${_NC}" 2
        simple_empty
        simple_line "This will:" 2
        simple_line "• Pull latest code from Git repositories" 4
        simple_line "• Rebuild container images" 4
        simple_line "• Restart all services" 4
        simple_line "• Run database migrations" 4
        simple_empty
        simple_line "Your data will be preserved." 2
        simple_border "bottom"
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

# Save deployed versions after successful update
save_all_deployed_versions() {
    info "Recording deployed versions..."
    
    # Save busibox version
    cd "$REPO_ROOT"
    if [[ -d ".git" ]]; then
        local busibox_commit busibox_branch busibox_tag
        busibox_commit=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        busibox_tag=$(git describe --tags --exact-match 2>/dev/null || true)
        
        if [[ -n "$busibox_tag" ]]; then
            save_deployed_version "busibox" "release" "$busibox_tag" "$busibox_commit"
        else
            busibox_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
            save_deployed_version "busibox" "branch" "$busibox_branch" "$busibox_commit"
        fi
    fi
    
    # Save ai-portal version
    local ai_portal_dir
    ai_portal_dir=$(get_state "AI_PORTAL_DIR" "")
    if [[ -n "$ai_portal_dir" ]] && [[ -d "$ai_portal_dir/.git" ]]; then
        cd "$ai_portal_dir"
        local ap_commit ap_branch ap_tag
        ap_commit=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        ap_tag=$(git describe --tags --exact-match 2>/dev/null || true)
        
        if [[ -n "$ap_tag" ]]; then
            save_deployed_version "ai-portal" "release" "$ap_tag" "$ap_commit"
        else
            ap_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
            save_deployed_version "ai-portal" "branch" "$ap_branch" "$ap_commit"
        fi
    fi
    
    # Save agent-manager version
    local agent_manager_dir
    agent_manager_dir=$(get_state "AGENT_MANAGER_DIR" "")
    if [[ -n "$agent_manager_dir" ]] && [[ -d "$agent_manager_dir/.git" ]]; then
        cd "$agent_manager_dir"
        local am_commit am_branch am_tag
        am_commit=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        am_tag=$(git describe --tags --exact-match 2>/dev/null || true)
        
        if [[ -n "$am_tag" ]]; then
            save_deployed_version "agent-manager" "release" "$am_tag" "$am_commit"
        else
            am_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
            save_deployed_version "agent-manager" "branch" "$am_branch" "$am_commit"
        fi
    fi
    
    # Save busibox-app version
    local busibox_app_dir
    busibox_app_dir=$(get_state "BUSIBOX_APP_DIR" "")
    if [[ -n "$busibox_app_dir" ]] && [[ -d "$busibox_app_dir/.git" ]]; then
        cd "$busibox_app_dir"
        local ba_commit ba_branch ba_tag
        ba_commit=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        ba_tag=$(git describe --tags --exact-match 2>/dev/null || true)
        
        if [[ -n "$ba_tag" ]]; then
            save_deployed_version "busibox-app" "release" "$ba_tag" "$ba_commit"
        else
            ba_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
            save_deployed_version "busibox-app" "branch" "$ba_branch" "$ba_commit"
        fi
    fi
    
    cd "$REPO_ROOT"
    success "Deployed versions recorded"
}

show_completion() {
    local environment
    environment=$(get_environment)
    
    echo ""
    show_progress_bar 100
    echo ""
    echo ""
    
    box_border "top" "$_GREEN"
    box_line "${_BOLD}UPDATE COMPLETE${_NC}" "center" "$_GREEN"
    box_border "middle" "$_GREEN"
    box_line "All services have been updated for: $(ucfirst "$environment")" "left" "$_GREEN" 2
    box_empty "$_GREEN"
    box_line "Your data has been preserved:" "left" "$_GREEN" 2
    box_line "• PostgreSQL database" "left" "$_GREEN" 4
    box_line "• Redis cache" "left" "$_GREEN" 4
    box_line "• MinIO object storage" "left" "$_GREEN" 4
    box_line "• Milvus vector database" "left" "$_GREEN" 4
    box_line "• Model cache" "left" "$_GREEN" 4
    box_line "• Deployed external apps" "left" "$_GREEN" 4
    box_empty "$_GREEN"
    
    if [[ "$PLATFORM" == "proxmox" ]]; then
        box_line "Check service status with:" "left" "$_GREEN" 2
        box_line "cd provision/ansible && make verify-health INV=inventory/${environment}" "left" "$_GREEN" 4
    else
        local base_domain
        base_domain=$(get_state "BASE_DOMAIN" "localhost")
        box_line "Open the AI Portal:" "left" "$_GREEN" 2
        box_line "https://${base_domain}/portal/" "left" "$_GREEN" 4
    fi
    
    box_border "bottom" "$_GREEN"
    echo ""
}

# =============================================================================
# INTERACTIVE MENU
# =============================================================================

# Show the main update menu
show_update_menu() {
    local environment
    environment=$(get_environment)
    
    clear
    show_update_banner
    
    printf '  Environment: %s%s%s\n' "$_BOLD" "$(ucfirst "$environment")" "$_NC"
    printf '  Platform: %s%s%s\n' "$_BOLD" "$(ucfirst "$PLATFORM")" "$_NC"
    echo ""
    
    echo -e "  ${_BOLD}Main Menu:${_NC}"
    echo -e "    ${_CYAN}1)${_NC} Change environment (currently: ${environment})"
    echo -e "    ${_CYAN}2)${_NC} What needs to be updated?"
    echo -e "    ${_CYAN}3)${_NC} Run update"
    echo -e "    ${_CYAN}4)${_NC} Exit"
    echo ""
    
    while true; do
        echo -ne "  ${_BOLD}Select option [1-4]:${_NC} "
        read -r choice
        
        case "$choice" in
            1) return 1 ;;  # Change environment
            2) return 2 ;;  # What needs updating
            3) return 3 ;;  # Run update
            4) return 0 ;;  # Exit
            *) echo -e "  ${_RED}Invalid selection${_NC}" ;;
        esac
    done
}

# Show environment selection menu
select_environment_menu() {
    echo ""
    simple_border "top"
    simple_line "${_BOLD}Select Environment${_NC}" 2
    simple_border "bottom"
    echo ""
    
    echo -e "    ${_CYAN}1)${_NC} Staging     ${_DIM}(10.96.201.x network)${_NC}"
    echo -e "    ${_CYAN}2)${_NC} Production  ${_DIM}(10.96.200.x network)${_NC}"
    if [[ "$PLATFORM" == "docker" ]]; then
        echo -e "    ${_CYAN}3)${_NC} Development ${_DIM}(Docker dev mode)${_NC}"
        echo -e "    ${_CYAN}4)${_NC} Demo        ${_DIM}(Docker prod mode)${_NC}"
    fi
    echo -e "    ${_CYAN}0)${_NC} Cancel"
    echo ""
    
    while true; do
        echo -ne "  ${_BOLD}Select [0-4]:${_NC} "
        read -r choice
        
        case "$choice" in
            0) return ;;
            1) 
                ENV="staging"
                export ENV
                set_state "ENVIRONMENT" "staging"
                success "Environment set to: staging"
                ;;
            2) 
                ENV="production"
                export ENV
                set_state "ENVIRONMENT" "production"
                success "Environment set to: production"
                ;;
            3)
                if [[ "$PLATFORM" == "docker" ]]; then
                    ENV="development"
                    export ENV
                    set_state "ENVIRONMENT" "development"
                    success "Environment set to: development"
                else
                    echo -e "  ${_RED}Invalid selection${_NC}"
                    continue
                fi
                ;;
            4)
                if [[ "$PLATFORM" == "docker" ]]; then
                    ENV="demo"
                    export ENV
                    set_state "ENVIRONMENT" "demo"
                    success "Environment set to: demo"
                else
                    echo -e "  ${_RED}Invalid selection${_NC}"
                    continue
                fi
                ;;
            *) 
                echo -e "  ${_RED}Invalid selection${_NC}"
                continue
                ;;
        esac
        break
    done
    
    sleep 1
}

# Show what needs to be updated
show_update_status() {
    echo ""
    simple_border "top"
    simple_line "${_BOLD}Update Status${_NC}" 2
    simple_border "bottom"
    
    # Check if versions.sh is loaded
    if ! type get_update_summary &>/dev/null; then
        echo ""
        echo -e "  ${_YELLOW}Version tracking not available${_NC}"
        echo -e "  ${_DIM}Install jq for version comparison: brew install jq${_NC}"
        echo ""
    else
        # Ensure GitHub token for API access
        if ! _get_github_token &>/dev/null; then
            echo ""
            echo -e "  ${_YELLOW}GitHub token required for version checking${_NC}"
            echo ""
            if type ensure_github_token &>/dev/null; then
                ensure_github_token || {
                    echo -e "  ${_RED}Could not get GitHub token${_NC}"
                    pause "Press any key to continue..."
                    return
                }
            fi
        fi
        
        # Show update summary
        info "Checking for updates..."
        get_update_summary
    fi
    
    pause "Press any key to continue..."
}

# Select version for a specific repository
# Usage: select_repo_version "busibox"
# Sets: SELECTED_VERSION_TYPE, SELECTED_VERSION_REF
select_repo_version() {
    local repo_key="$1"
    local repo="${TRACKED_REPOS[$repo_key]:-}"
    
    if [[ -z "$repo" ]]; then
        warn "Unknown repository: $repo_key"
        return 1
    fi
    
    echo ""
    simple_border "top"
    simple_line "${_BOLD}Select version for: ${repo_key}${_NC}" 2
    simple_border "bottom"
    echo ""
    
    # Get current deployed version
    local deployed
    deployed=$(get_deployed_version "$repo_key")
    if [[ -n "$deployed" ]]; then
        local type ref commit
        type=$(parse_deployed_version "$deployed" "type")
        ref=$(parse_deployed_version "$deployed" "ref")
        commit=$(parse_deployed_version "$deployed" "commit")
        echo -e "  ${_DIM}Currently deployed: ${type}:${ref}@${commit}${_NC}"
        echo ""
    fi
    
    # Get default branch
    local default_branch="${DEFAULT_BRANCHES[$repo_key]:-main}"
    
    # Get latest on default branch
    local branch_head=""
    branch_head=$(get_branch_head "$repo_key" "$default_branch" 2>/dev/null) || true
    
    # Get latest release
    local latest_release=""
    latest_release=$(get_latest_release "$repo_key" 2>/dev/null) || true
    
    echo -e "    ${_CYAN}1)${_NC} Latest on branch: ${default_branch}"
    if [[ -n "$branch_head" ]]; then
        echo -e "       ${_DIM}(commit: ${branch_head})${_NC}"
    fi
    
    if [[ -n "$latest_release" ]]; then
        echo -e "    ${_CYAN}2)${_NC} Latest release: ${latest_release}"
    else
        echo -e "    ${_DIM}2) No releases available${_NC}"
    fi
    
    echo -e "    ${_CYAN}3)${_NC} Specific release..."
    echo -e "    ${_CYAN}4)${_NC} Specific branch..."
    echo -e "    ${_CYAN}5)${_NC} Skip this repo"
    echo ""
    
    while true; do
        echo -ne "  ${_BOLD}Select [1-5]:${_NC} "
        read -r choice
        
        case "$choice" in
            1)
                SELECTED_VERSION_TYPE="branch"
                SELECTED_VERSION_REF="$default_branch"
                return 0
                ;;
            2)
                if [[ -n "$latest_release" ]]; then
                    SELECTED_VERSION_TYPE="release"
                    SELECTED_VERSION_REF="$latest_release"
                    return 0
                else
                    echo -e "  ${_RED}No releases available${_NC}"
                    continue
                fi
                ;;
            3)
                # Show list of releases
                echo ""
                info "Fetching releases..."
                local releases
                releases=$(get_release_options "$repo_key" 10 2>/dev/null) || true
                
                if [[ -z "$releases" ]]; then
                    echo -e "  ${_RED}No releases found${_NC}"
                    continue
                fi
                
                echo ""
                echo -e "  ${_BOLD}Available releases:${_NC}"
                local i=1
                local release_array=()
                while IFS= read -r release; do
                    echo -e "    ${_CYAN}${i})${_NC} ${release}"
                    release_array+=("$release")
                    ((i++))
                done <<< "$releases"
                echo -e "    ${_CYAN}0)${_NC} Cancel"
                echo ""
                
                echo -ne "  ${_BOLD}Select release:${_NC} "
                read -r rel_choice
                
                if [[ "$rel_choice" == "0" ]]; then
                    continue
                fi
                
                if [[ "$rel_choice" =~ ^[0-9]+$ ]] && [[ "$rel_choice" -ge 1 ]] && [[ "$rel_choice" -le "${#release_array[@]}" ]]; then
                    SELECTED_VERSION_TYPE="release"
                    SELECTED_VERSION_REF="${release_array[$((rel_choice-1))]}"
                    return 0
                else
                    echo -e "  ${_RED}Invalid selection${_NC}"
                fi
                ;;
            4)
                # Show list of branches
                echo ""
                info "Fetching branches..."
                local branches
                branches=$(get_branch_options "$repo_key" 10 2>/dev/null) || true
                
                if [[ -z "$branches" ]]; then
                    echo -e "  ${_RED}No branches found${_NC}"
                    continue
                fi
                
                echo ""
                echo -e "  ${_BOLD}Available branches:${_NC}"
                local i=1
                local branch_array=()
                while IFS= read -r branch; do
                    echo -e "    ${_CYAN}${i})${_NC} ${branch}"
                    branch_array+=("$branch")
                    ((i++))
                done <<< "$branches"
                echo -e "    ${_CYAN}0)${_NC} Cancel"
                echo ""
                
                echo -ne "  ${_BOLD}Select branch:${_NC} "
                read -r br_choice
                
                if [[ "$br_choice" == "0" ]]; then
                    continue
                fi
                
                if [[ "$br_choice" =~ ^[0-9]+$ ]] && [[ "$br_choice" -ge 1 ]] && [[ "$br_choice" -le "${#branch_array[@]}" ]]; then
                    SELECTED_VERSION_TYPE="branch"
                    SELECTED_VERSION_REF="${branch_array[$((br_choice-1))]}"
                    return 0
                else
                    echo -e "  ${_RED}Invalid selection${_NC}"
                fi
                ;;
            5)
                SELECTED_VERSION_TYPE="skip"
                SELECTED_VERSION_REF=""
                return 0
                ;;
            *)
                echo -e "  ${_RED}Invalid selection${_NC}"
                ;;
        esac
    done
}

# Interactive version selection for all repos
# Sets up UPDATE_TARGETS associative array
declare -A UPDATE_TARGETS

select_update_targets() {
    UPDATE_TARGETS=()
    
    echo ""
    simple_border "top"
    simple_line "${_BOLD}Select Update Targets${_NC}" 2
    simple_border "bottom"
    echo ""
    
    # Check if we should use quick mode or detailed selection
    echo -e "  ${_CYAN}1)${_NC} Quick update (latest on current branch/release)"
    echo -e "  ${_CYAN}2)${_NC} Select versions for each repository"
    echo -e "  ${_CYAN}0)${_NC} Cancel"
    echo ""
    
    echo -ne "  ${_BOLD}Select mode [0-2]:${_NC} "
    read -r mode
    
    case "$mode" in
        0)
            return 1
            ;;
        1)
            # Quick mode - use current track (branch or release)
            for repo_key in busibox ai-portal agent-manager busibox-app; do
                local deployed
                deployed=$(get_deployed_version "$repo_key")
                
                if [[ -n "$deployed" ]]; then
                    local type ref
                    type=$(parse_deployed_version "$deployed" "type")
                    ref=$(parse_deployed_version "$deployed" "ref")
                    UPDATE_TARGETS["$repo_key"]="${type}:${ref}"
                else
                    # Default to main branch
                    UPDATE_TARGETS["$repo_key"]="branch:main"
                fi
            done
            return 0
            ;;
        2)
            # Detailed selection for each repo
            for repo_key in busibox ai-portal agent-manager busibox-app; do
                if select_repo_version "$repo_key"; then
                    if [[ "$SELECTED_VERSION_TYPE" != "skip" ]]; then
                        UPDATE_TARGETS["$repo_key"]="${SELECTED_VERSION_TYPE}:${SELECTED_VERSION_REF}"
                    fi
                fi
            done
            
            if [[ ${#UPDATE_TARGETS[@]} -eq 0 ]]; then
                warn "No repositories selected for update"
                return 1
            fi
            
            return 0
            ;;
        *)
            echo -e "  ${_RED}Invalid selection${_NC}"
            return 1
            ;;
    esac
}

# Run the interactive menu loop
run_interactive_menu() {
    while true; do
        show_update_menu
        local choice=$?
        
        case $choice in
            0)  # Exit
                info "Goodbye!"
                exit 0
                ;;
            1)  # Change environment
                select_environment_menu
                ;;
            2)  # What needs updating
                show_update_status
                ;;
            3)  # Run update
                return 0
                ;;
        esac
    done
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    parse_args "$@"
    
    # Detect platform
    detect_platform
    
    # Get environment (defaults to staging now)
    local environment
    environment=$(get_environment)
    local container_prefix
    container_prefix=$(get_container_prefix)
    
    # Interactive menu mode (unless --no-prompt)
    if [[ "$NO_PROMPT" != true ]]; then
        run_interactive_menu
    fi
    
    # Re-get environment after potential menu changes
    environment=$(get_environment)
    container_prefix=$(get_container_prefix)
    
    # Show banner for the actual update
    show_update_banner
    
    printf '  Environment: %s%s%s\n' "$_BOLD" "$(ucfirst "$environment")" "$_NC"
    printf '  Platform: %s%s%s\n' "$_BOLD" "$(ucfirst "$PLATFORM")" "$_NC"
    if [[ "$PLATFORM" == "docker" ]]; then
        printf '  Container prefix: %s%s%s\n' "$_BOLD" "$container_prefix" "$_NC"
    fi
    echo ""
    
    # Run platform-specific update
    if [[ "$PLATFORM" == "proxmox" ]]; then
        # Confirm update for Proxmox
        if [[ "$NO_PROMPT" != true ]]; then
            simple_border "top"
            simple_line "${_BOLD}Ready to update (Proxmox/Ansible)${_NC}" 2
            simple_empty
            simple_line "This will run Ansible playbooks to update:" 2
            simple_line "• Core services (nginx, storage, database)" 4
            simple_line "• API services (authz, ingest, search, agent)" 4
            simple_line "• LLM services (if configured)" 4
            simple_line "• Frontend apps (ai-portal, agent-manager)" 4
            simple_empty
            simple_line "Your data will be preserved." 2
            simple_border "bottom"
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
        
        # Save deployed versions
        save_all_deployed_versions
    else
        if ! update_docker; then
            error "Docker update failed"
            exit 1
        fi
        
        # Save deployed versions
        save_all_deployed_versions
        
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
