#!/bin/bash
# =============================================================================
# Core Apps Entrypoint Script
# =============================================================================
#
# Starts busibox-portal and busibox-agents in the same container.
# Uses concurrently to manage both processes.
# Mirrors the Proxmox apps-lxc architecture.
#
# Modes:
#   dev   - Development mode with hot-reload (Turbopack)
#   start - Production mode with built Next.js apps
#
# =============================================================================

set -e

MODE="${1:-dev}"

# Setup npm authentication for GitHub Packages
setup_npm_auth() {
    if [ -n "${GITHUB_AUTH_TOKEN:-}" ]; then
        echo "Setting up npm authentication for @jazzmind packages..."
        
        # Create .npmrc in home directory (global fallback)
        echo "//npm.pkg.github.com/:_authToken=${GITHUB_AUTH_TOKEN}" > /root/.npmrc
        echo "@jazzmind:registry=https://npm.pkg.github.com" >> /root/.npmrc
        
        # Create project-level .npmrc files if they don't exist
        # These are gitignored and created from .npmrc.example pattern
        for app_dir in /srv/busibox-portal /srv/busibox-agents; do
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
    portal_checksum=$(compute_deps_checksum "/srv/busibox-portal")
    agent_checksum=$(compute_deps_checksum "/srv/busibox-agents")
    
    while true; do
        sleep 5  # Check every 5 seconds
        
        # Check busibox-portal
        if [ -f "/srv/busibox-portal/package.json" ]; then
            local new_portal_checksum
            new_portal_checksum=$(compute_deps_checksum "/srv/busibox-portal")
            if [ "$new_portal_checksum" != "$portal_checksum" ]; then
                echo "[dep-watcher] busibox-portal package.json changed! Running npm install..."
                (cd /srv/busibox-portal && npm install && echo "$new_portal_checksum" > node_modules/.deps-checksum) &
                portal_checksum="$new_portal_checksum"
            fi
        fi
        
        # Check busibox-agents
        if [ -f "/srv/busibox-agents/package.json" ]; then
            local new_agent_checksum
            new_agent_checksum=$(compute_deps_checksum "/srv/busibox-agents")
            if [ "$new_agent_checksum" != "$agent_checksum" ]; then
                echo "[dep-watcher] busibox-agents package.json changed! Running npm install..."
                (cd /srv/busibox-agents && npm install && echo "$new_agent_checksum" > node_modules/.deps-checksum) &
                agent_checksum="$new_agent_checksum"
            fi
        fi
    done
}

case "$MODE" in
    dev)
        echo "Starting Core Apps in development mode (Turbopack hot-reload)..."
        
        # Setup npm authentication (required for @jazzmind/busibox-app from GitHub Packages)
        setup_npm_auth
        
        # Setup dependencies for both apps
        # Note: busibox-app is mounted directly into node_modules/@jazzmind/busibox-app
        # via Docker volume overlay (no symlink needed)
        ensure_deps "/srv/busibox-portal" "busibox-portal"
        ensure_deps "/srv/busibox-agents" "busibox-agents"
        
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
            "cd /srv/busibox-portal && PORT=3000 NEXT_PUBLIC_BASE_PATH=/portal npm run dev" \
            "cd /srv/busibox-agents && PORT=3001 NEXT_PUBLIC_BASE_PATH=/agents npm run dev"
        ;;

    prod)
        echo "Starting Core Apps in local production mode (build + serve)..."
        echo "Source code is volume-mounted but apps are built and served with standalone server."
        echo "No hot-reload — restart the container to pick up code changes."
        
        # Override NODE_ENV for production build and serve
        export NODE_ENV=production
        
        # Setup npm authentication (required for @jazzmind/busibox-app from GitHub Packages)
        setup_npm_auth
        
        # Install deps in dev mode first (so devDependencies are available for build)
        NODE_ENV=development ensure_deps "/srv/busibox-portal" "busibox-portal"
        NODE_ENV=development ensure_deps "/srv/busibox-agents" "busibox-agents"
        
        # Build both apps (NEXT_PUBLIC_* env vars are baked in at build time)
        echo "Building busibox-portal..."
        (cd /srv/busibox-portal && NEXT_PUBLIC_BASE_PATH=/portal NODE_ENV=production npm run build) || {
            echo "ERROR: busibox-portal build failed"
            exit 1
        }
        
        echo "Building busibox-agents..."
        (cd /srv/busibox-agents && NEXT_PUBLIC_BASE_PATH=/agents NODE_ENV=production npm run build) || {
            echo "ERROR: busibox-agents build failed"
            exit 1
        }
        
        # Copy static assets into standalone directories
        # Standalone mode doesn't include public/ or .next/static/ automatically
        for app_dir in /srv/busibox-portal /srv/busibox-agents; do
            if [ -d "$app_dir/.next/standalone" ]; then
                echo "Copying static assets for $(basename $app_dir)..."
                cp -r "$app_dir/public" "$app_dir/.next/standalone/public" 2>/dev/null || true
                mkdir -p "$app_dir/.next/standalone/.next"
                cp -r "$app_dir/.next/static" "$app_dir/.next/standalone/.next/static"
            fi
        done
        
        # Prune dev dependencies to reduce memory footprint
        echo "Pruning dev dependencies..."
        (cd /srv/busibox-portal && npm prune --omit=dev 2>/dev/null) || true
        (cd /srv/busibox-agents && npm prune --omit=dev 2>/dev/null) || true
        
        # Sync database schema (if prisma is configured)
        if [ -d "/srv/busibox-portal/prisma" ] && [ -f "/srv/busibox-portal/prisma.config.ts" ]; then
            echo "Syncing database schema..."
            cd /srv/busibox-portal
            npx prisma db push 2>/dev/null || {
                echo "Warning: prisma db push failed, continuing..."
            }
        fi
        
        echo ""
        echo "Build complete. Starting standalone production servers..."
        echo ""
        
        # Start both apps with standalone server (much lower memory than npm start)
        # NODE_OPTIONS caps V8 heap to prevent unbounded growth
        exec concurrently \
            --names "portal,agents" \
            --prefix-colors "blue,green" \
            --kill-others-on-fail \
            "cd /srv/busibox-portal && PORT=3000 HOSTNAME=0.0.0.0 NODE_OPTIONS='--max-old-space-size=512' node .next/standalone/server.js" \
            "cd /srv/busibox-agents && PORT=3001 HOSTNAME=0.0.0.0 NODE_OPTIONS='--max-old-space-size=512' node .next/standalone/server.js"
        ;;
        
    start)
        echo "Starting Core Apps in production mode (pre-built, standalone)..."
        
        # Run prisma db push to sync schema (uses prisma.config.ts for DATABASE_URL)
        # This is safe because it only creates/modifies tables without data loss
        if [ -d "/srv/busibox-portal/prisma" ] && [ -f "/srv/busibox-portal/prisma.config.ts" ]; then
            echo "Syncing database schema..."
            cd /srv/busibox-portal
            npx prisma db push --accept-data-loss 2>/dev/null || {
                echo "Warning: prisma db push failed, trying migrate deploy..."
                npx prisma migrate deploy 2>/dev/null || true
            }
        fi
        
        # Start both apps with standalone server (much lower memory than npm start)
        # NEXT_PUBLIC_BASE_PATH was set at build time and is baked into the bundle
        # PORT and HOSTNAME must be set at runtime
        # NODE_OPTIONS caps V8 heap to prevent unbounded growth
        exec concurrently \
            --names "portal,agents" \
            --prefix-colors "blue,green" \
            --kill-others-on-fail \
            "cd /srv/busibox-portal && PORT=3000 HOSTNAME=0.0.0.0 NODE_OPTIONS='--max-old-space-size=512' node .next/standalone/server.js" \
            "cd /srv/busibox-agents && PORT=3001 HOSTNAME=0.0.0.0 NODE_OPTIONS='--max-old-space-size=512' node .next/standalone/server.js"
        ;;
        
    *)
        echo "Usage: $0 {dev|prod|start}"
        echo ""
        echo "  dev   - Development mode with Turbopack hot-reload (default)"
        echo "  prod  - Build from volume-mounted source, then serve with standalone server"
        echo "  start - Serve pre-built apps with standalone server (used by runtime Dockerfile)"
        exit 1
        ;;
esac
