#!/usr/bin/env bash
#
# Get model names for current system configuration
#
# Usage:
#   get-models.sh              # Output all model env vars
#   get-models.sh fast         # Output fast model name
#   get-models.sh agent        # Output agent model name
#   get-models.sh frontier     # Output frontier model name
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Get backend and tier
BACKEND="${LLM_BACKEND:-$(bash "${SCRIPT_DIR}/detect-backend.sh")}"
TIER="${LLM_TIER:-$(bash "${SCRIPT_DIR}/get-memory-tier.sh" "$BACKEND")}"

# Model registry file
MODEL_REGISTRY="${REPO_ROOT}/config/demo-models.yaml"

get_model() {
    local role="$1"
    
    if [[ ! -f "$MODEL_REGISTRY" ]]; then
        echo "ERROR: Model registry not found: $MODEL_REGISTRY" >&2
        return 1
    fi
    
    python3 -c "
import yaml
import sys

try:
    with open('${MODEL_REGISTRY}') as f:
        config = yaml.safe_load(f)
    
    tier = config['tiers'].get('${TIER}')
    if not tier:
        print(f'ERROR: Unknown tier: ${TIER}', file=sys.stderr)
        sys.exit(1)
    
    backend = tier.get('${BACKEND}')
    if not backend:
        print(f'ERROR: Backend ${BACKEND} not configured for tier ${TIER}', file=sys.stderr)
        sys.exit(1)
    
    model = backend.get('${role}')
    if not model:
        print(f'ERROR: Role ${role} not configured', file=sys.stderr)
        sys.exit(1)
    
    print(model)
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
"
}

# If run directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
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
            echo "LLM_BACKEND=${BACKEND}"
            echo "LLM_TIER=${TIER}"
            echo "LLM_MODEL_FAST=$(get_model fast)"
            echo "LLM_MODEL_AGENT=$(get_model agent)"
            echo "LLM_MODEL_FRONTIER=$(get_model frontier)"
            ;;
        *)
            echo "Usage: $0 [fast|agent|frontier|all]" >&2
            exit 1
            ;;
    esac
fi
