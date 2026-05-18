#!/usr/bin/env bash
#
# Busibox Test Script
#
# EXECUTION CONTEXT: Admin workstation or Proxmox host
# PURPOSE: Interactive test runner for infrastructure and service tests
#
# USAGE:
#   make test
#   OR
#   bash scripts/test.sh
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ANSIBLE_DIR="${REPO_ROOT}/provision/ansible"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"
source "${REPO_ROOT}/scripts/lib/services.sh"

# Detect container prefix from .env.* files or running containers
detect_container_prefix() {
    if [[ -n "${CONTAINER_PREFIX:-}" ]]; then
        echo "$CONTAINER_PREFIX"
        return
    fi
    for env_file in "${REPO_ROOT}/.env.dev" "${REPO_ROOT}/.env.local-dev" "${REPO_ROOT}/.env.demo"; do
        if [[ -f "$env_file" ]]; then
            local prefix
            prefix=$(grep -E '^CONTAINER_PREFIX=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' "'"'" || true)
            if [[ -n "$prefix" ]]; then
                echo "$prefix"
                return
            fi
        fi
    done
    local running
    running=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -oE '^[a-z]+-postgres$' | head -1 | sed 's/-postgres$//' || true)
    if [[ -n "$running" ]]; then
        echo "$running"
        return
    fi
    echo "dev"
}

DOCKER_PREFIX=$(detect_container_prefix)

# Detect vault password method.
# Priority: ANSIBLE_VAULT_PASSWORD env var > ~/.vault_pass file > interactive prompt.
get_vault_flags() {
    local vault_pass_from_env="${REPO_ROOT}/scripts/lib/vault-pass-from-env.sh"
    local vault_pass_file="$HOME/.vault_pass"

    if [ -n "${ANSIBLE_VAULT_PASSWORD:-}" ]; then
        echo "--vault-password-file ${vault_pass_from_env}"
    elif [ -f "$vault_pass_file" ]; then
        echo "--vault-password-file $vault_pass_file"
    else
        echo "--ask-vault-pass"
    fi
}

# Get container IP by name and environment
get_container_ip() {
    local container="$1"
    local env="$2"
    
    local network_base
    if [[ "$env" == "production" ]]; then
        network_base="10.96.200"
    else
        # staging (formerly "test") and docker both use 10.96.201.x
        network_base="10.96.201"
    fi
    
    case "$container" in
        proxy)    echo "${network_base}.200" ;;
        apps)     echo "${network_base}.201" ;;
        agent)    echo "${network_base}.202" ;;
        postgres) echo "${network_base}.203" ;;
        milvus)   echo "${network_base}.204" ;;
        minio)    echo "${network_base}.205" ;;
        data)   echo "${network_base}.206" ;;
        litellm)  echo "${network_base}.207" ;;
        vllm)     echo "${network_base}.208" ;;
        ollama)   echo "${network_base}.209" ;;
        authz)    echo "${network_base}.210" ;;
        search)   echo "${network_base}.204" ;;  # Search runs on milvus container
        bridge)   echo "${network_base}.210" ;;  # Bridge currently runs with authz-api
        config)   echo "${network_base}.210" ;;  # Config API runs on authz container
        *)        echo "" ;;
    esac
}

# Extract test credentials from vault using Python YAML parsing
# Usage: eval "$(extract_vault_credentials)"
extract_vault_credentials() {
    # Fast-path: if credentials were pre-injected by the TUI (which decrypts the
    # vault locally on the admin workstation and passes them over SSH), output
    # them directly without touching ansible-vault on this host.
    if [[ -n "${POSTGRES_PASSWORD:-}" && -n "${JWT_SECRET:-}" ]]; then
        echo "POSTGRES_PASSWORD='${POSTGRES_PASSWORD}'"
        echo "TEST_DB_PASSWORD='${POSTGRES_PASSWORD}'"
        echo "AUTHZ_MASTER_KEY='${AUTHZ_MASTER_KEY:-}'"
        echo "MINIO_ACCESS_KEY='${MINIO_ACCESS_KEY:-}'"
        echo "MINIO_SECRET_KEY='${MINIO_SECRET_KEY:-}'"
        echo "TEST_USER_ID='${TEST_USER_ID:-}'"
        echo "JWT_SECRET='${JWT_SECRET}'"
        return 0
    fi

    local vault_flags
    vault_flags="$(get_vault_flags)"

    # Resolve vault file:
    # 1. VAULT_PREFIX env var (set by TUI / make variable)
    # 2. Active profile's vault prefix (via profiles.sh)
    # 3. Any vault file that decrypts successfully (last resort probe)
    local vault_file=""
    local secrets_vars_dir="${ANSIBLE_DIR}/roles/secrets/vars"

    if [[ -n "${VAULT_PREFIX:-}" ]]; then
        vault_file="${secrets_vars_dir}/vault.${VAULT_PREFIX}.yml"
    fi

    if [[ -z "$vault_file" || ! -f "$vault_file" ]]; then
        # Try to source profiles.sh and get the active profile's vault prefix
        local profiles_sh="${REPO_ROOT}/scripts/lib/profiles.sh"
        if [[ -f "$profiles_sh" ]]; then
            # shellcheck source=/dev/null
            source "$profiles_sh" 2>/dev/null || true
            if type profile_get_vault_prefix &>/dev/null; then
                local vp
                vp=$(profile_get_vault_prefix 2>/dev/null)
                [[ -n "$vp" && "$vp" != "dev" ]] && vault_file="${secrets_vars_dir}/vault.${vp}.yml"
            fi
        fi
    fi

    if [[ -z "$vault_file" || ! -f "$vault_file" ]]; then
        # Last resort: probe all vault files and use the first one that decrypts
        while IFS= read -r -d '' candidate; do
            if ansible-vault view "$candidate" $vault_flags > /dev/null 2>&1; then
                vault_file="$candidate"
                break
            fi
        done < <(find "${secrets_vars_dir}" -maxdepth 1 -name "vault.*.yml" ! -name "vault.example.yml" -print0 2>/dev/null)
    fi

    if [[ -z "$vault_file" || ! -f "$vault_file" ]]; then
        echo "# No vault file found in ${secrets_vars_dir}" >&2
        return 1
    fi
    
    # Create temp file for decrypted vault
    local temp_vault
    temp_vault=$(mktemp)
    trap "rm -f $temp_vault" RETURN
    
    # Decrypt vault
    if ! ansible-vault view "$vault_file" $vault_flags > "$temp_vault" 2>/dev/null; then
        echo "# Failed to decrypt vault" >&2
        return 1
    fi
    
    # Extract credentials using Python
    python3 <<PYTHON_EOF
import yaml
import sys

try:
    with open('$temp_vault', 'r') as f:
        vault = yaml.safe_load(f) or {}
    
    secrets = vault.get('secrets', {})
    
    # PostgreSQL
    pg = secrets.get('postgresql', {})
    print(f"POSTGRES_PASSWORD='{pg.get('password', '')}'")
    print(f"TEST_DB_PASSWORD='{pg.get('password', '')}'")
    
    # Authz
    authz = secrets.get('authz', {})
    master_key = authz.get('master_key', '')
    print(f"AUTHZ_MASTER_KEY='{master_key}'")
    
    # MinIO
    minio = secrets.get('minio', {})
    print(f"MINIO_ACCESS_KEY='{minio.get('minio_access_key', '') or minio.get('access_key', '')}'")
    print(f"MINIO_SECRET_KEY='{minio.get('minio_secret_key', '') or minio.get('secret_key', '')}'")
    
    # Test credentials
    test_creds = secrets.get('test_credentials', {})
    print(f"TEST_USER_ID='{test_creds.get('test_user_id', '')}'")
    
    # JWT secret (used as bootstrap client secret)
    jwt_secret = secrets.get('jwt_secret', '')
    print(f"JWT_SECRET='{jwt_secret}'")
    
except Exception as e:
    print(f"# Error: {e}", file=sys.stderr)
    sys.exit(1)
PYTHON_EOF
}

# ============================================================================
# LLM Testing Functions
# ============================================================================

# Check and install jq if needed
check_jq() {
    if ! command -v jq &> /dev/null; then
        echo -e "${YELLOW}⚠ jq not found. Installing...${NC}"
        
        # Detect OS and install jq
        if [[ "$OSTYPE" == "linux-gnu"* ]]; then
            # Linux - try apt first, then yum
            if command -v apt-get &> /dev/null; then
                sudo apt-get update -qq > /dev/null 2>&1
                sudo apt-get install -y jq > /dev/null 2>&1 || {
                    error "Failed to install jq via apt-get"
                    echo "  Please install jq manually: sudo apt-get install jq"
                    return 1
                }
            elif command -v yum &> /dev/null; then
                sudo yum install -y jq > /dev/null 2>&1 || {
                    error "Failed to install jq via yum"
                    echo "  Please install jq manually: sudo yum install jq"
                    return 1
                }
            else
                error "Cannot determine package manager"
                echo "  Please install jq manually"
                return 1
            fi
        elif [[ "$OSTYPE" == "darwin"* ]]; then
            # macOS - use Homebrew
            if command -v brew &> /dev/null; then
                brew install jq > /dev/null 2>&1 || {
                    error "Failed to install jq via brew"
                    echo "  Please install jq manually: brew install jq"
                    return 1
                }
            else
                error "Homebrew not found"
                echo "  Please install jq manually: brew install jq"
                return 1
            fi
        else
            error "Unsupported OS: $OSTYPE"
            echo "  Please install jq manually"
            return 1
        fi
        
        success "jq installed successfully"
    fi
    return 0
}

# Get LiteLLM IP/hostname from service registry
get_litellm_ip() {
    local env="$1"
    # Use service registry (resolves via DNS hostname if available)
    get_service_ip "litellm" "$env" "proxmox"
}

# Get LiteLLM API key (from vault or default)
get_litellm_key() {
    local env="$1"
    local inv="inventory/${env}"
    
    # Try to get from vault, otherwise use default
    if [[ -f "${ANSIBLE_DIR}/${inv}/group_vars/all/vault.yml" ]]; then
        # Determine vault password flags
        if [[ -f ~/.vault_pass ]]; then
            # Use vault password file (non-interactive)
            ansible-vault view --vault-password-file ~/.vault_pass "${ANSIBLE_DIR}/${inv}/group_vars/all/vault.yml" 2>/dev/null | \
                grep -i "litellm.*key" | head -1 | sed 's/.*: *"\(.*\)".*/\1/' || \
                echo "sk-litellm-master-key-change-me"
        else
            # Prompt for vault password (interactive)
            ansible-vault view --ask-vault-pass "${ANSIBLE_DIR}/${inv}/group_vars/all/vault.yml" 2>/dev/null | \
                grep -i "litellm.*key" | head -1 | sed 's/.*: *"\(.*\)".*/\1/' || \
                echo "sk-litellm-master-key-change-me"
        fi
    else
        echo "sk-litellm-master-key-change-me"
    fi
}

# Check if LiteLLM is reachable
check_litellm() {
    local litellm_url="$1"
    if ! curl -sf "${litellm_url}/health" > /dev/null 2>&1; then
        error "LiteLLM is not reachable at ${litellm_url}"
        echo "  Make sure LiteLLM service is running"
        return 1
    fi
    return 0
}

# List models by purpose
list_models_by_purpose() {
    local env="$1"
    local inv="inventory/${env}"
    local model_registry="${ANSIBLE_DIR}/${inv}/group_vars/all/model_registry.yml"
    
    header "Models by Purpose" 70
    
    local litellm_ip=$(get_litellm_ip "$env")
    local litellm_url="http://${litellm_ip}:4000"
    local litellm_key=$(get_litellm_key "$env")
    
    if ! check_litellm "$litellm_url"; then
        return 1
    fi
    
    # Get available models from LiteLLM
    info "Fetching models from LiteLLM..."
    MODELS_JSON=$(curl -sf -H "Authorization: Bearer ${litellm_key}" \
        "${litellm_url}/v1/models" 2>/dev/null || echo '{"data":[]}')
    
    # Extract model IDs
    AVAILABLE_MODELS=$(echo "$MODELS_JSON" | jq -r '.data[].id' 2>/dev/null || echo "")
    
    if [[ -z "$AVAILABLE_MODELS" ]]; then
        warn "Could not fetch models from LiteLLM"
        echo "  Trying without authentication..."
        MODELS_JSON=$(curl -sf "${litellm_url}/v1/models" 2>/dev/null || echo '{"data":[]}')
        AVAILABLE_MODELS=$(echo "$MODELS_JSON" | jq -r '.data[].id' 2>/dev/null || echo "")
    fi
    
    # Read model registry
    if [[ ! -f "$model_registry" ]]; then
        error "Model registry not found: ${model_registry}"
        return 1
    fi
    
    echo ""
    success "Purpose-based Models (from model_registry.yml):"
    echo ""
    
    # Parse model registry and show purposes
    while IFS= read -r line; do
        if [[ "$line" =~ ^[[:space:]]*([a-z-]+): ]]; then
            PURPOSE="${BASH_REMATCH[1]}"
            # Skip if it's a nested key
            if [[ "$PURPOSE" == "model_purposes" ]]; then
                continue
            fi
            
            # Extract model name for this purpose
            MODEL_LINE=$(grep -A 10 "^  ${PURPOSE}:" "$model_registry" | grep "model:" | head -1 | sed 's/.*model: *"\(.*\)".*/\1/')
            MODEL_NAME_LINE=$(grep -A 10 "^  ${PURPOSE}:" "$model_registry" | grep "model_name:" | head -1 | sed 's/.*model_name: *"\(.*\)".*/\1/')
            DESC_LINE=$(grep -A 10 "^  ${PURPOSE}:" "$model_registry" | grep "description:" | head -1 | sed 's/.*description: *"\(.*\)".*/\1/')
            
            if [[ -n "$MODEL_LINE" ]]; then
                # Check if model is available in LiteLLM
                if echo "$AVAILABLE_MODELS" | grep -q "^${MODEL_LINE}$"; then
                    STATUS="${GREEN}✓${NC}"
                else
                    STATUS="${YELLOW}⚠${NC}"
                fi
                
                echo -e "  ${STATUS} ${CYAN}${PURPOSE}${NC}"
                echo -e "     Model: ${MODEL_LINE}"
                if [[ -n "$MODEL_NAME_LINE" ]]; then
                    echo -e "     Full: ${MODEL_NAME_LINE}"
                fi
                if [[ -n "$DESC_LINE" ]]; then
                    echo -e "     Desc: ${DESC_LINE}"
                fi
                echo ""
            fi
        fi
    done < "$model_registry"
    
    # Also show vLLM models if available
    success "Direct vLLM Models (if available):"
    echo ""
    if echo "$AVAILABLE_MODELS" | grep -q "vllm\|qwen\|phi"; then
        echo "$AVAILABLE_MODELS" | grep -E "vllm|qwen|phi" | while read -r model; do
            echo -e "  ${GREEN}✓${NC} ${model}"
        done
    else
        warn "No vLLM models found"
    fi
    echo ""
}

# Test chat completion for a purpose
test_purpose_chat() {
    local env="$1"
    local purpose="$2"
    local prompt="$3"
    local inv="inventory/${env}"
    local model_registry="${ANSIBLE_DIR}/${inv}/group_vars/all/model_registry.yml"
    
    header "Testing: ${purpose}" 70
    
    local litellm_ip=$(get_litellm_ip "$env")
    local litellm_url="http://${litellm_ip}:4000"
    local litellm_key=$(get_litellm_key "$env")
    
    if ! check_litellm "$litellm_url"; then
        return 1
    fi
    
    # Get model for this purpose
    MODEL=$(grep -A 10 "^  ${purpose}:" "$model_registry" | grep "model:" | head -1 | sed 's/.*model: *"\(.*\)".*/\1/')
    
    if [[ -z "$MODEL" ]]; then
        error "Purpose '${purpose}' not found in model registry"
        return 1
    fi
    
    info "Using model: ${MODEL}"
    info "Prompt: ${prompt}"
    echo ""
    
    # Make API call
    RESPONSE=$(curl -sf -X POST "${litellm_url}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${litellm_key}" \
        -d "{
            \"model\": \"${MODEL}\",
            \"messages\": [{\"role\": \"user\", \"content\": \"${prompt}\"}],
            \"max_tokens\": 500,
            \"temperature\": 0.7
        }" 2>/dev/null)
    
    if [[ -z "$RESPONSE" ]]; then
        error "Failed to get response from LiteLLM"
        return 1
    fi
    
    # Extract response
    CONTENT=$(echo "$RESPONSE" | jq -r '.choices[0].message.content' 2>/dev/null || echo "")
    USAGE=$(echo "$RESPONSE" | jq -r '.usage' 2>/dev/null || echo "")
    
    if [[ -z "$CONTENT" ]]; then
        error "No content in response"
        echo "Response: $RESPONSE"
        return 1
    fi
    
    success "Response:"
    echo "$CONTENT" | fold -w 80 -s
    echo ""
    
    if [[ -n "$USAGE" ]]; then
        info "Usage:"
        echo "$USAGE" | jq '.' 2>/dev/null || echo "$USAGE"
        echo ""
    fi
    
    return 0
}

# Test embedding for embedding purpose
test_purpose_embedding() {
    local env="$1"
    local inv="inventory/${env}"
    local model_registry="${ANSIBLE_DIR}/${inv}/group_vars/all/model_registry.yml"
    local purpose="embedding"
    
    header "Testing: ${purpose}" 70
    
    local litellm_ip=$(get_litellm_ip "$env")
    local litellm_url="http://${litellm_ip}:4000"
    local litellm_key=$(get_litellm_key "$env")
    
    if ! check_litellm "$litellm_url"; then
        return 1
    fi
    
    # Get model for this purpose
    MODEL=$(grep -A 10 "^  ${purpose}:" "$model_registry" | grep "model:" | head -1 | sed 's/.*model: *"\(.*\)".*/\1/')
    
    if [[ -z "$MODEL" ]]; then
        error "Purpose '${purpose}' not found in model registry"
        return 1
    fi
    
    info "Using model: ${MODEL}"
    info "Testing with sample text..."
    echo ""
    
    # Test embedding
    RESPONSE=$(curl -sf -X POST "${litellm_url}/v1/embeddings" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${litellm_key}" \
        -d "{
            \"model\": \"${MODEL}\",
            \"input\": \"This is a test sentence for embedding generation.\"
        }" 2>/dev/null)
    
    if [[ -z "$RESPONSE" ]]; then
        error "Failed to get response from LiteLLM"
        return 1
    fi
    
    # Extract embedding info
    DIMENSIONS=$(echo "$RESPONSE" | jq -r '.data[0].embedding | length' 2>/dev/null || echo "0")
    MODEL_USED=$(echo "$RESPONSE" | jq -r '.model' 2>/dev/null || echo "")
    USAGE=$(echo "$RESPONSE" | jq -r '.usage' 2>/dev/null || echo "")
    
    if [[ "$DIMENSIONS" == "0" ]]; then
        error "No embedding data in response"
        echo "Response: $RESPONSE"
        return 1
    fi
    
    success "Embedding generated successfully"
    echo -e "  Model: ${MODEL_USED}"
    echo -e "  Dimensions: ${DIMENSIONS}"
    
    if [[ -n "$USAGE" ]]; then
        info "Usage:"
        echo "$USAGE" | jq '.' 2>/dev/null || echo "$USAGE"
    fi
    echo ""
    
    return 0
}

# Test Bedrock (if configured)
test_bedrock() {
    local env="$1"
    
    header "Testing: AWS Bedrock" 70
    
    local litellm_ip=$(get_litellm_ip "$env")
    local litellm_url="http://${litellm_ip}:4000"
    local litellm_key=$(get_litellm_key "$env")
    
    # Check if bedrock models are available
    MODELS_JSON=$(curl -sf -H "Authorization: Bearer ${litellm_key}" \
        "${litellm_url}/v1/models" 2>/dev/null || echo '{"data":[]}')
    
    BEDROCK_MODELS=$(echo "$MODELS_JSON" | jq -r '.data[].id' 2>/dev/null | grep -i bedrock || echo "")
    
    if [[ -z "$BEDROCK_MODELS" ]]; then
        warn "Bedrock models not configured in LiteLLM"
        echo "  To configure Bedrock, add models to litellm config with 'bedrock/' prefix"
        return 1
    fi
    
    success "Bedrock models found:"
    echo "$BEDROCK_MODELS" | while read -r model; do
        echo "  - $model"
    done
    echo ""
    
    # Test first bedrock model
    FIRST_MODEL=$(echo "$BEDROCK_MODELS" | head -1)
    info "Testing with: ${FIRST_MODEL}"
    test_purpose_chat "$env" "$FIRST_MODEL" "Hello from Bedrock!"
}

# Test OpenAI (if configured)
test_openai() {
    local env="$1"
    
    header "Testing: OpenAI" 70
    
    local litellm_ip=$(get_litellm_ip "$env")
    local litellm_url="http://${litellm_ip}:4000"
    local litellm_key=$(get_litellm_key "$env")
    
    # Check if OpenAI models are available
    MODELS_JSON=$(curl -sf -H "Authorization: Bearer ${litellm_key}" \
        "${litellm_url}/v1/models" 2>/dev/null || echo '{"data":[]}')
    
    OPENAI_MODELS=$(echo "$MODELS_JSON" | jq -r '.data[].id' 2>/dev/null | grep -E "^gpt-|^o1-" || echo "")
    
    if [[ -z "$OPENAI_MODELS" ]]; then
        warn "OpenAI models not configured in LiteLLM"
        echo "  To configure OpenAI, add OPENAI_API_KEY to litellm environment"
        return 1
    fi
    
    success "OpenAI models found:"
    echo "$OPENAI_MODELS" | while read -r model; do
        echo "  - $model"
    done
    echo ""
    
    # Test first OpenAI model
    FIRST_MODEL=$(echo "$OPENAI_MODELS" | head -1)
    info "Testing with: ${FIRST_MODEL}"
    test_purpose_chat "$env" "$FIRST_MODEL" "Hello from OpenAI!"
}

# Infrastructure tests
run_infrastructure_tests() {
    local test_type="$1"
    
    header "Infrastructure Tests" 70
    
    if ! check_proxmox; then
        error "Infrastructure tests require Proxmox host"
        return 1
    fi
    
    echo ""
    info "Running $test_type infrastructure tests..."
    echo ""
    
    bash "${REPO_ROOT}/scripts/test/test-infrastructure.sh" "$test_type" || {
        error "Infrastructure tests failed"
        return 1
    }
    
    echo ""
    success "Infrastructure tests passed!"
    return 0
}

# LLM model tests menu
llm_tests_menu() {
    local env="$1"
    
    # Ensure jq is installed
    if ! check_jq; then
        error "jq is required for LLM testing"
        pause
        return 1
    fi
    
    while true; do
        echo ""
        menu "LLM Model Tests - $env" \
            "List models by purpose" \
            "Test fast model (quick chat)" \
            "Test embedding" \
            "Test research model (math/physics)" \
            "Test default model" \
            "Test chat model" \
            "Test cleanup model" \
            "Test parsing model" \
            "Test classify model" \
            "Test vision model" \
            "Test tool_calling model" \
            "Test AWS Bedrock (if configured)" \
            "Test OpenAI (if configured)" \
            "Back to Test Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-14]:${NC} ")" choice
        
        case $choice in
            1)
                list_models_by_purpose "$env"
                pause
                ;;
            2)
                test_purpose_chat "$env" "fast" "Say hello in one sentence."
                pause
                ;;
            3)
                test_purpose_embedding "$env"
                pause
                ;;
            4)
                local problem="Solve this step by step: A particle moves in a 2D plane with position vector r(t) = (3t^2, 4t^3) where t is time. Find the velocity vector, acceleration vector, and the magnitude of acceleration at t=2."
                test_purpose_chat "$env" "research" "$problem"
                pause
                ;;
            5)
                test_purpose_chat "$env" "default" "Provide a brief response demonstrating this model's capabilities."
                pause
                ;;
            6)
                test_purpose_chat "$env" "chat" "Provide a brief response demonstrating this model's capabilities."
                pause
                ;;
            7)
                test_purpose_chat "$env" "cleanup" "Provide a brief response demonstrating this model's capabilities."
                pause
                ;;
            8)
                test_purpose_chat "$env" "parsing" "Provide a brief response demonstrating this model's capabilities."
                pause
                ;;
            9)
                test_purpose_chat "$env" "classify" "Provide a brief response demonstrating this model's capabilities."
                pause
                ;;
            10)
                test_purpose_chat "$env" "vision" "Provide a brief response demonstrating this model's capabilities."
                pause
                ;;
            11)
                test_purpose_chat "$env" "tool_calling" "Provide a brief response demonstrating this model's capabilities."
                pause
                ;;
            12)
                test_bedrock "$env"
                pause
                ;;
            13)
                test_openai "$env"
                pause
                ;;
            14)
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-14."
                ;;
        esac
    done
}

# Data service tests
data_tests_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Data Service Tests - $env" \
            "Run Unit Tests" \
            "Run All Tests (Unit + Integration)" \
            "Run with Coverage" \
            "Test SIMPLE Extraction" \
            "Test LLM Cleanup Extraction" \
            "Test Marker Extraction" \
            "Test ColPali Extraction" \
            "Back to Test Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-8]:${NC} ")" choice
        
        cd "$ANSIBLE_DIR"
        local inv="inventory/${env}"
        
        case $choice in
            1)
                make test-data INV="$inv"
                pause
                ;;
            2)
                make test-data-all INV="$inv"
                pause
                ;;
            3)
                make test-data-coverage INV="$inv"
                pause
                ;;
            4)
                make test-extraction-simple INV="$inv"
                pause
                ;;
            5)
                make test-extraction-llm INV="$inv"
                pause
                ;;
            6)
                make test-extraction-marker INV="$inv"
                pause
                ;;
            7)
                make test-extraction-colpali INV="$inv"
                pause
                ;;
            8)
                cd "$REPO_ROOT"
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-8."
                ;;
        esac
        
        cd "$REPO_ROOT"
    done
}

# Search service tests
search_tests_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Search Service Tests - $env" \
            "Run Unit Tests" \
            "Run Integration Tests" \
            "Run with Coverage" \
            "Back to Test Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
        
        cd "$ANSIBLE_DIR"
        local inv="inventory/${env}"
        
        case $choice in
            1)
                make test-search-unit INV="$inv"
                pause
                ;;
            2)
                make test-search-integration INV="$inv"
                pause
                ;;
            3)
                make test-search-coverage INV="$inv"
                pause
                ;;
            4)
                cd "$REPO_ROOT"
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-4."
                ;;
        esac
        
        cd "$REPO_ROOT"
    done
}

# Security tests menu
security_tests_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Security Tests - $env Environment" \
            "Run All Security Tests" \
            "Authentication & Authorization Tests" \
            "Injection Attack Tests" \
            "Fuzzing Tests" \
            "Rate Limiting Tests" \
            "Run Security Tests with Slow Tests" \
            "Back to Service Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-7]:${NC} ")" choice
        
        local test_env="staging"
        if [[ "$env" == "production" ]]; then
            test_env="production"
        fi
        
        case $choice in
            1)
                header "All Security Tests" 70
                echo ""
                SECURITY_TEST_ENV="$test_env" bash "${REPO_ROOT}/tests/security/run_tests.sh"
                pause
                ;;
            2)
                header "Authentication Security Tests" 70
                echo ""
                SECURITY_TEST_ENV="$test_env" bash "${REPO_ROOT}/tests/security/run_tests.sh" --marker=auth
                pause
                ;;
            3)
                header "Injection Attack Tests" 70
                echo ""
                SECURITY_TEST_ENV="$test_env" bash "${REPO_ROOT}/tests/security/run_tests.sh" --marker=injection
                pause
                ;;
            4)
                header "Fuzzing Tests" 70
                echo ""
                SECURITY_TEST_ENV="$test_env" bash "${REPO_ROOT}/tests/security/run_tests.sh" --marker=fuzz
                pause
                ;;
            5)
                header "Rate Limiting Tests" 70
                echo ""
                SECURITY_TEST_ENV="$test_env" bash "${REPO_ROOT}/tests/security/run_tests.sh" --marker=rate_limit
                pause
                ;;
            6)
                header "All Security Tests (Including Slow)" 70
                echo ""
                warn "This will include slow tests like timing attacks and rate limiting."
                if confirm "Continue?"; then
                    SECURITY_TEST_ENV="$test_env" bash "${REPO_ROOT}/tests/security/run_tests.sh" --slow
                fi
                pause
                ;;
            7)
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-7."
                ;;
        esac
    done
}

# Service tests menu
service_tests_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Service Tests - $env Environment" \
            "LLM Model Tests (LiteLLM/vLLM)" \
            "Authz Service Tests" \
            "Data Service Tests" \
            "Search Service Tests" \
            "Agent Service Tests" \
            "Apps Service Tests" \
            "Security Tests" \
            "All Service Tests" \
            "Bootstrap Test Credentials (for local dev)" \
            "Back to Main Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-10]:${NC} ")" choice
        
        cd "$ANSIBLE_DIR"
        local inv="inventory/${env}"
        
        case $choice in
            1)
                llm_tests_menu "$env"
                ;;
            2)
                header "Authz Service Tests" 70
                echo ""
                if confirm "Run authz pytest on authz-lxc in $env?"; then
                    local vault_flags
                    vault_flags="$(get_vault_flags)"
                    
                    # Extract test credentials from vault using Python YAML parsing
                    info "Extracting test credentials from vault..."
                    local creds
                    creds=$(extract_vault_credentials 2>/dev/null) || {
                        error "Could not extract credentials from vault"
                        pause
                        continue
                    }
                    eval "$creds"
                    
                    # Get container IPs for the environment
                    local postgres_ip authz_ip
                    postgres_ip=$(get_container_ip postgres "$env")
                    authz_ip=$(get_container_ip authz "$env")
                    
                    if [ -z "$TEST_DB_PASSWORD" ]; then
                        error "Could not extract TEST_DB_PASSWORD from vault"
                        pause
                        continue
                    fi
                    
                    info "Using postgres at ${postgres_ip}, authz at ${authz_ip}"
                    
                    # ansible ad-hoc uses ANSIBLE_CONFIG; ensure we stay in ansible dir
                    # Sync test requirements and tests to authz-lxc
                    ANSIBLE_CONFIG="${ANSIBLE_DIR}/ansible.cfg" ansible -i "$inv" authz -m copy -a "src=${REPO_ROOT}/srv/authz/requirements.test.txt dest=/srv/authz/app/ mode=0644" $vault_flags || {
                        error "Failed to copy authz test requirements"
                    }
                    ANSIBLE_CONFIG="${ANSIBLE_DIR}/ansible.cfg" ansible -i "$inv" authz -m copy -a "src=${REPO_ROOT}/srv/authz/tests/ dest=/srv/authz/app/tests/ mode=0644" $vault_flags || {
                        error "Failed to copy authz tests"
                    }
                    
                    # Build environment variables for test run
                    # Authz uses dedicated "authz" database (not busibox_test)
                    local test_env="TEST_DB_USER=${db_user}"
                    test_env="${test_env} TEST_DB_PASSWORD=${TEST_DB_PASSWORD}"
                    test_env="${test_env} TEST_DB_NAME=authz"
                    test_env="${test_env} TEST_DB_HOST=${postgres_ip}"
                    test_env="${test_env} POSTGRES_HOST=${postgres_ip}"
                    test_env="${test_env} POSTGRES_USER=${db_user}"
                    test_env="${test_env} POSTGRES_DB=authz"
                    test_env="${test_env} POSTGRES_PASSWORD=${TEST_DB_PASSWORD}"
                    test_env="${test_env} AUTHZ_MASTER_KEY=${AUTHZ_MASTER_KEY}"
                    test_env="${test_env} AUTHZ_SERVICE_URL=http://${authz_ip}:8010"
                    
                    # Run tests with real database credentials
                    info "Running tests with real database integration..."
                    ANSIBLE_CONFIG="${ANSIBLE_DIR}/ansible.cfg" ansible -i "$inv" authz -m shell -a "bash -lc 'cd /srv/authz/app && source ../venv/bin/activate && pip install -q -r requirements.test.txt && export ${test_env} && pytest -v --tb=short'" $vault_flags || {
                        error "Authz tests failed"
                    }
                fi
                pause
                ;;
            3)
                data_tests_menu "$env"
                ;;
            4)
                search_tests_menu "$env"
                ;;
            5)
                header "Agent Service Tests" 70
                echo ""
                if confirm "Run agent tests on $env?"; then
                    make test-agent INV="$inv"
                fi
                pause
                ;;
            6)
                header "Apps Service Tests" 70
                echo ""
                if confirm "Run apps tests on $env?"; then
                    make test-apps INV="$inv"
                fi
                pause
                ;;
            7)
                cd "$REPO_ROOT"
                security_tests_menu "$env"
                cd "$ANSIBLE_DIR"
                ;;
            8)
                header "All Service Tests" 70
                echo ""
                if confirm "Run ALL service tests on $env? (This may take a while)"; then
                    make test-all INV="$inv"
                fi
                pause
                ;;
            9)
                header "Bootstrap Test Credentials" 70
                echo ""
                warn "This generates OAuth client credentials and admin tokens for local integration testing."
                echo ""
                info "Environment: ${env}"
                echo ""
                if confirm "Continue?"; then
                    make bootstrap-test-creds INV="$inv"
                    echo ""
                    success "Credentials generated!"
                    echo ""
                    warn "Copy the above variables to your busibox-app/.env file"
                    warn "Then run: cd busibox-app && npm test"
                    echo ""
                fi
                pause
                ;;
            10)
                cd "$REPO_ROOT"
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-10."
                ;;
        esac
        
        cd "$REPO_ROOT"
    done
}

# Local tests menu (run tests locally against container backends)
local_tests_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Local Tests - $env Environment (Run locally against containers)" \
            "Authz - Run authz tests locally" \
            "Data - Run data tests locally" \
            "Search - Run search tests locally" \
            "Agent - Run agent tests locally" \
            "All Services - Run all tests locally" \
            "Generate .env.local (for manual testing)" \
            "Back to Main Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-7]:${NC} ")" choice
        
        case $choice in
            1)
                header "Local Authz Tests" 70
                echo ""
                info "Running authz tests locally against $env containers..."
                info "This uses your local srv/authz code with remote databases/services."
                echo ""
                bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" authz "$env" || true
                pause
                ;;
            2)
                header "Local Data Tests" 70
                echo ""
                info "Running data tests locally against $env containers..."
                bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" data "$env" || true
                pause
                ;;
            3)
                header "Local Search Tests" 70
                echo ""
                info "Running search tests locally against $env containers..."
                bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" search "$env" || true
                pause
                ;;
            4)
                header "Local Agent Tests" 70
                echo ""
                info "Running agent tests locally against $env containers..."
                bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" agent "$env" || true
                pause
                ;;
            5)
                header "All Local Tests" 70
                echo ""
                warn "This will run all service tests locally. May take a while."
                if confirm "Continue?"; then
                    bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" all "$env" || true
                fi
                pause
                ;;
            6)
                header "Generate .env.local Files" 70
                echo ""
                info "Generating environment files for manual local testing..."
                echo ""
                for svc in authz data search agent bridge; do
                    bash "${REPO_ROOT}/scripts/test/generate-local-test-env.sh" "$svc" "$env" 2>/dev/null || true
                done
                echo ""
                success "Environment files generated!"
                echo ""
                info "Files created:"
                echo "  - srv/authz/.env.local"
                echo "  - srv/data/.env.local"
                echo "  - srv/search/.env.local"
                echo "  - srv/agent/.env.local"
                echo "  - srv/bridge/.env.local"
                echo ""
                info "To use: source srv/<service>/.env.local && pytest tests/ -v"
                pause
                ;;
            7)
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-7."
                ;;
        esac
    done
}

# Main test menu
main_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Busibox Test Suite - $env Environment" \
            "Bootstrap Test Credentials (Required for most tests)" \
            "Infrastructure Tests (Full Suite)" \
            "Infrastructure Tests (Provision Only)" \
            "Infrastructure Tests (Verify Only)" \
            "Service Tests (run on containers)" \
            "Local Tests (run locally against containers)" \
            "All Tests (Infrastructure + Services)" \
            "Exit"
        
        read -p "$(echo -e "${BOLD}Select option [1-8]:${NC} ")" choice
        
        case $choice in
            1)
                header "Bootstrap Test Credentials" 70
                echo ""
                info "This will create or retrieve test credentials for integration testing"
                info "Credentials are stored in Ansible vault and available to all services"
                echo ""
                
                if confirm "Bootstrap test credentials for $env environment?"; then
                    cd "$ANSIBLE_DIR"
                    make bootstrap-test-creds INV="inventory/${env}"
                    cd "$REPO_ROOT"
                    echo ""
                    success "Test credentials are ready!"
                    echo ""
                    info "Copy the .env variables from the output above to your local test environment"
                fi
                pause
                ;;
            2)
                if confirm "Run full infrastructure test suite?"; then
                    run_infrastructure_tests "full"
                fi
                pause
                ;;
            3)
                if confirm "Run infrastructure provisioning tests?"; then
                    run_infrastructure_tests "provision"
                fi
                pause
                ;;
            4)
                if confirm "Run infrastructure verification tests?"; then
                    run_infrastructure_tests "verify"
                fi
                pause
                ;;
            5)
                service_tests_menu "$env"
                ;;
            6)
                local_tests_menu "$env"
                ;;
            7)
                header "All Tests" 70
                echo ""
                warn "This will run infrastructure tests followed by all service tests"
                warn "This may take 30-60 minutes to complete"
                echo ""
                
                if confirm "Run ALL tests?" "n"; then
                    if check_proxmox; then
                        run_infrastructure_tests "full"
                    else
                        warn "Skipping infrastructure tests (not on Proxmox host)"
                    fi
                    
                    echo ""
                    info "Running service tests..."
                    cd "$ANSIBLE_DIR"
                    make test-all INV="inventory/${env}"
                    cd "$REPO_ROOT"
                fi
                pause
                ;;
            8)
                echo ""
                info "Exiting..."
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-8."
                ;;
        esac
    done
}

# Ensure the authz service on the given IP has AUTHZ_TEST_MODE_ENABLED=true.
# If the flag is missing or false, it is added/updated and the service is
# restarted.  This must run before any test suite that calls AuthTestClient so
# X-Test-Mode: true headers actually route to the test DB.
ensure_authz_test_mode() {
    local authz_ip="$1"
    info "Checking authz test mode on ${authz_ip}..."

    # Read current value from the deployed .env
    local current
    current=$(ssh "root@${authz_ip}" "grep -E '^AUTHZ_TEST_MODE_ENABLED=' /srv/authz/.env 2>/dev/null || true")

    if [[ "$current" == "AUTHZ_TEST_MODE_ENABLED=true" ]]; then
        info "authz test mode already enabled — no restart needed"
        return 0
    fi

    warn "Enabling AUTHZ_TEST_MODE_ENABLED=true on authz service (restart required)..."

    # Add or update the flag then restart
    ssh "root@${authz_ip}" "
        if grep -qE '^AUTHZ_TEST_MODE_ENABLED=' /srv/authz/.env 2>/dev/null; then
            sed -i 's|^AUTHZ_TEST_MODE_ENABLED=.*|AUTHZ_TEST_MODE_ENABLED=true|' /srv/authz/.env
        else
            echo 'AUTHZ_TEST_MODE_ENABLED=true' >> /srv/authz/.env
        fi
        systemctl restart authz-api
        # Wait up to 30 s for the service to come back
        for i in \$(seq 1 30); do
            if curl -sf http://localhost:8010/health/live > /dev/null 2>&1; then
                echo '[authz] service healthy after restart'
                exit 0
            fi
            sleep 1
        done
        echo '[authz] WARNING: service not healthy after 30 s' >&2
        exit 1
    " || {
        error "Failed to enable AUTHZ_TEST_MODE_ENABLED on authz service at ${authz_ip}"
        exit 1
    }

    success "authz test mode enabled and service restarted"
}

# Proxmox: agent-lxc cannot restart litellm-lxc (no docker, no SSH keys).
# When LLM key-restore tests are selected, run prepare → restart litellm from the
# test runner host → verify, then exclude those tests from the main pytest pass.
orchestrate_llm_key_restore_cycle() {
    local agent_ip="$1"
    local litellm_ip="$2"
    local test_env="$3"
    local pytest_path="$4"
    local pytest_extra="$5"
    local pytest_base_flags="$6"

    LLM_RESTART_ORCHESTRATED=0

    local collect_cmd
    collect_cmd="cd /srv/agent && source .venv/bin/activate && source .env 2>/dev/null || true && export ${test_env} && python -m pytest ${pytest_path} ${pytest_extra} --collect-only -q 2>/dev/null"
    local collected
    collected=$(ssh -o ConnectTimeout=15 "root@${agent_ip}" "${collect_cmd}" 2>/dev/null || true)

    if ! echo "${collected}" | grep -qE 'test_keys_survive_litellm_restart'; then
        return 0
    fi

    info "Orchestrating LLM key-restore restart (test runner → litellm-lxc systemctl restart)..."

    local agent_pytest="cd /srv/agent && source .venv/bin/activate && source .env 2>/dev/null || true && export ${test_env}"

    if echo "${collected}" | grep -qE '::test_keys_survive_litellm_restart_prepare$|::test_keys_survive_litellm_restart$'; then
        info "  Step 1/3: verify providers and config-api persistence..."
        if ! ssh -o ConnectTimeout=15 "root@${agent_ip}" \
            "${agent_pytest} && python -m pytest tests/integration/test_llm_key_restore.py::TestLLMKeyRestoreAfterRestart::test_keys_survive_litellm_restart_prepare ${pytest_base_flags}"; then
            error "LLM key-restore prepare step failed"
            return 1
        fi

        info "  Step 2/3: restarting LiteLLM on ${litellm_ip}..."
        if ! ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 "root@${litellm_ip}" "systemctl restart litellm"; then
            error "Failed to restart litellm on ${litellm_ip} (check SSH from test runner)"
            return 1
        fi
        sleep 5
    fi

    if echo "${collected}" | grep -qE '::test_keys_survive_litellm_restart_verify$|::test_keys_survive_litellm_restart$'; then
        info "  Step 3/3: verify keys restored after restart..."
        if ! ssh -o ConnectTimeout=15 "root@${agent_ip}" \
            "${agent_pytest} && export LITELLM_RESTART_EXTERNAL=1 && python -m pytest tests/integration/test_llm_key_restore.py::TestLLMKeyRestoreAfterRestart::test_keys_survive_litellm_restart_verify ${pytest_base_flags}"; then
            error "LLM key-restore verify step failed"
            return 1
        fi
    fi

    LLM_RESTART_ORCHESTRATED=1
    success "LLM key-restore restart cycle completed"
    return 0
}

# Run tests on container (non-interactive)
run_container_tests() {
    local service="$1"
    local env="$2"
    
    local vault_flags
    vault_flags="$(get_vault_flags)"
    
    # Extract credentials from vault
    info "Extracting test credentials from vault..."
    local creds
    creds=$(extract_vault_credentials 2>/dev/null) || {
        error "Could not extract credentials from vault"
        exit 1
    }
    eval "$creds"
    
    # Get container IPs
    local postgres_ip authz_ip data_ip search_ip agent_ip bridge_ip config_ip minio_ip milvus_ip
    postgres_ip=$(get_container_ip postgres "$env")
    authz_ip=$(get_container_ip authz "$env")
    data_ip=$(get_container_ip data "$env")
    search_ip=$(get_container_ip search "$env")
    agent_ip=$(get_container_ip agent "$env")
    bridge_ip=$(get_container_ip bridge "$env")
    config_ip=$(get_container_ip config "$env")
    minio_ip=$(get_container_ip minio "$env")
    milvus_ip=$(get_container_ip milvus "$env")

    # ALWAYS ensure authz is running in test mode before running any test suite.
    # Without this, X-Test-Mode: true headers are silently ignored and tests hit
    # the production database.
    ensure_authz_test_mode "${authz_ip}"
    
    # Database configuration for pytest
    # NOTE: Pytest tests run against isolated test databases owned by busibox_test_user:
    #   - test_authz (for authz service tests)
    #   - test_files (for data/search service tests)
    #   - test_agent (for agent service tests)
    #
    # The test user has identical table structures but completely isolated data.
    # Environment (staging/production) only determines which network/containers we SSH to.
    local db_user db_password
    db_user="busibox_test_user"
    db_password="${PYTEST_DB_PASSWORD:-testpassword}"

    # Well-known bootstrap test user (created automatically by authz on startup
    # when test mode is enabled).  AuthTestClient uses this ID by default.
    # Tests that need a real user use auth_client.get_token() which bootstraps
    # the user via magic link -- no external bootstrap script required.
    local test_user_id="${TEST_USER_ID:-00000000-0000-0000-0000-000000000001}"
    
    # Resolve pytest test path and extra flags from PYTEST_ARGS.
    # Supports shorthand patterns from the TUI / CLI:
    #   llm                      -> pytest tests/ -k llm
    #   integration/test_llm     -> pytest tests/integration/test_llm
    #   tests/unit/test_foo.py   -> pytest tests/unit/test_foo.py (full path)
    #   -k "foo and not slow"    -> pytest tests/ -k "foo and not slow"
    local _pytest_path _pytest_extra
    local _raw="${PYTEST_ARGS:-}"
    if [[ -z "$_raw" ]]; then
        _pytest_path="tests/"
        _pytest_extra=""
    elif [[ "$_raw" == -* ]]; then
        _pytest_path="tests/"
        _pytest_extra="$_raw"
    elif [[ "$_raw" == tests/* ]]; then
        _pytest_path="$_raw"
        _pytest_extra=""
    elif [[ "$_raw" == */* ]]; then
        _pytest_path="tests/${_raw}"
        _pytest_extra=""
    else
        _pytest_path="tests/"
        _pytest_extra="-k ${_raw}"
    fi

    # Default pytest flags: --stepwise stops at first failure and on the next run
    # continues from that test (skips already-passed tests in .pytest_cache).
    # Set PYTEST_STEPWISE_RESET=1 to clear stepwise state (fresh full run).
    # PYTEST_ARGS can override by including its own --tb / --stepwise / -v flags.
    local _pytest_stepwise_flags="--stepwise"
    if [[ "${PYTEST_STEPWISE_RESET:-}" == "1" ]]; then
        _pytest_stepwise_flags="--stepwise-reset --stepwise"
    fi
    local _pytest_base_flags="${_pytest_stepwise_flags} -v --tb=long"

    case "$service" in
        authz)
            header "Authz Service Tests" 70
            info "Running authz tests on ${authz_ip}..."
            
            # Build environment variables
            # Pytest uses test_authz database (owned by busibox_test_user)
            local test_env="TEST_DB_USER=${db_user}"
            test_env="${test_env} TEST_DB_PASSWORD=${db_password}"
            test_env="${test_env} TEST_DB_NAME=test_authz"
            test_env="${test_env} TEST_DB_HOST=${postgres_ip}"
            test_env="${test_env} POSTGRES_HOST=${postgres_ip}"
            test_env="${test_env} POSTGRES_USER=${db_user}"
            test_env="${test_env} POSTGRES_PASSWORD=${db_password}"
            test_env="${test_env} POSTGRES_DB=test_authz"
            test_env="${test_env} AUTHZ_MASTER_KEY=${AUTHZ_MASTER_KEY}"
            test_env="${test_env} AUTHZ_SERVICE_URL=http://${authz_ip}:8010"
            test_env="${test_env} TEST_AUTHZ_URL=http://${authz_ip}:8010"
            test_env="${test_env} AUTHZ_JWKS_URL=http://${authz_ip}:8010/.well-known/jwks.json"
            test_env="${test_env} AUTHZ_TEST_MODE_ENABLED=true"
            test_env="${test_env} TEST_USER_ID=${test_user_id}"
            
            # Run tests via SSH
            if ssh "root@${authz_ip}" "cd /srv/authz/app && source ../venv/bin/activate && export PYTHONPATH=/srv/authz/app/src && source /srv/authz/.env 2>/dev/null || true && export ${test_env} && python -m pytest ${_pytest_path} ${_pytest_base_flags} ${_pytest_extra}"; then
                success "Authz tests passed!"
                save_test_result "authz" "passed"
            else
                error "Authz tests failed"
                echo ""
                warn "Press 'r' in the TUI to resume from the failure (--stepwise), or check pytest filter above"
                echo ""
                save_test_result "authz" "failed"
                # Don't exit - continue to show summary
                return 1
            fi
            ;;
        data)
            header "Data Service Tests" 70
            info "Running data tests on ${data_ip}..."

            # Sync latest test files from repo to container (no full redeploy needed)
            info "Syncing data tests to container..."
            rsync -rltz --delete --no-owner --no-group \
                "${REPO_ROOT}/srv/data/tests/" \
                "root@${data_ip}:/srv/data/tests/" || {
                error "Failed to sync data tests"
                return 1
            }

            # Pytest uses test_files database (owned by busibox_test_user)
            # AuthTestClient bootstraps the test user automatically via magic link.
            local test_env="POSTGRES_HOST=${postgres_ip}"
            test_env="${test_env} POSTGRES_USER=${db_user}"
            test_env="${test_env} POSTGRES_PASSWORD=${db_password}"
            test_env="${test_env} POSTGRES_DB=test_files"
            test_env="${test_env} MINIO_HOST=${minio_ip}"
            test_env="${test_env} MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY}"
            test_env="${test_env} MINIO_SECRET_KEY=${MINIO_SECRET_KEY}"
            test_env="${test_env} AUTHZ_URL=http://${authz_ip}:8010"
            test_env="${test_env} AUTHZ_JWKS_URL=http://${authz_ip}:8010/.well-known/jwks.json"
            test_env="${test_env} AUTHZ_TEST_MODE_ENABLED=true"
            test_env="${test_env} TEST_USER_ID=${test_user_id}"
            
            if ssh "root@${data_ip}" "cd /srv/data && source venv/bin/activate && export PYTHONPATH=/srv/data/src && source .env 2>/dev/null || true && export ${test_env} && python -m pytest ${_pytest_path} ${_pytest_base_flags} ${_pytest_extra}"; then
                success "Data tests passed!"
                save_test_result "data" "passed"
            else
                error "Data tests failed"
                echo ""
                warn "Press 'r' in the TUI to resume from the failure (--stepwise), or check pytest filter above"
                echo ""
                save_test_result "data" "failed"
                # Don't exit - continue to show summary
                return 1
            fi
            ;;
        search)
            header "Search Service Tests" 70
            info "Running search tests on ${search_ip}..."

            # Sync latest test files from repo to container (no full redeploy needed)
            info "Syncing search tests to container..."
            rsync -rltz --delete --no-owner --no-group \
                "${REPO_ROOT}/srv/search/tests/" \
                "root@${search_ip}:/opt/search/tests/" || {
                error "Failed to sync search tests"
                return 1
            }

            # Pytest uses test_files database (owned by busibox_test_user)
            # AuthTestClient bootstraps the test user automatically via magic link.
            local test_env="POSTGRES_HOST=${postgres_ip}"
            test_env="${test_env} POSTGRES_USER=${db_user}"
            test_env="${test_env} POSTGRES_PASSWORD=${db_password}"
            test_env="${test_env} POSTGRES_DB=test_files"
            test_env="${test_env} MILVUS_HOST=${milvus_ip}"
            test_env="${test_env} AUTHZ_URL=http://${authz_ip}:8010"
            test_env="${test_env} AUTHZ_JWKS_URL=http://${authz_ip}:8010/.well-known/jwks.json"
            test_env="${test_env} AUTHZ_TEST_MODE_ENABLED=true"
            test_env="${test_env} TEST_USER_ID=${test_user_id}"
            
            # Search service is deployed to /opt/search on milvus container
            if ssh "root@${search_ip}" "cd /opt/search && source venv/bin/activate && export PYTHONPATH=/opt/search/src && source .env 2>/dev/null || true && export ${test_env} && python -m pytest ${_pytest_path} ${_pytest_base_flags} ${_pytest_extra}"; then
                success "Search tests passed!"
                save_test_result "search" "passed"
            else
                error "Search tests failed"
                echo ""
                warn "Press 'r' in the TUI to resume from the failure (--stepwise), or check pytest filter above"
                echo ""
                save_test_result "search" "failed"
                # Don't exit - continue to show summary
                return 1
            fi
            ;;
        agent)
            header "Agent Service Tests" 70
            info "Running agent tests on ${agent_ip}..."

            # Sync latest test files from repo to container (no full redeploy needed)
            info "Syncing agent tests to container..."
            rsync -rltz --delete --no-owner --no-group \
                "${REPO_ROOT}/srv/agent/tests/" \
                "root@${agent_ip}:/srv/agent/tests/" || {
                error "Failed to sync agent tests"
                return 1
            }
            rsync -rltz --delete --no-owner --no-group \
                "${REPO_ROOT}/srv/shared/busibox_common/" \
                "root@${agent_ip}:/srv/agent/busibox_common/" || {
                error "Failed to sync busibox_common for agent tests"
                return 1
            }

            # Pytest uses test_agent database (not the production agent database)
            # Also set TEST_DATABASE_URL which the agent conftest.py checks first
            # AuthTestClient bootstraps the test user automatically via magic link.
            local agent_test_db_url="postgresql+asyncpg://${db_user}:${db_password}@${postgres_ip}:5432/test_agent"
            local test_env="POSTGRES_HOST=${postgres_ip}"
            test_env="${test_env} POSTGRES_USER=${db_user}"
            test_env="${test_env} POSTGRES_PASSWORD=${db_password}"
            test_env="${test_env} POSTGRES_DB=test_agent"
            test_env="${test_env} TEST_DATABASE_URL=${agent_test_db_url}"
            test_env="${test_env} AUTHZ_URL=http://${authz_ip}:8010"
            test_env="${test_env} AUTHZ_JWKS_URL=http://${authz_ip}:8010/.well-known/jwks.json"
            test_env="${test_env} AUTHZ_TEST_MODE_ENABLED=true"
            test_env="${test_env} TEST_USER_ID=${test_user_id}"
            test_env="${test_env} CONFIG_API_URL=http://${authz_ip}:8012"
            local litellm_ip
            litellm_ip=$(get_container_ip litellm "$env")
            test_env="${test_env} LITELLM_HOST=${litellm_ip}"
            test_env="${test_env} DATA_URL=http://${data_ip}:8000"
            test_env="${test_env} SEARCH_URL=http://${search_ip}:8003"  # Search is on port 8003

            # LLM key-restore: restart litellm from test runner (agent-lxc has no docker/SSH)
            if ! orchestrate_llm_key_restore_cycle \
                "${agent_ip}" "${litellm_ip}" "${test_env}" \
                "${_pytest_path}" "${_pytest_extra}" "${_pytest_base_flags}"; then
                save_test_result "agent" "failed"
                return 1
            fi

            # Build pytest -k expression (quote multi-word expressions for SSH)
            local _agent_pytest_k_expr=""
            if [[ "${_pytest_extra}" == -k\ * ]]; then
                _agent_pytest_k_expr="${_pytest_extra#-k }"
            fi
            local _agent_pytest_invoke="${_pytest_path} ${_pytest_base_flags}"
            if [[ "${LLM_RESTART_ORCHESTRATED:-0}" == "1" ]]; then
                test_env="${test_env} LLM_RESTART_ORCHESTRATED=1"
                if [[ -n "${_agent_pytest_k_expr}" ]]; then
                    _agent_pytest_k_expr="${_agent_pytest_k_expr} and not keys_survive"
                elif [[ "${_pytest_path}" == *"test_llm_key_restore"* ]]; then
                    _agent_pytest_k_expr="not keys_survive"
                fi
            fi
            if [[ -n "${_agent_pytest_k_expr}" ]]; then
                _agent_pytest_invoke="${_agent_pytest_invoke} -k '${_agent_pytest_k_expr}'"
            elif [[ -n "${_pytest_extra}" ]]; then
                _agent_pytest_invoke="${_agent_pytest_invoke} ${_pytest_extra}"
            fi

            # Agent uses .venv not venv
            if ssh "root@${agent_ip}" "cd /srv/agent && source .venv/bin/activate && source .env 2>/dev/null || true && export ${test_env} && python -m pytest ${_agent_pytest_invoke}"; then
                success "Agent tests passed!"
                save_test_result "agent" "passed"
            else
                error "Agent tests failed"
                echo ""
                warn "Press 'r' in the TUI to resume from the failure (--stepwise), or check pytest filter above"
                echo ""
                save_test_result "agent" "failed"
                # Don't exit - continue to show summary
                return 1
            fi
            ;;
        bridge)
            header "Bridge Service Tests" 70
            info "Running bridge tests on ${bridge_ip}..."

            # Sync latest test files from repo to container (no full redeploy needed)
            info "Syncing bridge tests to container..."
            rsync -rltz --delete --no-owner --no-group \
                "${REPO_ROOT}/srv/bridge/tests/" \
                "root@${bridge_ip}:/srv/bridge/tests/" || {
                error "Failed to sync bridge tests"
                return 1
            }

            local test_env="BRIDGE_API_URL=http://${bridge_ip}:8081"
            test_env="${test_env} BRIDGE_API_PORT=8081"
            test_env="${test_env} AUTHZ_URL=http://${authz_ip}:8010"
            test_env="${test_env} AUTHZ_TEST_MODE_ENABLED=true"
            test_env="${test_env} TEST_USER_ID=${test_user_id}"

            if ssh "root@${bridge_ip}" "cd /srv/bridge && source venv/bin/activate && source .env 2>/dev/null || true && export ${test_env} && python -m pytest ${_pytest_path} ${_pytest_base_flags} ${_pytest_extra}"; then
                success "Bridge tests passed!"
                save_test_result "bridge" "passed"
            else
                error "Bridge tests failed"
                echo ""
                warn "Press 'r' in the TUI to resume from the failure (--stepwise), or check pytest filter above"
                echo ""
                save_test_result "bridge" "failed"
                return 1
            fi
            ;;
        config)
            header "Config API Tests" 70
            info "Running config-api tests on ${config_ip}..."

            # Sync latest test files from repo to container (no full redeploy needed)
            info "Syncing config-api tests to container..."
            rsync -rltz --delete --no-owner --no-group \
                "${REPO_ROOT}/srv/config/tests/" \
                "root@${config_ip}:/opt/config/app/tests/" || {
                error "Failed to sync config-api tests"
                return 1
            }

            local test_env="CONFIG_API_URL=http://${config_ip}:8012"
            test_env="${test_env} POSTGRES_HOST=${postgres_ip}"
            test_env="${test_env} POSTGRES_USER=${db_user}"
            test_env="${test_env} POSTGRES_PASSWORD=${db_password}"
            test_env="${test_env} POSTGRES_DB=test_config"
            test_env="${test_env} AUTHZ_URL=http://${authz_ip}:8010"
            test_env="${test_env} AUTHZ_JWKS_URL=http://${authz_ip}:8010/.well-known/jwks.json"
            test_env="${test_env} AUTHZ_TEST_MODE_ENABLED=true"
            test_env="${test_env} TEST_USER_ID=${test_user_id}"

            if ssh "root@${config_ip}" "cd /opt/config/app && source /opt/config/venv/bin/activate && source /opt/config/.env 2>/dev/null || true && export ${test_env} && python -m pytest ${_pytest_path} ${_pytest_base_flags} ${_pytest_extra}"; then
                success "Config API tests passed!"
                save_test_result "config" "passed"
            else
                error "Config API tests failed"
                echo ""
                warn "Press 'r' in the TUI to resume from the failure (--stepwise), or check pytest filter above"
                echo ""
                save_test_result "config" "failed"
                return 1
            fi
            ;;
        all)
            local failed_services=()
            for svc in authz data search agent bridge config; do
                if ! run_container_tests "$svc" "$env"; then
                    failed_services+=("$svc")
                fi
            done
            
            echo ""
            echo "═══════════════════════════════════════════════════════════════════════"
            echo "Test Summary"
            echo "═══════════════════════════════════════════════════════════════════════"
            echo ""
            
            # Show passed services
            local passed_services=($(get_passed_services))
            if [[ ${#passed_services[@]} -gt 0 ]]; then
                success "Passed services: ${passed_services[*]}"
            fi
            
            # Show failed services
            if [[ ${#failed_services[@]} -eq 0 ]]; then
                success "All service tests passed!"
            else
                error "Failed services: ${failed_services[*]}"
                echo ""
                warn "Review output above for pytest filters to rerun failed tests"
                warn "In the Busibox TUI: press 'r' to resume from the last failure (--stepwise)"
                return 1
            fi
            ;;
        *)
            error "Unknown service: $service"
            echo "Available services: authz, data, search, agent, bridge, all"
            exit 1
            ;;
    esac
}

# Check if test databases are bootstrapped
check_test_db_status() {
    local result
    result=$(docker exec "${DOCKER_PREFIX}-postgres" psql -U busibox_test_user -d test_authz -t -c \
        "SELECT COUNT(*) FROM authz_signing_keys WHERE is_active = true;" 2>/dev/null | tr -d ' ' || echo "0")
    
    if [[ "$result" -gt 0 ]]; then
        return 0  # Bootstrapped
    else
        return 1  # Not bootstrapped
    fi
}

# Docker test menu (for local Docker environment)
docker_test_menu() {
    while true; do
        echo ""
        
        # Check test database status
        local test_db_status
        if check_test_db_status; then
            test_db_status="${GREEN}✓ Ready${NC}"
            local tests_enabled=true
        else
            test_db_status="${YELLOW}⚠ Not Initialized${NC}"
            local tests_enabled=false
        fi
        
        echo -e "  Test Databases: ${test_db_status}"
        echo ""
        
        if [[ "$tests_enabled" == "true" ]]; then
            menu "Docker Test Suite - Local Development" \
                "Authz - Run authz tests" \
                "Data - Run data tests" \
                "Search - Run search tests" \
                "Agent - Run agent tests" \
                "All Services - Run all tests" \
                "Reinitialize Test Databases" \
                "Check Docker Services Status" \
                "View Docker Logs" \
                "Exit"
            
            read -p "$(echo -e "${BOLD}Select option [1-9]:${NC} ")" choice
        else
            menu "Docker Test Suite - Local Development" \
                "Initialize Test Databases (REQUIRED)" \
                "Check Docker Services Status" \
                "View Docker Logs" \
                "Exit"
            
            read -p "$(echo -e "${BOLD}Select option [1-4]:${NC} ")" choice
            
            # Remap choices when tests disabled
            case $choice in
                1) choice="init" ;;
                2) choice="status" ;;
                3) choice="logs" ;;
                4) choice="exit" ;;
            esac
        fi
        
        case $choice in
            1)
                header "Docker Authz Tests" 70
                echo ""
                info "Running authz tests against local Docker services..."
                echo ""
                bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" authz docker || true
                pause
                ;;
            2)
                header "Docker Data Tests" 70
                echo ""
                info "Running data tests against local Docker services..."
                echo ""
                bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" data docker || true
                pause
                ;;
            3)
                header "Docker Search Tests" 70
                echo ""
                info "Running search tests against local Docker services..."
                echo ""
                bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" search docker || true
                pause
                ;;
            4)
                header "Docker Agent Tests" 70
                echo ""
                info "Running agent tests against local Docker services..."
                echo ""
                bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" agent docker || true
                pause
                ;;
            5)
                header "All Docker Tests" 70
                echo ""
                warn "This will run all service tests. May take a while."
                if confirm "Continue?"; then
                    bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" all docker || true
                fi
                pause
                ;;
            6|init)
                header "Initialize Test Databases" 70
                echo ""
                info "Bootstrapping test databases with schema and OAuth clients..."
                echo ""
                (cd "$REPO_ROOT" && make test-db-init) || {
                    error "Failed to initialize test databases"
                }
                echo ""
                if check_test_db_status; then
                    success "Test databases initialized successfully!"
                else
                    error "Test database initialization may have failed. Check logs above."
                fi
                pause
                ;;
            7|status)
                header "Docker Services Status" 70
                echo ""
                docker compose -f "${REPO_ROOT}/docker-compose.yml" ps
                echo ""
                pause
                ;;
            8|logs)
                header "Docker Service Logs" 70
                echo ""
                info "Showing last 50 lines of logs (press Ctrl+C to stop)..."
                echo ""
                docker compose -f "${REPO_ROOT}/docker-compose.yml" logs --tail=50 -f || true
                ;;
            9|exit)
                return 0
                ;;
            *)
                error "Invalid selection."
                ;;
        esac
    done
}

# Main function
main() {
    # Check for command-line arguments for non-interactive mode
    if [[ $# -ge 1 ]]; then
        # Non-interactive mode: scripts/test.sh <service> [environment] [mode]
        local service="$1"
        local env="${2:-staging}"
        local mode="${3:-container}"
        
        # Don't clear screen in non-interactive mode
        echo ""
        box "Busibox Test Runner - Non-Interactive" 70
        echo ""
        info "Service: $service | Environment: $env | Mode: $mode"
        echo ""
        
        # Docker environment always runs locally against Docker containers
        if [[ "$env" == "docker" ]] || [[ "$mode" == "local" ]]; then
            info "Running local tests for $service on $env..."
            bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" "$service" "$env"
            exit $?
        else
            # Run tests on container
            run_container_tests "$service" "$env"
            exit $?
        fi
    fi
    
    # Interactive mode
    # Display welcome
    clear
    box "Busibox Test Runner" 70
    echo ""
    info "Run infrastructure and service tests"
    echo ""
    
    # Select environment
    ENV=$(select_environment)
    
    success "Selected environment: $ENV"
    
    # Show test menu based on environment type
    if [[ "$ENV" == "docker" ]]; then
        docker_test_menu
    else
        main_menu "$ENV"
    fi
    
    echo ""
    box "Testing Complete" 70
    echo ""
}

# Run main function with all arguments
main "$@"

exit 0

