#!/usr/bin/env bash
#
# Upload SSL Certificate to Ansible Vault
# 
# This script helps you securely upload SSL certificates to the Ansible vault
# without storing them in the git repository. It MERGES with existing vault content.
#
# Usage:
#   bash scripts/upload-ssl-cert.sh <cert-file> <key-file> [chain-file]
#
# Example:
#   bash scripts/upload-ssl-cert.sh ./cert.crt ./cert.key ./chain.crt
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
Usage: $0 <cert-file> <key-file> [chain-file]

Upload SSL certificates to Ansible vault for deployment.
This script MERGES certificates into the existing vault, preserving all other secrets.

Arguments:
  cert-file   Path to certificate file (.crt or .pem)
  key-file    Path to private key file (.key or .pem)
  chain-file  Path to certificate chain file (optional)

Example:
  $0 ./cert.crt ./cert.key ./chain.crt

The certificates will be stored in:
  $VAULT_FILE
  
Under the path: secrets.ssl_certificates

IMPORTANT:
- Script stores certificates generically (no domain in vault)
- Domain names come from inventory (base_domain variable)
- Works with wildcard certificates (*.ai.jaycashman.com)
- Same certificates used for all environments (test/production)

This file is encrypted with ansible-vault and should NOT be committed unencrypted.
EOF
}

# Check arguments
if [ $# -lt 2 ]; then
    error "Missing required arguments"
    echo
    usage
    exit 1
fi

CERT_FILE="$1"
KEY_FILE="$2"
CHAIN_FILE="${3:-}"

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

# Check for required tools
if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    error "Python is required but not installed"
    exit 1
fi

PYTHON_CMD=$(command -v python3 || command -v python)

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

# Show certificate domains (CN and SANs)
echo ""
info "Certificate covers these domains:"
CERT_CN=$(openssl x509 -in "$CERT_FILE" -noout -subject | sed -n 's/.*CN = \(.*\)/\1/p')
echo "  Common Name (CN): $CERT_CN"

CERT_SANS=$(openssl x509 -in "$CERT_FILE" -noout -text | grep -A1 "Subject Alternative Name" | tail -1 | sed 's/DNS://g' | tr ',' '\n' | sed 's/^[[:space:]]*/  - /')
if [ -n "$CERT_SANS" ]; then
    echo "  Subject Alternative Names (SANs):"
    echo "$CERT_SANS"
fi

if [[ "$CERT_CN" == "*."* ]]; then
    success "Wildcard certificate detected - will work for all subdomains"
fi

# Check if vault file exists
if [ ! -f "$VAULT_FILE" ]; then
    error "Vault file not found: $VAULT_FILE"
    echo "Please create the vault file first following SETUP.md"
    exit 1
fi

# Check if vault is encrypted
WAS_ENCRYPTED=false
if head -n1 "$VAULT_FILE" | grep -q "^\$ANSIBLE_VAULT"; then
    info "Vault file is encrypted, decrypting temporarily..."
    TEMP_VAULT=$(mktemp)
    trap "rm -f $TEMP_VAULT" EXIT
    
    if ! ansible-vault decrypt "$VAULT_FILE" --output="$TEMP_VAULT" 2>/dev/null; then
        error "Failed to decrypt vault file. Do you have the vault password?"
        rm -f "$TEMP_VAULT"
        exit 1
    fi
    
    WORKING_VAULT="$TEMP_VAULT"
    WAS_ENCRYPTED=true
else
    warn "Vault file is not encrypted!"
    WORKING_VAULT="$VAULT_FILE"
fi

# Read certificate files
info "Reading certificate files..."
CERT_CONTENT=$(cat "$CERT_FILE")
KEY_CONTENT=$(cat "$KEY_FILE")
if [ -n "$CHAIN_FILE" ]; then
    CHAIN_CONTENT=$(cat "$CHAIN_FILE")
    HAS_CHAIN=true
else
    CHAIN_CONTENT=""
    HAS_CHAIN=false
fi

# Create Python script to merge YAML
info "Merging SSL certificates into vault..."
TEMP_SCRIPT=$(mktemp)
trap "rm -f $TEMP_SCRIPT $TEMP_VAULT" EXIT

cat > "$TEMP_SCRIPT" <<'PYTHON_EOF'
import sys
import yaml
from pathlib import Path

# Custom representer for multi-line strings to use literal block style
def str_representer(dumper, data):
    if '\n' in data:
        # Use literal block style (|) for multi-line strings
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

yaml.add_representer(str, str_representer)

def merge_ssl_certs(vault_file, cert_content, key_content, chain_content, has_chain):
    """Merge SSL certificates into existing vault YAML"""
    
    # Load existing vault
    with open(vault_file, 'r') as f:
        vault_data = yaml.safe_load(f) or {}
    
    # Ensure secrets key exists
    if 'secrets' not in vault_data:
        vault_data['secrets'] = {}
    
    # Ensure certificate content ends with newline (standard PEM format)
    cert_content = cert_content.rstrip() + '\n'
    key_content = key_content.rstrip() + '\n'
    if chain_content:
        chain_content = chain_content.rstrip() + '\n'
    
    # Create or update ssl_certificates section
    vault_data['secrets']['ssl_certificates'] = {
        'certificate': cert_content,
        'private_key': key_content
    }
    
    if has_chain:
        vault_data['secrets']['ssl_certificates']['chain'] = chain_content
    
    # Write back to file with proper formatting
    with open(vault_file, 'w') as f:
        # Write header comment
        f.write('---\n')
        f.write('# Ansible Vault - Encrypted Deployment Configuration\n')
        f.write('# This file should be encrypted with ansible-vault\n')
        f.write('#\n')
        f.write('# SSL certificates updated by upload-ssl-cert.sh\n')
        f.write('\n')
        
        # Write YAML data with custom formatting
        yaml.dump(vault_data, f, default_flow_style=False, sort_keys=False, width=120, allow_unicode=True)
    
    return True

if __name__ == '__main__':
    vault_file = sys.argv[1]
    cert_content = sys.argv[2]
    key_content = sys.argv[3]
    chain_content = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
    has_chain = sys.argv[5] == 'true' if len(sys.argv) > 5 else False
    
    try:
        merge_ssl_certs(vault_file, cert_content, key_content, chain_content, has_chain)
        print("SUCCESS")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
PYTHON_EOF

# Run Python script to merge
if ! $PYTHON_CMD "$TEMP_SCRIPT" "$WORKING_VAULT" "$CERT_CONTENT" "$KEY_CONTENT" "$CHAIN_CONTENT" "$HAS_CHAIN" 2>/dev/null; then
    error "Failed to merge SSL certificates into vault"
    exit 1
fi

success "SSL certificates merged into vault"

# Show preview (with redacted private key)
info "Preview of ssl_certificates section (private key redacted):"
echo "---"
if command -v yq &> /dev/null; then
    yq '.secrets.ssl_certificates' "$WORKING_VAULT" 2>/dev/null | sed '/private_key:/,/^[a-z]/ { /private_key:/!{ /^[a-z]/!{ s/.*/      [REDACTED]/; }; }; }' || cat "$WORKING_VAULT" | grep -A 20 "ssl_certificates:" | head -25
else
    cat "$WORKING_VAULT" | grep -A 20 "ssl_certificates:" | head -25 | sed '/private_key:/,/^  [a-z]/ { /private_key:/!{ /^  [a-z]/!{ s/.*/      [REDACTED]/; }; }; }'
fi
echo "---"

# Confirm
echo
read -p "Upload these certificates to vault? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    warn "Upload cancelled"
    exit 0
fi

# Write back to actual vault file
if [ "$WAS_ENCRYPTED" = true ]; then
    info "Re-encrypting vault file..."
    # First copy unencrypted content
    cp "$WORKING_VAULT" "$VAULT_FILE"
    
    # Then encrypt in place
    if ! ansible-vault encrypt "$VAULT_FILE" 2>/dev/null; then
        error "Failed to encrypt vault file"
        exit 1
    fi
else
    # Just copy if wasn't encrypted
    cp "$WORKING_VAULT" "$VAULT_FILE"
    warn "Vault file is NOT encrypted. You should encrypt it:"
    echo "  ansible-vault encrypt $VAULT_FILE"
fi

success "SSL certificates uploaded successfully!"
echo
info "Next steps:"
echo "  1. Verify the vault is encrypted:"
echo "     head -n1 $VAULT_FILE"
echo
echo "  2. View the vault contents:"
echo "     ansible-vault view $VAULT_FILE | grep -A 20 ssl_certificates"
echo
echo "  3. Deploy to proxy container:"
echo "     cd provision/ansible"
echo "     ansible-playbook -i inventory/test/hosts.yml site.yml --tags nginx --ask-vault-pass -e placeholder_mode=true"
echo
echo "  4. Test SSL:"
if [ -n "$CHAIN_FILE" ]; then
    echo "     openssl s_client -connect YOUR_DOMAIN:443 -showcerts"
else
    warn "No certificate chain provided. SSL stapling may not work."
    echo "     You may want to add the intermediate certificate chain."
fi
echo

