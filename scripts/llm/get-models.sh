#!/usr/bin/env bash
#
# Get model names for current system configuration
#
# Usage:
#   get-models.sh              # Output all model env vars
#   get-models.sh fast         # Output fast model name
#   get-models.sh agent       # Output agent model name
#   get-models.sh test        # Output test model (from model_registry.yml)
#   get-models.sh default     # Output default model (from model_registry.yml)
#   get-models.sh embed       # Output embed model (tier-based)
#   get-models.sh whisper     # Output whisper model (tier-based)
#   get-models.sh kokoro      # Output kokoro model (tier-based)
#   get-models.sh flux        # Output flux model (tier-based)
#   get-models.sh colpali     # Output colpali model (tier-based)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Get backend and tier
BACKEND="${LLM_BACKEND:-$(bash "${SCRIPT_DIR}/detect-backend.sh")}"
TIER="${LLM_TIER:-$(bash "${SCRIPT_DIR}/get-memory-tier.sh" "$BACKEND")}"

# Model config file
MODEL_REGISTRY="${REPO_ROOT}/provision/ansible/group_vars/all/model_registry.yml"

# Use PYTHON_CMD if provided (e.g., from venv with PyYAML), else fall back to system python3
PYTHON="${PYTHON_CMD:-python3}"

# Get model from model_registry.yml tiers section (tier-based)
get_tier_model() {
    local role="$1"

    if [[ ! -f "$MODEL_REGISTRY" ]]; then
        echo "ERROR: Model registry not found: $MODEL_REGISTRY" >&2
        return 1
    fi

    "$PYTHON" -c "
import yaml
import sys

try:
    with open('${MODEL_REGISTRY}') as f:
        config = yaml.safe_load(f)

    tiers = config.get('tiers', {})
    tier = tiers.get('${TIER}')
    if not tier:
        print(f'ERROR: Unknown tier: ${TIER}', file=sys.stderr)
        sys.exit(1)

    backend = tier.get('${BACKEND}')
    if not backend:
        print(f'ERROR: Backend ${BACKEND} not configured for tier ${TIER}', file=sys.stderr)
        sys.exit(1)

    model_key = backend.get('${role}')
    if not model_key:
        # Role not available for this tier/backend (e.g., flux on minimal)
        print('')
        sys.exit(0)

    # Resolve model key to HuggingFace model_name via available_models
    available = config.get('available_models', {})
    model_info = available.get(model_key, {})
    model_name = model_info.get('model_name', '')
    if not model_name:
        print(f'ERROR: Model key {model_key} not found in available_models', file=sys.stderr)
        sys.exit(1)

    print(model_name)
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

    "$PYTHON" -c "
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

# Wrapper that resolves model names.
# When USE_TIER_ONLY=1, all roles use tier-based lookup (for download/caching).
# Otherwise, core text roles use model_purposes_dev; hardware roles use tiers section.
get_model() {
    local role="$1"

    # Tier-only mode: used by download-models.sh to respect LLM_TIER for all roles
    if [[ "${USE_TIER_ONLY:-0}" == "1" ]]; then
        get_tier_model "$role"
        return
    fi

    case "$role" in
        fast|agent|test|default|chat|classify|parsing|cleanup|tool_calling|research|vision|frontier|frontier-fast)
            get_purpose_model "$role"
            ;;
        embed|whisper|kokoro|flux|colpali)
            get_tier_model "$role"
            ;;
        *)
            # Try purpose first, fall back to tier
            local result
            result=$(get_purpose_model "$role" 2>/dev/null) && echo "$result" && return 0
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
        test)
            get_model test
            ;;
        default)
            get_model default
            ;;
        embed)
            get_model embed
            ;;
        whisper)
            get_model whisper
            ;;
        kokoro)
            get_model kokoro
            ;;
        flux)
            get_model flux
            ;;
        colpali)
            get_model colpali
            ;;
        all)
            echo "LLM_BACKEND=${BACKEND}"
            echo "LLM_TIER=${TIER}"
            echo "LLM_MODEL_FAST=$(get_model fast)"
            echo "LLM_MODEL_AGENT=$(get_model agent)"
            echo "LLM_MODEL_TEST=$(get_model test)"
            echo "LLM_MODEL_DEFAULT=$(get_model default)"
            echo "LLM_MODEL_EMBED=$(get_model embed)"
            echo "LLM_MODEL_WHISPER=$(get_model whisper)"
            echo "LLM_MODEL_KOKORO=$(get_model kokoro)"
            echo "LLM_MODEL_FLUX=$(get_model flux)"
            echo "LLM_MODEL_COLPALI=$(get_model colpali)"
            ;;
        *)
            echo "Usage: $0 [fast|agent|test|default|embed|whisper|kokoro|flux|colpali|all]" >&2
            exit 1
            ;;
    esac
fi
