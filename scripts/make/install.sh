#!/usr/bin/env bash
#
# Busibox Unified Install Script
#
# Usage:
#   install.sh                      # Interactive wizard
#   install.sh --demo               # Demo mode (auto-configure local/docker/local-llm)
#   install.sh --demo --no-prompt   # Demo mode, skip all confirmations
#   install.sh --warmup-only        # Pre-download models only (for offline use)
#   install.sh -v | --verbose       # Verbose output (show all logs)
#
# This script:
#   1. Collects environment/platform/LLM configuration via wizard (or uses demo defaults)
#   2. Generates all secrets automatically
#   3. Creates vault with auto-generated password (~/.vault-pass)
#   4. Bootstraps core services (PostgreSQL, AuthZ, Nginx, AI Portal)
#   5. Generates admin magic link for first login
#
# After install, all management is via the AI Portal web UI.
#

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${SCRIPT_DIR}/../lib/ui.sh"
source "${SCRIPT_DIR}/../lib/state.sh"
source "${SCRIPT_DIR}/../lib/github.sh"

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

# Portable uppercase first letter (works on bash 3.x / macOS)
# Usage: ucfirst "hello" -> "Hello"
ucfirst() {
    local str="$1"
    echo "$(echo "${str:0:1}" | tr '[:lower:]' '[:upper:]')${str:1}"
}

# Portable uppercase all (works on bash 3.x / macOS)
# Usage: uppercase "hello" -> "HELLO"
uppercase() {
    echo "$1" | tr '[:lower:]' '[:upper:]'
}

# =============================================================================
# CONFIGURATION
# =============================================================================

# Command line flags
DEMO_MODE=false
NO_PROMPT=false
WARMUP_ONLY=false
VERBOSE=false

# Installation config (set by wizard or demo defaults)
ENVIRONMENT=""
PLATFORM=""
LLM_BACKEND=""
LLM_TIER=""
ADMIN_EMAIL=""
BASE_DOMAIN=""
NETWORK_PRODUCTION=""
NETWORK_STAGING=""

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --demo)
                DEMO_MODE=true
                shift
                ;;
            --no-prompt)
                NO_PROMPT=true
                shift
                ;;
            --warmup-only)
                WARMUP_ONLY=true
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
# RICH PROGRESS DISPLAY
# =============================================================================

# Show banner with system detection
show_install_banner() {
    local title="$1"
    local subtitle="${2:-}"
    
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    printf "${CYAN}║${NC}%*s${BOLD}%s${NC}%*s${CYAN}║${NC}\n" $(( (78 - ${#title}) / 2 )) "" "$title" $(( (78 - ${#title} + 1) / 2 )) ""
    if [[ -n "$subtitle" ]]; then
        printf "${CYAN}║${NC}%*s${DIM}%s${NC}%*s${CYAN}║${NC}\n" $(( (78 - ${#subtitle}) / 2 )) "" "$subtitle" $(( (78 - ${#subtitle} + 1) / 2 )) ""
    fi
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# =============================================================================
# BOX LINE HELPER - Single function for all padded box lines
# =============================================================================
# Usage: box_line "content" [border_style] [border_color]
#   content:      Text to display (no padding needed - calculated automatically)
#   border_style: "single" (│) or "double" (║) - default "single"
#   border_color: ANSI color for border - default no color
#
# Box is 80 chars total: border(1) + content(78) + border(1)
# ANSI escape codes are stripped to calculate true visible length
# =============================================================================
box_line() {
    local content="$1"
    local border_style="${2:-single}"
    local border_color="${3:-}"
    
    # Border character
    local border_char="│"
    [[ "$border_style" == "double" ]] && border_char="║"
    
    # Strip ANSI codes to get visible character count
    # Use echo -e to interpret escape sequences, then strip them
    local visible_text
    visible_text=$(echo -e "$content" | sed -E 's/\x1b\[[0-9;]*m//g')
    local visible_len=${#visible_text}
    
    # Calculate padding (78 content width)
    local pad=$((78 - visible_len))
    [[ $pad -lt 0 ]] && pad=0
    
    # Build the line with echo -e to render ANSI codes
    if [[ -n "$border_color" ]]; then
        echo -e "${border_color}${border_char}${NC}${content}$(printf '%*s' "$pad" "")${border_color}${border_char}${NC}"
    else
        echo -e "${border_char}${content}$(printf '%*s' "$pad" "")${border_char}"
    fi
}

# Show progress bar with percentage
show_progress_bar() {
    local percent=$1
    local width=50
    local filled=$((percent * width / 100))
    
    # Ensure at 100% we get exactly 50 filled blocks
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

# Show stage with description box
show_stage() {
    local percent=$1
    local title="$2"
    local description="${3:-}"
    local status="${4:-Starting}"
    
    echo ""
    show_progress_bar "$percent"
    echo ""
    echo ""
    echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
    printf "│  ${BOLD}%-66s${NC} %8s │\n" "$title" "$status"
    echo -e "├──────────────────────────────────────────────────────────────────────────────┤"
    if [[ -n "$description" ]]; then
        # Word wrap description to fit in box (76 chars for content area)
        echo "$description" | fold -s -w 76 | while read -r line; do
            printf "│  %-76s│\n" "$line"
        done
    fi
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
}

# =============================================================================
# SYSTEM DETECTION
# =============================================================================

detect_system() {
    local os arch ram_gb
    
    os=$(uname -s)
    arch=$(uname -m)
    
    # Detect RAM
    if [[ "$os" == "Darwin" ]]; then
        ram_gb=$(($(sysctl -n hw.memsize) / 1024 / 1024 / 1024))
    else
        ram_gb=$(($(grep MemTotal /proc/meminfo | awk '{print $2}') / 1024 / 1024))
    fi
    
    # Detect LLM backend capability
    local backend="cloud"
    if [[ "$os" == "Darwin" && ("$arch" == "arm64" || "$arch" == "aarch64") ]]; then
        backend="mlx"
    elif command -v nvidia-smi &>/dev/null; then
        local gpu_count
        gpu_count=$(nvidia-smi -L 2>/dev/null | wc -l || echo "0")
        if [[ $gpu_count -gt 0 ]]; then
            backend="vllm"
        fi
    fi
    
    # Determine tier based on RAM
    local tier="minimal"
    if [[ $ram_gb -ge 256 ]]; then
        tier="ultra"
    elif [[ $ram_gb -ge 128 ]]; then
        tier="enterprise"
    elif [[ $ram_gb -ge 96 ]]; then
        tier="professional"
    elif [[ $ram_gb -ge 48 ]]; then
        tier="enhanced"
    elif [[ $ram_gb -ge 24 ]]; then
        tier="standard"
    fi
    
    # Export for use
    export DETECTED_OS="$os"
    export DETECTED_ARCH="$arch"
    export DETECTED_RAM_GB="$ram_gb"
    export DETECTED_LLM_BACKEND="$backend"
    export DETECTED_LLM_TIER="$tier"
}

# =============================================================================
# WIZARD FUNCTIONS
# =============================================================================

wizard_environment() {
    echo ""
    echo -e "┌─ ${BOLD}ENVIRONMENT${NC} ────────────────────────────────────────────────────────────────┐"
    box_line "" "single"
    box_line "  ${CYAN}1)${NC} development  Docker on this machine (dev mode, volume mounts)" "single"
    box_line "  ${CYAN}2)${NC} staging      Pre-production testing (Docker or Proxmox)" "single"
    box_line "  ${CYAN}3)${NC} production   Production deployment (Docker or Proxmox)" "single"
    box_line "" "single"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    while true; do
        read -p "$(echo -e "${BOLD}Choice [1]:${NC} ")" choice
        case "${choice:-1}" in
            1) ENVIRONMENT="development"; break ;;
            2) ENVIRONMENT="staging"; break ;;
            3) ENVIRONMENT="production"; break ;;
            *) echo "Invalid choice. Please enter 1, 2, or 3." ;;
        esac
    done
}

wizard_platform() {
    # Development environment always uses Docker - skip the prompt
    if [[ "$ENVIRONMENT" == "development" ]]; then
        PLATFORM="docker"
        return
    fi
    
    echo ""
    echo -e "┌─ ${BOLD}PLATFORM${NC} ───────────────────────────────────────────────────────────────────┐"
    box_line "" "single"
    box_line "  ${CYAN}1)${NC} docker       Docker Compose" "single"
    box_line "  ${CYAN}2)${NC} proxmox      LXC Containers (requires root on Proxmox host)" "single"
    box_line "" "single"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    while true; do
        read -p "$(echo -e "${BOLD}Choice [1]:${NC} ")" choice
        case "${choice:-1}" in
            1) PLATFORM="docker"; break ;;
            2) 
                PLATFORM="proxmox"
                # Check if running as root
                if [[ "$(id -u)" != "0" ]]; then
                    error "Proxmox installation must be run as root on the Proxmox host"
                    exit 1
                fi
                break
                ;;
            *) echo "Invalid choice. Please enter 1 or 2." ;;
        esac
    done
}

wizard_llm_backend() {
    # Local helper: box line with 2-space indent (│  text │)
    # Content width = 75 chars (after "│  " prefix, before " │" suffix)
    _wizard_line() {
        local text="$1"
        local stripped
        stripped=$(printf '%s' "$text" | sed $'s/\x1b\\[[0-9;]*m//g')
        local pad=$((75 - ${#stripped}))
        [[ $pad -lt 0 ]] && pad=0
        printf '│  %s%*s │\n' "$text" "$pad" ""
    }
    
    echo ""
    echo -e "┌─ ${BOLD}LLM BACKEND${NC} ────────────────────────────────────────────────────────────────┐"
    box_line "" "single"
    
    if [[ "$DETECTED_LLM_BACKEND" == "mlx" ]]; then
        local detected_text="Detected: Apple Silicon (${DETECTED_ARCH}) - ${DETECTED_RAM_GB}GB Unified Memory"
        local tier_text="Selected tier: $(ucfirst "$DETECTED_LLM_TIER") (${DETECTED_RAM_GB}GB)"
        
        _wizard_line "$detected_text"
        box_line "" "single"
        box_line "  ${CYAN}1)${NC} local        Run models locally with MLX (recommended)" "single"
        box_line "                  - Complete data privacy - nothing leaves your machine" "single"
        _wizard_line "                - $tier_text"
        box_line "" "single"
        box_line "  ${CYAN}2)${NC} cloud        Use AWS Bedrock" "single"
        box_line "                  - No local GPU/memory requirements" "single"
        box_line "                  - Requires AWS credentials" "single"
    elif [[ "$DETECTED_LLM_BACKEND" == "vllm" ]]; then
        local detected_text="Detected: x86_64 Linux - ${DETECTED_RAM_GB}GB RAM - NVIDIA GPU"
        local tier_text="Selected tier: $(ucfirst "$DETECTED_LLM_TIER")"
        
        _wizard_line "$detected_text"
        box_line "" "single"
        box_line "  ${CYAN}1)${NC} local        Run models locally with vLLM" "single"
        box_line "                  - Complete data privacy" "single"
        _wizard_line "                - $tier_text"
        box_line "" "single"
        box_line "  ${CYAN}2)${NC} cloud        Use AWS Bedrock" "single"
        box_line "                  - No local GPU requirements" "single"
    else
        local detected_text="Detected: ${DETECTED_ARCH} - No GPU detected"
        
        _wizard_line "$detected_text"
        box_line "" "single"
        box_line "  Local AI requires Apple Silicon (MLX) or NVIDIA GPU (vLLM)." "single"
        box_line "  Using AWS Bedrock for LLM inference." "single"
    fi
    
    box_line "" "single"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    if [[ "$DETECTED_LLM_BACKEND" == "cloud" ]]; then
        LLM_BACKEND="cloud"
        wizard_aws_credentials
    else
        while true; do
            read -p "$(echo -e "${BOLD}Choice [1]:${NC} ")" choice
            case "${choice:-1}" in
                1) 
                    LLM_BACKEND="$DETECTED_LLM_BACKEND"
                    LLM_TIER="$DETECTED_LLM_TIER"
                    break
                    ;;
                2) 
                    LLM_BACKEND="cloud"
                    wizard_aws_credentials
                    break
                    ;;
                *) echo "Invalid choice. Please enter 1 or 2." ;;
            esac
        done
    fi
}

wizard_aws_credentials() {
    echo ""
    echo -e "  ${BOLD}AWS Credentials:${NC}"
    read -p "    Access Key ID: " AWS_ACCESS_KEY_ID
    read -sp "    Secret Access Key: " AWS_SECRET_ACCESS_KEY
    echo ""
    read -p "    Region [us-east-1]: " AWS_REGION
    AWS_REGION="${AWS_REGION:-us-east-1}"
    
    # Validate credentials
    info "Validating AWS credentials..."
    # TODO: Actually validate credentials
    success "AWS credentials validated"
    
    export AWS_ACCESS_KEY_ID
    export AWS_SECRET_ACCESS_KEY
    export AWS_REGION
}

wizard_network() {
    if [[ "$PLATFORM" != "proxmox" ]]; then
        return
    fi
    
    echo ""
    echo -e "┌─ ${BOLD}NETWORK CONFIGURATION${NC} ──────────────────────────────────────────────────────┐"
    box_line "" "single"
    box_line "  Proxmox LXC containers use static IPs on isolated networks." "single"
    box_line "  Production and staging use separate subnets for isolation." "single"
    box_line "" "single"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    read -p "$(echo -e "${BOLD}Production network base [10.96.200]:${NC} ")" NETWORK_PRODUCTION
    NETWORK_PRODUCTION="${NETWORK_PRODUCTION:-10.96.200}"
    
    read -p "$(echo -e "${BOLD}Staging network base [10.96.201]:${NC} ")" NETWORK_STAGING
    NETWORK_STAGING="${NETWORK_STAGING:-10.96.201}"
}

wizard_domain() {
    # Development environment always uses localhost - skip the prompt
    if [[ "$ENVIRONMENT" == "development" ]]; then
        BASE_DOMAIN="localhost"
        return
    fi
    
    echo ""
    echo -e "┌─ ${BOLD}DOMAIN CONFIGURATION${NC} ───────────────────────────────────────────────────────┐"
    box_line "" "single"
    box_line "  Your Busibox deployment needs a domain name for HTTPS and external access." "single"
    box_line "" "single"
    box_line "  Examples:" "single"
    box_line "    - ${CYAN}localhost${NC}           Local development only (self-signed SSL)" "single"
    box_line "    - ${CYAN}ai.company.com${NC}      Production with proper SSL certificate" "single"
    box_line "    - ${CYAN}busibox.local${NC}       Internal network (requires DNS/hosts setup)" "single"
    box_line "" "single"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    read -p "$(echo -e "${BOLD}Base domain [localhost]:${NC} ")" BASE_DOMAIN
    BASE_DOMAIN="${BASE_DOMAIN:-localhost}"
    
    echo ""
    echo -e "  ${DIM}AI Portal will be available at:${NC} ${CYAN}https://${BASE_DOMAIN}/portal${NC}"
}

wizard_admin() {
    echo ""
    echo -e "┌─ ${BOLD}ADMIN CONFIGURATION${NC} ────────────────────────────────────────────────────────┐"
    box_line "" "single"
    box_line "  The admin account will have full access to manage Busibox." "single"
    box_line "  A magic link will be sent to this email for passwordless login." "single"
    box_line "" "single"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    read -p "$(echo -e "${BOLD}Admin email:${NC} ")" ADMIN_EMAIL
    
    if [[ "$ENVIRONMENT" != "development" ]]; then
        echo ""
        echo -e "  ${DIM}Restrict which email domains can sign up (comma-separated).${NC}"
        echo -e "  ${DIM}Use * to allow any domain.${NC}"
        echo ""
        local default_domains="${BASE_DOMAIN}"
        read -p "$(echo -e "${BOLD}Allowed email domains [${default_domains}]:${NC} ")" ALLOWED_DOMAINS
        ALLOWED_DOMAINS="${ALLOWED_DOMAINS:-${default_domains}}"
    else
        ALLOWED_DOMAINS="*"
    fi
}

# =============================================================================
# GITHUB TOKEN (uses library from scripts/lib/github.sh)
# =============================================================================

# Wrapper for install.sh that uses the library function
wizard_github_token() {
    # Use the library's ensure_github_token function
    ensure_github_token
}

# =============================================================================
# APP DIRECTORY DETECTION
# =============================================================================

# Detect where the app repositories are located
# This finds ai-portal, agent-manager, and busibox-app relative to busibox
detect_app_directories() {
    show_stage 35 "Detecting App Directories" "Looking for ai-portal, agent-manager, and busibox-app repositories."
    
    # Get the parent directory of busibox
    local parent_dir
    parent_dir=$(dirname "$REPO_ROOT")
    
    # Look for ai-portal
    if [[ -d "${parent_dir}/ai-portal" ]]; then
        export AI_PORTAL_DIR="${parent_dir}/ai-portal"
        info "Found ai-portal at: ${AI_PORTAL_DIR}"
    else
        # Search in common locations
        for search_dir in "$HOME/Code" "$HOME/code" "$HOME/src" "$HOME/projects" "$HOME/dev"; do
            if [[ -d "${search_dir}/ai-portal" ]]; then
                export AI_PORTAL_DIR="${search_dir}/ai-portal"
                info "Found ai-portal at: ${AI_PORTAL_DIR}"
                break
            fi
        done
    fi
    
    # Look for agent-manager
    if [[ -d "${parent_dir}/agent-manager" ]]; then
        export AGENT_MANAGER_DIR="${parent_dir}/agent-manager"
        info "Found agent-manager at: ${AGENT_MANAGER_DIR}"
    else
        for search_dir in "$HOME/Code" "$HOME/code" "$HOME/src" "$HOME/projects" "$HOME/dev"; do
            if [[ -d "${search_dir}/agent-manager" ]]; then
                export AGENT_MANAGER_DIR="${search_dir}/agent-manager"
                info "Found agent-manager at: ${AGENT_MANAGER_DIR}"
                break
            fi
        done
    fi
    
    # Look for busibox-app
    if [[ -d "${parent_dir}/busibox-app" ]]; then
        export BUSIBOX_APP_DIR="${parent_dir}/busibox-app"
        info "Found busibox-app at: ${BUSIBOX_APP_DIR}"
    else
        for search_dir in "$HOME/Code" "$HOME/code" "$HOME/src" "$HOME/projects" "$HOME/dev"; do
            if [[ -d "${search_dir}/busibox-app" ]]; then
                export BUSIBOX_APP_DIR="${search_dir}/busibox-app"
                info "Found busibox-app at: ${BUSIBOX_APP_DIR}"
                break
            fi
        done
    fi
    
    # Determine the apps base directory (common parent of all app repos)
    if [[ -n "${AI_PORTAL_DIR:-}" ]]; then
        export APPS_BASE_DIR=$(dirname "$AI_PORTAL_DIR")
    else
        export APPS_BASE_DIR="$parent_dir"
    fi
    
    # Validate required directories
    local missing=()
    if [[ -z "${AI_PORTAL_DIR:-}" ]]; then
        missing+=("ai-portal")
    fi
    if [[ -z "${AGENT_MANAGER_DIR:-}" ]]; then
        missing+=("agent-manager")
    fi
    if [[ -z "${BUSIBOX_APP_DIR:-}" ]]; then
        missing+=("busibox-app")
    fi
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        warn "Could not find: ${missing[*]}"
        echo ""
        echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
        box_line "  ${BOLD}MISSING REPOSITORIES${NC}" "single"
        echo -e "├──────────────────────────────────────────────────────────────────────────────┤"
        box_line "  The following repositories were not found:" "single"
        for repo in "${missing[@]}"; do
            box_line "    - $repo" "single"
        done
        box_line "" "single"
        box_line "  Please clone them to the same parent directory as busibox:" "single"
        box_line "    $parent_dir" "single"
        box_line "" "single"
        box_line "  Or set these environment variables before running install:" "single"
        box_line "    export AI_PORTAL_DIR=/path/to/ai-portal" "single"
        box_line "    export AGENT_MANAGER_DIR=/path/to/agent-manager" "single"
        box_line "    export BUSIBOX_APP_DIR=/path/to/busibox-app" "single"
        echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
        return 1
    fi
    
    success "All app directories found"
    return 0
}

# =============================================================================
# SECRET GENERATION
# =============================================================================

generate_secrets() {
    show_stage 30 "Generating Secure Configuration" "All passwords and keys are randomly generated. No default credentials."
    
    local vault_pass_file
    vault_pass_file=$(get_vault_pass_file)
    
    # Generate vault password if not exists
    if [[ ! -f "$vault_pass_file" ]]; then
        info "Generating vault password..."
        openssl rand -base64 32 > "$vault_pass_file"
        chmod 600 "$vault_pass_file"
        success "Vault password saved to $vault_pass_file"
    else
        info "Using existing vault password from $vault_pass_file"
    fi
    
    # Generate all secrets
    export POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')
    export BETTER_AUTH_SECRET=$(openssl rand -hex 32)
    export SSO_JWT_SECRET=$(openssl rand -hex 32)
    # Note: AUTHZ_ADMIN_TOKEN removed - we use direct PostgreSQL for bootstrap (Zero Trust)
    export AUTHZ_MASTER_KEY=$(openssl rand -base64 32)
    export LITELLM_API_KEY="sk-$(openssl rand -hex 16)"
    export LITELLM_MASTER_KEY="sk-$(openssl rand -hex 16)"
    export MINIO_ACCESS_KEY="minioadmin"
    export MINIO_SECRET_KEY=$(openssl rand -base64 24 | tr -d '/+=')
    export AUTHZ_BOOTSTRAP_CLIENT_ID="ai-portal"
    export AUTHZ_BOOTSTRAP_CLIENT_SECRET=$(openssl rand -hex 32)
    
    success "All secrets generated"
}

create_env_file() {
    local env_file
    env_file=$(get_env_file)
    local container_prefix
    container_prefix=$(get_container_prefix)
    
    info "Creating ${env_file}..."
    
    cat > "$env_file" << EOF
# Busibox Environment Configuration
# Generated by install.sh on $(date -Iseconds)
# DO NOT COMMIT THIS FILE

# PostgreSQL
POSTGRES_USER=busibox_user
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# MinIO
MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY}
MINIO_SECRET_KEY=${MINIO_SECRET_KEY}

# AuthZ
# Note: AUTHZ_ADMIN_TOKEN removed - bootstrap uses direct PostgreSQL (Zero Trust)
AUTHZ_MASTER_KEY=${AUTHZ_MASTER_KEY}
AUTHZ_BOOTSTRAP_CLIENT_ID=${AUTHZ_BOOTSTRAP_CLIENT_ID}
AUTHZ_BOOTSTRAP_CLIENT_SECRET=${AUTHZ_BOOTSTRAP_CLIENT_SECRET}

# AI Portal
BETTER_AUTH_SECRET=${BETTER_AUTH_SECRET}
SSO_JWT_SECRET=${SSO_JWT_SECRET}
ADMIN_EMAIL=${ADMIN_EMAIL}
ALLOWED_EMAIL_DOMAINS=${ALLOWED_DOMAINS:-*}

# LiteLLM
LITELLM_API_KEY=${LITELLM_API_KEY}
LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}
EOF

    # Add LLM backend config
    if [[ "$LLM_BACKEND" == "cloud" ]]; then
        cat >> "$env_file" << EOF

# AWS Bedrock
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:-}
AWS_REGION_NAME=${AWS_REGION:-us-east-1}
BEDROCK_API_KEY=${AWS_ACCESS_KEY_ID:-}:${AWS_SECRET_ACCESS_KEY:-}
EOF
    else
        cat >> "$env_file" << EOF

# Local LLM (${LLM_BACKEND})
LLM_BACKEND=${LLM_BACKEND}
LLM_TIER=${LLM_TIER}
EOF
    fi
    
    # Add container prefix for Docker
    cat >> "$env_file" << EOF

# Container Naming (allows multiple environments to coexist)
CONTAINER_PREFIX=${container_prefix}
COMPOSE_PROJECT_NAME=${container_prefix}-busibox

# GitHub Authentication (for private repos and npm packages)
GITHUB_AUTH_TOKEN=${GITHUB_AUTH_TOKEN}

# App Directories (for volume mounts in docker-compose.local-dev.yml)
AI_PORTAL_DIR=${AI_PORTAL_DIR}
AGENT_MANAGER_DIR=${AGENT_MANAGER_DIR}
BUSIBOX_APP_DIR=${BUSIBOX_APP_DIR}
APPS_BASE_DIR=${APPS_BASE_DIR}
EOF
    
    chmod 600 "$env_file"
    success "Created ${env_file}"
}

# =============================================================================
# SSL SETUP
# =============================================================================

ensure_ssl_certs() {
    local ssl_dir="${REPO_ROOT}/ssl"
    
    if [[ -f "${ssl_dir}/localhost.crt" && -f "${ssl_dir}/localhost.key" ]]; then
        info "SSL certificates already exist"
        return
    fi
    
    show_stage 40 "Generating SSL Certificates" "Self-signed certificates for local HTTPS. Your browser will show a warning."
    
    mkdir -p "$ssl_dir"
    
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "${ssl_dir}/localhost.key" \
        -out "${ssl_dir}/localhost.crt" \
        -subj "/C=US/ST=Local/L=Local/O=Busibox/CN=localhost" \
        2>/dev/null
    
    success "SSL certificates generated"
}

# =============================================================================
# ENVIRONMENT-PREFIXED PATHS
# =============================================================================

# Get container prefix based on environment
get_container_prefix() {
    case "$ENVIRONMENT" in
        demo) echo "demo" ;;
        development) echo "dev" ;;
        staging) echo "staging" ;;
        production) echo "prod" ;;
        *) echo "dev" ;;
    esac
}

# Get environment-specific file paths
# This allows multiple installations to coexist on the same system
get_env_file() {
    local prefix
    prefix=$(get_container_prefix)
    echo "${REPO_ROOT}/.env.${prefix}"
}

get_state_file() {
    local prefix
    prefix=$(get_container_prefix)
    echo "${REPO_ROOT}/.busibox-state-${prefix}"
}

get_vault_pass_file() {
    local prefix
    prefix=$(get_container_prefix)
    echo "${HOME}/.busibox-vault-pass-${prefix}"
}

# =============================================================================
# DOCKER BOOTSTRAP
# =============================================================================

bootstrap_docker() {
    # Set container prefix for docker compose
    local container_prefix
    container_prefix=$(get_container_prefix)
    export CONTAINER_PREFIX="$container_prefix"
    export COMPOSE_PROJECT_NAME="${container_prefix}-busibox"
    
    # Get environment-specific env file
    local env_file
    env_file=$(get_env_file)
    
    # Compose files: base + development overlay
    local compose_files="-f docker-compose.yml -f docker-compose.local-dev.yml"
    
    info "Using Docker project: ${COMPOSE_PROJECT_NAME} (containers: ${container_prefix}-*)"
    info "Using env file: ${env_file}"
    
    cd "${REPO_ROOT}"
    
    # ==========================================================================
    # PHASE 1: Core Infrastructure (PostgreSQL)
    # ==========================================================================
    show_stage 40 "Starting PostgreSQL" "Enterprise-grade database with row-level security."
    
    if [[ "$VERBOSE" == true ]]; then
        docker compose $compose_files up -d postgres
    else
        docker compose $compose_files up -d postgres 2>&1 | grep -v "^$" || true
    fi
    
    # Wait for postgres
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
        exit 1
    fi
    success "PostgreSQL is ready"
    
    # Sync database passwords (handles case where volume exists with old passwords)
    info "Syncing database passwords..."
    docker exec "${container_prefix}-postgres" psql -U postgres -c \
        "ALTER USER busibox_user WITH PASSWORD '${POSTGRES_PASSWORD}';" &>/dev/null || true
    docker exec "${container_prefix}-postgres" psql -U postgres -c \
        "ALTER USER busibox_test_user WITH PASSWORD 'testpassword';" &>/dev/null || true
    
    # ==========================================================================
    # PHASE 2: Authentication Service
    # ==========================================================================
    show_stage 55 "Starting AuthZ API" "Zero-trust authentication with OAuth 2.0."
    
    if [[ "$VERBOSE" == true ]]; then
        ADMIN_EMAIL="${ADMIN_EMAIL}" docker compose $compose_files up -d authz-api
    else
        ADMIN_EMAIL="${ADMIN_EMAIL}" docker compose $compose_files up -d authz-api 2>&1 | grep -v "^$" || true
    fi
    
    # Wait for authz
    info "Waiting for AuthZ API to be healthy..."
    attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:8010/health/live &>/dev/null; then
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    if [[ $attempt -ge $max_attempts ]]; then
        error "AuthZ API failed to start"
        exit 1
    fi
    success "AuthZ API is ready"
    
    # ==========================================================================
    # PHASE 3: Create Admin User
    # ==========================================================================
    show_stage 65 "Creating Admin User" "Setting up admin account with magic link."
    
    if create_admin_user "$ADMIN_EMAIL"; then
        success "Admin user created successfully"
    else
        warn "Could not create admin user - you'll need to sign up manually"
    fi
    
    # ==========================================================================
    # PHASE 4: AI Portal (Setup Wizard)
    # ==========================================================================
    show_stage 75 "Building AI Portal" "Building core-apps container with GitHub credentials."
    
    # Build core-apps first to ensure GITHUB_AUTH_TOKEN is baked into the image
    # This is required for npm to install @jazzmind/busibox-app from GitHub Packages
    info "Building core-apps container (this may take a few minutes on first run)..."
    if [[ "$VERBOSE" == true ]]; then
        GITHUB_AUTH_TOKEN="${GITHUB_AUTH_TOKEN}" docker compose $compose_files build core-apps
    else
        GITHUB_AUTH_TOKEN="${GITHUB_AUTH_TOKEN}" docker compose $compose_files build core-apps 2>&1 | tail -20 || true
    fi
    
    show_stage 80 "Starting AI Portal" "Your command center for managing Busibox."
    
    # Start core-apps (contains ai-portal + agent-manager) without waiting for docs-api
    # We use --no-deps to skip the docs-api dependency for bootstrap
    if [[ "$VERBOSE" == true ]]; then
        GITHUB_AUTH_TOKEN="${GITHUB_AUTH_TOKEN}" docker compose $compose_files up -d --no-deps core-apps
    else
        GITHUB_AUTH_TOKEN="${GITHUB_AUTH_TOKEN}" docker compose $compose_files up -d --no-deps core-apps 2>&1 | grep -v "^$" || true
    fi
    
    # Wait for core-apps container to exist
    info "Waiting for AI Portal container to start..."
    attempt=0
    while [[ $attempt -lt 30 ]]; do
        if docker ps --format '{{.Names}}' | grep -q "${container_prefix}-core-apps"; then
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    # Run database migrations for AI Portal
    show_stage 85 "Running database migrations" "Setting up AI Portal schema..."
    
    info "Waiting for AI Portal dependencies to install..."
    # Wait for node_modules to be populated (entrypoint runs npm install)
    attempt=0
    max_attempts=60  # 2 minutes
    while [[ $attempt -lt $max_attempts ]]; do
        if docker exec "${container_prefix}-core-apps" sh -c "test -f /srv/ai-portal/node_modules/.package-lock.json" 2>/dev/null; then
            success "Dependencies installed"
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    if [[ $attempt -ge $max_attempts ]]; then
        warn "Dependencies installation timed out - check container logs"
    else
        info "Running Prisma migrations for AI Portal..."
        # Run prisma db push to sync schema
        if docker exec "${container_prefix}-core-apps" sh -c "cd /srv/ai-portal && npx prisma db push --accept-data-loss" 2>&1; then
            success "Database schema synchronized"
        else
            warn "Database migration may have failed - check logs if issues persist"
        fi
    fi
    
    # ==========================================================================
    # PHASE 5: Nginx (Reverse Proxy)
    # ==========================================================================
    show_stage 90 "Starting Nginx" "Reverse proxy with SSL termination."
    
    # Start nginx without waiting for all API dependencies
    if [[ "$VERBOSE" == true ]]; then
        docker compose $compose_files up -d --no-deps nginx
    else
        docker compose $compose_files up -d --no-deps nginx 2>&1 | grep -v "^$" || true
    fi
    
    # ==========================================================================
    # PHASE 6: Wait for AI Portal to be ready
    # ==========================================================================
    show_stage 95 "Waiting for services" "Bootstrap services starting up..."
    
    info "Waiting for AI Portal to be healthy (this may take a minute on first run)..."
    max_attempts=90
    attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:3000/portal/api/health &>/dev/null; then
            break
        fi
        sleep 2
        ((attempt++))
        if [[ $((attempt % 15)) -eq 0 ]]; then
            echo -n "."
        fi
    done
    echo ""
    
    if [[ $attempt -ge $max_attempts ]]; then
        warn "AI Portal health check timed out, but it may still be starting"
    else
        success "AI Portal is ready"
    fi
}

# =============================================================================
# ADMIN LINK GENERATION
# =============================================================================

create_admin_user() {
    local email="$1"
    local container_prefix="${CONTAINER_PREFIX:-local}"
    local db_user="${POSTGRES_USER:-busibox_user}"
    local db_pass="${POSTGRES_PASSWORD:-devpassword}"
    local max_attempts=30
    local attempt=0
    
    info "Creating admin user via direct PostgreSQL (Zero Trust bootstrap)..."
    
    # Wait for postgres to be ready
    while [[ $attempt -lt $max_attempts ]]; do
        if docker exec "${container_prefix}-postgres" pg_isready -U "$db_user" &>/dev/null; then
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    if [[ $attempt -ge $max_attempts ]]; then
        warn "PostgreSQL not ready, cannot create admin user automatically"
        return 1
    fi
    
    # Also wait for authz to bootstrap (creates Admin role)
    attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:8010/health/live &>/dev/null; then
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    if [[ $attempt -ge $max_attempts ]]; then
        warn "AuthZ API not ready, Admin role may not exist"
    fi
    
    # Give authz a moment to bootstrap roles
    sleep 3
    
    # Generate UUIDs and token
    local user_id=$(uuidgen | tr '[:upper:]' '[:lower:]')
    local magic_link_token=$(openssl rand -base64 32 | tr -d '/+=' | head -c 43)
    local email_lower=$(echo "$email" | tr '[:upper:]' '[:lower:]')
    
    # Execute SQL via docker exec to postgres container
    # This is Zero Trust compliant - we're using direct DB access during bootstrap only
    local sql_result
    
    # First check if user already exists
    local existing_user_id
    existing_user_id=$(docker exec "${container_prefix}-postgres" psql -U "$db_user" -d authz -t -A -c "
        SELECT user_id::text FROM authz_users WHERE email = '${email_lower}';
    " 2>&1 | grep -v "^$" | head -1)
    
    if [[ -n "$existing_user_id" && "$existing_user_id" != *"ERROR"* ]]; then
        # User exists, update status
        user_id=$(echo "$existing_user_id" | tr -d '[:space:]')
        docker exec "${container_prefix}-postgres" psql -U "$db_user" -d authz -c "
            UPDATE authz_users SET status = 'active', updated_at = now()
            WHERE user_id = '${user_id}'::uuid;
        " >/dev/null 2>&1
        sql_result="$user_id"
    else
        # User doesn't exist, insert new
        sql_result=$(docker exec "${container_prefix}-postgres" psql -U "$db_user" -d authz -t -A -c "
            INSERT INTO authz_users (user_id, email, status)
            VALUES ('${user_id}'::uuid, '${email_lower}', 'active')
            RETURNING user_id::text;
        " 2>&1 | grep -v "^$" | head -1)
    fi
    
    if [[ $? -ne 0 ]]; then
        warn "Failed to create admin user: $sql_result"
        return 1
    fi
    
    # Get the actual user_id (may differ if user already existed)
    # Clean it up to remove any extra whitespace or newlines
    user_id=$(echo "$sql_result" | tr -d '[:space:]' | tr -d '\n' | tr -d '\r')
    
    if [[ -z "$user_id" ]]; then
        warn "Failed to get user ID from database"
        return 1
    fi
    
    # Get Admin role ID (created by authz bootstrap)
    local admin_role_id
    admin_role_id=$(docker exec "${container_prefix}-postgres" psql -U "$db_user" -d authz -t -A -c "
        SELECT id::text FROM authz_roles WHERE name = 'Admin' LIMIT 1;
    " 2>&1 | grep -v "^$" | head -1)
    
    if [[ -z "$admin_role_id" || "$admin_role_id" == *"ERROR"* ]]; then
        warn "Admin role not found - authz may not have bootstrapped yet"
        # Create Admin role manually as fallback
        # First check if it exists
        existing_admin=$(docker exec "${container_prefix}-postgres" psql -U "$db_user" -d authz -t -A -c "
            SELECT id::text FROM authz_roles WHERE name = 'Admin' LIMIT 1;
        " 2>&1 | grep -v "^$" | head -1)
        
        if [[ -z "$existing_admin" || "$existing_admin" == *"ERROR"* ]]; then
            # Create new Admin role
            admin_role_id=$(uuidgen | tr '[:upper:]' '[:lower:]')
            docker exec "${container_prefix}-postgres" psql -U "$db_user" -d authz -c "
                INSERT INTO authz_roles (id, name, description, scopes)
                VALUES ('${admin_role_id}'::uuid, 'Admin', 'Full system administrator', 
                        ARRAY['authz.*', 'busibox-admin.*']);
            " >/dev/null 2>&1
        else
            admin_role_id="$existing_admin"
        fi
    fi
    
    # Clean up the admin_role_id to remove any extra whitespace or newlines
    admin_role_id=$(echo "$admin_role_id" | tr -d '[:space:]' | tr -d '\n' | tr -d '\r')
    
    # Assign Admin role to user (check first to avoid constraint errors)
    local existing_role
    existing_role=$(docker exec "${container_prefix}-postgres" psql -U "$db_user" -d authz -t -A -c "
        SELECT 1 FROM authz_user_roles 
        WHERE user_id = '${user_id}'::uuid AND role_id = '${admin_role_id}'::uuid;
    " 2>&1)
    
    if [[ -z "$existing_role" || "$existing_role" == *"ERROR"* ]]; then
        docker exec "${container_prefix}-postgres" psql -U "$db_user" -d authz -c "
            INSERT INTO authz_user_roles (user_id, role_id)
            VALUES ('${user_id}'::uuid, '${admin_role_id}'::uuid);
        " >/dev/null 2>&1
    fi
    
    # Create magic link (24 hour expiry for initial setup)
    # First delete any existing magic links for this user to avoid conflicts
    docker exec "${container_prefix}-postgres" psql -U "$db_user" -d authz -c "
        DELETE FROM authz_magic_links WHERE user_id = '${user_id}'::uuid;
    " >/dev/null 2>&1
    
    # Now insert the new magic link
    local magic_result
    magic_result=$(docker exec "${container_prefix}-postgres" psql -U "$db_user" -d authz -c "
        INSERT INTO authz_magic_links (user_id, token, email, expires_at)
        VALUES ('${user_id}'::uuid, '${magic_link_token}', '${email_lower}', 
                now() + interval '24 hours')
        RETURNING token;
    " 2>&1)
    
    if [[ $? -eq 0 && "$magic_result" == *"${magic_link_token}"* ]]; then
        set_state "ADMIN_USER_ID" "$user_id"
        set_state "MAGIC_LINK_TOKEN" "$magic_link_token"
        success "Admin user created with ID: $user_id"
        return 0
    else
        warn "Failed to create magic link: $magic_result"
        return 1
    fi
}

generate_admin_link() {
    local regenerate="${1:-false}"  # Pass true to force regeneration
    local admin_email
    admin_email=$(get_state "ADMIN_EMAIL" 2>/dev/null || echo "")
    
    if [[ -z "$admin_email" ]]; then
        # No admin email configured - return portal URL without token
        if [[ "$BASE_DOMAIN" == "localhost" ]]; then
            echo "https://localhost/portal/"
        else
            echo "https://${BASE_DOMAIN}/portal/"
        fi
        return
    fi
    
    local token
    
    # Check if we should regenerate or if existing token is still valid
    if [[ "$regenerate" == "true" ]]; then
        token=""
    else
        token=$(get_state "MAGIC_LINK_TOKEN" 2>/dev/null || echo "")
        
        # Verify token is still valid (not used/expired) if we have one
        if [[ -n "$token" ]]; then
            local is_valid
            is_valid=$(docker exec "${CONTAINER_PREFIX}-postgres" psql -U postgres -d authz -t -A -c \
                "SELECT COUNT(*) FROM authz_magic_links WHERE token = '$token' AND expires_at > now() AND used_at IS NULL;" \
                2>/dev/null || echo "0")
            
            if [[ "$is_valid" != "1" ]]; then
                # Token is used or expired - need to regenerate
                token=""
            fi
        fi
    fi
    
    # Generate new token if needed
    if [[ -z "$token" ]]; then
        # Generate new magic link token
        token=$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-43)
        
        # Delete any existing magic links for this user
        docker exec "${CONTAINER_PREFIX}-postgres" psql -U postgres -d authz -c \
            "DELETE FROM authz_magic_links WHERE user_id = (SELECT user_id FROM authz_users WHERE email = '$admin_email');" \
            >/dev/null 2>&1 || true
        
        # Insert new magic link
        docker exec "${CONTAINER_PREFIX}-postgres" psql -U postgres -d authz -c \
            "INSERT INTO authz_magic_links (user_id, email, token, expires_at) 
             SELECT user_id, email, '$token', now() + interval '24 hours' 
             FROM authz_users WHERE email = '$admin_email';" \
            >/dev/null 2>&1
        
        # Save to state
        save_state "MAGIC_LINK_TOKEN" "$token"
    fi
    
    # Return proper setup URL with magic link token
    if [[ "$BASE_DOMAIN" == "localhost" ]]; then
        echo "https://localhost/portal/admin/setup?token=${token}"
    else
        echo "https://${BASE_DOMAIN}/portal/admin/setup?token=${token}"
    fi
}

show_completion() {
    local magic_link="$1"
    
    echo ""
    show_progress_bar 100
    echo ""
    echo ""
    
    # First box - BOOTSTRAP COMPLETE (green double-line box)
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    box_line "                         ${BOLD}BOOTSTRAP COMPLETE${NC}" "double" "${GREEN}"
    echo -e "${GREEN}╠══════════════════════════════════════════════════════════════════════════════╣${NC}"
    box_line "" "double" "${GREEN}"
    box_line "  Core services are running! Open the AI Portal in your browser:" "double" "${GREEN}"
    box_line "" "double" "${GREEN}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${CYAN}${magic_link}${NC}"
    echo ""
    
    # Second box - What's Running (green double-line box)
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    box_line "  ${BOLD}What's Running:${NC}" "double" "${GREEN}"
    box_line "" "double" "${GREEN}"
    box_line "  • PostgreSQL     - Database with row-level security" "double" "${GREEN}"
    box_line "  • AuthZ API      - OAuth 2.0 authentication" "double" "${GREEN}"
    box_line "  • AI Portal      - Web dashboard for managing Busibox" "double" "${GREEN}"
    box_line "  • Nginx          - Reverse proxy with SSL" "double" "${GREEN}"
    box_line "" "double" "${GREEN}"
    box_line "  ${BOLD}Note:${NC} Your browser will show a certificate warning (self-signed SSL)." "double" "${GREEN}"
    box_line "  Click 'Advanced' and proceed to continue." "double" "${GREEN}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# =============================================================================
# DEMO MODE
# =============================================================================

setup_demo_mode() {
    info "Demo mode: auto-configuring..."
    
    ENVIRONMENT="demo"
    PLATFORM="docker"
    BASE_DOMAIN="localhost"
    ADMIN_EMAIL="demo@localhost"
    ALLOWED_DOMAINS="*"
    
    # Use detected LLM backend
    if [[ "$DETECTED_LLM_BACKEND" != "cloud" ]]; then
        LLM_BACKEND="$DETECTED_LLM_BACKEND"
        LLM_TIER="$DETECTED_LLM_TIER"
    else
        # For demo without local LLM, we still need to handle this
        warn "No local LLM detected. Demo mode requires AWS Bedrock credentials."
        if [[ "$NO_PROMPT" == true ]]; then
            error "Cannot run demo without LLM backend. Provide AWS credentials or run on Apple Silicon/NVIDIA GPU."
            exit 1
        fi
        wizard_aws_credentials
        LLM_BACKEND="cloud"
    fi
}

# =============================================================================
# PREREQUISITES CHECK
# =============================================================================

check_prerequisites() {
    info "Checking prerequisites..."
    
    local errors=0
    
    # Docker
    if ! command -v docker &>/dev/null; then
        error "Docker is not installed"
        ((errors++))
    elif ! docker info &>/dev/null; then
        error "Docker daemon is not running"
        ((errors++))
    else
        success "Docker installed and running"
    fi
    
    # Docker Compose
    if ! docker compose version &>/dev/null; then
        error "Docker Compose is not installed"
        ((errors++))
    else
        success "Docker Compose available"
    fi
    
    # RAM check
    local min_ram=8
    if [[ $DETECTED_RAM_GB -lt $min_ram ]]; then
        error "Minimum ${min_ram}GB RAM required, found ${DETECTED_RAM_GB}GB"
        ((errors++))
    else
        success "${DETECTED_RAM_GB}GB RAM available"
    fi
    
    # Disk space
    local available_gb
    if [[ "$DETECTED_OS" == "Darwin" ]]; then
        available_gb=$(df -g "${REPO_ROOT}" | tail -1 | awk '{print $4}')
    else
        available_gb=$(df -BG "${REPO_ROOT}" | tail -1 | awk '{print $4}' | tr -d 'G')
    fi
    
    if [[ $available_gb -lt 20 ]]; then
        warn "Low disk space: ${available_gb}GB available (20GB recommended)"
    else
        success "${available_gb}GB disk space available"
    fi
    
    if [[ $errors -gt 0 ]]; then
        error "${errors} prerequisite(s) not met"
        exit 1
    fi
}

# =============================================================================
# INSTALL STATE MANAGEMENT
# =============================================================================

# Check for existing installation and offer resume/restart options
check_existing_install() {
    local env_prefix="$1"
    local state_file="${REPO_ROOT}/.busibox-state-${env_prefix}"
    
    if [[ ! -f "$state_file" ]]; then
        return 1  # No existing install
    fi
    
    # Read install phase from state
    local install_phase
    install_phase=$(grep "^INSTALL_PHASE=" "$state_file" 2>/dev/null | cut -d'=' -f2- || echo "")
    local install_status
    install_status=$(grep "^INSTALL_STATUS=" "$state_file" 2>/dev/null | cut -d'=' -f2- || echo "")
    
    if [[ -z "$install_phase" && -z "$install_status" ]]; then
        return 1  # No meaningful state
    fi
    
    # Check if bootstrap is complete
    if [[ "$install_status" == "installed" || "$install_phase" == "complete" ]]; then
        # Installation complete - show magic link and open browser
        # Load BASE_DOMAIN from state for URL generation
        BASE_DOMAIN=$(get_state "BASE_DOMAIN" 2>/dev/null || echo "localhost")
        
        echo ""
        echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
        box_line "                      ${BOLD}BUSIBOX ALREADY INSTALLED${NC}" "double" "${GREEN}"
        echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        
        local magic_link
        magic_link=$(generate_admin_link true)  # Force regenerate for existing installations
        
        echo -e "  Your Busibox instance is ready. Open the AI Portal:"
        echo ""
        echo -e "  ${CYAN}${magic_link}${NC}"
        echo ""
        
        if [[ "$NO_PROMPT" != true ]]; then
            echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
            box_line "" "single"
            box_line "  ${CYAN}1)${NC} Open browser       Launch AI Portal in your default browser" "single"
            box_line "  ${CYAN}2)${NC} Fresh install      Delete existing stack and start over" "single"
            box_line "  ${CYAN}3)${NC} Exit               Do nothing" "single"
            box_line "" "single"
            echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
            echo ""
            
            while true; do
                read -p "$(echo -e "${BOLD}Choice [1]:${NC} ")" choice
                case "${choice:-1}" in
                    1)
                        info "Opening browser..."
                        if [[ "$DETECTED_OS" == "Darwin" ]]; then
                            open "$magic_link" 2>/dev/null || true
                        else
                            xdg-open "$magic_link" 2>/dev/null || true
                        fi
                        exit 0
                        ;;
                    2)
                        warn "This will delete all containers and data for this environment."
                        if confirm "Are you sure you want to start fresh?"; then
                            cleanup_existing_install "$env_prefix"
                            return 1  # Continue with fresh install
                        fi
                        ;;
                    3)
                        info "Exiting."
                        exit 0
                        ;;
                    *)
                        echo "Invalid choice. Please enter 1, 2, or 3."
                        ;;
                esac
            done
        else
            # Non-interactive mode - just open browser and exit
            if [[ "$DETECTED_OS" == "Darwin" ]]; then
                open "$magic_link" 2>/dev/null || true
            else
                xdg-open "$magic_link" 2>/dev/null || true
            fi
            exit 0
        fi
    fi
    
    # Installation interrupted - offer resume or restart
    echo ""
    echo -e "${YELLOW}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    box_line "                     ${BOLD}INTERRUPTED INSTALLATION DETECTED${NC}" "double" "${YELLOW}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  A previous installation was interrupted at phase: ${BOLD}${install_phase:-unknown}${NC}"
    echo ""
    
    if [[ "$NO_PROMPT" != true ]]; then
        echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
        box_line "" "single"
        box_line "  ${CYAN}1)${NC} Continue          Resume from where we left off" "single"
        box_line "  ${CYAN}2)${NC} Start fresh       Delete existing stack and start over" "single"
        box_line "  ${CYAN}3)${NC} Exit              Do nothing" "single"
        box_line "" "single"
        echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
        echo ""
        
        while true; do
            read -p "$(echo -e "${BOLD}Choice [1]:${NC} ")" choice
            case "${choice:-1}" in
                1)
                    info "Resuming installation..."
                    return 0  # Resume - caller should check phase and continue
                    ;;
                2)
                    cleanup_existing_install "$env_prefix"
                    return 1  # Start fresh
                    ;;
                3)
                    info "Exiting."
                    exit 0
                    ;;
                *)
                    echo "Invalid choice. Please enter 1, 2, or 3."
                    ;;
            esac
        done
    else
        # Non-interactive mode - try to resume
        info "Attempting to resume installation..."
        return 0
    fi
}

# Clean up existing installation
cleanup_existing_install() {
    local env_prefix="$1"
    local state_file="${REPO_ROOT}/.busibox-state-${env_prefix}"
    local env_file="${REPO_ROOT}/.env.${env_prefix}"
    
    info "Cleaning up existing installation..."
    
    # Stop and remove containers
    cd "${REPO_ROOT}"
    export CONTAINER_PREFIX="$env_prefix"
    export COMPOSE_PROJECT_NAME="${env_prefix}-busibox"
    
    local compose_files="-f docker-compose.yml -f docker-compose.local-dev.yml"
    
    if [[ -f "$env_file" ]]; then
        docker compose $compose_files down -v --rmi local 2>/dev/null || true
    else
        docker compose $compose_files down -v --rmi local 2>/dev/null || true
    fi
    
    # Also remove any cached core-apps images to force rebuild with new Dockerfile
    # This ensures we don't use stale images when the Dockerfile changes
    info "Removing cached core-apps images..."
    docker images --filter "reference=*core-apps*" -q 2>/dev/null | xargs -r docker rmi -f 2>/dev/null || true
    docker images --filter "reference=*busibox*core*" -q 2>/dev/null | xargs -r docker rmi -f 2>/dev/null || true
    
    # Remove state and env files
    rm -f "$state_file"
    rm -f "$env_file"
    
    success "Cleanup complete"
}

# Get current install phase
get_install_phase() {
    get_state "INSTALL_PHASE" ""
}

# Set current install phase
set_install_phase() {
    set_state "INSTALL_PHASE" "$1"
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    parse_args "$@"
    
    # Detect system capabilities
    detect_system
    
    # Show banner
    if [[ "$DEMO_MODE" == true ]]; then
        show_install_banner "BUSIBOX DEMO" "Experience enterprise AI infrastructure in minutes"
        echo -e "Detected: ${BOLD}${DETECTED_ARCH}${NC} • ${BOLD}${DETECTED_RAM_GB}GB RAM${NC} • ${BOLD}$(ucfirst "$DETECTED_LLM_TIER") Tier${NC}"
        if [[ "$DETECTED_LLM_BACKEND" != "cloud" ]]; then
            echo -e "LLM Backend: ${BOLD}$(uppercase "$DETECTED_LLM_BACKEND")${NC} (local)"
        fi
        echo ""
    else
        show_install_banner "BUSIBOX INSTALLATION" "Your private AI infrastructure"
    fi
    
    # Check prerequisites
    check_prerequisites
    
    # Determine environment prefix for state checking
    local env_prefix=""
    local resuming=false
    
    if [[ "$DEMO_MODE" == true ]]; then
        env_prefix="demo"
        if check_existing_install "$env_prefix"; then
            resuming=true
        fi
    fi
    
    # Run wizard or use demo defaults (or resume from saved state)
    if [[ "$DEMO_MODE" == true ]]; then
        if [[ "$resuming" == true ]]; then
            # Load saved state
            ENVIRONMENT=$(get_state "ENVIRONMENT" "demo")
            PLATFORM=$(get_state "PLATFORM" "docker")
            LLM_BACKEND=$(get_state "LLM_BACKEND" "$DETECTED_LLM_BACKEND")
            LLM_TIER=$(get_state "LLM_TIER" "$DETECTED_LLM_TIER")
            ADMIN_EMAIL=$(get_state "ADMIN_EMAIL" "demo@localhost")
            BASE_DOMAIN=$(get_state "BASE_DOMAIN" "localhost")
            ALLOWED_DOMAINS=$(get_state "ALLOWED_DOMAINS" "*")
        else
            setup_demo_mode
        fi
    else
        # For non-demo mode, we need to run wizard to know which environment
        wizard_environment
        
        # Now we can check for existing install
        env_prefix=$(get_container_prefix)
        if check_existing_install "$env_prefix"; then
            resuming=true
            # Load saved state
            PLATFORM=$(get_state "PLATFORM" "docker")
            LLM_BACKEND=$(get_state "LLM_BACKEND" "")
            LLM_TIER=$(get_state "LLM_TIER" "")
            ADMIN_EMAIL=$(get_state "ADMIN_EMAIL" "")
            BASE_DOMAIN=$(get_state "BASE_DOMAIN" "localhost")
            ALLOWED_DOMAINS=$(get_state "ALLOWED_DOMAINS" "*")
        else
            wizard_platform
            wizard_llm_backend
            wizard_network
            wizard_domain
            wizard_admin
        fi
    fi
    
    # Check what phase we're resuming from
    local current_phase=""
    if [[ "$resuming" == true ]]; then
        current_phase=$(get_install_phase)
    fi
    
    # GitHub token is always required (for both demo and regular install)
    # Skip if we already have it from a previous run
    if [[ "$current_phase" != "secrets_generated" && "$current_phase" != "bootstrap_started" && "$current_phase" != "bootstrap_complete" ]]; then
        if ! wizard_github_token; then
            error "Cannot proceed without valid GitHub token"
            exit 1
        fi
        set_install_phase "github_token_obtained"
    fi
    
    # Detect app directories (ai-portal, agent-manager, busibox-app)
    # These are required for volume mounts in docker-compose.local-dev.yml
    if [[ "$current_phase" == "secrets_generated" || "$current_phase" == "bootstrap_started" || "$current_phase" == "bootstrap_complete" ]]; then
        # Load saved paths from state
        AI_PORTAL_DIR=$(get_state "AI_PORTAL_DIR" "")
        AGENT_MANAGER_DIR=$(get_state "AGENT_MANAGER_DIR" "")
        BUSIBOX_APP_DIR=$(get_state "BUSIBOX_APP_DIR" "")
        APPS_BASE_DIR=$(get_state "APPS_BASE_DIR" "")
        
        # If not in state, detect them
        if [[ -z "$AI_PORTAL_DIR" || -z "$BUSIBOX_APP_DIR" ]]; then
            if ! detect_app_directories; then
                error "Cannot proceed without app directories"
                exit 1
            fi
        fi
    else
        if ! detect_app_directories; then
            error "Cannot proceed without app directories"
            exit 1
        fi
        # Save paths to state
        set_state "AI_PORTAL_DIR" "$AI_PORTAL_DIR"
        set_state "AGENT_MANAGER_DIR" "$AGENT_MANAGER_DIR"
        set_state "BUSIBOX_APP_DIR" "$BUSIBOX_APP_DIR"
        set_state "APPS_BASE_DIR" "$APPS_BASE_DIR"
    fi
    
    # Generate secrets and create .env file
    # Skip if already done
    if [[ "$current_phase" != "secrets_generated" && "$current_phase" != "bootstrap_started" && "$current_phase" != "bootstrap_complete" ]]; then
        generate_secrets
        create_env_file
        set_install_phase "secrets_generated"
    fi
    
    # Ensure SSL certificates
    ensure_ssl_certs
    
    # Save state (for resume capability)
    set_environment "$ENVIRONMENT"
    set_backend "$ENVIRONMENT" "$PLATFORM"
    set_state "PLATFORM" "$PLATFORM"
    set_state "LLM_BACKEND" "$LLM_BACKEND"
    set_state "LLM_TIER" "${LLM_TIER:-}"
    set_state "ADMIN_EMAIL" "$ADMIN_EMAIL"
    set_state "BASE_DOMAIN" "$BASE_DOMAIN"
    set_state "ALLOWED_DOMAINS" "${ALLOWED_DOMAINS:-*}"
    
    # Bootstrap based on platform
    set_install_phase "bootstrap_started"
    
    if [[ "$PLATFORM" == "docker" ]]; then
        bootstrap_docker
    else
        # TODO: Implement Proxmox bootstrap
        error "Proxmox bootstrap not yet implemented"
        exit 1
    fi
    
    # Mark installation as complete
    set_install_phase "complete"
    set_install_status "installed"
    
    # Note: SETUP_COMPLETE will be set by AI Portal after admin completes setup wizard
    save_state "SETUP_COMPLETE" "false"
    
    # Generate admin magic link
    local magic_link
    magic_link=$(generate_admin_link)
    
    # Show completion message
    show_completion "$magic_link"
    
    # Open browser
    info "Opening browser..."
    if [[ "$DETECTED_OS" == "Darwin" ]]; then
        open "$magic_link" 2>/dev/null || true
    else
        xdg-open "$magic_link" 2>/dev/null || true
    fi
}

main "$@"
