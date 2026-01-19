#!/usr/bin/env bash
#
# Shared UI Library for Busibox Interactive Scripts
#
# This library provides consistent terminal UI functions for all interactive scripts.
# Usage: source "$(dirname "$0")/lib/ui.sh"

# Colors
export RED='\033[0;31m'
export GREEN='\033[0;32m'
export YELLOW='\033[1;33m'
export BLUE='\033[0;34m'
export CYAN='\033[0;36m'
export BOLD='\033[1m'
export DIM='\033[2m'
export NC='\033[0m' # No Color

# Status messages
info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ASCII box with title
box() {
    local title="$1"
    local width=${2:-60}
    
    # Calculate padding for centered title
    local title_len=${#title}
    local total_padding=$((width - title_len - 2))
    local left_padding=$((total_padding / 2))
    local right_padding=$((total_padding - left_padding))
    
    # Top border
    echo -e "${CYAN}╔$(printf '═%.0s' $(seq 1 $((width - 2))))╗${NC}"
    
    # Title line
    printf "${CYAN}║${NC}%${left_padding}s${BOLD}%s${NC}%${right_padding}s${CYAN}║${NC}\n" "" "$title" ""
    
    # Bottom border
    echo -e "${CYAN}╚$(printf '═%.0s' $(seq 1 $((width - 2))))╝${NC}"
}

# Section header
header() {
    local title="$1"
    local width=${2:-60}
    
    echo ""
    echo -e "${CYAN}$(printf '═%.0s' $(seq 1 $width))${NC}"
    echo -e "${CYAN}${BOLD}$title${NC}"
    echo -e "${CYAN}$(printf '═%.0s' $(seq 1 $width))${NC}"
    echo ""
}

# Simple separator
separator() {
    local width=${1:-60}
    echo -e "${CYAN}$(printf '─%.0s' $(seq 1 $width))${NC}"
}

# Progress indicator
progress() {
    local current=$1
    local total=$2
    local message=$3
    
    echo ""
    echo -e "${CYAN}[Step $current/$total]${NC} ${BOLD}$message${NC}"
    separator
}

# Interactive menu
# Usage: menu "Title" "option1" "option2" "option3"
menu() {
    local title="$1"
    shift
    local options=("$@")
    
    echo ""
    box "$title"
    echo ""
    
    local i=1
    for option in "${options[@]}"; do
        echo -e "  ${CYAN}$i)${NC} $option"
        ((i++))
    done
    
    echo ""
}

# Confirmation prompt
# Usage: if confirm "Are you sure?"; then ... fi
confirm() {
    local prompt="$1"
    local default="${2:-y}"
    
    if [[ "$default" == "y" ]]; then
        read -p "$(echo -e "${YELLOW}${prompt}${NC} (Y/n): ")" -n 1 -r
    else
        read -p "$(echo -e "${YELLOW}${prompt}${NC} (y/N): ")" -n 1 -r
    fi
    
    echo
    
    if [[ "$default" == "y" ]]; then
        [[ ! $REPLY =~ ^[Nn]$ ]]
    else
        [[ $REPLY =~ ^[Yy]$ ]]
    fi
}

# Environment selection
# Usage: ENV=$(select_environment)
# Returns: "local", "staging", or "production"
select_environment() {
    {
        echo ""
        box "Environment Selection"
        echo ""
        echo -e "  ${CYAN}1)${NC} Local              ${DIM}(Docker on localhost)${NC}"
        echo -e "  ${CYAN}2)${NC} Staging            ${DIM}(10.96.201.x network)${NC}"
        echo -e "  ${CYAN}3)${NC} Production         ${DIM}(10.96.200.x network)${NC}"
        echo ""
    } >&2
    
    while true; do
        echo -ne "${BOLD}Select environment [1-3]:${NC} " >&2
        read choice < /dev/tty
        case $choice in
            1) echo "local"; return 0 ;;
            2) echo "staging"; return 0 ;;
            3) echo "production"; return 0 ;;
            *) error "Invalid selection. Please enter 1, 2, or 3." ;;
        esac
    done
}

# Service selection for testing
# Usage: SERVICE=$(select_test_service)
select_test_service() {
    {
        echo ""
        box "Select Service to Test"
        echo ""
        echo -e "  ${CYAN}1)${NC} Authz   - Authorization & OAuth service"
        echo -e "  ${CYAN}2)${NC} Ingest  - Document ingestion service"
        echo -e "  ${CYAN}3)${NC} Search  - Vector search service"
        echo -e "  ${CYAN}4)${NC} Agent   - AI agent service"
        echo -e "  ${CYAN}5)${NC} All     - Run all service tests"
        echo ""
    } >&2
    
    while true; do
        echo -ne "${BOLD}Select service [1-5]:${NC} " >&2
        read choice < /dev/tty
        case $choice in
            1) echo "authz"; return 0 ;;
            2) echo "ingest"; return 0 ;;
            3) echo "search"; return 0 ;;
            4) echo "agent"; return 0 ;;
            5) echo "all"; return 0 ;;
            *) error "Invalid selection. Please enter 1-5." >&2 ;;
        esac
    done
}

# Wait for keypress
pause() {
    local message="${1:-Press any key to continue...}"
    read -n 1 -s -r -p "$(echo -e "${CYAN}$message${NC}")"
    echo
}

# Check if running on Proxmox host
check_proxmox() {
    if ! command -v pct &>/dev/null; then
        return 1
    fi
    return 0
}

# Display a list with checkmarks
list_item() {
    local status="$1"
    local message="$2"
    
    case "$status" in
        done) echo -e "  ${GREEN}✓${NC} $message" ;;
        pending) echo -e "  ${YELLOW}○${NC} $message" ;;
        skip) echo -e "  ${CYAN}−${NC} $message" ;;
        error) echo -e "  ${RED}✗${NC} $message" ;;
        *) echo -e "  ${BLUE}•${NC} $message" ;;
    esac
}

# Display completion summary
summary() {
    local title="$1"
    shift
    local items=("$@")
    
    echo ""
    box "$title"
    echo ""
    
    for item in "${items[@]}"; do
        echo -e "  ${GREEN}✓${NC} $item"
    done
    
    echo ""
}

# ============================================================================
# Enhanced UI Functions for Menu System
# ============================================================================

# Status bar showing current environment and status
status_bar() {
    local env="${1:-unknown}"
    local backend="${2:-}"
    local status="${3:-unknown}"
    local width=${4:-70}
    
    local env_display="$env"
    if [[ -n "$backend" ]]; then
        env_display="$env ($backend)"
    fi
    
    # Map status to display text and color
    local status_color="$NC"
    local status_icon="○"
    local status_display="$status"
    case "$status" in
        healthy) 
            status_color="$GREEN"; status_icon="✓"; status_display="healthy" ;;
        deployed) 
            status_color="$CYAN"; status_icon="●"; status_display="deployed" ;;
        configured) 
            status_color="$YELLOW"; status_icon="◐"; status_display="configured" ;;
        installed)
            # "installed" really means dependencies are ready but not configured
            # Show more descriptive status 
            status_color="$YELLOW"; status_icon="○"; status_display="ready" ;;
        not_installed) 
            status_color="$RED"; status_icon="✗"; status_display="not ready" ;;
        docker_not_running)
            status_color="$RED"; status_icon="○"; status_display="not running" ;;
        containers_not_running)
            status_color="$YELLOW"; status_icon="○"; status_display="not running" ;;
    esac
    
    local left_text="Environment: $env_display"
    local right_text="Status: $status_display $status_icon"
    local left_len=${#left_text}
    local right_len=$((${#right_text} + 2))
    local spaces=$((width - left_len - right_len - 4))
    
    echo ""
    echo -e "  ${BOLD}Environment:${NC} ${CYAN}$env_display${NC}$(printf '%*s' $spaces '')${BOLD}Status:${NC} ${status_color}$status_display $status_icon${NC}"
    separator "$width"
}

# Quick action menu
quick_menu() {
    local last_cmd="${1:-}"
    local last_ago="${2:-}"
    
    if [[ -z "$last_cmd" ]]; then
        return 0
    fi
    
    echo ""
    echo -e "  ${BOLD}Quick Actions:${NC}"
    
    local display_cmd="$last_cmd"
    if [[ ${#display_cmd} -gt 50 ]]; then
        display_cmd="${display_cmd:0:47}..."
    fi
    local display_ago=""
    if [[ -n "$last_ago" ]]; then
        display_ago=" ${DIM}($last_ago)${NC}"
    fi   
    echo -e "    ${CYAN}[r]${NC} Re-run: ${DIM}$display_cmd$display_ago"

    # echo -e "    ${CYAN}[s]${NC} Quick status check"
    # echo ""
}

# Backend selection for staging/production environments
select_backend() {
    {
        echo ""
        box "Backend Selection"
        echo ""
        echo -e "  ${CYAN}1)${NC} Docker     ${DIM}(portable, runs anywhere)${NC}"
        echo -e "  ${CYAN}2)${NC} Proxmox    ${DIM}(LXC containers, GPU support)${NC}"
        echo ""
    } >&2
    
    while true; do
        echo -ne "${BOLD}Select backend [1-2]:${NC} " >&2
        read choice < /dev/tty
        case $choice in
            1) echo "docker"; return 0 ;;
            2) echo "proxmox"; return 0 ;;
            *) error "Invalid selection. Please enter 1 or 2." >&2 ;;
        esac
    done
}

# Enhanced environment selection with backend
# Returns: "env:backend" (e.g., "staging:docker")
select_environment_with_backend() {
    local env backend
    
    {
        echo ""
        box "Environment Selection"
        echo ""
        echo -e "  ${CYAN}1)${NC} Local              ${DIM}(Docker on localhost)${NC}"
        echo -e "  ${CYAN}2)${NC} Staging            ${DIM}(10.96.201.x network)${NC}"
        echo -e "  ${CYAN}3)${NC} Production         ${DIM}(10.96.200.x network)${NC}"
        echo ""
    } >&2
    
    while true; do
        echo -ne "${BOLD}Select environment [1-3]:${NC} " >&2
        read choice < /dev/tty
        case $choice in
            1) echo "local:docker"; return 0 ;;
            2) env="staging"; break ;;
            3) env="production"; break ;;
            *) error "Invalid selection. Please enter 1, 2, or 3." >&2 ;;
        esac
    done
    
    backend=$(select_backend)
    echo "${env}:${backend}"
}

# Enhanced environment selection with auto-detection
# Tries to auto-detect backend before asking user
# Returns: "env:backend" (e.g., "staging:proxmox")
select_environment_with_backend_autodetect() {
    local env backend detected
    
    {
        echo ""
        box "Environment Selection"
        echo ""
        echo -e "  ${CYAN}1)${NC} Local              ${DIM}(Docker on localhost)${NC}"
        echo -e "  ${CYAN}2)${NC} Staging            ${DIM}(10.96.201.x network)${NC}"
        echo -e "  ${CYAN}3)${NC} Production         ${DIM}(10.96.200.x network)${NC}"
        echo ""
    } >&2
    
    while true; do
        echo -ne "${BOLD}Select environment [1-3]:${NC} " >&2
        read choice < /dev/tty
        case $choice in
            1) echo "local:docker"; return 0 ;;
            2) env="staging"; break ;;
            3) env="production"; break ;;
            *) error "Invalid selection. Please enter 1, 2, or 3." >&2 ;;
        esac
    done
    
    # Try to auto-detect backend
    detected=$(auto_detect_backend_ui "$env")
    if [[ -n "$detected" ]]; then
        echo -e "${GREEN}✓${NC} Auto-detected backend: ${BOLD}$detected${NC}" >&2
        echo -e "  ${DIM}Press Enter to use $detected, or 'c' to choose manually${NC}" >&2
        local confirm_choice=""
        read -t 5 -n 1 confirm_choice < /dev/tty 2>/dev/null || true
        if [[ "${confirm_choice:-}" != "c" ]]; then
            echo "${env}:${detected}"
            return 0
        fi
    fi
    
    backend=$(select_backend)
    echo "${env}:${backend}"
}

# Auto-detect backend for UI purposes (quick check)
# Returns: "docker" or "proxmox" if detected, empty string if not
auto_detect_backend_ui() {
    local env="$1"
    
    # Get network base for this environment
    local network_base
    case "$env" in
        production) network_base="10.96.200" ;;
        staging) network_base="10.96.201" ;;
        *) return 1 ;;
    esac
    
    # Check if Proxmox network is reachable (quick ping to gateway)
    if ping -c 1 -W 1 "${network_base}.200" &>/dev/null 2>&1; then
        echo "proxmox"
        return 0
    fi
    
    # Check if Docker containers for this env are running locally
    if command -v docker &>/dev/null && docker ps --format '{{.Names}}' 2>/dev/null | grep -qE "(local|${env})" ; then
        echo "docker"
        return 0
    fi
    
    # Not detected
    echo ""
    return 1
}

# Display dynamic menu based on available features
# Outputs menu to stderr, returns choice to stdout
dynamic_menu() {
    local status="${1:-not_installed}"
    local last_cmd="${2:-}"
    local last_ago="${3:-}"
    
    local options=()
    local option_keys=()
    
    # Docker-specific startup options based on status
    case "$status" in
        docker_not_running)
            # Docker daemon is not running
            options+=("Start Docker"); option_keys+=("start_docker")
            ;;
        containers_not_running)
            # Docker is running but containers aren't
            options+=("Start Busibox"); option_keys+=("start_busibox")
            options+=("Install/Setup"); option_keys+=("install")
            options+=("Configure"); option_keys+=("configure")
            options+=("Deploy (build images)"); option_keys+=("deploy")
            ;;
        *)
            # Normal flow for other statuses
            options+=("Install/Setup"); option_keys+=("install")
            
            if [[ "$status" != "not_installed" ]]; then
                options+=("Configure"); option_keys+=("configure")
            fi
            
            if [[ "$status" == "configured" || "$status" == "deployed" || "$status" == "healthy" ]]; then
                options+=("Deploy (build images)"); option_keys+=("deploy")
                options+=("Services (start/stop/restart)"); option_keys+=("services")
            fi
            
            if [[ "$status" == "deployed" || "$status" == "healthy" ]]; then
                options+=("Test"); option_keys+=("test")
            fi
            
            # Migration is always available for configured+ status
            if [[ "$status" == "configured" || "$status" == "deployed" || "$status" == "healthy" ]]; then
                options+=("Migration (database)"); option_keys+=("migration")
            fi
            ;;
    esac
    
    options+=("Change Environment"); option_keys+=("change_env")
    options+=("Help"); option_keys+=("help")
    options+=("Quit"); option_keys+=("quit")
    
    # Display menu to stderr so it shows on screen
    {
        echo ""
        echo -e "  ${BOLD}Main Menu:${NC}"
        local i=1
        for option in "${options[@]}"; do
            echo -e "    ${CYAN}$i)${NC} $option"
            ((i++))
        done
        echo ""
    } >&2
    
    while true; do
        echo -ne "  ${BOLD}Select option [1-${#options[@]}]:${NC} " >&2
        read choice < /dev/tty
        
        if [[ "${choice:-}" == "r" ]] && [[ -n "$last_cmd" ]]; then
            echo "rerun"; return 0
        elif [[ "${choice:-}" == "s" ]]; then
            echo "status"; return 0
        elif [[ "${choice:-}" == "q" ]] || [[ "${choice:-}" == "Q" ]]; then
            echo "quit"; return 0
        fi
        
        if [[ "${choice:-}" =~ ^[0-9]+$ ]] && [[ $choice -ge 1 ]] && [[ $choice -le ${#options[@]} ]]; then
            echo "${option_keys[$((choice-1))]}"; return 0
        fi
        
        error "Invalid selection." >&2
    done
}

# Clear screen and show header
clear_and_header() {
    local title="${1:-Busibox Control Panel}"
    local width=${2:-70}
    
    clear
    box "$title" "$width"
}

# ============================================================================
# Status Dashboard Functions
# ============================================================================

# Get script directory for sourcing dependencies
_UI_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source status library if not already loaded
if ! type get_service_status_from_cache &>/dev/null; then
    source "${_UI_SCRIPT_DIR}/status.sh" 2>/dev/null || true
fi

# Get status symbol for service state
# Usage: get_status_symbol "up"
get_status_symbol() {
    local status=$1
    
    case "$status" in
        up) echo -e "${GREEN}●${NC}" ;;
        down) echo -e "${DIM}○${NC}" ;;
        checking) echo -e "${DIM}◷${NC}" ;;
        unknown) echo -e "${DIM}○${NC}" ;;
        *) echo -e "${DIM}○${NC}" ;;
    esac
}

# Get health status indicator
# Usage: get_health_indicator "healthy"
get_health_indicator() {
    local health=$1
    
    case "$health" in
        healthy) echo -e "${GREEN}✓ up${NC}" ;;
        degraded) echo -e "${YELLOW}⚠ slow${NC}" ;;
        down) echo -e "${RED}✗ down${NC}" ;;
        checking) echo -e "${DIM}checking...${NC}" ;;
        unknown) echo -e "${DIM}- unknown${NC}" ;;
        *) echo -e "${DIM}- unknown${NC}" ;;
    esac
}

# Get sync state indicator
# Usage: get_sync_indicator "synced" "a1b2c3d"
get_sync_indicator() {
    local sync_state=$1
    local version=$2
    
    case "$sync_state" in
        synced) echo -e "${GREEN}✓ synced${NC}" ;;
        behind) echo -e "${YELLOW}⚠ behind${NC}" ;;
        local) echo -e "${BLUE}◆ local${NC}" ;;
        checking) echo -e "${DIM}checking...${NC}" ;;
        unknown) echo -e "${DIM}- unknown${NC}" ;;
        *) echo -e "${DIM}- unknown${NC}" ;;
    esac
}

# Format response time with color coding
# Usage: format_response_time 45
format_response_time() {
    local ms=$1
    
    if [[ "$ms" == "0" || "$ms" == "checking" ]]; then
        echo -e "${DIM}-${NC}"
    elif [[ $ms -lt 100 ]]; then
        echo -e "${GREEN}${ms}ms${NC}"
    elif [[ $ms -lt 500 ]]; then
        echo -e "${YELLOW}${ms}ms${NC}"
    else
        echo -e "${RED}${ms}ms${NC}"
    fi
}

# Render consolidated ingest line (API + Worker)
# Usage: render_consolidated_ingest_line "staging"
render_consolidated_ingest_line() {
    local env=$1
    
    # Get status for both API and Worker
    local api_status_json worker_status_json
    api_status_json=$(get_service_status_from_cache "ingest-api" "$env" 2>/dev/null)
    worker_status_json=$(get_service_status_from_cache "ingest-worker" "$env" 2>/dev/null)
    
    # Parse statuses
    local api_status worker_status api_version api_current api_sync
    if command -v jq &>/dev/null; then
        api_status=$(echo "$api_status_json" | jq -r '.status // "unknown"')
        worker_status=$(echo "$worker_status_json" | jq -r '.status // "unknown"')
        api_version=$(echo "$api_status_json" | jq -r '.version // "unknown"')
        api_current=$(echo "$api_status_json" | jq -r '.current_version // "unknown"')
        api_sync=$(echo "$api_status_json" | jq -r '.sync_state // "unknown"')
    else
        api_status="unknown"
        worker_status="unknown"
        api_version="unknown"
        api_current="unknown"
        api_sync="unknown"
    fi
    
    # Determine combined status
    local combined_status combined_symbol status_text
    if [[ "$api_status" == "up" && "$worker_status" == "up" ]]; then
        combined_status="up"
        combined_symbol=$(echo -e "${GREEN}●${NC}")
        status_text=$(echo -e "${GREEN}✓ up${NC}")
    elif [[ "$api_status" == "down" && "$worker_status" == "down" ]]; then
        combined_status="down"
        combined_symbol=$(echo -e "${RED}○${NC}")
        status_text=$(echo -e "${RED}✗ down (both)${NC}")
    elif [[ "$api_status" == "down" ]]; then
        combined_status="degraded"
        combined_symbol=$(echo -e "${YELLOW}◐${NC}")
        status_text=$(echo -e "${YELLOW}⚠ down (api)${NC}")
    elif [[ "$worker_status" == "down" ]]; then
        combined_status="degraded"
        combined_symbol=$(echo -e "${YELLOW}◐${NC}")
        status_text=$(echo -e "${YELLOW}⚠ down (worker)${NC}")
    else
        combined_status="unknown"
        combined_symbol=$(echo -e "${DIM}○${NC}")
        status_text=$(echo -e "${DIM}- unknown${NC}")
    fi
    
    # Format version info
    local version_display="$api_version"
    if [[ ${#api_version} -gt 7 ]]; then
        version_display="${api_version:0:7}"
    fi
    
    local current_display="$api_current"
    if [[ ${#api_current} -gt 7 ]]; then
        current_display="${api_current:0:7}"
    fi
    
    local version_info
    if [[ "$api_version" == "$api_current" ]]; then
        version_info="${version_display}"
    else
        version_info="${version_display} → ${current_display}"
    fi
    
    # Sync indicator
    local sync_indicator=$(get_sync_indicator "$api_sync" "$api_version")
    
    # Render line with proper spacing using tabs (matching render_service_line format)
    printf "  %s %-15s\t%s\t\t│ %-18s\t%s\n" \
        "$combined_symbol" \
        "Ingest API & Worker" \
        "$status_text" \
        "$version_info" \
        "$sync_indicator"
}

# Render consolidated litellm line (LiteLLM + vLLM if available)
# Usage: render_consolidated_litellm_line "staging"
render_consolidated_litellm_line() {
    local env=$1
    
    # Get status for LiteLLM and vLLM
    local litellm_status_json vllm_status_json
    litellm_status_json=$(get_service_status_from_cache "litellm" "$env" 2>/dev/null)
    vllm_status_json=$(get_service_status_from_cache "vllm" "$env" 2>/dev/null)
    
    # Parse statuses
    local litellm_status vllm_status litellm_version litellm_current litellm_sync
    if command -v jq &>/dev/null; then
        litellm_status=$(echo "$litellm_status_json" | jq -r '.status // "unknown"')
        vllm_status=$(echo "$vllm_status_json" | jq -r '.status // "unknown"')
        litellm_version=$(echo "$litellm_status_json" | jq -r '.version // "unknown"')
        litellm_current=$(echo "$litellm_status_json" | jq -r '.current_version // "unknown"')
        litellm_sync=$(echo "$litellm_status_json" | jq -r '.sync_state // "unknown"')
    else
        litellm_status="unknown"
        vllm_status="unknown"
        litellm_version="unknown"
        litellm_current="unknown"
        litellm_sync="unknown"
    fi
    
    # Determine combined status and label
    local combined_status combined_symbol status_text service_label
    
    # Check if vLLM is actually deployed/used (not just unknown)
    local vllm_used=false
    if [[ "$vllm_status" == "up" || "$vllm_status" == "down" ]]; then
        vllm_used=true
    fi
    
    if [[ "$vllm_used" == "true" ]]; then
        service_label="LiteLLM & vLLM"
        if [[ "$litellm_status" == "up" && "$vllm_status" == "up" ]]; then
            combined_status="up"
            combined_symbol=$(echo -e "${GREEN}●${NC}")
            status_text=$(echo -e "${GREEN}✓ up${NC}")
        elif [[ "$litellm_status" == "down" && "$vllm_status" == "down" ]]; then
            combined_status="down"
            combined_symbol=$(echo -e "${RED}○${NC}")
            status_text=$(echo -e "${RED}✗ down (both)${NC}")
        elif [[ "$litellm_status" == "down" ]]; then
            combined_status="degraded"
            combined_symbol=$(echo -e "${YELLOW}◐${NC}")
            status_text=$(echo -e "${YELLOW}⚠ down (litellm)${NC}")
        elif [[ "$vllm_status" == "down" ]]; then
            combined_status="degraded"
            combined_symbol=$(echo -e "${YELLOW}◐${NC}")
            status_text=$(echo -e "${YELLOW}⚠ down (vllm)${NC}")
        else
            combined_status="unknown"
            combined_symbol=$(echo -e "${DIM}○${NC}")
            status_text=$(echo -e "${DIM}- unknown${NC}")
        fi
    else
        # vLLM not used, show just LiteLLM
        service_label="LiteLLM"
        if [[ "$litellm_status" == "up" ]]; then
            combined_symbol=$(echo -e "${GREEN}●${NC}")
            status_text=$(echo -e "${GREEN}✓ up${NC}")
        elif [[ "$litellm_status" == "down" ]]; then
            combined_symbol=$(echo -e "${RED}○${NC}")
            status_text=$(echo -e "${RED}✗ down${NC}")
        else
            combined_symbol=$(echo -e "${DIM}○${NC}")
            status_text=$(echo -e "${DIM}- unknown${NC}")
        fi
    fi
    
    # Format version info
    local version_display="$litellm_version"
    if [[ ${#litellm_version} -gt 7 ]]; then
        version_display="${litellm_version:0:7}"
    fi
    
    local current_display="$litellm_current"
    if [[ ${#litellm_current} -gt 7 ]]; then
        current_display="${litellm_current:0:7}"
    fi
    
    local version_info
    if [[ "$litellm_version" == "$litellm_current" ]]; then
        version_info="${version_display}"
    else
        version_info="${version_display} → ${current_display}"
    fi
    
    # Sync indicator
    local sync_indicator=$(get_sync_indicator "$litellm_sync" "$litellm_version")
    
    # Render line with proper spacing using tabs (matching render_service_line format)
    printf "  %s %-15s\t%s\t\t│ %-18s\t%s\n" \
        "$combined_symbol" \
        "$service_label" \
        "$status_text" \
        "$version_info" \
        "$sync_indicator"
}

# Render single service line
# Usage: render_service_line "authz" "staging"
render_service_line() {
    local service=$1
    local env=$2
    
    # Get cached status
    local status_json
    status_json=$(get_service_status_from_cache "$service" "$env" 2>/dev/null)
    
    # Parse JSON (using jq if available, otherwise basic parsing)
    local status health version current_version sync_state response_time
    if command -v jq &>/dev/null; then
        status=$(echo "$status_json" | jq -r '.status // "checking"')
        health=$(echo "$status_json" | jq -r '.health // "checking"')
        version=$(echo "$status_json" | jq -r '.version // "checking"')
        current_version=$(echo "$status_json" | jq -r '.current_version // "checking"')
        sync_state=$(echo "$status_json" | jq -r '.sync_state // "checking"')
        response_time=$(echo "$status_json" | jq -r '.response_time_ms // 0')
    else
        # Fallback parsing
        status="checking"
        health="checking"
        version="checking"
        current_version="checking"
        sync_state="checking"
        response_time=0
    fi
    
    # Get display components
    local display_name=$(get_service_display_name "$service")
    local status_symbol=$(get_status_symbol "$status")
    local health_indicator=$(get_health_indicator "$health")
    local sync_indicator=$(get_sync_indicator "$sync_state" "$version")
    local time_display=$(format_response_time "$response_time")
    
    # Format versions (truncate if too long)
    local version_display="$version"
    if [[ ${#version} -gt 7 ]]; then
        version_display="${version:0:7}"
    fi
    
    local current_display="$current_version"
    if [[ ${#current_version} -gt 7 ]]; then
        current_display="${current_version:0:7}"
    fi
    
    # Show both deployed and current versions
    local version_info
    if [[ "$version" == "checking" || "$current_version" == "checking" ]]; then
        version_info="${version_display}"
    elif [[ "$version" == "local" ]]; then
        version_info="local → ${current_display}"
    elif [[ "$version" == "$current_version" ]]; then
        version_info="${version_display}"
    else
        version_info="${version_display} → ${current_display}"
    fi
    
    # Render line with proper spacing using tabs
    # Format: "  ● ServiceName    ✓ up    │ a1b2c3d → b2c3d4e    ✓ synced"
    printf "  %s %-15s\t%s\t\t│ %-18s\t%s\n" \
        "$status_symbol" \
        "$display_name" \
        "$health_indicator" \
        "$version_info" \
        "$sync_indicator"
}

# Render service category group
# Usage: render_service_category "Core Services" "core" "staging" [show_header]
render_service_category() {
    local category_title=$1
    local category=$2
    local env=$3
    local show_header=${4:-true}
    
    echo ""
    echo -e "${BOLD}$category_title${NC}"
    # Calculate underline length based on visible text only (strip ANSI codes and extra text)
    # For "Core Services", use 13 chars; for others use their actual length
    local underline_length
    case "$category" in
        core) underline_length=13 ;;  # "Core Services"
        api) underline_length=12 ;;   # "API Services"
        app) underline_length=4 ;;    # "Apps"
        *) underline_length=20 ;;     # Default fallback
    esac
    echo -e "${DIM}$(printf '─%.0s' $(seq 1 $underline_length))${NC}"
    
    # Add column headers only if requested (only for first category)
    if [[ "$show_header" == "true" ]]; then
        printf "    ${DIM}%-15s\t%-8s\t│ %-18s\t%-10s${NC}\n" \
            "Service" \
            "Status" \
            "Version" \
            "Sync"
    fi
    
    # Get services in category
    local services=$(get_services_in_category "$category")
    
    # Render each service (with special handling for consolidated services)
    for service in $services; do
        # Special handling for ingest - consolidate API + Worker
        if [[ "$service" == "ingest" ]]; then
            render_consolidated_ingest_line "$env"
        # Special handling for litellm - consolidate LiteLLM + vLLM
        elif [[ "$service" == "litellm" ]]; then
            render_consolidated_litellm_line "$env"
        else
            render_service_line "$service" "$env"
        fi
    done
}

# Main status dashboard renderer (non-blocking)
# Usage: render_status_dashboard "staging" "proxmox"
render_status_dashboard() {
    local env=$1
    local backend=$2
    
    # Check if status library is available
    if ! type get_service_status_from_cache &>/dev/null; then
        echo ""
        echo -e "${DIM}  (Status dashboard unavailable)${NC}"
        return 0
    fi
    
    # Get cache age for display
    local cache_age="unknown"
    local cache_file=$(get_cache_file "authz" "$env" 2>/dev/null)
    if [[ -f "$cache_file" ]]; then
        local cache_mtime
        if [[ "$(uname)" == "Darwin" ]]; then
            cache_mtime=$(stat -f %m "$cache_file" 2>/dev/null || echo 0)
        else
            cache_mtime=$(stat -c %Y "$cache_file" 2>/dev/null || echo 0)
        fi
        local now=$(date +%s)
        cache_age=$((now - cache_mtime))
        
        if [[ $cache_age -lt 60 ]]; then
            cache_age="${cache_age}s ago"
        elif [[ $cache_age -lt 3600 ]]; then
            cache_age="$((cache_age / 60))m ago"
        else
            cache_age="$((cache_age / 3600))h ago"
        fi
    fi
       
    # Render each category (only show header for first category)
    render_service_category "Core Services $(printf '%*s' 10 '')${DIM}Last check: $cache_age  ${CYAN}[Press 's' to refresh]${NC}" "core" "$env" "true"
    render_service_category "API Services" "api" "$env" "false"
    render_service_category "Apps" "app" "$env" "false"
    
    echo ""
}
