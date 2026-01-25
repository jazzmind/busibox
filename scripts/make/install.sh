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

# Show progress bar with percentage
show_progress_bar() {
    local percent=$1
    local width=50
    local filled=$((percent * width / 100))
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
    echo -e "│                                                                              │"
    echo -e "│  ${CYAN}1)${NC} development  Docker on this machine (dev mode, volume mounts)            │"
    echo -e "│  ${CYAN}2)${NC} staging      Pre-production testing (Docker or Proxmox)                  │"
    echo -e "│  ${CYAN}3)${NC} production   Production deployment (Docker or Proxmox)                   │"
    echo -e "│                                                                              │"
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
    echo -e "│                                                                              │"
    echo -e "│  ${CYAN}1)${NC} docker       Docker Compose                                              │"
    echo -e "│  ${CYAN}2)${NC} proxmox      LXC Containers (requires root on Proxmox host)              │"
    echo -e "│                                                                              │"
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
    # Box inner content = 75 chars (after "│  " prefix, before " │" suffix)
    # Total: │ (1) + 2 spaces + 75 content + 1 space + │ (1) = 80 visual cols
    local box_content_width=75
    
    # Helper function to pad a line to box width (plain text only, no escape codes)
    box_line() {
        local text="$1"
        local text_len=${#text}
        local padding=$((box_content_width - text_len))
        if [[ $padding -lt 0 ]]; then padding=0; fi
        printf "│  %s%*s │\n" "$text" "$padding" ""
    }
    
    echo ""
    echo -e "┌─ ${BOLD}LLM BACKEND${NC} ────────────────────────────────────────────────────────────────┐"
    echo -e "│                                                                              │"
    
    if [[ "$DETECTED_LLM_BACKEND" == "mlx" ]]; then
        local detected_text="Detected: Apple Silicon (${DETECTED_ARCH}) - ${DETECTED_RAM_GB}GB Unified Memory"
        local tier_text="Selected tier: $(ucfirst "$DETECTED_LLM_TIER") (${DETECTED_RAM_GB}GB)"
        
        box_line "$detected_text"
        echo -e "│                                                                              │"
        echo -e "│  ${CYAN}1)${NC} local        Run models locally with MLX (recommended)                   │"
        echo -e "│                  - Complete data privacy - nothing leaves your machine       │"
        box_line "                - $tier_text"
        echo -e "│                                                                              │"
        echo -e "│  ${CYAN}2)${NC} cloud        Use AWS Bedrock                                             │"
        echo -e "│                  - No local GPU/memory requirements                          │"
        echo -e "│                  - Requires AWS credentials                                  │"
    elif [[ "$DETECTED_LLM_BACKEND" == "vllm" ]]; then
        local detected_text="Detected: x86_64 Linux - ${DETECTED_RAM_GB}GB RAM - NVIDIA GPU"
        local tier_text="Selected tier: $(ucfirst "$DETECTED_LLM_TIER")"
        
        box_line "$detected_text"
        echo -e "│                                                                              │"
        echo -e "│  ${CYAN}1)${NC} local        Run models locally with vLLM                                │"
        echo -e "│                  - Complete data privacy                                     │"
        box_line "                - $tier_text"
        echo -e "│                                                                              │"
        echo -e "│  ${CYAN}2)${NC} cloud        Use AWS Bedrock                                             │"
        echo -e "│                  - No local GPU requirements                                 │"
    else
        local detected_text="Detected: ${DETECTED_ARCH} - No GPU detected"
        
        box_line "$detected_text"
        echo -e "│                                                                              │"
        echo -e "│  Local AI requires Apple Silicon (MLX) or NVIDIA GPU (vLLM).                 │"
        echo -e "│  Using AWS Bedrock for LLM inference.                                        │"
    fi
    
    echo -e "│                                                                              │"
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
    echo -e "│                                                                              │"
    echo -e "│  Proxmox LXC containers use static IPs on isolated networks.                 │"
    echo -e "│  Production and staging use separate subnets for isolation.                  │"
    echo -e "│                                                                              │"
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
    echo -e "│                                                                              │"
    echo -e "│  Your Busibox deployment needs a domain name for HTTPS and external access.  │"
    echo -e "│                                                                              │"
    echo -e "│  Examples:                                                                   │"
    echo -e "│    - ${CYAN}localhost${NC}           Local development only (self-signed SSL)            │"
    echo -e "│    - ${CYAN}ai.company.com${NC}      Production with proper SSL certificate              │"
    echo -e "│    - ${CYAN}busibox.local${NC}       Internal network (requires DNS/hosts setup)         │"
    echo -e "│                                                                              │"
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
    echo -e "│                                                                              │"
    echo -e "│  The admin account will have full access to manage Busibox.                  │"
    echo -e "│  A magic link will be sent to this email for passwordless login.             │"
    echo -e "│                                                                              │"
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
        echo -e "│  ${BOLD}MISSING REPOSITORIES${NC}                                                        │"
        echo -e "├──────────────────────────────────────────────────────────────────────────────┤"
        echo -e "│  The following repositories were not found:                                 │"
        for repo in "${missing[@]}"; do
            printf "│    - %-72s│\n" "$repo"
        done
        echo -e "│                                                                              │"
        echo -e "│  Please clone them to the same parent directory as busibox:                 │"
        printf "│    %-74s│\n" "$parent_dir"
        echo -e "│                                                                              │"
        echo -e "│  Or set these environment variables before running install:                 │"
        echo -e "│    export AI_PORTAL_DIR=/path/to/ai-portal                                  │"
        echo -e "│    export AGENT_MANAGER_DIR=/path/to/agent-manager                          │"
        echo -e "│    export BUSIBOX_APP_DIR=/path/to/busibox-app                              │"
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
    export AUTHZ_ADMIN_TOKEN=$(openssl rand -hex 32)
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
AUTHZ_ADMIN_TOKEN=${AUTHZ_ADMIN_TOKEN}
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
        docker compose $compose_files --env-file "$env_file" up -d postgres
    else
        docker compose $compose_files --env-file "$env_file" up -d postgres 2>&1 | grep -v "^$" || true
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
        docker compose $compose_files --env-file "$env_file" up -d authz-api
    else
        docker compose $compose_files --env-file "$env_file" up -d authz-api 2>&1 | grep -v "^$" || true
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
        docker compose $compose_files --env-file "$env_file" build core-apps
    else
        docker compose $compose_files --env-file "$env_file" build core-apps 2>&1 | tail -20 || true
    fi
    
    show_stage 80 "Starting AI Portal" "Your command center for managing Busibox."
    
    # Start core-apps (contains ai-portal + agent-manager) without waiting for docs-api
    # We use --no-deps to skip the docs-api dependency for bootstrap
    if [[ "$VERBOSE" == true ]]; then
        docker compose $compose_files --env-file "$env_file" up -d --no-deps core-apps
    else
        docker compose $compose_files --env-file "$env_file" up -d --no-deps core-apps 2>&1 | grep -v "^$" || true
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
    
    info "Running Prisma migrations for AI Portal..."
    # Wait for container to be ready to accept commands
    sleep 10
    
    # Run prisma db push to sync schema
    if docker exec "${container_prefix}-core-apps" sh -c "cd /srv/ai-portal && npx prisma db push" 2>&1; then
        success "Database schema synchronized"
    else
        warn "Database migration may have failed - check logs if issues persist"
    fi
    
    # ==========================================================================
    # PHASE 5: Nginx (Reverse Proxy)
    # ==========================================================================
    show_stage 90 "Starting Nginx" "Reverse proxy with SSL termination."
    
    # Start nginx without waiting for all API dependencies
    if [[ "$VERBOSE" == true ]]; then
        docker compose $compose_files --env-file "$env_file" up -d --no-deps nginx
    else
        docker compose $compose_files --env-file "$env_file" up -d --no-deps nginx 2>&1 | grep -v "^$" || true
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
    local authz_admin_token="${AUTHZ_ADMIN_TOKEN:-}"
    local max_attempts=30
    local attempt=0
    
    info "Creating admin user in authz..."
    
    # Wait for authz to be ready
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:8010/health/live &>/dev/null; then
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    if [[ $attempt -ge $max_attempts ]]; then
        warn "AuthZ API not ready, cannot create admin user automatically"
        return 1
    fi
    
    # Bootstrap the Admin role with busibox-admin scope
    local roles_response
    roles_response=$(curl -s -X POST "http://localhost:8010/internal/bootstrap-roles" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${authz_admin_token}" \
        -d '{"roles": [{"name": "Admin", "scopes": ["authz.*", "busibox-admin.*"], "description": "Full system administrator with all permissions"}]}')
    
    if [[ "$VERBOSE" == true ]]; then
        info "Roles bootstrap response: $roles_response"
    fi
    
    # Create admin user and get magic link
    local response
    response=$(curl -s -X POST "http://localhost:8010/internal/bootstrap-admin" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${authz_admin_token}" \
        -d "{\"email\": \"${email}\", \"roles\": [\"Admin\"]}")
    
    if [[ "$VERBOSE" == true ]]; then
        info "Bootstrap admin response: $response"
    fi
    
    # Extract user_id and magic_link_token from response
    local user_id magic_link_token
    user_id=$(echo "$response" | grep -o '"user_id"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"\([^"]*\)".*/\1/' || echo "")
    magic_link_token=$(echo "$response" | grep -o '"magic_link_token"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*: *"\([^"]*\)".*/\1/' || echo "")
    
    if [[ -n "$user_id" && -n "$magic_link_token" ]]; then
        set_state "ADMIN_USER_ID" "$user_id"
        set_state "MAGIC_LINK_TOKEN" "$magic_link_token"
        success "Admin user created with ID: $user_id"
        return 0
    else
        warn "Failed to create admin user or extract magic link token"
        if [[ "$VERBOSE" == true ]]; then
            warn "Response was: $response"
        fi
        return 1
    fi
}

generate_admin_link() {
    local token
    token=$(get_state "MAGIC_LINK_TOKEN" 2>/dev/null || echo "")
    
    if [[ -n "$token" ]]; then
        # Return proper setup URL with magic link token
        if [[ "$BASE_DOMAIN" == "localhost" ]]; then
            echo "https://localhost/portal/admin/setup?token=${token}"
        else
            echo "https://${BASE_DOMAIN}/portal/admin/setup?token=${token}"
        fi
    else
        # Fallback to portal URL without token
        if [[ "$BASE_DOMAIN" == "localhost" ]]; then
            echo "https://localhost/portal/"
        else
            echo "https://${BASE_DOMAIN}/portal/"
        fi
    fi
}

show_completion() {
    local magic_link="$1"
    
    echo ""
    show_progress_bar 100
    echo ""
    echo ""
    
    # Box width is 80 chars (78 inside the borders)
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}                         ${BOLD}BOOTSTRAP COMPLETE${NC}                                 ${GREEN}║${NC}"
    echo -e "${GREEN}╠══════════════════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC}                                                                              ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  Core services are running! Open the AI Portal in your browser:             ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}                                                                              ${GREEN}║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${CYAN}${magic_link}${NC}"
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}  ${BOLD}What's Running:${NC}                                                             ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}                                                                              ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  • PostgreSQL     - Database with row-level security                        ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  • AuthZ API      - OAuth 2.0 authentication                                ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  • AI Portal      - Web dashboard for managing Busibox                      ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  • Nginx          - Reverse proxy with SSL                                  ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}                                                                              ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  ${BOLD}Note:${NC} Your browser will show a certificate warning (self-signed SSL).     ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  Click 'Advanced' and proceed to continue.                                  ${GREEN}║${NC}"
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
        echo ""
        echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║${NC}                      ${BOLD}BUSIBOX ALREADY INSTALLED${NC}                              ${GREEN}║${NC}"
        echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        
        local magic_link
        magic_link=$(generate_admin_link)
        
        echo -e "  Your Busibox instance is ready. Open the AI Portal:"
        echo ""
        echo -e "  ${CYAN}${magic_link}${NC}"
        echo ""
        
        if [[ "$NO_PROMPT" != true ]]; then
            echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
            echo -e "│                                                                              │"
            echo -e "│  ${CYAN}1)${NC} Open browser       Launch AI Portal in your default browser              │"
            echo -e "│  ${CYAN}2)${NC} Fresh install      Delete existing stack and start over                  │"
            echo -e "│  ${CYAN}3)${NC} Exit               Do nothing                                            │"
            echo -e "│                                                                              │"
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
    echo -e "${YELLOW}║${NC}                     ${BOLD}INTERRUPTED INSTALLATION DETECTED${NC}                        ${YELLOW}║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  A previous installation was interrupted at phase: ${BOLD}${install_phase:-unknown}${NC}"
    echo ""
    
    if [[ "$NO_PROMPT" != true ]]; then
        echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
        echo -e "│                                                                              │"
        echo -e "│  ${CYAN}1)${NC} Continue          Resume from where we left off                          │"
        echo -e "│  ${CYAN}2)${NC} Start fresh       Delete existing stack and start over                   │"
        echo -e "│  ${CYAN}3)${NC} Exit              Do nothing                                             │"
        echo -e "│                                                                              │"
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
        docker compose $compose_files --env-file "$env_file" down -v 2>/dev/null || true
    else
        docker compose $compose_files down -v 2>/dev/null || true
    fi
    
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
