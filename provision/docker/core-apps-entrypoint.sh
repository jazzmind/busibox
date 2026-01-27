#!/bin/bash
# =============================================================================
# Core Apps Entrypoint Script
# =============================================================================
#
# Starts both ai-portal and agent-manager in the same container.
# Uses concurrently to manage both processes.
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

# Function to run npm install if needed (for dev mode with volume mounts)
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
        
        # Check if node_modules exists and is populated
        if [ ! -d "node_modules" ] || [ ! -f "node_modules/.package-lock.json" ]; then
            echo "Installing dependencies for $app_name..."
            npm install
        fi
        
        # Run prisma generate if prisma directory exists
        if [ -d "prisma" ]; then
            echo "Running prisma generate for $app_name..."
            npx prisma generate 2>/dev/null || true
        fi
    fi
}

case "$MODE" in
    dev)
        echo "Starting Core Apps in development mode..."
        
        # Setup npm authentication (required for @jazzmind/busibox-app from GitHub Packages)
        setup_npm_auth
        
        # Setup dependencies for both apps
        # Note: busibox-app is mounted directly into node_modules/@jazzmind/busibox-app
        # via Docker volume overlay (no symlink needed)
        ensure_deps "/srv/ai-portal" "ai-portal"
        ensure_deps "/srv/agent-manager" "agent-manager"
        
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
        
    start)
        echo "Starting Core Apps in production mode..."
        
        # Run prisma migrations if needed
        if [ -d "/srv/ai-portal/prisma" ]; then
            cd /srv/ai-portal
            npx prisma migrate deploy 2>/dev/null || true
        fi
        
        # Start both apps with concurrently
        # IMPORTANT: Set NEXT_PUBLIC_BASE_PATH per-app since they need different values
        exec concurrently \
            --names "portal,agents" \
            --prefix-colors "blue,green" \
            --kill-others-on-fail \
            "cd /srv/ai-portal && PORT=3000 NEXT_PUBLIC_BASE_PATH=/portal npm start" \
            "cd /srv/agent-manager && PORT=3001 NEXT_PUBLIC_BASE_PATH=/agents npm start"
        ;;
        
    *)
        echo "Usage: $0 {dev|start}"
        exit 1
        ;;
esac
