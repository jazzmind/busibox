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
    
    echo -e "    ${CYAN}[r]${NC} Re-run: ${DIM}$display_cmd${NC}"
    if [[ -n "$last_ago" ]]; then
        echo -e "        ${DIM}($last_ago)${NC}"
    fi
    echo -e "    ${CYAN}[s]${NC} Quick status check"
    echo ""
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
