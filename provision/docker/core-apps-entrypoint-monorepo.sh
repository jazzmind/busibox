#!/bin/bash
set -euo pipefail

MODE="${1:-dev}"
ROOT_DIR="/srv/busibox-frontend"

setup_npm_auth() {
  # @jazzmind/busibox-app is public on npmjs.org - no auth needed for install
  true
}

install_workspace_deps() {
  cd "${ROOT_DIR}"
  # Clean stale caches to prevent duplicate React instances during build
  rm -rf node_modules/.cache 2>/dev/null || true
  for app_dir in apps/*/node_modules; do
    rm -rf "$app_dir/.cache" 2>/dev/null || true
  done
  pnpm install --no-frozen-lockfile
}

run() {
  # Build shared package once before starting any apps
  cd "${ROOT_DIR}" && pnpm --filter @jazzmind/busibox-app build

  # Export ROOT_DIR for the process manager
  export ROOT_DIR

  # Launch the Node.js process manager as PID 1.
  # It reads CORE_APPS_MODE, ENABLED_APPS (comma-separated, e.g. "portal,admin"),
  # and optional INITIAL_APP_MODES to decide per-app dev vs prod mode.
  # Control API on port 9999.
  exec node /usr/local/bin/app-manager.js
}

validate_monorepo() {
  if [ ! -f "${ROOT_DIR}/package.json" ] || [ ! -f "${ROOT_DIR}/pnpm-workspace.yaml" ]; then
    echo "================================================================="
    echo "ERROR: busibox-frontend monorepo is missing or incomplete."
    echo ""
    echo "  Expected: ${ROOT_DIR}/package.json"
    echo "  Expected: ${ROOT_DIR}/pnpm-workspace.yaml"
    echo ""
    echo "  Contents of ${ROOT_DIR}:"
    ls -la "${ROOT_DIR}" 2>/dev/null || echo "  (directory does not exist)"
    echo ""
    echo "  This container uses local-dev mode which volume-mounts the"
    echo "  busibox-frontend repo from the host. The host directory is"
    echo "  either empty or not a valid monorepo."
    echo ""
    echo "  Fix: ensure busibox-frontend is cloned on the host, then"
    echo "  re-run the installer."
    echo "================================================================="
    exit 1
  fi
}

setup_npm_auth
validate_monorepo
install_workspace_deps

case "${MODE}" in
  dev|prod|start)
    # All modes now go through the process manager.
    # CORE_APPS_MODE env var tells the PM the global default (dev or prod).
    # For "prod"/"start", set NODE_ENV and let PM handle builds.
    if [ "${MODE}" = "prod" ] || [ "${MODE}" = "start" ]; then
      export NODE_ENV=production
      export CORE_APPS_MODE=prod
    fi
    run
    ;;
  *)
    echo "Usage: $0 {dev|prod|start}"
    exit 1
    ;;
esac
