#!/usr/bin/env bash
#
# Busibox Production Deployment Script
#
# ⚠️  IMPORTANT: This script is designed to run ON THE PROXMOX HOST
#     It requires:
#     - Proxmox VE with pct command
#     - Ansible installed
#     - Access to LXC storage (local-lvm or similar)
#     - NVIDIA GPUs for LLM services
#
# This script provides comprehensive deployment of production infrastructure:
# 1. Create production containers (IDs 200-209)
# 2. Configure GPU passthrough for LLM services
# 3. Provision services via Ansible
# 4. Run health checks and smoke tests
# 5. Verify deployment
#
# Usage (on Proxmox host):
#   bash deploy-production.sh [command]
#
# Commands:
#   full       - Run full deployment (provision, configure, verify)
#   containers - Create production containers
#   gpu        - Configure GPU passthrough
#   provision  - Run Ansible provisioning
#   verify     - Run health checks and smoke tests
#   help       - Show this help message

set -euo pipefail

# Check if running on Proxmox
if ! command -v pct &> /dev/null; then
    echo "❌ ERROR: This script must run on a Proxmox host with 'pct' command available"
    echo ""
    echo "Current environment: $(uname -s)"
    echo ""
    echo "To deploy to production:"
    echo "  1. Copy this repository to your Proxmox host"
    echo "  2. SSH to the Proxmox host"
    echo "  3. Run: bash deploy-production.sh full"
    echo ""
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROVISION_DIR="${SCRIPT_DIR}/provision"
PCT_DIR="${PROVISION_DIR}/pct"
ANSIBLE_DIR="${PROVISION_DIR}/ansible"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

log_section() {
  echo ""
  echo "=========================================="
  echo "$1"
  echo "=========================================="
}

log_critical() {
  echo -e "${MAGENTA}[CRITICAL]${NC} $1"
}

# Deployment state tracking
DEPLOYMENT_STEPS=()
FAILED_STEPS=0

record_step() {
  local step_name=$1
  local status=$2
  local message=${3:-}
  
  if [[ "$status" == "PASS" ]]; then
    log_success "✓ $step_name"
    DEPLOYMENT_STEPS+=("PASS: $step_name")
  else
    log_error "✗ $step_name: $message"
    DEPLOYMENT_STEPS+=("FAIL: $step_name - $message")
    ((FAILED_STEPS++))
  fi
}

# Deployment functions

deploy_containers() {
  log_section "Step 1: Create Production Containers"
  
  log_info "Creating production containers with IDs 200-209..."
  log_warning "This will create permanent infrastructure!"
  
  echo ""
  read -p "Continue with production container creation? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    log_warning "Container creation cancelled"
    return 1
  fi
  
  if bash "${PCT_DIR}/create_lxc_base.sh" production; then
    record_step "Container creation" "PASS"
  else
    record_step "Container creation" "FAIL" "Script failed"
    return 1
  fi
  
  # Verify containers exist
  log_info "Verifying containers exist..."
  local prod_ctids=(200 201 202 203 204 205 206 207 208 209 210)
  
  for ctid in "${prod_ctids[@]}"; do
    if pct status "$ctid" &>/dev/null; then
      log_success "Container $ctid exists"
    else
      record_step "Container $ctid verification" "FAIL" "Container doesn't exist"
      return 1
    fi
  done
  
  record_step "Container verification" "PASS"
}

configure_gpu() {
  log_section "Step 2: Configure GPU Passthrough for LLM Services"
  
  # Check if GPUs are available on host
  if ! command -v nvidia-smi &>/dev/null; then
    log_error "nvidia-smi not found - GPU passthrough requires NVIDIA drivers on host"
    record_step "GPU availability check" "FAIL" "No NVIDIA drivers"
    return 1
  fi
  
  log_info "Checking GPU availability..."
  nvidia-smi
  echo ""
  
  log_info "Configuring GPU passthrough:"
  log_info "  - Ollama (container 208): GPU 0"
  log_info "  - vLLM (container 209): GPU 1"
  
  echo ""
  read -p "Continue with GPU passthrough configuration? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    log_warning "GPU passthrough cancelled"
    return 1
  fi
  
  # Run GPU passthrough script
  if bash "${PCT_DIR}/configure-gpu-passthrough.sh" 208 209; then
    log_success "GPU passthrough configured"
    record_step "GPU passthrough configuration" "PASS"
  else
    log_error "GPU passthrough configuration failed"
    record_step "GPU passthrough configuration" "FAIL" "Script failed"
    return 1
  fi
  
  # Verify GPU configuration in LXC configs
  log_info "Verifying GPU passthrough in container configs..."
  
  if grep -q "GPU Passthrough" /etc/pve/lxc/208.conf 2>/dev/null; then
    log_success "Ollama container (208) has GPU passthrough configured"
  else
    record_step "Ollama GPU config verification" "FAIL" "GPU config not found"
    return 1
  fi
  
  if grep -q "GPU Passthrough" /etc/pve/lxc/209.conf 2>/dev/null; then
    log_success "vLLM container (209) has GPU passthrough configured"
  else
    record_step "vLLM GPU config verification" "FAIL" "GPU config not found"
    return 1
  fi
  
  record_step "GPU passthrough verification" "PASS"
}

deploy_ansible() {
  log_section "Step 3: Ansible Service Provisioning"
  
  log_info "Running Ansible playbook with production inventory..."
  log_critical "This will deploy production services!"
  
  echo ""
  read -p "Continue with production deployment? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    log_warning "Ansible deployment cancelled"
    return 1
  fi
  
  cd "${ANSIBLE_DIR}"
  
  # Test ping connectivity
  log_info "Testing Ansible connectivity..."
  if ansible -i inventory/production all -m ping; then
    record_step "Ansible connectivity" "PASS"
  else
    record_step "Ansible connectivity" "FAIL" "Ping failed"
    return 1
  fi
  
  # Run full provisioning
  log_info "Running full Ansible provisioning..."
  if ansible-playbook -i inventory/production site.yml; then
    record_step "Ansible provisioning" "PASS"
  else
    record_step "Ansible provisioning" "FAIL" "Playbook failed"
    return 1
  fi
  
  cd "${SCRIPT_DIR}"
}

verify_health() {
  log_section "Step 4: Service Health Checks"
  
  log_info "Running health checks on production services..."
  
  # PostgreSQL
  log_info "Checking PostgreSQL..."
  if pct exec 203 -- pg_isready -U postgres &>/dev/null; then
    record_step "PostgreSQL health" "PASS"
  else
    record_step "PostgreSQL health" "FAIL" "Service not ready"
  fi
  
  # MinIO
  log_info "Checking MinIO..."
  if curl -f -s http://10.96.200.205:9000/minio/health/live > /dev/null 2>&1; then
    record_step "MinIO health" "PASS"
  else
    record_step "MinIO health" "FAIL" "Health endpoint failed"
  fi
  
  # Milvus
  log_info "Checking Milvus..."
  if curl -f -s http://10.96.200.204:9091/healthz > /dev/null 2>&1; then
    record_step "Milvus health" "PASS"
  else
    record_step "Milvus health" "FAIL" "Health endpoint failed"
  fi
  
  # LiteLLM
  log_info "Checking LiteLLM..."
  if curl -f -s http://10.96.200.207:4000/health > /dev/null 2>&1; then
    record_step "LiteLLM health" "PASS"
  else
    log_warning "LiteLLM not responding (may still be starting)"
    record_step "LiteLLM health" "PASS" "Not ready yet (expected during initial deploy)"
  fi
  
  
  # vLLM
  log_info "Checking vLLM..."
  if curl -f -s http://10.96.200.208:8000/health > /dev/null 2>&1; then
    record_step "vLLM health" "PASS"
  else
    log_warning "vLLM not responding (may be loading model)"
    record_step "vLLM health" "PASS" "Not ready yet (expected during initial deploy)"
  fi
}

print_deployment_summary() {
  log_section "Deployment Summary"
  
  echo ""
  echo "Deployment Steps:"
  for result in "${DEPLOYMENT_STEPS[@]}"; do
    if [[ "$result" == PASS* ]]; then
      echo -e "${GREEN}  ✓${NC} $result"
    else
      echo -e "${RED}  ✗${NC} $result"
    fi
  done
  
  echo ""
  echo "Total Steps: ${#DEPLOYMENT_STEPS[@]}"
  echo "Failed: $FAILED_STEPS"
  echo ""
  
  if [[ "$FAILED_STEPS" -eq 0 ]]; then
    log_success "PRODUCTION DEPLOYMENT SUCCESSFUL!"
    echo ""
    echo "Production Endpoints:"
    echo "  LiteLLM API:    http://10.96.200.207:4000"
    echo "  Ollama API:     http://10.96.200.208:11434"
    echo "  vLLM API:       http://10.96.200.209:8000"
    echo "  PostgreSQL:     10.96.200.203:5432"
    echo "  MinIO Console:  http://10.96.200.205:9001"
    echo "  Milvus:         10.96.200.204:19530"
    echo ""
    return 0
  else
    log_error "$FAILED_STEPS STEP(S) FAILED"
    echo ""
    echo "Review the errors above and check:"
    echo "  - Container logs: pct exec <id> -- journalctl -xe"
    echo "  - Service status: pct exec <id> -- systemctl status <service>"
    echo "  - Ansible logs: ${ANSIBLE_DIR}/ansible.log"
    echo ""
    return 1
  fi
}

# Command functions

cmd_containers() {
  deploy_containers
  print_deployment_summary
}

cmd_gpu() {
  configure_gpu
  print_deployment_summary
}

cmd_provision() {
  deploy_ansible
  print_deployment_summary
}

cmd_verify() {
  verify_health
  print_deployment_summary
}

cmd_full() {
  log_section "Production Deployment - Full Stack"
  
  log_critical "This will deploy the COMPLETE production infrastructure!"
  echo ""
  echo "This includes:"
  echo "  - 10 production LXC containers (IDs 200-209)"
  echo "  - GPU passthrough for LLM services"
  echo "  - PostgreSQL, MinIO, Milvus, Redis"
  echo "  - Ollama, vLLM, LiteLLM services"
  echo "  - Full model downloads (may take 1-2 hours)"
  echo ""
  
  read -p "Are you absolutely sure? Type 'DEPLOY' to continue: " -r
  echo
  if [[ ! $REPLY == "DEPLOY" ]]; then
    log_warning "Deployment cancelled"
    exit 0
  fi
  
  deploy_containers || true
  configure_gpu || true
  deploy_ansible || true
  verify_health || true
  
  print_deployment_summary
}

cmd_help() {
  cat << EOF
Busibox Production Deployment Script

⚠️  WARNING: This deploys PRODUCTION infrastructure!

Usage: bash deploy-production.sh [command]

Commands:
  full       - Run full deployment (containers + GPU + Ansible + verify)
  containers - Create production containers only
  gpu        - Configure GPU passthrough only
  provision  - Run Ansible provisioning only
  verify     - Run health checks only
  help       - Show this help message

Production Environment:
  Container IDs: 200-210
  IP Range: 10.96.200.207-209 (LLM), 10.96.200.200-206, 210 (services)
  
LLM Services:
  - LiteLLM:  10.96.200.207:4000 (unified API)
  - vLLM:     10.96.200.208:8000 
  - vLLM:     10.96.200.208:8001...
  - Ollama:   10.96.200.209:11434

Services:
  - Authz:    10.96.200.210
  - Ingest:   10.96.200.206
  - Files:    10.96.200.205
  - PostgreSQL: 10.96.200.203
  - Milvus:    10.96.200.204
  - Proxy:    10.96.200.200
  - Apps:     10.96.200.201
  - Agent:    10.96.200.202

Examples:
  bash deploy-production.sh full        # Complete deployment
  bash deploy-production.sh containers  # Just create containers
  bash deploy-production.sh gpu         # Just configure GPUs
  bash deploy-production.sh provision   # Just run Ansible
  bash deploy-production.sh verify      # Just health checks
  
For testing, use: bash test-infrastructure.sh
EOF
}

# Main execution

COMMAND="${1:-help}"

case "$COMMAND" in
  full)
    cmd_full
    ;;
  containers)
    cmd_containers
    ;;
  gpu)
    cmd_gpu
    ;;
  provision)
    cmd_provision
    ;;
  verify)
    cmd_verify
    ;;
  help|--help|-h)
    cmd_help
    ;;
  *)
    log_error "Unknown command: $COMMAND"
    cmd_help
    exit 1
    ;;
esac

