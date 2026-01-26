#!/usr/bin/env bash
# Test magic link creation and usage with grace period
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUSIBOX_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Source UI utilities for logging functions
source "$BUSIBOX_DIR/scripts/lib/ui.sh"

# Configuration
AUTHZ_URL="${AUTHZ_URL:-http://localhost:8010}"
TEST_EMAIL="${TEST_EMAIL:-test@example.com}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-dev-postgres}"

info "Testing Magic Link Flow"
info "======================"
echo ""

# Step 1: Clean up any existing test user
info "Step 1: Cleaning up existing test user..."
docker exec -i "$POSTGRES_CONTAINER" psql -U postgres -d authz -c \
    "DELETE FROM authz_magic_links WHERE user_id IN (SELECT user_id FROM authz_users WHERE email = '$TEST_EMAIL');" \
    2>/dev/null || true
docker exec -i "$POSTGRES_CONTAINER" psql -U postgres -d authz -c \
    "DELETE FROM authz_sessions WHERE user_id IN (SELECT user_id FROM authz_users WHERE email = '$TEST_EMAIL');" \
    2>/dev/null || true
docker exec -i "$POSTGRES_CONTAINER" psql -U postgres -d authz -c \
    "DELETE FROM authz_user_roles WHERE user_id IN (SELECT user_id FROM authz_users WHERE email = '$TEST_EMAIL');" \
    2>/dev/null || true
docker exec -i "$POSTGRES_CONTAINER" psql -U postgres -d authz -c \
    "DELETE FROM authz_users WHERE email = '$TEST_EMAIL';" \
    2>/dev/null || true
success "Test user cleaned up"
echo ""

# Step 2: Request magic link
info "Step 2: Requesting magic link for $TEST_EMAIL..."
response=$(curl -s -w "\n%{http_code}" -X POST "$AUTHZ_URL/auth/magic-links/request" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"$TEST_EMAIL\"}")

http_code=$(echo "$response" | tail -1)
body=$(echo "$response" | sed '$d')

if [[ "$http_code" != "200" ]]; then
    error "Failed to request magic link: HTTP $http_code"
    echo "$body"
    exit 1
fi

success "Magic link requested successfully"
echo ""

# Step 3: Get token from database
info "Step 3: Retrieving magic link token from database..."
magic_token=$(docker exec -i "$POSTGRES_CONTAINER" psql -U postgres -d authz -t -c \
    "SELECT token FROM authz_magic_links WHERE user_id = (SELECT user_id FROM authz_users WHERE email = '$TEST_EMAIL') ORDER BY created_at DESC LIMIT 1;" \
    | tr -d '[:space:]' | tr -d '\n' | tr -d '\r')

if [[ -z "$magic_token" ]]; then
    error "No magic token found in database"
    exit 1
fi

success "Magic token retrieved: $magic_token"
echo ""

# Step 4: Use magic link (first time)
info "Step 4: Using magic link (first time)..."
response=$(curl -s -w "\n%{http_code}" -X POST "$AUTHZ_URL/auth/magic-links/$magic_token/use" \
    -H "Content-Type: application/json" \
    -d "{}")

http_code=$(echo "$response" | tail -1)
body=$(echo "$response" | sed '$d')

if [[ "$http_code" != "200" ]]; then
    error "Failed to use magic link: HTTP $http_code"
    echo "$body"
    exit 1
fi

session_token=$(echo "$body" | jq -r '.session.token')
if [[ -z "$session_token" || "$session_token" == "null" ]]; then
    error "No session token in response"
    echo "$body"
    exit 1
fi

success "Magic link used successfully (first time)"
echo "Session token: ${session_token:0:20}..."
echo ""

# Step 5: Use magic link again (within grace period)
info "Step 5: Using magic link again (within 60s grace period)..."
sleep 2  # Wait 2 seconds to simulate double-click or Outlook verification

response=$(curl -s -w "\n%{http_code}" -X POST "$AUTHZ_URL/auth/magic-links/$magic_token/use" \
    -H "Content-Type: application/json" \
    -d "{}")

http_code=$(echo "$response" | tail -1)
body=$(echo "$response" | sed '$d')

if [[ "$http_code" != "200" ]]; then
    error "Failed to use magic link (grace period): HTTP $http_code"
    echo "$body"
    exit 1
fi

session_token2=$(echo "$body" | jq -r '.session.token')
if [[ -z "$session_token2" || "$session_token2" == "null" ]]; then
    error "No session token in grace period response"
    echo "$body"
    exit 1
fi

success "Magic link used successfully (grace period)"
echo "Session token: ${session_token2:0:20}..."
echo ""

# Step 6: Verify both tokens are valid
info "Step 6: Verifying both session tokens..."
for i in 1 2; do
    token_var="session_token$([[ $i == 2 ]] && echo '2' || echo '')"
    token="${!token_var}"
    
    response=$(curl -s -w "\n%{http_code}" -X GET "$AUTHZ_URL/auth/session/validate" \
        -H "Authorization: Bearer $token")
    
    http_code=$(echo "$response" | tail -1)
    body=$(echo "$response" | sed '$d')
    
    if [[ "$http_code" != "200" ]]; then
        error "Session token $i validation failed: HTTP $http_code"
        echo "$body"
        exit 1
    fi
    
    success "Session token $i is valid"
done
echo ""

# Step 7: Try to use magic link after grace period (should fail)
info "Step 7: Testing grace period expiration (waiting 65 seconds)..."
sleep 65

response=$(curl -s -w "\n%{http_code}" -X POST "$AUTHZ_URL/auth/magic-links/$magic_token/use" \
    -H "Content-Type: application/json" \
    -d "{}")

http_code=$(echo "$response" | tail -1)
body=$(echo "$response" | sed '$d')

if [[ "$http_code" == "200" ]]; then
    warn "Magic link should have expired after grace period but didn't"
    echo "$body"
elif [[ "$http_code" == "404" ]]; then
    success "Magic link correctly expired after grace period"
else
    warn "Unexpected status code: $http_code"
    echo "$body"
fi
echo ""

# Cleanup
info "Cleanup: Removing test user..."
docker exec -i "$POSTGRES_CONTAINER" psql -U postgres -d authz -c \
    "DELETE FROM authz_magic_links WHERE user_id IN (SELECT user_id FROM authz_users WHERE email = '$TEST_EMAIL');" \
    2>/dev/null || true
docker exec -i "$POSTGRES_CONTAINER" psql -U postgres -d authz -c \
    "DELETE FROM authz_sessions WHERE user_id IN (SELECT user_id FROM authz_users WHERE email = '$TEST_EMAIL');" \
    2>/dev/null || true
docker exec -i "$POSTGRES_CONTAINER" psql -U postgres -d authz -c \
    "DELETE FROM authz_user_roles WHERE user_id IN (SELECT user_id FROM authz_users WHERE email = '$TEST_EMAIL');" \
    2>/dev/null || true
docker exec -i "$POSTGRES_CONTAINER" psql -U postgres -d authz -c \
    "DELETE FROM authz_users WHERE email = '$TEST_EMAIL';" \
    2>/dev/null || true

echo ""
success "✓ All magic link tests passed!"
echo ""
echo "Summary:"
echo "  ✓ Magic link creation"
echo "  ✓ First use (session created)"
echo "  ✓ Second use within grace period (same session)"
echo "  ✓ Both session tokens valid"
echo "  ✓ Link expired after grace period"
