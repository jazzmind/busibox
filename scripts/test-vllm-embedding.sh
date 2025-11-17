#!/bin/bash
set -euo pipefail

# Test vLLM Embedding Service
# Run on Proxmox host to verify vLLM embedding and liteLLM integration
# Usage: bash scripts/test-vllm-embedding.sh

VLLM_HOST="10.96.200.208"  # vLLM container (not 210 which is old Ollama)
VLLM_PORT="8001"
LITELLM_HOST="10.96.200.207"  # liteLLM container
LITELLM_PORT="4000"

# liteLLM API key (set via environment variable or use default for testing)
# To set: export LITELLM_API_KEY="your-key-here"
LITELLM_API_KEY="${LITELLM_API_KEY:-}"

echo "========================================"
echo "vLLM Embedding Service Test"
echo "========================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test 1: Check vLLM embedding service status
echo "1. Checking vLLM embedding service status..."
if ssh root@${VLLM_HOST} "systemctl is-active vllm-embedding" &>/dev/null; then
    echo -e "${GREEN}✓ vLLM embedding service is running${NC}"
else
    echo -e "${RED}✗ vLLM embedding service is NOT running${NC}"
    echo "  Fix: ssh root@${VLLM_HOST} 'systemctl start vllm-embedding'"
    exit 1
fi
echo ""

# Test 2: Check GPU allocation
echo "2. Checking GPU allocation..."
ssh root@${VLLM_HOST} "nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader" | while read line; do
    echo "  $line"
done
echo ""

# Test 3: Check vLLM embedding API health
echo "3. Testing vLLM embedding API (direct)..."
if curl -sf http://${VLLM_HOST}:${VLLM_PORT}/health &>/dev/null; then
    echo -e "${GREEN}✓ vLLM embedding API is responding${NC}"
else
    echo -e "${RED}✗ vLLM embedding API is NOT responding${NC}"
    echo "  Check logs: ssh root@${VLLM_HOST} 'journalctl -u vllm-embedding -n 50'"
    exit 1
fi
echo ""

# Test 4: List models
echo "4. Listing available models..."
MODELS=$(curl -sf http://${VLLM_HOST}:${VLLM_PORT}/v1/models)
if echo "$MODELS" | jq -e '.data[0].id' &>/dev/null; then
    MODEL_ID=$(echo "$MODELS" | jq -r '.data[0].id')
    echo -e "${GREEN}✓ Model loaded: ${MODEL_ID}${NC}"
    
    # Check if it's the correct model
    if [[ "$MODEL_ID" == *"Qwen3-Embedding-8B"* ]] || [[ "$MODEL_ID" == "qwen3-embedding" ]]; then
        echo -e "${GREEN}✓ Correct model: Qwen3-Embedding-8B${NC}"
    else
        echo -e "${YELLOW}⚠ Unexpected model: ${MODEL_ID}${NC}"
        echo "  Expected: Qwen3-Embedding-8B or qwen3-embedding"
    fi
else
    echo -e "${RED}✗ Failed to list models${NC}"
    echo "$MODELS"
    exit 1
fi
echo ""

# Test 5: Generate test embedding (direct to vLLM)
echo "5. Testing embedding generation (direct to vLLM)..."
EMBED_RESPONSE=$(curl -sf http://${VLLM_HOST}:${VLLM_PORT}/v1/embeddings \
    -H "Content-Type: application/json" \
    -d '{
        "model": "qwen3-embedding",
        "input": "This is a test document for semantic search."
    }')

if echo "$EMBED_RESPONSE" | jq -e '.data[0].embedding' &>/dev/null; then
    EMBEDDING_DIM=$(echo "$EMBED_RESPONSE" | jq '.data[0].embedding | length')
    echo -e "${GREEN}✓ Embedding generated successfully${NC}"
    echo "  Embedding dimension: ${EMBEDDING_DIM}"
    
    if [ "$EMBEDDING_DIM" -eq 4096 ]; then
        echo -e "${GREEN}✓ Correct dimension: 4096${NC}"
    else
        echo -e "${YELLOW}⚠ Unexpected dimension: ${EMBEDDING_DIM} (expected 4096)${NC}"
    fi
    
    # Show first 5 values
    echo "  First 5 values:"
    echo "$EMBED_RESPONSE" | jq -r '.data[0].embedding[0:5]'
else
    echo -e "${RED}✗ Failed to generate embedding${NC}"
    echo "$EMBED_RESPONSE"
    exit 1
fi
echo ""

# Test 6: Check liteLLM service
echo "6. Checking liteLLM service..."
if ssh root@${LITELLM_HOST} "systemctl is-active litellm" &>/dev/null; then
    echo -e "${GREEN}✓ liteLLM service is running${NC}"
else
    echo -e "${RED}✗ liteLLM service is NOT running${NC}"
    echo "  Fix: ssh root@${LITELLM_HOST} 'systemctl start litellm'"
    exit 1
fi
echo ""

# Test 7: Test liteLLM proxy routing
echo "7. Testing liteLLM proxy routing..."

# Build curl command with optional API key
if [ -n "$LITELLM_API_KEY" ]; then
    LITELLM_RESPONSE=$(curl -sf http://${LITELLM_HOST}:${LITELLM_PORT}/v1/embeddings \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${LITELLM_API_KEY}" \
        -d '{
            "model": "qwen3-embedding",
            "input": "Testing liteLLM proxy routing to vLLM embedding service."
        }')
else
    echo -e "${YELLOW}⚠ No LITELLM_API_KEY set, trying without authentication${NC}"
    LITELLM_RESPONSE=$(curl -sf http://${LITELLM_HOST}:${LITELLM_PORT}/v1/embeddings \
        -H "Content-Type: application/json" \
        -d '{
            "model": "qwen3-embedding",
            "input": "Testing liteLLM proxy routing to vLLM embedding service."
        }')
fi

if echo "$LITELLM_RESPONSE" | jq -e '.data[0].embedding' &>/dev/null; then
    LITELLM_DIM=$(echo "$LITELLM_RESPONSE" | jq '.data[0].embedding | length')
    echo -e "${GREEN}✓ liteLLM proxy routing works${NC}"
    echo "  Embedding dimension: ${LITELLM_DIM}"
    
    if [ "$LITELLM_DIM" -eq 4096 ]; then
        echo -e "${GREEN}✓ Correct dimension: 4096${NC}"
    else
        echo -e "${YELLOW}⚠ Unexpected dimension: ${LITELLM_DIM} (expected 4096)${NC}"
    fi
else
    echo -e "${RED}✗ liteLLM proxy routing failed${NC}"
    echo "$LITELLM_RESPONSE"
    echo ""
    echo "Check liteLLM config:"
    echo "  ssh root@${LITELLM_HOST} 'cat /opt/litellm/config.yaml | grep -A 10 qwen3-embedding'"
    exit 1
fi
echo ""

# Test 8: Batch embedding test
echo "8. Testing batch embedding (5 texts)..."
BATCH_RESPONSE=$(curl -sf http://${VLLM_HOST}:${VLLM_PORT}/v1/embeddings \
    -H "Content-Type: application/json" \
    -d '{
        "model": "qwen3-embedding",
        "input": [
            "First document about machine learning",
            "Second document about artificial intelligence",
            "Third document about neural networks",
            "Fourth document about deep learning",
            "Fifth document about natural language processing"
        ]
    }')

if echo "$BATCH_RESPONSE" | jq -e '.data | length' &>/dev/null; then
    BATCH_COUNT=$(echo "$BATCH_RESPONSE" | jq '.data | length')
    echo -e "${GREEN}✓ Batch embedding successful${NC}"
    echo "  Generated ${BATCH_COUNT} embeddings"
    
    if [ "$BATCH_COUNT" -eq 5 ]; then
        echo -e "${GREEN}✓ Correct batch size: 5${NC}"
    else
        echo -e "${YELLOW}⚠ Unexpected batch size: ${BATCH_COUNT} (expected 5)${NC}"
    fi
else
    echo -e "${RED}✗ Batch embedding failed${NC}"
    echo "$BATCH_RESPONSE"
    exit 1
fi
echo ""

# Test 9: Performance test
echo "9. Running performance test (10 embeddings)..."
START_TIME=$(date +%s.%N)
for i in {1..10}; do
    curl -sf http://${VLLM_HOST}:${VLLM_PORT}/v1/embeddings \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"qwen3-embedding\", \"input\": \"Performance test document number $i\"}" \
        > /dev/null
done
END_TIME=$(date +%s.%N)
DURATION=$(echo "$END_TIME - $START_TIME" | bc)
AVG_TIME=$(echo "scale=3; $DURATION / 10" | bc)

echo -e "${GREEN}✓ Performance test complete${NC}"
echo "  Total time: ${DURATION}s"
echo "  Average per embedding: ${AVG_TIME}s"

if (( $(echo "$AVG_TIME < 1.0" | bc -l) )); then
    echo -e "${GREEN}✓ Performance is good (<1s per embedding)${NC}"
else
    echo -e "${YELLOW}⚠ Performance is slow (>${AVG_TIME}s per embedding)${NC}"
    echo "  Consider checking GPU utilization"
fi
echo ""

# Summary
echo "========================================"
echo -e "${GREEN}All tests passed!${NC}"
echo "========================================"
echo ""
echo "Summary:"
echo "  - vLLM embedding service: Running on ${VLLM_HOST}:${VLLM_PORT}"
echo "  - Model: Qwen3-Embedding-8B (4096 dimensions)"
echo "  - liteLLM proxy: Routing correctly on ${LITELLM_HOST}:${LITELLM_PORT}"
echo "  - Average latency: ${AVG_TIME}s per embedding"
echo ""
echo "Next steps:"
echo "  1. Deploy ingest service: cd provision/ansible && make ingest"
echo "  2. Upload test document via AI Portal"
echo "  3. Monitor processing: bash scripts/check-document-status.sh"
echo ""

