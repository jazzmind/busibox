#!/bin/bash
set -euo pipefail

#==============================================================================
# Bootstrap Test Credentials for Busibox Integration Testing
#
# EXECUTION CONTEXT: Admin workstation
#
# DESCRIPTION:
#   Creates test user, OAuth client, and admin credentials in the authz service
#   for use in local and service integration testing. Stores credentials in
#   ansible vault for use by all services.
#
# USAGE:
#   bash scripts/bootstrap-test-credentials.sh [test|production]
#
# DEPENDENCIES:
#   - jq (for JSON parsing)
#   - curl (for HTTP requests)
#   - python3 (for YAML manipulation)
#   - ansible-vault (for encrypting credentials)
#   - Authz service must be running
#
# OUTPUTS:
#   - Test user created in authz
#   - Test OAuth client created
#   - Credentials saved to ansible vault
#   - .env variables printed to stdout
#
# EXAMPLE:
#   bash scripts/bootstrap-test-credentials.sh test
#   # Credentials are saved to inventory/test/group_vars/all/vault.yml
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
if ! curl -s -f "${AUTHZ_URL}/health/live" > /dev/null 2>&1; then
    echo -e "${RED}Error: Cannot connect to authz service at ${AUTHZ_URL}${NC}"
    echo -e "${YELLOW}Make sure the authz service is running:${NC}"
    echo "  cd provision/ansible"
    echo "  make authz INV=inventory/${ENV}"
    exit 1
fi
echo -e "${GREEN}✓ Authz service is running${NC}"
echo ""

# Generate test credentials
TEST_USER_ID="test-user-$(date +%s)"
TEST_USER_EMAIL="test@busibox.local"
TEST_CLIENT_ID="test-client-$(date +%s)"
TEST_CLIENT_SECRET=$(openssl rand -hex 32)
ADMIN_TOKEN=$(openssl rand -hex 32)

echo -e "${BLUE}Creating test user and OAuth client...${NC}"

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
    echo -e "${YELLOW}⚠ Could not create user via API (may need manual setup)${NC}"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Test Credentials Generated!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Save credentials to ansible vault
VAULT_FILE="provision/ansible/inventory/${ENV}/group_vars/all/vault.yml"

if [ ! -f "$VAULT_FILE" ]; then
    echo -e "${RED}Error: Vault file not found: ${VAULT_FILE}${NC}"
    echo -e "${YELLOW}Skipping vault update. Credentials will only be printed below.${NC}"
    echo ""
else
    echo -e "${BLUE}Merging credentials into vault: ${VAULT_FILE}...${NC}"
    
    # Check if vault is encrypted
    WAS_ENCRYPTED=false
    WORKING_VAULT=""
    if head -n1 "$VAULT_FILE" | grep -q "^\$ANSIBLE_VAULT"; then
        echo -e "${BLUE}Vault is encrypted, decrypting temporarily...${NC}"
        TEMP_VAULT=$(mktemp)
        trap "rm -f $TEMP_VAULT" EXIT
        
        # Try with vault password file first, then prompt
        VAULT_PASS_FILE="${HOME}/.vault_pass"
        if [ -f "$VAULT_PASS_FILE" ]; then
            if ansible-vault decrypt "$VAULT_FILE" --output="$TEMP_VAULT" --vault-password-file="$VAULT_PASS_FILE" 2>/dev/null; then
                WORKING_VAULT="$TEMP_VAULT"
                WAS_ENCRYPTED=true
            else
                echo -e "${RED}Failed to decrypt vault with ${VAULT_PASS_FILE}${NC}"
                echo -e "${YELLOW}Skipping vault update. Credentials will only be printed below.${NC}"
                echo ""
                rm -f "$TEMP_VAULT"
            fi
        else
            if ansible-vault decrypt "$VAULT_FILE" --output="$TEMP_VAULT" 2>/dev/null; then
                WORKING_VAULT="$TEMP_VAULT"
                WAS_ENCRYPTED=true
            else
                echo -e "${RED}Failed to decrypt vault${NC}"
                echo -e "${YELLOW}Skipping vault update. Credentials will only be printed below.${NC}"
                echo ""
                rm -f "$TEMP_VAULT"
            fi
        fi
    else
        echo -e "${YELLOW}Warning: Vault file is not encrypted!${NC}"
        WORKING_VAULT="$VAULT_FILE"
    fi
    
    if [ -n "$WORKING_VAULT" ]; then
        # Create Python script to merge credentials into vault
        TEMP_SCRIPT=$(mktemp)
        trap "rm -f $TEMP_VAULT $TEMP_SCRIPT" EXIT
        
        cat > "$TEMP_SCRIPT" <<'PYTHON_EOF'
import sys
import yaml
from pathlib import Path

def merge_test_credentials(vault_file, client_id, client_secret, user_id, user_email, admin_token):
    """Merge test credentials into existing vault YAML"""
    
    # Load existing vault
    with open(vault_file, 'r') as f:
        vault_data = yaml.safe_load(f) or {}
    
    # Ensure secrets key exists
    if 'secrets' not in vault_data:
        vault_data['secrets'] = {}
    
    # Ensure test_credentials key exists under secrets
    if 'test_credentials' not in vault_data['secrets']:
        vault_data['secrets']['test_credentials'] = {}
    
    # Update test credentials under secrets
    vault_data['secrets']['test_credentials'] = {
        'authz_test_client_id': client_id,
        'authz_test_client_secret': client_secret,
        'test_user_id': user_id,
        'test_user_email': user_email,
        'authz_admin_token': admin_token
    }
    
    # Write back to file
    with open(vault_file, 'w') as f:
        f.write('---\n')
        f.write('# Ansible Vault - Encrypted Deployment Configuration\n')
        f.write('#\n')
        f.write('# Test credentials updated by bootstrap-test-credentials.sh\n')
        f.write('\n')
        yaml.dump(vault_data, f, default_flow_style=False, sort_keys=False, width=120)
    
    return True

if __name__ == '__main__':
    vault_file = sys.argv[1]
    client_id = sys.argv[2]
    client_secret = sys.argv[3]
    user_id = sys.argv[4]
    user_email = sys.argv[5]
    admin_token = sys.argv[6]
    
    success = merge_test_credentials(vault_file, client_id, client_secret, user_id, user_email, admin_token)
    sys.exit(0 if success else 1)
PYTHON_EOF
        
        # Run Python script to merge credentials
        if python3 "$TEMP_SCRIPT" "$WORKING_VAULT" "$TEST_CLIENT_ID" "$TEST_CLIENT_SECRET" "$TEST_USER_ID" "$TEST_USER_EMAIL" "$ADMIN_TOKEN"; then
            echo -e "${GREEN}✓ Credentials merged into vault${NC}"
            
            # Re-encrypt if it was encrypted
            if [ "$WAS_ENCRYPTED" = true ]; then
                echo -e "${BLUE}Re-encrypting vault...${NC}"
                if [ -f "$VAULT_PASS_FILE" ]; then
                    if ansible-vault encrypt "$WORKING_VAULT" --output="$VAULT_FILE" --vault-password-file="$VAULT_PASS_FILE" 2>/dev/null; then
                        echo -e "${GREEN}✓ Vault re-encrypted${NC}"
                    else
                        echo -e "${RED}Failed to re-encrypt vault${NC}"
                    fi
                else
                    if ansible-vault encrypt "$WORKING_VAULT" --output="$VAULT_FILE" 2>/dev/null; then
                        echo -e "${GREEN}✓ Vault re-encrypted${NC}"
                    else
                        echo -e "${RED}Failed to re-encrypt vault${NC}"
                    fi
                fi
            else
                cp "$WORKING_VAULT" "$VAULT_FILE"
            fi
            
            echo ""
            echo -e "${GREEN}✓ Test credentials saved to vault!${NC}"
            echo ""
            echo -e "${YELLOW}Credentials are now available to all ansible playbooks and services:${NC}"
            echo -e "${YELLOW}  - Agent API tests: {{ secrets.test_credentials.authz_test_client_id }}${NC}"
            echo -e "${YELLOW}  - Search API tests: {{ secrets.test_credentials.authz_test_client_id }}${NC}"
            echo -e "${YELLOW}  - Ingest API tests: {{ secrets.test_credentials.authz_test_client_id }}${NC}"
            echo -e "${YELLOW}  - AI Portal tests: {{ secrets.test_credentials.authz_test_client_id }}${NC}"
            echo -e "${YELLOW}  - Agent Client tests: {{ secrets.test_credentials.authz_test_client_id }}${NC}"
            echo ""
        else
            echo -e "${RED}Failed to merge credentials${NC}"
        fi
        
        rm -f "$TEMP_SCRIPT" "$TEMP_VAULT"
    fi
fi

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
echo -e "${BLUE}To use in service tests, services can access via environment variables:${NC}"
echo -e "  AUTHZ_TEST_CLIENT_ID (from vault: {{ secrets.test_credentials.authz_test_client_id }})"
echo -e "  AUTHZ_TEST_CLIENT_SECRET (from vault: {{ secrets.test_credentials.authz_test_client_secret }})"
echo ""
echo -e "${GREEN}Done!${NC}"
