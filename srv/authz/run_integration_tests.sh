#!/bin/bash
set -euo pipefail

#==============================================================================
# Run Authz Integration Tests Against Test Environment
#
# EXECUTION CONTEXT: Local workstation
#
# DESCRIPTION:
#   Runs comprehensive integration tests against the test PostgreSQL database
#   and authz service. Tests database schema, RBAC operations, OAuth clients,
#   signing keys, and audit logging.
#
# USAGE:
#   bash run_integration_tests.sh
#
# DEPENDENCIES:
#   - pytest
#   - asyncpg
#   - httpx
#   - Access to test PostgreSQL database (10.96.201.203)
#   - Test authz service running (10.96.201.210:8010)
#
# ENVIRONMENT VARIABLES:
#   TEST_DB_PASSWORD - PostgreSQL password (required)
#   Or set all these:
#   TEST_DB_HOST - Default: 10.96.201.203
#   TEST_DB_PORT - Default: 5432
#   TEST_DB_NAME - Default: busibox
#   TEST_DB_USER - Default: busibox_user
#   TEST_AUTHZ_URL - Default: http://10.96.201.210:8010
#==============================================================================

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Authz Integration Tests${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if we're in the authz directory
if [ ! -f "requirements.txt" ] || [ ! -d "tests" ]; then
    echo -e "${RED}Error: Must be run from srv/authz directory${NC}"
    echo "cd /path/to/busibox/srv/authz"
    exit 1
fi

# Check for required environment variables
if [ -z "${TEST_DB_PASSWORD:-}" ]; then
    echo -e "${YELLOW}TEST_DB_PASSWORD not set.${NC}"
    echo ""
    echo "Get the password from ansible vault:"
    echo "  cd ../../provision/ansible"
    echo "  ansible-vault view inventory/test/group_vars/all/vault.yml | grep postgres_password"
    echo ""
    echo "Then export it:"
    echo "  export TEST_DB_PASSWORD='your-password'"
    echo ""
    exit 1
fi

# Set default values
export TEST_DB_HOST="${TEST_DB_HOST:-10.96.201.203}"
export TEST_DB_PORT="${TEST_DB_PORT:-5432}"
export TEST_DB_NAME="${TEST_DB_NAME:-busibox}"
export TEST_DB_USER="${TEST_DB_USER:-busibox_user}"
export TEST_AUTHZ_URL="${TEST_AUTHZ_URL:-http://10.96.201.210:8010}"

echo -e "${GREEN}Test Configuration:${NC}"
echo "  Database: ${TEST_DB_HOST}:${TEST_DB_PORT}/${TEST_DB_NAME}"
echo "  User: ${TEST_DB_USER}"
echo "  Authz URL: ${TEST_AUTHZ_URL}"
echo ""

# Check if venv exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Virtual environment not found. Creating...${NC}"
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies
echo -e "${BLUE}Installing dependencies...${NC}"
pip install -q -r requirements.txt
pip install -q -r requirements.test.txt

# Run tests
echo ""
echo -e "${BLUE}Running integration tests...${NC}"
echo ""

pytest tests/test_real_db.py -v --tb=short --color=yes

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}All tests passed!${NC}"
    echo -e "${GREEN}========================================${NC}"
else
    echo ""
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}Some tests failed${NC}"
    echo -e "${RED}========================================${NC}"
fi

exit $EXIT_CODE





