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
# Returns: Selected option number (1-based)
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
# Returns: 0 for yes, 1 for no
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
# Returns: "docker", "staging", or "production"
select_environment() {
    # Send display output to stderr so it shows on terminal (not captured by command substitution)
    {
        echo ""
        box "Environment Selection"
        echo ""
        echo -e "  ${CYAN}1)${NC} Local Docker           (localhost development)"
        echo -e "  ${CYAN}2)${NC} Staging Environment   (10.96.201.x network)"
        echo -e "  ${CYAN}3)${NC} Production Environment (10.96.200.x network)"
        echo ""
    } >&2
    
    while true; do
        # Read from terminal and show prompt on stderr
        echo -ne "${BOLD}Select environment [1-3]:${NC} " >&2
        read choice < /dev/tty
        case $choice in
            1)
                # Only output result to stdout (will be captured)
                echo "docker"
                return 0
                ;;
            2)
                echo "staging"
                return 0
                ;;
            3)
                echo "production"
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1, 2, or 3."
                ;;
        esac
    done
}

# Test mode selection
# Usage: MODE=$(select_test_mode)
# Returns: "container" (run on container) or "local" (run locally against containers)
select_test_mode() {
    # Send display output to stderr so it shows on terminal (not captured by command substitution)
    {
        echo ""
        box "Test Execution Mode"
        echo ""
        echo -e "  ${CYAN}1)${NC} Container Mode  - Run tests on deployed containers (standard)"
        echo -e "  ${CYAN}2)${NC} Local Mode      - Run tests locally against container backends"
        echo ""
        echo -e "  ${DIM}Local mode runs your local code but uses container databases/services.${NC}"
        echo -e "  ${DIM}Useful for rapid debugging without redeploying.${NC}"
        echo ""
    } >&2
    
    while true; do
        # Read from terminal and show prompt on stderr
        echo -ne "${BOLD}Select mode [1-2]:${NC} " >&2
        read choice < /dev/tty
        case $choice in
            1)
                echo "container"
                return 0
                ;;
            2)
                echo "local"
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1 or 2." >&2
                ;;
        esac
    done
}

# Service selection for testing
# Usage: SERVICE=$(select_test_service)
# Returns: service name (authz, ingest, search, agent, etc.)
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
        error "This script must run on a Proxmox host with 'pct' command available"
        echo ""
        info "Current environment: $(uname -s)"
        echo ""
        error "Please run this script on your Proxmox host"
        return 1
    fi
    return 0
}

# Display a list with checkmarks
list_item() {
    local status="$1"  # "done", "pending", "skip", or "info"
    local message="$2"
    
    case "$status" in
        done)
            echo -e "  ${GREEN}✓${NC} $message"
            ;;
        pending)
            echo -e "  ${YELLOW}○${NC} $message"
            ;;
        skip)
            echo -e "  ${CYAN}−${NC} $message"
            ;;
        error)
            echo -e "  ${RED}✗${NC} $message"
            ;;
        *)
            echo -e "  ${BLUE}•${NC} $message"
            ;;
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

