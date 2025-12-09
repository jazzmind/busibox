#!/usr/bin/env bash
#
# Busibox Test Script
#
# EXECUTION CONTEXT: Admin workstation or Proxmox host
# PURPOSE: Interactive test runner for infrastructure and service tests
#
# USAGE:
#   make test
#   OR
#   bash scripts/test.sh
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ANSIBLE_DIR="${REPO_ROOT}/provision/ansible"

# Source UI library
source "${SCRIPT_DIR}/lib/ui.sh"

# Display welcome
clear
box "Busibox Test Runner" 70
echo ""
info "Run infrastructure and service tests"
echo ""

# Infrastructure tests
run_infrastructure_tests() {
    local test_type="$1"
    
    header "Infrastructure Tests" 70
    
    if ! check_proxmox; then
        error "Infrastructure tests require Proxmox host"
        return 1
    fi
    
    echo ""
    info "Running $test_type infrastructure tests..."
    echo ""
    
    bash "${REPO_ROOT}/scripts/test-infrastructure.sh" "$test_type" || {
        error "Infrastructure tests failed"
        return 1
    }
    
    echo ""
    success "Infrastructure tests passed!"
    return 0
}

# Ingest service tests
ingest_tests_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Ingest Service Tests - $env" \
            "Run Unit Tests" \
            "Run All Tests (Unit + Integration)" \
            "Run with Coverage" \
            "Test SIMPLE Extraction" \
            "Test LLM Cleanup Extraction" \
            "Test Marker Extraction" \
            "Test ColPali Extraction" \
            "Back to Test Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-8]:${NC} ")" choice
        
        cd "$ANSIBLE_DIR"
        local inv="inventory/${env}"
        
        case $choice in
            1)
                make test-ingest INV="$inv"
                pause
                ;;
            2)
                make test-ingest-all INV="$inv"
                pause
                ;;
            3)
                make test-ingest-coverage INV="$inv"
                pause
                ;;
            4)
                make test-extraction-simple INV="$inv"
                pause
                ;;
            5)
                make test-extraction-llm INV="$inv"
                pause
                ;;
            6)
                make test-extraction-marker INV="$inv"
                pause
                ;;
            7)
                make test-extraction-colpali INV="$inv"
                pause
                ;;
            8)
                cd "$REPO_ROOT"
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-8."
                ;;
        esac
        
        cd "$REPO_ROOT"
    done
}

# Search service tests
search_tests_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Search Service Tests - $env" \
            "Run Unit Tests" \
            "Run Integration Tests" \
            "Run with Coverage" \
            "Back to Test Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
        
        cd "$ANSIBLE_DIR"
        local inv="inventory/${env}"
        
        case $choice in
            1)
                make test-search-unit INV="$inv"
                pause
                ;;
            2)
                make test-search-integration INV="$inv"
                pause
                ;;
            3)
                make test-search-coverage INV="$inv"
                pause
                ;;
            4)
                cd "$REPO_ROOT"
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-4."
                ;;
        esac
        
        cd "$REPO_ROOT"
    done
}

# Service tests menu
service_tests_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Service Tests - $env Environment" \
            "Authz Service Tests" \
            "Ingest Service Tests" \
            "Search Service Tests" \
            "Agent Service Tests" \
            "Apps Service Tests" \
            "All Service Tests" \
            "Back to Main Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-7]:${NC} ")" choice
        
        cd "$ANSIBLE_DIR"
        local inv="inventory/${env}"
        
        case $choice in
            1)
                header "Authz Service Tests" 70
                echo ""
                if confirm "Run authz pytest on authz-lxc in $env?"; then
                    local vault_flags
                    vault_flags="$(get_vault_flags)"
                    # ansible ad-hoc uses ANSIBLE_CONFIG; ensure we stay in ansible dir
                    ANSIBLE_CONFIG="${ANSIBLE_DIR}/ansible.cfg" ansible -i "$inv" authz -m shell -a "cd /srv/authz && source venv/bin/activate && pip install -q -r requirements.test.txt && pytest -q" $vault_flags || {
                        error "Authz tests failed"
                    }
                fi
                pause
                ;;
            2)
                ingest_tests_menu "$env"
                ;;
            3)
                search_tests_menu "$env"
                ;;
            4)
                header "Agent Service Tests" 70
                echo ""
                if confirm "Run agent tests on $env?"; then
                    make test-agent INV="$inv"
                fi
                pause
                ;;
            5)
                header "Apps Service Tests" 70
                echo ""
                if confirm "Run apps tests on $env?"; then
                    make test-apps INV="$inv"
                fi
                pause
                ;;
            6)
                header "All Service Tests" 70
                echo ""
                if confirm "Run ALL service tests on $env? (This may take a while)"; then
                    make test-all INV="$inv"
                fi
                pause
                ;;
            7)
                cd "$REPO_ROOT"
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-7."
                ;;
        esac
        
        cd "$REPO_ROOT"
    done
}

# Main test menu
main_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Busibox Test Suite - $env Environment" \
            "Infrastructure Tests (Full Suite)" \
            "Infrastructure Tests (Provision Only)" \
            "Infrastructure Tests (Verify Only)" \
            "Service Tests" \
            "All Tests (Infrastructure + Services)" \
            "Exit"
        
        read -p "$(echo -e "${BOLD}Select option [1-6]:${NC} ")" choice
        
        case $choice in
            1)
                if confirm "Run full infrastructure test suite?"; then
                    run_infrastructure_tests "full"
                fi
                pause
                ;;
            2)
                if confirm "Run infrastructure provisioning tests?"; then
                    run_infrastructure_tests "provision"
                fi
                pause
                ;;
            3)
                if confirm "Run infrastructure verification tests?"; then
                    run_infrastructure_tests "verify"
                fi
                pause
                ;;
            4)
                service_tests_menu "$env"
                ;;
            5)
                header "All Tests" 70
                echo ""
                warn "This will run infrastructure tests followed by all service tests"
                warn "This may take 30-60 minutes to complete"
                echo ""
                
                if confirm "Run ALL tests?" "n"; then
                    if check_proxmox; then
                        run_infrastructure_tests "full"
                    else
                        warn "Skipping infrastructure tests (not on Proxmox host)"
                    fi
                    
                    echo ""
                    info "Running service tests..."
                    cd "$ANSIBLE_DIR"
                    make test-all INV="inventory/${env}"
                    cd "$REPO_ROOT"
                fi
                pause
                ;;
            6)
                echo ""
                info "Exiting..."
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-6."
                ;;
        esac
    done
}

# Main function
main() {
    # Select environment
    ENV=$(select_environment)
    
    success "Selected environment: $ENV"
    
    # Show test menu
    main_menu "$ENV"
    
    echo ""
    box "Testing Complete" 70
    echo ""
}

# Run main function
main

exit 0

