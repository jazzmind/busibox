#!/usr/bin/env bash
# =============================================================================
# Get Model Names for Current System Configuration
# =============================================================================
#
# Reads the model registry and returns model names based on detected
# system configuration (architecture and RAM tier).
#
# Usage:
#   ./get-models.sh fast      # Get fast model name
#   ./get-models.sh agent     # Get agent model name
#   ./get-models.sh frontier  # Get frontier model name
#   ./get-models.sh all       # Get all models as env vars (default)
#
# Output (for 'all'):
#   DEMO_MODEL_FAST=mlx-community/Qwen2.5-3B-Instruct-4bit
#   DEMO_MODEL_AGENT=mlx-community/Qwen2.5-7B-Instruct-4bit
#   DEMO_MODEL_FRONTIER=mlx-community/Qwen2.5-14B-Instruct-4bit
#
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source system detection
source "${SCRIPT_DIR}/detect-system.sh"

# Parse model registry using Python (requires PyYAML)
get_model() {
    local role="$1"
    python3 -c "
import yaml
import sys

try:
    with open('${REPO_ROOT}/config/demo-models.yaml') as f:
        config = yaml.safe_load(f)
    tier = config['tiers']['${DEMO_TIER}']
    print(tier['${DEMO_BACKEND}']['${role}'])
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    sys.exit(1)
"
}

case "${1:-all}" in
    fast)
        get_model fast
        ;;
    agent)
        get_model agent
        ;;
    frontier)
        get_model frontier
        ;;
    all)
        echo "DEMO_MODEL_FAST=$(get_model fast)"
        echo "DEMO_MODEL_AGENT=$(get_model agent)"
        echo "DEMO_MODEL_FRONTIER=$(get_model frontier)"
        ;;
    *)
        echo "Usage: $0 [fast|agent|frontier|all]" >&2
        exit 1
        ;;
esac
