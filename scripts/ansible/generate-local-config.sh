#!/bin/bash
#
# Generate local.yml for Ansible inventory from state file
#
# This script reads network octets from .busibox-state-{env} and generates
# the local.yml file needed by Ansible inventory.
#
# Usage:
#   ./generate-local-config.sh staging
#   ./generate-local-config.sh production
#
# The generated file contains installation-specific configuration that
# should not be committed to git (network octets, base domain, etc.)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

error() { echo -e "${RED}ERROR: $1${NC}" >&2; }
info() { echo -e "${GREEN}$1${NC}"; }
warn() { echo -e "${YELLOW}$1${NC}"; }

# Get environment from argument or detect from state files
ENV="${1:-}"
if [[ -z "$ENV" ]]; then
    # Try to detect from existing state files
    if [[ -f "${REPO_ROOT}/.busibox-state-staging" ]]; then
        ENV="staging"
    elif [[ -f "${REPO_ROOT}/.busibox-state-prod" ]]; then
        ENV="production"
    else
        error "No environment specified and no state file found"
        echo "Usage: $0 <staging|production>"
        exit 1
    fi
fi

# Map environment names to state file suffixes
case "$ENV" in
    staging)
        STATE_SUFFIX="staging"
        INVENTORY_DIR="staging"
        ;;
    production|prod)
        STATE_SUFFIX="prod"
        INVENTORY_DIR="production"
        ENV="production"
        ;;
    *)
        error "Unknown environment: $ENV"
        echo "Usage: $0 <staging|production>"
        exit 1
        ;;
esac

STATE_FILE="${REPO_ROOT}/.busibox-state-${STATE_SUFFIX}"
LOCAL_CONFIG="${REPO_ROOT}/provision/ansible/inventory/${INVENTORY_DIR}/group_vars/all/local.yml"

# Check state file exists
if [[ ! -f "$STATE_FILE" ]]; then
    error "State file not found: $STATE_FILE"
    echo "Run the install script first to create the state file."
    exit 1
fi

# Read values from state file
get_state() {
    local key="$1"
    local default="${2:-}"
    local value
    value=$(grep "^${key}=" "$STATE_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
    # Remove surrounding quotes if present
    value="${value#\"}"
    value="${value%\"}"
    value="${value#\'}"
    value="${value%\'}"
    echo "${value:-$default}"
}

# Get network octets from state file
NETWORK_BASE_STAGING=$(get_state "NETWORK_BASE_OCTETS_STAGING" "")
NETWORK_BASE_PRODUCTION=$(get_state "NETWORK_BASE_OCTETS_PRODUCTION" "")
# Check SITE_DOMAIN first, fall back to BASE_DOMAIN for backwards compatibility
SITE_DOMAIN=$(get_state "SITE_DOMAIN" "")
[[ -z "$SITE_DOMAIN" ]] && SITE_DOMAIN=$(get_state "BASE_DOMAIN" "")

# Check required values
MISSING=""
if [[ -z "$NETWORK_BASE_STAGING" ]]; then
    MISSING="$MISSING NETWORK_BASE_OCTETS_STAGING"
fi
if [[ -z "$NETWORK_BASE_PRODUCTION" ]]; then
    MISSING="$MISSING NETWORK_BASE_OCTETS_PRODUCTION"
fi

if [[ -n "$MISSING" ]]; then
    error "Missing required values in state file:$MISSING"
    echo ""
    echo "Add the following to $STATE_FILE:"
    echo "  NETWORK_BASE_OCTETS_STAGING=10.96.201"
    echo "  NETWORK_BASE_OCTETS_PRODUCTION=10.96.200"
    exit 1
fi

# Create directory if needed
mkdir -p "$(dirname "$LOCAL_CONFIG")"

# Generate local.yml
info "Generating $LOCAL_CONFIG..."

cat > "$LOCAL_CONFIG" << EOF
---
# Local Installation Configuration
# Generated from: $STATE_FILE
# Generated at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
#
# DO NOT EDIT - regenerate with: scripts/ansible/generate-local-config.sh $ENV
# DO NOT COMMIT - this file is gitignored

# Network Configuration (installation-specific)
network_base_octets_staging: "${NETWORK_BASE_STAGING}"
network_base_octets_production: "${NETWORK_BASE_PRODUCTION}"

# Site Domain (the full domain for this environment, e.g., staging.ai.example.com)
EOF

if [[ -n "$SITE_DOMAIN" ]]; then
    echo "site_domain: \"${SITE_DOMAIN}\"" >> "$LOCAL_CONFIG"
else
    echo "# site_domain: \"staging.ai.example.com\"  # Add to state file if needed" >> "$LOCAL_CONFIG"
fi

info "Done! Generated $LOCAL_CONFIG"
echo ""
echo "Contents:"
cat "$LOCAL_CONFIG"
