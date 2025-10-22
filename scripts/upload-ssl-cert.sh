#!/usr/bin/env bash
#
# Upload SSL Certificate to Ansible Vault
# 
# This script helps you securely upload SSL certificates to the Ansible vault
# without storing them in the git repository.
#
# Usage:
#   bash scripts/upload-ssl-cert.sh <domain> <cert-file> <key-file> [chain-file]
#
# Example:
#   bash scripts/upload-ssl-cert.sh test.ai.jaycashman.com ./cert.crt ./cert.key ./chain.crt
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VAULT_FILE="$PROJECT_ROOT/provision/ansible/roles/secrets/vars/vault.yml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

usage() {
    cat <<EOF
Usage: $0 <domain> <cert-file> <key-file> [chain-file]

Upload SSL certificates to Ansible vault for deployment.

Arguments:
  domain      Domain name (e.g., test.ai.jaycashman.com)
  cert-file   Path to certificate file (.crt or .pem)
  key-file    Path to private key file (.key or .pem)
  chain-file  Path to certificate chain file (optional)

Example:
  $0 test.ai.jaycashman.com ./cert.crt ./cert.key ./chain.crt

The certificates will be stored in:
  $VAULT_FILE

This file is encrypted with ansible-vault and should NOT be committed unencrypted.
EOF
}

# Check arguments
if [ $# -lt 3 ]; then
    error "Missing required arguments"
    echo
    usage
    exit 1
fi

DOMAIN="$1"
CERT_FILE="$2"
KEY_FILE="$3"
CHAIN_FILE="${4:-}"

# Validate files exist
if [ ! -f "$CERT_FILE" ]; then
    error "Certificate file not found: $CERT_FILE"
    exit 1
fi

if [ ! -f "$KEY_FILE" ]; then
    error "Private key file not found: $KEY_FILE"
    exit 1
fi

if [ -n "$CHAIN_FILE" ] && [ ! -f "$CHAIN_FILE" ]; then
    error "Chain file not found: $CHAIN_FILE"
    exit 1
fi

# Validate certificate and key match
info "Validating certificate and key..."
CERT_MODULUS=$(openssl x509 -noout -modulus -in "$CERT_FILE" | openssl md5)
KEY_MODULUS=$(openssl rsa -noout -modulus -in "$KEY_FILE" 2>/dev/null | openssl md5)

if [ "$CERT_MODULUS" != "$KEY_MODULUS" ]; then
    error "Certificate and private key do not match!"
    echo "  Certificate modulus: $CERT_MODULUS"
    echo "  Key modulus: $KEY_MODULUS"
    exit 1
fi

success "Certificate and key match"

# Display certificate info
info "Certificate information:"
openssl x509 -in "$CERT_FILE" -noout -subject -issuer -dates

# Check if vault file exists
if [ ! -f "$VAULT_FILE" ]; then
    info "Creating new vault file: $VAULT_FILE"
    mkdir -p "$(dirname "$VAULT_FILE")"
    echo "---" > "$VAULT_FILE"
    echo "# Ansible Vault for Secrets" >> "$VAULT_FILE"
    echo "# This file should be encrypted with: ansible-vault encrypt $VAULT_FILE" >> "$VAULT_FILE"
    echo "" >> "$VAULT_FILE"
    echo "secrets:" >> "$VAULT_FILE"
fi

# Check if vault is encrypted
if head -n1 "$VAULT_FILE" | grep -q "^\$ANSIBLE_VAULT"; then
    info "Vault file is encrypted, decrypting temporarily..."
    TEMP_VAULT=$(mktemp)
    trap "rm -f $TEMP_VAULT" EXIT
    
    if ! ansible-vault decrypt "$VAULT_FILE" --output="$TEMP_VAULT"; then
        error "Failed to decrypt vault file. Do you have the vault password?"
        exit 1
    fi
    
    VAULT_FILE="$TEMP_VAULT"
    NEEDS_REENCRYPT=true
else
    warn "Vault file is not encrypted!"
    NEEDS_REENCRYPT=false
fi

# Create temporary file for new vault content
TEMP_NEW_VAULT=$(mktemp)
trap "rm -f $TEMP_NEW_VAULT $TEMP_VAULT" EXIT

# Read certificate files
info "Reading certificate files..."
CERT_CONTENT=$(cat "$CERT_FILE")
KEY_CONTENT=$(cat "$KEY_FILE")
if [ -n "$CHAIN_FILE" ]; then
    CHAIN_CONTENT=$(cat "$CHAIN_FILE")
else
    CHAIN_CONTENT=""
fi

# Create new vault content
cat > "$TEMP_NEW_VAULT" <<EOF
---
# Ansible Vault for Secrets
# Encrypted with ansible-vault

secrets:
  ssl_certificates:
    domain: "$DOMAIN"
    certificate: |
$(echo "$CERT_CONTENT" | sed 's/^/      /')
    private_key: |
$(echo "$KEY_CONTENT" | sed 's/^/      /')
EOF

if [ -n "$CHAIN_CONTENT" ]; then
    cat >> "$TEMP_NEW_VAULT" <<EOF
    chain: |
$(echo "$CHAIN_CONTENT" | sed 's/^/      /')
EOF
fi

# Show preview
info "Preview of vault content (private key redacted):"
echo "---"
cat "$TEMP_NEW_VAULT" | sed '/private_key:/,/^    [a-z]/ { /private_key:/!{ /^    [a-z]/!d; }; s/-----BEGIN.*-----/[REDACTED]/; s/-----END.*-----/[REDACTED]/; }'
echo "---"

# Confirm
echo
read -p "Upload these certificates to vault? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    warn "Upload cancelled"
    exit 0
fi

# Write to actual vault file
ACTUAL_VAULT="$PROJECT_ROOT/provision/ansible/roles/secrets/vars/vault.yml"
cp "$TEMP_NEW_VAULT" "$ACTUAL_VAULT"

if [ "$NEEDS_REENCRYPT" = true ]; then
    info "Re-encrypting vault file..."
    if ! ansible-vault encrypt "$ACTUAL_VAULT"; then
        error "Failed to encrypt vault file"
        exit 1
    fi
fi

success "SSL certificates uploaded successfully!"
echo
info "Next steps:"
echo "  1. Deploy to proxy container with: cd provision/ansible && ansible-playbook -i inventory/test/hosts.yml site.yml --tags nginx --ask-vault-pass"
echo "  2. Verify SSL: curl -v https://$DOMAIN"
echo
warn "Remember: This vault file should be encrypted before committing to git"
if [ "$NEEDS_REENCRYPT" = false ]; then
    echo "  Run: ansible-vault encrypt $ACTUAL_VAULT"
fi

