#!/usr/bin/env bash
#
# Rich Progress Display Library for Busibox
#
# Provides animated progress bars, stage displays with feature callouts,
# and value proposition messaging for investor demos and user experience.
#
# Usage: source "$(dirname "$0")/lib/progress.sh"
#

# Source base UI if not already loaded
if [[ -z "${NC:-}" ]]; then
    source "$(dirname "${BASH_SOURCE[0]}")/ui.sh"
fi

# =============================================================================
# PROGRESS BAR
# =============================================================================

# Show progress bar with percentage
# Usage: show_progress_bar 75
show_progress_bar() {
    local percent=$1
    local width=${2:-50}
    local filled=$((percent * width / 100))
    local empty=$((width - filled))
    
    printf "\r[${GREEN}"
    if [[ $filled -gt 0 ]]; then
        printf '█%.0s' $(seq 1 $filled)
    fi
    printf "${DIM}"
    if [[ $empty -gt 0 ]]; then
        printf '░%.0s' $(seq 1 $empty)
    fi
    printf "${NC}] %3d%%" "$percent"
}

# =============================================================================
# STAGE DISPLAY
# =============================================================================

# Show stage with title and description
# Usage: show_stage 50 "PostgreSQL" "Enterprise-grade database with RLS"
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
        # Word wrap description to fit in box
        echo "$description" | fold -s -w 74 | while read -r line; do
            printf "│  %-74s │\n" "$line"
        done
    fi
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
}

# Show stage with feature list
# Usage: show_stage_features 50 "PostgreSQL" "feature1" "feature2" "feature3"
show_stage_features() {
    local percent=$1
    local title="$2"
    shift 2
    local features=("$@")
    
    echo ""
    show_progress_bar "$percent"
    echo ""
    echo ""
    echo -e "┌──────────────────────────────────────────────────────────────────────────────┐"
    printf "│  ${BOLD}%-74s${NC} │\n" "$title"
    echo -e "├──────────────────────────────────────────────────────────────────────────────┤"
    for feature in "${features[@]}"; do
        printf "│  ${GREEN}✓${NC} %-72s │\n" "$feature"
    done
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
}

# =============================================================================
# BANNER DISPLAY
# =============================================================================

# Show installation banner
# Usage: show_install_banner "BUSIBOX DEMO" "Your subtitle here"
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

# Show completion banner
# Usage: show_completion_banner "Setup Complete" "Next steps..."
show_completion_banner() {
    local title="$1"
    shift
    local lines=("$@")
    
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    printf "${GREEN}║${NC}%*s${BOLD}%s${NC}%*s${GREEN}║${NC}\n" $(( (78 - ${#title}) / 2 )) "" "$title" $(( (78 - ${#title} + 1) / 2 )) ""
    echo -e "${GREEN}╠══════════════════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC}                                                                              ${GREEN}║${NC}"
    for line in "${lines[@]}"; do
        printf "${GREEN}║${NC}  %-74s ${GREEN}║${NC}\n" "$line"
    done
    echo -e "${GREEN}║${NC}                                                                              ${GREEN}║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# =============================================================================
# VALUE PROPOSITION BOXES
# =============================================================================

# Show "Why This Matters" box
# Usage: show_value_box "Title" "Description" "bullet1" "bullet2"
show_value_box() {
    local title="$1"
    local description="$2"
    shift 2
    local bullets=("$@")
    
    echo ""
    echo -e "  ┌────────────────────────────────────────────────────────────────────────┐"
    printf "  │  ${BOLD}%-68s${NC}  │\n" "$title"
    echo -e "  ├────────────────────────────────────────────────────────────────────────┤"
    echo -e "  │                                                                        │"
    # Word wrap description
    echo "$description" | fold -s -w 66 | while read -r line; do
        printf "  │  %-68s  │\n" "$line"
    done
    echo -e "  │                                                                        │"
    for bullet in "${bullets[@]}"; do
        printf "  │  ${CYAN}•${NC} %-66s  │\n" "$bullet"
    done
    echo -e "  │                                                                        │"
    echo -e "  └────────────────────────────────────────────────────────────────────────┘"
}

# =============================================================================
# WAIT FUNCTIONS
# =============================================================================

# Wait for URL to be healthy
# Usage: wait_for_url "http://localhost:8010/health" 60
wait_for_url() {
    local url="$1"
    local timeout="${2:-60}"
    local interval="${3:-2}"
    
    local elapsed=0
    while [[ $elapsed -lt $timeout ]]; do
        if curl -sf "$url" &>/dev/null; then
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    return 1
}

# Wait for Docker container to be healthy
# Usage: wait_for_container "local-postgres" 60
wait_for_container() {
    local container="$1"
    local timeout="${2:-60}"
    local interval="${3:-2}"
    
    local elapsed=0
    while [[ $elapsed -lt $timeout ]]; do
        local health
        health=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "unknown")
        if [[ "$health" == "healthy" ]]; then
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    return 1
}

# =============================================================================
# SERVICE STATUS DISPLAY
# =============================================================================

# Show service status table
# Usage: show_service_status "PostgreSQL" "Running" "10.96.200.203"
show_service_status() {
    local name="$1"
    local status="$2"
    local address="${3:-}"
    
    local status_color="${GREEN}"
    local status_icon="●"
    
    case "$status" in
        Running|Healthy|Active)
            status_color="${GREEN}"
            status_icon="●"
            ;;
        Starting|Pending)
            status_color="${YELLOW}"
            status_icon="○"
            ;;
        Stopped|Failed|Error)
            status_color="${RED}"
            status_icon="○"
            ;;
        *)
            status_color="${DIM}"
            status_icon="○"
            ;;
    esac
    
    if [[ -n "$address" ]]; then
        printf "  ${status_color}${status_icon}${NC} %-20s %-15s %s\n" "$name" "$address" "$status"
    else
        printf "  ${status_color}${status_icon}${NC} %-20s %s\n" "$name" "$status"
    fi
}

# =============================================================================
# SPINNER
# =============================================================================

# Run command with spinner
# Usage: with_spinner "Installing..." command args
with_spinner() {
    local message="$1"
    shift
    local cmd=("$@")
    
    local spinstr='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local i=0
    
    # Start command in background
    "${cmd[@]}" &
    local pid=$!
    
    # Show spinner while command runs
    while kill -0 $pid 2>/dev/null; do
        printf "\r  ${CYAN}${spinstr:i++%${#spinstr}:1}${NC} ${message}"
        sleep 0.1
    done
    
    # Wait for command and get exit code
    wait $pid
    local exit_code=$?
    
    # Clear spinner line
    printf "\r%-60s\r" " "
    
    return $exit_code
}

# =============================================================================
# INSTALL MESSAGES
# =============================================================================

# Load install messages from JSON (if available)
load_install_messages() {
    local messages_file="${REPO_ROOT:-$(pwd)}/config/install-messages.json"
    if [[ -f "$messages_file" ]]; then
        export INSTALL_MESSAGES_FILE="$messages_file"
    fi
}

# Get service message
# Usage: msg=$(get_service_message "postgresql" "tagline")
get_service_message() {
    local service="$1"
    local field="$2"
    
    if [[ -n "${INSTALL_MESSAGES_FILE:-}" ]]; then
        python3 -c "
import json
with open('${INSTALL_MESSAGES_FILE}') as f:
    data = json.load(f)
svc = data.get('services', {}).get('$service', {})
print(svc.get('$field', ''))
" 2>/dev/null || echo ""
    else
        echo ""
    fi
}
