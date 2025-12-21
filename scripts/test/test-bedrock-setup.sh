#!/usr/bin/env bash
# Test AWS Bedrock configuration with LiteLLM
# 
# Execution context: Admin workstation
# Purpose: Verify Bedrock models are accessible through LiteLLM
# Prerequisites: 
#   - Bedrock API credentials configured in vault
#   - LiteLLM deployed and running
# Usage: bash scripts/tests/test-bedrock-setup.sh [test|production]

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUSIBOX_ROOT="$(dirname "$SCRIPT_DIR")"
ENV="${1:-test}"

# Set environment-specific variables
if [[ "$ENV" == "production" ]]; then
    LITELLM_IP="10.96.200.207"
    INVENTORY_DIR="$BUSIBOX_ROOT/provision/ansible/inventory/production"
else
    LITELLM_IP="10.96.201.207"
    INVENTORY_DIR="$BUSIBOX_ROOT/provision/ansible/inventory/test"
fi

LITELLM_PORT="4000"
LITELLM_URL="http://${LITELLM_IP}:${LITELLM_PORT}"

# Get master key from vault
echo -e "${BLUE}=== Bedrock Configuration Test ===${NC}"
echo "Environment: $ENV"
echo "LiteLLM URL: $LITELLM_URL"
echo ""

# Helper function to make API calls
api_call() {
    local endpoint="$1"
    local method="${2:-GET}"
    local data="${3:-}"
    
    if [[ -n "$data" ]]; then
        curl -s -X "$method" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
            -d "$data" \
            "${LITELLM_URL}${endpoint}"
    else
        curl -s -X "$method" \
            -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
            "${LITELLM_URL}${endpoint}"
    fi
}

# Test 1: Check LiteLLM health
echo -e "${BLUE}Test 1: LiteLLM Health Check${NC}"
if curl -sf "${LITELLM_URL}/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ LiteLLM is running${NC}"
else
    echo -e "${RED}✗ LiteLLM is not accessible at ${LITELLM_URL}${NC}"
    echo "  Make sure LiteLLM is deployed and running"
    exit 1
fi
echo ""

# Get master key from environment or prompt
if [[ -z "${LITELLM_MASTER_KEY:-}" ]]; then
    echo -e "${YELLOW}Please enter LiteLLM master key:${NC}"
    read -rs LITELLM_MASTER_KEY
    export LITELLM_MASTER_KEY
    echo ""
fi

# Test 2: List available models
echo -e "${BLUE}Test 2: List Available Models${NC}"
MODELS_RESPONSE=$(api_call "/v1/models" "GET")
if echo "$MODELS_RESPONSE" | jq -e '.data' > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Successfully retrieved model list${NC}"
    
    # Extract Bedrock models
    BEDROCK_MODELS=$(echo "$MODELS_RESPONSE" | jq -r '.data[].id' | grep -E "(frontier|claude|bedrock)" || true)
    
    if [[ -n "$BEDROCK_MODELS" ]]; then
        echo -e "${GREEN}✓ Bedrock models found:${NC}"
        echo "$BEDROCK_MODELS" | while read -r model; do
            echo "  - $model"
        done
    else
        echo -e "${YELLOW}⚠ No Bedrock models found in model list${NC}"
        echo "  Available models:"
        echo "$MODELS_RESPONSE" | jq -r '.data[].id' | sed 's/^/  - /'
    fi
else
    echo -e "${RED}✗ Failed to retrieve model list${NC}"
    echo "$MODELS_RESPONSE"
    exit 1
fi
echo ""

# Test 3: Test Bedrock model inference
test_model() {
    local purpose="$1"
    local test_prompt="${2:-Hello! Please respond with a brief greeting.}"
    
    echo -e "${BLUE}Test 3: Testing model '$purpose'${NC}"
    
    local request_data=$(cat <<EOF
{
  "model": "$purpose",
  "messages": [
    {
      "role": "user",
      "content": "$test_prompt"
    }
  ],
  "max_tokens": 100,
  "temperature": 0.7
}
EOF
)
    
    local response=$(api_call "/v1/chat/completions" "POST" "$request_data")
    
    if echo "$response" | jq -e '.choices[0].message.content' > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Model '$purpose' responded successfully${NC}"
        echo -e "${GREEN}Response:${NC}"
        echo "$response" | jq -r '.choices[0].message.content' | sed 's/^/  /'
        
        # Show usage
        local usage=$(echo "$response" | jq -r '.usage')
        if [[ "$usage" != "null" ]]; then
            echo -e "${BLUE}Usage:${NC}"
            echo "$usage" | jq '.' | sed 's/^/  /'
        fi
        return 0
    else
        echo -e "${RED}✗ Model '$purpose' failed${NC}"
        echo -e "${RED}Error response:${NC}"
        echo "$response" | jq '.' | sed 's/^/  /'
        return 1
    fi
}

# Test each Bedrock model purpose
echo ""
MODELS_TO_TEST=("frontier" "frontier-fast" "balanced" "advanced")
SUCCESS_COUNT=0
TOTAL_COUNT=${#MODELS_TO_TEST[@]}

for model_purpose in "${MODELS_TO_TEST[@]}"; do
    if test_model "$model_purpose"; then
        ((SUCCESS_COUNT++))
    fi
    echo ""
done

# Test 4: Test streaming
echo -e "${BLUE}Test 4: Test Streaming Response${NC}"
echo "Testing streaming with 'frontier' model..."

STREAM_REQUEST=$(cat <<'EOF'
{
  "model": "frontier",
  "messages": [{"role": "user", "content": "Count from 1 to 5, each number on a new line."}],
  "max_tokens": 50,
  "stream": true
}
EOF
)

STREAM_RESPONSE=$(curl -s -N -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
    -d "$STREAM_REQUEST" \
    "${LITELLM_URL}/v1/chat/completions" 2>&1 || true)

if echo "$STREAM_RESPONSE" | grep -q "data: "; then
    echo -e "${GREEN}✓ Streaming is working${NC}"
    echo "Sample output:"
    echo "$STREAM_RESPONSE" | head -5 | sed 's/^/  /'
else
    echo -e "${YELLOW}⚠ Streaming test inconclusive${NC}"
fi
echo ""

# Summary
echo -e "${BLUE}=== Test Summary ===${NC}"
echo "Successful model tests: $SUCCESS_COUNT/$TOTAL_COUNT"

if [[ $SUCCESS_COUNT -eq $TOTAL_COUNT ]]; then
    echo -e "${GREEN}✓ All Bedrock models are working correctly!${NC}"
    exit 0
elif [[ $SUCCESS_COUNT -gt 0 ]]; then
    echo -e "${YELLOW}⚠ Some Bedrock models are working, but not all${NC}"
    exit 1
else
    echo -e "${RED}✗ No Bedrock models are working${NC}"
    echo ""
    echo -e "${YELLOW}Troubleshooting steps:${NC}"
    echo "1. Verify Bedrock API credentials in vault:"
    echo "   cd $BUSIBOX_ROOT/provision/ansible"
    echo "   ansible-vault edit roles/secrets/vars/vault.yml"
    echo ""
    echo "2. Check LiteLLM logs:"
    echo "   ssh root@${LITELLM_IP} journalctl -u litellm -n 50 --no-pager"
    echo ""
    echo "3. Verify model configuration:"
    echo "   ssh root@${LITELLM_IP} cat /etc/litellm/config.yaml"
    echo ""
    echo "4. Test direct Bedrock access (bypass LiteLLM):"
    echo "   curl -X POST https://bedrock-runtime.us-east-1.amazonaws.com/model/us.anthropic.claude-3-5-haiku-20241022-v1:0/converse \\"
    echo "     -H \"Authorization: Bearer YOUR_API_KEY\" \\"
    echo "     -H \"Content-Type: application/json\" \\"
    echo "     -d '{\"messages\": [{\"role\": \"user\", \"content\": [{\"text\": \"Hello\"}]}]}'"
    exit 1
fi

