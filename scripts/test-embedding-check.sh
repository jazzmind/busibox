#!/bin/bash
#
# Test Embedding Model Cache Check
# Run this on Proxmox to test if the check_model_cached function works

set -e

FASTEMBED_CACHE="/var/lib/embedding-models/fastembed"

echo "Testing embedding model cache check..."
echo "Cache directory: ${FASTEMBED_CACHE}"
echo ""

# Source the check function from setup script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../provision/pct/host" && pwd)"
source "${SCRIPT_DIR}/setup-embedding-models.sh"

# Test models
TEST_MODELS=(
    "BAAI/bge-small-en-v1.5"
    "BAAI/bge-base-en-v1.5"
    "BAAI/bge-large-en-v1.5"
)

echo "Testing model detection:"
echo "======================="
for model in "${TEST_MODELS[@]}"; do
    echo -n "Checking ${model}... "
    if check_model_cached "$model"; then
        echo "✓ FOUND (cached)"
    else
        echo "✗ NOT FOUND"
    fi
done

echo ""
echo "Actual cache contents:"
echo "====================="
ls -la "${FASTEMBED_CACHE}/" 2>/dev/null || echo "Cache directory doesn't exist"
