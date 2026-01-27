#!/usr/bin/env bash
#
# Get model names for current system configuration
#
# Usage:
#   get-models.sh              # Output all model env vars
#   get-models.sh fast         # Output fast model name
#   get-models.sh agent        # Output agent model name
#   get-models.sh frontier     # Output frontier model name
#   get-models.sh test         # Output test model (from model_registry.yml)
#   get-models.sh default      # Output default model (from model_registry.yml)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Get backend and tier
BACKEND="${LLM_BACKEND:-$(bash "${SCRIPT_DIR}/detect-backend.sh")}"
TIER="${LLM_TIER:-$(bash "${SCRIPT_DIR}/get-memory-tier.sh" "$BACKEND")}"

# Model config files
DEMO_MODELS="${REPO_ROOT}/config/demo-models.yaml"
MODEL_REGISTRY="${REPO_ROOT}/provision/ansible/group_vars/all/model_registry.yml"

# Get model from demo-models.yaml (tier-based)
get_tier_model() {
    local role="$1"
    
    if [[ ! -f "$DEMO_MODELS" ]]; then
        echo "ERROR: Demo models not found: $DEMO_MODELS" >&2
        return 1
    fi
    
    python3 -c "
import yaml
import sys

try:
    with open('${DEMO_MODELS}') as f:
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

# Get model from model_registry.yml (purpose-based, for dev environment)
get_purpose_model() {
    local purpose="$1"
    
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
    
    # Use model_purposes_dev for development environments
    purposes = config.get('model_purposes_dev', config.get('model_purposes', {}))
    model_key = purposes.get('${purpose}')
    if not model_key:
        print(f'ERROR: Purpose ${purpose} not configured', file=sys.stderr)
        sys.exit(1)
    
    # Get the model_name from available_models
    available = config.get('available_models', {})
    model_info = available.get(model_key, {})
    model_name = model_info.get('model_name', model_key)
    
    print(model_name)
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
"
}

# Wrapper that tries purpose first, falls back to tier
get_model() {
    local role="$1"
    
    # For 'test' and 'default', use purpose-based lookup from model_registry.yml
    case "$role" in
        test|default)
            get_purpose_model "$role"
            ;;
        *)
            get_tier_model "$role"
            ;;
    esac
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
        test)
            get_model test
            ;;
        default)
            get_model default
            ;;
        all)
            echo "LLM_BACKEND=${BACKEND}"
            echo "LLM_TIER=${TIER}"
            echo "LLM_MODEL_FAST=$(get_model fast)"
            echo "LLM_MODEL_AGENT=$(get_model agent)"
            echo "LLM_MODEL_FRONTIER=$(get_model frontier)"
            echo "LLM_MODEL_TEST=$(get_model test)"
            echo "LLM_MODEL_DEFAULT=$(get_model default)"
            ;;
        *)
            echo "Usage: $0 [fast|agent|frontier|test|default|all]" >&2
            exit 1
            ;;
    esac
fi
