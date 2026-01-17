#!/usr/bin/env bash
#
# Generate and Configure TOKEN_SERVICE Keys
#
# EXECUTION CONTEXT: Admin workstation
# PURPOSE: Generate Ed25519 keys for agent-server token service and add to vault
#
# USAGE:
#   From ansible dir: bash scripts/generate-token-service-keys.sh
#   From repo root:   bash provision/ansible/scripts/generate-token-service-keys.sh
#
# REQUIRES:
#   - Python 3.11+ with cryptography library
#   - ~/.vault_pass file OR will prompt for vault password
#
set -euo pipefail

# Get script directory (provision/ansible/scripts)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${ANSIBLE_DIR}/../.." && pwd)"

# Source UI library from main scripts
source "${REPO_ROOT}/scripts/lib/ui.sh"

# Paths - key generator is local to this script
KEY_GENERATOR_DIR="${SCRIPT_DIR}"
VAULT_FILE="${ANSIBLE_DIR}/roles/secrets/vars/vault.yml"
VAULT_PASS_FILE="$HOME/.vault_pass"

# Check if vault password file exists
get_vault_flags() {
    if [ -f "$VAULT_PASS_FILE" ]; then
        echo "--vault-password-file $VAULT_PASS_FILE"
    else
        echo "--ask-vault-pass"
    fi
}

# Check if Python and cryptography library are available
check_key_generator() {
    if ! command -v python3 &> /dev/null; then
        error "python3 not found - required for key generation"
        return 1
    fi
    
    # Check if cryptography library is installed
    if ! python3 -c "import cryptography" 2>/dev/null; then
        warn "cryptography library not found"
        info "Installing cryptography library..."
        if ! python3 -m pip install cryptography --quiet 2>/dev/null; then
            error "Failed to install cryptography library"
            info "Try: python3 -m pip install cryptography"
            return 1
        fi
    fi
    
    return 0
}

# Check if keys already exist in vault
check_existing_keys() {
    local vault_flags=$(get_vault_flags)
    
    cd "${REPO_ROOT}/provision/ansible"
    
    # Decrypt vault and check for token_service keys
    if ansible-vault view roles/secrets/vars/vault.yml $vault_flags 2>/dev/null | \
       grep -q "token_service_private_key:"; then
        return 0  # Keys exist
    else
        return 1  # Keys don't exist
    fi
}

# Generate keys using standalone key generator
generate_keys() {
    header "Generating TOKEN_SERVICE Keys" 70
    echo ""
    
    info "Checking key generator..."
    if ! check_key_generator; then
        return 1
    fi
    success "Key generator ready"
    echo ""
    
    info "Generating Ed25519 keypair..."
    echo ""
    
    # Run key generator and capture output
    local output
    output=$(python3 "${KEY_GENERATOR_DIR}/generate_jwk_keys.py" 2>&1) || {
        error "Failed to generate keys"
        echo "$output"
        return 1
    }
    
    # Parse JSON output using Python
    # The privateKey and publicKey fields are JSON-stringified, so we need to parse them
    # to get the actual JSON objects (not Python dict strings)
    local kid
    local private_key
    local public_key
    
    kid=$(echo "$output" | python3 -c "import json, sys; print(json.load(sys.stdin)['kid'])")
    # These are already JSON strings, but we keep them as-is (they have proper double quotes)
    private_key=$(echo "$output" | python3 -c "import json, sys; data = json.load(sys.stdin); print(data['privateKey'])")
    public_key=$(echo "$output" | python3 -c "import json, sys; data = json.load(sys.stdin); print(data['publicKey'])")
    
    if [ -z "$private_key" ] || [ -z "$public_key" ]; then
        error "Failed to extract keys from generator output"
        echo ""
        echo "Output:"
        echo "$output"
        echo ""
        echo "Parsed values:"
        echo "kid: '$kid'"
        echo "private_key length: ${#private_key}"
        echo "public_key length: ${#public_key}"
        return 1
    fi
    
    success "Keys generated successfully"
    echo ""
    
    # Display key info
    info "Key ID: $kid"
    info "Algorithm: EdDSA (Ed25519)"
    echo ""
    
    # Return keys via global variables (bash doesn't have good return mechanisms)
    GENERATED_PRIVATE_KEY="$private_key"
    GENERATED_PUBLIC_KEY="$public_key"
    
    return 0
}

# Add keys to vault
add_keys_to_vault() {
    local private_key="$1"
    local public_key="$2"
    
    header "Adding Keys to Ansible Vault" 70
    echo ""
    
    local vault_flags=$(get_vault_flags)
    
    cd "${REPO_ROOT}/provision/ansible"
    
    # Create temporary file with decrypted vault
    local temp_file=$(mktemp)
    trap "rm -f $temp_file" EXIT
    
    info "Decrypting vault..."
    if ! ansible-vault view roles/secrets/vars/vault.yml $vault_flags > "$temp_file" 2>/dev/null; then
        error "Failed to decrypt vault"
        return 1
    fi
    success "Vault decrypted"
    echo ""
    
    # Check if keys already exist
    if grep -q "token_service_private_key:" "$temp_file"; then
        warn "TOKEN_SERVICE keys already exist in vault"
        echo ""
        if ! confirm "Overwrite existing keys?"; then
            info "Keeping existing keys"
            return 0
        fi
        
        # Remove existing keys
        sed -i.bak '/token_service_private_key:/d' "$temp_file"
        sed -i.bak '/token_service_public_key:/d' "$temp_file"
        rm -f "${temp_file}.bak"
    fi
    
    info "Adding keys to vault..."
    
    # Find the agent-server section and add keys after management_client_secret
    # Use awk to insert after the line containing management_client_secret
    awk -v private="$private_key" -v public="$public_key" '
    /management_client_secret:/ {
        print
        print "    # Token Service Keys (Ed25519 JWK format)"
        print "    # Generated by: scripts /generate/generate-token-service-keys.sh"
        print "    token_service_private_key: " private
        print "    token_service_public_key: " public
        next
    }
    { print }
    ' "$temp_file" > "${temp_file}.new"
    
    mv "${temp_file}.new" "$temp_file"
    
    # Re-encrypt vault
    info "Re-encrypting vault..."
    if ! ansible-vault encrypt "$temp_file" $vault_flags --output=roles/secrets/vars/vault.yml 2>/dev/null; then
        error "Failed to re-encrypt vault"
        return 1
    fi
    
    success "Keys added to vault successfully"
    echo ""
    
    cd "${REPO_ROOT}"
    return 0
}

# Main execution
main() {
    clear
    box "TOKEN_SERVICE Key Generation" 70
    echo ""
    info "This will generate Ed25519 keys for agent-server token service"
    info "and add them to your Ansible vault"
    echo ""
    
    # Check if keys already exist
    if check_existing_keys; then
        warn "TOKEN_SERVICE keys already exist in vault"
        echo ""
        if ! confirm "Regenerate keys (will overwrite existing)?"; then
            info "Keeping existing keys"
            return 0
        fi
    fi
    
    # Generate keys
    if ! generate_keys; then
        error "Key generation failed"
        return 1
    fi
    
    # Add to vault
    if ! add_keys_to_vault "$GENERATED_PRIVATE_KEY" "$GENERATED_PUBLIC_KEY"; then
        error "Failed to add keys to vault"
        return 1
    fi
    
    separator 70
    success "TOKEN_SERVICE keys configured successfully!"
    separator 70
    echo ""
    info "Next steps:"
    list_item "info" "Deploy agent-server: make deploy-agent-server"
    list_item "info" "Or deploy all: make all"
    echo ""
    
    return 0
}

# Run main
main "$@"
