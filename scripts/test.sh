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
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ANSIBLE_DIR="${REPO_ROOT}/provision/ansible"

# Source UI library
source "${SCRIPT_DIR}/lib/ui.sh"

# Display welcome
clear
box "Busibox Test Runner" 70
echo ""
info "Run infrastructure and service tests"
echo ""

# Detect vault password method (shared with deploy script)
get_vault_flags() {
    local vault_pass_file="$HOME/.vault_pass"
    
    if [ -f "$vault_pass_file" ]; then
        echo "--vault-password-file $vault_pass_file"
    else
        echo "--ask-vault-pass"
    fi
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
    
    bash "${REPO_ROOT}/scripts/test-infrastructure.sh" "$test_type" || {
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
            "All Service Tests" \
            "Bootstrap Test Credentials (for local dev)" \
            "Back to Main Menu"
        
        read -p "$(echo -e "${BOLD}Select option [1-9]:${NC} ")" choice
        
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
                    # ansible ad-hoc uses ANSIBLE_CONFIG; ensure we stay in ansible dir
                    # Sync test requirements and tests to authz-lxc
                    ANSIBLE_CONFIG="${ANSIBLE_DIR}/ansible.cfg" ansible -i "$inv" authz -m copy -a "src=${REPO_ROOT}/srv/authz/requirements.test.txt dest=/srv/authz/app/ mode=0644" $vault_flags || {
                        error "Failed to copy authz test requirements"
                    }
                    ANSIBLE_CONFIG="${ANSIBLE_DIR}/ansible.cfg" ansible -i "$inv" authz -m copy -a "src=${REPO_ROOT}/srv/authz/tests/ dest=/srv/authz/app/tests/ mode=0644" $vault_flags || {
                        error "Failed to copy authz tests"
                    }
                    ANSIBLE_CONFIG="${ANSIBLE_DIR}/ansible.cfg" ansible -i "$inv" authz -m shell -a "bash -lc 'cd /srv/authz/app && source ../venv/bin/activate && pip install -q -r requirements.test.txt && pytest -q'" $vault_flags || {
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
                header "All Service Tests" 70
                echo ""
                if confirm "Run ALL service tests on $env? (This may take a while)"; then
                    make test-all INV="$inv"
                fi
                pause
                ;;
            8)
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
            9)
                cd "$REPO_ROOT"
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-9."
                ;;
        esac
        
        cd "$REPO_ROOT"
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
            "Service Tests" \
            "All Tests (Infrastructure + Services)" \
            "Exit"
        
        read -p "$(echo -e "${BOLD}Select option [1-7]:${NC} ")" choice
        
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
            7)
                echo ""
                info "Exiting..."
                return 0
                ;;
            *)
                error "Invalid selection. Please enter 1-7."
                ;;
        esac
    done
}

# Main function
main() {
    # Select environment
    ENV=$(select_environment)
    
    success "Selected environment: $ENV"
    
    # Show test menu
    main_menu "$ENV"
    
    echo ""
    box "Testing Complete" 70
    echo ""
}

# Run main function
main

exit 0

