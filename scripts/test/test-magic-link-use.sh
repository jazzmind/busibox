#!/usr/bin/env bash
# Test magic link usage with grace period
# Usage: bash test-magic-link-use.sh <magic_token>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUSIBOX_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Source UI utilities for logging functions
source "$BUSIBOX_DIR/scripts/lib/ui.sh"

# Configuration
AUTHZ_URL="${AUTHZ_URL:-http://localhost:8010}"
MAGIC_TOKEN="${1:-}"

if [[ -z "$MAGIC_TOKEN" ]]; then
    error "Usage: $0 <magic_token>"
    echo "  Get the token from the install output or database:"
    echo "  docker exec dev-postgres psql -U postgres -d authz -t -c \"SELECT token FROM authz_magic_links ORDER BY created_at DESC LIMIT 1;\""
    exit 1
fi

info "Testing Magic Link: $MAGIC_TOKEN"
echo ""

# Step 1: Use magic link (first time)
info "Step 1: Using magic link (first time)..."
response=$(curl -s -w "\n%{http_code}" -X POST "$AUTHZ_URL/auth/magic-links/$MAGIC_TOKEN/use" \
    -H "Content-Type: application/json" \
    -d "{}")

http_code=$(echo "$response" | tail -1)
body=$(echo "$response" | sed '$d')

if [[ "$http_code" != "200" ]]; then
    error "Failed to use magic link: HTTP $http_code"
    echo "$body" | jq -C . || echo "$body"
    exit 1
fi

session_token=$(echo "$body" | jq -r '.session.token')
user_email=$(echo "$body" | jq -r '.user.email')

if [[ -z "$session_token" || "$session_token" == "null" ]]; then
    error "No session token in response"
    echo "$body" | jq -C .
    exit 1
fi

success "Magic link used successfully (first time)"
echo "  User: $user_email"
echo "  Session token: ${session_token:0:30}..."
echo ""

# Step 2: Use magic link again immediately (within grace period)
info "Step 2: Using same magic link again (grace period test)..."
sleep 1  # Wait 1 second to simulate double-click or Outlook verification

response=$(curl -s -w "\n%{http_code}" -X POST "$AUTHZ_URL/auth/magic-links/$MAGIC_TOKEN/use" \
    -H "Content-Type: application/json" \
    -d "{}")

http_code=$(echo "$response" | tail -1)
body=$(echo "$response" | sed '$d')

if [[ "$http_code" != "200" ]]; then
    error "Failed to use magic link (grace period): HTTP $http_code"
    echo "$body" | jq -C . || echo "$body"
    exit 1
fi

session_token2=$(echo "$body" | jq -r '.session.token')

if [[ -z "$session_token2" || "$session_token2" == "null" ]]; then
    error "No session token in grace period response"
    echo "$body" | jq -C .
    exit 1
fi

success "Magic link used successfully (within grace period)"
echo "  Session token: ${session_token2:0:30}..."
echo ""

success "✓ All magic link tests passed!"
echo ""
echo "Summary:"
echo "  ✓ First use: Session created successfully"
echo "  ✓ Second use: Grace period handled correctly (within 60s)"
echo "  ✓ Both requests returned valid session tokens"
echo ""
echo "Note: Grace period expiration test skipped (would take 65+ seconds)"
echo "      To test manually, wait 65 seconds and try using the link again"
