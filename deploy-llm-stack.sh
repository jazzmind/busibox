#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   log_error "This script must be run as root"
   exit 1
fi

# Check if running on Proxmox
if ! command -v pct &> /dev/null; then
    log_error "This script must run on a Proxmox host"
    exit 1
fi

MODE="${1:-test}"

if [[ "$MODE" != "test" && "$MODE" != "prod" ]]; then
    log_error "Usage: $0 [test|prod]"
    log_info "  test: Deploy to test environment (default)"
    log_info "  prod: Deploy to production environment"
    exit 1
fi

log_info "Deploying LLM stack to ${MODE} environment..."
echo ""

# Source the appropriate vars
if [[ "$MODE" == "test" ]]; then
    VARS_FILE="${SCRIPT_DIR}/provision/pct/test-vars.env"
    INVENTORY="test"
else
    VARS_FILE="${SCRIPT_DIR}/provision/pct/vars.env"
    INVENTORY="production"
fi

if [[ ! -f "$VARS_FILE" ]]; then
    log_error "Vars file not found: $VARS_FILE"
    exit 1
fi

source "$VARS_FILE"

# Step 1: Create LXC containers
log_info "Step 1: Creating LXC containers..."
cd "${SCRIPT_DIR}/provision/pct"

if [[ "$MODE" == "test" ]]; then
    bash create_lxc_base.sh test
else
    bash create_lxc_base.sh prod
fi

log_success "Containers created"
echo ""

# Step 2: Configure GPU passthrough
log_info "Step 2: Configuring GPU passthrough..."

if [[ "$MODE" == "test" ]]; then
    bash configure-gpu-passthrough.sh "${CT_OLLAMA_TEST}" 0
    bash configure-gpu-passthrough.sh "${CT_VLLM_TEST}" 1
else
    bash configure-gpu-passthrough.sh "${CT_OLLAMA}" 0
    bash configure-gpu-passthrough.sh "${CT_VLLM}" 1
fi

log_success "GPU passthrough configured"
echo ""

# Step 3: Wait for containers to be ready
log_info "Step 3: Waiting for containers to boot and be ready for SSH..."
sleep 10

log_success "Containers ready"
echo ""

# Step 4: Deploy with Ansible
log_info "Step 4: Deploying services with Ansible..."
cd "${SCRIPT_DIR}/provision/ansible"

ansible-playbook \
    -i "inventory/${INVENTORY}" \
    --limit llm_services \
    site.yml

log_success "Ansible deployment complete"
echo ""

# Step 5: Test the services
log_info "Step 5: Testing deployed services..."

if [[ "$MODE" == "test" ]]; then
    OLLAMA_IP="${IP_OLLAMA_TEST}"
    VLLM_IP="${IP_VLLM_TEST}"
    LITELLM_IP="${IP_LITELLM_TEST}"
else
    OLLAMA_IP="${IP_OLLAMA}"
    VLLM_IP="${IP_VLLM}"
    LITELLM_IP="${IP_LITELLM}"
fi

# Test Ollama
log_info "Testing Ollama at ${OLLAMA_IP}:11434..."
if curl -s "http://${OLLAMA_IP}:11434/api/version" | grep -q "version"; then
    log_success "Ollama is responding"
else
    log_warning "Ollama not responding yet (may still be starting up)"
fi

# Test vLLM
log_info "Testing vLLM at ${VLLM_IP}:8000..."
if curl -s "http://${VLLM_IP}:8000/health" | grep -q "ok"; then
    log_success "vLLM is responding"
else
    log_warning "vLLM not responding yet (may still be loading model)"
fi

# Test LiteLLM
log_info "Testing LiteLLM at ${LITELLM_IP}:4000..."
if curl -s "http://${LITELLM_IP}:4000/health" | grep -q "healthy"; then
    log_success "LiteLLM is responding"
else
    log_warning "LiteLLM not responding yet (may still be starting up)"
fi

echo ""
log_success "=========================================="
log_success "LLM Stack Deployment Complete!"
log_success "=========================================="
echo ""
log_info "Service endpoints:"
log_info "  Ollama:   http://${OLLAMA_IP}:11434"
log_info "  vLLM:     http://${VLLM_IP}:8000"
log_info "  LiteLLM:  http://${LITELLM_IP}:4000"
echo ""
log_info "To verify GPU access in containers:"
log_info "  pct exec <CTID> -- nvidia-smi"
log_info ""
log_info "  Ollama container: pct exec $(eval echo \$CT_OLLAMA${MODE:+_TEST}) -- nvidia-smi"
log_info "  vLLM container:   pct exec $(eval echo \$CT_VLLM${MODE:+_TEST}) -- nvidia-smi"
echo ""
log_info "To test with a model:"
log_info "  curl http://${OLLAMA_IP}:11434/api/generate -d '{\"model\":\"qwen2.5:0.5b\",\"prompt\":\"Hello\"}'"
echo ""

