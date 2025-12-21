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

# Source UI library
source "${REPO_ROOT}/scripts/lib/ui.sh"

# Detect vault password method (shared with deploy script)
get_vault_flags() {
    local vault_pass_file="$HOME/.vault_pass"
    
    if [ -f "$vault_pass_file" ]; then
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
        network_base="10.96.201"
    fi
    
    case "$container" in
        proxy)    echo "${network_base}.200" ;;
        apps)     echo "${network_base}.201" ;;
        agent)    echo "${network_base}.202" ;;
        postgres) echo "${network_base}.203" ;;
        milvus)   echo "${network_base}.204" ;;
        minio)    echo "${network_base}.205" ;;
        ingest)   echo "${network_base}.206" ;;
        litellm)  echo "${network_base}.207" ;;
        vllm)     echo "${network_base}.208" ;;
        ollama)   echo "${network_base}.209" ;;
        authz)    echo "${network_base}.210" ;;
        search)   echo "${network_base}.204" ;;  # Search runs on milvus container
        *)        echo "" ;;
    esac
}

# Extract test credentials from vault using Python YAML parsing
# Usage: eval "$(extract_vault_credentials)"
extract_vault_credentials() {
    local vault_flags
    vault_flags="$(get_vault_flags)"
    local vault_file="${ANSIBLE_DIR}/roles/secrets/vars/vault.yml"
    
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
    
    # Authz - check multiple possible locations
    authz = secrets.get('authz', {})
    test_creds = secrets.get('test_credentials', {})
    ai_portal = secrets.get('ai-portal', {})
    
    # Admin token: check authz.admin_token, then test_credentials.authz_admin_token
    admin_token = authz.get('admin_token', '')
    if not admin_token:
        admin_token = test_creds.get('authz_admin_token', '')
    if not admin_token:
        admin_token = ai_portal.get('authz_admin_token', '')
    print(f"AUTHZ_ADMIN_TOKEN='{admin_token}'")
    
    # Master key for envelope encryption
    master_key = authz.get('master_key', '')
    print(f"AUTHZ_MASTER_KEY='{master_key}'")
    
    # MinIO
    minio = secrets.get('minio', {})
    print(f"MINIO_ACCESS_KEY='{minio.get('minio_access_key', '') or minio.get('access_key', '')}'")
    print(f"MINIO_SECRET_KEY='{minio.get('minio_secret_key', '') or minio.get('secret_key', '')}'")
    
    # Test credentials
    print(f"AUTHZ_TEST_CLIENT_ID='{test_creds.get('authz_test_client_id', '')}'")
    print(f"AUTHZ_TEST_CLIENT_SECRET='{test_creds.get('authz_test_client_secret', '')}'")
    print(f"TEST_USER_ID='{test_creds.get('test_user_id', '')}'")
    
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

# Get LiteLLM IP from inventory
get_litellm_ip() {
    local env="$1"
    if [[ "$env" == "production" ]]; then
        echo "10.96.200.207"
    else
        echo "10.96.200.207"  # Test uses same IP
    fi
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

# Ingest service tests
ingest_tests_menu() {
    local env="$1"
    
    while true; do
        echo ""
        menu "Ingest Service Tests - $env" \
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
                make test-ingest INV="$inv"
                pause
                ;;
            2)
                make test-ingest-all INV="$inv"
                pause
                ;;
            3)
                make test-ingest-coverage INV="$inv"
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
        
        local test_env="test"
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
            "Ingest Service Tests" \
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
                    local test_env="TEST_DB_USER=busibox_test_user"
                    test_env="${test_env} TEST_DB_PASSWORD=${TEST_DB_PASSWORD}"
                    test_env="${test_env} TEST_DB_NAME=busibox_test"
                    test_env="${test_env} TEST_DB_HOST=${postgres_ip}"
                    test_env="${test_env} AUTHZ_ADMIN_TOKEN=${AUTHZ_ADMIN_TOKEN}"
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
                ingest_tests_menu "$env"
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
            "Ingest - Run ingest tests locally" \
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
                header "Local Ingest Tests" 70
                echo ""
                info "Running ingest tests locally against $env containers..."
                bash "${REPO_ROOT}/scripts/test/run-local-tests.sh" ingest "$env" || true
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
                for svc in authz ingest search agent; do
                    bash "${REPO_ROOT}/scripts/test/generate-local-test-env.sh" "$svc" "$env" 2>/dev/null || true
                done
                echo ""
                success "Environment files generated!"
                echo ""
                info "Files created:"
                echo "  - srv/authz/.env.local"
                echo "  - srv/ingest/.env.local"
                echo "  - srv/search/.env.local"
                echo "  - srv/agent/.env.local"
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
    local postgres_ip authz_ip ingest_ip search_ip agent_ip minio_ip milvus_ip
    postgres_ip=$(get_container_ip postgres "$env")
    authz_ip=$(get_container_ip authz "$env")
    ingest_ip=$(get_container_ip ingest "$env")
    search_ip=$(get_container_ip search "$env")
    agent_ip=$(get_container_ip agent "$env")
    minio_ip=$(get_container_ip minio "$env")
    milvus_ip=$(get_container_ip milvus "$env")
    
    case "$service" in
        authz)
            header "Authz Service Tests" 70
            info "Running authz tests on ${authz_ip}..."
            
            # Build environment variables
            local test_env="TEST_DB_USER=busibox_test_user"
            test_env="${test_env} TEST_DB_PASSWORD=${TEST_DB_PASSWORD}"
            test_env="${test_env} TEST_DB_NAME=busibox_test"
            test_env="${test_env} TEST_DB_HOST=${postgres_ip}"
            test_env="${test_env} AUTHZ_ADMIN_TOKEN=${AUTHZ_ADMIN_TOKEN}"
            test_env="${test_env} AUTHZ_MASTER_KEY=${AUTHZ_MASTER_KEY}"
            test_env="${test_env} AUTHZ_SERVICE_URL=http://${authz_ip}:8010"
            test_env="${test_env} TEST_AUTHZ_URL=http://${authz_ip}:8010"
            
            # Run tests via SSH
            ssh "root@${authz_ip}" "cd /srv/authz/app && source ../venv/bin/activate && export PYTHONPATH=/srv/authz/app/src && source /srv/authz/.env && export ${test_env} && python -m pytest tests/ -v --tb=short" || {
                error "Authz tests failed"
                exit 1
            }
            success "Authz tests passed!"
            ;;
        ingest)
            header "Ingest Service Tests" 70
            info "Running ingest tests on ${ingest_ip}..."
            
            local test_env="POSTGRES_HOST=${postgres_ip}"
            test_env="${test_env} POSTGRES_PASSWORD=${POSTGRES_PASSWORD}"
            test_env="${test_env} MINIO_HOST=${minio_ip}"
            test_env="${test_env} MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY}"
            test_env="${test_env} MINIO_SECRET_KEY=${MINIO_SECRET_KEY}"
            test_env="${test_env} AUTHZ_URL=http://${authz_ip}:8010"
            test_env="${test_env} AUTHZ_JWKS_URL=http://${authz_ip}:8010/.well-known/jwks.json"
            
            ssh "root@${ingest_ip}" "cd /srv/ingest && source venv/bin/activate && export PYTHONPATH=/srv/ingest/src && source .env && export ${test_env} && python -m pytest tests/ -v --tb=short" || {
                error "Ingest tests failed"
                exit 1
            }
            success "Ingest tests passed!"
            ;;
        search)
            header "Search Service Tests" 70
            info "Running search tests on ${search_ip}..."
            
            local test_env="POSTGRES_HOST=${postgres_ip}"
            test_env="${test_env} POSTGRES_PASSWORD=${POSTGRES_PASSWORD}"
            test_env="${test_env} MILVUS_HOST=${milvus_ip}"
            test_env="${test_env} AUTHZ_URL=http://${authz_ip}:8010"
            test_env="${test_env} AUTHZ_JWKS_URL=http://${authz_ip}:8010/.well-known/jwks.json"
            
            # Search service is deployed to /opt/search on milvus container
            ssh "root@${search_ip}" "cd /opt/search && source venv/bin/activate && export PYTHONPATH=/opt/search/src && source .env && export ${test_env} && python -m pytest tests/ -v --tb=short" || {
                error "Search tests failed"
                exit 1
            }
            success "Search tests passed!"
            ;;
        agent)
            header "Agent Service Tests" 70
            info "Running agent tests on ${agent_ip}..."
            
            local test_env="POSTGRES_HOST=${postgres_ip}"
            test_env="${test_env} POSTGRES_PASSWORD=${POSTGRES_PASSWORD}"
            test_env="${test_env} AUTHZ_URL=http://${authz_ip}:8010"
            test_env="${test_env} AUTHZ_JWKS_URL=http://${authz_ip}:8010/.well-known/jwks.json"
            test_env="${test_env} INGEST_URL=http://${ingest_ip}:8000"
            test_env="${test_env} SEARCH_URL=http://${search_ip}:8001"
            
            ssh "root@${agent_ip}" "cd /srv/agent && source venv/bin/activate && source .env && export ${test_env} && python -m pytest tests/ -v --tb=short" || {
                error "Agent tests failed"
                exit 1
            }
            success "Agent tests passed!"
            ;;
        all)
            for svc in authz ingest search agent; do
                run_container_tests "$svc" "$env"
            done
            ;;
        *)
            error "Unknown service: $service"
            echo "Available services: authz, ingest, search, agent, all"
            exit 1
            ;;
    esac
}

# Main function
main() {
    # Check for command-line arguments for non-interactive mode
    if [[ $# -ge 1 ]]; then
        # Non-interactive mode: scripts/test.sh <service> [environment] [mode]
        local service="$1"
        local env="${2:-test}"
        local mode="${3:-container}"
        
        # Don't clear screen in non-interactive mode
        echo ""
        box "Busibox Test Runner - Non-Interactive" 70
        echo ""
        info "Service: $service | Environment: $env | Mode: $mode"
        echo ""
        
        if [[ "$mode" == "local" ]]; then
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
    
    # Show test menu
    main_menu "$ENV"
    
    echo ""
    box "Testing Complete" 70
    echo ""
}

# Run main function with all arguments
main "$@"

exit 0

