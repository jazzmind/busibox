#!/bin/bash
#
# ColPali Service Test Script
#
# Purpose:
#   Comprehensive testing and diagnostics for ColPali visual embedding service
#
# Execution Context:
#   Run from: Admin workstation
#   Target: vLLM container (colpali service on port 8002)
#
# Usage:
#   bash scripts/test-colpali.sh [test|production]
#
# Environment:
#   TEST: 10.96.201.208:8002
#   PRODUCTION: 10.96.200.31:8002

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Determine environment
ENV="${1:-test}"
if [[ "$ENV" == "production" ]]; then
    COLPALI_HOST="10.96.200.31"
    CONTAINER_NAME="vllm-lxc"
else
    COLPALI_HOST="10.96.201.208"
    CONTAINER_NAME="TEST-vllm-lxc"
fi

COLPALI_PORT="8002"
COLPALI_BASE_URL="http://${COLPALI_HOST}:${COLPALI_PORT}"
COLPALI_HEALTH_URL="${COLPALI_BASE_URL}/health"
COLPALI_EMBEDDINGS_URL="${COLPALI_BASE_URL}/v1/embeddings"

# ============================================================================
# Utility Functions
# ============================================================================

print_header() {
    echo -e "\n${BLUE}======================================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}======================================================================${NC}\n"
}

print_section() {
    echo -e "\n${YELLOW}▸ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

# ============================================================================
# Test Functions
# ============================================================================

test_network_connectivity() {
    print_section "1. Network Connectivity"
    
    # Ping test
    echo -n "  Ping test... "
    if ping -c 1 -W 2 "$COLPALI_HOST" &>/dev/null; then
        print_success "Host is reachable"
    else
        print_error "Host is not reachable"
        return 1
    fi
    
    # Port test
    echo -n "  Port test... "
    if timeout 5 bash -c "echo > /dev/tcp/${COLPALI_HOST}/${COLPALI_PORT}" 2>/dev/null; then
        print_success "Port ${COLPALI_PORT} is open"
    else
        print_error "Port ${COLPALI_PORT} is not accessible"
        return 1
    fi
}

test_service_health() {
    print_section "2. Service Health Check"
    
    echo -n "  HTTP health endpoint... "
    
    RESPONSE=$(curl -s -w "\n%{http_code}" "$COLPALI_HEALTH_URL" 2>&1)
    HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
    BODY=$(echo "$RESPONSE" | head -n-1)
    
    if [[ "$HTTP_CODE" == "200" ]]; then
        print_success "Health check passed (HTTP $HTTP_CODE)"
        
        # Parse and display health info
        if command -v jq &>/dev/null; then
            STATUS=$(echo "$BODY" | jq -r '.status // "unknown"')
            MODEL=$(echo "$BODY" | jq -r '.model // "unknown"')
            DEVICE=$(echo "$BODY" | jq -r '.device // "unknown"')
            
            echo "    Status: $STATUS"
            echo "    Model:  $MODEL"
            echo "    Device: $DEVICE"
        else
            echo "    Response: $BODY"
        fi
    else
        print_error "Health check failed (HTTP $HTTP_CODE)"
        echo "    Response: $BODY"
        return 1
    fi
}

test_embedding_generation() {
    print_section "3. Embedding Generation Test"
    
    # Create a test image (1x1 white pixel PNG)
    TEST_IMAGE="/tmp/colpali_test_$(date +%s).png"
    
    # Create minimal PNG (1x1 white pixel)
    printf '\x89\x50\x4e\x47\x0d\x0a\x1a\x0a\x00\x00\x00\x0d\x49\x48\x44\x52\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90\x77\x53\xde\x00\x00\x00\x0c\x49\x44\x41\x54\x08\x99\x63\xf8\xff\xff\x3f\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00\x49\x45\x4e\x44\xae\x42\x60\x82' > "$TEST_IMAGE"
    
    # Encode to base64
    BASE64_IMAGE=$(base64 -w 0 "$TEST_IMAGE" 2>/dev/null || base64 "$TEST_IMAGE")
    
    # Create JSON request
    JSON_REQUEST=$(cat <<EOF
{
  "input": ["$BASE64_IMAGE"],
  "model": "colpali",
  "encoding_format": "float"
}
EOF
)
    
    echo "  Generating embedding..."
    
    START_TIME=$(date +%s.%N)
    RESPONSE=$(curl -s -w "\n%{http_code}" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer EMPTY" \
        -d "$JSON_REQUEST" \
        "$COLPALI_EMBEDDINGS_URL" 2>&1)
    END_TIME=$(date +%s.%N)
    
    HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
    BODY=$(echo "$RESPONSE" | head -n-1)
    
    ELAPSED=$(echo "$END_TIME - $START_TIME" | bc)
    
    # Cleanup
    rm -f "$TEST_IMAGE"
    
    if [[ "$HTTP_CODE" == "200" ]]; then
        print_success "Embedding generated successfully"
        echo "    Time: ${ELAPSED}s"
        
        if command -v jq &>/dev/null; then
            EMBEDDING_LENGTH=$(echo "$BODY" | jq -r '.data[0].embedding | length')
            echo "    Embedding length: $EMBEDDING_LENGTH"
            
            # Validate embedding length (should be multiple of 128)
            if (( EMBEDDING_LENGTH % 128 == 0 )); then
                NUM_PATCHES=$((EMBEDDING_LENGTH / 128))
                print_success "Valid ColPali embedding structure"
                echo "    Patches: $NUM_PATCHES"
                echo "    Dimensions per patch: 128"
            else
                print_warning "Unexpected embedding length"
            fi
        fi
    else
        print_error "Embedding generation failed (HTTP $HTTP_CODE)"
        echo "    Response: ${BODY:0:200}"
        return 1
    fi
}

test_service_on_container() {
    print_section "4. Container Service Status"
    
    echo "  Checking systemd service..."
    
    # SSH to container and check service status
    SERVICE_STATUS=$(ssh root@"$COLPALI_HOST" "systemctl is-active colpali" 2>&1 || echo "inactive")
    
    if [[ "$SERVICE_STATUS" == "active" ]]; then
        print_success "colpali.service is active"
    else
        print_error "colpali.service is $SERVICE_STATUS"
        
        echo ""
        print_info "Service logs (last 20 lines):"
        ssh root@"$COLPALI_HOST" "journalctl -u colpali -n 20 --no-pager" 2>&1 | sed 's/^/    /'
        return 1
    fi
    
    # Check GPU usage
    echo ""
    echo "  GPU usage:"
    ssh root@"$COLPALI_HOST" "nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader" 2>&1 | sed 's/^/    /'
}

test_model_files() {
    print_section "5. Model Files Check"
    
    echo "  Checking model cache..."
    
    MODEL_DIR="/var/lib/llm-models/huggingface/hub"
    
    # Check ColPali model
    COLPALI_MODEL_PATH="${MODEL_DIR}/models--vidore--colpali-v1.3"
    if ssh root@"$COLPALI_HOST" "test -d $COLPALI_MODEL_PATH" 2>/dev/null; then
        print_success "ColPali model cached"
        SIZE=$(ssh root@"$COLPALI_HOST" "du -sh $COLPALI_MODEL_PATH" 2>&1 | awk '{print $1}')
        echo "    Size: $SIZE"
    else
        print_warning "ColPali model not cached (will download on first use)"
    fi
    
    # Check PaliGemma base model
    PALIGEMMA_MODEL_PATH="${MODEL_DIR}/models--google--paligemma-3b-pt-448"
    if ssh root@"$COLPALI_HOST" "test -d $PALIGEMMA_MODEL_PATH" 2>/dev/null; then
        print_success "PaliGemma base model cached"
        SIZE=$(ssh root@"$COLPALI_HOST" "du -sh $PALIGEMMA_MODEL_PATH" 2>&1 | awk '{print $1}')
        echo "    Size: $SIZE"
    else
        print_warning "PaliGemma model not cached (~11GB will download on first use)"
    fi
}

run_python_tests() {
    print_section "6. Python Integration Tests"
    
    if [[ ! -f "$PROJECT_ROOT/srv/ingest/tests/test_colpali.py" ]]; then
        print_warning "Python tests not found"
        return 0
    fi
    
    echo "  Running pytest suite..."
    cd "$PROJECT_ROOT/srv/ingest"
    
    # Set environment variables
    export COLPALI_BASE_URL="$COLPALI_BASE_URL"
    export COLPALI_API_KEY="EMPTY"
    export COLPALI_ENABLED="true"
    
    if python -m pytest tests/test_colpali.py -v --tb=short 2>&1 | tee /tmp/colpali_pytest.log; then
        print_success "Python tests passed"
    else
        print_error "Some Python tests failed"
        echo ""
        print_info "See full output in /tmp/colpali_pytest.log"
        return 1
    fi
}

generate_diagnostic_report() {
    print_section "7. Diagnostic Report"
    
    REPORT_FILE="/tmp/colpali_diagnostic_${ENV}_$(date +%Y%m%d_%H%M%S).txt"
    
    {
        echo "ColPali Diagnostic Report"
        echo "========================="
        echo "Generated: $(date)"
        echo "Environment: $ENV"
        echo "Host: $COLPALI_HOST"
        echo "Port: $COLPALI_PORT"
        echo ""
        
        echo "Configuration"
        echo "-------------"
        echo "Base URL: $COLPALI_BASE_URL"
        echo "Health URL: $COLPALI_HEALTH_URL"
        echo "Embeddings URL: $COLPALI_EMBEDDINGS_URL"
        echo ""
        
        echo "Network Test"
        echo "------------"
        ping -c 3 "$COLPALI_HOST" || echo "Ping failed"
        echo ""
        
        echo "Health Check"
        echo "------------"
        curl -s "$COLPALI_HEALTH_URL" | jq '.' 2>&1 || curl -s "$COLPALI_HEALTH_URL"
        echo ""
        
        echo "Service Status"
        echo "-------------"
        ssh root@"$COLPALI_HOST" "systemctl status colpali --no-pager" 2>&1
        echo ""
        
        echo "Recent Logs"
        echo "-----------"
        ssh root@"$COLPALI_HOST" "journalctl -u colpali -n 50 --no-pager" 2>&1
        echo ""
        
        echo "GPU Status"
        echo "----------"
        ssh root@"$COLPALI_HOST" "nvidia-smi" 2>&1
        echo ""
        
    } > "$REPORT_FILE"
    
    print_success "Diagnostic report saved to: $REPORT_FILE"
}

show_troubleshooting_tips() {
    print_section "Troubleshooting Tips"
    
    echo ""
    echo "  Common Issues:"
    echo ""
    echo "  1. Service Not Running:"
    echo "     ssh root@$COLPALI_HOST"
    echo "     systemctl status colpali"
    echo "     journalctl -u colpali -n 50 --no-pager"
    echo ""
    echo "  2. Model Not Loaded:"
    echo "     • Check HuggingFace token in vault"
    echo "     • Accept PaliGemma license: https://huggingface.co/google/paligemma-3b-pt-448"
    echo "     • Check disk space: df -h"
    echo ""
    echo "  3. GPU Issues:"
    echo "     ssh root@$COLPALI_HOST"
    echo "     nvidia-smi"
    echo "     • Check GPU 2 is available"
    echo "     • Check VRAM usage (needs ~8-10GB)"
    echo ""
    echo "  4. Re-deploy Service:"
    echo "     cd provision/ansible"
    echo "     make colpali ENV=$ENV"
    echo ""
}

# ============================================================================
# Main
# ============================================================================

main() {
    print_header "ColPali Service Test Suite - ${ENV^^} Environment"
    
    echo "Target: $COLPALI_BASE_URL"
    echo "Container: $CONTAINER_NAME"
    echo ""
    
    # Run tests
    FAILED_TESTS=0
    
    test_network_connectivity || ((FAILED_TESTS++))
    test_service_health || ((FAILED_TESTS++))
    test_embedding_generation || ((FAILED_TESTS++))
    test_service_on_container || ((FAILED_TESTS++))
    test_model_files || ((FAILED_TESTS++))
    
    # Optional: Python tests
    if [[ "${RUN_PYTHON_TESTS:-0}" == "1" ]]; then
        run_python_tests || ((FAILED_TESTS++))
    fi
    
    # Generate report
    generate_diagnostic_report
    
    # Summary
    print_header "Test Summary"
    
    if [[ $FAILED_TESTS -eq 0 ]]; then
        print_success "All tests passed! ColPali is working correctly."
        echo ""
        print_info "ColPali is ready to use for visual document embeddings"
    else
        print_error "$FAILED_TESTS test(s) failed"
        show_troubleshooting_tips
        exit 1
    fi
}

# Help text
if [[ "${1:-}" == "-h" ]] || [[ "${1:-}" == "--help" ]]; then
    cat <<EOF
ColPali Service Test Script

Usage:
  $0 [test|production] [options]

Arguments:
  test|production    Environment to test (default: test)

Options:
  -h, --help         Show this help message
  --with-python      Run Python integration tests

Environment Variables:
  RUN_PYTHON_TESTS=1  Enable Python tests

Examples:
  # Test default (test environment)
  $0

  # Test production
  $0 production

  # Test with Python integration tests
  RUN_PYTHON_TESTS=1 $0 test

EOF
    exit 0
fi

# Check for --with-python flag
if [[ "${2:-}" == "--with-python" ]]; then
    export RUN_PYTHON_TESTS=1
fi

main

