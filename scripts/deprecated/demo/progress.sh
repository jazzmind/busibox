#!/usr/bin/env bash
# =============================================================================
# Visual Progress Library for Busibox Demo
# =============================================================================
#
# Provides attractive terminal UI functions for investor demos.
# Each stage shows business value messaging, not just technical status.
#
# Usage:
#   source progress.sh
#   show_banner "BUSIBOX DEMO" "Subtitle text"
#   show_stage 50 "Stage Name" "Why this matters..."
#   show_dashboard "tier" "ram" "model"
#
# =============================================================================

# Colors
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m' # No Color

# =============================================================================
# Banner and Headers
# =============================================================================

show_banner() {
    local title="$1"
    local subtitle="$2"
    clear
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    printf "║%*s${BOLD}%s${NC}${CYAN}%*s║\n" $(( (70 - ${#title}) / 2 )) "" "$title" $(( (71 - ${#title}) / 2 )) ""
    echo "╠══════════════════════════════════════════════════════════════════════╣"
    printf "║  %-68s║\n" "$subtitle"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# =============================================================================
# Progress Display
# =============================================================================

show_stage() {
    local percent=$1
    local stage="$2"
    local message="$3"
    
    # Progress bar
    local filled=$((percent * 50 / 100))
    local empty=$((50 - filled))
    local bar="${GREEN}"
    for ((i=0; i<filled; i++)); do bar+="█"; done
    bar+="${NC}"
    for ((i=0; i<empty; i++)); do bar+="░"; done
    
    echo ""
    echo -e "  [${bar}] ${percent}%  ${BOLD}${stage}${NC}"
    echo ""
    echo -e "  ${CYAN}┌─ Why this matters ─────────────────────────────────────────────────┐${NC}"
    echo -e "  ${CYAN}│${NC} $message"
    echo -e "  ${CYAN}└─────────────────────────────────────────────────────────────────────┘${NC}"
    echo ""
}

show_progress() {
    local percent=$1
    local message="$2"
    
    # Simple progress without the box
    local filled=$((percent * 50 / 100))
    local empty=$((50 - filled))
    local bar="${GREEN}"
    for ((i=0; i<filled; i++)); do bar+="█"; done
    bar+="${NC}"
    for ((i=0; i<empty; i++)); do bar+="░"; done
    
    echo -e "  [${bar}] ${percent}%  ${message}"
}

# =============================================================================
# Wait Functions
# =============================================================================

wait_for_url() {
    local url="$1"
    local timeout="${2:-60}"
    local start=$(date +%s)
    
    echo -n "  Waiting for $url "
    while ! curl -sf "$url" >/dev/null 2>&1; do
        local now=$(date +%s)
        if (( now - start > timeout )); then
            echo -e " ${RED}TIMEOUT${NC}"
            return 1
        fi
        echo -n "."
        sleep 2
    done
    echo -e " ${GREEN}OK${NC}"
}

wait_for_healthy() {
    local container="$1"
    local timeout="${2:-60}"
    local start=$(date +%s)
    
    echo -n "  Waiting for $container "
    while true; do
        local status=$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || echo "not_found")
        
        if [[ "$status" == "healthy" ]]; then
            echo -e " ${GREEN}OK${NC}"
            return 0
        fi
        
        local now=$(date +%s)
        if (( now - start > timeout )); then
            echo -e " ${YELLOW}TIMEOUT (continuing)${NC}"
            return 0  # Continue anyway
        fi
        echo -n "."
        sleep 2
    done
}

wait_for_container() {
    local container="$1"
    local timeout="${2:-30}"
    local start=$(date +%s)
    
    echo -n "  Waiting for $container to start "
    while ! docker ps --format '{{.Names}}' | grep -q "^${container}$"; do
        local now=$(date +%s)
        if (( now - start > timeout )); then
            echo -e " ${RED}TIMEOUT${NC}"
            return 1
        fi
        echo -n "."
        sleep 1
    done
    echo -e " ${GREEN}OK${NC}"
}

# =============================================================================
# Dashboard
# =============================================================================

show_dashboard() {
    local tier="${1:-standard}"
    local ram="${2:-16}"
    local model="${3:-unknown}"
    
    echo ""
    echo -e "${GREEN}"
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║                          DEMO READY                                  ║"
    echo "╠══════════════════════════════════════════════════════════════════════╣"
    echo "║                                                                      ║"
    # Capitalize tier name
    local tier_cap="${tier^}"
    printf "║  System: %-60s║\n" "${ram}GB RAM, ${tier_cap} tier"
    printf "║  Model:  %-60s║\n" "$model"
    echo "║                                                                      ║"
    echo "╠══════════════════════════════════════════════════════════════════════╣"
    echo "║                                                                      ║"
    echo "║  Busibox Portal:      https://localhost/portal                            ║"
    echo "║  Agent Manager:  https://localhost/agents                            ║"
    echo "║  API Docs:       https://localhost/api/docs                          ║"
    echo "║                                                                      ║"
    echo "║  Demo credentials:                                                   ║"
    echo "║    Email: demo@localhost                                             ║"
    echo "║    (Magic link printed in console logs - check docker logs)          ║"
    echo "║                                                                      ║"
    echo "╠══════════════════════════════════════════════════════════════════════╣"
    echo "║                                                                      ║"
    echo -e "║  ${BOLD}Try disconnecting wifi - everything keeps working!${NC}${GREEN}                  ║"
    echo "║                                                                      ║"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# =============================================================================
# Logging Functions
# =============================================================================

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
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# =============================================================================
# Spinner for Long Operations
# =============================================================================

spinner() {
    local pid=$1
    local message="${2:-Processing...}"
    local delay=0.1
    local spinstr='|/-\'
    
    echo -n "  $message "
    while kill -0 "$pid" 2>/dev/null; do
        local temp=${spinstr#?}
        printf "[%c]" "$spinstr"
        local spinstr=$temp${spinstr%"$temp"}
        sleep $delay
        printf "\b\b\b"
    done
    printf "   \b\b\b"
    echo -e "${GREEN}Done${NC}"
}
