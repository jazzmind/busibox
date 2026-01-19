#!/usr/bin/env bash
#
# Migrate .env.local to Ansible Vault
#
# EXECUTION CONTEXT: Admin workstation
# PURPOSE: One-time migration from .env.local to vault.yml structure
#
# This script:
# 1. Reads your current .env.local file
# 2. Reads the current vault.yml (decrypted)
# 3. Maps .env.local values to vault.yml structure
# 4. Creates a new vault with merged values
# 5. Backs up both old files before replacing
#
# USAGE:
#   bash scripts/vault/migrate-env-to-vault.sh
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

ENV_LOCAL="${REPO_ROOT}/.env.local"
ANSIBLE_DIR="${REPO_ROOT}/provision/ansible"
VAULT_FILE="${ANSIBLE_DIR}/roles/secrets/vars/vault.yml"
EXAMPLE_FILE="${ANSIBLE_DIR}/roles/secrets/vars/vault.example.yml"
VAULT_BACKUP_DIR="${ANSIBLE_DIR}/roles/secrets/vars/backups"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
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

# Parse .env.local file
parse_env_file() {
    if [[ ! -f "$ENV_LOCAL" ]]; then
        error ".env.local file not found: $ENV_LOCAL"
        return 1
    fi
    
    info "Parsing .env.local file..."
    
    # Extract key=value pairs (ignore comments and empty lines)
    grep -v '^#' "$ENV_LOCAL" | grep -v '^$' | grep '=' > "$TEMP_DIR/env.txt" || true
    
    success "Parsed $(wc -l < "$TEMP_DIR/env.txt" | tr -d ' ') environment variables"
    return 0
}

# Get value from .env file
get_env_value() {
    local key="$1"
    grep "^${key}=" "$TEMP_DIR/env.txt" | cut -d'=' -f2- | sed 's/^"//' | sed 's/"$//' || echo ""
}

# Decrypt current vault or use example
decrypt_vault() {
    if [[ ! -f "$VAULT_FILE" ]]; then
        info "No existing vault.yml found, using vault.example.yml as base"
        cp "$EXAMPLE_FILE" "$TEMP_DIR/current_vault.yml"
        success "Using example vault as base"
        return 0
    fi
    
    local vault_pass_args=$(get_vault_pass_args)
    
    info "Decrypting current vault..."
    cd "$ANSIBLE_DIR"
    
    if ! ansible-vault view "$VAULT_FILE" $vault_pass_args > "$TEMP_DIR/current_vault.yml" 2>/dev/null; then
        error "Failed to decrypt vault (check password)"
        cd "$REPO_ROOT"
        return 1
    fi
    
    cd "$REPO_ROOT"
    success "Vault decrypted"
    return 0
}

# Create merged vault using Python
create_merged_vault() {
    info "Merging .env.local values into vault structure..."
    
    python3 -c "
import yaml
import sys
import os

def get_env_value(key):
    env_file = '$TEMP_DIR/env.txt'
    try:
        with open(env_file, 'r') as f:
            for line in f:
                if line.startswith(key + '='):
                    return line.split('=', 1)[1].strip().strip('\"')
    except:
        pass
    return None

try:
    # Load current vault
    with open('$TEMP_DIR/current_vault.yml', 'r') as f:
        vault = yaml.safe_load(f)
    
    if not vault:
        vault = {'secrets': {}}
    
    if 'secrets' not in vault:
        vault['secrets'] = {}
    
    # Map .env.local values to vault structure
    # Database
    postgres_pass = get_env_value('POSTGRES_PASSWORD')
    if postgres_pass:
        if 'postgresql' not in vault['secrets']:
            vault['secrets']['postgresql'] = {}
        vault['secrets']['postgresql']['password'] = postgres_pass
    
    # MinIO
    minio_user = get_env_value('MINIO_ACCESS_KEY')
    minio_pass = get_env_value('MINIO_SECRET_KEY')
    if minio_user:
        if 'minio' not in vault['secrets']:
            vault['secrets']['minio'] = {}
        vault['secrets']['minio']['root_user'] = minio_user
        vault['secrets']['minio']['root_password'] = minio_pass
    
    # OpenAI
    openai_key = get_env_value('OPENAI_API_KEY')
    if openai_key and openai_key.startswith('sk-'):
        if 'openai' not in vault['secrets']:
            vault['secrets']['openai'] = {}
        vault['secrets']['openai']['api_key'] = openai_key
        vault['secrets']['openai_api_key'] = openai_key
    
    # Bedrock/AWS
    bedrock_key = get_env_value('BEDROCK_API_KEY')
    aws_access = get_env_value('AWS_ACCESS_KEY_ID')
    aws_secret = get_env_value('AWS_SECRET_ACCESS_KEY')
    aws_region = get_env_value('AWS_REGION_NAME')
    
    if bedrock_key:
        if 'bedrock' not in vault['secrets']:
            vault['secrets']['bedrock'] = {}
        vault['secrets']['bedrock']['api_key'] = bedrock_key
        vault['secrets']['bedrock_api_key'] = bedrock_key
        if aws_region:
            vault['secrets']['bedrock']['region'] = aws_region
    
    # LiteLLM
    litellm_key = get_env_value('LITELLM_MASTER_KEY') or get_env_value('LITELLM_API_KEY')
    if litellm_key:
        vault['secrets']['litellm_api_key'] = litellm_key
        if 'litellm' not in vault['secrets']:
            vault['secrets']['litellm'] = {}
        vault['secrets']['litellm']['master_key'] = litellm_key
    
    # AuthZ
    authz_admin = get_env_value('AUTHZ_ADMIN_TOKEN')
    authz_master = get_env_value('AUTHZ_MASTER_KEY')
    
    if authz_admin or authz_master:
        if 'authz' not in vault['secrets']:
            vault['secrets']['authz'] = {}
        if authz_admin:
            vault['secrets']['authz']['admin_token'] = authz_admin
        if authz_master:
            vault['secrets']['authz']['master_key'] = authz_master
    
    # Better Auth / JWT
    better_auth = get_env_value('BETTER_AUTH_SECRET')
    sso_jwt = get_env_value('SSO_JWT_SECRET')
    jwt_secret = get_env_value('JWT_SECRET') or sso_jwt
    
    if better_auth:
        vault['secrets']['better_auth_secret'] = better_auth
    if jwt_secret:
        vault['secrets']['jwt_secret'] = jwt_secret
    
    # AI Portal specific
    admin_email = get_env_value('ADMIN_EMAIL')
    allowed_domains = get_env_value('ALLOWED_EMAIL_DOMAINS')
    resend_key = get_env_value('RESEND_API_KEY')
    email_from = get_env_value('EMAIL_FROM')
    
    if admin_email:
        vault['secrets']['admin_email'] = admin_email
    if allowed_domains:
        vault['secrets']['allowed_email_domains'] = allowed_domains
    if resend_key:
        vault['secrets']['resend_api_key'] = resend_key
    
    # OAuth clients
    ai_portal_client = get_env_value('AUTHZ_BOOTSTRAP_CLIENT_ID')
    ai_portal_secret = get_env_value('AUTHZ_BOOTSTRAP_CLIENT_SECRET')
    agent_manager_client = get_env_value('AGENT_MANAGER_CLIENT_ID')
    agent_manager_secret = get_env_value('AGENT_MANAGER_CLIENT_SECRET')
    
    if ai_portal_client:
        vault['secrets']['oauth_client_id'] = ai_portal_client
        vault['secrets']['oauth_client_secret'] = ai_portal_secret or ''
    
    # Agent Manager
    if agent_manager_client or agent_manager_secret:
        if 'agent-manager' not in vault['secrets']:
            vault['secrets']['agent-manager'] = {}
        if agent_manager_client:
            vault['secrets']['agent-manager']['admin_client_id'] = agent_manager_client
        if agent_manager_secret:
            vault['secrets']['agent-manager']['admin_client_secret'] = agent_manager_secret
            vault['secrets']['agent-manager']['oauth_client_secret'] = agent_manager_secret
        if litellm_key:
            vault['secrets']['agent-manager']['litellm_api_key'] = litellm_key
    
    # GitHub
    github_token = get_env_value('GITHUB_AUTH_TOKEN')
    if github_token:
        vault['secrets']['github_token'] = github_token
    
    # Save merged vault
    with open('$TEMP_DIR/merged_vault.yml', 'w') as f:
        yaml.dump(vault, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    
    print('SUCCESS')
    sys.exit(0)
    
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
"
    
    if [[ $? -eq 0 ]]; then
        success "Vault values merged"
        return 0
    else
        error "Failed to merge vault values"
        return 1
    fi
}

# ============================================================================
# Main
# ============================================================================

main() {
    header "Migrate .env.local to Ansible Vault" 70
    
    echo ""
    info "This will:"
    echo "  1. Read your current .env.local file"
    echo "  2. Decrypt your current vault.yml"
    echo "  3. Merge .env.local values into vault structure"
    echo "  4. Backup both old files (timestamped)"
    echo "  5. Encrypt and save the new vault"
    echo ""
    warn "After migration, you can delete .env.local (it won't be used)"
    echo ""
    
    if ! confirm "Continue with migration?"; then
        info "Migration cancelled"
        return 0
    fi
    
    echo ""
    separator 70
    
    # Check files exist
    if [[ ! -f "$ENV_LOCAL" ]]; then
        error ".env.local not found: $ENV_LOCAL"
        return 1
    fi
    
    if [[ ! -f "$EXAMPLE_FILE" ]]; then
        error "vault.example.yml not found: $EXAMPLE_FILE"
        return 1
    fi
    
    # Parse .env.local
    if ! parse_env_file; then
        return 1
    fi
    
    # Decrypt current vault
    if ! decrypt_vault; then
        return 1
    fi
    
    # Create merged vault
    if ! create_merged_vault; then
        return 1
    fi
    
    echo ""
    separator 70
    
    # Show preview
    info "Preview of merged vault:"
    echo ""
    head -50 "$TEMP_DIR/merged_vault.yml" | sed 's/^/  /'
    echo ""
    echo "  ... (truncated)"
    echo ""
    
    separator 70
    
    # Ask to apply
    echo ""
    if ! confirm "Apply these changes?"; then
        info "Changes not applied. Files remain unchanged."
        return 0
    fi
    
    echo ""
    info "Applying changes..."
    
    # Create backup directory
    mkdir -p "$VAULT_BACKUP_DIR"
    success "Backup directory ready"
    
    # Backup old files
    if [[ -f "$VAULT_FILE" ]]; then
        cp "$VAULT_FILE" "$VAULT_BACKUP_DIR/vault.backup.${TIMESTAMP}.yml"
        success "Vault backed up to vault.backup.${TIMESTAMP}.yml"
    else
        info "No existing vault to backup (creating new one)"
    fi
    
    cp "$ENV_LOCAL" "$VAULT_BACKUP_DIR/env.local.backup.${TIMESTAMP}"
    success ".env.local backed up to env.local.backup.${TIMESTAMP}"
    
    # Encrypt and save new vault
    cd "$ANSIBLE_DIR"
    local vault_pass_args=$(get_vault_pass_args)
    
    if ! ansible-vault encrypt "$TEMP_DIR/merged_vault.yml" $vault_pass_args --output="$VAULT_FILE" 2>/dev/null; then
        error "Failed to encrypt new vault"
        # Restore backup if it exists
        if [[ -f "$VAULT_BACKUP_DIR/vault.backup.${TIMESTAMP}.yml" ]]; then
            cp "$VAULT_BACKUP_DIR/vault.backup.${TIMESTAMP}.yml" "$VAULT_FILE"
            error "Restored original vault from backup"
        fi
        cd "$REPO_ROOT"
        return 1
    fi
    
    cd "$REPO_ROOT"
    success "New vault saved and encrypted"
    
    echo ""
    separator 70
    success "Migration complete!"
    separator 70
    
    echo ""
    info "Next steps:"
    echo ""
    echo "  1. Verify the new vault:"
    echo "     ${CYAN}cd provision/ansible${NC}"
    echo "     ${CYAN}ansible-vault view roles/secrets/vars/vault.yml${NC}"
    echo ""
    echo "  2. Test with Docker Compose:"
    echo "     ${CYAN}make docker-down${NC}"
    echo "     ${CYAN}make docker-up${NC}"
    echo ""
    echo "  3. After verification, you can delete .env.local:"
    echo "     ${CYAN}rm .env.local${NC}"
    echo "     ${DIM}(backup saved at: provision/ansible/roles/secrets/vars/backups/env.local.backup.${TIMESTAMP})${NC}"
    echo ""
    echo "  Backups available at:"
    echo "     ${DIM}provision/ansible/roles/secrets/vars/backups/${NC}"
    echo "     - vault.backup.${TIMESTAMP}.yml"
    echo "     - env.local.backup.${TIMESTAMP}"
    echo ""
}

main "$@"
