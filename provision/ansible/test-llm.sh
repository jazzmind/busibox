#!/bin/bash
# LLM Testing Functions for Busibox
# Used by test-menu.sh for LLM model testing

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Get inventory from environment or default to test
INV="${INV:-inventory/test}"

# Get LiteLLM IP from inventory
get_litellm_ip() {
    if [[ "$INV" == "inventory/production" ]]; then
        echo "10.96.200.207"
    else
        echo "10.96.200.207"  # Test uses same IP
    fi
}

# Get LiteLLM API key (from vault or default)
get_litellm_key() {
    # Try to get from vault, otherwise use default
    if [[ -f "${INV}/group_vars/all/vault.yml" ]]; then
        # Try to extract from vault (requires ansible-vault)
        ansible-vault view "${INV}/group_vars/all/vault.yml" 2>/dev/null | \
            grep -i "litellm.*key" | head -1 | sed 's/.*: *"\(.*\)".*/\1/' || \
            echo "sk-litellm-master-key-change-me"
    else
        echo "sk-litellm-master-key-change-me"
    fi
}

LITELLM_IP=$(get_litellm_ip)
LITELLM_URL="http://${LITELLM_IP}:4000"
LITELLM_KEY=$(get_litellm_key)

# Model registry path
MODEL_REGISTRY="${INV}/group_vars/all/model_registry.yml"

# Check if LiteLLM is reachable
check_litellm() {
    if ! curl -sf "${LITELLM_URL}/health" > /dev/null 2>&1; then
        echo -e "${RED}✗ LiteLLM is not reachable at ${LITELLM_URL}${NC}"
        echo "  Make sure LiteLLM service is running: make litellm INV=${INV}"
        return 1
    fi
    return 0
}

# List models by purpose
list_models_by_purpose() {
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}Models by Purpose${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    
    if ! check_litellm; then
        return 1
    fi
    
    # Get available models from LiteLLM
    echo -e "${CYAN}Fetching models from LiteLLM...${NC}"
    MODELS_JSON=$(curl -sf -H "Authorization: Bearer ${LITELLM_KEY}" \
        "${LITELLM_URL}/v1/models" 2>/dev/null || echo '{"data":[]}')
    
    # Extract model IDs
    AVAILABLE_MODELS=$(echo "$MODELS_JSON" | jq -r '.data[].id' 2>/dev/null || echo "")
    
    if [[ -z "$AVAILABLE_MODELS" ]]; then
        echo -e "${YELLOW}⚠ Could not fetch models from LiteLLM${NC}"
        echo "  Trying without authentication..."
        MODELS_JSON=$(curl -sf "${LITELLM_URL}/v1/models" 2>/dev/null || echo '{"data":[]}')
        AVAILABLE_MODELS=$(echo "$MODELS_JSON" | jq -r '.data[].id' 2>/dev/null || echo "")
    fi
    
    # Read model registry
    if [[ ! -f "$MODEL_REGISTRY" ]]; then
        echo -e "${RED}✗ Model registry not found: ${MODEL_REGISTRY}${NC}"
        return 1
    fi
    
    echo ""
    echo -e "${GREEN}Purpose-based Models (from model_registry.yml):${NC}"
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
            MODEL_LINE=$(grep -A 10 "^  ${PURPOSE}:" "$MODEL_REGISTRY" | grep "model:" | head -1 | sed 's/.*model: *"\(.*\)".*/\1/')
            MODEL_NAME_LINE=$(grep -A 10 "^  ${PURPOSE}:" "$MODEL_REGISTRY" | grep "model_name:" | head -1 | sed 's/.*model_name: *"\(.*\)".*/\1/')
            DESC_LINE=$(grep -A 10 "^  ${PURPOSE}:" "$MODEL_REGISTRY" | grep "description:" | head -1 | sed 's/.*description: *"\(.*\)".*/\1/')
            
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
    done < "$MODEL_REGISTRY"
    
    # Also show vLLM models if available
    echo -e "${GREEN}Direct vLLM Models (if available):${NC}"
    echo ""
    if echo "$AVAILABLE_MODELS" | grep -q "vllm\|qwen\|phi"; then
        echo "$AVAILABLE_MODELS" | grep -E "vllm|qwen|phi" | while read -r model; do
            echo -e "  ${GREEN}✓${NC} ${model}"
        done
    else
        echo -e "  ${YELLOW}No vLLM models found${NC}"
    fi
    echo ""
}

# Test chat completion for a purpose
test_purpose_chat() {
    local purpose="$1"
    local prompt="$2"
    
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}Testing: ${purpose}${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    
    if ! check_litellm; then
        return 1
    fi
    
    # Get model for this purpose
    MODEL=$(grep -A 10 "^  ${purpose}:" "$MODEL_REGISTRY" | grep "model:" | head -1 | sed 's/.*model: *"\(.*\)".*/\1/')
    
    if [[ -z "$MODEL" ]]; then
        echo -e "${RED}✗ Purpose '${purpose}' not found in model registry${NC}"
        return 1
    fi
    
    echo -e "${CYAN}Using model: ${MODEL}${NC}"
    echo -e "${CYAN}Prompt: ${prompt}${NC}"
    echo ""
    
    # Make API call
    RESPONSE=$(curl -sf -X POST "${LITELLM_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${LITELLM_KEY}" \
        -d "{
            \"model\": \"${MODEL}\",
            \"messages\": [{\"role\": \"user\", \"content\": \"${prompt}\"}],
            \"max_tokens\": 500,
            \"temperature\": 0.7
        }" 2>/dev/null)
    
    if [[ -z "$RESPONSE" ]]; then
        echo -e "${RED}✗ Failed to get response from LiteLLM${NC}"
        return 1
    fi
    
    # Extract response
    CONTENT=$(echo "$RESPONSE" | jq -r '.choices[0].message.content' 2>/dev/null || echo "")
    USAGE=$(echo "$RESPONSE" | jq -r '.usage' 2>/dev/null || echo "")
    
    if [[ -z "$CONTENT" ]]; then
        echo -e "${RED}✗ No content in response${NC}"
        echo "Response: $RESPONSE"
        return 1
    fi
    
    echo -e "${GREEN}✓ Response:${NC}"
    echo "$CONTENT" | fold -w 80 -s
    echo ""
    
    if [[ -n "$USAGE" ]]; then
        echo -e "${CYAN}Usage:${NC}"
        echo "$USAGE" | jq '.' 2>/dev/null || echo "$USAGE"
        echo ""
    fi
    
    return 0
}

# Test embedding for embedding purpose
test_purpose_embedding() {
    local purpose="embedding"
    
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}Testing: ${purpose}${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    
    if ! check_litellm; then
        return 1
    fi
    
    # Get model for this purpose
    MODEL=$(grep -A 10 "^  ${purpose}:" "$MODEL_REGISTRY" | grep "model:" | head -1 | sed 's/.*model: *"\(.*\)".*/\1/')
    
    if [[ -z "$MODEL" ]]; then
        echo -e "${RED}✗ Purpose '${purpose}' not found in model registry${NC}"
        return 1
    fi
    
    echo -e "${CYAN}Using model: ${MODEL}${NC}"
    echo -e "${CYAN}Testing with sample text...${NC}"
    echo ""
    
    # Test embedding
    RESPONSE=$(curl -sf -X POST "${LITELLM_URL}/v1/embeddings" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${LITELLM_KEY}" \
        -d "{
            \"model\": \"${MODEL}\",
            \"input\": \"This is a test sentence for embedding generation.\"
        }" 2>/dev/null)
    
    if [[ -z "$RESPONSE" ]]; then
        echo -e "${RED}✗ Failed to get response from LiteLLM${NC}"
        return 1
    fi
    
    # Extract embedding info
    DIMENSIONS=$(echo "$RESPONSE" | jq -r '.data[0].embedding | length' 2>/dev/null || echo "0")
    MODEL_USED=$(echo "$RESPONSE" | jq -r '.model' 2>/dev/null || echo "")
    USAGE=$(echo "$RESPONSE" | jq -r '.usage' 2>/dev/null || echo "")
    
    if [[ "$DIMENSIONS" == "0" ]]; then
        echo -e "${RED}✗ No embedding data in response${NC}"
        echo "Response: $RESPONSE"
        return 1
    fi
    
    echo -e "${GREEN}✓ Embedding generated successfully${NC}"
    echo -e "  Model: ${MODEL_USED}"
    echo -e "  Dimensions: ${DIMENSIONS}"
    
    if [[ -n "$USAGE" ]]; then
        echo -e "${CYAN}Usage:${NC}"
        echo "$USAGE" | jq '.' 2>/dev/null || echo "$USAGE"
    fi
    echo ""
    
    return 0
}

# Test fast model (quick chat)
test_fast() {
    test_purpose_chat "fast" "Say hello in one sentence."
}

# Test analysis (challenging math/physics problem)
test_analysis() {
    local problem="Solve this step by step: A particle moves in a 2D plane with position vector r(t) = (3t^2, 4t^3) where t is time. Find the velocity vector, acceleration vector, and the magnitude of acceleration at t=2."
    test_purpose_chat "analysis" "$problem"
}

# Test other purposes
test_purpose() {
    local purpose="$1"
    case "$purpose" in
        "fast")
            test_fast
            ;;
        "embedding")
            test_purpose_embedding
            ;;
        "analysis")
            test_analysis
            ;;
        "default"|"chat"|"research"|"cleanup"|"parsing"|"classify"|"vision")
            test_purpose_chat "$purpose" "Provide a brief response demonstrating this model's capabilities."
            ;;
        *)
            echo -e "${RED}✗ Unknown purpose: ${purpose}${NC}"
            return 1
            ;;
    esac
}

# Test Bedrock (if configured)
test_bedrock() {
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}Testing: AWS Bedrock${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    
    # Check if bedrock models are available
    MODELS_JSON=$(curl -sf -H "Authorization: Bearer ${LITELLM_KEY}" \
        "${LITELLM_URL}/v1/models" 2>/dev/null || echo '{"data":[]}')
    
    BEDROCK_MODELS=$(echo "$MODELS_JSON" | jq -r '.data[].id' 2>/dev/null | grep -i bedrock || echo "")
    
    if [[ -z "$BEDROCK_MODELS" ]]; then
        echo -e "${YELLOW}⚠ Bedrock models not configured in LiteLLM${NC}"
        echo "  To configure Bedrock, add models to litellm config with 'bedrock/' prefix"
        return 1
    fi
    
    echo -e "${GREEN}✓ Bedrock models found:${NC}"
    echo "$BEDROCK_MODELS" | while read -r model; do
        echo "  - $model"
    done
    echo ""
    
    # Test first bedrock model
    FIRST_MODEL=$(echo "$BEDROCK_MODELS" | head -1)
    echo -e "${CYAN}Testing with: ${FIRST_MODEL}${NC}"
    test_purpose_chat "$FIRST_MODEL" "Hello from Bedrock!"
}

# Test OpenAI (if configured)
test_openai() {
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}Testing: OpenAI${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    
    # Check if OpenAI models are available
    MODELS_JSON=$(curl -sf -H "Authorization: Bearer ${LITELLM_KEY}" \
        "${LITELLM_URL}/v1/models" 2>/dev/null || echo '{"data":[]}')
    
    OPENAI_MODELS=$(echo "$MODELS_JSON" | jq -r '.data[].id' 2>/dev/null | grep -E "^gpt-|^o1-" || echo "")
    
    if [[ -z "$OPENAI_MODELS" ]]; then
        echo -e "${YELLOW}⚠ OpenAI models not configured in LiteLLM${NC}"
        echo "  To configure OpenAI, add OPENAI_API_KEY to litellm environment"
        return 1
    fi
    
    echo -e "${GREEN}✓ OpenAI models found:${NC}"
    echo "$OPENAI_MODELS" | while read -r model; do
        echo "  - $model"
    done
    echo ""
    
    # Test first OpenAI model
    FIRST_MODEL=$(echo "$OPENAI_MODELS" | head -1)
    echo -e "${CYAN}Testing with: ${FIRST_MODEL}${NC}"
    test_purpose_chat "$FIRST_MODEL" "Hello from OpenAI!"
}

