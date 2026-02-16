#!/usr/bin/env bash
# =============================================================================
# Seed Demo Data
# =============================================================================
#
# Uploads sample documents from busibox-testdocs repository to demonstrate
# the ingestion pipeline.
#
# Usage:
#   ./seed-demo-data.sh
#
# Prerequisites:
#   - All APIs must be running
#   - busibox-testdocs must be cloned (via warmup)
#
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${SCRIPT_DIR}/progress.sh"

TESTDOCS_DIR="${REPO_ROOT}/../busibox-testdocs"
API_BASE="https://localhost"
DATA_API="${API_BASE}/api/data"

# =============================================================================
# Check Prerequisites
# =============================================================================

if [[ ! -d "$TESTDOCS_DIR" ]]; then
    warn "busibox-testdocs not found at $TESTDOCS_DIR"
    info "Run 'make demo-warmup' first to clone the test documents"
    exit 0
fi

# Get an access token
info "Getting access token..."

# Use the bootstrap client credentials
TOKEN_RESPONSE=$(curl -sk -X POST "https://localhost/api/authz/oauth/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials" \
    -d "client_id=busibox-portal" \
    -d "client_secret=demo-secret" \
    -d "scope=data.write data.read" \
    2>/dev/null || echo "")

if [[ -z "$TOKEN_RESPONSE" || "$TOKEN_RESPONSE" == *"error"* ]]; then
    warn "Could not get access token - APIs may still be starting"
    info "Demo data will be uploaded when you sign in to the portal"
    exit 0
fi

ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))" 2>/dev/null || echo "")

if [[ -z "$ACCESS_TOKEN" ]]; then
    warn "Could not parse access token"
    exit 0
fi

# =============================================================================
# Upload Sample Documents
# =============================================================================

info "Uploading sample documents..."

SAMPLE_DOCS=(
    "${TESTDOCS_DIR}/pdf/general/doc01_rfp_project_management/source.pdf"
    "${TESTDOCS_DIR}/pdf/general/doc03_chartparser_paper/source.pdf"
    "${TESTDOCS_DIR}/pdf/general/doc05_rslzva1_datasheet/source.pdf"
)

UPLOADED=0

for doc in "${SAMPLE_DOCS[@]}"; do
    if [[ -f "$doc" ]]; then
        FILENAME=$(basename "$doc")
        info "  Uploading: $FILENAME"
        
        RESPONSE=$(curl -sk -X POST "${DATA_API}/v1/files/upload" \
            -H "Authorization: Bearer ${ACCESS_TOKEN}" \
            -F "file=@${doc}" \
            -F "metadata={\"source\": \"demo\", \"demo\": true}" \
            2>/dev/null || echo "error")
        
        if [[ "$RESPONSE" != "error" && "$RESPONSE" == *"file_id"* ]]; then
            success "  Uploaded: $FILENAME"
            UPLOADED=$((UPLOADED + 1))
        else
            warn "  Failed to upload: $FILENAME"
        fi
    else
        warn "  Document not found: $doc"
    fi
done

if [[ $UPLOADED -gt 0 ]]; then
    success "Uploaded $UPLOADED demo documents"
    info "Documents will be processed in the background"
    info "Check status in the Busibox Portal document library"
else
    warn "No documents were uploaded"
    info "You can upload documents manually through the Busibox Portal"
fi
