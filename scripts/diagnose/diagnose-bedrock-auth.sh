#!/usr/bin/env bash
# Diagnose Bedrock API authentication method
# 
# Execution context: Admin workstation
# Purpose: Test direct Bedrock API access to determine auth method
# Usage: 
#   bash scripts/diagnose/diagnose-bedrock-auth.sh [API_KEY]
#   BEDROCK_API_KEY=xxx bash scripts/diagnose/diagnose-bedrock-auth.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Bedrock API Authentication Diagnostic ===${NC}"
echo ""

# Get API key from argument or environment
if [[ -n "${1:-}" ]]; then
    BEDROCK_API_KEY="$1"
elif [[ -z "${BEDROCK_API_KEY:-}" ]]; then
    echo -e "${YELLOW}Please enter your Bedrock API key:${NC}"
    read -rs BEDROCK_API_KEY
    export BEDROCK_API_KEY
fi

if [[ -z "${BEDROCK_API_KEY:-}" ]]; then
    echo -e "${RED}✗ No API key provided${NC}"
    echo "Usage: $0 [API_KEY]"
    echo "   Or: BEDROCK_API_KEY=xxx $0"
    exit 1
fi

REGION="${AWS_REGION:-us-east-1}"

# Test 1: Direct API call with bearer token (your working method)
echo -e "${BLUE}Test 1: Direct API with Bearer Token${NC}"
echo "Testing model: us.anthropic.claude-3-5-haiku-20241022-v1:0"
echo "Endpoint: https://bedrock-runtime.$REGION.amazonaws.com/model/us.anthropic.claude-3-5-haiku-20241022-v1:0/converse"

RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST \
  "https://bedrock-runtime.$REGION.amazonaws.com/model/us.anthropic.claude-3-5-haiku-20241022-v1:0/converse" \
  -H "Authorization: Bearer $BEDROCK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{
      "role": "user",
      "content": [{"text": "Say hello"}]
    }]
  }' 2>&1 || true)

HTTP_CODE=$(echo "$RESPONSE" | grep "HTTP_CODE:" | cut -d: -f2)
BODY=$(echo "$RESPONSE" | sed '/HTTP_CODE:/d')

if [[ "$HTTP_CODE" == "200" ]]; then
    echo -e "${GREEN}✓ Bearer token authentication works!${NC}"
    echo "Response:"
    echo "$BODY" | jq '.' 2>/dev/null || echo "$BODY"
else
    echo -e "${RED}✗ Bearer token authentication failed (HTTP $HTTP_CODE)${NC}"
    echo "Response:"
    echo "$BODY"
fi
echo ""

# Test 2: Check if API key is actually AWS credentials
echo -e "${BLUE}Test 2: Checking API Key Format${NC}"

if [[ "$BEDROCK_API_KEY" =~ ^[A-Z0-9]{20}$ ]]; then
    echo -e "${YELLOW}⚠ API key looks like AWS Access Key ID (20 chars, uppercase alphanumeric)${NC}"
    echo "  This might be standard AWS credentials"
    echo ""
    echo "  If you have both Access Key ID and Secret Access Key:"
    echo "  Format for vault: ACCESS_KEY_ID:SECRET_ACCESS_KEY"
elif [[ "$BEDROCK_API_KEY" =~ : ]]; then
    echo -e "${GREEN}✓ API key contains ':' - appears to be ACCESS_KEY_ID:SECRET_ACCESS_KEY format${NC}"
    ACCESS_KEY=$(echo "$BEDROCK_API_KEY" | cut -d: -f1)
    SECRET_KEY=$(echo "$BEDROCK_API_KEY" | cut -d: -f2)
    echo "  Access Key ID: ${ACCESS_KEY:0:8}... (${#ACCESS_KEY} chars)"
    echo "  Secret Key: ${SECRET_KEY:0:8}... (${#SECRET_KEY} chars)"
else
    echo -e "${YELLOW}⚠ API key format is unclear${NC}"
    echo "  Length: ${#BEDROCK_API_KEY} characters"
    echo "  Starts with: ${BEDROCK_API_KEY:0:10}..."
fi
echo ""

# Test 3: Try AWS SDK-style authentication (what LiteLLM uses)
echo -e "${BLUE}Test 3: AWS SDK Authentication${NC}"

if [[ "$BEDROCK_API_KEY" =~ : ]]; then
    ACCESS_KEY=$(echo "$BEDROCK_API_KEY" | cut -d: -f1)
    SECRET_KEY=$(echo "$BEDROCK_API_KEY" | cut -d: -f2)
    
    echo "Testing with AWS credentials..."
    
    # Create a temporary Python script to test AWS SDK
    cat > /tmp/test_bedrock_sdk.py <<'PYEOF'
import boto3
import json
import sys
import os

access_key = os.environ.get('AWS_ACCESS_KEY_ID')
secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
region = os.environ.get('AWS_REGION', 'us-east-1')

try:
    client = boto3.client(
        'bedrock-runtime',
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region
    )
    
    response = client.converse(
        modelId='us.anthropic.claude-3-5-haiku-20241022-v1:0',
        messages=[{
            'role': 'user',
            'content': [{'text': 'Say hello'}]
        }]
    )
    
    print("SUCCESS: AWS SDK authentication works!")
    print(json.dumps(response, indent=2, default=str))
    sys.exit(0)
    
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {str(e)}")
    sys.exit(1)
PYEOF
    
    AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
    AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
    AWS_REGION="$REGION" \
    python3 /tmp/test_bedrock_sdk.py 2>&1 || true
    
    rm -f /tmp/test_bedrock_sdk.py
else
    echo -e "${YELLOW}⚠ Cannot test AWS SDK - API key not in ACCESS:SECRET format${NC}"
    echo "  Your API key appears to be a different authentication type"
fi
echo ""

# Summary and recommendations
echo -e "${BLUE}=== Recommendations ===${NC}"
echo ""

if [[ "$HTTP_CODE" == "200" ]]; then
    echo -e "${GREEN}Your Bedrock API key works with bearer token authentication.${NC}"
    echo ""
    echo "However, LiteLLM uses the AWS SDK which expects standard AWS credentials."
    echo ""
    echo -e "${YELLOW}Options:${NC}"
    echo ""
    echo "1. ${BLUE}Use AWS IAM credentials instead${NC} (Recommended for LiteLLM)"
    echo "   - Create an IAM user with Bedrock permissions"
    echo "   - Use Access Key ID + Secret Access Key"
    echo "   - Format: ACCESS_KEY_ID:SECRET_ACCESS_KEY"
    echo ""
    echo "2. ${BLUE}Use a custom proxy${NC}"
    echo "   - Create a proxy that converts bearer token to AWS SDK calls"
    echo "   - Point LiteLLM to the proxy"
    echo ""
    echo "3. ${BLUE}Test if your API key IS AWS credentials${NC}"
    echo "   - If your key is actually ACCESS_KEY_ID:SECRET_ACCESS_KEY"
    echo "   - It should work with LiteLLM directly"
    echo ""
    
    if [[ "$BEDROCK_API_KEY" =~ : ]]; then
        echo -e "${GREEN}✓ Your API key appears to contain both parts (ACCESS:SECRET)${NC}"
        echo "  This should work with LiteLLM!"
        echo ""
        echo "  Next step: Update vault with this key and deploy"
    fi
else
    echo -e "${RED}Your Bedrock API key doesn't work with bearer token auth.${NC}"
    echo ""
    echo "It might already be AWS IAM credentials. Try:"
    echo "1. Check if it's in ACCESS_KEY_ID:SECRET_ACCESS_KEY format"
    echo "2. Test with AWS SDK (Test 3 above)"
fi

