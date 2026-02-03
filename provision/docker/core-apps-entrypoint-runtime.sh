#!/bin/bash
# =============================================================================
# Core Apps Runtime Entrypoint Script
# =============================================================================
#
# Starts nginx via supervisord, then deploys apps via Deploy API.
# Apps are installed at runtime into persistent volumes, not baked into image.
#
# This mirrors the Proxmox pattern where:
#   - Container starts with just runtime dependencies
#   - Apps are deployed via Deploy API on first start
#   - Subsequent starts use existing apps from persistent volumes
#
# Modes:
#   start   - Normal startup (default)
#   deploy  - Deploy a specific app: entrypoint.sh deploy <app> [ref]
#   nginx-reload - Reload nginx configuration
#   bash    - Start bash shell for debugging
#
# Environment variables:
#   DEPLOY_API_URL - URL to Deploy API (required for deploy via API)
#   AUTHZ_BASE_URL - URL to AuthZ API (required for bootstrap token)
#   ADMIN_EMAIL - Admin email for bootstrap token (required for Deploy API auth)
#   GITHUB_AUTH_TOKEN - Required for cloning private repos and npm packages
#   AI_PORTAL_GITHUB_REF - Git ref for ai-portal (default: main)
#   AGENT_MANAGER_GITHUB_REF - Git ref for agent-manager (default: main)
#   DATABASE_URL - PostgreSQL connection string (required for ai-portal)
#
# =============================================================================

set -euo pipefail

# Default GitHub refs
AI_PORTAL_GITHUB_REF="${AI_PORTAL_GITHUB_REF:-main}"
AGENT_MANAGER_GITHUB_REF="${AGENT_MANAGER_GITHUB_REF:-main}"

# Deploy API URL (from docker-compose.github.yml)
DEPLOY_API_URL="${DEPLOYMENT_SERVICE_URL:-http://deploy-api:8011/api/v1/deployment}"
AUTHZ_BASE_URL="${AUTHZ_BASE_URL:-http://authz-api:8010}"

# Admin email from environment (for bootstrap token)
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@localhost}"

# Logging functions
log_info() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] INFO: $*"
}

log_error() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

log_success() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] SUCCESS: $*"
}

# =============================================================================
# SSL Certificate Generation
# =============================================================================
generate_ssl_cert() {
    local ssl_dir="/etc/nginx/ssl"
    local cert_file="$ssl_dir/server.crt"
    local key_file="$ssl_dir/server.key"
    
    if [ ! -f "$cert_file" ] || [ ! -f "$key_file" ]; then
        log_info "Generating self-signed SSL certificate..."
        mkdir -p "$ssl_dir"
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "$key_file" \
            -out "$cert_file" \
            -subj "/CN=localhost/O=Busibox/C=US" \
            2>/dev/null
        log_success "SSL certificate generated"
    fi
}

# =============================================================================
# NPM Authentication Setup
# =============================================================================
setup_npm_auth() {
    if [ -n "${GITHUB_AUTH_TOKEN:-}" ]; then
        log_info "Setting up npm authentication for @jazzmind packages..."
        
        # Create .npmrc in home directory
        echo "//npm.pkg.github.com/:_authToken=${GITHUB_AUTH_TOKEN}" > /root/.npmrc
        echo "@jazzmind:registry=https://npm.pkg.github.com" >> /root/.npmrc
        
        log_success "npm authentication configured"
    else
        log_error "GITHUB_AUTH_TOKEN not set - npm install may fail for @jazzmind packages"
    fi
}

# =============================================================================
# Wait for Deploy API
# =============================================================================
wait_for_deploy_api() {
    local max_attempts=60
    local attempt=1
    local deploy_health_url="${DEPLOY_API_URL}/health"
    
    log_info "Waiting for Deploy API at ${deploy_health_url}..."
    
    while [ $attempt -le $max_attempts ]; do
        if curl -sf "${deploy_health_url}" > /dev/null 2>&1; then
            log_success "Deploy API is ready"
            return 0
        fi
        
        log_info "Waiting for Deploy API... (attempt ${attempt}/${max_attempts})"
        sleep 5
        attempt=$((attempt + 1))
    done
    
    log_error "Deploy API not available after ${max_attempts} attempts"
    return 1
}

# =============================================================================
# Get Bootstrap Token from AuthZ
# =============================================================================
# Zero Trust flow: login → magic link → session JWT → token exchange
get_bootstrap_token() {
    log_info "Getting bootstrap token from AuthZ for ${ADMIN_EMAIL}..."
    
    # Step 1: Initiate login (creates magic link)
    local login_response
    login_response=$(curl -sf -X POST "${AUTHZ_BASE_URL}/auth/login/initiate" \
        -H "Content-Type: application/json" \
        -d "{\"email\": \"${ADMIN_EMAIL}\"}" 2>&1) || {
        log_error "Failed to initiate login: ${login_response}"
        return 1
    }
    
    local magic_link_token
    magic_link_token=$(echo "$login_response" | grep -o '"magic_link_token":"[^"]*"' | cut -d'"' -f4)
    
    if [ -z "$magic_link_token" ]; then
        log_error "No magic_link_token in response: ${login_response}"
        return 1
    fi
    
    # Step 2: Use magic link to get session JWT
    local session_response
    session_response=$(curl -sf -X POST "${AUTHZ_BASE_URL}/auth/magic-links/${magic_link_token}/use" 2>&1) || {
        log_error "Failed to use magic link: ${session_response}"
        return 1
    }
    
    local session_token
    session_token=$(echo "$session_response" | grep -o '"token":"[^"]*"' | head -1 | cut -d'"' -f4)
    
    if [ -z "$session_token" ]; then
        log_error "No session token in response: ${session_response}"
        return 1
    fi
    
    # Step 3: Exchange session token for deploy-api scoped token
    local exchange_response
    exchange_response=$(curl -sf -X POST "${AUTHZ_BASE_URL}/oauth/token" \
        -H "Content-Type: application/json" \
        -d "{
            \"grant_type\": \"urn:ietf:params:oauth:grant-type:token-exchange\",
            \"subject_token\": \"${session_token}\",
            \"subject_token_type\": \"urn:ietf:params:oauth:token-type:access_token\",
            \"audience\": \"deploy-api\"
        }" 2>&1) || {
        log_error "Failed to exchange token: ${exchange_response}"
        return 1
    }
    
    local access_token
    access_token=$(echo "$exchange_response" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
    
    if [ -z "$access_token" ]; then
        log_error "No access_token in response: ${exchange_response}"
        return 1
    fi
    
    log_success "Bootstrap token obtained"
    echo "$access_token"
}

# =============================================================================
# Deploy App via Deploy API
# =============================================================================
deploy_app_via_api() {
    local app_name="$1"
    local github_ref="${2:-main}"
    local token="$3"
    
    log_info "=== Deploying ${app_name} (ref: ${github_ref}) via Deploy API ==="
    
    # Map app names to GitHub repos
    local github_owner="jazzmind"
    local github_repo="${app_name}"
    
    # Deploy via API
    local deploy_response
    deploy_response=$(curl -sf -X POST "${DEPLOY_API_URL}/deploy" \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        -d "{
            \"manifest\": {
                \"id\": \"${app_name}\",
                \"name\": \"${app_name}\",
                \"version\": \"1.0.0\",
                \"defaultPath\": \"/${app_name}\",
                \"runtime\": {
                    \"type\": \"nextjs\",
                    \"nodeVersion\": \"20\"
                },
                \"build\": {
                    \"command\": \"npm run build\"
                },
                \"run\": {
                    \"command\": \"npm start\"
                }
            },
            \"config\": {
                \"environment\": \"docker\",
                \"githubRepoOwner\": \"${github_owner}\",
                \"githubRepoName\": \"${github_repo}\",
                \"githubBranch\": \"${github_ref}\",
                \"githubToken\": \"${GITHUB_AUTH_TOKEN:-}\",
                \"devMode\": false,
                \"secrets\": {}
            }
        }" 2>&1) || {
        log_error "Deploy API call failed: ${deploy_response}"
        return 1
    }
    
    # Extract deployment ID
    local deployment_id
    deployment_id=$(echo "$deploy_response" | grep -o '"deploymentId":"[^"]*"' | cut -d'"' -f4)
    
    if [ -z "$deployment_id" ]; then
        log_error "No deploymentId in response: ${deploy_response}"
        return 1
    fi
    
    log_info "Deployment started: ${deployment_id}"
    
    # Poll for completion
    local max_polls=120  # 10 minutes (5s interval)
    local poll=1
    
    while [ $poll -le $max_polls ]; do
        local status_response
        status_response=$(curl -sf "${DEPLOY_API_URL}/deploy/${deployment_id}/status" \
            -H "Authorization: Bearer ${token}" 2>&1) || {
            log_info "Status check failed, retrying..."
            sleep 5
            poll=$((poll + 1))
            continue
        }
        
        local status
        status=$(echo "$status_response" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
        
        case "$status" in
            completed|success)
                log_success "=== ${app_name} deployed successfully ==="
                return 0
                ;;
            failed|error)
                local error_msg
                error_msg=$(echo "$status_response" | grep -o '"error":"[^"]*"' | cut -d'"' -f4)
                log_error "Deployment failed: ${error_msg:-unknown error}"
                return 1
                ;;
            *)
                log_info "Deployment status: ${status} (poll ${poll}/${max_polls})"
                sleep 5
                poll=$((poll + 1))
                ;;
        esac
    done
    
    log_error "Deployment timed out"
    return 1
}

# =============================================================================
# Fallback: Direct GitHub Deploy (if Deploy API unavailable)
# =============================================================================
deploy_app_direct() {
    local app_name="$1"
    local github_ref="${2:-main}"
    local app_dir="/srv/${app_name}"
    local github_repo="jazzmind/${app_name}"
    
    log_info "=== Deploying ${app_name} (ref: ${github_ref}) directly from GitHub ==="
    
    # Validate GitHub token
    if [ -z "${GITHUB_AUTH_TOKEN:-}" ]; then
        log_error "GITHUB_AUTH_TOKEN is required for deployment"
        return 1
    fi
    
    # Stop the app if it's running
    log_info "Stopping ${app_name} if running..."
    supervisorctl stop "${app_name}" 2>/dev/null || true
    
    # Clone to temp directory first
    local temp_dir="/tmp/${app_name}-clone"
    rm -rf "${temp_dir}"
    
    log_info "Cloning ${github_repo} (ref: ${github_ref})..."
    if ! git clone --depth 1 --branch "${github_ref}" \
        "https://${GITHUB_AUTH_TOKEN}@github.com/${github_repo}.git" \
        "${temp_dir}" 2>&1; then
        log_error "Failed to clone ${github_repo}"
        return 1
    fi
    
    # Clear existing app directory (except .env if present)
    log_info "Preparing ${app_dir}..."
    if [ -f "${app_dir}/.env" ]; then
        cp "${app_dir}/.env" /tmp/.env.backup
    fi
    rm -rf "${app_dir:?}"/*
    cp -r "${temp_dir}/." "${app_dir}/"
    rm -rf "${temp_dir}"
    
    # Restore .env if it existed
    if [ -f /tmp/.env.backup ]; then
        mv /tmp/.env.backup "${app_dir}/.env"
    fi
    
    # Copy npmrc for GitHub packages
    if [ -f /root/.npmrc ]; then
        cp /root/.npmrc "${app_dir}/.npmrc"
    fi
    
    # Install dependencies
    log_info "Installing dependencies..."
    cd "${app_dir}"
    
    if ! npm ci 2>&1; then
        log_error "npm ci failed, trying npm install..."
        npm install 2>&1 || {
            log_error "npm install failed"
            return 1
        }
    fi
    
    # Run prisma generate if needed
    if [ -f "prisma/schema.prisma" ]; then
        log_info "Generating Prisma client..."
        npx prisma generate 2>&1 || true
    fi
    
    # Set build-time environment variables based on app
    log_info "Building application..."
    case "${app_name}" in
        ai-portal)
            export NEXT_PUBLIC_BASE_PATH=/portal
            export DATABASE_URL="${DATABASE_URL:-postgresql://dummy:dummy@localhost:5432/dummy}"
            ;;
        agent-manager)
            export NEXT_PUBLIC_BASE_PATH=/agents
            export NEXT_PUBLIC_AI_PORTAL_URL="${NEXT_PUBLIC_AI_PORTAL_URL:-https://localhost/portal}"
            ;;
    esac
    
    if ! npm run build 2>&1; then
        log_error "Build failed"
        return 1
    fi
    
    log_info "Build complete"
    
    # Run database migrations for ai-portal
    if [ "${app_name}" = "ai-portal" ] && [ -d "prisma" ]; then
        log_info "Running database migrations..."
        if [ -n "${DATABASE_URL:-}" ] && [ "${DATABASE_URL}" != "postgresql://dummy:dummy@localhost:5432/dummy" ]; then
            npx prisma db push --accept-data-loss 2>&1 || {
                log_error "prisma db push failed, continuing anyway..."
            }
        else
            log_info "Skipping migrations - DATABASE_URL not configured"
        fi
    fi
    
    # Start the app via supervisord
    log_info "Starting ${app_name}..."
    supervisorctl start "${app_name}" 2>&1 || {
        log_error "Failed to start ${app_name}"
        return 1
    }
    
    log_success "=== ${app_name} deployed successfully ==="
    return 0
}

# =============================================================================
# Check if App is Deployed
# =============================================================================
is_app_deployed() {
    local app_name="$1"
    local app_dir="/srv/${app_name}"
    
    # Check if package.json exists with built output (.next directory)
    if [ -f "${app_dir}/package.json" ] && [ -d "${app_dir}/.next" ]; then
        return 0
    fi
    
    return 1
}

# =============================================================================
# Start App and Check if Running
# =============================================================================
start_app() {
    local app_name="$1"
    
    log_info "Starting ${app_name}..."
    supervisorctl start "${app_name}" 2>&1 || true
    
    # Wait a bit for it to start
    sleep 5
    
    # Check if it's running
    local status
    status=$(supervisorctl status "${app_name}" 2>/dev/null | awk '{print $2}')
    
    if [ "$status" = "RUNNING" ]; then
        log_success "${app_name} started successfully"
        return 0
    else
        log_error "${app_name} failed to start (status: ${status})"
        return 1
    fi
}

# =============================================================================
# Deploy if Not Present or Failed to Start
# =============================================================================
deploy_if_needed() {
    local app_name="$1"
    local github_ref="$2"
    local token="$3"
    
    if is_app_deployed "${app_name}"; then
        log_info "${app_name} already deployed, starting..."
        
        if start_app "${app_name}"; then
            return 0
        fi
        
        # App failed to start - force redeploy
        log_info "${app_name} failed to start, forcing redeploy..."
        rm -rf "/srv/${app_name}/.next" "/srv/${app_name}/node_modules"
    else
        log_info "${app_name} not found, deploying..."
    fi
    
    # Try Deploy API first, fallback to direct if unavailable
    if [ -n "$token" ]; then
        if deploy_app_via_api "${app_name}" "${github_ref}" "$token"; then
            return 0
        fi
        log_info "Deploy API failed, falling back to direct deployment..."
    fi
    
    # Fallback to direct deployment
    deploy_app_direct "${app_name}" "${github_ref}"
}

# =============================================================================
# Main Entry Point
# =============================================================================
case "${1:-start}" in
    start)
        log_info "Starting Core Apps (runtime mode)..."
        
        # Generate SSL certificates
        generate_ssl_cert
        
        # Setup npm authentication
        setup_npm_auth
        
        # Start supervisord in foreground
        # This starts nginx immediately (autostart=true)
        # Apps are autostart=false, we start them after deployment check
        log_info "Starting supervisord..."
        supervisord -c /etc/supervisor/conf.d/supervisord.conf &
        SUPERVISOR_PID=$!
        
        # Wait for supervisor to be ready
        sleep 3
        
        # Try to get bootstrap token from AuthZ via Deploy API
        BOOTSTRAP_TOKEN=""
        
        log_info "Attempting to use Deploy API for app deployment..."
        if wait_for_deploy_api; then
            BOOTSTRAP_TOKEN=$(get_bootstrap_token) || {
                log_info "Could not get bootstrap token, will use direct deployment"
                BOOTSTRAP_TOKEN=""
            }
        else
            log_info "Deploy API not available, will use direct deployment"
        fi
        
        # Check and deploy apps if needed
        log_info "Checking app deployments..."
        deploy_if_needed "ai-portal" "${AI_PORTAL_GITHUB_REF}" "${BOOTSTRAP_TOKEN}"
        deploy_if_needed "agent-manager" "${AGENT_MANAGER_GITHUB_REF}" "${BOOTSTRAP_TOKEN}"
        
        log_success "Core apps started"
        
        # Wait for supervisor to exit (keeps container running)
        wait $SUPERVISOR_PID
        ;;
        
    deploy)
        # Manual deployment: entrypoint.sh deploy <app> [ref]
        APP_NAME="${2:-}"
        GITHUB_REF="${3:-main}"
        
        if [ -z "${APP_NAME}" ]; then
            log_error "Usage: entrypoint.sh deploy <app-name> [git-ref]"
            log_error "  app-name: ai-portal, agent-manager"
            log_error "  git-ref: branch or tag (default: main)"
            exit 1
        fi
        
        # Setup npm auth first
        setup_npm_auth
        
        # Try to get bootstrap token
        BOOTSTRAP_TOKEN=""
        if wait_for_deploy_api; then
            BOOTSTRAP_TOKEN=$(get_bootstrap_token) || BOOTSTRAP_TOKEN=""
        fi
        
        if [ -n "$BOOTSTRAP_TOKEN" ]; then
            deploy_app_via_api "${APP_NAME}" "${GITHUB_REF}" "${BOOTSTRAP_TOKEN}"
        else
            deploy_app_direct "${APP_NAME}" "${GITHUB_REF}"
        fi
        ;;
        
    nginx-reload)
        log_info "Testing nginx configuration..."
        if nginx -t; then
            log_info "Reloading nginx..."
            nginx -s reload
            log_success "Nginx reloaded"
        else
            log_error "Nginx configuration test failed"
            exit 1
        fi
        ;;
        
    nginx-test)
        nginx -t
        ;;
        
    status)
        supervisorctl status
        ;;
        
    bash|sh)
        exec /bin/bash
        ;;
        
    *)
        # Pass through to exec
        exec "$@"
        ;;
esac
