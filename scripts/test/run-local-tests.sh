#!/usr/bin/env bash
#
# Run Local Tests Against Remote Containers
#
# EXECUTION CONTEXT: Admin workstation
# PURPOSE: Run service tests locally using container backends for rapid debugging
#
# USAGE:
#   Interactive:
#     bash scripts/tests/run-local-tests.sh
#
#   Direct:
#     bash scripts/tests/run-local-tests.sh authz test
#     bash scripts/tests/run-local-tests.sh ingest test --verbose
#     bash scripts/tests/run-local-tests.sh search production -k test_hybrid
#
# This script:
# 1. Generates a .env.local file with all secrets/IPs from vault
# 2. Activates the service's virtual environment
# 3. Runs pytest with the environment loaded
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source UI library
source "${REPO_ROOT}/scripts/lib/ui.sh"

# Parse arguments
SERVICE="${1:-}"
ENV="${2:-test}"
shift 2 2>/dev/null || true
PYTEST_ARGS="$*"

# FAST mode: skip slow and GPU tests
# Set via environment variable FAST=1
if [[ "${FAST:-}" == "1" ]]; then
    PYTEST_ARGS="-m 'not slow and not gpu' $PYTEST_ARGS"
    info "FAST mode: skipping @pytest.mark.slow and @pytest.mark.gpu tests"
fi

# WORKER mode: start a local worker for tests that require it
# Set via environment variable WORKER=1
WORKER_PID=""
START_LOCAL_WORKER="${WORKER:-0}"

# Interactive mode if no service provided
if [[ -z "$SERVICE" ]]; then
    clear
    box "Local Test Runner" 70
    echo ""
    info "Run tests locally against remote container backends"
    echo ""
    
    SERVICE=$(select_test_service)
    ENV=$(select_environment)
    
    echo ""
    info "Selected: $SERVICE service on $ENV environment"
    echo ""
fi

# Validate service
case "$SERVICE" in
    authz|ingest|search|agent|all)
        ;;
    *)
        error "Unknown service: $SERVICE"
        echo "Valid services: authz, ingest, search, agent, all"
        exit 1
        ;;
esac

# Step 1: Generate environment file
header "Step 1: Generate Environment" 70

info "Extracting secrets and generating .env.local..."
if ! bash "${SCRIPT_DIR}/generate-local-test-env.sh" "$SERVICE" "$ENV" > /dev/null; then
    error "Failed to generate environment file"
    exit 1
fi

if [[ "$SERVICE" == "all" ]]; then
    ENV_FILE="${REPO_ROOT}/.env.local"
else
    ENV_FILE="${REPO_ROOT}/srv/${SERVICE}/.env.local"
fi

if [[ ! -f "$ENV_FILE" ]]; then
    error "Environment file not generated: $ENV_FILE"
    exit 1
fi

success "Environment file ready: $ENV_FILE"
echo ""

# Step 2: Set up and run tests
header "Step 2: Run Tests" 70

# Function to start local worker
start_local_worker() {
    local service_dir="${REPO_ROOT}/srv/ingest"
    local venv_dir="${service_dir}/test_venv"
    
    if [[ ! -d "$venv_dir" ]]; then
        venv_dir="${service_dir}/venv"
    fi
    
    if [[ ! -d "$venv_dir" ]]; then
        warn "No virtual environment found for ingest worker"
        return 1
    fi
    
    info "Starting local ingest worker..."
    
    # Load environment
    set -a
    source "$ENV_FILE"
    set +a
    
    # Set PYTHONPATH for the worker
    export PYTHONPATH="${service_dir}/src:${PYTHONPATH:-}"
    
    # Set LOCAL_WORKER so tests know a worker was started
    export LOCAL_WORKER=1
    
    # Start worker in background
    (
        cd "$service_dir"
        source "${venv_dir}/bin/activate"
        python src/worker.py 2>&1 | while read line; do
            echo "[WORKER] $line"
        done
    ) &
    WORKER_PID=$!
    
    # Wait for worker to initialize (longer wait for GPU model loading)
    info "Waiting for worker to initialize (loading models)..."
    sleep 10
    
    if kill -0 $WORKER_PID 2>/dev/null; then
        success "Local worker started (PID: $WORKER_PID)"
        return 0
    else
        error "Failed to start local worker"
        return 1
    fi
}

# Function to stop local worker
stop_local_worker() {
    if [[ -n "$WORKER_PID" ]] && kill -0 $WORKER_PID 2>/dev/null; then
        info "Stopping local worker (PID: $WORKER_PID)..."
        kill -TERM $WORKER_PID 2>/dev/null || true
        sleep 2
        kill -9 $WORKER_PID 2>/dev/null || true
        success "Local worker stopped"
    fi
}

# Cleanup function
cleanup() {
    stop_local_worker
}

# Set trap for cleanup on exit
trap cleanup EXIT

run_service_tests() {
    local service="$1"
    local service_dir="${REPO_ROOT}/srv/${service}"
    local venv_dir=""
    
    if [[ ! -d "$service_dir" ]]; then
        error "Service directory not found: $service_dir"
        return 1
    fi
    
    info "Testing: $service"
    echo ""
    
    # Start local worker if requested and testing ingest
    if [[ "$START_LOCAL_WORKER" == "1" ]] && [[ "$service" == "ingest" ]]; then
        if [[ -z "$WORKER_PID" ]]; then
            start_local_worker
        fi
    fi
    
    # Find virtual environment (check common names)
    if [[ -d "${service_dir}/venv" ]]; then
        venv_dir="${service_dir}/venv"
    elif [[ -d "${service_dir}/.venv" ]]; then
        venv_dir="${service_dir}/.venv"
    elif [[ -d "${service_dir}/test_venv" ]]; then
        venv_dir="${service_dir}/test_venv"
    fi
    
    # Change to service directory
    cd "$service_dir"
    
    # Load environment
    set -a
    source "$ENV_FILE"
    set +a
    
    # Auto-setup virtual environment if not found
    if [[ -z "$venv_dir" ]]; then
        info "No virtual environment found. Setting up test_venv..."
        venv_dir="${service_dir}/test_venv"
        
        python3 -m venv "$venv_dir"
        source "${venv_dir}/bin/activate"
        
        # Install requirements
        if [[ -f "requirements.txt" ]]; then
            info "Installing requirements.txt..."
            pip install -q --upgrade pip
            pip install -q -r requirements.txt
        fi
        
        # Install test requirements
        if [[ -f "requirements.test.txt" ]]; then
            info "Installing requirements.test.txt..."
            pip install -q -r requirements.test.txt
        fi
        
        # Always ensure pytest and httpx are available for tests
        pip install -q pytest pytest-asyncio httpx
        
        success "Virtual environment created: $venv_dir"
    else
        # Activate existing virtual environment
        info "Activating virtual environment: $venv_dir"
        source "${venv_dir}/bin/activate"
        
        # Install test dependencies if present (assumes main requirements already installed)
        if [[ -f "requirements.test.txt" ]]; then
            info "Installing test dependencies..."
            pip install -q -r requirements.test.txt 2>/dev/null || true
        fi
    fi
    
    # Determine test directory
    local test_dir="tests"
    if [[ ! -d "$test_dir" ]]; then
        warn "No tests directory found in $service_dir"
        return 0
    fi
    
    # Use pytest from venv if activated, otherwise system pytest
    local pytest_cmd="pytest"
    if [[ -n "$venv_dir" ]] && [[ -f "${venv_dir}/bin/pytest" ]]; then
        pytest_cmd="${venv_dir}/bin/pytest"
    fi
    
    info "Running: $pytest_cmd $test_dir -v $PYTEST_ARGS"
    echo ""
    
    # Add src to PYTHONPATH for the service
    export PYTHONPATH="${service_dir}/src:${PYTHONPATH:-}"
    
    # Use eval to properly handle quoted arguments in PYTEST_ARGS
    if eval "$pytest_cmd $test_dir -v $PYTEST_ARGS"; then
        success "$service tests passed!"
        return 0
    else
        error "$service tests failed!"
        return 1
    fi
}

# Run tests for selected service(s)
FAILED_SERVICES=""

if [[ "$SERVICE" == "all" ]]; then
    for svc in authz ingest search agent; do
        echo ""
        separator 70
        if ! run_service_tests "$svc"; then
            FAILED_SERVICES="$FAILED_SERVICES $svc"
        fi
    done
else
    if ! run_service_tests "$SERVICE"; then
        FAILED_SERVICES="$SERVICE"
    fi
fi

# Summary
echo ""
header "Test Summary" 70

if [[ -z "$FAILED_SERVICES" ]]; then
    success "All tests passed!"
else
    error "Failed services:$FAILED_SERVICES"
    exit 1
fi

