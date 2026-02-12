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
#     bash scripts/tests/run-local-tests.sh data test --verbose
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

# Detect container prefix from .env.* files or running containers
# The prefix determines Docker container names (e.g., dev-postgres, local-authz-api)
detect_container_prefix() {
    # 1. Check CONTAINER_PREFIX env var (may be set by caller)
    if [[ -n "${CONTAINER_PREFIX:-}" ]]; then
        echo "$CONTAINER_PREFIX"
        return
    fi
    
    # 2. Read from .env.dev or other .env.* state files
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
    
    # 3. Detect from running Docker containers (look for *-postgres pattern)
    local running
    running=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -oE '^[a-z]+-postgres$' | head -1 | sed 's/-postgres$//' || true)
    if [[ -n "$running" ]]; then
        echo "$running"
        return
    fi
    
    # 4. Default fallback
    echo "dev"
}

DOCKER_PREFIX=$(detect_container_prefix)

# Parse arguments
SERVICE="${1:-}"
ENV="${2:-test}"
shift 2 2>/dev/null || true
PYTEST_ARGS="$*"

# FAST mode: skip slow and GPU tests
# Set via environment variable FAST=1
# Note: Only add FAST filter if user hasn't specified their own -m filter or a specific path
if [[ "${FAST:-}" == "1" ]]; then
    # Don't add FAST filter if user specified a marker filter or a test path
    if [[ "$PYTEST_ARGS" == *"-m "* ]]; then
        info "FAST mode: disabled because -m filter was specified in ARGS"
    elif [[ "$PYTEST_ARGS" =~ ^tests/ ]]; then
        info "FAST mode: disabled because specific test path was specified"
    else
        PYTEST_ARGS="-m 'not slow and not gpu' $PYTEST_ARGS"
        info "FAST mode: skipping @pytest.mark.slow and @pytest.mark.gpu tests"
    fi
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
    authz|data|ingest|search|agent|ai-portal|agent-manager|apps|all)
        ;;
    *)
        error "Unknown service: $SERVICE"
        echo "Valid services: authz, data, data, search, agent, ai-portal, agent-manager, apps, all"
        exit 1
        ;;
esac

# Map 'data' to 'data' for backward compatibility
if [[ "$SERVICE" == "data" ]]; then
    warn "Service 'data' is deprecated, using 'data' instead"
    SERVICE="data"
fi

# Step 1: Generate environment file
header "Step 1: Generate Environment" 70

# For Docker environment, use the root .env.local with Docker-specific overrides
if [[ "$ENV" == "docker" ]]; then
    info "Using Docker environment configuration..."
    ENV_FILE="${REPO_ROOT}/.env.local"
    
    if [[ ! -f "$ENV_FILE" ]]; then
        if [[ -f "${REPO_ROOT}/env.local.example" ]]; then
            warn "No .env.local found. Creating from env.local.example..."
            cp "${REPO_ROOT}/env.local.example" "$ENV_FILE"
        else
            error "No .env.local found and no env.local.example to copy from"
            exit 1
        fi
    fi
    
    # Docker-specific environment overrides (services are on localhost)
    export POSTGRES_HOST=localhost
    export POSTGRES_PORT=5432
    export POSTGRES_DB=busibox
    export POSTGRES_USER=busibox_user
    export POSTGRES_PASSWORD=devpassword
    export REDIS_HOST=localhost
    export REDIS_PORT=6379
    export MILVUS_HOST=localhost
    export MILVUS_PORT=19530
    export MINIO_ENDPOINT=localhost:9000
    export MINIO_ACCESS_KEY=minioadmin
    export MINIO_SECRET_KEY=minioadmin
    export AUTHZ_JWKS_URL=http://localhost:8010/.well-known/jwks.json
    export AUTHZ_ISSUER=busibox-authz
    export AUTHZ_TOKEN_URL=http://localhost:8010/oauth/token
    export TEST_AUTHZ_URL=http://localhost:8010
    export LITELLM_BASE_URL=http://localhost:4000
    export LITELLM_API_KEY=sk-local-dev-key
    export EMBEDDING_SERVICE_URL=http://localhost:8002
    export SEARCH_API_URL=http://localhost:8003
    export DATA_API_URL=http://localhost:8002
    export DATA_API_URL=http://localhost:8002  # Deprecated alias
    export AGENT_API_URL=http://localhost:8000
    
    # Agent API uses AUTH_* (without Z) for its own auth config
    export AUTH_JWKS_URL=http://localhost:8010/.well-known/jwks.json
    export AUTH_ISSUER=busibox-authz
    export AUTH_TOKEN_URL=http://localhost:8010/oauth/token
    
    # Database URLs for each service
    export DATABASE_URL=postgresql+asyncpg://busibox_user:devpassword@localhost:5432/agent
    
    # Test-specific environment variables (needed for integration tests)
    export TEST_DB_HOST=localhost
    export TEST_DB_PORT=5432
    export TEST_DB_NAME=busibox
    export TEST_DB_USER=busibox_user
    export TEST_DB_PASSWORD=devpassword
    
    # Use the well-known consistent test user ID
    # This ID is created by bootstrap-test-databases.py in the test_authz database
    # via 'make test-db-init'
    TEST_USER_ID="00000000-0000-0000-0000-000000000001"
    TEST_USER_EMAIL="test@busibox.local"
    
    # Verify the test user exists in test_authz database
    USER_CHECK=$(docker exec "${DOCKER_PREFIX}-postgres" psql -U busibox_test_user -d test_authz -t -A -c "SELECT user_id FROM authz_users WHERE user_id = '${TEST_USER_ID}' LIMIT 1;" 2>/dev/null || echo "")
    
    if [[ -z "$USER_CHECK" ]]; then
        info "Test user not found in test_authz. Attempting to create..."
        # Try to create the user directly
        docker exec "${DOCKER_PREFIX}-postgres" psql -U busibox_test_user -d test_authz -c "INSERT INTO authz_users (user_id, email, created_at) VALUES ('${TEST_USER_ID}', '${TEST_USER_EMAIL}', NOW()) ON CONFLICT DO NOTHING;" 2>/dev/null || {
            warn "Could not create test user. Run 'make test-db-init' to bootstrap test databases."
        }
        info "Test user: ${TEST_USER_ID}"
    fi
    
    export TEST_USER_ID
    export TEST_USER_EMAIL
    
    success "Docker environment configured (services on localhost)"
else
    # For Proxmox environments, use the generate script
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
fi

success "Environment file ready: $ENV_FILE"
echo ""

# Step 2: Set up and run tests
header "Step 2: Run Tests" 70

# Function to start local worker
start_local_worker() {
    local service_dir="${REPO_ROOT}/srv/data"
    local venv_dir="${service_dir}/test_venv"
    
    if [[ ! -d "$venv_dir" ]]; then
        venv_dir="${service_dir}/venv"
    fi
    
    if [[ ! -d "$venv_dir" ]]; then
        warn "No virtual environment found for data worker"
        return 1
    fi
    
    info "Starting local data worker..."
    
    # Load environment
    set -a
    source "$ENV_FILE"
    set +a
    
    # Set PYTHONPATH for the worker
    export PYTHONPATH="${service_dir}/src:${PYTHONPATH:-}"
    
    # Set LOCAL_WORKER so tests know a worker was started
    export LOCAL_WORKER=1
    
    # Start worker in background with explicitly exported environment
    # Export REDIS_STREAM to ensure worker uses the local stream
    export REDIS_STREAM="${REDIS_STREAM:-jobs:data:local}"
    
    (
        cd "$service_dir"
        source "${venv_dir}/bin/activate"
        # Re-export to ensure it persists after venv activation
        export REDIS_STREAM="${REDIS_STREAM}"
        export PYTHONPATH="${service_dir}/src:${PYTHONPATH:-}"
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
    
    # For Docker environment, run tests inside the container
    if [[ "$ENV" == "docker" ]]; then
        run_docker_container_tests "$service"
        return $?
    fi
    
    # Start local worker if requested and testing ingest
    if [[ "$START_LOCAL_WORKER" == "1" ]] && [[ "$service" == "data" ]]; then
        if [[ -z "$WORKER_PID" ]]; then
            start_local_worker
        fi
    fi
    
    # Find virtual environment (check common names)
    # Prefer test_venv first as it's specifically for testing and may have newer Python
    if [[ -d "${service_dir}/test_venv" ]]; then
        venv_dir="${service_dir}/test_venv"
    elif [[ -d "${service_dir}/venv" ]]; then
        venv_dir="${service_dir}/venv"
    elif [[ -d "${service_dir}/.venv" ]]; then
        venv_dir="${service_dir}/.venv"
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
        
        # Always ensure core test tools are installed
        # This handles cases where venv exists but is incomplete
        if ! python -c "import pytest" 2>/dev/null; then
            info "Installing core test dependencies (pytest missing)..."
            pip install -q --upgrade pip
            
            # Install main requirements if present
            if [[ -f "requirements.txt" ]]; then
                info "Installing requirements.txt..."
                pip install -q -r requirements.txt
            fi
            
            # Install test requirements if present
            if [[ -f "requirements.test.txt" ]]; then
                info "Installing requirements.test.txt..."
                pip install -q -r requirements.test.txt
            fi
            
            # Always ensure pytest and httpx are available for tests
            pip install -q pytest pytest-asyncio httpx
        else
            # Pytest exists, just ensure test dependencies are up to date
            if [[ -f "requirements.test.txt" ]]; then
                pip install -q -r requirements.test.txt 2>/dev/null || true
            fi
        fi
    fi
    
    # Determine test directory
    local test_dir="tests"
    if [[ ! -d "$test_dir" ]]; then
        warn "No tests directory found in $service_dir"
        return 0
    fi
    
    # Use python -m pytest to avoid broken shebang issues in venv
    local pytest_cmd="python -m pytest"
    if [[ -n "$venv_dir" ]] && [[ -f "${venv_dir}/bin/python" ]]; then
        pytest_cmd="${venv_dir}/bin/python -m pytest"
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

# Run tests inside Docker container
# This avoids Python version compatibility issues (host Python 3.14 vs container Python 3.11)
run_docker_container_tests() {
    local service="$1"
    
    # Map service name to container name using detected prefix
    local container_name=""
    case "$service" in
        authz)   container_name="${DOCKER_PREFIX}-authz-api" ;;
        data)    container_name="${DOCKER_PREFIX}-data-api" ;;
        search)  container_name="${DOCKER_PREFIX}-search-api" ;;
        agent)   container_name="${DOCKER_PREFIX}-agent-api" ;;
        *)
            error "Unknown service: $service"
            return 1
            ;;
    esac
    
    # Check if container is running
    if ! docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        error "Container not running: $container_name"
        info "Start Docker services first: make docker-up"
        return 1
    fi
    
    info "Running tests inside container: $container_name"
    echo ""
    
    # Copy test files to container (they may have changed)
    local service_dir="${REPO_ROOT}/srv/${service}"
    if [[ -d "${service_dir}/tests" ]]; then
        info "Syncing test files to container..."
        # Remove old test structure in container first to avoid stale files
        docker exec "$container_name" sh -c "rm -rf /app/tests/*" 2>/dev/null || true
        # Copy entire tests directory with new structure
        docker cp "${service_dir}/tests/." "${container_name}:/app/tests/"
    fi
    
    # Copy test requirements if present
    if [[ -f "${service_dir}/requirements.test.txt" ]]; then
        docker cp "${service_dir}/requirements.test.txt" "${container_name}:/app/"
    fi
    
    # Copy pytest.ini if present (needed for pytest-asyncio config)
    if [[ -f "${service_dir}/pytest.ini" ]]; then
        docker cp "${service_dir}/pytest.ini" "${container_name}:/app/"
    fi
    
    # Copy conftest.py if present
    if [[ -f "${service_dir}/tests/conftest.py" ]]; then
        docker cp "${service_dir}/tests/conftest.py" "${container_name}:/app/tests/"
    fi
    
    # Install test dependencies and run tests inside container
    # Use a single exec to avoid multiple container connections
    local test_path="tests"
    local pytest_filter=""
    
    if [[ -n "$PYTEST_ARGS" ]]; then
        # Check if PYTEST_ARGS contains a path (tests/unit, tests/integration, or a specific file)
        if [[ "$PYTEST_ARGS" =~ ^tests/ ]]; then
            # PYTEST_ARGS is a path, use it as test_path and no default filter
            test_path="$PYTEST_ARGS"
            pytest_filter=""
        else
            # PYTEST_ARGS is a filter/option, keep default test path
            test_path="tests"
            pytest_filter="$PYTEST_ARGS"
        fi
    else
        # Default: skip slow and gpu tests
        test_path="tests"
        pytest_filter="-m 'not slow and not gpu'"
    fi
    
    # Build the actual pytest command
    local pytest_cmd="python -m pytest $test_path -v"
    if [[ -n "$pytest_filter" ]]; then
        pytest_cmd="$pytest_cmd $pytest_filter"
    fi
    
    info "Running: $pytest_cmd"
    echo ""
    
    # Build test database environment variables
    # Tests ALWAYS use ISOLATED test databases (test_authz, test_data, test_agent)
    # owned by busibox_test_user - NEVER production databases
    # See config/init-databases.sql for test database setup
    local test_db_name="test_authz"
    case "$service" in
        authz)  test_db_name="test_authz" ;;
        data) test_db_name="test_data" ;;
        search) test_db_name="test_data" ;;
        agent)  test_db_name="test_agent" ;;
    esac
    
    # Use the well-known consistent test user ID
    # This ID is created by bootstrap-test-databases.py in the test_authz database
    local test_user_id="00000000-0000-0000-0000-000000000001"
    
    # Verify the test user exists in test_authz (created by make test-db-init)
    local user_check
    user_check=$(docker exec "${DOCKER_PREFIX}-postgres" psql -U busibox_test_user -d test_authz -t -A -c "SELECT user_id FROM authz_users WHERE user_id = '${test_user_id}' LIMIT 1;" 2>/dev/null || echo "")
    if [[ -z "$user_check" ]]; then
        warn "Test user not found in test_authz database. Run 'make test-db-init' first."
        warn "Attempting to create test user..."
        docker exec "${DOCKER_PREFIX}-postgres" psql -U busibox_test_user -d test_authz -c "INSERT INTO authz_users (user_id, email, created_at) VALUES ('${test_user_id}', 'test@busibox.local', NOW()) ON CONFLICT DO NOTHING;" 2>/dev/null || true
    fi
    
    # Run tests with proper error handling
    # Tests use isolated test databases with busibox_test_user
    # PYTHONPATH includes /app/shared for testing and busibox_common modules
    if docker exec \
        -e PYTHONPATH=/app/src:/app:/app/shared \
        -e TEST_DB_HOST=postgres \
        -e TEST_DB_PORT=5432 \
        -e TEST_DB_NAME="$test_db_name" \
        -e TEST_DB_USER=busibox_test_user \
        -e TEST_DB_PASSWORD=testpassword \
        -e TEST_AUTHZ_URL=http://authz-api:8010 \
        -e AUTHZ_JWKS_URL=http://authz-api:8010/.well-known/jwks.json \
        -e TEST_USER_ID="$test_user_id" \
        -e TEST_DOC_REPO_PATH=/testdocs \
        "$container_name" \
        sh -c "pip install -q pytest pytest-asyncio httpx 2>/dev/null; \
               if [ -f /app/requirements.test.txt ]; then pip install -q -r /app/requirements.test.txt 2>/dev/null || true; fi; \
               cd /app && $pytest_cmd"; then
        success "$service tests passed!"
        return 0
    else
        error "$service tests failed!"
        return 1
    fi
}

# Run tests for Node.js apps (ai-portal, agent-manager) inside Docker containers
run_nodejs_app_tests() {
    local app="$1"
    
    # Map app name to container and source directory
    local container_name=""
    local app_dir=""
    case "$app" in
        ai-portal)
            container_name="${DOCKER_PREFIX}-ai-portal"
            app_dir="${REPO_ROOT}/../ai-portal"
            ;;
        agent-manager)
            container_name="${DOCKER_PREFIX}-agent-manager"
            app_dir="${REPO_ROOT}/../agent-manager"
            ;;
        *)
            error "Unknown Node.js app: $app"
            return 1
            ;;
    esac
    
    info "Testing: $app (Node.js/vitest)"
    echo ""
    
    # Check if container is running
    if ! docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        error "Container not running: $container_name"
        info "Start Docker services first: make docker-up"
        return 1
    fi
    
    # Check if source directory exists
    if [[ ! -d "$app_dir" ]]; then
        error "App directory not found: $app_dir"
        info "Expected sibling directory to busibox"
        return 1
    fi
    
    info "Running tests inside container: $container_name"
    echo ""
    
    # Build the vitest command
    # Note: Don't pass PYTEST_ARGS if it contains pytest-specific flags like -m
    local vitest_cmd="npm run test"
    local vitest_args="${VITEST_ARGS:-}"
    
    # Check if user passed Node.js/vitest-compatible args (not pytest args)
    if [[ -n "$vitest_args" ]]; then
        vitest_cmd="npm run test -- $vitest_args"
    fi
    
    info "Running: $vitest_cmd"
    echo ""
    
    # Run tests inside the container
    # The container already has the source mounted and dependencies installed
    if docker exec \
        -e NODE_ENV=test \
        -e AUTHZ_BASE_URL=http://authz-api:8010 \
        -e AUTHZ_JWKS_URL=http://authz-api:8010/.well-known/jwks.json \
        -e AUTHZ_ISSUER=busibox-authz \
        -e DATABASE_URL="postgresql://busibox_user:devpassword@postgres:5432/busibox" \
        -e DATA_API_HOST=data-api \
        -e DATA_API_PORT=8002 \
        -e DATA_API_HOST=data-api \
        -e DATA_API_PORT=8002 \
        -e SEARCH_API_HOST=search-api \
        -e SEARCH_API_PORT=8003 \
        -e AGENT_API_HOST=agent-api \
        -e AGENT_API_PORT=8000 \
        "$container_name" \
        sh -c "cd /app && $vitest_cmd"; then
        success "$app tests passed!"
        return 0
    else
        error "$app tests failed!"
        return 1
    fi
}

# Run tests for selected service(s)
FAILED_SERVICES=""

if [[ "$SERVICE" == "all" ]]; then
    # Run Python service tests
    for svc in authz data search agent; do
        echo ""
        separator 70
        if ! run_service_tests "$svc"; then
            FAILED_SERVICES="$FAILED_SERVICES $svc"
        fi
    done
    # Run Node.js app tests
    for app in ai-portal agent-manager; do
        echo ""
        separator 70
        if ! run_nodejs_app_tests "$app"; then
            FAILED_SERVICES="$FAILED_SERVICES $app"
        fi
    done
elif [[ "$SERVICE" == "apps" ]]; then
    # Run only Node.js app tests
    for app in ai-portal agent-manager; do
        echo ""
        separator 70
        if ! run_nodejs_app_tests "$app"; then
            FAILED_SERVICES="$FAILED_SERVICES $app"
        fi
    done
elif [[ "$SERVICE" == "ai-portal" ]] || [[ "$SERVICE" == "agent-manager" ]]; then
    # Run specific Node.js app test
    if ! run_nodejs_app_tests "$SERVICE"; then
        FAILED_SERVICES="$SERVICE"
    fi
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

