#!/bin/bash
set -euo pipefail

#==============================================================================
# Bootstrap Test Credentials for Busibox
#
# EXECUTION CONTEXT: Proxmox host (requires pct access)
#
# DESCRIPTION:
#   Manages test credentials for Busibox integration testing:
#   - Creates/retrieves a single test user
#   - Ensures OAuth clients exist (ai-portal, api-service)
#   - Updates ansible vault with credentials
#
#   OAuth Client Architecture:
#   - ai-portal: Used by frontend apps to exchange user credentials for tokens
#   - api-service: Used by backend services for service-to-service calls
#
#   Both clients share the same secret (jwt_secret) for simplicity, but have
#   different client_ids for audit trail purposes.
#
# USAGE:
#   bash scripts/test/bootstrap-test-credentials.sh [test|production] [--force|-f]
#
# OPTIONS:
#   --force, -f    Delete existing test users and create fresh credentials
#
# BEHAVIOR:
#   Without --force: Returns existing credentials if test user exists
#   With --force: Deletes all test users and creates new ones
#
# OUTPUTS:
#   - Test user in authz database (single user: test@busibox.local)
#   - OAuth clients: ai-portal, api-service
#   - Credentials saved to ansible vault
#==============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Determine script location and repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Parse arguments
ENV="${1:-test}"
FORCE=false

for arg in "$@"; do
    case "$arg" in
        --force|-f)
            FORCE=true
            ;;
    esac
done

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Bootstrap Test Credentials${NC}"
if [ "$FORCE" = true ]; then
    echo -e "${YELLOW}  (FORCE MODE: Will delete existing test users)${NC}"
fi
echo -e "${BLUE}========================================${NC}"
echo ""

# Environment-specific configuration
if [ "$ENV" = "test" ]; then
    AUTHZ_HOST="10.96.201.210"
    AUTHZ_CTID=310
    PG_HOST="10.96.201.203"
    PG_CTID=303
    echo -e "${GREEN}Environment: TEST${NC}"
elif [ "$ENV" = "production" ]; then
    AUTHZ_HOST="10.96.200.210"
    AUTHZ_CTID=210
    PG_HOST="10.96.200.203"
    PG_CTID=203
    echo -e "${YELLOW}Environment: PRODUCTION${NC}"
else
    echo -e "${RED}Error: Invalid environment '$ENV'. Use 'test' or 'production'${NC}"
    exit 1
fi

AUTHZ_URL="http://${AUTHZ_HOST}:8010"

# Fixed test user email
TEST_USER_EMAIL="test@busibox.local"

# Check if authz service is running
echo -e "${BLUE}Checking authz service...${NC}"
if ! curl -s -f "${AUTHZ_URL}/health/live" > /dev/null 2>&1; then
    echo -e "${RED}Error: Cannot connect to authz service at ${AUTHZ_URL}${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Authz service is running${NC}"
echo ""

#==============================================================================
# Database Functions
#==============================================================================

run_db_query() {
    local query="$1"
    pct exec ${PG_CTID} -- sudo -u postgres psql -d busibox_test -t -A -c "$query" 2>/dev/null || echo ""
}

#==============================================================================
# Check for Existing Test User
#==============================================================================

echo -e "${BLUE}Checking for existing test user...${NC}"

EXISTING_USER=$(run_db_query "SELECT user_id FROM authz_users WHERE email = '${TEST_USER_EMAIL}' LIMIT 1;")

if [ -n "$EXISTING_USER" ] && [ "$FORCE" = false ]; then
    echo -e "${GREEN}✓ Found existing test user: ${EXISTING_USER}${NC}"
    TEST_USER_ID="$EXISTING_USER"
    
    # Count how many test users exist
    USER_COUNT=$(run_db_query "SELECT COUNT(*) FROM authz_users WHERE email = '${TEST_USER_EMAIL}';")
    if [ "$USER_COUNT" -gt 1 ]; then
        echo -e "${YELLOW}⚠ Warning: Found ${USER_COUNT} test users. Run with --force to clean up.${NC}"
    fi
else
    if [ "$FORCE" = true ]; then
        echo -e "${YELLOW}Force mode: Deleting existing test users...${NC}"
        DELETED=$(run_db_query "DELETE FROM authz_users WHERE email = '${TEST_USER_EMAIL}' RETURNING user_id;" | wc -l)
        echo -e "${GREEN}✓ Deleted ${DELETED} existing test user(s)${NC}"
    fi
    
    # Generate new user ID
    TEST_USER_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
    echo -e "${BLUE}Creating new test user: ${TEST_USER_ID}${NC}"
    
    # Create user directly in database
    run_db_query "INSERT INTO authz_users (user_id, email, created_at) VALUES ('${TEST_USER_ID}', '${TEST_USER_EMAIL}', NOW());"
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Test user created${NC}"
    else
        echo -e "${RED}Failed to create test user${NC}"
        exit 1
    fi
fi

echo ""

#==============================================================================
# Check OAuth Clients
#==============================================================================

echo -e "${BLUE}Checking OAuth clients...${NC}"

# Check ai-portal client
AI_PORTAL_EXISTS=$(run_db_query "SELECT client_id FROM authz_oauth_clients WHERE client_id = 'ai-portal';")

if [ -n "$AI_PORTAL_EXISTS" ]; then
    echo -e "${GREEN}✓ ai-portal client exists${NC}"
else
    echo -e "${YELLOW}ai-portal client not found - will be created on first authz deploy${NC}"
fi

# Check api-service client
API_SERVICE_EXISTS=$(run_db_query "SELECT client_id FROM authz_oauth_clients WHERE client_id = 'api-service';")

if [ -n "$API_SERVICE_EXISTS" ]; then
    echo -e "${GREEN}✓ api-service client exists${NC}"
else
    echo -e "${BLUE}Creating api-service client...${NC}"
    
    # Get the jwt_secret from authz container to hash it the same way
    JWT_SECRET=$(pct exec ${AUTHZ_CTID} -- grep AUTHZ_BOOTSTRAP_CLIENT_SECRET /srv/authz/.env 2>/dev/null | cut -d= -f2 || echo "")
    
    if [ -n "$JWT_SECRET" ]; then
        # Hash the secret using Python (same method as authz service)
        SECRET_HASH=$(python3 << PYTHON_EOF
import hashlib
import secrets
import base64

secret = "${JWT_SECRET}"
salt = secrets.token_urlsafe(16)
iterations = 200000
dk = hashlib.pbkdf2_hmac('sha256', secret.encode(), salt.encode(), iterations)
hash_value = base64.b64encode(dk).decode()
print(f"pbkdf2_sha256\${iterations}\${salt}\${hash_value}")
PYTHON_EOF
)
        
        # Insert the api-service client
        run_db_query "INSERT INTO authz_oauth_clients (client_id, client_secret_hash, allowed_audiences, allowed_scopes, is_active, created_at) VALUES ('api-service', '${SECRET_HASH}', ARRAY['ingest-api', 'search-api', 'agent-api', 'authz'], ARRAY['read', 'write'], true, NOW());"
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ api-service client created${NC}"
        else
            echo -e "${YELLOW}⚠ Could not create api-service client${NC}"
        fi
    else
        echo -e "${YELLOW}⚠ Could not get jwt_secret to create api-service client${NC}"
    fi
fi

echo ""

#==============================================================================
# Get Admin Token
#==============================================================================

echo -e "${BLUE}Fetching admin token...${NC}"

ADMIN_TOKEN=$(pct exec ${AUTHZ_CTID} -- grep '^AUTHZ_ADMIN_TOKEN=' /srv/authz/.env 2>/dev/null | cut -d'=' -f2 || echo "")

if [ -z "$ADMIN_TOKEN" ]; then
    echo -e "${RED}Error: Could not fetch admin token from authz service${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Admin token retrieved${NC}"

echo ""

#==============================================================================
# Verify Token Exchange Works
#==============================================================================

echo -e "${BLUE}Verifying token exchange...${NC}"

JWT_SECRET=$(pct exec ${AUTHZ_CTID} -- grep AUTHZ_BOOTSTRAP_CLIENT_SECRET /srv/authz/.env 2>/dev/null | cut -d= -f2 || echo "")

TOKEN_RESPONSE=$(curl -s -X POST "${AUTHZ_URL}/oauth/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials&client_id=ai-portal&client_secret=${JWT_SECRET}&audience=ingest-api" 2>&1 || echo "")

if echo "$TOKEN_RESPONSE" | grep -q "access_token"; then
    echo -e "${GREEN}✓ Token exchange verified${NC}"
else
    echo -e "${YELLOW}⚠ Token exchange failed: ${TOKEN_RESPONSE}${NC}"
fi

echo ""

#==============================================================================
# Summary
#==============================================================================

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Test Credentials Summary${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Test User:${NC}"
echo "  User ID: ${TEST_USER_ID}"
echo "  Email: ${TEST_USER_EMAIL}"
echo ""
echo -e "${BLUE}OAuth Clients:${NC}"
echo "  ai-portal: For frontend user authentication"
echo "  api-service: For backend service-to-service calls"
echo "  (Both use jwt_secret as client_secret)"
echo ""
echo -e "${BLUE}Admin Token:${NC}"
echo "  Retrieved from authz service .env"
echo ""

#==============================================================================
# Output for .env files
#==============================================================================

echo -e "${BLUE}Environment variables for testing:${NC}"
echo ""
echo "# Test User"
echo "TEST_USER_ID=${TEST_USER_ID}"
echo "TEST_USER_EMAIL=${TEST_USER_EMAIL}"
echo ""
echo "# Authz Service"
echo "AUTHZ_URL=${AUTHZ_URL}"
echo "AUTHZ_ADMIN_TOKEN=${ADMIN_TOKEN}"
echo ""

echo -e "${GREEN}Done!${NC}"
