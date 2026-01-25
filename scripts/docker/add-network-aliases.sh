#!/usr/bin/env bash
# =============================================================================
# Add Network Aliases to Docker Compose Services
# =============================================================================
# Execution Context: Admin workstation
# Purpose: Add canonical hostname aliases to all services in docker-compose files
# Usage: bash scripts/docker/add-network-aliases.sh
#
# This script adds network aliases to docker-compose.yml for all services
# to enable consistent DNS resolution using canonical hostnames.
#
# Services will be accessible via their canonical names (e.g., "postgres")
# regardless of the container name (e.g., "local-postgres", "dev-postgres")
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"

echo "Adding network aliases to $COMPOSE_FILE..."

# Backup the file
cp "$COMPOSE_FILE" "$COMPOSE_FILE.bak"
echo "Backup created: $COMPOSE_FILE.bak"

# Define services and their aliases
declare -A SERVICE_ALIASES=(
    ["embedding-api"]="embedding-api embedding"
    ["ingest-api"]="ingest-api ingest"
    ["search-api"]="search-api search"
    ["agent-api"]="agent-api agent"
    ["docs-api"]="docs-api docs"
    ["deploy-api"]="deploy-api deploy"
    ["nginx"]="nginx proxy"
    ["ollama"]="ollama"
    ["vllm"]="vllm"
)

# Function to add network aliases to a service
add_aliases() {
    local service=$1
    local aliases=$2
    
    echo "Processing $service..."
    
    # Check if service already has network aliases
    if grep -A 5 "^  $service:" "$COMPOSE_FILE" | grep -q "aliases:"; then
        echo "  - $service already has aliases, skipping"
        return
    fi
    
    # Find the networks section for this service and add aliases
    # This is a simplified approach - for production, use yq or similar tool
    python3 <<EOF
import yaml
import sys

with open('$COMPOSE_FILE', 'r') as f:
    content = f.read()

# Split into lines for processing
lines = content.split('\n')
output = []
in_service = False
in_networks = False
indent_level = 0

for i, line in enumerate(lines):
    output.append(line)
    
    # Check if we're at the service definition
    if line.strip() == '$service:' and line.startswith('  '):
        in_service = True
        continue
    
    # If we're in the service and hit networks section
    if in_service and 'networks:' in line and '- busibox-net' in line:
        # Replace simple network reference with aliased version
        indent = len(line) - len(line.lstrip())
        output[-1] = ' ' * indent + 'networks:'
        output.append(' ' * (indent + 2) + 'busibox-net:')
        output.append(' ' * (indent + 4) + 'aliases:')
        for alias in '$aliases'.split():
            output.append(' ' * (indent + 6) + f'- {alias}')
        in_service = False
        continue
    
    # Exit service context if we hit another top-level service
    if in_service and line.startswith('  ') and ':' in line and not line.startswith('    '):
        in_service = False

with open('$COMPOSE_FILE', 'w') as f:
    f.write('\n'.join(output))
EOF
    
    echo "  - Added aliases: $aliases"
}

# Process each service
for service in "${!SERVICE_ALIASES[@]}"; do
    add_aliases "$service" "${SERVICE_ALIASES[$service]}"
done

echo ""
echo "Network aliases added successfully!"
echo "Backup saved to: $COMPOSE_FILE.bak"
echo ""
echo "To verify changes:"
echo "  diff $COMPOSE_FILE.bak $COMPOSE_FILE"
echo ""
echo "To test DNS resolution after starting services:"
echo "  docker exec local-ingest-api ping -c 1 postgres"
echo "  docker exec local-ingest-api getent hosts authz-api"
