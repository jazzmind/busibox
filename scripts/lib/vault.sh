#!/usr/bin/env bash
#
# Vault Access Library for Busibox
#
# Description:
#   Provides functions for accessing Ansible vault secrets from make install/update.
#   Used by both Docker and Proxmox deployments to access encrypted secrets.
#
#   MULTI-VAULT ARCHITECTURE:
#   -------------------------
#   Supports separate vault files per environment (dev, staging, prod, demo):
#   - vault.dev.yml    -> ~/.busibox-vault-pass-dev
#   - vault.staging.yml -> ~/.busibox-vault-pass-staging
#   - vault.prod.yml   -> ~/.busibox-vault-pass-prod
#   - vault.demo.yml   -> ~/.busibox-vault-pass-demo
#   
#   Fallback for legacy setups:
#   - vault.yml        -> ~/.vault_pass
#
# Usage:
#   source scripts/lib/vault.sh
#   set_vault_environment "prod"  # Set environment first!
#   ensure_vault_access
#   get_vault_secret "secrets.postgresql.password"
#
# Dependencies: ansible-vault, yq (optional, for structured reading)

# Base paths (relative to REPO_ROOT)
_VAULT_BASE_DIR="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/provision/ansible/roles/secrets/vars"

# Current vault environment (set by set_vault_environment)
VAULT_ENVIRONMENT=""

# Vault file locations - these get set by set_vault_environment()
# Default to legacy single vault for backwards compatibility
VAULT_FILE="${_VAULT_BASE_DIR}/vault.yml"
VAULT_EXAMPLE="${_VAULT_BASE_DIR}/vault.example.yml"
VAULT_PASS_FILE=""

# =============================================================================
# Environment-Specific Vault Functions
# =============================================================================

# Set the vault environment - MUST be called before accessing vault
# Usage: set_vault_environment "prod"
# Sets up VAULT_FILE and VAULT_PASS_FILE for the given environment
set_vault_environment() {
    local env_prefix="$1"
    
    if [[ -z "$env_prefix" ]]; then
        _vault_error "Environment prefix required (dev, staging, prod, demo)"
        return 1
    fi
    
    VAULT_ENVIRONMENT="$env_prefix"
    
    # Environment-specific vault file
    local env_vault="${_VAULT_BASE_DIR}/vault.${env_prefix}.yml"
    local env_pass_file="$HOME/.busibox-vault-pass-${env_prefix}"
    
    # Check if environment-specific vault exists
    if [[ -f "$env_vault" ]]; then
        VAULT_FILE="$env_vault"
        VAULT_PASS_FILE="$env_pass_file"
        VAULT_EXAMPLE="${_VAULT_BASE_DIR}/vault.example.yml"
        _vault_info "Using environment vault: vault.${env_prefix}.yml"
        return 0
    fi
    
    # Fallback to legacy single vault
    local legacy_vault="${_VAULT_BASE_DIR}/vault.yml"
    if [[ -f "$legacy_vault" ]]; then
        VAULT_FILE="$legacy_vault"
        # Try env-specific pass file first, then legacy
        if [[ -f "$env_pass_file" ]]; then
            VAULT_PASS_FILE="$env_pass_file"
        elif [[ -f "$HOME/.vault_pass" ]]; then
            VAULT_PASS_FILE="$HOME/.vault_pass"
        else
            VAULT_PASS_FILE="$env_pass_file"  # Will be created
        fi
        _vault_warn "Environment vault not found, using legacy vault.yml"
        _vault_warn "Consider creating: vault.${env_prefix}.yml"
        return 0
    fi
    
    # Neither exists - will need to be created
    VAULT_FILE="$env_vault"
    VAULT_PASS_FILE="$env_pass_file"
    VAULT_EXAMPLE="${_VAULT_BASE_DIR}/vault.example.yml"
    return 0
}

# Get the vault file path for an environment (without setting it)
# Usage: get_vault_file_for_env "prod"
get_vault_file_for_env() {
    local env_prefix="$1"
    local env_vault="${_VAULT_BASE_DIR}/vault.${env_prefix}.yml"
    
    if [[ -f "$env_vault" ]]; then
        echo "$env_vault"
    elif [[ -f "${_VAULT_BASE_DIR}/vault.yml" ]]; then
        echo "${_VAULT_BASE_DIR}/vault.yml"
    else
        echo "$env_vault"  # Return expected path even if doesn't exist
    fi
}

# Get the vault password file path for an environment
# Usage: get_vault_pass_file_for_env "prod"
get_vault_pass_file_for_env() {
    local env_prefix="$1"
    local env_pass="$HOME/.busibox-vault-pass-${env_prefix}"
    
    if [[ -f "$env_pass" ]]; then
        echo "$env_pass"
    elif [[ -f "$HOME/.vault_pass" ]]; then
        echo "$HOME/.vault_pass"
    else
        echo "$env_pass"  # Return expected path
    fi
}

# Verify vault can be decrypted with the given password file
# Usage: verify_vault_decryption [vault_file] [pass_file]
# Returns 0 if successful, 1 if failed
verify_vault_decryption() {
    local vault_file="${1:-$VAULT_FILE}"
    local pass_file="${2:-$VAULT_PASS_FILE}"
    
    if [[ ! -f "$vault_file" ]]; then
        _vault_error "Vault file not found: $vault_file"
        return 1
    fi
    
    if [[ ! -f "$pass_file" ]]; then
        _vault_error "Vault password file not found: $pass_file"
        _vault_error "Expected password file for this environment: $pass_file"
        return 1
    fi
    
    # Test decryption
    if ! ansible-vault view "$vault_file" --vault-password-file="$pass_file" &>/dev/null; then
        _vault_error "Failed to decrypt vault!"
        _vault_error "  Vault file: $vault_file"
        _vault_error "  Password file: $pass_file"
        _vault_error ""
        _vault_error "This usually means:"
        _vault_error "  1. The password file contains the wrong password"
        _vault_error "  2. The vault was encrypted with a different password"
        _vault_error ""
        _vault_error "To fix:"
        _vault_error "  - Ensure $pass_file contains the correct password"
        _vault_error "  - Or re-encrypt the vault with: ansible-vault rekey $vault_file"
        return 1
    fi
    
    return 0
}

# Colors (use existing if available, or define minimal set)
_V_RED="${RED:-\033[0;31m}"
_V_GREEN="${GREEN:-\033[0;32m}"
_V_YELLOW="${YELLOW:-\033[1;33m}"
_V_BLUE="${BLUE:-\033[0;34m}"
_V_NC="${NC:-\033[0m}"

# Logging functions (use existing if available)
_vault_info() {
    if type info &>/dev/null; then
        info "$1"
    else
        echo -e "${_V_BLUE}[INFO]${_V_NC} $1"
    fi
}

_vault_warn() {
    if type warn &>/dev/null; then
        warn "$1"
    else
        echo -e "${_V_YELLOW}[WARNING]${_V_NC} $1"
    fi
}

_vault_error() {
    if type error &>/dev/null; then
        error "$1"
    else
        echo -e "${_V_RED}[ERROR]${_V_NC} $1"
    fi
}

_vault_success() {
    if type success &>/dev/null; then
        success "$1"
    else
        echo -e "${_V_GREEN}[SUCCESS]${_V_NC} $1"
    fi
}

# Auto-install yq if not present (Linux only)
ensure_yq_installed() {
    # Check if yq is already available
    if command -v yq &>/dev/null; then
        return 0
    fi
    
    _vault_info "yq not found, attempting to install..."
    
    # Detect OS
    local os_type=$(uname -s)
    
    if [[ "$os_type" == "Linux" ]]; then
        # Linux - use wget/curl to install from GitHub releases
        local yq_version="v4.35.2"
        local yq_binary="yq_linux_amd64"
        local install_dir="/usr/local/bin"
        
        # Check if we have root access
        if [[ $EUID -ne 0 ]]; then
            _vault_error "Root access required to install yq to /usr/local/bin"
            _vault_error "Please run: sudo wget https://github.com/mikefarah/yq/releases/download/${yq_version}/${yq_binary} -O /usr/local/bin/yq && sudo chmod +x /usr/local/bin/yq"
            return 1
        fi
        
        # Try to download and install
        if command -v wget &>/dev/null; then
            if wget -q "https://github.com/mikefarah/yq/releases/download/${yq_version}/${yq_binary}" -O "${install_dir}/yq" 2>/dev/null; then
                chmod +x "${install_dir}/yq"
                _vault_success "yq installed successfully to ${install_dir}/yq"
                return 0
            fi
        elif command -v curl &>/dev/null; then
            if curl -sL "https://github.com/mikefarah/yq/releases/download/${yq_version}/${yq_binary}" -o "${install_dir}/yq" 2>/dev/null; then
                chmod +x "${install_dir}/yq"
                _vault_success "yq installed successfully to ${install_dir}/yq"
                return 0
            fi
        else
            _vault_error "Neither wget nor curl found. Cannot auto-install yq."
            _vault_error "Please install manually: wget https://github.com/mikefarah/yq/releases/download/${yq_version}/${yq_binary} -O /usr/local/bin/yq && chmod +x /usr/local/bin/yq"
            return 1
        fi
        
        _vault_error "Failed to download yq from GitHub"
        _vault_error "Please install manually: wget https://github.com/mikefarah/yq/releases/download/${yq_version}/${yq_binary} -O /usr/local/bin/yq && chmod +x /usr/local/bin/yq"
        return 1
        
    elif [[ "$os_type" == "Darwin" ]]; then
        # macOS - suggest homebrew
        _vault_error "yq is required for writing vault secrets."
        _vault_error "On macOS, install with: brew install yq"
        return 1
    else
        _vault_error "yq is required for writing vault secrets."
        _vault_error "Please install from: https://github.com/mikefarah/yq/releases"
        return 1
    fi
}

# Check if ansible-vault is available
check_ansible_vault() {
    if ! command -v ansible-vault &>/dev/null; then
        _vault_error "ansible-vault not found. Please install Ansible:"
        echo "  pip install ansible"
        return 1
    fi
    return 0
}

# Check if vault file exists
check_vault_file() {
    if [[ ! -f "$VAULT_FILE" ]]; then
        return 1
    fi
    return 0
}

# Check if vault file is encrypted
is_vault_encrypted() {
    if [[ ! -f "$VAULT_FILE" ]]; then
        return 1
    fi
    
    if head -1 "$VAULT_FILE" | grep -q '^\$ANSIBLE_VAULT'; then
        return 0
    fi
    return 1
}

# Create vault from example if it doesn't exist
create_vault_from_example() {
    if [[ ! -f "$VAULT_EXAMPLE" ]]; then
        _vault_error "Vault example file not found: $VAULT_EXAMPLE"
        return 1
    fi
    
    _vault_info "Creating vault file from example..."
    cp "$VAULT_EXAMPLE" "$VAULT_FILE"
    
    _vault_warn "Vault file created but NOT encrypted."
    echo "  Please edit with your secrets and encrypt:"
    echo "    1. Edit: $VAULT_FILE"
    echo "    2. Encrypt: ansible-vault encrypt $VAULT_FILE"
    return 0
}

# Encrypt vault file
encrypt_vault() {
    if [[ ! -f "$VAULT_FILE" ]]; then
        _vault_error "Vault file not found"
        return 1
    fi
    
    if is_vault_encrypted; then
        _vault_info "Vault file is already encrypted"
        return 0
    fi
    
    _vault_info "Encrypting vault file..."
    if ansible-vault encrypt "$VAULT_FILE"; then
        _vault_success "Vault file encrypted"
        return 0
    else
        _vault_error "Failed to encrypt vault file"
        return 1
    fi
}

# Ensure vault password is accessible
# Sets ANSIBLE_VAULT_PASSWORD_FILE environment variable
# 
# IMPORTANT: Call set_vault_environment() first to set the correct vault context!
ensure_vault_access() {
    # Determine which password file to use
    local vault_pass_file="${VAULT_PASS_FILE:-$HOME/.vault_pass}"
    
    # Check ansible-vault is available
    if ! check_ansible_vault; then
        return 1
    fi
    
    # Check if vault file exists
    if ! check_vault_file; then
        _vault_warn "Vault file not found: $VAULT_FILE"
        
        # Offer to create from example
        if [[ -f "$VAULT_EXAMPLE" ]]; then
            echo ""
            read -p "Create vault from example? (y/N) " create_vault
            if [[ "$create_vault" =~ ^[Yy]$ ]]; then
                create_vault_from_example || return 1
            else
                _vault_error "Vault file required. Create it manually:"
                echo "  cp $VAULT_EXAMPLE $VAULT_FILE"
                echo "  # Edit with your secrets"
                echo "  ansible-vault encrypt $VAULT_FILE"
                return 1
            fi
        else
            _vault_error "Neither vault file nor example found"
            return 1
        fi
    fi
    
    # Check if vault is encrypted
    if ! is_vault_encrypted; then
        _vault_warn "Vault file is not encrypted!"
        echo ""
        read -p "Encrypt vault file now? (Y/n) " encrypt_now
        if [[ ! "$encrypt_now" =~ ^[Nn]$ ]]; then
            encrypt_vault || return 1
        else
            _vault_warn "Continuing with unencrypted vault (not recommended)"
            # No password needed for unencrypted file
            return 0
        fi
    fi
    
    # Use existing password file if available
    if [[ -f "$vault_pass_file" ]]; then
        _vault_info "Using vault password from $vault_pass_file"
        export ANSIBLE_VAULT_PASSWORD_FILE="$vault_pass_file"
        
        # Verify password works - this is CRITICAL
        if ! ansible-vault view "$VAULT_FILE" --vault-password-file="$vault_pass_file" &>/dev/null; then
            _vault_error ""
            _vault_error "╔══════════════════════════════════════════════════════════════════════════════╗"
            _vault_error "║                      VAULT DECRYPTION FAILED                                ║"
            _vault_error "╚══════════════════════════════════════════════════════════════════════════════╝"
            _vault_error ""
            _vault_error "  Vault file:    $VAULT_FILE"
            _vault_error "  Password file: $vault_pass_file"
            _vault_error "  Environment:   ${VAULT_ENVIRONMENT:-unset}"
            _vault_error ""
            _vault_error "  The password in $vault_pass_file cannot decrypt this vault."
            _vault_error ""
            _vault_error "  This usually means:"
            _vault_error "    • The vault was encrypted with a different password"
            _vault_error "    • You're using the wrong environment's password file"
            _vault_error ""
            _vault_error "  To fix:"
            _vault_error "    1. Update the password file with the correct password:"
            _vault_error "       echo 'your-vault-password' > $vault_pass_file"
            _vault_error "       chmod 600 $vault_pass_file"
            _vault_error ""
            _vault_error "    2. Or re-encrypt the vault with a new password:"
            _vault_error "       ansible-vault rekey $VAULT_FILE"
            _vault_error ""
            # Don't silently fall through - this is a critical error
            return 1
        else
            return 0
        fi
    fi
    
    # Password file doesn't exist - prompt for password
    _vault_info "No password file found at: $vault_pass_file"
    
    local max_attempts=3
    local attempt=1
    
    while [[ $attempt -le $max_attempts ]]; do
        echo -n "Enter Ansible vault password: "
        read -s vault_pass
        echo ""
        
        if [[ -z "$vault_pass" ]]; then
            _vault_error "Password cannot be empty"
            ((attempt++))
            continue
        fi
        
        # Create temporary password file
        local tmp_pass=$(mktemp)
        echo "$vault_pass" > "$tmp_pass"
        chmod 600 "$tmp_pass"
        export ANSIBLE_VAULT_PASSWORD_FILE="$tmp_pass"
        
        # Verify password works
        if ansible-vault view "$VAULT_FILE" --vault-password-file="$tmp_pass" &>/dev/null; then
            # Password is correct - offer to save it
            echo ""
            local save_target="$vault_pass_file"
            read -p "Save password to $save_target for future use? (y/N) " save_pass
            if [[ "$save_pass" =~ ^[Yy]$ ]]; then
                echo "$vault_pass" > "$save_target"
                chmod 600 "$save_target"
                _vault_info "Password saved to $save_target"
                export ANSIBLE_VAULT_PASSWORD_FILE="$save_target"
                rm -f "$tmp_pass"
            else
                # Clean up temp file on exit
                trap "rm -f $tmp_pass" EXIT
            fi
            return 0
        else
            _vault_error "Incorrect vault password (attempt $attempt/$max_attempts)"
            rm -f "$tmp_pass"
            ((attempt++))
        fi
    done
    
    _vault_error "Too many incorrect password attempts"
    return 1
}

# Read a secret from the vault
# Usage: get_vault_secret "secrets.postgresql.password"
# Returns: The secret value, or empty string on error
get_vault_secret() {
    local key_path="$1"
    
    if [[ -z "$key_path" ]]; then
        _vault_error "Key path required"
        return 1
    fi
    
    # Ensure we have vault access
    if [[ -z "${ANSIBLE_VAULT_PASSWORD_FILE:-}" ]]; then
        if ! is_vault_encrypted; then
            # Unencrypted - read directly
            :
        else
            _vault_error "Vault access not initialized."
            _vault_error "Call set_vault_environment() and ensure_vault_access() first."
            return 1
        fi
    fi
    
    # Verify password file still exists
    if [[ -n "${ANSIBLE_VAULT_PASSWORD_FILE:-}" ]] && [[ ! -f "$ANSIBLE_VAULT_PASSWORD_FILE" ]]; then
        _vault_error "Vault password file not found: $ANSIBLE_VAULT_PASSWORD_FILE"
        return 1
    fi
    
    # Convert dot notation to yq path (e.g., secrets.postgresql.password -> .secrets.postgresql.password)
    local yq_path=".$key_path"
    
    # Check if yq is available
    if command -v yq &>/dev/null; then
        if is_vault_encrypted; then
            ansible-vault view "$VAULT_FILE" 2>/dev/null | yq -r "$yq_path" 2>/dev/null
        else
            yq -r "$yq_path" "$VAULT_FILE" 2>/dev/null
        fi
    else
        # Fallback: use grep/sed for simple key extraction
        # This only works for simple keys, not nested structures
        local simple_key="${key_path##*.}"
        if is_vault_encrypted; then
            ansible-vault view "$VAULT_FILE" 2>/dev/null | grep -E "^[[:space:]]*${simple_key}:" | head -1 | sed 's/.*:[[:space:]]*//' | tr -d '"'"'"
        else
            grep -E "^[[:space:]]*${simple_key}:" "$VAULT_FILE" | head -1 | sed 's/.*:[[:space:]]*//' | tr -d '"'"'"
        fi
    fi
}

# Check if a secret exists and has a non-placeholder value
# Usage: has_vault_secret "secrets.postgresql.password"
# Returns: 0 if secret exists and is not a placeholder, 1 otherwise
has_vault_secret() {
    local key_path="$1"
    local value
    
    value=$(get_vault_secret "$key_path")
    
    if [[ -z "$value" ]]; then
        return 1
    fi
    
    # Check for placeholder values
    if [[ "$value" == "CHANGE_ME"* ]] || \
       [[ "$value" == "your-"* ]] || \
       [[ "$value" == "TODO"* ]] || \
       [[ "$value" == "null" ]] || \
       [[ "$value" == "~" ]]; then
        return 1
    fi
    
    return 0
}

# Validate that required bootstrap secrets exist
# These are secrets that MUST be in the vault before installation/update
validate_vault_secrets() {
    local required_secrets=(
        "secrets.postgresql.password"
        "secrets.minio.root_user"
        "secrets.minio.root_password"
        "secrets.jwt_secret"
    )
    
    local optional_secrets=(
        "secrets.authz_master_key"
        "secrets.litellm_api_key"
        "secrets.litellm_master_key"
        "secrets.encryption_key"
    )
    
    # Ensure vault access first
    if ! ensure_vault_access; then
        return 1
    fi
    
    local missing=()
    local unconfigured=()
    
    echo ""
    _vault_info "Validating required vault secrets..."
    
    for key in "${required_secrets[@]}"; do
        local value=$(get_vault_secret "$key")
        local short_key="${key##*.}"
        
        if [[ -z "$value" ]]; then
            missing+=("$key")
            echo -e "  ${_V_RED}✗${_V_NC} $short_key - missing"
        elif ! has_vault_secret "$key"; then
            unconfigured+=("$key")
            echo -e "  ${_V_YELLOW}○${_V_NC} $short_key - placeholder value"
        else
            echo -e "  ${_V_GREEN}✓${_V_NC} $short_key - configured"
        fi
    done
    
    echo ""
    _vault_info "Checking optional secrets..."
    
    for key in "${optional_secrets[@]}"; do
        local value=$(get_vault_secret "$key")
        local short_key="${key##*.}"
        
        if [[ -z "$value" ]] || ! has_vault_secret "$key"; then
            echo -e "  ${_V_YELLOW}○${_V_NC} $short_key - not configured (optional)"
        else
            echo -e "  ${_V_GREEN}✓${_V_NC} $short_key - configured"
        fi
    done
    
    echo ""
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        _vault_error "Missing required secrets:"
        for key in "${missing[@]}"; do
            echo "  - $key"
        done
        echo ""
        echo "Add these to your vault file: $VAULT_FILE"
        return 1
    fi
    
    if [[ ${#unconfigured[@]} -gt 0 ]]; then
        _vault_error "Secrets with placeholder values:"
        for key in "${unconfigured[@]}"; do
            echo "  - $key"
        done
        echo ""
        echo "Update these in your vault file: $VAULT_FILE"
        echo "Then re-encrypt: ansible-vault encrypt $VAULT_FILE"
        return 1
    fi
    
    _vault_success "All required vault secrets validated"
    return 0
}

# Generate a random secret (for initial setup)
generate_secret() {
    local length="${1:-32}"
    openssl rand -base64 "$length" | tr -d '/+=' | head -c "$length"
}

# Write a secret to the vault
# Usage: write_vault_secret "secrets.postgresql.password" "my-password"
# Note: Requires yq to be installed
write_vault_secret() {
    local key_path="$1"
    local value="$2"
    local vault_pass_file="${ANSIBLE_VAULT_PASSWORD_FILE:-}"
    
    if [[ -z "$key_path" ]] || [[ -z "$value" ]]; then
        _vault_error "Key path and value required"
        return 1
    fi
    
    # Check if yq is available (required for writing)
    if ! ensure_yq_installed; then
        return 1
    fi
    
    local was_encrypted=false
    local tmp_file=""
    
    # Check if vault is encrypted
    if is_vault_encrypted; then
        was_encrypted=true
        
        if [[ -z "$vault_pass_file" ]]; then
            _vault_error "Vault is encrypted but no password file set. Call ensure_vault_access first."
            return 1
        fi
        
        # Decrypt to temp file
        tmp_file=$(mktemp)
        if ! ansible-vault decrypt --vault-password-file="$vault_pass_file" --output="$tmp_file" "$VAULT_FILE" 2>/dev/null; then
            _vault_error "Failed to decrypt vault"
            rm -f "$tmp_file"
            return 1
        fi
    else
        tmp_file="$VAULT_FILE"
    fi
    
    # Convert dot notation to yq path (e.g., secrets.postgresql.password -> .secrets.postgresql.password)
    local yq_path=".$key_path"
    
    # Update the value using yq
    if ! yq -i "$yq_path = \"$value\"" "$tmp_file" 2>/dev/null; then
        _vault_error "Failed to update vault secret: $key_path"
        [[ "$was_encrypted" == "true" ]] && rm -f "$tmp_file"
        return 1
    fi
    
    # Re-encrypt if it was encrypted
    if [[ "$was_encrypted" == "true" ]]; then
        # Copy back and encrypt in place to avoid vault-id conflicts
        cp "$tmp_file" "$VAULT_FILE"
        rm -f "$tmp_file"
        if ! ansible-vault encrypt --vault-password-file="$vault_pass_file" --encrypt-vault-id default "$VAULT_FILE" 2>/dev/null; then
            _vault_error "Failed to re-encrypt vault"
            return 1
        fi
    fi
    
    return 0
}

# Update multiple vault secrets at once
# Usage: update_vault_secrets "secrets.postgresql.password=pass1" "secrets.minio.root_user=admin"
update_vault_secrets() {
    local vault_pass_file="${ANSIBLE_VAULT_PASSWORD_FILE:-}"
    local was_encrypted=false
    local tmp_file=""
    
    if [[ $# -eq 0 ]]; then
        _vault_error "At least one key=value pair required"
        return 1
    fi
    
    # Check if yq is available (required for writing)
    if ! ensure_yq_installed; then
        return 1
    fi
    
    # Check if vault is encrypted
    if is_vault_encrypted; then
        was_encrypted=true
        
        if [[ -z "$vault_pass_file" ]]; then
            _vault_error "Vault is encrypted but no password file set. Call ensure_vault_access first."
            return 1
        fi
        
        # Decrypt to temp file
        tmp_file=$(mktemp)
        if ! ansible-vault decrypt --vault-password-file="$vault_pass_file" --output="$tmp_file" "$VAULT_FILE" 2>/dev/null; then
            _vault_error "Failed to decrypt vault"
            rm -f "$tmp_file"
            return 1
        fi
    else
        tmp_file="$VAULT_FILE"
    fi
    
    # Process each key=value pair
    for pair in "$@"; do
        local key_path="${pair%%=*}"
        local value="${pair#*=}"
        local yq_path=".$key_path"
        
        if ! yq -i "$yq_path = \"$value\"" "$tmp_file" 2>/dev/null; then
            _vault_error "Failed to update vault secret: $key_path"
            [[ "$was_encrypted" == "true" ]] && rm -f "$tmp_file"
            return 1
        fi
    done
    
    # Re-encrypt if it was encrypted, OR encrypt if password file is available
    if [[ "$was_encrypted" == "true" ]]; then
        # Copy back and encrypt in place to avoid vault-id conflicts
        cp "$tmp_file" "$VAULT_FILE"
        rm -f "$tmp_file"
        if ! ansible-vault encrypt --vault-password-file="$vault_pass_file" --encrypt-vault-id default "$VAULT_FILE" 2>/dev/null; then
            _vault_error "Failed to re-encrypt vault"
            return 1
        fi
    elif [[ -n "$vault_pass_file" ]] && [[ -f "$vault_pass_file" ]]; then
        # Vault wasn't encrypted but we have a password file - encrypt it now
        if ! ansible-vault encrypt --vault-password-file="$vault_pass_file" --encrypt-vault-id default "$VAULT_FILE" 2>/dev/null; then
            _vault_error "Failed to encrypt vault"
            return 1
        fi
    fi
    
    return 0
}

# Sync secrets and protected config from environment variables to vault
# This is called by install.sh after generating secrets
# Usage: sync_secrets_to_vault
#
# The vault contains:
# 1. Secrets (passwords, API keys, tokens) - for security
# 2. Protected config (admin_email, allowed_domains) - for integrity/anti-tampering
sync_secrets_to_vault() {
    _vault_info "Syncing secrets and protected config to vault..."
    
    # Build list of values to update
    local values_to_update=()
    
    # ==========================================================================
    # SECRETS (security-sensitive)
    # ==========================================================================
    
    # PostgreSQL
    if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
        values_to_update+=("secrets.postgresql.password=${POSTGRES_PASSWORD}")
    fi
    
    # MinIO
    if [[ -n "${MINIO_ACCESS_KEY:-}" ]]; then
        values_to_update+=("secrets.minio.root_user=${MINIO_ACCESS_KEY}")
    fi
    if [[ -n "${MINIO_SECRET_KEY:-}" ]]; then
        values_to_update+=("secrets.minio.root_password=${MINIO_SECRET_KEY}")
    fi
    
    # Auth secrets
    if [[ -n "${SSO_JWT_SECRET:-}" ]]; then
        values_to_update+=("secrets.jwt_secret=${SSO_JWT_SECRET}")
        values_to_update+=("secrets.session_secret=${SSO_JWT_SECRET}")
    fi
    
    # AuthZ
    if [[ -n "${AUTHZ_MASTER_KEY:-}" ]]; then
        values_to_update+=("secrets.authz_master_key=${AUTHZ_MASTER_KEY}")
    fi
    
    # LiteLLM
    if [[ -n "${LITELLM_API_KEY:-}" ]]; then
        values_to_update+=("secrets.litellm_api_key=${LITELLM_API_KEY}")
    fi
    
    # GitHub
    if [[ -n "${GITHUB_AUTH_TOKEN:-}" ]]; then
        values_to_update+=("secrets.github.personal_access_token=${GITHUB_AUTH_TOKEN}")
    fi
    
    # Cloud LLM credentials
    if [[ -n "${OPENAI_API_KEY:-}" ]]; then
        values_to_update+=("secrets.openai_api_key=${OPENAI_API_KEY}")
    fi
    if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
        values_to_update+=("secrets.anthropic_api_key=${ANTHROPIC_API_KEY}")
    fi
    if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
        values_to_update+=("secrets.openrouter_api_key=${OPENROUTER_API_KEY}")
    fi
    if [[ -n "${AWS_ACCESS_KEY_ID:-}" ]]; then
        values_to_update+=("secrets.aws.access_key_id=${AWS_ACCESS_KEY_ID}")
    fi
    if [[ -n "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
        values_to_update+=("secrets.aws.secret_access_key=${AWS_SECRET_ACCESS_KEY}")
    fi
    
    # ==========================================================================
    # PROTECTED CONFIG (integrity-sensitive, anti-tampering)
    # These are under secrets.* to match vault.example.yml structure
    # ==========================================================================
    
    # Admin configuration - stored in vault to prevent unauthorized changes
    if [[ -n "${ADMIN_EMAIL:-}" ]]; then
        values_to_update+=("secrets.admin_email=${ADMIN_EMAIL}")
    fi
    if [[ -n "${ALLOWED_DOMAINS:-}" ]]; then
        values_to_update+=("secrets.allowed_email_domains=${ALLOWED_DOMAINS}")
    fi
    
    # ==========================================================================
    # APPLICATION SECRETS
    # These are application-specific secrets required by apps in apps.yml
    # Format: secrets.{app-name}.{secret_key} (use hyphens, not underscores!)
    # ==========================================================================
    
    # AI Portal secrets (app name: ai-portal)
    if [[ -n "${POSTGRES_PASSWORD:-}" ]] && [[ -n "${POSTGRES_USER:-}" ]] && [[ -n "${POSTGRES_DB:-}" ]] && [[ -n "${POSTGRES_HOST:-}" ]] && [[ -n "${POSTGRES_PORT:-}" ]]; then
        values_to_update+=("secrets.ai-portal.database_url=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}")
    fi
    if [[ -n "${SSO_JWT_SECRET:-}" ]]; then
        values_to_update+=("secrets.ai-portal.sso_jwt_secret=${SSO_JWT_SECRET}")
    fi
    if [[ -n "${LITELLM_API_KEY:-}" ]]; then
        values_to_update+=("secrets.ai-portal.litellm_api_key=${LITELLM_API_KEY}")
    fi
    if [[ -n "${OPENAI_API_KEY:-}" ]]; then
        values_to_update+=("secrets.ai-portal.openai_api_key=${OPENAI_API_KEY}")
    fi
    if [[ -n "${ALLOWED_DOMAINS:-}" ]]; then
        values_to_update+=("secrets.ai-portal.allowed_email_domains=${ALLOWED_DOMAINS}")
    fi
    # Email/SMTP secrets - use defaults if not set
    values_to_update+=("secrets.ai-portal.email_from=${EMAIL_FROM:-noreply@busibox.local}")
    values_to_update+=("secrets.ai-portal.smtp_host=${SMTP_HOST:-localhost}")
    values_to_update+=("secrets.ai-portal.smtp_port=${SMTP_PORT:-25}")
    values_to_update+=("secrets.ai-portal.smtp_user=${SMTP_USER:-}")
    values_to_update+=("secrets.ai-portal.smtp_password=${SMTP_PASSWORD:-}")
    values_to_update+=("secrets.ai-portal.smtp_secure=${SMTP_SECURE:-false}")
    # GitHub OAuth secrets - use placeholders if not set
    values_to_update+=("secrets.ai-portal.github_client_id=${GITHUB_CLIENT_ID:-CHANGE_ME}")
    values_to_update+=("secrets.ai-portal.github_client_secret=${GITHUB_CLIENT_SECRET:-CHANGE_ME}")
    values_to_update+=("secrets.ai-portal.github_redirect_uri=${GITHUB_REDIRECT_URI:-https://localhost/portal/api/admin/github/callback}")
    # Encryption key - generate if not set
    if [[ -n "${ENCRYPTION_KEY:-}" ]]; then
        values_to_update+=("secrets.ai-portal.encryption_key=${ENCRYPTION_KEY}")
    elif [[ -n "${SSO_JWT_SECRET:-}" ]]; then
        values_to_update+=("secrets.ai-portal.encryption_key=${SSO_JWT_SECRET}")
    fi
    # Container IPs - use variables that will be resolved by Ansible
    values_to_update+=("secrets.ai-portal.current_container_ip={{ core_apps_ip }}")
    values_to_update+=("secrets.ai-portal.apps_container_ip={{ core_apps_ip }}")
    values_to_update+=("secrets.ai-portal.agent_container_ip={{ agent_ip }}")
    values_to_update+=("secrets.ai-portal.postgres_container_ip={{ postgres_ip }}")
    
    # Agent Manager secrets (app name: agent-manager)
    if [[ -n "${POSTGRES_PASSWORD:-}" ]] && [[ -n "${POSTGRES_USER:-}" ]] && [[ -n "${POSTGRES_DB:-}" ]] && [[ -n "${POSTGRES_HOST:-}" ]] && [[ -n "${POSTGRES_PORT:-}" ]]; then
        values_to_update+=("secrets.agent-manager.database_url=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}")
    fi
    values_to_update+=("secrets.agent-manager.agent_api_key=${LITELLM_API_KEY:-sk-agent-api-key}")
    if [[ -n "${SSO_JWT_SECRET:-}" ]]; then
        values_to_update+=("secrets.agent-manager.jwt_secret=${SSO_JWT_SECRET}")
        values_to_update+=("secrets.agent-manager.session_secret=${SSO_JWT_SECRET}")
        values_to_update+=("secrets.agent-manager.sso_jwt_secret=${SSO_JWT_SECRET}")
    fi
    if [[ -n "${LITELLM_API_KEY:-}" ]]; then
        values_to_update+=("secrets.agent-manager.litellm_api_key=${LITELLM_API_KEY}")
    fi
    
    if [[ ${#values_to_update[@]} -eq 0 ]]; then
        _vault_warn "No values to sync"
        return 0
    fi
    
    # Update all values at once
    if update_vault_secrets "${values_to_update[@]}"; then
        _vault_success "Synced ${#values_to_update[@]} values to vault"
        return 0
    else
        _vault_error "Failed to sync values to vault"
        return 1
    fi
}

# Generate all required secrets and update vault (interactive)
setup_vault_secrets() {
    if ! ensure_vault_access; then
        return 1
    fi
    
    _vault_info "Setting up vault secrets..."
    echo ""
    echo "This will generate random values for unconfigured secrets."
    echo "You can edit the vault file later to customize."
    echo ""
    read -p "Continue? (y/N) " continue_setup
    
    if [[ ! "$continue_setup" =~ ^[Yy]$ ]]; then
        return 1
    fi
    
    # Generate secrets
    local new_secrets=()
    
    if ! has_vault_secret "secrets.postgresql.password"; then
        new_secrets+=("secrets.postgresql.password=$(generate_secret 24)")
    fi
    if ! has_vault_secret "secrets.minio.root_user"; then
        new_secrets+=("secrets.minio.root_user=minioadmin")
    fi
    if ! has_vault_secret "secrets.minio.root_password"; then
        new_secrets+=("secrets.minio.root_password=$(generate_secret 24)")
    fi
    if ! has_vault_secret "secrets.jwt_secret"; then
        local jwt=$(generate_secret 32)
        new_secrets+=("secrets.jwt_secret=$jwt")
        new_secrets+=("secrets.session_secret=$jwt")
    fi
    if ! has_vault_secret "secrets.authz_master_key"; then
        new_secrets+=("secrets.authz_master_key=$(openssl rand -base64 32)")
    fi
    if ! has_vault_secret "secrets.litellm_api_key"; then
        new_secrets+=("secrets.litellm_api_key=sk-$(generate_secret 16)")
    fi
    
    if [[ ${#new_secrets[@]} -eq 0 ]]; then
        _vault_success "All secrets already configured"
        return 0
    fi
    
    _vault_info "Generating ${#new_secrets[@]} secrets..."
    
    if update_vault_secrets "${new_secrets[@]}"; then
        _vault_success "Vault secrets configured"
        return 0
    else
        _vault_error "Failed to setup vault secrets"
        return 1
    fi
}
