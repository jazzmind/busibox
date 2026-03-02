#!/bin/bash
# =============================================================================
# Core Apps Runtime Entrypoint Script (Monorepo)
# =============================================================================
#
# Clones busibox-frontend monorepo, builds enabled apps, runs via supervisord.
#
# Modes:
#   start   - Normal startup (default)
#   deploy  - Rebuild a specific app: entrypoint.sh deploy <app-name>
#   bash    - Start bash shell for debugging
#
# Environment variables:
#   GITHUB_AUTH_TOKEN - Required for cloning private repos and npm packages
#   BUSIBOX_FRONTEND_GITHUB_REF - Git ref for busibox-frontend (default: main)
#   ENABLED_APPS - Comma-separated app names (default: portal,admin)
#   DATABASE_URL - PostgreSQL connection string (required for portal)
#
# =============================================================================

set -euo pipefail

BUSIBOX_FRONTEND_GITHUB_REF="${BUSIBOX_FRONTEND_GITHUB_REF:-main}"
MONOREPO_DIR="/srv/busibox-frontend"

# App definitions: short_name -> pnpm filter, base path, port
declare -A APP_FILTERS=(
    [portal]="@busibox/portal"
    [agents]="@busibox/agents"
    [admin]="@busibox/admin"
    [chat]="@busibox/chat"
    [appbuilder]="@busibox/appbuilder"
    [media]="@busibox/media"
    [documents]="@busibox/documents"
)

declare -A APP_DIRS=(
    [portal]="apps/portal"
    [agents]="apps/agents"
    [admin]="apps/admin"
    [chat]="apps/chat"
    [appbuilder]="apps/appbuilder"
    [media]="apps/media"
    [documents]="apps/documents"
)

declare -A APP_BASE_PATHS=(
    [portal]="/portal"
    [agents]="/agents"
    [admin]="/admin"
    [chat]="/chat"
    [appbuilder]="/builder"
    [media]="/media"
    [documents]="/documents"
)

# Logging functions
log_info() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] INFO: $*"; }
log_error() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; }
log_success() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] SUCCESS: $*"; }

# =============================================================================
# NPM/pnpm Authentication Setup
# =============================================================================
setup_npm_auth() {
    if [ -n "${GITHUB_AUTH_TOKEN:-}" ]; then
        log_info "Setting up npm authentication for @jazzmind packages..."
        echo "//npm.pkg.github.com/:_authToken=${GITHUB_AUTH_TOKEN}" > /root/.npmrc
        echo "@jazzmind:registry=https://npm.pkg.github.com" >> /root/.npmrc
        log_success "npm authentication configured"
    else
        log_error "GITHUB_AUTH_TOKEN not set - npm install may fail for @jazzmind packages"
    fi
}

# =============================================================================
# Clone Monorepo
# =============================================================================
clone_monorepo() {
    if [ -z "${GITHUB_AUTH_TOKEN:-}" ]; then
        log_error "GITHUB_AUTH_TOKEN is required for cloning"
        return 1
    fi

    log_info "Cloning busibox-frontend (ref: ${BUSIBOX_FRONTEND_GITHUB_REF})..."

    local temp_dir="/tmp/busibox-frontend-clone"
    rm -rf "${temp_dir}"

    if ! git clone --depth 1 --branch "${BUSIBOX_FRONTEND_GITHUB_REF}" \
        "https://${GITHUB_AUTH_TOKEN}@github.com/jazzmind/busibox-frontend.git" \
        "${temp_dir}" 2>&1; then
        log_error "Failed to clone busibox-frontend"
        return 1
    fi

    # Move to target directory
    rm -rf "${MONOREPO_DIR:?}"/*
    cp -r "${temp_dir}/." "${MONOREPO_DIR}/"
    rm -rf "${temp_dir}"

    log_success "Monorepo cloned successfully"
}

# =============================================================================
# Update Monorepo (git pull)
# =============================================================================
update_monorepo() {
    if [ -z "${GITHUB_AUTH_TOKEN:-}" ]; then
        log_error "GITHUB_AUTH_TOKEN is required for updating"
        return 1
    fi

    local target_ref="${BUSIBOX_FRONTEND_GITHUB_REF:-main}"

    cd "${MONOREPO_DIR}"

    # Get current commit before pull
    local before_commit
    before_commit=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

    log_info "Updating busibox-frontend to ${target_ref} (current: ${before_commit:0:8})..."

    # Configure git auth for pull
    git remote set-url origin "https://${GITHUB_AUTH_TOKEN}@github.com/jazzmind/busibox-frontend.git" 2>/dev/null || true

    # Fetch and reset to the target ref
    # We use fetch+reset instead of pull because the volume may have local changes
    if ! git fetch origin "${target_ref}" --depth 1 2>&1; then
        log_error "Failed to fetch ${target_ref}"
        return 1
    fi

    git reset --hard "origin/${target_ref}" 2>&1 || git reset --hard FETCH_HEAD 2>&1

    local after_commit
    after_commit=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

    if [ "${before_commit}" = "${after_commit}" ]; then
        log_info "Already up to date (${after_commit:0:8})"
        return 1  # Return 1 to indicate no changes (not an error)
    else
        log_success "Updated from ${before_commit:0:8} to ${after_commit:0:8}"
        return 0  # Return 0 to indicate changes were pulled
    fi
}

# =============================================================================
# Install Dependencies
# =============================================================================
install_deps() {
    log_info "Installing dependencies..."
    cd "${MONOREPO_DIR}"

    # Clean stale caches
    rm -rf node_modules/.cache 2>/dev/null || true

    pnpm install --no-frozen-lockfile 2>&1

    log_success "Dependencies installed"
}

# =============================================================================
# Build Shared Package
# =============================================================================
build_shared() {
    log_info "Building shared package (@jazzmind/busibox-app)..."
    cd "${MONOREPO_DIR}"
    pnpm --filter @jazzmind/busibox-app build 2>&1
    log_success "Shared package built"
}

# =============================================================================
# Build Single App
# =============================================================================
build_app() {
    local app_name="$1"
    local filter="${APP_FILTERS[$app_name]:-}"
    local app_dir="${APP_DIRS[$app_name]:-}"
    local base_path="${APP_BASE_PATHS[$app_name]:-}"

    if [ -z "$filter" ] || [ -z "$app_dir" ]; then
        log_error "Unknown app: ${app_name}"
        return 1
    fi

    local full_app_dir="${MONOREPO_DIR}/${app_dir}"
    local srv_dir="/srv/busibox-${app_name}"

    log_info "Building ${app_name} (${filter})..."
    cd "${MONOREPO_DIR}"

    # Set build-time env vars
    export NEXT_PUBLIC_BASE_PATH="${base_path}"

    # App-specific env vars
    case "${app_name}" in
        portal)
            export DATABASE_URL="${DATABASE_URL:-postgresql://dummy:dummy@localhost:5432/dummy}"
            ;;
        agents)
            export NEXT_PUBLIC_BUSIBOX_PORTAL_URL="${NEXT_PUBLIC_BUSIBOX_PORTAL_URL:-https://localhost/portal}"
            export DEFAULT_API_AUDIENCE="agent-api"
            ;;
        appbuilder)
            export APP_NAME="busibox-appbuilder"
            export NEXT_PUBLIC_APP_URL="${NEXT_PUBLIC_APP_URL:-https://localhost/builder}"
            export NEXT_PUBLIC_BUSIBOX_PORTAL_URL="${NEXT_PUBLIC_BUSIBOX_PORTAL_URL:-https://localhost/portal}"
            ;;
    esac

    # Build the app
    if ! pnpm --filter "${filter}" build 2>&1; then
        log_error "Build failed for ${app_name}"
        return 1
    fi

    # Copy standalone output to /srv/busibox-{app}/ preserving monorepo structure
    # Next.js standalone in a monorepo expects:
    #   <srv_dir>/.next/standalone/apps/{app}/server.js  (the server)
    #   <srv_dir>/.next/standalone/apps/{app}/public/     (public assets)
    #   <srv_dir>/.next/standalone/apps/{app}/.next/static/ (static chunks)
    log_info "Copying standalone build to ${srv_dir}..."
    mkdir -p "${srv_dir}/.next"

    if [ -d "${full_app_dir}/.next/standalone" ]; then
        cp -r "${full_app_dir}/.next/standalone" "${srv_dir}/.next/"

        # Copy public and static assets into the standalone app directory
        # where server.js expects them
        local standalone_app_dir="${srv_dir}/.next/standalone/apps/${app_name}"
        if [ -d "${full_app_dir}/public" ]; then
            cp -r "${full_app_dir}/public" "${standalone_app_dir}/public"
        fi
        if [ -d "${full_app_dir}/.next/static" ]; then
            mkdir -p "${standalone_app_dir}/.next"
            cp -r "${full_app_dir}/.next/static" "${standalone_app_dir}/.next/static"
        fi
    else
        log_error "No standalone output found for ${app_name}"
        return 1
    fi

    log_success "${app_name} built and deployed to ${srv_dir}"
}

# =============================================================================
# Check if Monorepo is Cloned
# =============================================================================
is_monorepo_present() {
    [ -f "${MONOREPO_DIR}/pnpm-workspace.yaml" ]
}

# =============================================================================
# Check if App is Built
# =============================================================================
is_app_built() {
    local app_name="$1"
    local srv_dir="/srv/busibox-${app_name}"
    [ -f "${srv_dir}/.next/standalone/apps/${app_name}/server.js" ]
}

# =============================================================================
# Run Database Migrations
# =============================================================================
run_migrations() {
    if [ -n "${DATABASE_URL:-}" ] && [ "${DATABASE_URL}" != "postgresql://dummy:dummy@localhost:5432/dummy" ]; then
        local portal_dir="${MONOREPO_DIR}/apps/portal"
        if [ -d "${portal_dir}/prisma" ]; then
            log_info "Running database migrations..."
            cd "${portal_dir}"
            npx prisma db push 2>&1 || {
                log_error "prisma db push failed, continuing anyway..."
            }
        fi
    fi
}

# =============================================================================
# Main Entry Point
# =============================================================================
case "${1:-start}" in
    start)
        log_info "Starting Core Apps (monorepo runtime mode)..."

        setup_npm_auth

        # Start supervisord in background
        log_info "Starting supervisord..."
        supervisord -c /etc/supervisor/supervisord.conf &
        SUPERVISOR_PID=$!
        sleep 3

        # Determine enabled apps
        ENABLED="${ENABLED_APPS:-portal,admin}"
        log_info "ENABLED_APPS: ${ENABLED}"

        is_app_enabled() {
            local short_name="$1"
            if [ "${ENABLED}" = "all" ] || [ -z "${ENABLED}" ]; then
                return 0
            fi
            echo ",${ENABLED}," | grep -q ",${short_name},"
        }

        # Clone and install if not present
        if ! is_monorepo_present; then
            clone_monorepo
            install_deps
            build_shared

            # Build all enabled apps
            for app_name in portal agents admin chat appbuilder media documents; do
                if is_app_enabled "${app_name}"; then
                    build_app "${app_name}" || log_error "Failed to build ${app_name}, continuing..."
                fi
            done

            # Run portal migrations if portal was built
            if is_app_enabled "portal"; then
                run_migrations
            fi
        else
            log_info "Monorepo already present, checking for updates..."

            # Try to pull latest code
            code_updated=false
            if update_monorepo; then
                code_updated=true
                log_info "Code updated, will rebuild all enabled apps"
            fi

            # Ensure dependencies are installed (might be missing if volume only has source)
            if [ ! -d "${MONOREPO_DIR}/node_modules" ] || [ "$code_updated" = true ]; then
                install_deps
            fi

            # Ensure shared package is built
            if [ ! -d "${MONOREPO_DIR}/packages/app/dist" ] || [ "$code_updated" = true ]; then
                build_shared
            fi

            # Build any enabled apps that aren't built yet OR need rebuild due to code update
            needs_build=false
            for app_name in portal agents admin chat appbuilder media documents; do
                if is_app_enabled "${app_name}"; then
                    if [ "$code_updated" = true ] || ! is_app_built "${app_name}"; then
                        log_info "Building ${app_name}..."
                        build_app "${app_name}" || log_error "Failed to build ${app_name}, continuing..."
                        needs_build=true
                    fi
                fi
            done

            # Run portal migrations if portal was just built
            if [ "$needs_build" = true ] && is_app_enabled "portal"; then
                run_migrations
            fi
        fi

        # Start enabled apps via supervisord
        for app_name in portal agents admin chat appbuilder media documents; do
            if is_app_enabled "${app_name}" && is_app_built "${app_name}"; then
                log_info "Starting busibox-${app_name}..."
                supervisorctl start "busibox-${app_name}" 2>&1 || true
            fi
        done

        log_success "Core apps started"

        # Wait for supervisor
        wait $SUPERVISOR_PID
        ;;

    deploy)
        # Rebuild a specific app: entrypoint.sh deploy <app-name>
        APP_NAME="${2:-}"

        if [ -z "${APP_NAME}" ]; then
            log_error "Usage: entrypoint.sh deploy <app-name>"
            log_error "  app-name: portal, agents, admin, chat, appbuilder, media, documents"
            exit 1
        fi

        # Strip "busibox-" prefix if provided
        APP_NAME="${APP_NAME#busibox-}"

        setup_npm_auth

        if ! is_monorepo_present; then
            clone_monorepo
            install_deps
            build_shared
        fi

        # Stop the app
        supervisorctl stop "busibox-${APP_NAME}" 2>/dev/null || true

        # Rebuild
        build_app "${APP_NAME}"

        # Run migrations if portal
        if [ "${APP_NAME}" = "portal" ]; then
            run_migrations
        fi

        # Restart
        supervisorctl start "busibox-${APP_NAME}" 2>&1 || true

        log_success "${APP_NAME} redeployed"
        ;;

    status)
        supervisorctl status
        ;;

    bash|sh)
        exec /bin/bash
        ;;

    *)
        exec "$@"
        ;;
esac
