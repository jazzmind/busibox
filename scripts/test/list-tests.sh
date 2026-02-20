#!/usr/bin/env bash
#
# List Available Tests for Busibox Services
#
# EXECUTION CONTEXT: Admin workstation (from repo root)
# PURPOSE: Discover and list available tests for each service to help LLM agents drive testing
#
# USAGE:
#   bash scripts/test/list-tests.sh                # List all services and their test categories
#   bash scripts/test/list-tests.sh agent           # List test files for agent service
#   bash scripts/test/list-tests.sh agent unit      # List unit test files + test names for agent
#   bash scripts/test/list-tests.sh agent unit full # List full test IDs (file::class::method)
#   bash scripts/test/list-tests.sh all             # List all tests across all services
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SERVICE="${1:-}"
CATEGORY="${2:-}"
DETAIL="${3:-}"

# Python services with their test directories
PYTHON_SERVICES=(authz agent bridge data deploy search)

# Detect container prefix for Docker test collection
detect_container_prefix() {
    if [[ -n "${CONTAINER_PREFIX:-}" ]]; then
        echo "$CONTAINER_PREFIX"
        return
    fi
    for env_file in "${REPO_ROOT}/.env.dev" "${REPO_ROOT}/.env.local-dev" "${REPO_ROOT}/.env.demo"; do
        if [[ -f "$env_file" ]]; then
            local prefix
            prefix=$(grep -E '^CONTAINER_PREFIX=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' "'"'" || true)
            if [[ -n "$prefix" ]]; then
                echo "$prefix"
                return
            fi
        fi
    done
    local running
    running=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -oE '^[a-z]+-postgres$' | head -1 | sed 's/-postgres$//' || true)
    if [[ -n "$running" ]]; then
        echo "$running"
        return
    fi
    echo "dev"
}

# List test categories (unit/integration) for a service
list_categories() {
    local svc="$1"
    local test_dir="${REPO_ROOT}/srv/${svc}/tests"
    
    if [[ ! -d "$test_dir" ]]; then
        echo "  (no tests directory)"
        return
    fi
    
    for subdir in "$test_dir"/*/; do
        [[ -d "$subdir" ]] || continue
        local name
        name=$(basename "$subdir")
        # Skip __pycache__, fixtures, and other non-test dirs
        [[ "$name" == "__pycache__" || "$name" == "fixtures" || "$name" == "utils" || "$name" == "helpers" ]] && continue
        local count
        count=$(find "$subdir" -maxdepth 1 -name "test_*.py" 2>/dev/null | wc -l | tr -d ' ')
        echo "  ${name}/ (${count} files)"
    done
}

# List test files for a service, optionally filtered by category
list_files() {
    local svc="$1"
    local category="${2:-}"
    local test_dir="${REPO_ROOT}/srv/${svc}/tests"
    
    if [[ ! -d "$test_dir" ]]; then
        return
    fi
    
    if [[ -n "$category" ]]; then
        find "${test_dir}/${category}" -maxdepth 1 -name "test_*.py" 2>/dev/null | sort | while read -r f; do
            echo "  tests/${category}/$(basename "$f")"
        done
    else
        find "$test_dir" -name "test_*.py" -not -path "*/__pycache__/*" 2>/dev/null | sort | while read -r f; do
            local rel
            rel=$(echo "$f" | sed "s|${REPO_ROOT}/srv/${svc}/||")
            echo "  $rel"
        done
    fi
}

# Collect test IDs from within Docker container using pytest --collect-only
collect_test_ids_docker() {
    local svc="$1"
    local category="${2:-}"
    local docker_prefix
    docker_prefix=$(detect_container_prefix)
    
    local container_name=""
    case "$svc" in
        authz)   container_name="${docker_prefix}-authz-api" ;;
        data)    container_name="${docker_prefix}-data-api" ;;
        search)  container_name="${docker_prefix}-search-api" ;;
        agent)   container_name="${docker_prefix}-agent-api" ;;
        bridge)  container_name="${docker_prefix}-bridge-api" ;;
        *)       return 1 ;;
    esac
    
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container_name}$"; then
        echo "  (container ${container_name} not running - showing file listing instead)"
        list_files "$svc" "$category"
        return
    fi
    
    # Sync test files first
    local service_dir="${REPO_ROOT}/srv/${svc}"
    if [[ -d "${service_dir}/tests" ]]; then
        docker exec "$container_name" sh -c "rm -rf /app/tests/*" 2>/dev/null || true
        docker cp "${service_dir}/tests/." "${container_name}:/app/tests/" 2>/dev/null || true
    fi
    if [[ -f "${service_dir}/pytest.ini" ]]; then
        docker cp "${service_dir}/pytest.ini" "${container_name}:/app/" 2>/dev/null || true
    fi
    
    local test_path="tests"
    if [[ -n "$category" ]]; then
        test_path="tests/${category}"
    fi
    
    docker exec \
        -e PYTHONPATH=/app/src:/app:/app/shared \
        "$container_name" \
        sh -c "pip install -q pytest pytest-asyncio 2>/dev/null; cd /app && python -m pytest ${test_path} --collect-only -q 2>/dev/null || true" \
        2>/dev/null | grep "::" | sed 's/^/  /'
}

# Read pytest markers from pytest.ini
list_markers() {
    local svc="$1"
    local ini="${REPO_ROOT}/srv/${svc}/pytest.ini"
    
    if [[ ! -f "$ini" ]]; then
        return
    fi
    
    local in_markers=0
    while IFS= read -r line; do
        if [[ "$line" =~ ^markers ]]; then
            in_markers=1
            continue
        fi
        if [[ $in_markers -eq 1 ]]; then
            if [[ "$line" =~ ^[a-z] ]] || [[ -z "$line" ]]; then
                break
            fi
            local trimmed
            trimmed=$(echo "$line" | sed 's/^[[:space:]]*//')
            if [[ -n "$trimmed" ]]; then
                echo "  @pytest.mark.${trimmed}"
            fi
        fi
    done < "$ini"
}

# ============================================================================
# MAIN
# ============================================================================

if [[ -z "$SERVICE" ]]; then
    # Overview mode: list all services with their test categories
    echo ""
    echo "BUSIBOX TEST INVENTORY"
    echo "======================"
    echo ""
    echo "Python API Services:"
    for svc in "${PYTHON_SERVICES[@]}"; do
        local_test_dir="${REPO_ROOT}/srv/${svc}/tests"
        if [[ -d "$local_test_dir" ]]; then
            total=$(find "$local_test_dir" -name "test_*.py" -not -path "*/__pycache__/*" 2>/dev/null | wc -l | tr -d ' ')
            echo ""
            echo "  ${svc} (${total} test files)"
            list_categories "$svc"
            
            markers=$(list_markers "$svc")
            if [[ -n "$markers" ]]; then
                echo "  markers:"
                echo "$markers"
            fi
        fi
    done
    
    echo ""
    echo "Root-level Tests:"
    echo "  tests/security/ (security testing suite)"
    echo ""
    echo "─────────────────────────────────────────"
    echo "For more detail:"
    echo "  make test-docker ACTION=list SERVICE=agent           # List test files"
    echo "  make test-docker ACTION=list SERVICE=agent CATEGORY=unit  # List unit tests"
    echo "  make test-docker ACTION=list SERVICE=agent CATEGORY=unit DETAIL=full  # Collect test IDs from Docker"
    echo ""
    
elif [[ "$SERVICE" == "all" ]]; then
    # List everything
    for svc in "${PYTHON_SERVICES[@]}"; do
        echo ""
        echo "═══ ${svc} ═══"
        if [[ -n "$CATEGORY" ]]; then
            list_files "$svc" "$CATEGORY"
        else
            list_files "$svc"
        fi
    done
    echo ""
    
else
    # Single service mode
    svc="$SERVICE"
    test_dir="${REPO_ROOT}/srv/${svc}/tests"
    
    if [[ ! -d "$test_dir" ]]; then
        echo "Error: No tests directory found at srv/${svc}/tests"
        exit 1
    fi
    
    echo ""
    echo "═══ ${svc} tests ═══"
    echo ""
    
    if [[ -z "$CATEGORY" ]]; then
        # List categories + all files
        echo "Categories:"
        list_categories "$svc"
        echo ""
        echo "All test files:"
        list_files "$svc"
    elif [[ "$DETAIL" == "full" ]]; then
        # Collect full test IDs from Docker container
        echo "Collecting test IDs from Docker container (tests/${CATEGORY}/)..."
        echo ""
        collect_test_ids_docker "$svc" "$CATEGORY"
    else
        # List files in category
        echo "Test files (tests/${CATEGORY}/):"
        list_files "$svc" "$CATEGORY"
    fi
    
    echo ""
    
    # Show markers
    markers=$(list_markers "$svc")
    if [[ -n "$markers" ]]; then
        echo "Available markers:"
        echo "$markers"
        echo ""
    fi
fi
