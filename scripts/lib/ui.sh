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
    local width=${2:-120}
    
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
    local width=${2:-120}
    
    echo ""
    echo -e "${CYAN}$(printf '═%.0s' $(seq 1 $width))${NC}"
    echo -e "${CYAN}${BOLD}$title${NC}"
    echo -e "${CYAN}$(printf '═%.0s' $(seq 1 $width))${NC}"
    echo ""
}

# Simple separator
separator() {
    local width=${1:-120}
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
# Returns: "development", "demo", "staging", or "production"
#
# Environments:
#   development - Docker with dev mode (volume mounts, npm link busibox-app)
#   demo        - Docker with prod mode (for demos/presentations)
#   staging     - Docker or Proxmox (10.96.201.x network)
#   production  - Docker or Proxmox (10.96.200.x network)
select_environment() {
    {
        echo ""
        box "Environment Selection"
        echo ""
        echo -e "  ${CYAN}1)${NC} Development        ${DIM}(Docker dev mode - volume mounts, hot reload)${NC}"
        echo -e "  ${CYAN}2)${NC} Demo               ${DIM}(Docker prod mode - for presentations)${NC}"
        echo -e "  ${CYAN}3)${NC} Staging            ${DIM}(10.96.201.x network - Docker or Proxmox)${NC}"
        echo -e "  ${CYAN}4)${NC} Production         ${DIM}(10.96.200.x network - Docker or Proxmox)${NC}"
        echo ""
    } >&2
    
    while true; do
        echo -ne "${BOLD}Select environment [1-4]:${NC} " >&2
        read choice < /dev/tty
        case $choice in
            1) echo "development"; return 0 ;;
            2) echo "demo"; return 0 ;;
            3) echo "staging"; return 0 ;;
            4) echo "production"; return 0 ;;
            *) error "Invalid selection. Please enter 1, 2, 3, or 4." ;;
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
    local width=${4:-120}
    
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
    
    local left_text="Environment: $env_display [Press 'e' to change]"
    local right_text="Status: $status_display $status_icon"
    local left_len=${#left_text}
    local right_len=$((${#right_text} + 2))
    local spaces=$((width - left_len - right_len - 4))
    
    echo ""
    echo -e "  ${BOLD}Environment:${NC} ${CYAN}$env_display${NC} ${DIM}[Press 'e' to change]${NC}$(printf '%*s' $spaces '')${BOLD}Status:${NC} ${status_color}$status_display $status_icon${NC}"
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
#
# Environments and their backends:
#   development - Always Docker (dev overlay with volume mounts)
#   demo        - Always Docker (prod overlay for demos)
#   staging     - Docker or Proxmox (asks user)
#   production  - Docker or Proxmox (asks user)
select_environment_with_backend() {
    local env backend
    
    {
        echo ""
        box "Environment Selection"
        echo ""
        echo -e "  ${CYAN}1)${NC} Development        ${DIM}(Docker dev mode - volume mounts, hot reload)${NC}"
        echo -e "  ${CYAN}2)${NC} Demo               ${DIM}(Docker prod mode - for presentations)${NC}"
        echo -e "  ${CYAN}3)${NC} Staging            ${DIM}(10.96.201.x network - Docker or Proxmox)${NC}"
        echo -e "  ${CYAN}4)${NC} Production         ${DIM}(10.96.200.x network - Docker or Proxmox)${NC}"
        echo ""
    } >&2
    
    while true; do
        echo -ne "${BOLD}Select environment [1-4]:${NC} " >&2
        read choice < /dev/tty
        case $choice in
            1) echo "development:docker"; return 0 ;;
            2) echo "demo:docker"; return 0 ;;
            3) env="staging"; break ;;
            4) env="production"; break ;;
            *) error "Invalid selection. Please enter 1, 2, 3, or 4." >&2 ;;
        esac
    done
    
    backend=$(select_backend)
    echo "${env}:${backend}"
}

# Enhanced environment selection with auto-detection
# Tries to auto-detect backend before asking user
# Returns: "env:backend" (e.g., "staging:proxmox")
#
# development and demo are always Docker (no backend selection needed)
# staging and production will try to auto-detect, then ask if needed
select_environment_with_backend_autodetect() {
    local env backend detected
    
    {
        echo ""
        box "Environment Selection"
        echo ""
        echo -e "  ${CYAN}1)${NC} Development        ${DIM}(Docker dev mode - volume mounts, hot reload)${NC}"
        echo -e "  ${CYAN}2)${NC} Demo               ${DIM}(Docker prod mode - for presentations)${NC}"
        echo -e "  ${CYAN}3)${NC} Staging            ${DIM}(10.96.201.x network - Docker or Proxmox)${NC}"
        echo -e "  ${CYAN}4)${NC} Production         ${DIM}(10.96.200.x network - Docker or Proxmox)${NC}"
        echo ""
    } >&2
    
    while true; do
        echo -ne "${BOLD}Select environment [1-4]:${NC} " >&2
        read choice < /dev/tty
        case $choice in
            1) echo "development:docker"; return 0 ;;
            2) echo "demo:docker"; return 0 ;;
            3) env="staging"; break ;;
            4) env="production"; break ;;
            *) error "Invalid selection. Please enter 1, 2, 3, or 4." >&2 ;;
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
            options+=("Services (deploy/start/stop/restart/rebuild)"); option_keys+=("services")
            ;;
        *)
            # Normal flow for other statuses
            options+=("Install/Setup"); option_keys+=("install")
            
            if [[ "$status" != "not_installed" ]]; then
                options+=("Configure"); option_keys+=("configure")
            fi
            
            if [[ "$status" == "configured" || "$status" == "deployed" || "$status" == "healthy" ]]; then
                options+=("Services (deploy/start/stop/restart/rebuild)"); option_keys+=("services")
            fi
            
            if [[ "$status" == "deployed" || "$status" == "healthy" ]]; then
                options+=("Test"); option_keys+=("test")
            fi
            
            # Databases menu is always available for configured+ status
            if [[ "$status" == "configured" || "$status" == "deployed" || "$status" == "healthy" ]]; then
                options+=("Databases"); option_keys+=("databases")
            fi
            ;;
    esac
    
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
        
        if [[ "${choice:-}" == "e" ]] || [[ "${choice:-}" == "E" ]]; then
            echo "change_env"; return 0
        elif [[ "${choice:-}" == "r" ]] && [[ -n "$last_cmd" ]]; then
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
    local width=${2:-120}
    
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
    
    # Define column widths (matching render_service_line)
    local col1_width=25  # Service name (including symbol)
    local col2_width=30  # Status/health
    local col3_width=45  # Version info
    
    # Calculate spacing dynamically
    local name_text="Ingest API & Worker"
    local name_text_len=${#name_text}
    local status_text_len=$(strip_ansi "$status_text" | wc -c | tr -d ' ')
    
    # Calculate padding needed
    local name_padding=$((col1_width - name_text_len - 2))  # -2 for symbol and space
    # Adjust padding based on status text length
    # "checking..." = 11 chars, "up" = 2 chars (+9), "down" = 4 chars (+7)
    # "down (api)" = 10 chars, "down (worker)" = 13 chars, "down (both)" = 11 chars
    if [[ "$status_text" =~ "down (api)" ]]; then
        local status_padding=$((col2_width - status_text_len + 2))
    elif [[ "$status_text" =~ "down (both)" ]]; then
        local status_padding=$((col2_width - status_text_len + 2))
    elif [[ "$status_text" =~ "down (worker)" ]]; then
        local status_padding=$((col2_width - status_text_len + 2))
    elif [[ "$status_text" =~ "up" ]] || [[ "$status_text" =~ "down" ]]; then
        local status_padding=$((col2_width - status_text_len + 2))
    else
        local status_padding=$((col2_width - status_text_len))
    fi
    
    # Ensure minimum padding
    [[ $name_padding -lt 1 ]] && name_padding=1
    [[ $status_padding -lt 1 ]] && status_padding=1
    
    # Render line with dynamic spacing
    printf "  %s %s%*s%s%*s│ %-${col3_width}s%s\n" \
        "$combined_symbol" \
        "$name_text" \
        "$name_padding" "" \
        "$status_text" \
        "$status_padding" "" \
        "$version_info" \
        "$sync_indicator"
}

# Render LiteLLM status line (standalone - no longer combined with vLLM)
# Usage: render_litellm_line "staging"
render_litellm_line() {
    local env=$1
    
    # Get status for LiteLLM only
    local litellm_status_json
    litellm_status_json=$(get_service_status_from_cache "litellm" "$env" 2>/dev/null)
    
    # Parse statuses
    local litellm_status litellm_version litellm_current litellm_sync
    if command -v jq &>/dev/null; then
        litellm_status=$(echo "$litellm_status_json" | jq -r '.status // "unknown"')
        litellm_version=$(echo "$litellm_status_json" | jq -r '.version // "unknown"')
        litellm_current=$(echo "$litellm_status_json" | jq -r '.current_version // "unknown"')
        litellm_sync=$(echo "$litellm_status_json" | jq -r '.sync_state // "unknown"')
    else
        litellm_status="unknown"
        litellm_version="unknown"
        litellm_current="unknown"
        litellm_sync="unknown"
    fi
    
    # LiteLLM always shows as standalone
    local service_label="LiteLLM"
    local status_symbol status_text
    
    if [[ "$litellm_status" == "up" ]]; then
        status_symbol=$(echo -e "${GREEN}●${NC}")
        status_text=$(echo -e "${GREEN}✓ up${NC}")
    elif [[ "$litellm_status" == "down" ]]; then
        status_symbol=$(echo -e "${RED}○${NC}")
        status_text=$(echo -e "${RED}✗ down${NC}")
    else
        status_symbol=$(echo -e "${DIM}○${NC}")
        status_text=$(echo -e "${DIM}- unknown${NC}")
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
    
    # Define column widths (matching render_service_line)
    local col1_width=25  # Service name (including symbol)
    local col2_width=30  # Status/health
    local col3_width=45  # Version info
    
    # Calculate spacing dynamically
    local name_text_len=${#service_label}
    local status_text_len=$(strip_ansi "$status_text" | wc -c | tr -d ' ')
    
    # Calculate padding needed
    local name_padding=$((col1_width - name_text_len - 2))  # -2 for symbol and space
    # Add +3 for "up"/"down" lines to match "checking..." length
    if [[ "$status_text" =~ "up" ]] || [[ "$status_text" =~ "down" ]]; then
        local status_padding=$((col2_width - status_text_len + 2))
    else
        local status_padding=$((col2_width - status_text_len))
    fi
    
    # Ensure minimum padding
    [[ $name_padding -lt 1 ]] && name_padding=1
    [[ $status_padding -lt 1 ]] && status_padding=1
    
    # Render line with dynamic spacing
    printf "  %s %s%*s%s%*s│ %-${col3_width}s%s\n" \
        "$status_symbol" \
        "$service_label" \
        "$name_padding" "" \
        "$status_text" \
        "$status_padding" "" \
        "$version_info" \
        "$sync_indicator"
}

# Keep backward-compatible alias
render_consolidated_litellm_line() {
    render_litellm_line "$@"
}

# Helper function to strip ANSI color codes for length calculation
strip_ansi() {
    echo "$1" | sed 's/\x1b\[[0-9;]*m//g'
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
    
    # Format versions (truncate if too long, but not for checking/unknown)
    local version_display="$version"
    if [[ "$version" != "checking" && "$version" != "unknown" && ${#version} -gt 7 ]]; then
        version_display="${version:0:7}"
    fi
    
    local current_display="$current_version"
    if [[ "$current_version" != "checking" && "$current_version" != "unknown" && ${#current_version} -gt 7 ]]; then
        current_display="${current_version:0:7}"
    fi
    
    # Show both deployed and current versions (always show arrow for consistency)
    local version_info
    if [[ "$version" == "checking" || "$current_version" == "checking" ]]; then
        version_info="${version_display}"
    elif [[ "$version" == "local" ]]; then
        version_info="local → ${current_display}"
    elif [[ "$version" == "unknown" || "$current_version" == "unknown" ]]; then
        # Show whatever we have
        if [[ "$version" != "unknown" ]]; then
            version_info="${version_display} → unknown"
        elif [[ "$current_version" != "unknown" ]]; then
            version_info="unknown → ${current_display}"
        else
            version_info="unknown"
        fi
    else
        # Always show both versions for consistency
        version_info="${version_display} → ${current_display}"
    fi
    
    # Define column widths (wider for 120-char display)
    local col1_width=25  # Service name (including symbol)
    local col2_width=30  # Status/health
    local col3_width=45  # Version info
    
    # Calculate spacing dynamically based on actual text length (without ANSI codes)
    local name_text_len=${#display_name}
    local health_text_len=$(strip_ansi "$health_indicator" | wc -c | tr -d ' ')
    
    # Calculate padding needed
    local name_padding=$((col1_width - name_text_len - 2))  # -2 for symbol and space
    # Add +3 for "up"/"down" lines to match "checking..." length
    if [[ "$health_indicator" =~ "up" ]] || [[ "$health_indicator" =~ "down" ]]; then
        local health_padding=$((col2_width - health_text_len + 2))
    else
        local health_padding=$((col2_width - health_text_len))
    fi
    
    # Ensure minimum padding
    [[ $name_padding -lt 1 ]] && name_padding=1
    [[ $health_padding -lt 1 ]] && health_padding=1
    
    # Render line with dynamic spacing
    printf "  %s %s%*s%s%*s│ %-${col3_width}s%s\n" \
        "$status_symbol" \
        "$display_name" \
        "$name_padding" "" \
        "$health_indicator" \
        "$health_padding" "" \
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
        llm) underline_length=12 ;;   # "LLM Services"
        api) underline_length=12 ;;   # "API Services"
        app) underline_length=4 ;;    # "Apps"
        *) underline_length=20 ;;     # Default fallback
    esac
    echo -e "${DIM}$(printf '─%.0s' $(seq 1 $underline_length))${NC}"
    
    # Add column headers only if requested (only for first category)
    if [[ "$show_header" == "true" ]]; then
        # Match column widths from render_service_line (25, 30, 45)
        # Status column: -1 space as requested
        printf "    ${DIM}%-23s %-27s │ %-43s %s${NC}\n" \
            "Service" \
            "Status" \
            "Version (deployed → current)" \
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
# Uses a two-column grouped layout for compact display
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
    
    # Use two-column layout for compact display
    render_status_dashboard_two_column "$env" "$cache_age"
}

# Render compact service line for two-column layout
# Usage: render_compact_service_line "authz" "staging"
# Returns: formatted string (no newline)
render_compact_service_line() {
    local service=$1
    local env=$2
    
    # Get cached status
    local status_json
    status_json=$(get_service_status_from_cache "$service" "$env" 2>/dev/null)
    
    # Parse JSON
    local status health version
    if command -v jq &>/dev/null; then
        status=$(echo "$status_json" | jq -r '.status // "checking"')
        health=$(echo "$status_json" | jq -r '.health // "checking"')
        version=$(echo "$status_json" | jq -r '.version // ""')
    else
        status="checking"
        health="checking"
        version=""
    fi
    
    # Get display name
    local display_name=$(get_service_display_name "$service")
    
    # Status symbol
    local status_symbol
    case "$status" in
        up) status_symbol="${GREEN}●${NC}" ;;
        down) status_symbol="${RED}○${NC}" ;;
        *) status_symbol="${DIM}◷${NC}" ;;
    esac
    
    # Health indicator (compact)
    local health_text
    case "$health" in
        healthy) health_text="${GREEN}up${NC}" ;;
        degraded) health_text="${YELLOW}slow${NC}" ;;
        down) health_text="${RED}down${NC}" ;;
        *) health_text="${DIM}...${NC}" ;;
    esac
    
    # Format version (truncate)
    local ver_display=""
    if [[ -n "$version" && "$version" != "checking" && "$version" != "unknown" ]]; then
        if [[ ${#version} -gt 7 ]]; then
            ver_display="${version:0:7}"
        else
            ver_display="$version"
        fi
    fi
    
    # Return formatted line
    printf "%b %-14s %-6b %s" "$status_symbol" "$display_name" "$health_text" "$ver_display"
}

# Compact consolidated ingest line (API + Worker)
render_compact_ingest_line() {
    local env=$1
    
    # Get status for both
    local api_json worker_json
    api_json=$(get_service_status_from_cache "ingest-api" "$env" 2>/dev/null)
    worker_json=$(get_service_status_from_cache "ingest-worker" "$env" 2>/dev/null)
    
    local api_status worker_status
    if command -v jq &>/dev/null; then
        api_status=$(echo "$api_json" | jq -r '.status // "unknown"')
        worker_status=$(echo "$worker_json" | jq -r '.status // "unknown"')
    else
        api_status="unknown"
        worker_status="unknown"
    fi
    
    # Combined status
    local status_symbol health_text
    if [[ "$api_status" == "up" && "$worker_status" == "up" ]]; then
        status_symbol="${GREEN}●${NC}"
        health_text="${GREEN}up${NC}"
    elif [[ "$api_status" == "down" && "$worker_status" == "down" ]]; then
        status_symbol="${RED}○${NC}"
        health_text="${RED}down${NC}"
    elif [[ "$api_status" == "down" ]]; then
        status_symbol="${YELLOW}◐${NC}"
        health_text="${YELLOW}api↓${NC}"
    elif [[ "$worker_status" == "down" ]]; then
        status_symbol="${YELLOW}◐${NC}"
        health_text="${YELLOW}wrk↓${NC}"
    else
        status_symbol="${DIM}◷${NC}"
        health_text="${DIM}...${NC}"
    fi
    
    printf "%b %-14s %-6b" "$status_symbol" "Ingest" "$health_text"
}

# Two-column status dashboard layout
# Left: Core + LLM services  |  Right: API + Apps
render_status_dashboard_two_column() {
    local env=$1
    local cache_age=$2
    
    echo ""
    echo -e "${BOLD}Service Status${NC}  ${DIM}Last check: $cache_age${NC}  ${CYAN}[Press 's' to refresh]${NC}"
    echo -e "${DIM}$(printf '─%.0s' $(seq 1 115))${NC}"
    
    # Column width
    local col_width=55
    
    # Get services for each category
    local core_services=$(get_services_in_category "core")
    local llm_services=$(get_services_in_category "llm")
    local api_services=$(get_services_in_category "api")
    local app_services=$(get_services_in_category "app")
    
    # Build service lists for each column
    # Left: Core + LLM
    # Right: API + Apps
    local -a left_items=()
    local -a right_items=()
    
    # Add Core services to left
    left_items+=("HEADER:Core")
    for svc in $core_services; do
        left_items+=("$svc")
    done
    
    # Add LLM services to left
    left_items+=("HEADER:LLM")
    for svc in $llm_services; do
        left_items+=("$svc")
    done
    
    # Add API services to right
    right_items+=("HEADER:API")
    for svc in $api_services; do
        # Consolidate ingest-api and ingest-worker
        if [[ "$svc" == "ingest-api" ]]; then
            right_items+=("INGEST")
        elif [[ "$svc" == "ingest-worker" ]]; then
            continue  # Skip, shown as consolidated
        else
            right_items+=("$svc")
        fi
    done
    
    # Add App services to right
    right_items+=("HEADER:Apps")
    for svc in $app_services; do
        right_items+=("$svc")
    done
    
    # Determine max rows
    local left_count=${#left_items[@]}
    local right_count=${#right_items[@]}
    local max_rows=$((left_count > right_count ? left_count : right_count))
    
    # Print rows side by side
    for ((i=0; i<max_rows; i++)); do
        local left_line=""
        local right_line=""
        
        # Left column
        if [[ $i -lt $left_count ]]; then
            local item="${left_items[$i]}"
            if [[ "$item" == HEADER:* ]]; then
                local header_name="${item#HEADER:}"
                left_line="${DIM}── $header_name ──${NC}"
            else
                left_line="$(render_compact_service_line "$item" "$env")"
            fi
        fi
        
        # Right column
        if [[ $i -lt $right_count ]]; then
            local item="${right_items[$i]}"
            if [[ "$item" == HEADER:* ]]; then
                local header_name="${item#HEADER:}"
                right_line="${DIM}── $header_name ──${NC}"
            elif [[ "$item" == "INGEST" ]]; then
                right_line="$(render_compact_ingest_line "$env")"
            else
                right_line="$(render_compact_service_line "$item" "$env")"
            fi
        fi
        
        # Print with proper spacing
        printf "  %-${col_width}b │ %-${col_width}b\n" "$left_line" "$right_line"
    done
    
    echo ""
}
