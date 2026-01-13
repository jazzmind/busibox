#!/bin/bash
set -euo pipefail

#==============================================================================
# Setup Signal Bot Authentication
#
# EXECUTION CONTEXT: 
#   - Proxmox host (requires pct access)
#   - Or any machine with access to authz service
#
# DESCRIPTION:
#   Sets up authentication for the Signal bot service:
#   - Creates OAuth client for signal-bot
#   - Creates service user for the bot
#   - Verifies token exchange works
#
# USAGE:
#   bash scripts/setup/setup-signal-bot-auth.sh [test|production]
#
# PREREQUISITES:
#   - Authz service deployed and running
#   - Admin token available
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

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Setup Signal Bot Authentication${NC}"
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

# Signal bot service configuration
SIGNAL_BOT_CLIENT_ID="signal-bot-client"
SIGNAL_BOT_USER_ID="signal-bot-service"
SIGNAL_BOT_EMAIL="signal-bot@internal.busibox.local"

#==============================================================================
# Check Authz Service
#==============================================================================

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
    local db="${2:-authz}"
    pct exec ${PG_CTID} -- sudo -u postgres psql -d ${db} -t -A -c "$query" 2>/dev/null || echo ""
}

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

# Get JWT secret for client secret
JWT_SECRET=$(pct exec ${AUTHZ_CTID} -- grep '^AUTHZ_BOOTSTRAP_CLIENT_SECRET=' /srv/authz/.env 2>/dev/null | cut -d'=' -f2 || echo "")
if [ -z "$JWT_SECRET" ]; then
    JWT_SECRET=$(pct exec ${AUTHZ_CTID} -- grep '^JWT_SECRET=' /srv/authz/.env 2>/dev/null | cut -d'=' -f2 || echo "")
fi

if [ -z "$JWT_SECRET" ]; then
    echo -e "${RED}Error: Could not fetch JWT secret from authz service${NC}"
    exit 1
fi

#==============================================================================
# Create Service User
#==============================================================================

echo -e "${BLUE}Checking for signal-bot service user...${NC}"

EXISTING_USER=$(run_db_query "SELECT user_id FROM authz_users WHERE email = '${SIGNAL_BOT_EMAIL}' LIMIT 1;")

if [ -n "$EXISTING_USER" ]; then
    echo -e "${GREEN}✓ Service user already exists: ${EXISTING_USER}${NC}"
    SIGNAL_BOT_USER_ID="$EXISTING_USER"
else
    echo -e "${BLUE}Creating signal-bot service user...${NC}"
    
    # Generate a UUID for the user
    SIGNAL_BOT_USER_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
    
    run_db_query "INSERT INTO authz_users (user_id, email, created_at) VALUES ('${SIGNAL_BOT_USER_ID}', '${SIGNAL_BOT_EMAIL}', NOW());"
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Service user created: ${SIGNAL_BOT_USER_ID}${NC}"
    else
        echo -e "${RED}Failed to create service user${NC}"
        exit 1
    fi
fi
echo ""

#==============================================================================
# Create OAuth Client
#==============================================================================

echo -e "${BLUE}Checking signal-bot OAuth client...${NC}"

CLIENT_EXISTS=$(run_db_query "SELECT client_id FROM authz_oauth_clients WHERE client_id = '${SIGNAL_BOT_CLIENT_ID}';")

if [ -n "$CLIENT_EXISTS" ]; then
    echo -e "${GREEN}✓ OAuth client already exists${NC}"
else
    echo -e "${BLUE}Creating signal-bot OAuth client...${NC}"
    
    CREATE_RESPONSE=$(curl -s -X POST "${AUTHZ_URL}/admin/oauth-clients" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        -d "{
            \"client_id\": \"${SIGNAL_BOT_CLIENT_ID}\",
            \"client_secret\": \"${JWT_SECRET}\",
            \"allowed_audiences\": [\"agent-api\", \"search-api\", \"ingest-api\"],
            \"allowed_scopes\": [\"agent.execute\", \"chat.write\", \"chat.read\", \"search.read\"]
        }" 2>&1)
    
    if echo "$CREATE_RESPONSE" | grep -q "client_id"; then
        echo -e "${GREEN}✓ OAuth client created${NC}"
    else
        echo -e "${RED}Failed to create OAuth client: ${CREATE_RESPONSE}${NC}"
        exit 1
    fi
fi
echo ""

#==============================================================================
# Verify Token Exchange
#==============================================================================

echo -e "${BLUE}Verifying token exchange...${NC}"

TOKEN_RESPONSE=$(curl -s -X POST "${AUTHZ_URL}/oauth/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials&client_id=${SIGNAL_BOT_CLIENT_ID}&client_secret=${JWT_SECRET}&audience=agent-api" 2>&1 || echo "")

if echo "$TOKEN_RESPONSE" | grep -q "access_token"; then
    echo -e "${GREEN}✓ Token exchange verified${NC}"
    
    # Extract token for display (first 50 chars)
    ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'][:50])")
    echo -e "${BLUE}  Token preview: ${ACCESS_TOKEN}...${NC}"
else
    echo -e "${YELLOW}⚠ Token exchange failed: ${TOKEN_RESPONSE}${NC}"
fi
echo ""

#==============================================================================
# Summary
#==============================================================================

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Signal Bot Auth Setup Complete${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Service User:${NC}"
echo "  User ID: ${SIGNAL_BOT_USER_ID}"
echo "  Email: ${SIGNAL_BOT_EMAIL}"
echo ""
echo -e "${BLUE}OAuth Client:${NC}"
echo "  Client ID: ${SIGNAL_BOT_CLIENT_ID}"
echo "  Scopes: agent.execute, chat.write, chat.read, search.read"
echo ""
echo -e "${BLUE}Environment Variables for signal-bot:${NC}"
echo ""
echo "AUTH_CLIENT_ID=${SIGNAL_BOT_CLIENT_ID}"
echo "AUTH_CLIENT_SECRET=<jwt_secret from vault>"
echo "SERVICE_USER_ID=${SIGNAL_BOT_USER_ID}"
echo ""
echo -e "${BLUE}Add to vault.yml:${NC}"
echo ""
echo "secrets:"
echo "  signal_bot:"
echo "    phone_number: \"+12025551234\"  # Your Signal phone number"
echo "    service_user_id: \"${SIGNAL_BOT_USER_ID}\""
echo "    service_user_email: \"${SIGNAL_BOT_EMAIL}\""
echo ""
echo -e "${GREEN}Done!${NC}"
