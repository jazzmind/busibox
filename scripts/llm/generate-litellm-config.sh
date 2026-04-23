#!/usr/bin/env bash
#
# Generate LiteLLM configuration from model_registry.yml
#
# Usage:
#   generate-litellm-config.sh              # Generate for detected backend
#   generate-litellm-config.sh mlx          # Generate for MLX
#   generate-litellm-config.sh vllm         # Generate for vLLM
#   generate-litellm-config.sh cloud        # Generate for AWS Bedrock
#
# This script reads model definitions from:
#   provision/ansible/group_vars/all/model_registry.yml
#
# Environment variables:
#   ENVIRONMENT - development/staging/production (default: development)
#   LLM_BACKEND - mlx/vllm/cloud (auto-detected if not set)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source UI library
source "${SCRIPT_DIR}/../lib/ui.sh"

# Model registry file
MODEL_REGISTRY="${REPO_ROOT}/provision/ansible/group_vars/all/model_registry.yml"

# Output to the same file docker-compose mounts
OUTPUT_FILE="${REPO_ROOT}/config/litellm-config.yaml"

# Get backend
BACKEND="${1:-${LLM_BACKEND:-$(bash "${SCRIPT_DIR}/detect-backend.sh" 2>/dev/null || echo "mlx")}}"
ENVIRONMENT="${ENVIRONMENT:-${NODE_ENV:-development}}"

# Parse YAML using Python (PyYAML is bundled with Ansible)
parse_yaml() {
    local file="$1"
    local query="$2"
    
    python3 -c "
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
parts = sys.argv[2].strip('.').split('.')
result = data
for part in parts:
    if result is None:
        break
    if part.startswith('[') and part.endswith(']'):
        idx = int(part[1:-1])
        result = result[idx] if isinstance(result, list) and len(result) > idx else None
    else:
        result = result.get(part) if isinstance(result, dict) else None
if result is not None:
    print(result)
" "$file" "$query" 2>/dev/null || echo ""
}

# Get model_name from available_models for a given key
get_model_name() {
    local model_key="$1"
    local model_name
    
    # Try to get model_name from available_models section
    model_name=$(python3 -c "
import yaml
with open('$MODEL_REGISTRY', 'r') as f:
    data = yaml.safe_load(f)
available = data.get('available_models', {})
model = available.get('$model_key', {})
print(model.get('model_name', '$model_key'))
" 2>/dev/null)
    
    echo "${model_name:-$model_key}"
}

# Get purpose->model mapping based on environment.
# Checks PURPOSE_<ROLE> env vars first (set by CLI tier editor), then falls
# back to the model_registry YAML.  Also applies tier-based overrides from
# model_registry.yml tiers.<tier>.mlx when LLM_TIER is set.
get_model_for_purpose() {
    local purpose="$1"

    # Highest priority: explicit PURPOSE_<ROLE> environment variable
    local env_key
    env_key="PURPOSE_$(echo "$purpose" | tr '[:lower:]-' '[:upper:]_')"
    local env_val=""
    eval "env_val=\"\${${env_key}:-}\""
    if [[ -n "$env_val" ]]; then
        echo "$env_val"
        return
    fi

    local model_key
    model_key=$(python3 -c "
import yaml, os
with open('$MODEL_REGISTRY', 'r') as f:
    data = yaml.safe_load(f)

# default_purposes provides base assignments shared by all environments;
# environment-specific maps only need to list overrides.
purposes = dict(data.get('default_purposes', {}) or {})

env = '$ENVIRONMENT'
if env in ('development', 'demo', 'dev'):
    purposes.update(data.get('model_purposes_dev', {}) or {})
else:
    purposes.update(data.get('model_purposes', {}) or {})

# Apply tier-based overrides when LLM_TIER is set
llm_tier = os.environ.get('LLM_TIER', '').strip(\"'\\\"\").strip()
backend = os.environ.get('LLM_BACKEND', 'mlx').strip()
if llm_tier:
    tiers = data.get('tiers', {}) or {}
    tier_cfg = tiers.get(llm_tier, {})
    tier_models = tier_cfg.get(backend, {}) or {}
    if tier_models:
        purposes.update(tier_models)

# Resolve aliases: a purpose can point at another purpose (e.g. test -> fast).
available = data.get('available_models', {}) or {}
visited = set()
target = '$purpose'
while target in purposes and target not in visited:
    visited.add(target)
    value = purposes[target]
    if value in available or value not in purposes:
        print(value)
        break
    target = value
else:
    if '$purpose' in purposes:
        print(purposes['$purpose'])
" 2>/dev/null)

    echo "$model_key"
}

# Get provider for a model key
get_model_provider() {
    local model_key="$1"
    
    python3 -c "
import yaml
with open('$MODEL_REGISTRY', 'r') as f:
    data = yaml.safe_load(f)
available = data.get('available_models', {})
model = available.get('$model_key', {})
print(model.get('provider', 'mlx'))
" 2>/dev/null || echo "mlx"
}

# Get description for a model key
get_model_description() {
    local model_key="$1"
    
    python3 -c "
import yaml
with open('$MODEL_REGISTRY', 'r') as f:
    data = yaml.safe_load(f)
available = data.get('available_models', {})
model = available.get('$model_key', {})
desc = model.get('description', '')
print(desc)
" 2>/dev/null || echo ""
}

# Get mode for a model key (for non-chat endpoints)
get_model_mode() {
    local model_key="$1"

    python3 -c "
import yaml
with open('$MODEL_REGISTRY', 'r') as f:
    data = yaml.safe_load(f)
available = data.get('available_models', {})
model = available.get('$model_key', {})
print(model.get('mode', ''))
" 2>/dev/null || echo ""
}

# Get multimodal capability for a model key
get_model_multimodal() {
    local model_key="$1"

    python3 -c "
import yaml
with open('$MODEL_REGISTRY', 'r') as f:
    data = yaml.safe_load(f)
available = data.get('available_models', {})
model = available.get('$model_key', {})
val = model.get('multimodal', None)
if val is not None:
    print('true' if val else 'false')
else:
    print('')
" 2>/dev/null || echo ""
}

# Get tool_calling capability for a model key
get_model_tool_calling() {
    local model_key="$1"

    python3 -c "
import yaml
with open('$MODEL_REGISTRY', 'r') as f:
    data = yaml.safe_load(f)
available = data.get('available_models', {})
model = available.get('$model_key', {})
val = model.get('tool_calling', None)
if val is not None:
    print('true' if val else 'false')
else:
    print('')
" 2>/dev/null || echo ""
}

# Get display name for a purpose (capitalize first letter of each word)
get_purpose_display_name() {
    local purpose="$1"
    python3 -c "
name = '$purpose'
parts = name.replace('_', ' ').replace('-', ' ').split()
print(' '.join(p.capitalize() for p in parts))
" 2>/dev/null || echo "$purpose"
}

# Model config file (generated by generate-model-config.sh for vLLM)
MODEL_CONFIG="${REPO_ROOT}/provision/ansible/group_vars/all/model_config.yml"

# Look up the vLLM port for a given purpose from model_config.yml.
# Returns the port number, or empty string if not found.
_vllm_port_for_purpose() {
    local purpose="$1"
    python3 -c "
import yaml, sys, os
cfg_path = sys.argv[1]
purpose = sys.argv[2]
if not os.path.exists(cfg_path):
    sys.exit(0)
with open(cfg_path) as f:
    data = yaml.safe_load(f) or {}
purposes = data.get('model_purposes', {}) or {}
model_key = purposes.get(purpose, '')
if not model_key:
    sys.exit(0)
models = data.get('models', {}) or {}
entry = models.get(model_key, {}) or {}
port = entry.get('port')
if port is not None and entry.get('assigned'):
    print(port)
" "$MODEL_CONFIG" "$purpose" 2>/dev/null || true
}

# Get api_base for a purpose and backend
get_api_base_for_purpose() {
    local backend="$1"
    local purpose="$2"
    local mlx_fast_port="${MLX_FAST_PORT:-18081}"

    if [[ "$backend" == "mlx" ]]; then
        case "$purpose" in
            fast|test|classify) echo "http://host.docker.internal:${mlx_fast_port}/v1" ;;
            transcribe) echo "http://host.docker.internal:8084/v1" ;;
            voice) echo "http://host.docker.internal:8082/v1" ;;
            image) echo "http://host.docker.internal:8083/v1" ;;
            *) echo "http://host.docker.internal:8080/v1" ;;
        esac
    elif [[ "$backend" == "vllm" ]]; then
        local port
        port=$(_vllm_port_for_purpose "$purpose")
        if [[ -n "$port" ]]; then
            echo "http://vllm:${port}/v1"
        else
            echo "http://vllm:8000/v1"
        fi
    else
        echo ""
    fi
}

generate_config_from_registry() {
    local backend="$1"
    
    # Check registry exists
    if [[ ! -f "$MODEL_REGISTRY" ]]; then
        error "Model registry not found: $MODEL_REGISTRY"
        exit 1
    fi
    
    # Start building config
    cat > "$OUTPUT_FILE" << EOF
# LiteLLM Configuration - Generated from model_registry.yml
# Environment: ${ENVIRONMENT}
# Backend: ${backend}
# Generated: $(date -Iseconds)
# DO NOT EDIT - regenerate with: scripts/llm/generate-litellm-config.sh

model_list:
EOF

    # Define purposes to include (order matters for readability)
    # Must stay in sync with CONFIGURABLE_PURPOSES in srv/agent/app/api/llm.py
    local purposes=("default" "test" "fast" "classify" "cleanup" "parsing" "vision" "agent" "chat" "research" "frontier" "fallback" "tool_calling" "video" "image" "transcribe" "voice")
    local unique_model_keys=""
    
    for purpose in "${purposes[@]}"; do
        local model_key=$(get_model_for_purpose "$purpose")
        
        if [[ -z "$model_key" ]]; then
            continue
        fi
        
        local model_name=$(get_model_name "$model_key")
        local provider=$(get_model_provider "$model_key")
        local description=$(get_model_description "$model_key")
        local mode=$(get_model_mode "$model_key")
        local purpose_api_base
        purpose_api_base=$(get_api_base_for_purpose "$backend" "$purpose")

        # Track unique registry model keys used by purposes so we can register
        # explicit model entries (e.g. qwen3-4b) in addition to purpose aliases.
        if [[ ",${unique_model_keys}," != *",${model_key},"* ]]; then
            unique_model_keys="${unique_model_keys},${model_key}"
        fi
        
        # Build model entry based on provider
        if [[ "$provider" == "bedrock" ]]; then
            cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${purpose}
    litellm_params:
      model: bedrock/${model_name}
EOF
        elif [[ "$provider" == "openai" ]]; then
            # Cloud OpenAI (Sora-2, GPT-*, etc.) — LiteLLM picks up
            # OPENAI_API_KEY from env or its own credential store.
            cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${purpose}
    litellm_params:
      model: openai/${model_name}
      api_key: os.environ/OPENAI_API_KEY
EOF
        elif [[ "$provider" == "mlx" || "$provider" == "vllm" ]]; then
            if [[ -n "$purpose_api_base" ]]; then
                cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${purpose}
    litellm_params:
      model: openai/${model_name}
      api_base: ${purpose_api_base}
      api_key: local
EOF
            else
                cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${purpose}
    litellm_params:
      model: openai/${model_name}
      api_key: local
EOF
            fi
        else
            cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${purpose}
    litellm_params:
      model: ${model_name}
EOF
        fi
        
        # Fetch capability metadata from model registry
        local multimodal
        multimodal=$(get_model_multimodal "$model_key")
        local tool_calling
        tool_calling=$(get_model_tool_calling "$model_key")
        local display_name
        display_name=$(get_purpose_display_name "$purpose")
        
        # Always write model_info for purpose entries
        cat >> "$OUTPUT_FILE" << EOF
    model_info:
      display_name: "${display_name}"
EOF
        if [[ -n "$description" ]]; then
            cat >> "$OUTPUT_FILE" << EOF
      description: "${description}"
EOF
        fi
        if [[ -n "$mode" ]]; then
            cat >> "$OUTPUT_FILE" << EOF
      mode: "${mode}"
EOF
        fi
        if [[ -n "$multimodal" ]]; then
            cat >> "$OUTPUT_FILE" << EOF
      multimodal: ${multimodal}
EOF
        fi
        if [[ -n "$tool_calling" ]]; then
            cat >> "$OUTPUT_FILE" << EOF
      tool_calling: ${tool_calling}
EOF
        fi
        
        echo "" >> "$OUTPUT_FILE"
    done

    # Register explicit model entries for each unique registry model key.
    # Purpose aliases (agent/fast/etc.) remain for runtime calls, while these
    # entries let admin UI purpose assignment target concrete models.
    IFS=',' read -r -a _model_key_arr <<< "${unique_model_keys}"
    for model_key in "${_model_key_arr[@]}"; do
        [[ -z "$model_key" ]] && continue

        local model_name
        model_name=$(get_model_name "$model_key")
        local provider
        provider=$(get_model_provider "$model_key")
        local description
        description=$(get_model_description "$model_key")
        local mode
        mode=$(get_model_mode "$model_key")

        # Choose API base using the first purpose that maps to this model key.
        local representative_purpose=""
        for purpose in "${purposes[@]}"; do
            local key_for_purpose
            key_for_purpose=$(get_model_for_purpose "$purpose")
            if [[ "$key_for_purpose" == "$model_key" ]]; then
                representative_purpose="$purpose"
                break
            fi
        done
        local model_api_base=""
        model_api_base=$(get_api_base_for_purpose "$backend" "${representative_purpose:-default}")

        if [[ "$provider" == "bedrock" ]]; then
            cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${model_key}
    litellm_params:
      model: bedrock/${model_name}
EOF
        elif [[ "$provider" == "openai" ]]; then
            cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${model_key}
    litellm_params:
      model: openai/${model_name}
      api_key: os.environ/OPENAI_API_KEY
EOF
        elif [[ "$provider" == "mlx" || "$provider" == "vllm" ]]; then
            if [[ -n "$model_api_base" ]]; then
                cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${model_key}
    litellm_params:
      model: openai/${model_name}
      api_base: ${model_api_base}
      api_key: local
EOF
            else
                cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${model_key}
    litellm_params:
      model: openai/${model_name}
      api_key: local
EOF
            fi
        else
            cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${model_key}
    litellm_params:
      model: ${model_name}
EOF
        fi

        local multimodal
        multimodal=$(get_model_multimodal "$model_key")
        local tool_calling
        tool_calling=$(get_model_tool_calling "$model_key")

        if [[ -n "$description" || -n "$mode" || -n "$multimodal" || -n "$tool_calling" ]]; then
            cat >> "$OUTPUT_FILE" << EOF
    model_info:
EOF
            if [[ -n "$description" ]]; then
                cat >> "$OUTPUT_FILE" << EOF
      description: "${description}"
EOF
            fi
            if [[ -n "$mode" ]]; then
                cat >> "$OUTPUT_FILE" << EOF
      mode: "${mode}"
EOF
            fi
            if [[ -n "$multimodal" ]]; then
                cat >> "$OUTPUT_FILE" << EOF
      multimodal: ${multimodal}
EOF
            fi
            if [[ -n "$tool_calling" ]]; then
                cat >> "$OUTPUT_FILE" << EOF
      tool_calling: ${tool_calling}
EOF
            fi
        fi

        echo "" >> "$OUTPUT_FILE"
    done
    
    # Add general settings
    cat >> "$OUTPUT_FILE" << 'EOF'
general_settings:
  debug: true
  master_key: os.environ/LITELLM_MASTER_KEY

router_settings:
  enable_cache: true
  num_retries: 3
  retry_after: 5
  timeout: 120
  allowed_fails: 1

litellm_settings:
  drop_params: true
  request_timeout: 120
  set_verbose: true
  callbacks: litellm_hooks.mlx_ensure_hook.mlx_ensure_hook_instance
EOF
    
    success "Generated ${OUTPUT_FILE}"
    echo "  Source: ${MODEL_REGISTRY}"
    echo "  Environment: ${ENVIRONMENT}"
    echo "  Backend: ${backend}"
    echo ""
    echo "  Models configured:"
    for purpose in "${purposes[@]}"; do
        local model_key=$(get_model_for_purpose "$purpose")
        if [[ -n "$model_key" ]]; then
            local model_name=$(get_model_name "$model_key")
            echo "    ${purpose}: ${model_name}"
        fi
    done
}

# Main
main() {
    info "Generating LiteLLM configuration from model registry..."
    echo ""
    
    case "$BACKEND" in
        mlx|vllm|cloud)
            generate_config_from_registry "$BACKEND"
            ;;
        *)
            error "Unknown backend: ${BACKEND}"
            echo "Valid backends: mlx, vllm, cloud"
            exit 1
            ;;
    esac
}

main
