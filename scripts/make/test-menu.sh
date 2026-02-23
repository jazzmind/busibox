#!/usr/bin/env bash
#
# Busibox Test Menu
# =================
#
# Interactive menu for running tests against deployed services.
# Supports both Docker and Proxmox backends.
#
# Usage:
#   make test                # Interactive test menu
#   bash scripts/make/test-menu.sh
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"

# ============================================================================
# Backend Detection
# ============================================================================

get_backend_type() {
    local env
    env=$(get_state "ENVIRONMENT" || echo "development")
    get_backend "$env" 2>/dev/null || echo "docker"
}

# ============================================================================
# Test Categories
# ============================================================================

run_health_checks() {
    local backend
    backend=$(get_backend_type)
    
    clear
    box_header "HEALTH CHECKS"
    echo ""
    
    info "Running health checks..."
    echo ""
    
    cd "$REPO_ROOT"
    
    if [[ "$backend" == "docker" ]]; then
        # Docker health checks
        local prefix="${CONTAINER_PREFIX:-dev}"
        
        local services=(
            "postgres:5432"
            "redis:6379"
            "minio:9000"
            "milvus:19530"
            "authz-api:8010"
            "agent-api:8000"
            "data-api:8002"
            "search-api:8003"
            "litellm:4000"
        )
        
        for svc in "${services[@]}"; do
            local name="${svc%%:*}"
            local port="${svc##*:}"
            local container="${prefix}-${name}"
            
            printf "  %-20s" "$name"
            
            if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
                # Try health endpoint
                local health_url="http://localhost:${port}/health"
                if curl -sf --max-time 2 "$health_url" &>/dev/null; then
                    printf "${GREEN}healthy${NC}\n"
                elif docker exec "$container" echo "ok" &>/dev/null; then
                    printf "${GREEN}running${NC}\n"
                else
                    printf "${YELLOW}degraded${NC}\n"
                fi
            else
                printf "${RED}not running${NC}\n"
            fi
        done
    else
        # Proxmox health checks
        local env
        env=$(get_state "ENVIRONMENT" || echo "staging")
        cd "${REPO_ROOT}/provision/ansible"
        make verify-health INV="inventory/${env}" 2>&1 || true
    fi
    
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
}

run_smoke_tests() {
    local backend
    backend=$(get_backend_type)
    
    clear
    box_header "SMOKE TESTS"
    echo ""
    
    info "Running smoke tests..."
    echo ""
    
    cd "$REPO_ROOT"
    
    if [[ "$backend" == "docker" ]]; then
        make test MODE=container ARGS="-k smoke" 2>&1 || true
    else
        local env
        env=$(get_state "ENVIRONMENT" || echo "staging")
        cd "${REPO_ROOT}/provision/ansible"
        make test-smoke INV="inventory/${env}" 2>&1 || true
    fi
    
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
}

run_integration_tests() {
    local backend
    backend=$(get_backend_type)
    
    clear
    box_header "INTEGRATION TESTS"
    echo ""
    
    info "Running integration tests..."
    echo ""
    
    cd "$REPO_ROOT"
    
    if [[ "$backend" == "docker" ]]; then
        make test MODE=container 2>&1 || true
    else
        local env
        env=$(get_state "ENVIRONMENT" || echo "staging")
        cd "${REPO_ROOT}/provision/ansible"
        make test-all INV="inventory/${env}" 2>&1 || true
    fi
    
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
}

run_service_tests() {
    clear
    box_header "SERVICE TESTS"
    echo ""
    
    local services=(
        "data"
        "search"
        "agent"
        "authz"
    )
    
    local idx=1
    for svc in "${services[@]}"; do
        printf "  ${BOLD}%d)${NC} Test %s\n" "$idx" "$svc"
        ((idx++))
    done
    echo ""
    printf "  ${DIM}b = back${NC}\n"
    echo ""
    box_footer
    echo ""
    
    read -n 1 -s -r -p "Select option: " choice
    echo ""
    
    case "$choice" in
        1)
            clear
            info "Testing Data Service..."
            cd "$REPO_ROOT"
            make test-data 2>&1 || true
            echo ""
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        2)
            clear
            info "Testing Search Service..."
            cd "$REPO_ROOT"
            make test-search 2>&1 || true
            echo ""
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        3)
            clear
            info "Testing Agent Service..."
            cd "$REPO_ROOT"
            make test-agent 2>&1 || true
            echo ""
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        4)
            clear
            info "Testing AuthZ Service..."
            cd "$REPO_ROOT"
            make test-authz 2>&1 || true
            echo ""
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        b|B)
            return
            ;;
    esac
}

run_extraction_tests() {
    clear
    box_header "EXTRACTION STRATEGY TESTS"
    echo ""
    
    printf "  ${BOLD}1)${NC} Simple extraction (basic PDF)\n"
    printf "  ${BOLD}2)${NC} LLM-enhanced extraction\n"
    printf "  ${BOLD}3)${NC} Marker extraction (GPU)\n"
    printf "  ${BOLD}4)${NC} ColPali visual extraction\n"
    printf "  ${BOLD}5)${NC} Run all extraction tests\n"
    echo ""
    printf "  ${DIM}b = back${NC}\n"
    echo ""
    box_footer
    echo ""
    
    read -n 1 -s -r -p "Select option: " choice
    echo ""
    
    case "$choice" in
        1)
            clear
            info "Testing simple extraction..."
            cd "$REPO_ROOT"
            make test-extraction-simple 2>&1 || true
            echo ""
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        2)
            clear
            info "Testing LLM-enhanced extraction..."
            cd "$REPO_ROOT"
            make test-extraction-llm 2>&1 || true
            echo ""
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        3)
            clear
            info "Testing Marker extraction..."
            cd "$REPO_ROOT"
            make test-extraction-marker 2>&1 || true
            echo ""
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        4)
            clear
            info "Testing ColPali extraction..."
            cd "$REPO_ROOT"
            make test-extraction-colpali 2>&1 || true
            echo ""
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        5)
            clear
            info "Running all extraction tests..."
            cd "$REPO_ROOT"
            make test-extraction-all 2>&1 || true
            echo ""
            read -n 1 -s -r -p "Press any key to continue..."
            ;;
        b|B)
            return
            ;;
    esac
}

run_custom_test() {
    clear
    box_header "CUSTOM TEST"
    echo ""
    
    printf "  Enter pytest arguments (e.g., -k 'test_upload' -v):\n"
    echo ""
    read -p "  > " args
    
    if [[ -n "$args" ]]; then
        clear
        info "Running: pytest $args"
        cd "$REPO_ROOT"
        make test MODE=container ARGS="$args" 2>&1 || true
        echo ""
        read -n 1 -s -r -p "Press any key to continue..."
    fi
}

# ============================================================================
# Main Menu
# ============================================================================

show_test_menu() {
    local backend
    backend=$(get_backend_type)
    local env
    env=$(get_state "ENVIRONMENT" || echo "development")
    
    clear
    box_header "BUSIBOX - TESTING"
    echo ""
    printf "  ${CYAN}Environment:${NC} %s (%s)\n" "$env" "$backend"
    echo ""
    
    printf "  ${BOLD}Quick Tests${NC}\n"
    printf "    ${BOLD}1)${NC} Health Checks\n"
    printf "    ${BOLD}2)${NC} Smoke Tests\n"
    echo ""
    printf "  ${BOLD}Integration Tests${NC}\n"
    printf "    ${BOLD}3)${NC} Full Integration Suite\n"
    printf "    ${BOLD}4)${NC} Service-Specific Tests\n"
    printf "    ${BOLD}5)${NC} Extraction Strategy Tests\n"
    echo ""
    printf "  ${BOLD}Advanced${NC}\n"
    printf "    ${BOLD}6)${NC} Run Custom Test\n"
    echo ""
    printf "  ${DIM}b = back to main menu    q = quit${NC}\n"
    echo ""
    box_footer
}

main() {
    while true; do
        show_test_menu
        echo ""
        
        read -n 1 -s -r -p "Select option: " choice
        echo ""
        
        case "$choice" in
            1)
                run_health_checks
                ;;
            2)
                run_smoke_tests
                ;;
            3)
                run_integration_tests
                ;;
            4)
                run_service_tests
                ;;
            5)
                run_extraction_tests
                ;;
            6)
                run_custom_test
                ;;
            b|B)
                exec bash "${SCRIPT_DIR}/launcher.sh"
                ;;
            q|Q)
                echo ""
                echo "Goodbye!"
                exit 0
                ;;
        esac
    done
}

# Run main
main "$@"
