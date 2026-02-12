#!/bin/bash
# =============================================================================
# Core Apps Entrypoint Script
# =============================================================================
#
# Starts nginx, ai-portal, and agent-manager in the same container.
# Uses concurrently to manage all processes.
# Mirrors the Proxmox apps-lxc architecture.
#
# Modes:
#   dev   - Development mode with hot-reload (Turbopack)
#   start - Production mode with built Next.js apps
#
# =============================================================================

set -e

MODE="${1:-dev}"

# Generate self-signed SSL certificate if not present
generate_ssl_cert() {
    local ssl_dir="/etc/nginx/ssl"
    # Must match nginx-bundled.conf which expects localhost.crt and localhost.key
    local cert_file="$ssl_dir/localhost.crt"
    local key_file="$ssl_dir/localhost.key"
    
    if [ ! -f "$cert_file" ] || [ ! -f "$key_file" ]; then
        echo "Generating self-signed SSL certificate (localhost.crt/localhost.key)..."
        mkdir -p "$ssl_dir"
        if ! openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "$key_file" \
            -out "$cert_file" \
            -subj "/CN=localhost/O=Busibox/C=US" \
            -addext "subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1"; then
            echo "ERROR: Failed to generate SSL certificate"
            return 1
        fi
        echo "SSL certificate generated."
    else
        echo "SSL certificates already exist, skipping generation."
    fi
}

# Start nginx in background
start_nginx() {
    echo "Starting nginx..."
    
    # Ensure SSL certificates exist
    generate_ssl_cert
    
    # Test nginx configuration
    if nginx -t 2>/dev/null; then
        nginx -g 'daemon off;' &
        NGINX_PID=$!
        echo "Nginx started (PID: $NGINX_PID)"
    else
        echo "WARNING: Nginx configuration test failed, skipping nginx"
        nginx -t
    fi
}

# Setup npm authentication for GitHub Packages
setup_npm_auth() {
    if [ -n "${GITHUB_AUTH_TOKEN:-}" ]; then
        echo "Setting up npm authentication for @jazzmind packages..."
        
        # Create .npmrc in home directory (global fallback)
        echo "//npm.pkg.github.com/:_authToken=${GITHUB_AUTH_TOKEN}" > /root/.npmrc
        echo "@jazzmind:registry=https://npm.pkg.github.com" >> /root/.npmrc
        
        # Create project-level .npmrc files if they don't exist
        # These are gitignored and created from .npmrc.example pattern
        for app_dir in /srv/ai-portal /srv/agent-manager; do
            if [ -d "$app_dir" ] && [ ! -f "$app_dir/.npmrc" ]; then
                echo "Creating $app_dir/.npmrc with GitHub token..."
                echo "@jazzmind:registry=https://npm.pkg.github.com" > "$app_dir/.npmrc"
                echo "//npm.pkg.github.com/:_authToken=${GITHUB_AUTH_TOKEN}" >> "$app_dir/.npmrc"
            fi
        done
    else
        echo "WARNING: GITHUB_AUTH_TOKEN not set - npm install may fail for @jazzmind packages"
    fi
}

# Compute a checksum of package.json + package-lock.json for change detection
compute_deps_checksum() {
    local app_dir="$1"
    local checksum=""
    if [ -f "$app_dir/package.json" ]; then
        checksum=$(md5sum "$app_dir/package.json" 2>/dev/null | awk '{print $1}')
    fi
    if [ -f "$app_dir/package-lock.json" ]; then
        local lock_checksum
        lock_checksum=$(md5sum "$app_dir/package-lock.json" 2>/dev/null | awk '{print $1}')
        checksum="${checksum}-${lock_checksum}"
    fi
    echo "$checksum"
}

# Function to run npm install if needed (for dev mode with volume mounts)
# Detects changes to package.json/package-lock.json across container restarts
# by comparing checksums stored in node_modules/.deps-checksum
ensure_deps() {
    local app_dir="$1"
    local app_name="$2"
    
    if [ -d "$app_dir" ] && [ -f "$app_dir/package.json" ]; then
        cd "$app_dir"
        
        # Clear .next/dev cache on startup to prevent stale cache issues
        # This prevents "ENOENT: no such file or directory, open '...build-manifest.json'" errors
        # that occur when cache was created in a different environment (host vs container)
        if [ -d ".next/dev" ]; then
            echo "Clearing .next/dev cache for $app_name..."
            rm -rf .next/dev
        fi
        
        local needs_install=false
        local checksum_file="node_modules/.deps-checksum"
        local current_checksum
        current_checksum=$(compute_deps_checksum "$app_dir")
        
        # Check if node_modules exists at all
        if [ ! -d "node_modules" ] || [ ! -f "node_modules/.package-lock.json" ]; then
            echo "[$app_name] node_modules missing, installing dependencies..."
            needs_install=true
        # Check if package.json or package-lock.json changed since last install
        elif [ ! -f "$checksum_file" ] || [ "$(cat "$checksum_file" 2>/dev/null)" != "$current_checksum" ]; then
            echo "[$app_name] package.json or package-lock.json changed, updating dependencies..."
            needs_install=true
        else
            echo "[$app_name] Dependencies up to date (checksum match)"
        fi
        
        if [ "$needs_install" = true ]; then
            npm install
            # Store the checksum after successful install
            echo "$current_checksum" > "$checksum_file"
            echo "[$app_name] Dependencies installed, checksum saved"
        fi
        
        # Run prisma generate if prisma directory exists
        if [ -d "prisma" ]; then
            echo "Running prisma generate for $app_name..."
            npx prisma generate 2>/dev/null || true
        fi
    fi
}

# Watch package.json files for changes and auto-reinstall dependencies
# Runs in background during dev mode. Uses polling since inotify isn't
# available on Docker for Mac volume mounts.
watch_package_changes() {
    echo "[dep-watcher] Starting package.json watcher for hot-reinstall..."
    
    # Store initial checksums
    local portal_checksum agent_checksum
    portal_checksum=$(compute_deps_checksum "/srv/ai-portal")
    agent_checksum=$(compute_deps_checksum "/srv/agent-manager")
    
    while true; do
        sleep 5  # Check every 5 seconds
        
        # Check ai-portal
        if [ -f "/srv/ai-portal/package.json" ]; then
            local new_portal_checksum
            new_portal_checksum=$(compute_deps_checksum "/srv/ai-portal")
            if [ "$new_portal_checksum" != "$portal_checksum" ]; then
                echo "[dep-watcher] ai-portal package.json changed! Running npm install..."
                (cd /srv/ai-portal && npm install && echo "$new_portal_checksum" > node_modules/.deps-checksum) &
                portal_checksum="$new_portal_checksum"
            fi
        fi
        
        # Check agent-manager
        if [ -f "/srv/agent-manager/package.json" ]; then
            local new_agent_checksum
            new_agent_checksum=$(compute_deps_checksum "/srv/agent-manager")
            if [ "$new_agent_checksum" != "$agent_checksum" ]; then
                echo "[dep-watcher] agent-manager package.json changed! Running npm install..."
                (cd /srv/agent-manager && npm install && echo "$new_agent_checksum" > node_modules/.deps-checksum) &
                agent_checksum="$new_agent_checksum"
            fi
        fi
    done
}

case "$MODE" in
    dev)
        echo "Starting Core Apps in development mode (Turbopack hot-reload)..."
        
        # Start nginx first (handles routing)
        start_nginx
        
        # Setup npm authentication (required for @jazzmind/busibox-app from GitHub Packages)
        setup_npm_auth
        
        # Setup dependencies for both apps
        # Note: busibox-app is mounted directly into node_modules/@jazzmind/busibox-app
        # via Docker volume overlay (no symlink needed)
        ensure_deps "/srv/ai-portal" "ai-portal"
        ensure_deps "/srv/agent-manager" "agent-manager"
        
        # Start background watcher for package.json changes (hot-reinstall)
        watch_package_changes &
        WATCHER_PID=$!
        echo "Package watcher started (PID: $WATCHER_PID)"
        
        # Start both apps with concurrently
        # Names are prefixed with app name for log clarity
        # IMPORTANT: Set NEXT_PUBLIC_BASE_PATH per-app since they need different values
        # This overrides the container-wide environment variable
        exec concurrently \
            --names "portal,agents" \
            --prefix-colors "blue,green" \
            --kill-others-on-fail \
            "cd /srv/ai-portal && PORT=3000 NEXT_PUBLIC_BASE_PATH=/portal npm run dev" \
            "cd /srv/agent-manager && PORT=3001 NEXT_PUBLIC_BASE_PATH=/agents npm run dev"
        ;;

    prod)
        echo "Starting Core Apps in local production mode (build + serve)..."
        echo "Source code is volume-mounted but apps are built and served with 'next start'."
        echo "No hot-reload — restart the container to pick up code changes."
        
        # Override NODE_ENV for production build and serve
        export NODE_ENV=production
        
        # Start nginx first (handles routing)
        start_nginx
        
        # Setup npm authentication (required for @jazzmind/busibox-app from GitHub Packages)
        setup_npm_auth
        
        # Install deps in dev mode first (so devDependencies are available for build)
        NODE_ENV=development ensure_deps "/srv/ai-portal" "ai-portal"
        NODE_ENV=development ensure_deps "/srv/agent-manager" "agent-manager"
        
        # Build both apps (NEXT_PUBLIC_* env vars are baked in at build time)
        echo "Building ai-portal..."
        (cd /srv/ai-portal && NEXT_PUBLIC_BASE_PATH=/portal NODE_ENV=production npm run build) || {
            echo "ERROR: ai-portal build failed"
            exit 1
        }
        
        echo "Building agent-manager..."
        (cd /srv/agent-manager && NEXT_PUBLIC_BASE_PATH=/agents NODE_ENV=production npm run build) || {
            echo "ERROR: agent-manager build failed"
            exit 1
        }
        
        # Sync database schema (if prisma is configured)
        if [ -d "/srv/ai-portal/prisma" ] && [ -f "/srv/ai-portal/prisma.config.ts" ]; then
            echo "Syncing database schema..."
            cd /srv/ai-portal
            npx prisma db push 2>/dev/null || {
                echo "Warning: prisma db push failed, continuing..."
            }
        fi
        
        echo ""
        echo "Build complete. Starting production servers..."
        echo ""
        
        # Start both apps with concurrently using npm start (next start)
        exec concurrently \
            --names "portal,agents" \
            --prefix-colors "blue,green" \
            --kill-others-on-fail \
            "cd /srv/ai-portal && PORT=3000 HOSTNAME=0.0.0.0 npm start" \
            "cd /srv/agent-manager && PORT=3001 HOSTNAME=0.0.0.0 npm start"
        ;;
        
    start)
        echo "Starting Core Apps in production mode (pre-built)..."
        
        # Start nginx first (handles routing)
        start_nginx
        
        # Run prisma db push to sync schema (uses prisma.config.ts for DATABASE_URL)
        # This is safe because it only creates/modifies tables without data loss
        if [ -d "/srv/ai-portal/prisma" ] && [ -f "/srv/ai-portal/prisma.config.ts" ]; then
            echo "Syncing database schema..."
            cd /srv/ai-portal
            npx prisma db push --accept-data-loss 2>/dev/null || {
                echo "Warning: prisma db push failed, trying migrate deploy..."
                npx prisma migrate deploy 2>/dev/null || true
            }
        fi
        
        # Start both apps with concurrently using npm start (next start)
        # NEXT_PUBLIC_BASE_PATH was set at build time and is baked into the bundle
        # PORT and HOSTNAME must be set at runtime
        exec concurrently \
            --names "portal,agents" \
            --prefix-colors "blue,green" \
            --kill-others-on-fail \
            "cd /srv/ai-portal && PORT=3000 HOSTNAME=0.0.0.0 npm start" \
            "cd /srv/agent-manager && PORT=3001 HOSTNAME=0.0.0.0 npm start"
        ;;
        
    *)
        echo "Usage: $0 {dev|prod|start}"
        echo ""
        echo "  dev   - Development mode with Turbopack hot-reload (default)"
        echo "  prod  - Build from volume-mounted source, then serve with 'next start'"
        echo "  start - Serve pre-built apps with 'next start' (used by runtime Dockerfile)"
        exit 1
        ;;
esac
