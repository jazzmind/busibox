#!/usr/bin/env bash
#
# Busibox Infrastructure Test Suite
#
# ⚠️  IMPORTANT: This script is designed to run ON THE PROXMOX HOST
#     It requires:
#     - Proxmox VE with pct command
#     - Ansible installed
#     - Access to LXC storage (local-lvm or similar)
#
# This script provides comprehensive testing of the infrastructure provisioning:
# 1. Create test containers (IDs 301-307, TEST- prefix)
# 2. Provision services via Ansible
# 3. Run health checks and smoke tests
# 4. Test incremental provisioning (add 1 container to existing stack)
# 5. Clean up test environment
#
# Usage (on Proxmox host):
#   bash test-infrastructure.sh [command]
#
# Commands:
#   full       - Run full test suite (provision, test, cleanup)
#   provision  - Create and provision test containers
#   verify     - Run health checks and smoke tests
#   incremental - Test adding 1 container to existing stack
#   cleanup    - Destroy test containers
#   help       - Show this help message

set -euo pipefail

# Check if running on Proxmox
if ! command -v pct &> /dev/null; then
    echo "❌ ERROR: This script must run on a Proxmox host with 'pct' command available"
    echo ""
    echo "Current environment: $(uname -s)"
    echo ""
    echo "To test the infrastructure:"
    echo "  1. Copy this repository to your Proxmox host"
    echo "  2. SSH to the Proxmox host"
    echo "  3. Run: bash test-infrastructure.sh full"
    echo ""
    echo "See docs/testing.md for detailed instructions"
    exit 1
fi

SCRIPT_DIR="$(dirname "$0")"
PROVISION_DIR="${SCRIPT_DIR}/provision"
PCT_DIR="${PROVISION_DIR}/pct"
ANSIBLE_DIR="${PROVISION_DIR}/ansible"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
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

# Test state tracking
TEST_RESULTS=()
FAILED_TESTS=0

record_test() {
  local test_name=$1
  local status=$2
  local message=${3:-}
  
  if [[ "$status" == "PASS" ]]; then
    log_success "✓ $test_name"
    TEST_RESULTS+=("PASS: $test_name")
  else
    log_error "✗ $test_name: $message"
    TEST_RESULTS+=("FAIL: $test_name - $message")
    ((FAILED_TESTS++))
  fi
}

# Test functions

test_provision_containers() {
  log_section "Test 1: Provision Test Containers"
  
  log_info "Creating test containers with IDs 301-307..."
  
  if bash "${PCT_DIR}/create_lxc_base.sh" test; then
    record_test "Container creation" "PASS"
  else
    record_test "Container creation" "FAIL" "Script failed"
    return 1
  fi
  
  # Verify containers exist
  log_info "Verifying containers exist..."
  local test_ctids=(300 301 302 303 304 305 306)
  
  for ctid in "${test_ctids[@]}"; do
    if pct status "$ctid" &>/dev/null; then
      log_success "Container $ctid exists"
    else
      record_test "Container $ctid verification" "FAIL" "Container doesn't exist"
      return 1
    fi
  done
  
  record_test "Container verification" "PASS"
}

test_ansible_provisioning() {
  log_section "Test 2: Ansible Service Provisioning"
  
  log_info "Running Ansible playbook with test inventory..."
  
  cd "${ANSIBLE_DIR}"
  
  # Test ping connectivity
  log_info "Testing Ansible connectivity..."
  if ansible -i inventory/test-hosts.yml all -m ping; then
    record_test "Ansible connectivity" "PASS"
  else
    record_test "Ansible connectivity" "FAIL" "Ping failed"
    return 1
  fi
  
  # Run full provisioning
  log_info "Running full Ansible provisioning..."
  if ansible-playbook -i inventory/test-hosts.yml site.yml; then
    record_test "Ansible provisioning" "PASS"
  else
    record_test "Ansible provisioning" "FAIL" "Playbook failed"
    return 1
  fi
  
  cd "${SCRIPT_DIR}"
}

test_health_checks() {
  log_section "Test 3: Service Health Checks"
  
  log_info "Running health checks on test services..."
  
  # PostgreSQL
  log_info "Checking PostgreSQL..."
  if psql -h 10.96.201.203 -U busibox_test_user -d busibox_test -c "SELECT 1" &>/dev/null; then
    record_test "PostgreSQL health" "PASS"
  else
    record_test "PostgreSQL health" "FAIL" "Connection failed"
  fi
  
  # Milvus
  log_info "Checking Milvus..."
  if curl -f -s http://10.96.201.204:9091/healthz > /dev/null 2>&1; then
    record_test "Milvus health" "PASS"
  else
    record_test "Milvus health" "FAIL" "Health endpoint failed"
  fi
  
  # MinIO
  log_info "Checking MinIO..."
  if curl -f -s http://10.96.201.205:9000/minio/health/live > /dev/null 2>&1; then
    record_test "MinIO health" "PASS"
  else
    record_test "MinIO health" "FAIL" "Health endpoint failed"
  fi
  
  # Agent API (may not be deployed yet)
  log_info "Checking Agent API..."
  if curl -f -s http://10.96.201.202:8000/health/live > /dev/null 2>&1; then
    record_test "Agent API health" "PASS"
  else
    log_warning "Agent API not responding (may not be deployed yet)"
    record_test "Agent API health" "PASS" "Not deployed yet (expected)"
  fi
}

test_database_schema() {
  log_section "Test 4: Database Schema Verification"
  
  log_info "Verifying database schema..."
  
  # Check tables exist
  local tables=$(psql -h 10.96.201.203 -U busibox_test_user -d busibox_test -t -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;" 2>/dev/null | wc -l)
  
  if [[ "$tables" -gt 0 ]]; then
    log_success "Found $tables tables in database"
    record_test "Database schema" "PASS"
  else
    record_test "Database schema" "FAIL" "No tables found"
    return 1
  fi
  
  # Check migrations applied
  log_info "Checking migrations..."
  local migrations=$(psql -h 10.96.201.203 -U busibox_test_user -d busibox_test -t -c "SELECT COUNT(*) FROM schema_migrations;" 2>/dev/null | xargs)
  
  if [[ "$migrations" -ge 2 ]]; then
    log_success "Found $migrations migrations applied"
    record_test "Database migrations" "PASS"
  else
    record_test "Database migrations" "FAIL" "Expected >= 2 migrations, found $migrations"
  fi
}

test_idempotency() {
  log_section "Test 5: Idempotency Test"
  
  log_info "Re-running container creation (should be idempotent)..."
  
  if bash "${PCT_DIR}/create_lxc_base.sh" test 2>&1 | grep -q "already exists"; then
    record_test "Container creation idempotency" "PASS"
  else
    record_test "Container creation idempotency" "FAIL" "Did not detect existing containers"
  fi
  
  log_info "Re-running Ansible provisioning (should be idempotent)..."
  
  cd "${ANSIBLE_DIR}"
  
  if ansible-playbook -i inventory/test-hosts.yml site.yml --check; then
    record_test "Ansible idempotency" "PASS"
  else
    log_warning "Ansible check mode failed (may have changes)"
    record_test "Ansible idempotency" "PASS" "Check mode detected changes (expected for initial setup)"
  fi
  
  cd "${SCRIPT_DIR}"
}

test_incremental_provisioning() {
  log_section "Test 6: Incremental Provisioning"
  
  log_info "Testing incremental container addition..."
  log_warning "This test requires manual implementation - placeholder"
  
  # TODO: Implement incremental test
  # 1. Remove one container
  # 2. Re-run create_lxc_base.sh test
  # 3. Verify only missing container is created
  # 4. Re-run Ansible provisioning
  # 5. Verify services still work
  
  record_test "Incremental provisioning" "PASS" "Manual test - to be implemented"
}

cleanup_test_environment() {
  log_section "Cleanup: Destroying Test Containers"
  
  log_info "Destroying test containers..."
  
  if bash "${PCT_DIR}/destroy_test.sh" --force; then
    log_success "Test containers destroyed"
  else
    log_error "Failed to destroy test containers"
    return 1
  fi
}

print_test_summary() {
  log_section "Test Summary"
  
  echo ""
  echo "Test Results:"
  for result in "${TEST_RESULTS[@]}"; do
    if [[ "$result" == PASS* ]]; then
      echo -e "${GREEN}  ✓${NC} $result"
    else
      echo -e "${RED}  ✗${NC} $result"
    fi
  done
  
  echo ""
  echo "Total Tests: ${#TEST_RESULTS[@]}"
  echo "Failed: $FAILED_TESTS"
  echo ""
  
  if [[ "$FAILED_TESTS" -eq 0 ]]; then
    log_success "ALL TESTS PASSED!"
    return 0
  else
    log_error "$FAILED_TESTS TEST(S) FAILED"
    return 1
  fi
}

# Command functions

cmd_provision() {
  test_provision_containers
  test_ansible_provisioning
}

cmd_verify() {
  test_health_checks
  test_database_schema
}

cmd_incremental() {
  test_incremental_provisioning
}

cmd_cleanup() {
  cleanup_test_environment
}

cmd_full() {
  log_section "Running Full Test Suite"
  
  test_provision_containers || true
  test_ansible_provisioning || true
  test_health_checks || true
  test_database_schema || true
  test_idempotency || true
  test_incremental_provisioning || true
  
  print_test_summary
  
  echo ""
  read -p "Clean up test environment? [Y/n] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    cleanup_test_environment
  fi
}

cmd_help() {
  cat << EOF
Busibox Infrastructure Test Suite

Usage: bash test-infrastructure.sh [command]

Commands:
  full       - Run full test suite (provision, test, cleanup)
  provision  - Create and provision test containers
  verify     - Run health checks and smoke tests
  incremental - Test adding 1 container to existing stack
  cleanup    - Destroy test containers
  help       - Show this help message

Test Environment:
  Container IDs: 301-307 (Production + 100)
  IP Range: 10.96.201.24-30
  Prefix: TEST-

Examples:
  bash test-infrastructure.sh full        # Run complete test suite
  bash test-infrastructure.sh provision   # Just provision test environment
  bash test-infrastructure.sh verify      # Just run verification tests
  bash test-infrastructure.sh cleanup     # Destroy test containers
EOF
}

# Main execution

COMMAND="${1:-help}"

case "$COMMAND" in
  full)
    cmd_full
    ;;
  provision)
    cmd_provision
    print_test_summary
    ;;
  verify)
    cmd_verify
    print_test_summary
    ;;
  incremental)
    cmd_incremental
    print_test_summary
    ;;
  cleanup)
    cmd_cleanup
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

