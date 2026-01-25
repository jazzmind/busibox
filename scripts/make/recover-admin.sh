#!/usr/bin/env bash
#
# Busibox Admin Recovery Script
#
# Generates a new magic link for admin access when browser/passkey access is lost.
# This requires CLI access to the server (SSH or physical).
#
# Usage:
#   recover-admin.sh                # Use admin email from state
#   recover-admin.sh [email]        # Specify admin email
#

set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${SCRIPT_DIR}/../lib/ui.sh"
source "${SCRIPT_DIR}/../lib/state.sh"

# =============================================================================
# MAIN
# =============================================================================

main() {
    local admin_email="${1:-}"
    
    # Check if any environment is installed
    # State files are now environment-specific: .busibox-state-{dev,demo,staging,prod}
    local found_state=false
    for state_file in "${REPO_ROOT}"/.busibox-state-*; do
        if [[ -f "$state_file" ]]; then
            found_state=true
            break
        fi
    done
    
    if [[ "$found_state" != "true" ]]; then
        error "Busibox not installed. Run 'make install' or 'make demo' first."
        exit 1
    fi
    
    # Get admin email from state or argument
    if [[ -z "$admin_email" ]]; then
        admin_email=$(get_state "ADMIN_EMAIL" "")
    fi
    
    if [[ -z "$admin_email" ]]; then
        read -p "Admin email: " admin_email
    fi
    
    if [[ -z "$admin_email" ]]; then
        error "Admin email is required"
        exit 1
    fi
    
    # Check what platform we're using
    local platform
    platform=$(get_current_backend)
    
    if [[ -z "$platform" ]]; then
        platform="docker"  # Default assumption
    fi
    
    info "Generating recovery link for: ${admin_email}"
    
    # Get base domain
    local base_domain
    base_domain=$(get_state "BASE_DOMAIN" "localhost")
    
    # TODO: Magic link flow not yet implemented in AI Portal
    # For now, just point to the portal login page
    # The proper flow would be:
    # 1. Generate a secure token
    # 2. Store it in the database with expiry and admin email
    # 3. AI Portal validates token and creates session
    
    local portal_url
    if [[ "$base_domain" == "localhost" ]]; then
        portal_url="https://localhost/portal/"
    else
        portal_url="https://${base_domain}/portal/"
    fi
    
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}                           ${BOLD}ADMIN ACCESS INFO${NC}                                ${GREEN}║${NC}"
    echo -e "${GREEN}╠══════════════════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC}                                                                              ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  Access the AI Portal at:                                                   ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}                                                                              ${GREEN}║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${CYAN}${portal_url}${NC}"
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}  Admin email: ${BOLD}${admin_email}${NC}"
    printf "${GREEN}║${NC}  %-76s ${GREEN}║${NC}\n" ""
    echo -e "${GREEN}║${NC}  ${YELLOW}Note:${NC} Magic link authentication is not yet implemented.                    ${GREEN}║${NC}"
    echo -e "${GREEN}║${NC}  Use email sign-in for now.                                                 ${GREEN}║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    # If on Docker, also check if services are running
    if [[ "$platform" == "docker" ]]; then
        # Check for any ai-portal container (any prefix: demo-, dev-, staging-, prod-)
        if ! docker ps --format '{{.Names}}' | grep -q "ai-portal"; then
            warn "AI Portal container is not running"
            info "Start services with: make docker-up"
        fi
    fi
}

main "$@"
