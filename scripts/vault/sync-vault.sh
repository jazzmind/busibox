#!/usr/bin/env bash
#
# Vault Sync Script
#
# EXECUTION CONTEXT: Admin workstation
# PURPOSE: Sync current vault with vault.example.yml structure
#
# This script:
# 1. Decrypts the current vault
# 2. Reads the vault.example.yml structure
# 3. Maps secrets from current vault to new structure
# 4. Identifies secrets that don't map (removed)
# 5. Identifies secrets that are missing (need to add)
# 6. Creates a new vault with example structure + current values
# 7. Saves unmapped secrets to vault.removed (encrypted)
# 8. Reports what needs to be added manually
#
# USAGE:
#   bash scripts/vault/sync-vault.sh
#   OR
#   make configure → Sync Vault with Example
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"

# ============================================================================
# Configuration
# ============================================================================

ANSIBLE_DIR="${REPO_ROOT}/provision/ansible"
VAULT_FILE="${ANSIBLE_DIR}/roles/secrets/vars/vault.yml"
EXAMPLE_FILE="${ANSIBLE_DIR}/roles/secrets/vars/vault.example.yml"
VAULT_NEW="${ANSIBLE_DIR}/roles/secrets/vars/vault.new.yml"
VAULT_REMOVED="${ANSIBLE_DIR}/roles/secrets/vars/vault.removed.yml"
VAULT_BACKUP_DIR="${ANSIBLE_DIR}/roles/secrets/vars/backups"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
VAULT_BACKUP="${VAULT_BACKUP_DIR}/vault.backup.${TIMESTAMP}.yml"
VAULT_REMOVED_BACKUP="${VAULT_BACKUP_DIR}/vault.removed.${TIMESTAMP}.yml"
TEMP_DIR=$(mktemp -d)

cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

# ============================================================================
# Functions
# ============================================================================

get_vault_pass_args() {
    local vault_pass_file="$HOME/.vault_pass"
    if [ -f "$vault_pass_file" ]; then
        echo "--vault-password-file $vault_pass_file"
    else
        echo "--ask-vault-pass"
    fi
}

# Check if files exist
check_files() {
    if [[ ! -f "$VAULT_FILE" ]]; then
        error "Vault file not found: $VAULT_FILE"
        return 1
    fi
    
    if [[ ! -f "$EXAMPLE_FILE" ]]; then
        error "Example file not found: $EXAMPLE_FILE"
        return 1
    fi
    
    # Check if vault is encrypted
    if ! head -1 "$VAULT_FILE" | grep -q '^\$ANSIBLE_VAULT'; then
        error "Vault file is not encrypted"
        return 1
    fi
    
    return 0
}

# Decrypt current vault
decrypt_vault() {
    local vault_pass_args=$(get_vault_pass_args)
    
    info "Decrypting current vault..."
    cd "$ANSIBLE_DIR"
    
    if ! ansible-vault view "$VAULT_FILE" $vault_pass_args > "$TEMP_DIR/current.yml" 2>/dev/null; then
        error "Failed to decrypt vault (check password)"
        cd "$REPO_ROOT"
        return 1
    fi
    
    cd "$REPO_ROOT"
    success "Vault decrypted"
    return 0
}

# Parse YAML and extract secret paths
# This extracts nested keys like: secrets.postgresql.password
parse_yaml_structure() {
    local file="$1"
    python3 -c "
import yaml
import sys

def flatten_dict(d, parent_key='', sep='.'):
    items = []
    for k, v in d.items():
        new_key = f'{parent_key}{sep}{k}' if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)

try:
    with open('$file', 'r') as f:
        data = yaml.safe_load(f)
        if data:
            flat = flatten_dict(data)
            for key in flat.keys():
                print(key)
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    sys.exit(1)
"
}

# Get value from YAML file by path
get_yaml_value() {
    local file="$1"
    local path="$2"
    
    python3 -c "
import yaml
import sys

def get_nested(d, path):
    keys = path.split('.')
    value = d
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return None
    return value

try:
    with open('$file', 'r') as f:
        data = yaml.safe_load(f)
        value = get_nested(data, '$path')
        if value is not None:
            print(str(value))
        else:
            sys.exit(1)
except Exception as e:
    sys.exit(1)
"
}

# Create new vault with example structure + current values
create_synced_vault() {
    info "Creating synced vault (preserving comments)..."
    
    # First, use comment-preserving script to merge values while keeping structure
    python3 "${REPO_ROOT}/scripts/vault/preserve_comments.py" \
        "$TEMP_DIR/current.yml" \
        "$EXAMPLE_FILE" \
        > "$TEMP_DIR/new.yml"
    
    if [[ ! -s "$TEMP_DIR/new.yml" ]]; then
        error "Failed to create synced vault"
        return 1
    fi
    
    # Now analyze what was removed and what's missing using Python
    python3 -c "
import yaml
import sys

def flatten_dict(d, parent_key='', sep='.'):
    items = []
    for k, v in d.items():
        new_key = f'{parent_key}{sep}{k}' if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)

try:
    # Load files
    with open('$EXAMPLE_FILE', 'r') as f:
        example_data = yaml.safe_load(f)
    
    with open('$TEMP_DIR/current.yml', 'r') as f:
        current_data = yaml.safe_load(f)
    
    with open('$TEMP_DIR/new.yml', 'r') as f:
        new_data = yaml.safe_load(f)
    
    if not example_data or not current_data or not new_data:
        print('Error: Empty vault files', file=sys.stderr)
        sys.exit(1)
    
    # Flatten structures
    example_flat = flatten_dict(example_data)
    current_flat = flatten_dict(current_data)
    new_flat = flatten_dict(new_data)
    
    # Find mapped keys
    mapped_keys = set()
    for key in example_flat.keys():
        if key in current_flat:
            mapped_keys.add(key)
    
    # Find removed secrets (in current but not in example)
    removed_secrets = {}
    for key in current_flat.keys():
        if key not in example_flat:
            # Check if this key is a prefix of any example key
            # (i.e., it's an intermediate node, not a removed leaf)
            is_prefix = any(example_key.startswith(key + '.') for example_key in example_flat.keys())
            if not is_prefix:
                removed_secrets[key] = current_flat[key]
    
    # Save removed secrets if any
    if removed_secrets:
        def unflatten_dict(d, sep='.'):
            result = {}
            for key, value in d.items():
                parts = key.split(sep)
                current = result
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = value
            return result
        
        removed_data = unflatten_dict(removed_secrets)
        with open('$TEMP_DIR/removed.yml', 'w') as f:
            yaml.dump(removed_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        
        # Save list of removed keys
        with open('$TEMP_DIR/removed_keys.txt', 'w') as f:
            for key in sorted(removed_secrets.keys()):
                f.write(f'{key}\\n')
    
    # Find missing secrets (placeholders in new vault)
    missing = []
    for key, value in new_flat.items():
        if isinstance(value, str) and ('CHANGE_ME' in value or 'your-' in value or value.endswith('-here')):
            missing.append(key)
    
    if missing:
        with open('$TEMP_DIR/missing.txt', 'w') as f:
            for key in sorted(missing):
                f.write(f'{key}\\n')
    
    sys.exit(0)
    
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
"
    
    if [[ $? -eq 0 ]]; then
        success "Vault structure synced with comments preserved"
        return 0
    else
        error "Failed to analyze vault changes"
        return 1
    fi
}

# ============================================================================
# Main
# ============================================================================

main() {
    header "Sync Vault with Example" 70
    
    echo ""
    info "This will:"
    echo "  1. Decrypt your current vault"
    echo "  2. Map secrets to vault.example.yml structure"
    echo "  3. Create a new vault with updated structure"
    echo "  4. Save unmapped secrets to vault.removed.yml"
    echo "  5. Report secrets that need to be added"
    echo ""
    warn "Your current vault will be backed up to vault.backup.yml"
    echo ""
    
    if ! confirm "Continue with vault sync?"; then
        info "Sync cancelled"
        return 0
    fi
    
    echo ""
    separator 70
    
    # Check files exist
    if ! check_files; then
        return 1
    fi
    
    # Decrypt current vault
    if ! decrypt_vault; then
        return 1
    fi
    
    # Create synced vault
    if ! create_synced_vault; then
        return 1
    fi
    
    echo ""
    separator 70
    
    # Show results
    info "Sync completed. Summary:"
    echo ""
    
    # Count secrets in each file
    local current_count=$(grep -c '^  [a-z_]' "$TEMP_DIR/current.yml" 2>/dev/null || echo "0")
    local new_count=$(grep -c '^  [a-z_]' "$TEMP_DIR/new.yml" 2>/dev/null || echo "0")
    
    echo "  Current vault: ${current_count} secrets"
    echo "  New vault:     ${new_count} secrets"
    
    # Check for removed secrets
    if [[ -f "$TEMP_DIR/removed.yml" ]]; then
        echo ""
        warn "Secret(s) removed from new structure (saved to vault.removed.*.yml)"
        echo ""
        echo "Removed secrets:"
        # Show the flattened keys that were actually removed
        if [[ -f "$TEMP_DIR/removed_keys.txt" ]]; then
            cat "$TEMP_DIR/removed_keys.txt" | sed 's/^/    - /'
        else
            # Fallback to YAML structure
            grep '^  [a-z_]' "$TEMP_DIR/removed.yml" | sed 's/:.*$//' | sed 's/^/    - /'
        fi
    fi
    
    # Check for missing secrets
    if [[ -f "$TEMP_DIR/missing.txt" ]]; then
        local missing_count=$(wc -l < "$TEMP_DIR/missing.txt" | tr -d ' ')
        echo ""
        warn "${missing_count} secret(s) need values (still have placeholders)"
        echo ""
        echo "Missing secrets:"
        cat "$TEMP_DIR/missing.txt" | sed 's/^/    - /'
    fi
    
    echo ""
    separator 70
    
    # Ask to apply changes
    echo ""
    if ! confirm "Apply these changes?"; then
        info "Changes not applied. Files remain unchanged."
        return 0
    fi
    
    echo ""
    info "Applying changes..."
    
    # Create backup directory if it doesn't exist
    mkdir -p "$VAULT_BACKUP_DIR"
    success "Backup directory ready: roles/secrets/vars/backups/"
    
    # Backup current vault with timestamp
    cp "$VAULT_FILE" "$VAULT_BACKUP"
    success "Current vault backed up to vault.backup.${TIMESTAMP}.yml"
    
    # Encrypt and save new vault
    cd "$ANSIBLE_DIR"
    local vault_pass_args=$(get_vault_pass_args)
    
    if ! ansible-vault encrypt "$TEMP_DIR/new.yml" $vault_pass_args --output="$VAULT_NEW" 2>/dev/null; then
        error "Failed to encrypt new vault"
        cd "$REPO_ROOT"
        return 1
    fi
    
    # Replace old vault with new
    mv "$VAULT_NEW" "$VAULT_FILE"
    success "New vault saved"
    
    # Encrypt and save removed secrets if any
    if [[ -f "$TEMP_DIR/removed.yml" ]]; then
        if ! ansible-vault encrypt "$TEMP_DIR/removed.yml" $vault_pass_args --output="$VAULT_REMOVED_BACKUP" 2>/dev/null; then
            error "Failed to encrypt removed secrets"
        else
            success "Removed secrets saved to vault.removed.${TIMESTAMP}.yml (encrypted)"
        fi
    fi
    
    cd "$REPO_ROOT"
    
    echo ""
    separator 70
    success "Vault sync complete!"
    separator 70
    
    echo ""
    info "Next steps:"
    echo ""
    
    if [[ -f "$VAULT_REMOVED_BACKUP" ]]; then
        echo "  1. Review removed secrets:"
        echo -e "     ${CYAN}cd provision/ansible${NC}"
        echo -e "     ${CYAN}ansible-vault view roles/secrets/vars/backups/vault.removed.${TIMESTAMP}.yml${NC}"
        echo ""
    fi
    
    if [[ -f "$TEMP_DIR/missing.txt" ]]; then
        echo "  2. Add missing secrets:"
        echo -e "     ${CYAN}cd provision/ansible${NC}"
        echo -e "     ${CYAN}ansible-vault edit roles/secrets/vars/vault.yml${NC}"
        echo ""
        echo "  Secrets that need values:"
        cat "$TEMP_DIR/missing.txt" | sed 's/^/     - /'
        echo ""
    fi
    
    echo "  3. Test the new vault:"
    echo -e "     ${CYAN}make configure${NC} → Verify Configuration"
    echo ""
    
    echo "  Backups available at:"
    echo -e "     ${DIM}provision/ansible/roles/secrets/vars/backups/${NC}"
    echo "     - vault.backup.${TIMESTAMP}.yml (your previous vault)"
    if [[ -f "$VAULT_REMOVED_BACKUP" ]]; then
        echo "     - vault.removed.${TIMESTAMP}.yml (unmapped secrets)"
    fi
    echo ""
}

main "$@"
