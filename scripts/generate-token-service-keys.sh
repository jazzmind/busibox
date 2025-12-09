#!/usr/bin/env bash
#
# Generate and Configure TOKEN_SERVICE Keys
#
# EXECUTION CONTEXT: Admin workstation
# PURPOSE: Generate Ed25519 keys for agent-server token service and add to vault
#
# USAGE:
#   bash scripts/generate-token-service-keys.sh
#
# REQUIRES:
#   - agent-server repository cloned alongside busibox
#   - Node.js 20+ installed
#   - ~/.vault_pass file OR will prompt for vault password
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Source UI library
source "${SCRIPT_DIR}/lib/ui.sh"

# Paths
AGENT_SERVER_DIR="${REPO_ROOT}/../agent-server"
VAULT_FILE="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.yml"
VAULT_PASS_FILE="$HOME/.vault_pass"

# Check if vault password file exists
get_vault_flags() {
    if [ -f "$VAULT_PASS_FILE" ]; then
        echo "--vault-password-file $VAULT_PASS_FILE"
    else
        echo "--ask-vault-pass"
    fi
}

# Check if agent-server exists
check_agent_server() {
    if [ ! -d "$AGENT_SERVER_DIR" ]; then
        error "agent-server repository not found at: $AGENT_SERVER_DIR"
        echo ""
        info "Expected location: ${REPO_ROOT}/../agent-server"
        info "Clone it with: git clone git@github.com:jazzmind/agent-server.git ${REPO_ROOT}/../agent-server"
        return 1
    fi
    
    if [ ! -f "$AGENT_SERVER_DIR/package.json" ]; then
        error "Invalid agent-server directory (missing package.json)"
        return 1
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

# Generate keys using agent-server setup-auth
generate_keys() {
    header "Generating TOKEN_SERVICE Keys" 70
    echo ""
    
    info "Checking agent-server repository..."
    if ! check_agent_server; then
        return 1
    fi
    success "Found agent-server at: $AGENT_SERVER_DIR"
    echo ""
    
    info "Installing dependencies..."
    cd "$AGENT_SERVER_DIR"
    if [ ! -d "node_modules" ]; then
        npm install --silent > /dev/null 2>&1 || {
            error "Failed to install dependencies"
            return 1
        }
    fi
    success "Dependencies ready"
    echo ""
    
    info "Generating Ed25519 keypair..."
    echo ""
    
    # Run setup-auth and capture output
    local output
    output=$(npm run setup-auth 2>&1) || {
        error "Failed to generate keys"
        echo "$output"
        return 1
    }
    
    # Extract the keys from output
    local private_key
    local public_key
    
    private_key=$(echo "$output" | grep "TOKEN_SERVICE_PRIVATE_KEY=" | sed "s/TOKEN_SERVICE_PRIVATE_KEY=//")
    public_key=$(echo "$output" | grep "TOKEN_SERVICE_PUBLIC_KEY=" | sed "s/TOKEN_SERVICE_PUBLIC_KEY=//")
    
    if [ -z "$private_key" ] || [ -z "$public_key" ]; then
        error "Failed to extract keys from setup-auth output"
        echo ""
        echo "Output:"
        echo "$output"
        return 1
    fi
    
    success "Keys generated successfully"
    echo ""
    
    # Display key info (kid only, not the full keys)
    local kid
    kid=$(echo "$private_key" | grep -oE '"kid":"[^"]*"' | cut -d'"' -f4)
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
        print "    # Generated by: scripts/generate-token-service-keys.sh"
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
