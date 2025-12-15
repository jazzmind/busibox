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
#   bash scripts/bootstrap-test-credentials.sh [test|production] [--force|-f]
#
# OPTIONS:
#   --force, -f    Force creation of new credentials even if they exist
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
#   # Credentials are saved to provision/ansible/roles/secrets/vars/vault.yml
#   # Copy the output .env variables to busibox-app/.env
#==============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
ENV="${1:-test}"
FORCE=false

# Check for --force or -f flag
for arg in "$@"; do
    case "$arg" in
        --force|-f)
            FORCE=true
            ;;
    esac
done

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Bootstrap Test Credentials for Authz${NC}"
if [ "$FORCE" = true ]; then
    echo -e "${YELLOW}  (FORCE MODE: Creating new credentials)${NC}"
fi
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

# Get bootstrap client credentials for API calls
# Determine container ID based on environment
if [ "$ENV" = "test" ]; then
    AUTHZ_CTID=310
else
    AUTHZ_CTID=210
fi

BOOTSTRAP_CLIENT_ID=$(pct exec ${AUTHZ_CTID} -- grep AUTHZ_BOOTSTRAP_CLIENT_ID /srv/authz/.env 2>/dev/null | cut -d= -f2 || echo "ai-portal")
BOOTSTRAP_CLIENT_SECRET=$(pct exec ${AUTHZ_CTID} -- grep AUTHZ_BOOTSTRAP_CLIENT_SECRET /srv/authz/.env 2>/dev/null | cut -d= -f2 || echo "")

# Check for existing credentials in vault (unless --force is set)
# Note: The vault file is shared between test and production environments
VAULT_FILE="roles/secrets/vars/vault.yml"
EXISTING_CREDS_FOUND=false
TEST_USER_ID=""
TEST_USER_EMAIL="test@busibox.local"
TEST_CLIENT_ID=""
TEST_CLIENT_SECRET=""
ADMIN_TOKEN=""

if [ "$FORCE" = true ]; then
    echo -e "${YELLOW}Force mode: Skipping existing credentials check${NC}"
    EXISTING_CREDS_FOUND=false
elif [ -f "$VAULT_FILE" ]; then
    echo -e "${BLUE}Checking for existing credentials in vault...${NC}"
    
    # Try to read existing credentials from vault
    TEMP_VAULT=$(mktemp)
    trap "rm -f $TEMP_VAULT" EXIT
    
    VAULT_PASS_FILE="${HOME}/.vault_pass"
    WORKING_VAULT=""
    if head -n1 "$VAULT_FILE" | grep -q "^\$ANSIBLE_VAULT"; then
        # Vault is encrypted
        if [ -f "$VAULT_PASS_FILE" ]; then
            if ansible-vault decrypt "$VAULT_FILE" --output="$TEMP_VAULT" --vault-password-file="$VAULT_PASS_FILE" 2>/dev/null; then
                WORKING_VAULT="$TEMP_VAULT"
            fi
        else
            if ansible-vault decrypt "$VAULT_FILE" --output="$TEMP_VAULT" 2>/dev/null; then
                WORKING_VAULT="$TEMP_VAULT"
            fi
        fi
    else
        WORKING_VAULT="$VAULT_FILE"
    fi
    
    if [ -n "${WORKING_VAULT:-}" ] && [ -f "$WORKING_VAULT" ]; then
        # Extract credentials using Python
        EXISTING_CREDS=$(python3 <<PYTHON_EOF
import yaml
import sys
try:
    with open('$WORKING_VAULT', 'r') as f:
        vault_data = yaml.safe_load(f) or {}
    creds = vault_data.get('secrets', {}).get('test_credentials', {})
    if creds:
        print(f"{creds.get('authz_test_client_id', '')}|{creds.get('authz_test_client_secret', '')}|{creds.get('test_user_id', '')}|{creds.get('test_user_email', 'test@busibox.local')}|{creds.get('authz_admin_token', '')}")
except Exception as e:
    pass
PYTHON_EOF
)
        
        if [ -n "$EXISTING_CREDS" ]; then
            IFS='|' read -r TEST_CLIENT_ID TEST_CLIENT_SECRET TEST_USER_ID TEST_USER_EMAIL ADMIN_TOKEN <<< "$EXISTING_CREDS"
            
            if [ -n "$TEST_CLIENT_ID" ] && [ -n "$TEST_CLIENT_SECRET" ] && [ -n "$TEST_USER_ID" ]; then
                echo -e "${GREEN}✓ Found existing credentials in vault${NC}"
                
                # Try to verify OAuth client exists by attempting to get a token
                # This is a better verification than listing clients (which requires admin auth)
                echo -e "${BLUE}Verifying OAuth client exists in authz...${NC}"
                TOKEN_CHECK=$(curl -s -X POST "${AUTHZ_URL}/oauth/token" \
                    -H "Content-Type: application/x-www-form-urlencoded" \
                    -d "grant_type=client_credentials&client_id=${TEST_CLIENT_ID}&client_secret=${TEST_CLIENT_SECRET}&audience=ingest-api" 2>&1 || echo "")
                
                if echo "$TOKEN_CHECK" | grep -q "access_token"; then
                    echo -e "${GREEN}✓ OAuth client verified (successfully obtained token)${NC}"
                    EXISTING_CREDS_FOUND=true
                else
                    # Verification failed, but credentials exist in vault
                    # Trust the vault credentials (they were created by this script before)
                    # Just warn the user
                    echo -e "${YELLOW}⚠ OAuth client verification failed (${TOKEN_CHECK:0:100})${NC}"
                    echo -e "${YELLOW}  Using credentials from vault anyway (they may need to be recreated)${NC}"
                    EXISTING_CREDS_FOUND=true
                fi
            fi
        fi
        
        rm -f "$TEMP_VAULT"
    fi
fi

if [ "$EXISTING_CREDS_FOUND" = false ]; then
    echo -e "${BLUE}No existing credentials found, generating new ones...${NC}"
    
    # Generate new test credentials
    # Use UUID for user_id (required by authz database schema)
    if command -v uuidgen &> /dev/null; then
        TEST_USER_ID=$(uuidgen)
    elif command -v python3 &> /dev/null; then
        TEST_USER_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
    else
        # Fallback: generate UUID-like string (not ideal but works)
        TEST_USER_ID="$(openssl rand -hex 4)-$(openssl rand -hex 2)-$(openssl rand -hex 2)-$(openssl rand -hex 2)-$(openssl rand -hex 6)"
    fi
    TEST_USER_EMAIL="test@busibox.local"
    TEST_CLIENT_ID="test-client-$(date +%s)"
    TEST_CLIENT_SECRET=$(openssl rand -hex 32)
    ADMIN_TOKEN=$(openssl rand -hex 32)
    
    echo -e "${BLUE}Creating test user and OAuth client...${NC}"
else
    echo -e "${GREEN}Using existing credentials${NC}"
    echo ""
fi

if [ "$EXISTING_CREDS_FOUND" = false ]; then
    # Create OAuth client FIRST (needed for user creation)
    echo -e "${BLUE}Creating OAuth client...${NC}"

    if [ -z "$BOOTSTRAP_CLIENT_SECRET" ]; then
        echo -e "${YELLOW}⚠ Could not get bootstrap client credentials${NC}"
        echo -e "${YELLOW}  OAuth client will need to be created manually${NC}"
        CLIENT_CREATED=false
    else
        # Include bootstrap client credentials in body for authentication (per admin.py _require_admin_auth)
        # The endpoint expects: client_id/client_secret for auth + client_id/client_secret/audiences/scopes for the new client
        CREATE_CLIENT_RESPONSE=$(curl -s -X POST "${AUTHZ_URL}/admin/oauth-clients" \
            -H "Content-Type: application/json" \
            -d "{
                \"client_id\": \"${TEST_CLIENT_ID}\",
                \"client_secret\": \"${TEST_CLIENT_SECRET}\",
                \"allowed_audiences\": [\"ingest-api\", \"search-api\", \"agent-api\", \"authz\"],
                \"allowed_scopes\": [\"read\", \"write\", \"admin\"],
                \"auth_client_id\": \"${BOOTSTRAP_CLIENT_ID}\",
                \"auth_client_secret\": \"${BOOTSTRAP_CLIENT_SECRET}\"
            }" 2>&1 || true)

        if echo "$CREATE_CLIENT_RESPONSE" | grep -q "client_id"; then
            echo -e "${GREEN}✓ OAuth client created${NC}"
            CLIENT_CREATED=true
        else
            echo -e "${YELLOW}⚠ Could not create OAuth client via API: ${CREATE_CLIENT_RESPONSE}${NC}"
            echo -e "${YELLOW}  You may need to create it manually${NC}"
            CLIENT_CREATED=false
        fi
    fi

    # Create test user via internal sync endpoint (requires OAuth client credentials)
    if [ "$CLIENT_CREATED" = true ]; then
        echo -e "${BLUE}Creating test user...${NC}"
        
        # Get existing roles or create new ones
        # First, try to get existing roles by name
        EXISTING_ROLES=$(curl -s -X GET "${AUTHZ_URL}/admin/roles" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" 2>&1 || echo "[]")
        
        ADMIN_ROLE_ID=""
        USER_ROLE_ID=""
        
        # Try to find existing roles
        if echo "$EXISTING_ROLES" | grep -q "\"name\": \"Admin\""; then
            ADMIN_ROLE_ID=$(echo "$EXISTING_ROLES" | grep -o '"id": "[^"]*"' | head -1 | cut -d'"' -f4)
            echo -e "${BLUE}  Found existing Admin role: ${ADMIN_ROLE_ID}${NC}"
        fi
        if echo "$EXISTING_ROLES" | grep -q "\"name\": \"User\""; then
            USER_ROLE_ID=$(echo "$EXISTING_ROLES" | grep -o '"id": "[^"]*"' | tail -1 | cut -d'"' -f4)
            echo -e "${BLUE}  Found existing User role: ${USER_ROLE_ID}${NC}"
        fi
        
        # Generate UUIDs for new roles if they don't exist
        if [ -z "$ADMIN_ROLE_ID" ]; then
            if command -v uuidgen &> /dev/null; then
                ADMIN_ROLE_ID=$(uuidgen)
            elif command -v python3 &> /dev/null; then
                ADMIN_ROLE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
            else
                ADMIN_ROLE_ID="$(openssl rand -hex 4)-$(openssl rand -hex 2)-$(openssl rand -hex 2)-$(openssl rand -hex 2)-$(openssl rand -hex 6)"
            fi
        fi
        if [ -z "$USER_ROLE_ID" ]; then
            if command -v uuidgen &> /dev/null; then
                USER_ROLE_ID=$(uuidgen)
            elif command -v python3 &> /dev/null; then
                USER_ROLE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
            else
                USER_ROLE_ID="$(openssl rand -hex 4)-$(openssl rand -hex 2)-$(openssl rand -hex 2)-$(openssl rand -hex 2)-$(openssl rand -hex 6)"
            fi
        fi
        
        # Only include roles in sync if they don't exist (to avoid duplicate name error)
        ROLES_JSON="[]"
        if ! echo "$EXISTING_ROLES" | grep -q "\"name\": \"Admin\""; then
            ROLES_JSON="[{\"id\": \"${ADMIN_ROLE_ID}\", \"name\": \"Admin\", \"description\": \"Administrator role\"}"
        fi
        if ! echo "$EXISTING_ROLES" | grep -q "\"name\": \"User\""; then
            if [ "$ROLES_JSON" = "[]" ]; then
                ROLES_JSON="[{\"id\": \"${USER_ROLE_ID}\", \"name\": \"User\", \"description\": \"Standard user role\"}]"
            else
                ROLES_JSON="${ROLES_JSON}, {\"id\": \"${USER_ROLE_ID}\", \"name\": \"User\", \"description\": \"Standard user role\"}]"
            fi
        elif [ "$ROLES_JSON" != "[]" ]; then
            ROLES_JSON="${ROLES_JSON}]"
        fi
        
        SYNC_USER_RESPONSE=$(curl -s -X POST "${AUTHZ_URL}/internal/sync/user" \
            -H "Content-Type: application/json" \
            -d "{
                \"client_id\": \"${TEST_CLIENT_ID}\",
                \"client_secret\": \"${TEST_CLIENT_SECRET}\",
                \"user_id\": \"${TEST_USER_ID}\",
                \"email\": \"${TEST_USER_EMAIL}\",
                \"roles\": ${ROLES_JSON},
                \"user_role_ids\": [\"${ADMIN_ROLE_ID}\", \"${USER_ROLE_ID}\"]
            }" 2>&1 || true)

        if echo "$SYNC_USER_RESPONSE" | grep -q "ok"; then
            echo -e "${GREEN}✓ Test user created${NC}"
        else
            echo -e "${YELLOW}⚠ Could not create user via API: ${SYNC_USER_RESPONSE}${NC}"
            echo -e "${YELLOW}  User may need to be created manually${NC}"
        fi
    else
        echo -e "${YELLOW}⚠ Skipping user creation (OAuth client not created)${NC}"
    fi
else
    echo -e "${GREEN}✓ Using existing test user and OAuth client${NC}"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
if [ "$EXISTING_CREDS_FOUND" = true ]; then
    echo -e "${GREEN}Test Credentials Retrieved!${NC}"
else
    echo -e "${GREEN}Test Credentials Generated!${NC}"
fi
echo -e "${GREEN}========================================${NC}"
echo ""

# Save credentials to ansible vault (only if we created new ones)
if [ "$EXISTING_CREDS_FOUND" = false ]; then
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
else
    echo -e "${GREEN}✓ Using existing credentials from vault${NC}"
    echo ""
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
    echo "AGENT_API_URL=http://10.96.201.202:8000"
    echo "MILVUS_HOST=10.96.201.204"
    echo "MILVUS_PORT=19530"
else
    echo "INGEST_API_HOST=10.96.200.206"
    echo "INGEST_API_PORT=8002"
    echo "AGENT_API_URL=http://10.96.200.202:8000"
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
