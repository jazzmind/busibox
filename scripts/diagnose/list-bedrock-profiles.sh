#!/usr/bin/env bash
# List available Bedrock inference profiles
# 
# Execution context: Admin workstation
# Purpose: Discover available Claude 4.x models and inference profiles
# Usage: bash scripts/list-bedrock-profiles.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Bedrock Inference Profiles ===${NC}"
echo ""

# Get API key
if [[ -z "${BEDROCK_API_KEY:-}" ]]; then
    echo -e "${YELLOW}Please enter your Bedrock API key:${NC}"
    read -rs BEDROCK_API_KEY
    export BEDROCK_API_KEY
fi

REGION="${AWS_REGION:-us-east-1}"

echo "Testing available Claude 4.x models with your API key..."
echo "Region: $REGION"
echo ""

# Known Claude 4.x inference profiles to test
MODELS_TO_TEST=(
    "us.anthropic.claude-4-5-haiku-20251001-v1:0"
    "us.anthropic.claude-4-5-sonnet-20250514-v1:0"
    "anthropic.claude-4-5-haiku-20251001-v1:0"
    "anthropic.claude-4-5-sonnet-20250514-v1:0"
    "us.anthropic.claude-4-haiku-20250514-v1:0"
    "us.anthropic.claude-4-sonnet-20250514-v1:0"
)

echo -e "${BLUE}Testing models...${NC}"
echo ""

WORKING_MODELS=()
FAILED_MODELS=()

for model_id in "${MODELS_TO_TEST[@]}"; do
    echo -n "Testing: $model_id ... "
    
    response=$(curl -k -s -w "\nHTTP_CODE:%{http_code}" -X POST \
        "https://bedrock-runtime.$REGION.amazonaws.com/model/$model_id/converse" \
        -H "Authorization: Bearer $BEDROCK_API_KEY" \
        -H "Content-Type: application/json" \
        -d '{"messages":[{"role":"user","content":[{"text":"Hi"}]}]}' 2>&1)
    
    http_code=$(echo "$response" | grep "HTTP_CODE:" | cut -d: -f2)
    body=$(echo "$response" | sed '/HTTP_CODE:/d')
    
    if [[ "$http_code" == "200" ]]; then
        echo -e "${GREEN}✓ WORKS${NC}"
        WORKING_MODELS+=("$model_id")
        
        # Extract model info from response
        if echo "$body" | jq -e '.output' > /dev/null 2>&1; then
            tokens=$(echo "$body" | jq -r '.usage.totalTokens // "N/A"')
            latency=$(echo "$body" | jq -r '.metrics.latencyMs // "N/A"')
            echo "  Tokens: $tokens, Latency: ${latency}ms"
        fi
    else
        echo -e "${RED}✗ FAILED (HTTP $http_code)${NC}"
        FAILED_MODELS+=("$model_id")
        
        # Show error message
        if echo "$body" | jq -e '.message' > /dev/null 2>&1; then
            error_msg=$(echo "$body" | jq -r '.message')
            echo "  Error: $error_msg"
        fi
    fi
    echo ""
done

# Summary
echo -e "${BLUE}=== Summary ===${NC}"
echo ""

if [[ ${#WORKING_MODELS[@]} -gt 0 ]]; then
    echo -e "${GREEN}✓ Working Models (${#WORKING_MODELS[@]}):${NC}"
    for model in "${WORKING_MODELS[@]}"; do
        echo "  - $model"
    done
    echo ""
fi

if [[ ${#FAILED_MODELS[@]} -gt 0 ]]; then
    echo -e "${RED}✗ Failed Models (${#FAILED_MODELS[@]}):${NC}"
    for model in "${FAILED_MODELS[@]}"; do
        echo "  - $model"
    done
    echo ""
fi

# Generate config
if [[ ${#WORKING_MODELS[@]} -gt 0 ]]; then
    echo -e "${BLUE}=== Configuration for model_registry.yml ===${NC}"
    echo ""
    
    for model in "${WORKING_MODELS[@]}"; do
        # Extract model name parts
        if [[ "$model" =~ haiku ]]; then
            name="claude-haiku-4-5"
            desc="Claude 4.5 Haiku - Fastest and most efficient"
        elif [[ "$model" =~ 4-5-sonnet ]]; then
            name="claude-sonnet-4-5"
            desc="Claude 4.5 Sonnet - Most capable"
        elif [[ "$model" =~ 4-haiku ]]; then
            name="claude-haiku-4"
            desc="Claude 4 Haiku - Fast"
        elif [[ "$model" =~ 4-sonnet ]]; then
            name="claude-sonnet-4"
            desc="Claude 4 Sonnet - Capable"
        fi
        
        echo "  \"$name\":"
        echo "    provider: \"bedrock\""
        echo "    model: \"$model\""
        echo "    model_name: \"$model\""
        echo "    description: \"$desc\""
        echo ""
    done
fi

echo -e "${BLUE}=== Notes ===${NC}"
echo ""
echo "• Models with 'us.' prefix are cross-region inference profiles"
echo "• Models without prefix are direct model IDs (may require provisioned throughput)"
echo "• Claude 4.x models typically REQUIRE inference profiles"
echo "• Copy the working model configurations above into your model_registry.yml"

