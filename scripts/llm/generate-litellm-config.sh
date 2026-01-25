#!/usr/bin/env bash
#
# Generate LiteLLM configuration based on detected backend
#
# Usage:
#   generate-litellm-config.sh              # Generate for detected backend
#   generate-litellm-config.sh mlx          # Generate for MLX
#   generate-litellm-config.sh vllm         # Generate for vLLM
#   generate-litellm-config.sh cloud        # Generate for AWS Bedrock
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source UI library
source "${SCRIPT_DIR}/../lib/ui.sh"

# Get backend and models
BACKEND="${1:-${LLM_BACKEND:-$(bash "${SCRIPT_DIR}/detect-backend.sh")}}"
TIER="${LLM_TIER:-$(bash "${SCRIPT_DIR}/get-memory-tier.sh" "$BACKEND")}"

OUTPUT_FILE="${REPO_ROOT}/config/litellm-generated.yaml"

generate_local_config() {
    local backend="$1"
    
    # Get models for current tier
    eval "$(bash "${SCRIPT_DIR}/get-models.sh" all)"
    
    local api_base
    if [[ "$backend" == "mlx" ]]; then
        # MLX runs on host, Docker accesses via host.docker.internal
        api_base="http://host.docker.internal:8080/v1"
    else
        # vLLM runs in container
        api_base="http://vllm:8000/v1"
    fi
    
    cat > "$OUTPUT_FILE" << EOF
# LiteLLM Configuration - Generated for ${backend} (${TIER} tier)
# Generated: $(date -Iseconds)
# DO NOT EDIT - regenerate with: scripts/llm/generate-litellm-config.sh

model_list:
  # Fast model - for simple tasks
  - model_name: fast
    litellm_params:
      model: openai/${LLM_MODEL_FAST}
      api_base: ${api_base}
      api_key: local
    model_info:
      description: "Fast local model for simple tasks"
      
  # Agent model - for complex reasoning
  - model_name: agent
    litellm_params:
      model: openai/${LLM_MODEL_AGENT}
      api_base: ${api_base}
      api_key: local
    model_info:
      description: "Agent model for complex reasoning"
      
  # Chat alias (same as agent)
  - model_name: chat
    litellm_params:
      model: openai/${LLM_MODEL_AGENT}
      api_base: ${api_base}
      api_key: local
      
  # Frontier model - best quality
  - model_name: frontier
    litellm_params:
      model: openai/${LLM_MODEL_FRONTIER}
      api_base: ${api_base}
      api_key: local
    model_info:
      description: "Frontier model for complex analysis"

general_settings:
  debug: true
  master_key: \${LITELLM_MASTER_KEY}

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
    echo "  Backend: ${backend}"
    echo "  Tier: ${TIER}"
    echo "  Fast: ${LLM_MODEL_FAST}"
    echo "  Agent: ${LLM_MODEL_AGENT}"
    echo "  Frontier: ${LLM_MODEL_FRONTIER}"
}

generate_cloud_config() {
    cat > "$OUTPUT_FILE" << 'EOF'
# LiteLLM Configuration - Generated for AWS Bedrock
# Generated: $(date -Iseconds)
# DO NOT EDIT - regenerate with: scripts/llm/generate-litellm-config.sh

model_list:
  # Fast model - Claude 3 Haiku
  - model_name: fast
    litellm_params:
      model: bedrock/anthropic.claude-3-haiku-20240307-v1:0
    model_info:
      description: "Fast Claude model for simple tasks"
      
  # Agent model - Claude 3.5 Sonnet
  - model_name: agent
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
    model_info:
      description: "Agent model for complex reasoning"
      
  # Chat alias (same as agent)
  - model_name: chat
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
      
  # Frontier model - Claude 3.5 Sonnet (best available)
  - model_name: frontier
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
    model_info:
      description: "Frontier model for complex analysis"

general_settings:
  debug: true
  master_key: ${LITELLM_MASTER_KEY}

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
    echo "  Backend: AWS Bedrock"
    echo "  Fast: Claude 3 Haiku"
    echo "  Agent: Claude 3.5 Sonnet"
    echo "  Frontier: Claude 3.5 Sonnet"
}

# Main
main() {
    info "Generating LiteLLM configuration..."
    echo ""
    
    case "$BACKEND" in
        mlx|vllm)
            generate_local_config "$BACKEND"
            ;;
        cloud)
            generate_cloud_config
            ;;
        *)
            error "Unknown backend: ${BACKEND}"
            exit 1
            ;;
    esac
}

main
