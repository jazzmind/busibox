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

# =============================================================================
# SIGNAL HANDLING
# =============================================================================
# Ensure clean exit on Ctrl+C and errors

# Track if we're in a cleanup to prevent recursive cleanup
_CLEANUP_IN_PROGRESS=false

# Cleanup function called on exit
_cleanup() {
    local exit_code=$?
    
    # Prevent recursive cleanup
    if [[ "$_CLEANUP_IN_PROGRESS" == true ]]; then
        return
    fi
    _CLEANUP_IN_PROGRESS=true
    
    # If we're exiting due to an error, show a helpful message
    if [[ $exit_code -ne 0 ]]; then
        echo ""
        echo "Installation interrupted or failed (exit code: $exit_code)"
        echo "You can resume by running the install script again."
        echo ""
    fi
}

# Handle Ctrl+C (SIGINT) and SIGTERM
_handle_interrupt() {
    echo ""
    echo ""
    echo "==> Installation interrupted by user (Ctrl+C)"
    echo ""
    exit 130  # Standard exit code for SIGINT
}

# Set up traps
trap _cleanup EXIT
trap _handle_interrupt INT TERM

# Source libraries (note: state.sh uses BUSIBOX_ENV for state file path)
# We'll manage our own state file path after ENVIRONMENT is determined
source "${SCRIPT_DIR}/../lib/ui.sh"
source "${SCRIPT_DIR}/../lib/state.sh"
source "${SCRIPT_DIR}/../lib/vault.sh"    # Must be before github.sh for vault functions
source "${SCRIPT_DIR}/../lib/github.sh"

# =============================================================================
# STATE FILE MANAGEMENT
# =============================================================================
# Install.sh manages its own state file based on ENVIRONMENT variable
# This overrides the default state.sh behavior which uses BUSIBOX_ENV

# Update state file path and vault environment for current environment
# Call this after ENVIRONMENT is set
_update_state_file_for_env() {
    local prefix
    case "$ENVIRONMENT" in
        demo) prefix="demo" ;;
        development) prefix="dev" ;;
        staging) prefix="staging" ;;
        production) prefix="prod" ;;
        *) prefix="dev" ;;
    esac
    # Override the global state file path from state.sh
    BUSIBOX_STATE_FILE="${REPO_ROOT}/.busibox-state-${prefix}"
    
    # Set vault environment for this prefix
    # This configures VAULT_FILE and VAULT_PASS_FILE
    set_vault_environment "$prefix"
    export BUSIBOX_STATE_FILE
}

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
REINSTALL=false
ENV_FROM_LAUNCHER=""
BACKEND_FROM_LAUNCHER=""

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
            --reinstall)
                REINSTALL=true
                shift
                ;;
            --env)
                ENV_FROM_LAUNCHER="$2"
                shift 2
                ;;
            --backend)
                BACKEND_FROM_LAUNCHER="$2"
                shift 2
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
    local os arch ram_gb ram_bytes
    
    os=$(uname -s)
    arch=$(uname -m)
    
    # Detect RAM (with fallback on error)
    ram_gb=8  # Default fallback
    if [[ "$os" == "Darwin" ]]; then
        ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo "")
        if [[ -n "$ram_bytes" && "$ram_bytes" =~ ^[0-9]+$ ]]; then
            ram_gb=$((ram_bytes / 1024 / 1024 / 1024))
        fi
    else
        local mem_kb
        mem_kb=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo "")
        if [[ -n "$mem_kb" && "$mem_kb" =~ ^[0-9]+$ ]]; then
            ram_gb=$((mem_kb / 1024 / 1024))
        fi
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
    # If environment passed from launcher, confirm it instead of asking
    if [[ -n "$ENV_FROM_LAUNCHER" ]]; then
        ENVIRONMENT="$ENV_FROM_LAUNCHER"
        # Also set PLATFORM from launcher backend
        if [[ -n "$BACKEND_FROM_LAUNCHER" ]]; then
            PLATFORM="$BACKEND_FROM_LAUNCHER"
        fi
        
        # Update state file path for this environment
        _update_state_file_for_env
        
        echo ""
        echo -e "┌─ ${BOLD}ENVIRONMENT${NC} ────────────────────────────────────────────────────────────────┐"
        box_line "" "single"
        box_line "  Environment: ${CYAN}$(ucfirst "$ENVIRONMENT")${NC}" "single"
        if [[ -n "$PLATFORM" ]]; then
            box_line "  Platform:    ${CYAN}$(ucfirst "$PLATFORM")${NC}" "single"
        fi
        box_line "" "single"
        echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
        echo ""
        
        read -p "$(echo -e "${BOLD}Continue with this configuration? [Y/n]:${NC} ")" confirm
        if [[ "${confirm:-y}" =~ ^[Nn] ]]; then
            # User wants to change - clear launcher values and show full menu
            ENV_FROM_LAUNCHER=""
            BACKEND_FROM_LAUNCHER=""
            ENVIRONMENT=""
            PLATFORM=""
            wizard_environment
        fi
        return
    fi
    
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
    
    # Update state file path for this environment
    _update_state_file_for_env
}

wizard_platform() {
    # Development environment always uses Docker - skip the prompt
    if [[ "$ENVIRONMENT" == "development" ]]; then
        PLATFORM="docker"
        return
    fi
    
    # If platform already set from launcher, skip
    if [[ -n "$PLATFORM" ]]; then
        # Validate proxmox requires root
        if [[ "$PLATFORM" == "proxmox" && "$(id -u)" != "0" ]]; then
            error "Proxmox installation must be run as root on the Proxmox host"
            exit 1
        fi
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
        box_line "  ${CYAN}2)${NC} cloud        Use cloud AI provider" "single"
        box_line "                  - OpenAI, Anthropic, OpenRouter, or AWS Bedrock" "single"
        box_line "                  - No local GPU/memory requirements" "single"
    elif [[ "$DETECTED_LLM_BACKEND" == "vllm" ]]; then
        local detected_text="Detected: x86_64 Linux - ${DETECTED_RAM_GB}GB RAM - NVIDIA GPU"
        local tier_text="Selected tier: $(ucfirst "$DETECTED_LLM_TIER")"
        
        _wizard_line "$detected_text"
        box_line "" "single"
        box_line "  ${CYAN}1)${NC} local        Run models locally with vLLM" "single"
        box_line "                  - Complete data privacy" "single"
        _wizard_line "                - $tier_text"
        box_line "" "single"
        box_line "  ${CYAN}2)${NC} cloud        Use cloud AI provider" "single"
        box_line "                  - OpenAI, Anthropic, OpenRouter, or AWS Bedrock" "single"
    else
        local detected_text="Detected: ${DETECTED_ARCH} - No GPU detected"
        
        _wizard_line "$detected_text"
        box_line "" "single"
        box_line "  Local AI requires Apple Silicon (MLX) or NVIDIA GPU (vLLM)." "single"
        box_line "  Using cloud AI provider for LLM inference." "single"
    fi
    
    box_line "" "single"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    if [[ "$DETECTED_LLM_BACKEND" == "cloud" ]]; then
        LLM_BACKEND="cloud"
        wizard_cloud_provider
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
                    wizard_cloud_provider
                    break
                    ;;
                *) echo "Invalid choice. Please enter 1 or 2." ;;
            esac
        done
    fi
}

# Cloud provider selection and credential collection
CLOUD_PROVIDER=""
CLOUD_API_KEY=""
CLOUD_API_BASE=""

wizard_cloud_provider() {
    echo ""
    echo -e "┌─ ${BOLD}CLOUD AI PROVIDER${NC} ──────────────────────────────────────────────────────────┐"
    box_line "" "single"
    box_line "  ${CYAN}1)${NC} OpenAI       GPT-4o, GPT-4 Turbo, GPT-3.5" "single"
    box_line "                  - Most popular, excellent for general use" "single"
    box_line "" "single"
    box_line "  ${CYAN}2)${NC} Anthropic    Claude 3.5 Sonnet, Claude 3 Opus" "single"
    box_line "                  - Strong reasoning, longer context" "single"
    box_line "" "single"
    box_line "  ${CYAN}3)${NC} OpenRouter   Access to 100+ models via single API" "single"
    box_line "                  - Pay-per-use, no commitments" "single"
    box_line "" "single"
    box_line "  ${CYAN}4)${NC} AWS Bedrock  Claude, Titan, Llama via AWS" "single"
    box_line "                  - Enterprise, uses AWS credentials" "single"
    box_line "" "single"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    while true; do
        read -p "$(echo -e "${BOLD}Choice [1]:${NC} ")" choice
        case "${choice:-1}" in
            1) 
                CLOUD_PROVIDER="openai"
                wizard_openai_credentials
                break
                ;;
            2) 
                CLOUD_PROVIDER="anthropic"
                wizard_anthropic_credentials
                break
                ;;
            3) 
                CLOUD_PROVIDER="openrouter"
                wizard_openrouter_credentials
                break
                ;;
            4) 
                CLOUD_PROVIDER="bedrock"
                wizard_aws_credentials
                break
                ;;
            *) echo "Invalid choice. Please enter 1, 2, 3, or 4." ;;
        esac
    done
    
    # Save provider to state
    set_state "CLOUD_PROVIDER" "$CLOUD_PROVIDER"
}

wizard_openai_credentials() {
    echo ""
    echo -e "  ${BOLD}OpenAI API Configuration${NC}"
    echo -e "  ${DIM}Get your API key from: https://platform.openai.com/api-keys${NC}"
    echo ""
    
    read -sp "  API Key (sk-...): " CLOUD_API_KEY
    echo ""
    
    if [[ -z "$CLOUD_API_KEY" ]]; then
        error "API key is required"
        wizard_openai_credentials
        return
    fi
    
    if [[ ! "$CLOUD_API_KEY" =~ ^sk- ]]; then
        warn "API key doesn't look like an OpenAI key (should start with 'sk-')"
        read -p "  Continue anyway? [y/N]: " confirm
        if [[ ! "${confirm:-n}" =~ ^[Yy] ]]; then
            wizard_openai_credentials
            return
        fi
    fi
    
    # Optional: custom base URL for Azure OpenAI or proxies
    read -p "  Custom API base URL (leave empty for default): " CLOUD_API_BASE
    
    export OPENAI_API_KEY="$CLOUD_API_KEY"
    [[ -n "$CLOUD_API_BASE" ]] && export OPENAI_API_BASE="$CLOUD_API_BASE"
    
    set_state "OPENAI_API_KEY" "$CLOUD_API_KEY"
    [[ -n "$CLOUD_API_BASE" ]] && set_state "OPENAI_API_BASE" "$CLOUD_API_BASE"
    
    success "OpenAI credentials saved"
}

wizard_anthropic_credentials() {
    echo ""
    echo -e "  ${BOLD}Anthropic API Configuration${NC}"
    echo -e "  ${DIM}Get your API key from: https://console.anthropic.com/settings/keys${NC}"
    echo ""
    
    read -sp "  API Key (sk-ant-...): " CLOUD_API_KEY
    echo ""
    
    if [[ -z "$CLOUD_API_KEY" ]]; then
        error "API key is required"
        wizard_anthropic_credentials
        return
    fi
    
    if [[ ! "$CLOUD_API_KEY" =~ ^sk-ant- ]]; then
        warn "API key doesn't look like an Anthropic key (should start with 'sk-ant-')"
        read -p "  Continue anyway? [y/N]: " confirm
        if [[ ! "${confirm:-n}" =~ ^[Yy] ]]; then
            wizard_anthropic_credentials
            return
        fi
    fi
    
    export ANTHROPIC_API_KEY="$CLOUD_API_KEY"
    set_state "ANTHROPIC_API_KEY" "$CLOUD_API_KEY"
    
    success "Anthropic credentials saved"
}

wizard_openrouter_credentials() {
    echo ""
    echo -e "  ${BOLD}OpenRouter API Configuration${NC}"
    echo -e "  ${DIM}Get your API key from: https://openrouter.ai/keys${NC}"
    echo ""
    
    read -sp "  API Key (sk-or-...): " CLOUD_API_KEY
    echo ""
    
    if [[ -z "$CLOUD_API_KEY" ]]; then
        error "API key is required"
        wizard_openrouter_credentials
        return
    fi
    
    export OPENROUTER_API_KEY="$CLOUD_API_KEY"
    set_state "OPENROUTER_API_KEY" "$CLOUD_API_KEY"
    
    # OpenRouter uses OpenAI-compatible API
    export OPENAI_API_KEY="$CLOUD_API_KEY"
    export OPENAI_API_BASE="https://openrouter.ai/api/v1"
    set_state "OPENAI_API_KEY" "$CLOUD_API_KEY"
    set_state "OPENAI_API_BASE" "https://openrouter.ai/api/v1"
    
    success "OpenRouter credentials saved"
}

wizard_aws_credentials() {
    echo ""
    echo -e "  ${BOLD}AWS Bedrock Configuration${NC}"
    echo -e "  ${DIM}Requires IAM user with Bedrock access${NC}"
    echo ""
    
    read -p "  Access Key ID: " AWS_ACCESS_KEY_ID
    read -sp "  Secret Access Key: " AWS_SECRET_ACCESS_KEY
    echo ""
    read -p "  Region [us-east-1]: " AWS_REGION
    AWS_REGION="${AWS_REGION:-us-east-1}"
    
    if [[ -z "$AWS_ACCESS_KEY_ID" || -z "$AWS_SECRET_ACCESS_KEY" ]]; then
        error "Both Access Key ID and Secret Access Key are required"
        wizard_aws_credentials
        return
    fi
    
    export AWS_ACCESS_KEY_ID
    export AWS_SECRET_ACCESS_KEY
    export AWS_REGION
    
    set_state "AWS_ACCESS_KEY_ID" "$AWS_ACCESS_KEY_ID"
    set_state "AWS_SECRET_ACCESS_KEY" "$AWS_SECRET_ACCESS_KEY"
    set_state "AWS_REGION" "$AWS_REGION"
    
    success "AWS Bedrock credentials saved"
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
    
    # Load saved value as default
    local saved_domain
    saved_domain=$(get_state "BASE_DOMAIN" "localhost")
    
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
    
    read -p "$(echo -e "${BOLD}Base domain [${saved_domain}]:${NC} ")" BASE_DOMAIN
    BASE_DOMAIN="${BASE_DOMAIN:-${saved_domain}}"
    
    echo ""
    echo -e "  ${DIM}AI Portal will be available at:${NC} ${CYAN}https://${BASE_DOMAIN}/portal${NC}"
}

wizard_admin() {
    # Load saved values as defaults
    local saved_email saved_domains
    saved_email=$(get_state "ADMIN_EMAIL" "")
    saved_domains=$(get_state "ALLOWED_DOMAINS" "")
    
    echo ""
    echo -e "┌─ ${BOLD}ADMIN CONFIGURATION${NC} ────────────────────────────────────────────────────────┐"
    box_line "" "single"
    box_line "  The admin account will have full access to manage Busibox." "single"
    box_line "  A magic link will be sent to this email for passwordless login." "single"
    box_line "  You can specify multiple emails separated by commas." "single"
    box_line "" "single"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    if [[ -n "$saved_email" ]]; then
        read -p "$(echo -e "${BOLD}Admin email(s) [${saved_email}]:${NC} ")" ADMIN_EMAIL
        ADMIN_EMAIL="${ADMIN_EMAIL:-${saved_email}}"
    else
        read -p "$(echo -e "${BOLD}Admin email(s):${NC} ")" ADMIN_EMAIL
    fi
    
    if [[ "$ENVIRONMENT" != "development" ]]; then
        # Extract unique domains from admin emails for default
        # E.g., "wes@sonnenreich.com,wes@maigent.ai" -> "sonnenreich.com,maigent.ai"
        local default_domains=""
        local seen_domains=""
        IFS=',' read -ra emails <<< "$ADMIN_EMAIL"
        for email in "${emails[@]}"; do
            # Trim whitespace and extract domain
            email=$(echo "$email" | xargs)
            local domain="${email##*@}"
            # Only add if not seen before
            if [[ ! ",$seen_domains," =~ ",$domain," ]]; then
                if [[ -n "$default_domains" ]]; then
                    default_domains="${default_domains},${domain}"
                else
                    default_domains="${domain}"
                fi
                seen_domains="${seen_domains},${domain}"
            fi
        done
        
        # Use saved value if available, otherwise extracted domains
        local display_default="${saved_domains:-${default_domains}}"
        
        echo ""
        echo -e "  ${DIM}Restrict which email domains can sign up (comma-separated).${NC}"
        echo -e "  ${DIM}Use * to allow any domain.${NC}"
        echo ""
        read -p "$(echo -e "${BOLD}Allowed email domains [${display_default}]:${NC} ")" ALLOWED_DOMAINS
        ALLOWED_DOMAINS="${ALLOWED_DOMAINS:-${display_default}}"
    else
        ALLOWED_DOMAINS="*"
    fi
}

wizard_dev_apps_dir() {
    # Only prompt in development environment
    if [[ "$ENVIRONMENT" != "development" ]]; then
        return
    fi
    
    # Default to parent directory of busibox (where app repos typically live)
    local parent_dir
    parent_dir=$(dirname "$REPO_ROOT")
    
    echo ""
    echo -e "┌─ ${BOLD}LOCAL DEVELOPMENT APPS DIRECTORY${NC} ─────────────────────────────────────────┐"
    box_line "" "single"
    box_line "  This directory will be mounted into deploy-api for local app deployment." "single"
    box_line "  Apps in this directory with a busibox.json manifest can be deployed." "single"
    box_line "" "single"
    box_line "  Default: Parent directory of busibox repository" "single"
    box_line "    ${CYAN}${parent_dir}${NC}" "single"
    box_line "" "single"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    read -p "$(echo -e "${BOLD}Dev apps directory [${parent_dir}]:${NC} ")" DEV_APPS_DIR
    DEV_APPS_DIR="${DEV_APPS_DIR:-${parent_dir}}"
    
    # Validate directory exists
    if [[ ! -d "$DEV_APPS_DIR" ]]; then
        warn "Directory does not exist: $DEV_APPS_DIR"
        if confirm "Create this directory?"; then
            mkdir -p "$DEV_APPS_DIR"
            success "Created: $DEV_APPS_DIR"
        else
            error "Dev apps directory must exist"
            exit 1
        fi
    fi
    
    # Show what apps were found
    local app_count=0
    for dir in "$DEV_APPS_DIR"/*/; do
        if [[ -f "${dir}busibox.json" ]]; then
            ((app_count++))
        fi
    done
    
    if [[ $app_count -gt 0 ]]; then
        success "Found $app_count app(s) with busibox.json in $DEV_APPS_DIR"
    else
        info "No apps with busibox.json found yet in $DEV_APPS_DIR"
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
    export POSTGRES_USER="busibox_user"
    export POSTGRES_DB="busibox"
    export POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
    export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
    
    export SSO_JWT_SECRET=$(openssl rand -hex 32)
    export AUTHZ_MASTER_KEY=$(openssl rand -base64 32)
    # LiteLLM uses master_key for authentication - services should use the same key
    export LITELLM_MASTER_KEY="sk-$(openssl rand -hex 16)"
    export LITELLM_API_KEY="${LITELLM_MASTER_KEY}"  # Same as master key for authentication
    export MINIO_ACCESS_KEY="minioadmin"
    export MINIO_SECRET_KEY=$(openssl rand -base64 24 | tr -d '/+=')
    
    # Email/SMTP defaults (can be overridden)
    export EMAIL_FROM="${EMAIL_FROM:-noreply@busibox.local}"
    export SMTP_HOST="${SMTP_HOST:-localhost}"
    export SMTP_PORT="${SMTP_PORT:-25}"
    export SMTP_USER="${SMTP_USER:-}"
    export SMTP_PASSWORD="${SMTP_PASSWORD:-}"
    export SMTP_SECURE="${SMTP_SECURE:-false}"
    
    # GitHub OAuth defaults (should be configured for production)
    export GITHUB_CLIENT_ID="${GITHUB_CLIENT_ID:-CHANGE_ME}"
    export GITHUB_CLIENT_SECRET="${GITHUB_CLIENT_SECRET:-CHANGE_ME}"
    export GITHUB_REDIRECT_URI="${GITHUB_REDIRECT_URI:-https://localhost/portal/api/admin/github/callback}"
    
    # Encryption key (use JWT secret if not set)
    export ENCRYPTION_KEY="${ENCRYPTION_KEY:-${SSO_JWT_SECRET}}"
    
    success "All secrets generated"
}

create_env_file() {
    local env_file
    env_file=$(get_env_file)
    local container_prefix
    container_prefix=$(get_container_prefix)
    
    info "Creating ${env_file}..."
    
    # NOTE: This file contains ONLY non-secret configuration.
    # All secrets are stored in the Ansible vault and injected at deployment time.
    # See: provision/ansible/roles/secrets/vars/vault.yml
    
    cat > "$env_file" << EOF
# Busibox Environment Configuration
# Generated by install.sh on $(date -Iseconds)
#
# IMPORTANT: This file contains NON-SECRET configuration only.
# All secrets are stored in the encrypted Ansible vault:
#   provision/ansible/roles/secrets/vars/vault.yml
#
# Secrets are injected at deployment time by Ansible.

# =============================================================================
# NON-SECRET CONFIGURATION
# =============================================================================

# PostgreSQL (username only - password in vault)
POSTGRES_USER=busibox_user

# MinIO (access key only - secret key in vault)
MINIO_ACCESS_KEY=minioadmin


# Container Naming (allows multiple environments to coexist)
CONTAINER_PREFIX=${container_prefix}
COMPOSE_PROJECT_NAME=${container_prefix}-busibox

# Busibox host path (for volume mounts when deploy-api spawns containers)
BUSIBOX_HOST_PATH="${REPO_ROOT}"

# Docker Development Mode
# - local-dev: Uses local directory mounts for hot-reload (development)
# - github: Clones from GitHub at build time (staging/production)
DOCKER_DEV_MODE=${DOCKER_DEV_MODE:-local-dev}
EOF

    # Add LLM backend config (non-secret)
    if [[ "$LLM_BACKEND" == "cloud" ]]; then
        cat >> "$env_file" << EOF

# Cloud LLM Provider (credentials in vault)
CLOUD_PROVIDER=${CLOUD_PROVIDER:-openai}
LLM_BACKEND=cloud
EOF
        # Add non-secret provider config
        if [[ "${CLOUD_PROVIDER:-openai}" == "openrouter" ]]; then
            echo "OPENAI_API_BASE=https://openrouter.ai/api/v1" >> "$env_file"
        elif [[ "${CLOUD_PROVIDER:-openai}" == "bedrock" ]]; then
            echo "AWS_REGION_NAME=${AWS_REGION:-us-east-1}" >> "$env_file"
        fi
    else
        cat >> "$env_file" << EOF

# Local LLM (${LLM_BACKEND})
LLM_BACKEND=${LLM_BACKEND}
LLM_TIER=${LLM_TIER}
EOF
    fi

    # Environment-specific app configuration
    if [[ "$ENVIRONMENT" == "development" ]]; then
        # Development: mount local directories for hot-reload
        cat >> "$env_file" << EOF

# App Directories (for volume mounts in docker-compose.local-dev.yml)
AI_PORTAL_DIR=${AI_PORTAL_DIR}
AGENT_MANAGER_DIR=${AGENT_MANAGER_DIR}
BUSIBOX_APP_DIR=${BUSIBOX_APP_DIR}
APPS_BASE_DIR=${APPS_BASE_DIR}

# Local Development Apps Directory
DEV_APPS_DIR=${DEV_APPS_DIR:-${APPS_BASE_DIR}}
DEV_APPS_DIR_HOST=${DEV_APPS_DIR:-${APPS_BASE_DIR}}
EOF
    else
        # Staging/Production: clone from GitHub releases
        cat >> "$env_file" << EOF

# GitHub Release Configuration (for docker-compose.github.yml)
AI_PORTAL_GITHUB_REF=${AI_PORTAL_GITHUB_REF:-main}
AGENT_MANAGER_GITHUB_REF=${AGENT_MANAGER_GITHUB_REF:-main}

# Empty local paths (not used in github mode)
AI_PORTAL_DIR=
AGENT_MANAGER_DIR=
BUSIBOX_APP_DIR=
APPS_BASE_DIR=
DEV_APPS_DIR=
DEV_APPS_DIR_HOST=
EOF
    fi
    
    chmod 600 "$env_file"
    success "Created ${env_file} (non-secret config only)"
    info "Secrets will be injected from vault at deployment time"
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

# Load GitHub token from vault (used during resume)
# This sets GITHUB_AUTH_TOKEN environment variable if found in vault
# 
# IMPORTANT: set_vault_environment() should be called first via _update_state_file_for_env()
_load_github_token_from_vault() {
    # Skip if token already set
    if [[ -n "${GITHUB_AUTH_TOKEN:-}" ]]; then
        return 0
    fi
    
    # Check if vault file exists
    if [[ ! -f "$VAULT_FILE" ]]; then
        warn "Vault file not found: $VAULT_FILE"
        return 1
    fi
    
    # Set up vault password if needed
    if is_vault_encrypted; then
        # VAULT_PASS_FILE should be set by set_vault_environment()
        local pass_file="${VAULT_PASS_FILE:-$(get_vault_pass_file)}"
        
        if [[ -n "$pass_file" && -f "$pass_file" ]]; then
            export ANSIBLE_VAULT_PASSWORD_FILE="$pass_file"
            
            # Verify we can actually decrypt the vault
            if ! verify_vault_decryption "$VAULT_FILE" "$pass_file"; then
                error ""
                error "Cannot load secrets - vault decryption failed!"
                error ""
                error "Environment: ${VAULT_ENVIRONMENT:-unknown}"
                error "Vault file: $VAULT_FILE"
                error "Password file: $pass_file"
                error ""
                return 1
            fi
        else
            # Password file not found - need to prompt
            if ! ensure_vault_access; then
                error "Could not access vault - secrets not loaded"
                return 1
            fi
        fi
    fi
    
    # Try to read GitHub token from vault
    local vault_token
    vault_token=$(get_vault_secret "secrets.github.personal_access_token" 2>/dev/null || echo "")
    
    if [[ -n "$vault_token" ]] && [[ "$vault_token" != "null" ]] && [[ "$vault_token" != "CHANGE_ME"* ]]; then
        # Validate the token before accepting it
        if validate_github_token "$vault_token" --quiet 2>/dev/null; then
            export GITHUB_AUTH_TOKEN="$vault_token"
            info "GitHub token loaded from vault and validated"
            return 0
        else
            warn "GitHub token from vault failed validation - will prompt for new token"
            return 1
        fi
    fi
    
    return 1
}

# Load admin config from vault (used during resume)
# This sets ADMIN_EMAIL and ALLOWED_DOMAINS from vault if they're not set or are "null"
#
# IMPORTANT: set_vault_environment() should be called first via _update_state_file_for_env()
_load_admin_config_from_vault() {
    # Check if vault file exists
    if [[ ! -f "$VAULT_FILE" ]]; then
        return 0
    fi
    
    # Set up vault password if needed
    # ANSIBLE_VAULT_PASSWORD_FILE should already be set by _load_github_token_from_vault
    # or ensure_vault_access, but double-check
    if is_vault_encrypted && [[ -z "${ANSIBLE_VAULT_PASSWORD_FILE:-}" ]]; then
        local pass_file="${VAULT_PASS_FILE:-$(get_vault_pass_file)}"
        if [[ -n "$pass_file" && -f "$pass_file" ]]; then
            export ANSIBLE_VAULT_PASSWORD_FILE="$pass_file"
        else
            # Can't load without password
            return 1
        fi
    fi
    
    # Load admin_emails from vault if ADMIN_EMAIL is not set or is "null"
    if [[ -z "${ADMIN_EMAIL:-}" ]] || [[ "${ADMIN_EMAIL}" == "null" ]]; then
        local vault_admin_emails
        vault_admin_emails=$(get_vault_secret "secrets.admin_emails" 2>/dev/null || echo "")
        if [[ -n "$vault_admin_emails" ]] && [[ "$vault_admin_emails" != "null" ]] && [[ "$vault_admin_emails" != "CHANGE_ME"* ]]; then
            ADMIN_EMAIL="$vault_admin_emails"
        fi
    fi
    
    # Load allowed_email_domains from vault if ALLOWED_DOMAINS is not set or is "null"  
    if [[ -z "${ALLOWED_DOMAINS:-}" ]] || [[ "${ALLOWED_DOMAINS}" == "null" ]]; then
        local vault_allowed_domains
        vault_allowed_domains=$(get_vault_secret "secrets.allowed_email_domains" 2>/dev/null || echo "")
        if [[ -n "$vault_allowed_domains" ]] && [[ "$vault_allowed_domains" != "null" ]] && [[ "$vault_allowed_domains" != "CHANGE_ME"* ]]; then
            ALLOWED_DOMAINS="$vault_allowed_domains"
        fi
    fi
    
    return 0
}

# =============================================================================
# DOCKER BOOTSTRAP (Ansible-based)
# =============================================================================

# Bootstrap Docker using Ansible playbook
# This provides idempotent, unified deployment across all environments
bootstrap_docker_ansible() {
    local container_prefix
    container_prefix=$(get_container_prefix)
    
    info "Deploying via Ansible (unified deployment system)..."
    
    # Set environment variables for Ansible
    export CONTAINER_PREFIX="$container_prefix"
    export COMPOSE_PROJECT_NAME="${container_prefix}-busibox"
    export BUSIBOX_HOST_PATH="${REPO_ROOT}"
    export LLM_BACKEND="${LLM_BACKEND:-}"
    export GITHUB_AUTH_TOKEN="${GITHUB_AUTH_TOKEN:-}"
    export ADMIN_EMAIL="${ADMIN_EMAIL:-}"
    
    # Set Docker dev mode based on environment
    # - local-dev: Uses local directory mounts for hot-reload (development)
    # - github: Clones from GitHub at build time (staging/production)
    if [[ "$ENVIRONMENT" == "development" ]]; then
        export DOCKER_DEV_MODE="local-dev"
    else
        export DOCKER_DEV_MODE="github"
    fi
    info "Docker mode: ${DOCKER_DEV_MODE}"
    
    # Navigate to Ansible directory
    local ansible_dir="${REPO_ROOT}/provision/ansible"
    
    if [[ ! -d "$ansible_dir" ]]; then
        error "Ansible directory not found: $ansible_dir"
        return 1
    fi
    
    cd "$ansible_dir"
    
    # Check if Docker inventory exists
    if [[ ! -d "inventory/docker" ]]; then
        error "Docker inventory not found. Run from the repository root."
        return 1
    fi
    
    # Generate environment file first (needed by Docker Compose)
    local env_file
    env_file=$(get_env_file)
    info "Using env file: ${env_file}"
    
    # Check for vault password file
    local vault_args=""
    if [[ -f "${HOME}/.vault_pass" ]]; then
        vault_args="--vault-password-file=${HOME}/.vault_pass"
    elif [[ -f "${HOME}/.busibox-vault-pass-${container_prefix}" ]]; then
        vault_args="--vault-password-file=${HOME}/.busibox-vault-pass-${container_prefix}"
    fi
    
    # Build ansible-playbook command
    local playbook_cmd="ansible-playbook -i inventory/docker docker.yml"
    playbook_cmd+=" -e container_prefix=${container_prefix}"
    playbook_cmd+=" -e busibox_env=${ENVIRONMENT:-development}"
    playbook_cmd+=" -e github_token=${GITHUB_AUTH_TOKEN:-}"
    playbook_cmd+=" -e admin_email=${ADMIN_EMAIL:-}"
    playbook_cmd+=" -e docker_dev_mode=${DOCKER_DEV_MODE:-local-dev}"
    
    if [[ -n "$vault_args" ]]; then
        playbook_cmd+=" $vault_args"
    fi
    
    info "Docker mode: ${DOCKER_DEV_MODE:-local-dev}"
    
    # Helper function to run ansible with proper output handling
    run_ansible() {
        local tags="$1"
        local log_file="${REPO_ROOT}/.ansible-${container_prefix}-${tags}.log"
        
        # Build skip-tags list based on phases before FIRST_UNHEALTHY_PHASE
        # This prevents Ansible from running tasks in healthy phases during reinstall
        local skip_tags=""
        if [[ -n "$FIRST_UNHEALTHY_PHASE" ]]; then
            case "$FIRST_UNHEALTHY_PHASE" in
                infrastructure)
                    # Need to run infrastructure, no skips
                    ;;
                apis)
                    # Skip infrastructure phase
                    skip_tags="infrastructure"
                    ;;
                frontend)
                    # Skip infrastructure and apis phases
                    skip_tags="infrastructure,apis"
                    ;;
            esac
        fi
        
        # Always show full output for now - ansible filtering is tricky
        # Use -v for slightly more verbose output to see what's happening
        echo ""
        info "Running ansible with tags: $tags"
        if [[ -n "$skip_tags" ]]; then
            info "Skipping already-healthy phases: $skip_tags"
            ANSIBLE_FORCE_COLOR=1 $playbook_cmd --tags "$tags" --skip-tags "$skip_tags" -v 2>&1 | tee "$log_file"
        else
            ANSIBLE_FORCE_COLOR=1 $playbook_cmd --tags "$tags" -v 2>&1 | tee "$log_file"
        fi
        local exit_code=${PIPESTATUS[0]}
        
        if [[ $exit_code -ne 0 ]]; then
            error "Ansible failed (exit code: $exit_code). See log: $log_file"
            return 1
        fi
        
        return 0
    }
    
    # ==========================================================================
    # MINIMAL BOOTSTRAP: Deploy only services needed for AI Portal to work
    # The rest (MinIO, Milvus, Data-API, Search-API, Agent-API, LiteLLM, etc.)
    # will be deployed via AI Portal setup wizard using deploy-api
    # ==========================================================================
    
    # Phase 1: PostgreSQL (database)
    show_stage 40 "Deploying PostgreSQL" "Enterprise-grade database with row-level security."
    info "Running: ansible-playbook ... --tags postgres"
    if ! run_ansible "postgres"; then
        error "PostgreSQL deployment failed"
        return 1
    fi
    
    # Phase 2: AuthZ API (needed for admin user creation and authentication)
    show_stage 55 "Deploying AuthZ API" "Zero-trust authentication with OAuth 2.0."
    info "Running: ansible-playbook ... --tags authz"
    if ! run_ansible "authz"; then
        error "AuthZ deployment failed"
        return 1
    fi
    
    # Wait for AuthZ to be ready before creating admin user
    info "Waiting for AuthZ API to be healthy..."
    local max_attempts=30
    local attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:8010/health/live &>/dev/null; then
            success "AuthZ API is ready"
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    # Phase 3: Create Admin User
    show_stage 65 "Creating Admin User" "Setting up admin account with magic link."
    if create_admin_user "$ADMIN_EMAIL"; then
        success "Admin user created successfully"
    else
        warn "Could not create admin user - you'll need to sign up manually"
    fi
    
    # Phase 4: Deploy API (service orchestration - needed to deploy remaining services)
    show_stage 70 "Deploying Deploy API" "Service orchestration and deployment automation."
    info "Running: ansible-playbook ... --tags deploy"
    if ! run_ansible "deploy"; then
        error "Deploy API deployment failed"
        return 1
    fi
    
    # Wait for Deploy API to be ready
    info "Waiting for Deploy API to be healthy..."
    attempt=0
    while [[ $attempt -lt 30 ]]; do
        if curl -sf http://localhost:8011/health/live &>/dev/null; then
            success "Deploy API is ready"
            break
        fi
        sleep 1
        ((attempt++))
    done
    
    # Phase 5: Core Apps (Nginx + AI Portal + Agent Manager)
    # In Docker mode, nginx is bundled inside the core-apps container
    # This mirrors the Proxmox apps-lxc architecture
    show_stage 80 "Deploying Core Apps" "Nginx, AI Portal, and Agent Manager."
    info "Running: ansible-playbook ... --tags core-apps"
    if ! run_ansible "core-apps"; then
        error "Core apps deployment failed"
        return 1
    fi
    
    # Phase 7: Wait for AI Portal to be ready
    show_stage 95 "Waiting for AI Portal" "Verifying services are healthy..."
    info "Waiting for AI Portal to be healthy (this may take a minute on first run)..."
    max_attempts=90
    attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:3000/portal/api/health &>/dev/null; then
            success "AI Portal is ready"
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
    fi
    
    # Note: Additional services (MinIO, Milvus, Data-API, Search-API, Agent-API, 
    # Docs-API, LiteLLM, etc.) will be deployed via AI Portal setup wizard
    
    cd "${REPO_ROOT}"
}

# =============================================================================
# PROXMOX BOOTSTRAP (Ansible-based)
# =============================================================================

# Bootstrap Proxmox LXC containers using Ansible playbook
# This provides idempotent, unified deployment across all environments
bootstrap_proxmox_ansible() {
    info "Deploying to Proxmox via Ansible (unified deployment system)..."
    
    # Determine inventory based on environment
    local inventory_name
    case "$ENVIRONMENT" in
        production) inventory_name="production" ;;
        staging) inventory_name="staging" ;;
        *)
            error "Proxmox deployment requires production or staging environment"
            return 1
            ;;
    esac
    
    # Set environment variables for Ansible
    export BUSIBOX_ENV="$ENVIRONMENT"
    export GITHUB_AUTH_TOKEN="${GITHUB_AUTH_TOKEN:-}"
    export ADMIN_EMAIL="${ADMIN_EMAIL:-}"
    
    # Navigate to Ansible directory
    local ansible_dir="${REPO_ROOT}/provision/ansible"
    
    if [[ ! -d "$ansible_dir" ]]; then
        error "Ansible directory not found: $ansible_dir"
        return 1
    fi
    
    cd "$ansible_dir"
    
    # Check if inventory exists
    if [[ ! -d "inventory/${inventory_name}" ]]; then
        error "Inventory not found: inventory/${inventory_name}"
        return 1
    fi
    
    # =========================================================================
    # PHASE 0: Create/Validate LXC Containers
    # =========================================================================
    # LXC containers must exist before Ansible can deploy to them
    # This step creates containers if they don't exist, or validates existing ones
    
    show_stage 15 "Creating LXC Containers" "Provisioning containers for ${inventory_name} environment."
    
    local pct_dir="${REPO_ROOT}/provision/pct/containers"
    local create_script="${pct_dir}/create_lxc_base.sh"
    
    if [[ ! -f "$create_script" ]]; then
        error "Container creation script not found: $create_script"
        return 1
    fi
    
    info "Running container creation script for ${inventory_name}..."
    
    # Run container creation script
    # This script is idempotent - it will skip existing containers
    local create_log="${REPO_ROOT}/.lxc-create-${inventory_name}.log"
    
    if [[ "$VERBOSE" == true ]]; then
        if ! bash "$create_script" "$inventory_name" 2>&1 | tee "$create_log"; then
            error "LXC container creation failed. See log: $create_log"
            tail -30 "$create_log"
            return 1
        fi
    else
        echo ""
        if ! bash "$create_script" "$inventory_name" 2>&1 | tee "$create_log" | grep -E "(Creating|Starting|Skipping|ERROR|SUCCESS|Step)" || true; then
            # Check log for actual errors
            if grep -qE "(ERROR|FAILED|failed to)" "$create_log" 2>/dev/null; then
                error "LXC container creation failed. See log: $create_log"
                tail -30 "$create_log"
                return 1
            fi
        fi
        
        # Verify script completed successfully by checking log
        if grep -qE "(ERROR|FAILED|failed to)" "$create_log" 2>/dev/null; then
            error "LXC container creation had errors. See log: $create_log"
            tail -30 "$create_log"
            return 1
        fi
    fi
    
    success "LXC containers ready"
    
    # Brief pause to ensure containers are fully started
    sleep 3
    
    # Check for vault password file
    local vault_args=""
    if [[ -f "${HOME}/.vault_pass" ]]; then
        vault_args="--vault-password-file=${HOME}/.vault_pass"
    elif [[ -f "${HOME}/.busibox-vault-pass" ]]; then
        vault_args="--vault-password-file=${HOME}/.busibox-vault-pass"
    else
        warn "No vault password file found. Ansible may fail if vault is encrypted."
    fi
    
    # Build ansible-playbook command
    local playbook_cmd="ansible-playbook -i inventory/${inventory_name} site.yml"
    playbook_cmd+=" -e busibox_env=${ENVIRONMENT}"
    playbook_cmd+=" -e github_token=${GITHUB_AUTH_TOKEN:-}"
    playbook_cmd+=" -e admin_email=${ADMIN_EMAIL:-}"
    
    if [[ -n "$vault_args" ]]; then
        playbook_cmd+=" $vault_args"
    fi
    
    # Helper function to run ansible with proper output handling
    # CRITICAL: This function must properly propagate errors to stop the install
    run_ansible_proxmox() {
        local tags="$1"
        local log_file="${REPO_ROOT}/.ansible-${inventory_name}-${tags}.log"
        local exit_code=0
        
        # Use a named pipe to capture exit code while still showing output
        # This avoids the PIPESTATUS issues with multiple pipes
        
        if [[ "$VERBOSE" == true ]]; then
            # Verbose mode: show all output with colors, capture exit code
            echo ""
            ANSIBLE_FORCE_COLOR=1 $playbook_cmd --tags "$tags" 2>&1 | tee "$log_file"
            exit_code=${PIPESTATUS[0]}
        else
            # Quiet mode: run and show summary, save full log
            # Run ansible in a subshell to capture its exact exit code
            echo ""
            (
                set +e  # Don't exit on error in subshell
                ANSIBLE_FORCE_COLOR=1 $playbook_cmd --tags "$tags" 2>&1
            ) | tee "$log_file" | grep -E "^(TASK|PLAY|ok:|changed:|failed:|fatal:|skipping:|included:|\s+[✓✗])" || true
            
            # Check the log file for failure indicators since PIPESTATUS won't work across subshell
            if grep -qE "(fatal:|failed:|FAILED!|unreachable:)" "$log_file" 2>/dev/null; then
                exit_code=1
            fi
            
            # Also check if ansible itself exited with error (look for error summary)
            if grep -qE "failed=[1-9]|unreachable=[1-9]" "$log_file" 2>/dev/null; then
                exit_code=1
            fi
        fi
        
        if [[ $exit_code -ne 0 ]]; then
            error "Ansible failed (tags: $tags). See log: $log_file"
            echo ""
            echo "Last 30 lines of log:"
            tail -30 "$log_file"
            return 1
        fi
        
        return 0
    }
    
    # Run deployment phases with progress display
    
    # Phase 1: Core Infrastructure (nginx first for web-driven recovery)
    show_stage 30 "Deploying Core Infrastructure" "Nginx, MinIO, PostgreSQL, Milvus via Ansible."
    info "Running: ansible-playbook ... --tags core"
    if ! run_ansible_proxmox "core"; then
        error "Core infrastructure deployment failed"
        return 1
    fi
    
    # Phase 2: LLM Services
    show_stage 50 "Deploying LLM Services" "vLLM, LiteLLM, ColPali via Ansible."
    info "Running: ansible-playbook ... --tags llm"
    if ! run_ansible_proxmox "llm"; then
        error "LLM deployment failed"
        return 1
    fi
    
    # Phase 3: API Services
    show_stage 65 "Deploying API Services" "AuthZ, Data, Search, Agent, Deploy APIs."
    info "Running: ansible-playbook ... --tags apis"
    if ! run_ansible_proxmox "apis"; then
        error "API deployment failed"
        return 1
    fi
    
    # Wait for AuthZ to be ready before creating admin user
    local authz_ip
    case "$ENVIRONMENT" in
        production) authz_ip="10.96.200.210" ;;
        staging) authz_ip="10.96.201.210" ;;
    esac
    
    info "Waiting for AuthZ API to be healthy at ${authz_ip}..."
    local max_attempts=30
    local attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf "http://${authz_ip}:8010/health/live" &>/dev/null; then
            success "AuthZ API is ready"
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    # Phase 4: Create Admin User
    show_stage 75 "Creating Admin User" "Setting up admin account with magic link."
    # For Proxmox, we need to call the remote AuthZ API
    export AUTHZ_BASE_URL="http://${authz_ip}:8010"
    if create_admin_user "$ADMIN_EMAIL"; then
        success "Admin user created successfully"
    else
        warn "Could not create admin user - you'll need to sign up manually"
    fi
    
    # Phase 5: Frontend Apps
    show_stage 85 "Deploying Frontend Apps" "AI Portal, Agent Manager via Ansible."
    info "Running: ansible-playbook ... --tags apps"
    if ! run_ansible_proxmox "apps"; then
        error "Frontend deployment failed"
        return 1
    fi
    
    # Phase 6: Wait for AI Portal
    local portal_ip
    case "$ENVIRONMENT" in
        production) portal_ip="10.96.200.201" ;;
        staging) portal_ip="10.96.201.201" ;;
    esac
    
    show_stage 95 "Waiting for AI Portal" "Verifying services are healthy..."
    info "Waiting for AI Portal to be healthy at ${portal_ip}..."
    max_attempts=90
    attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf "http://${portal_ip}:3000/portal/api/health" &>/dev/null; then
            success "AI Portal is ready"
            break
        fi
        sleep 2
        ((attempt++))
    done
    
    if [[ $attempt -ge $max_attempts ]]; then
        warn "AI Portal health check timed out, but it may still be starting"
    fi
    
    cd "${REPO_ROOT}"
}

# =============================================================================
# DOCKER BOOTSTRAP (Legacy docker-compose based)
# =============================================================================

bootstrap_docker() {
    # Set container prefix for docker compose
    local container_prefix
    container_prefix=$(get_container_prefix)
    export CONTAINER_PREFIX="$container_prefix"
    export COMPOSE_PROJECT_NAME="${container_prefix}-busibox"
    
    # Set BUSIBOX_HOST_PATH for deploy-api (needed for volume mounts in spawned containers)
    export BUSIBOX_HOST_PATH="${REPO_ROOT}"
    
    # Export LLM_BACKEND so deploy-api knows what hardware is on the host
    export LLM_BACKEND="${LLM_BACKEND:-}"
    
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
        docker compose $compose_files up -d --no-deps postgres
    else
        docker compose $compose_files up -d --no-deps postgres 2>&1 | grep -v "^$" || true
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
        ADMIN_EMAIL="${ADMIN_EMAIL}" docker compose $compose_files up -d --no-deps authz-api
    else
        ADMIN_EMAIL="${ADMIN_EMAIL}" docker compose $compose_files up -d --no-deps authz-api 2>&1 | grep -v "^$" || true
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
    # PHASE 3.5: Deploy API (Service Orchestration)
    # ==========================================================================
    show_stage 68 "Building Deploy API" "Service orchestration and deployment automation."
    
    # Build deploy-api first
    info "Building deploy-api container..."
    if [[ "$VERBOSE" == true ]]; then
        docker compose $compose_files build deploy-api
    else
        docker compose $compose_files build deploy-api 2>&1 | tail -10 || true
    fi
    
    show_stage 70 "Starting Deploy API" "Starting deployment orchestration service."
    
    # BUSIBOX_HOST_PATH must be passed explicitly as docker compose may not pick it up from env file
    if [[ "$VERBOSE" == true ]]; then
        BUSIBOX_HOST_PATH="${BUSIBOX_HOST_PATH}" docker compose $compose_files up -d --no-deps deploy-api
    else
        BUSIBOX_HOST_PATH="${BUSIBOX_HOST_PATH}" docker compose $compose_files up -d --no-deps deploy-api 2>&1 | grep -v "^$" || true
    fi
    
    # Wait for deploy-api
    info "Waiting for Deploy API to be healthy..."
    attempt=0
    while [[ $attempt -lt 30 ]]; do
        if curl -sf http://localhost:8011/health/live > /dev/null 2>&1; then
            success "Deploy API is ready"
            break
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    
    if [[ $attempt -ge 30 ]]; then
        warn "Deploy API health check timeout - continuing anyway"
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
        set_state "MAGIC_LINK_TOKEN" "$token"
    fi
    
    # Return proper setup URL with magic link token
    if [[ "$BASE_DOMAIN" == "localhost" ]]; then
        echo "https://localhost/portal/verify?token=${token}"
    else
        echo "https://${BASE_DOMAIN}/portal/verify?token=${token}"
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
    if [[ "${LLM_BACKEND:-}" == "mlx" ]]; then
        box_line "  • Host Agent     - MLX control service (localhost:8089)" "double" "${GREEN}"
        box_line "" "double" "${GREEN}"
        box_line "  ${BOLD}MLX:${NC} Test model downloaded. Start via AI Portal or run:" "double" "${GREEN}"
        box_line "       scripts/llm/start-mlx-server.sh" "double" "${GREEN}"
    fi
    box_line "" "double" "${GREEN}"
    box_line "  ${BOLD}Note:${NC} Your browser will show a certificate warning (self-signed SSL)." "double" "${GREEN}"
    box_line "  Click 'Advanced' and proceed to continue." "double" "${GREEN}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# =============================================================================
# MLX SETUP (Apple Silicon)
# =============================================================================

# MLX virtual environment path
MLX_VENV_DIR="${HOME}/.busibox/mlx-venv"

# Setup or activate the MLX virtual environment
# This is required on modern macOS due to PEP 668 (externally-managed-environment)
setup_mlx_venv() {
    # Check for Python 3
    if ! command -v python3 &>/dev/null; then
        error "Python 3 is required for MLX but not found"
        return 1
    fi
    
    # Create ~/.busibox directory if it doesn't exist
    mkdir -p "${HOME}/.busibox"
    
    # Create virtual environment if it doesn't exist
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        info "Creating MLX virtual environment at ${MLX_VENV_DIR}..."
        python3 -m venv "$MLX_VENV_DIR" || {
            error "Failed to create virtual environment"
            return 1
        }
        success "Virtual environment created"
    fi
    
    # Save venv path to state for other scripts to use
    set_state "MLX_VENV_DIR" "$MLX_VENV_DIR"
    
    return 0
}

# Get the path to the MLX venv Python
get_mlx_python() {
    echo "${MLX_VENV_DIR}/bin/python3"
}

# Get the path to the MLX venv pip
get_mlx_pip() {
    echo "${MLX_VENV_DIR}/bin/pip3"
}

# Global variable to track background model download PID
MODEL_DOWNLOAD_PID=""

# Start downloading the test model in background (called early in install)
# This allows the model to download while Docker containers are being built
start_model_download_background() {
    if [[ "$LLM_BACKEND" != "mlx" ]]; then
        return 0
    fi
    
    info "Starting model download in background..."
    
    # Setup virtual environment first
    setup_mlx_venv || return 1
    
    local mlx_python
    local mlx_pip
    mlx_python=$(get_mlx_python)
    mlx_pip=$(get_mlx_pip)
    
    # Install huggingface_hub first (needed for download)
    "$mlx_pip" install -q huggingface_hub 2>/dev/null || {
        warn "Could not install huggingface_hub - model will be downloaded later"
        return 0
    }
    
    # Check if model is already cached
    local test_model="mlx-community/Qwen3-0.6B-4bit"
    local cache_dir="${HOME}/.cache/huggingface/hub"
    local model_dir="${cache_dir}/models--${test_model//\//-}"
    
    if [[ -d "$model_dir" ]]; then
        info "Test model already cached, skipping background download"
        return 0
    fi
    
    # Start download in background
    (
        "$mlx_python" -c "
from huggingface_hub import snapshot_download
import sys
try:
    snapshot_download('${test_model}', local_dir_use_symlinks=True)
except Exception as e:
    print(f'Background download failed: {e}', file=sys.stderr)
    sys.exit(1)
" &>/dev/null
    ) &
    
    MODEL_DOWNLOAD_PID=$!
    set_state "MODEL_DOWNLOAD_PID" "$MODEL_DOWNLOAD_PID"
    info "Model download started in background (PID: ${MODEL_DOWNLOAD_PID})"
    
    return 0
}

# Wait for background model download to complete (called at end of install)
wait_for_model_download() {
    if [[ -z "$MODEL_DOWNLOAD_PID" ]]; then
        # Try to restore from state
        MODEL_DOWNLOAD_PID=$(get_state "MODEL_DOWNLOAD_PID" "")
    fi
    
    if [[ -z "$MODEL_DOWNLOAD_PID" ]]; then
        return 0
    fi
    
    # Check if process is still running
    if kill -0 "$MODEL_DOWNLOAD_PID" 2>/dev/null; then
        info "Waiting for background model download to complete..."
        wait "$MODEL_DOWNLOAD_PID" 2>/dev/null || true
        success "Model download complete"
    fi
    
    # Clear the PID from state
    set_state "MODEL_DOWNLOAD_PID" ""
}

# Global variable to track background embedding download PID
EMBEDDING_DOWNLOAD_PID=""

# Start downloading embedding model in background (called early in install)
# This allows the embedding model to download while Docker containers are being built
# Uses Docker because fastembed requires Python <3.13 (onnxruntime compatibility)
start_embedding_download_background() {
    # For Proxmox, use the host-based download script
    if [[ "$PLATFORM" == "proxmox" ]]; then
        info "Downloading embedding models to Proxmox host cache..."
        local setup_script="${REPO_ROOT}/provision/pct/host/setup-embedding-models.sh"
        
        if [[ ! -f "$setup_script" ]]; then
            warn "Embedding model setup script not found: ${setup_script}"
            warn "Models will be downloaded on first container start"
            return 0
        fi
        
        # Run the setup script in background
        (
            bash "$setup_script" 2>&1 | sed 's/^/  /'
        ) &
        EMBEDDING_DOWNLOAD_PID=$!
        set_state "EMBEDDING_DOWNLOAD_PID" "$EMBEDDING_DOWNLOAD_PID"
        
        info "Embedding model download started in background (PID: ${EMBEDDING_DOWNLOAD_PID})"
        return 0
    fi
    
    # For Docker, check if Docker is available
    if ! command -v docker &>/dev/null; then
        warn "Docker not available - embedding model will be downloaded later"
        return 0
    fi
    
    # Check if Docker daemon is running
    if ! docker info &>/dev/null 2>&1; then
        warn "Docker daemon not running - embedding model will be downloaded later"
        return 0
    fi
    
    local embedding_model
    embedding_model=$(get_embedding_model_for_env)
    
    # FastEmbed cache location
    local fastembed_cache="${HOME}/.cache/fastembed"
    mkdir -p "$fastembed_cache"
    
    # Check if model is already cached
    local model_size=""
    case "$embedding_model" in
        *small*) model_size="small" ;;
        *base*) model_size="base" ;;
        *large*) model_size="large" ;;
    esac
    
    if [[ -n "$model_size" ]]; then
        if find "${fastembed_cache}" -name "model*.onnx" -path "*bge-${model_size}*" 2>/dev/null | grep -q .; then
            info "Embedding model already cached, skipping background download"
            return 0
        fi
    fi
    
    # Show size estimate
    local size_info=""
    case "$embedding_model" in
        *small*) size_info="(~134MB)" ;;
        *base*) size_info="(~438MB)" ;;
        *large*) size_info="(~1.3GB)" ;;
    esac
    
    info "Starting embedding model download in background: ${embedding_model} ${size_info}"
    
    # Start download in background using Docker
    (
        docker run --rm \
            -v "${fastembed_cache}:/root/.cache/fastembed" \
            python:3.11-slim \
            bash -c "
                pip install -q fastembed && \
                python -c \"
from fastembed import TextEmbedding
model = '${embedding_model}'
cache_dir = '/root/.cache/fastembed'
embedder = TextEmbedding(model_name=model, cache_dir=cache_dir)
list(embedder.embed(['warmup test']))
\"
            " &>/dev/null
    ) &
    
    EMBEDDING_DOWNLOAD_PID=$!
    set_state "EMBEDDING_DOWNLOAD_PID" "$EMBEDDING_DOWNLOAD_PID"
    info "Embedding model download started in background (PID: ${EMBEDDING_DOWNLOAD_PID})"
    
    return 0
}

# Wait for background embedding download to complete
wait_for_embedding_download() {
    if [[ -z "$EMBEDDING_DOWNLOAD_PID" ]]; then
        # Try to restore from state
        EMBEDDING_DOWNLOAD_PID=$(get_state "EMBEDDING_DOWNLOAD_PID" "")
    fi
    
    if [[ -z "$EMBEDDING_DOWNLOAD_PID" ]]; then
        return 0
    fi
    
    # Check if process is still running
    if kill -0 "$EMBEDDING_DOWNLOAD_PID" 2>/dev/null; then
        info "Waiting for background embedding model download to complete..."
        wait "$EMBEDDING_DOWNLOAD_PID" 2>/dev/null || true
        success "Embedding model download complete"
    fi
    
    # Clear the PID from state
    set_state "EMBEDDING_DOWNLOAD_PID" ""
}

# Get the appropriate embedding model based on environment
# Development/demo use small model (faster), staging/production use large (better quality)
get_embedding_model_for_env() {
    # Check if explicitly set
    if [[ -n "${FASTEMBED_MODEL:-}" ]]; then
        echo "$FASTEMBED_MODEL"
        return
    fi
    
    # Choose based on environment
    case "${ENVIRONMENT:-development}" in
        staging|production)
            echo "BAAI/bge-large-en-v1.5"
            ;;
        *)
            # development, demo, or unset - use small model
            echo "BAAI/bge-small-en-v1.5"
            ;;
    esac
}

# Download FastEmbed embedding model for offline use
# This pre-downloads the model so embedding-api doesn't need to download on first run
# Uses Docker because fastembed requires Python <3.13 (onnxruntime compatibility)
download_embedding_model() {
    local embedding_model
    embedding_model=$(get_embedding_model_for_env)
    
    info "Preparing FastEmbed model: ${embedding_model}"
    
    # FastEmbed cache location - we use ~/.cache/fastembed to match what Docker containers expect
    local fastembed_cache="${HOME}/.cache/fastembed"
    mkdir -p "$fastembed_cache"
    
    # Check if model is already cached
    # FastEmbed uses HuggingFace hub cache format: qdrant/bge-small-en-v1.5-onnx-q
    # Extract model size to match the right cached model
    local model_size=""
    case "$embedding_model" in
        *small*) model_size="small" ;;
        *base*) model_size="base" ;;
        *large*) model_size="large" ;;
    esac
    
    local is_cached=false
    if [[ -n "$model_size" ]]; then
        if find "${fastembed_cache}" -name "model*.onnx" -path "*bge-${model_size}*" 2>/dev/null | grep -q .; then
            is_cached=true
        fi
    fi
    
    if [[ "$is_cached" == "true" ]]; then
        success "Embedding model already cached: ${embedding_model}"
        # Still need to save the cache path and model to env/state
        save_fastembed_config "$fastembed_cache" "$embedding_model"
        return 0
    fi
    
    # Check if Docker is available
    if ! command -v docker &>/dev/null; then
        warn "Docker not available - embedding model will be downloaded on first container start"
        save_fastembed_config "$fastembed_cache" "$embedding_model"
        return 0
    fi
    
    # Show size estimate
    local size_info=""
    case "$embedding_model" in
        *small*) size_info="(~134MB)" ;;
        *base*) size_info="(~438MB)" ;;
        *large*) size_info="(~1.3GB)" ;;
    esac
    
    info "Downloading embedding model: ${embedding_model} ${size_info}"
    info "Using Docker (fastembed requires Python <3.13)"
    info "Cache location: ${fastembed_cache}"
    
    # Use Docker to download the model
    docker run --rm \
        -v "${fastembed_cache}:/root/.cache/fastembed" \
        python:3.11-slim \
        bash -c "
            pip install -q fastembed && \
            python -c \"
from fastembed import TextEmbedding
model = '${embedding_model}'
cache_dir = '/root/.cache/fastembed'
print(f'Downloading {model}...')
embedder = TextEmbedding(model_name=model, cache_dir=cache_dir)
list(embedder.embed(['warmup test']))
print('Download complete!')
\"
        " || {
        warn "Failed to download embedding model - it will be downloaded on first container start"
        save_fastembed_config "$fastembed_cache" "$embedding_model"
        return 0  # Don't fail the installation
    }
    
    success "Embedding model downloaded and verified"
    info "  Model: ${embedding_model}"
    info "  Location: ${fastembed_cache}"
    
    # Save cache path and model to env/state for Docker to use
    save_fastembed_config "$fastembed_cache" "$embedding_model"
    
    return 0
}

# Get embedding dimension based on model
get_embedding_dimension() {
    local model="$1"
    case "$model" in
        *small*) echo "384" ;;
        *base*) echo "768" ;;
        *large*) echo "1024" ;;
        *) echo "1024" ;;  # Default to large dimension
    esac
}

# Save FastEmbed config (cache path and model) to env file and state
save_fastembed_config() {
    local fastembed_cache="$1"
    local embedding_model="$2"
    local env_file
    env_file=$(get_env_file)
    
    # Determine embedding dimension based on model
    local dimension
    dimension=$(get_embedding_dimension "$embedding_model")
    
    # Save model name and dimension to state for warmup script to use
    set_state "FASTEMBED_MODEL" "$embedding_model"
    set_state "FASTEMBED_HOST_CACHE" "$fastembed_cache"
    set_state "EMBEDDING_DIMENSION" "$dimension"
    
    # Check if FASTEMBED_HOST_CACHE is already in env file
    if grep -q "^FASTEMBED_HOST_CACHE=" "$env_file" 2>/dev/null; then
        # Update existing value
        sed -i.bak "s|^FASTEMBED_HOST_CACHE=.*|FASTEMBED_HOST_CACHE=${fastembed_cache}|" "$env_file"
        rm -f "${env_file}.bak"
    else
        # Add new entry
        echo "" >> "$env_file"
        echo "# FastEmbed model cache (pre-downloaded for fast container startup)" >> "$env_file"
        echo "FASTEMBED_HOST_CACHE=${fastembed_cache}" >> "$env_file"
    fi
    
    # Also save the model name to env file
    if grep -q "^FASTEMBED_MODEL=" "$env_file" 2>/dev/null; then
        sed -i.bak "s|^FASTEMBED_MODEL=.*|FASTEMBED_MODEL=${embedding_model}|" "$env_file"
        rm -f "${env_file}.bak"
    else
        echo "FASTEMBED_MODEL=${embedding_model}" >> "$env_file"
    fi
    
    # Save embedding dimension to env file
    if grep -q "^EMBEDDING_DIMENSION=" "$env_file" 2>/dev/null; then
        sed -i.bak "s|^EMBEDDING_DIMENSION=.*|EMBEDDING_DIMENSION=${dimension}|" "$env_file"
        rm -f "${env_file}.bak"
    else
        echo "EMBEDDING_DIMENSION=${dimension}" >> "$env_file"
    fi
    
    info "Set embedding dimension to ${dimension} for model ${embedding_model}"
}

# Install MLX dependencies and download tiny test model
setup_mlx() {
    if [[ "$LLM_BACKEND" != "mlx" ]]; then
        return 0
    fi
    
    show_stage 92 "Setting up MLX" "Installing MLX-LM and downloading models for Apple Silicon."
    
    # Setup virtual environment first
    setup_mlx_venv || return 1
    
    local mlx_python
    local mlx_pip
    mlx_python=$(get_mlx_python)
    mlx_pip=$(get_mlx_pip)
    
    # Install mlx-lm and huggingface_hub if not already installed
    info "Installing MLX-LM dependencies into virtual environment..."
    if ! "$mlx_python" -c "import mlx_lm" 2>/dev/null; then
        "$mlx_pip" install -q mlx-lm huggingface_hub || {
            error "Failed to install mlx-lm"
            return 1
        }
        success "MLX-LM installed"
    else
        info "MLX-LM already installed"
    fi
    
    # Download small test model (Qwen3-0.6B-4bit ~400MB)
    # Larger models are managed by deploy-api and can be downloaded via AI Portal
    local test_model="mlx-community/Qwen3-0.6B-4bit"
    
    # Check if model is already cached (may have been downloaded in background)
    local cache_dir="${HOME}/.cache/huggingface/hub"
    local model_dir="${cache_dir}/models--${test_model//\//-}"
    
    if [[ -d "$model_dir" ]]; then
        info "Test model already cached (downloaded in background)"
        success "Test model ready"
    else
        info "Downloading test model: ${test_model}"
        info "This is a small model (~400MB) to verify MLX works."
        info "Larger models can be downloaded via the AI Portal later."
        
        "$mlx_python" -c "
from huggingface_hub import snapshot_download
import os

model = '${test_model}'
print(f'Downloading {model}...')
try:
    snapshot_download(model, local_dir_use_symlinks=True)
    print('Download complete!')
except Exception as e:
    print(f'Download failed: {e}')
    exit(1)
" || {
            warn "Failed to download test model - you can download it later via the AI Portal"
            return 0  # Don't fail the installation
        }
        
        success "Test model downloaded"
    fi
    
    # Download embedding model for FastEmbed (used by embedding-api container)
    info ""
    download_embedding_model
    
    # Save MLX state
    set_state "MLX_INSTALLED" "true"
    set_state "MLX_TEST_MODEL" "$test_model"
    
    return 0
}

# Install and start the host-agent for MLX control
setup_host_agent() {
    if [[ "$LLM_BACKEND" != "mlx" ]]; then
        return 0
    fi
    
    show_stage 94 "Setting up Host Agent" "Installing host-agent for MLX control from Docker containers."
    
    local host_agent_dir="${REPO_ROOT}/scripts/host-agent"
    
    if [[ ! -f "${host_agent_dir}/host-agent.py" ]]; then
        error "Host agent script not found at ${host_agent_dir}/host-agent.py"
        return 1
    fi
    
    # Ensure MLX venv is set up (should already be done by setup_mlx, but be safe)
    setup_mlx_venv || return 1
    
    local mlx_pip
    mlx_pip=$(get_mlx_pip)
    
    # Install host-agent dependencies into the MLX venv
    info "Installing host-agent dependencies into virtual environment..."
    "$mlx_pip" install -q fastapi uvicorn httpx pyyaml || {
        error "Failed to install host-agent dependencies"
        return 1
    }
    
    # Generate host-agent token
    local host_agent_token
    host_agent_token=$(openssl rand -hex 32)
    
    # Save to env file
    local env_file
    env_file=$(get_env_file)
    echo "" >> "$env_file"
    echo "# Host Agent (for MLX control)" >> "$env_file"
    echo "HOST_AGENT_TOKEN=${host_agent_token}" >> "$env_file"
    echo "HOST_AGENT_PORT=8089" >> "$env_file"
    
    # Save to state
    set_state "HOST_AGENT_TOKEN" "$host_agent_token"
    set_state "HOST_AGENT_PORT" "8089"
    
    # Run the launchd installer
    info "Installing host-agent as background service..."
    if [[ -f "${host_agent_dir}/install-host-agent.sh" ]]; then
        bash "${host_agent_dir}/install-host-agent.sh" || {
            warn "Failed to install host-agent as service - you can start it manually"
            info "Run: $(get_mlx_python) ${host_agent_dir}/host-agent.py"
            return 0
        }
        success "Host agent installed and started"
    else
        warn "Host agent installer not found - skipping service installation"
        info "Run manually: $(get_mlx_python) ${host_agent_dir}/host-agent.py"
    fi
    
    # Wait for host-agent to be ready
    info "Waiting for host-agent to be ready..."
    local max_attempts=10
    local attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:8089/health &>/dev/null; then
            success "Host agent is ready"
            return 0
        fi
        sleep 1
        ((attempt++))
    done
    
    warn "Host agent health check timeout - it may still be starting"
    return 0
}

# Ensure MLX server is running before AI Portal setup
# This is called after all MLX setup is complete and models are downloaded
ensure_mlx_running() {
    if [[ "$LLM_BACKEND" != "mlx" ]]; then
        return 0
    fi
    
    show_stage 96 "Starting MLX Server" "Ensuring MLX is ready for AI Portal setup wizard."
    
    # Check if MLX is already running
    if curl -sf http://localhost:8080/v1/models &>/dev/null; then
        success "MLX server is already running"
        return 0
    fi
    
    info "MLX server not running, starting it..."
    
    # Try to start via host-agent first (if it's running)
    local host_agent_token
    host_agent_token=$(get_state "HOST_AGENT_TOKEN" 2>/dev/null || echo "")
    
    if curl -sf http://localhost:8089/health &>/dev/null; then
        info "Using host-agent to start MLX..."
        
        # Get the test model (small model for quick startup)
        local test_model
        test_model=$(get_state "MLX_TEST_MODEL" 2>/dev/null || echo "mlx-community/Qwen3-0.6B-4bit")
        
        local start_response
        if [[ -n "$host_agent_token" ]]; then
            start_response=$(curl -sf -X POST http://localhost:8089/mlx/start \
                -H "Content-Type: application/json" \
                -H "Authorization: Bearer ${host_agent_token}" \
                -d "{\"model\": \"${test_model}\"}" 2>/dev/null)
        else
            start_response=$(curl -sf -X POST http://localhost:8089/mlx/start \
                -H "Content-Type: application/json" \
                -d "{\"model\": \"${test_model}\"}" 2>/dev/null)
        fi
        
        if [[ -n "$start_response" ]]; then
            info "MLX start command sent via host-agent"
        else
            warn "Failed to start MLX via host-agent, trying direct start..."
        fi
    fi
    
    # Wait for MLX to be ready (either from host-agent start or direct start)
    info "Waiting for MLX server to be ready..."
    local max_attempts=60
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf http://localhost:8080/v1/models &>/dev/null; then
            success "MLX server is ready"
            return 0
        fi
        sleep 2
        ((attempt++))
        if [[ $((attempt % 10)) -eq 0 ]]; then
            echo -n "."
        fi
    done
    echo ""
    
    # If still not running, try direct start as fallback
    if ! curl -sf http://localhost:8080/v1/models &>/dev/null; then
        warn "MLX server still not ready, attempting direct start..."
        
        local mlx_script="${REPO_ROOT}/scripts/llm/start-mlx-server.sh"
        if [[ -f "$mlx_script" ]]; then
            # Start MLX server in background
            bash "$mlx_script" agent &
            
            # Wait again
            attempt=0
            while [[ $attempt -lt 30 ]]; do
                if curl -sf http://localhost:8080/v1/models &>/dev/null; then
                    success "MLX server started successfully"
                    return 0
                fi
                sleep 2
                ((attempt++))
            done
        fi
    fi
    
    # Final check
    if curl -sf http://localhost:8080/v1/models &>/dev/null; then
        success "MLX server is running"
        return 0
    else
        warn "MLX server could not be started automatically"
        info "You can start it manually with: make mlx-start"
        info "The AI Portal setup wizard will also try to start it"
        return 0  # Don't fail installation, just warn
    fi
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
    
    # Update state file path for demo environment
    _update_state_file_for_env
    
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
    
    # Save demo mode settings to state
    set_state "ENVIRONMENT" "$ENVIRONMENT"
    set_state "PLATFORM" "$PLATFORM"
    set_state "LLM_BACKEND" "$LLM_BACKEND"
    set_state "LLM_TIER" "${LLM_TIER:-}"
    set_state "ADMIN_EMAIL" "$ADMIN_EMAIL"
    set_state "BASE_DOMAIN" "$BASE_DOMAIN"
    set_state "ALLOWED_DOMAINS" "$ALLOWED_DOMAINS"
    set_install_phase "wizard_complete"
}

# =============================================================================
# PREREQUISITES CHECK
# =============================================================================

check_prerequisites() {
    info "Checking prerequisites..."
    
    local errors=0
    
    # Platform-specific checks
    if [[ "$PLATFORM" == "docker" ]]; then
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
    elif [[ "$PLATFORM" == "proxmox" ]]; then
        # Proxmox Container Tools
        if ! command -v pct &>/dev/null; then
            error "Proxmox container tools (pct) not found. Are you on a Proxmox host?"
            ((errors++))
        else
            success "Proxmox container tools available"
        fi
        
        # Check if running as root (required for Proxmox)
        if [[ "$(id -u)" != "0" ]]; then
            error "Proxmox installation must be run as root"
            ((errors++))
        else
            success "Running as root"
        fi
        
        # Ansible
        if ! command -v ansible-playbook &>/dev/null; then
            error "Ansible is not installed"
            ((errors++))
        else
            success "Ansible available"
        fi
    fi
    
    # Common checks
    
    # Ansible (required for both platforms now)
    if ! command -v ansible-playbook &>/dev/null; then
        warn "Ansible not installed - some features may not work"
    else
        success "Ansible available"
    fi
    
    # RAM check (skip on Proxmox as containers have separate resources)
    if [[ "$PLATFORM" == "docker" ]]; then
        local min_ram=8
        if [[ $DETECTED_RAM_GB -lt $min_ram ]]; then
            error "Minimum ${min_ram}GB RAM required, found ${DETECTED_RAM_GB}GB"
            ((errors++))
        else
            success "${DETECTED_RAM_GB}GB RAM available"
        fi
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

# Define install order for services (used for health check validation)
# Services are checked in this order; if any is unhealthy, we resume from there
# Minimal bootstrap services deployed via CLI
# Other services (redis, minio, milvus, litellm, data-api, search-api, agent-api, docs-api)
# are managed by deploy-api after the initial bootstrap
INSTALL_SERVICES_ORDER=(
    "postgres:5432:infrastructure"
    "authz-api:8010:apis"
    "deploy-api:8011:apis"
    "core-apps:443:frontend"  # Includes nginx, ai-portal, agent-manager
)

# Validate container health in installation order
# Returns: 0 if all healthy, 1 if any unhealthy
# Sets: FIRST_UNHEALTHY_SERVICE, FIRST_UNHEALTHY_PHASE
validate_install_health() {
    local env_prefix="$1"
    
    FIRST_UNHEALTHY_SERVICE=""
    FIRST_UNHEALTHY_PHASE=""
    
    # Check if Docker daemon is running
    if ! docker info &>/dev/null 2>&1; then
        FIRST_UNHEALTHY_SERVICE="docker-daemon"
        FIRST_UNHEALTHY_PHASE="infrastructure"
        return 1
    fi
    
    # Get running containers
    export CONTAINER_PREFIX="$env_prefix"
    local running_containers
    running_containers=$(docker ps --format '{{.Names}}' 2>/dev/null | grep "^${env_prefix}-" || echo "")
    
    if [[ -z "$running_containers" ]]; then
        FIRST_UNHEALTHY_SERVICE="all-containers"
        FIRST_UNHEALTHY_PHASE="infrastructure"
        return 1
    fi
    
    # Check each service in order
    for service_entry in "${INSTALL_SERVICES_ORDER[@]}"; do
        local service_name="${service_entry%%:*}"
        local rest="${service_entry#*:}"
        local port="${rest%%:*}"
        local phase="${rest#*:}"
        
        # Check if container exists and is running
        local container_name="${env_prefix}-${service_name}"
        
        # Skip if service uses different container name
        case "$service_name" in
            core-apps)
                # Check nginx port on core-apps
                if ! echo "$running_containers" | grep -q "${env_prefix}-core-apps"; then
                    FIRST_UNHEALTHY_SERVICE="$service_name"
                    FIRST_UNHEALTHY_PHASE="$phase"
                    return 1
                fi
                # Also check port health
                if ! nc -z localhost "$port" 2>/dev/null && ! timeout 2 bash -c "echo > /dev/tcp/localhost/$port" 2>/dev/null; then
                    FIRST_UNHEALTHY_SERVICE="$service_name"
                    FIRST_UNHEALTHY_PHASE="$phase"
                    return 1
                fi
                ;;
            *)
                # Standard container check
                if ! echo "$running_containers" | grep -qE "(^|-)${service_name}$"; then
                    FIRST_UNHEALTHY_SERVICE="$service_name"
                    FIRST_UNHEALTHY_PHASE="$phase"
                    return 1
                fi
                # Port check with short timeout
                if ! nc -z localhost "$port" 2>/dev/null && ! timeout 2 bash -c "echo > /dev/tcp/localhost/$port" 2>/dev/null; then
                    FIRST_UNHEALTHY_SERVICE="$service_name"
                    FIRST_UNHEALTHY_PHASE="$phase"
                    return 1
                fi
                ;;
        esac
    done
    
    return 0
}

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
    
    # Check if bootstrap is complete according to state
    if [[ "$install_status" == "installed" || "$install_phase" == "complete" ]]; then
        # IMPORTANT: Validate that services are actually healthy
        # State says "installed" but containers may be missing/stopped
        info "Validating installation health (checking ${#INSTALL_SERVICES_ORDER[@]} services)..."
        
        if ! validate_install_health "$env_prefix"; then
            # Services are unhealthy - treat as interrupted install
            echo ""
            echo -e "${YELLOW}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
            box_line "                     ${BOLD}INSTALLATION INCOMPLETE${NC}" "double" "${YELLOW}"
            echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
            echo ""
            echo -e "  State file indicates installation is complete, but services are not healthy."
            echo ""
            echo -e "  First unhealthy service: ${BOLD}${FIRST_UNHEALTHY_SERVICE}${NC}"
            echo -e "  Phase: ${BOLD}${FIRST_UNHEALTHY_PHASE}${NC}"
            echo ""
            
            if [[ "$NO_PROMPT" != true ]]; then
                echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
                box_line "" "single"
                box_line "  ${CYAN}1)${NC} Resume            Continue install from ${FIRST_UNHEALTHY_SERVICE}" "single"
                box_line "  ${CYAN}2)${NC} Start fresh       Delete existing stack and start over" "single"
                box_line "  ${CYAN}3)${NC} Exit              Do nothing" "single"
                box_line "" "single"
                echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
                echo ""
                
                while true; do
                    read -p "$(echo -e "${BOLD}Choice [1]:${NC} ")" choice
                    case "${choice:-1}" in
                        1)
                            info "Resuming installation from ${FIRST_UNHEALTHY_PHASE} phase..."
                            # Update install phase to resume from the right point
                            set_state "INSTALL_PHASE" "$FIRST_UNHEALTHY_PHASE"
                            set_state "INSTALL_STATUS" "interrupted"
                            return 0  # Resume
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
                # Non-interactive mode - auto-resume from failed point
                info "Auto-resuming installation from ${FIRST_UNHEALTHY_PHASE} phase..."
                set_state "INSTALL_PHASE" "$FIRST_UNHEALTHY_PHASE"
                set_state "INSTALL_STATUS" "interrupted"
                return 0  # Resume
            fi
        fi
        
        # Services are healthy - installation is truly complete
        success "All services are healthy"
        
        # Show magic link and open browser
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
                        # Ensure MLX is running on Apple Silicon before opening portal
                        LLM_BACKEND=$(get_state "LLM_BACKEND" 2>/dev/null || echo "")
                        if [[ "$LLM_BACKEND" == "mlx" ]]; then
                            ensure_mlx_running
                        fi
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
            # Ensure MLX is running on Apple Silicon before opening portal
            LLM_BACKEND=$(get_state "LLM_BACKEND" 2>/dev/null || echo "")
            if [[ "$LLM_BACKEND" == "mlx" ]]; then
                ensure_mlx_running
            fi
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
    
    # Determine environment prefix for state checking
    local env_prefix=""
    local resuming=false
    
    if [[ "$DEMO_MODE" == true ]]; then
        env_prefix="demo"
        ENVIRONMENT="demo"
        PLATFORM="docker"  # Demo always uses Docker
        # Update state file path for demo environment
        _update_state_file_for_env
        if check_existing_install "$env_prefix"; then
            resuming=true
        fi
    fi
    
    # Run wizard or use demo defaults (or resume from saved state)
    if [[ "$DEMO_MODE" == true ]]; then
        if [[ "$resuming" == true ]]; then
            # Load saved state - all wizard values (state file path already updated above)
            PLATFORM=$(get_state "PLATFORM" "docker")
            LLM_BACKEND=$(get_state "LLM_BACKEND" "$DETECTED_LLM_BACKEND")
            LLM_TIER=$(get_state "LLM_TIER" "$DETECTED_LLM_TIER")
            ADMIN_EMAIL=$(get_state "ADMIN_EMAIL" "demo@localhost")
            BASE_DOMAIN=$(get_state "BASE_DOMAIN" "localhost")
            ALLOWED_DOMAINS=$(get_state "ALLOWED_DOMAINS" "*")
            # Load secrets from vault (secrets are now in vault, not state)
            # These may not exist yet - that's OK, we'll prompt later
            _load_github_token_from_vault || true
            _load_admin_config_from_vault || true
        else
            setup_demo_mode
        fi
    else
        # For non-demo mode, we need to run wizard to know which environment
        wizard_environment
        
        # Now we can check for existing install (state file path is now correct for this env)
        env_prefix=$(get_container_prefix)
        if check_existing_install "$env_prefix"; then
            resuming=true
            # Load saved state - all wizard values
            PLATFORM=$(get_state "PLATFORM" "docker")
            LLM_BACKEND=$(get_state "LLM_BACKEND" "")
            LLM_TIER=$(get_state "LLM_TIER" "")
            ADMIN_EMAIL=$(get_state "ADMIN_EMAIL" "")
            BASE_DOMAIN=$(get_state "BASE_DOMAIN" "localhost")
            ALLOWED_DOMAINS=$(get_state "ALLOWED_DOMAINS" "*")
            # Load secrets from vault (secrets are now in vault, not state)
            # These may not exist yet - that's OK, we'll prompt later
            _load_github_token_from_vault || true
            _load_admin_config_from_vault || true
            
            # Show what we restored
            info "Restored configuration from saved state:"
            echo -e "  Platform:        ${CYAN}${PLATFORM}${NC}"
            echo -e "  LLM Backend:     ${CYAN}${LLM_BACKEND:-not set}${NC}"
            echo -e "  Base Domain:     ${CYAN}${BASE_DOMAIN}${NC}"
            echo -e "  Admin Email:     ${CYAN}${ADMIN_EMAIL:-not set}${NC}"
            echo -e "  Allowed Domains: ${CYAN}${ALLOWED_DOMAINS}${NC}"
            [[ -n "${GITHUB_AUTH_TOKEN:-}" ]] && echo -e "  GitHub Token:    ${CYAN}saved${NC}"
            echo ""
        else
            # Fresh install - run wizards (they use saved values as defaults if available)
            wizard_platform
            wizard_llm_backend
            wizard_network
            wizard_domain
            wizard_admin
            wizard_dev_apps_dir
            
            # Save wizard inputs to state immediately so they can be restored on resume
            set_state "ENVIRONMENT" "$ENVIRONMENT"
            set_state "PLATFORM" "$PLATFORM"
            set_state "LLM_BACKEND" "$LLM_BACKEND"
            set_state "LLM_TIER" "${LLM_TIER:-}"
            set_state "ADMIN_EMAIL" "$ADMIN_EMAIL"
            set_state "BASE_DOMAIN" "$BASE_DOMAIN"
            set_state "ALLOWED_DOMAINS" "${ALLOWED_DOMAINS:-*}"
            set_install_phase "wizard_complete"
        fi
    fi
    
    # Now check prerequisites (after PLATFORM is determined)
    check_prerequisites
    
    # Check what phase we're resuming from
    local current_phase=""
    if [[ "$resuming" == true ]]; then
        current_phase=$(get_install_phase)
    fi
    
    # GitHub token is always required (for both demo and regular install)
    # Check if we have a valid token - prompt if missing or empty
    if [[ -z "${GITHUB_AUTH_TOKEN:-}" ]]; then
        if ! wizard_github_token; then
            error "Cannot proceed without valid GitHub token"
            exit 1
        fi
        # Token is saved to vault via sync_secrets_to_vault, not state file
        set_install_phase "github_token_obtained"
    else
        info "Using saved GitHub token"
    fi
    
    # App directory detection depends on environment:
    # - development: Requires local directories for volume mounts (hot-reload)
    # - staging/production: Deploys from GitHub releases (no local dirs needed)
    if [[ "$ENVIRONMENT" == "development" ]]; then
        # Development mode: detect local app directories for volume mounts
        export DOCKER_DEV_MODE="local-dev"
        
        if [[ "$current_phase" == "secrets_generated" || "$current_phase" == "bootstrap_started" || "$current_phase" == "bootstrap_complete" ]]; then
            # Load saved paths from state
            AI_PORTAL_DIR=$(get_state "AI_PORTAL_DIR" "")
            AGENT_MANAGER_DIR=$(get_state "AGENT_MANAGER_DIR" "")
            BUSIBOX_APP_DIR=$(get_state "BUSIBOX_APP_DIR" "")
            APPS_BASE_DIR=$(get_state "APPS_BASE_DIR" "")
            DEV_APPS_DIR=$(get_dev_apps_dir)
            
            # If not in state, detect them
            if [[ -z "$AI_PORTAL_DIR" || -z "$BUSIBOX_APP_DIR" ]]; then
                if ! detect_app_directories; then
                    error "Cannot proceed without app directories"
                    exit 1
                fi
            fi
            # Default DEV_APPS_DIR if not set
            DEV_APPS_DIR="${DEV_APPS_DIR:-$APPS_BASE_DIR}"
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
            # Set DEV_APPS_DIR (defaults to APPS_BASE_DIR if not set by wizard)
            DEV_APPS_DIR="${DEV_APPS_DIR:-$APPS_BASE_DIR}"
            set_dev_apps_dir "$DEV_APPS_DIR"
        fi
    else
        # Staging/Production mode: deploy from GitHub releases
        # No local directory detection needed - apps are cloned at build time
        export DOCKER_DEV_MODE="github"
        info "Using GitHub mode - apps will be deployed from latest releases"
        
        # Set empty values to prevent docker-compose from complaining about missing vars
        AI_PORTAL_DIR=""
        AGENT_MANAGER_DIR=""
        BUSIBOX_APP_DIR=""
        APPS_BASE_DIR=""
        DEV_APPS_DIR=""
    fi
    
    # Start model downloads in background early
    # This allows models to download while Docker containers are being built
    if [[ "$LLM_BACKEND" == "mlx" ]]; then
        start_model_download_background
    fi
    
    # Start embedding model download in background (needed for data-api/embedding-api)
    # This runs regardless of LLM backend since embeddings are always local
    start_embedding_download_background
    
    # Generate secrets and create .env file
    # Skip if already done, but restore from vault if resuming
    if [[ "$current_phase" != "secrets_generated" && "$current_phase" != "bootstrap_started" && "$current_phase" != "bootstrap_complete" ]]; then
        generate_secrets
        create_env_file
        
        # Sync secrets to vault (vault is source of truth for secrets)
        # Ensure vault file exists first
        
        # For fresh install, force environment-specific vault (don't use legacy)
        # VAULT_ENVIRONMENT is set by set_vault_environment() in vault.sh
        if [[ -n "${VAULT_ENVIRONMENT:-}" ]]; then
            local env_vault_path="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.${VAULT_ENVIRONMENT}.yml"
            if [[ ! -f "$env_vault_path" ]] && [[ "$VAULT_FILE" != "$env_vault_path" ]]; then
                warn "Legacy vault detected, but environment-specific vault doesn't exist"
                info "Creating new environment-specific vault: vault.${VAULT_ENVIRONMENT}.yml"
                VAULT_FILE="$env_vault_path"
            fi
        fi
        
        if [[ ! -f "$VAULT_FILE" ]]; then
            # Ensure VAULT_EXAMPLE is set (should be set by set_vault_environment)
            if [[ -z "${VAULT_EXAMPLE:-}" ]]; then
                VAULT_EXAMPLE="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.example.yml"
                warn "VAULT_EXAMPLE was not set, using default: $VAULT_EXAMPLE"
            fi
            
            if [[ -f "$VAULT_EXAMPLE" ]]; then
                info "Creating vault from example..."
                info "  Source: $VAULT_EXAMPLE"
                info "  Target: $VAULT_FILE"
                
                # Ensure target directory exists
                mkdir -p "$(dirname "$VAULT_FILE")"
                
                cp "$VAULT_EXAMPLE" "$VAULT_FILE"
                success "Vault file created: $VAULT_FILE"
                
                # Mark that we need to encrypt the vault after updating secrets
                VAULT_NEEDS_ENCRYPTION=true
            else
                error "Vault file not found and no example to copy from"
                error "  Expected: $VAULT_EXAMPLE"
                error "  Looking in: $(dirname "$VAULT_EXAMPLE" 2>/dev/null || echo "N/A")"
                ls -la "$(dirname "$VAULT_EXAMPLE" 2>/dev/null)" 2>/dev/null || true
                exit 1
            fi
        else
            info "Using existing vault: $VAULT_FILE"
            VAULT_NEEDS_ENCRYPTION=false
        fi
        
        # Set vault password for sync operation
        # Use environment-specific vault password file
        # VAULT_ENVIRONMENT is set by set_vault_environment() in vault.sh
        if [[ -n "${VAULT_ENVIRONMENT:-}" ]]; then
            local vault_pass_file="${HOME}/.busibox-vault-pass-${VAULT_ENVIRONMENT}"
        else
            # Fallback for legacy installations
            local vault_pass_file="${HOME}/.vault_pass"
        fi
        
        # Generate vault password if it doesn't exist
        if [[ ! -f "$vault_pass_file" ]]; then
            if [[ -n "${VAULT_ENVIRONMENT:-}" ]]; then
                info "Generating vault password for $VAULT_ENVIRONMENT environment..."
            else
                info "Generating vault password..."
            fi
            openssl rand -base64 32 > "$vault_pass_file"
            chmod 600 "$vault_pass_file"
            success "Vault password generated: $vault_pass_file"
        fi
        
        export ANSIBLE_VAULT_PASSWORD_FILE="$vault_pass_file"
        
        # Sync generated secrets to vault
        sync_secrets_to_vault
        
        # Encrypt the vault if it was just created (unencrypted)
        if [[ "${VAULT_NEEDS_ENCRYPTION:-false}" == "true" ]]; then
            info "Encrypting vault with environment password..."
            if ansible-vault encrypt --vault-password-file="$vault_pass_file" "$VAULT_FILE" 2>/dev/null; then
                success "Vault encrypted: $VAULT_FILE"
            else
                error "Failed to encrypt vault"
                exit 1
            fi
        fi
        
        set_install_phase "secrets_generated"
    else
        # Restore secrets and protected config from vault when resuming
        info "Restoring secrets and config from vault..."
        
        # For resume, also force environment-specific vault (don't use legacy)
        # VAULT_ENVIRONMENT is set by set_vault_environment() in vault.sh
        if [[ -n "${VAULT_ENVIRONMENT:-}" ]]; then
            local env_vault_path="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.${VAULT_ENVIRONMENT}.yml"
            if [[ -f "$env_vault_path" ]] && [[ "$VAULT_FILE" != "$env_vault_path" ]]; then
                warn "Legacy vault path detected, switching to environment-specific vault"
                info "Using environment vault: vault.${VAULT_ENVIRONMENT}.yml"
                VAULT_FILE="$env_vault_path"
            fi
        fi
        
        # Initialize ANSIBLE_VAULT_PASSWORD_FILE to avoid unbound variable errors
        export ANSIBLE_VAULT_PASSWORD_FILE="${ANSIBLE_VAULT_PASSWORD_FILE:-}"
        
        # First check if vault is accessible
        if [[ -f "$VAULT_FILE" ]]; then
            # Try to read from vault (may be encrypted or unencrypted)
            if is_vault_encrypted; then
                # Need vault password
                local vault_pass_file=$(get_vault_pass_file)
                if [[ -f "$vault_pass_file" ]]; then
                    export ANSIBLE_VAULT_PASSWORD_FILE="$vault_pass_file"
                fi
            fi
            
            # Restore secrets
            export POSTGRES_PASSWORD=$(get_vault_secret "secrets.postgresql.password" || echo "")
            export GITHUB_AUTH_TOKEN=$(get_vault_secret "secrets.github.personal_access_token" || echo "")
            export SSO_JWT_SECRET=$(get_vault_secret "secrets.jwt_secret" || echo "")
            export MINIO_SECRET_KEY=$(get_vault_secret "secrets.minio.root_password" || echo "")
            export AUTHZ_MASTER_KEY=$(get_vault_secret "secrets.authz_master_key" || echo "")
            export LITELLM_API_KEY=$(get_vault_secret "secrets.litellm_api_key" || echo "")
            
            # Restore protected config (admin settings from vault for integrity)
            local vault_admin_email=$(get_vault_secret "secrets.admin_email" || echo "")
            local vault_allowed_domains=$(get_vault_secret "secrets.allowed_email_domains" || echo "")
            
            if [[ -n "$vault_admin_email" ]] && [[ "$vault_admin_email" != "CHANGE_ME"* ]]; then
                export ADMIN_EMAIL="$vault_admin_email"
            fi
            if [[ -n "$vault_allowed_domains" ]] && [[ "$vault_allowed_domains" != "CHANGE_ME"* ]]; then
                export ALLOWED_DOMAINS="$vault_allowed_domains"
            fi
        fi
        
        # Check if critical secrets were found
        if [[ -z "$POSTGRES_PASSWORD" ]] || [[ "$POSTGRES_PASSWORD" == "CHANGE_ME"* ]]; then
            error "Required secrets not found in vault - cannot resume"
            error "Please run 'make clean' and start fresh installation"
            exit 1
        fi
        
        # GitHub token may be empty for some setups
        if [[ -z "${GITHUB_AUTH_TOKEN:-}" ]] || [[ "${GITHUB_AUTH_TOKEN:-}" == "CHANGE_ME"* ]]; then
            warn "GitHub token not found in vault - may need to re-enter"
            ensure_github_token
        fi
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
        # Use Ansible for unified deployment (default)
        # Set USE_LEGACY_DOCKER=true to use old docker-compose approach
        if [[ "${USE_LEGACY_DOCKER:-false}" == "true" ]]; then
            warn "Using legacy docker-compose deployment (deprecated)"
            bootstrap_docker
        else
            info "Using Ansible for Docker deployment (unified deployment system)"
            bootstrap_docker_ansible
        fi
    elif [[ "$PLATFORM" == "proxmox" ]]; then
        info "Using Ansible for Proxmox deployment"
        bootstrap_proxmox_ansible
    else
        error "Unknown platform: $PLATFORM"
        exit 1
    fi
    
    # Setup MLX and host-agent if on Apple Silicon
    if [[ "$LLM_BACKEND" == "mlx" ]]; then
        setup_mlx
        setup_host_agent
        # Wait for background model download if still running
        wait_for_model_download
        # Ensure MLX server is running for AI Portal setup
        ensure_mlx_running
    else
        # For non-MLX backends, still download the embedding model
        # (embeddings run locally regardless of LLM backend)
        show_stage 92 "Downloading Embedding Model" "Pre-downloading FastEmbed model for document search."
        download_embedding_model
    fi
    
    # Mark installation as complete
    set_install_phase "complete"
    set_install_status "installed"
    
    # Note: SETUP_COMPLETE will be set by AI Portal after admin completes setup wizard
    set_state "SETUP_COMPLETE" "false"
    
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
