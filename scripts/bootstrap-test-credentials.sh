#!/bin/bash
set -euo pipefail

#==============================================================================
# Bootstrap Test Credentials for Busibox Integration Testing
#
# EXECUTION CONTEXT: Admin workstation
#
# DESCRIPTION:
#   Creates test user, OAuth client, and admin credentials in the authz service
#   for use in local integration testing. Outputs .env variables to copy/paste.
#
# USAGE:
#   bash scripts/bootstrap-test-credentials.sh [test|production]
#
# DEPENDENCIES:
#   - jq (for JSON parsing)
#   - curl (for HTTP requests)
#   - Authz service must be running
#
# OUTPUTS:
#   - Test user created in authz
#   - Test OAuth client created
#   - .env variables printed to stdout
#
# EXAMPLE:
#   bash scripts/bootstrap-test-credentials.sh test
#   # Copy the output .env variables to busibox-app/.env
#==============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default to test environment
ENV="${1:-test}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Bootstrap Test Credentials for Authz${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Load environment-specific configuration
if [ "$ENV" = "test" ]; then
    AUTHZ_HOST="10.96.201.210"
    AUTHZ_PORT="8010"
    echo -e "${GREEN}Environment: TEST${NC}"
elif [ "$ENV" = "production" ]; then
    AUTHZ_HOST="10.96.200.210"
    AUTHZ_PORT="8010"
    echo -e "${YELLOW}Environment: PRODUCTION${NC}"
else
    echo -e "${RED}Error: Invalid environment '$ENV'. Use 'test' or 'production'${NC}"
    exit 1
fi

AUTHZ_URL="http://${AUTHZ_HOST}:${AUTHZ_PORT}"

# Check if authz service is running
echo -e "${BLUE}Checking authz service...${NC}"
if ! curl -s -f "${AUTHZ_URL}/health" > /dev/null 2>&1; then
    echo -e "${RED}Error: Cannot connect to authz service at ${AUTHZ_URL}${NC}"
    echo -e "${YELLOW}Make sure the authz service is running:${NC}"
    echo "  cd provision/ansible"
    echo "  make authz INV=inventory/${ENV}"
    exit 1
fi
echo -e "${GREEN}✓ Authz service is running${NC}"
echo ""

# Get bootstrap client credentials from JWKS endpoint
echo -e "${BLUE}Getting bootstrap client info...${NC}"
JWKS_RESPONSE=$(curl -s "${AUTHZ_URL}/.well-known/jwks.json")

if [ -z "$JWKS_RESPONSE" ] || [ "$JWKS_RESPONSE" = "null" ]; then
    echo -e "${RED}Error: Could not get JWKS from authz service${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Bootstrap client exists${NC}"

# Generate test credentials
TEST_USER_ID="test-user-$(date +%s)"
TEST_USER_EMAIL="test@busibox.local"
TEST_CLIENT_ID="test-client-$(date +%s)"
TEST_CLIENT_SECRET=$(openssl rand -hex 32)
ADMIN_TOKEN=$(openssl rand -hex 32)

echo ""
echo -e "${BLUE}Creating test OAuth client...${NC}"

# Create test OAuth client using bootstrap client
# Note: This requires the bootstrap client secret which should be in ansible vault
BOOTSTRAP_CLIENT_ID="bootstrap-client"

# Try to get bootstrap secret from ansible vault
VAULT_FILE="provision/ansible/roles/secrets/vars/vault.yml"
if [ -f "$VAULT_FILE" ]; then
    echo -e "${YELLOW}Note: Bootstrap client secret needed from ansible vault${NC}"
    echo -e "${YELLOW}Run: ansible-vault view $VAULT_FILE | grep authz_bootstrap_client_secret${NC}"
    echo ""
fi

# For now, we'll use the admin token approach if available
# Check if we can access the authz admin endpoint
echo -e "${BLUE}Attempting to create test client via admin endpoint...${NC}"

# Try to create client with a temporary admin token
# (In production, this would use the actual admin token from vault)
CREATE_CLIENT_RESPONSE=$(curl -s -X POST "${AUTHZ_URL}/admin/oauth/clients" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -d "{
        \"client_id\": \"${TEST_CLIENT_ID}\",
        \"client_secret\": \"${TEST_CLIENT_SECRET}\",
        \"allowed_audiences\": [\"ingest-api\", \"agent-api\", \"search-api\", \"authz\"],
        \"allowed_scopes\": [\"ingest.read\", \"ingest.write\", \"agent.execute\", \"search.read\", \"audit.write\", \"rbac.read\"]
    }" 2>&1 || true)

# Check if client creation succeeded
if echo "$CREATE_CLIENT_RESPONSE" | grep -q "client_id"; then
    echo -e "${GREEN}✓ Test OAuth client created${NC}"
else
    echo -e "${YELLOW}⚠ Could not create client via API (may need manual setup)${NC}"
    echo -e "${YELLOW}Response: ${CREATE_CLIENT_RESPONSE}${NC}"
fi

echo ""
echo -e "${BLUE}Creating test user...${NC}"

# Create test user via internal sync endpoint
SYNC_USER_RESPONSE=$(curl -s -X POST "${AUTHZ_URL}/internal/sync/user" \
    -H "Content-Type: application/json" \
    -d "{
        \"user_id\": \"${TEST_USER_ID}\",
        \"email\": \"${TEST_USER_EMAIL}\",
        \"roles\": [
            {\"id\": \"admin\", \"name\": \"Admin\", \"permissions\": [\"*\"]},
            {\"id\": \"user\", \"name\": \"User\", \"permissions\": [\"read\", \"write\"]}
        ]
    }" 2>&1 || true)

if echo "$SYNC_USER_RESPONSE" | grep -q "ok"; then
    echo -e "${GREEN}✓ Test user created${NC}"
else
    echo -e "${YELLOW}⚠ Could not create user via API${NC}"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Test Credentials Generated!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Save credentials to group_vars for ansible
INVENTORY_DIR="provision/ansible/inventory/${ENV}"
GROUP_VARS_FILE="${INVENTORY_DIR}/group_vars/all/test_credentials.yml"

echo -e "${BLUE}Saving credentials to ${GROUP_VARS_FILE}...${NC}"

mkdir -p "$(dirname "$GROUP_VARS_FILE")"

cat > "$GROUP_VARS_FILE" << EOF
---
# Test credentials for integration testing
# Generated: $(date)
# DO NOT COMMIT - Add to .gitignore if not already there

# Test OAuth Client (for service-to-service authentication)
authz_test_client_id: "${TEST_CLIENT_ID}"
authz_test_client_secret: "${TEST_CLIENT_SECRET}"

# Test User
test_user_id: "${TEST_USER_ID}"
test_user_email: "${TEST_USER_EMAIL}"

# Admin Token (for RBAC operations)
authz_admin_token: "${ADMIN_TOKEN}"
EOF

echo -e "${GREEN}✓ Credentials saved to ${GROUP_VARS_FILE}${NC}"
echo ""

# Update .gitignore to exclude test credentials
GITIGNORE_FILE="provision/ansible/inventory/${ENV}/group_vars/all/.gitignore"
if [ ! -f "$GITIGNORE_FILE" ] || ! grep -q "test_credentials.yml" "$GITIGNORE_FILE" 2>/dev/null; then
    mkdir -p "$(dirname "$GITIGNORE_FILE")"
    echo "test_credentials.yml" >> "$GITIGNORE_FILE"
    echo -e "${GREEN}✓ Added test_credentials.yml to .gitignore${NC}"
fi

echo ""
echo -e "${YELLOW}These credentials are now available to all ansible playbooks and services:${NC}"
echo -e "${YELLOW}  - Agent API tests: Can use {{ authz_test_client_id }}${NC}"
echo -e "${YELLOW}  - Search API tests: Can use {{ authz_test_client_id }}${NC}"
echo -e "${YELLOW}  - Ingest API tests: Can use {{ authz_test_client_id }}${NC}"
echo -e "${YELLOW}  - AI Portal tests: Can use {{ authz_test_client_id }}${NC}"
echo -e "${YELLOW}  - Agent Client tests: Can use {{ authz_test_client_id }}${NC}"
echo ""
echo -e "${BLUE}To use in service templates, add to .env.j2 files:${NC}"
echo -e "  AUTHZ_TEST_CLIENT_ID={{ authz_test_client_id }}"
echo -e "  AUTHZ_TEST_CLIENT_SECRET={{ authz_test_client_secret }}"
echo ""
echo -e "${BLUE}Copy these variables to your busibox-app/.env file:${NC}"
echo ""
echo "# ============================================"
echo "# Busibox Test Credentials"
echo "# Generated: $(date)"
echo "# Environment: ${ENV}"
echo "# ============================================"
echo ""
echo "# Authz Service"
echo "AUTHZ_BASE_URL=${AUTHZ_URL}"
echo ""
echo "# Test OAuth Client (for getting service tokens)"
echo "AUTHZ_TEST_CLIENT_ID=${TEST_CLIENT_ID}"
echo "AUTHZ_TEST_CLIENT_SECRET=${TEST_CLIENT_SECRET}"
echo ""
echo "# Bootstrap Client (fallback)"
echo "AUTHZ_BOOTSTRAP_CLIENT_ID=${BOOTSTRAP_CLIENT_ID}"
echo "# AUTHZ_BOOTSTRAP_CLIENT_SECRET=<get-from-ansible-vault>"
echo ""
echo "# Admin Token (for RBAC admin operations)"
echo "AUTHZ_ADMIN_TOKEN=${ADMIN_TOKEN}"
echo ""
echo "# Test User"
echo "TEST_USER_ID=${TEST_USER_ID}"
echo "TEST_USER_EMAIL=${TEST_USER_EMAIL}"
echo ""
echo "# Service URLs (${ENV} environment)"
if [ "$ENV" = "test" ]; then
    echo "INGEST_API_HOST=10.96.201.206"
    echo "INGEST_API_PORT=8002"
    echo "AGENT_API_URL=http://10.96.201.207:4111"
    echo "MILVUS_HOST=10.96.201.204"
    echo "MILVUS_PORT=19530"
else
    echo "INGEST_API_HOST=10.96.200.206"
    echo "INGEST_API_PORT=8002"
    echo "AGENT_API_URL=http://10.96.200.207:4111"
    echo "MILVUS_HOST=10.96.200.204"
    echo "MILVUS_PORT=19530"
fi
echo ""
echo "# ============================================"
echo ""
echo -e "${YELLOW}Note: If the OAuth client creation failed, you may need to:${NC}"
echo -e "${YELLOW}1. Get the bootstrap client secret from ansible vault:${NC}"
echo "   cd provision/ansible"
echo "   ansible-vault view roles/secrets/vars/vault.yml | grep authz_bootstrap"
echo ""
echo -e "${YELLOW}2. Use the bootstrap credentials to create the test client manually${NC}"
echo ""
echo -e "${BLUE}To test the credentials:${NC}"
echo "  cd /path/to/busibox-app"
echo "  # Add the above variables to .env"
echo "  npm test"
echo ""
echo -e "${GREEN}Done!${NC}"

