#!/usr/bin/env bash
#
# Test LiteLLM API with Database Operations
#
set -e

LITELLM_URL="http://localhost:4000"
MASTER_KEY=$(grep '^LITELLM_MASTER_KEY=' /etc/default/litellm | cut -d'=' -f2)

echo "=========================================="
echo "LiteLLM API Test (with Database)"
echo "=========================================="
echo ""

# Test 1: Health check
echo "[1/5] Testing health endpoint..."
HEALTH=$(curl -s "${LITELLM_URL}/health")
if echo "$HEALTH" | grep -q "healthy"; then
    echo "  ✓ Health check passed"
else
    echo "  ✗ Health check failed: $HEALTH"
fi
echo ""

# Test 2: List models
echo "[2/5] Testing models endpoint..."
MODELS=$(curl -s -H "Authorization: Bearer ${MASTER_KEY}" "${LITELLM_URL}/v1/models")
MODEL_COUNT=$(echo "$MODELS" | grep -o '"id"' | wc -l)
echo "  ✓ Found $MODEL_COUNT models"
echo "$MODELS" | grep '"id"' | head -5
echo ""

# Test 3: Make a simple completion request (logs to database)
echo "[3/5] Testing chat completions (logs to database)..."
RESPONSE=$(curl -s -X POST "${LITELLM_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "phi-4-multimodal",
    "messages": [{"role": "user", "content": "Say hello in one word"}],
    "max_tokens": 10
  }')

if echo "$RESPONSE" | grep -q '"choices"'; then
    echo "  ✓ Chat completion successful"
    echo "$RESPONSE" | grep -o '"content":"[^"]*"' | head -1
else
    echo "  ✗ Chat completion failed"
    echo "  Response: $RESPONSE"
fi
echo ""

# Test 4: Check spend logs in database
echo "[4/5] Checking database spend logs..."
sudo -u postgres psql -d litellm -c "SELECT COUNT(*) as log_count FROM \"LiteLLM_SpendLogs\";" 2>/dev/null && \
    echo "  ✓ Database logging is working" || \
    echo "  ✗ Cannot query spend logs"
echo ""

# Test 5: Admin UI login
echo "[5/5] Testing admin UI..."
if curl -s "${LITELLM_URL}/" | grep -q "LiteLLM"; then
    echo "  ✓ Admin UI is accessible"
    echo "  Login at: ${LITELLM_URL}/"
    echo "  Password: (use LITELLM_MASTER_KEY from /etc/default/litellm)"
else
    echo "  ✗ Admin UI not accessible"
fi
echo ""

echo "=========================================="
echo "API test complete!"
echo "=========================================="

