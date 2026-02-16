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

# Check if yq is available, fall back to Python if not
parse_yaml() {
    local file="$1"
    local query="$2"
    
    if command -v yq &>/dev/null; then
        yq -r "$query" "$file" 2>/dev/null || echo ""
    else
        # Use Python as fallback
        python3 -c "
import yaml
import sys

with open('$file', 'r') as f:
    data = yaml.safe_load(f)

# Navigate the query path
query = '''$query'''.strip('.')
parts = query.split('.')
result = data
for part in parts:
    if result is None:
        break
    if part.startswith('[') and part.endswith(']'):
        # Array index
        idx = int(part[1:-1])
        result = result[idx] if isinstance(result, list) and len(result) > idx else None
    else:
        result = result.get(part) if isinstance(result, dict) else None

if result is not None:
    print(result)
" 2>/dev/null || echo ""
    fi
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

# Get purpose->model mapping based on environment
get_model_for_purpose() {
    local purpose="$1"
    local model_key
    
    # Use model_purposes_dev for development, model_purposes for staging/production
    if [[ "$ENVIRONMENT" == "development" || "$ENVIRONMENT" == "demo" || "$ENVIRONMENT" == "dev" ]]; then
        model_key=$(python3 -c "
import yaml
with open('$MODEL_REGISTRY', 'r') as f:
    data = yaml.safe_load(f)
purposes = data.get('model_purposes_dev', data.get('model_purposes', {}))
print(purposes.get('$purpose', ''))
" 2>/dev/null)
    else
        model_key=$(python3 -c "
import yaml
with open('$MODEL_REGISTRY', 'r') as f:
    data = yaml.safe_load(f)
purposes = data.get('model_purposes', {})
print(purposes.get('$purpose', ''))
" 2>/dev/null)
    fi
    
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

# Get api_base for a purpose and backend
get_api_base_for_purpose() {
    local backend="$1"
    local purpose="$2"

    if [[ "$backend" == "mlx" ]]; then
        case "$purpose" in
            transcribe) echo "http://host.docker.internal:8081/v1" ;;
            voice) echo "http://host.docker.internal:8082/v1" ;;
            image) echo "http://host.docker.internal:8083/v1" ;;
            *) echo "http://host.docker.internal:8080/v1" ;;
        esac
    elif [[ "$backend" == "vllm" ]]; then
        echo "http://vllm:8000/v1"
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
    local purposes=("default" "test" "fast" "agent" "chat" "frontier" "tool_calling" "image" "transcribe" "voice")
    
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
        
        # Build model entry based on provider
        if [[ "$provider" == "bedrock" ]]; then
            cat >> "$OUTPUT_FILE" << EOF
  - model_name: ${purpose}
    litellm_params:
      model: bedrock/${model_name}
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
        
        # Add model metadata if available
        if [[ -n "$description" || -n "$mode" ]]; then
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
