#!/usr/bin/env bash
#
# Generate Local Test Environment Configuration
#
# EXECUTION CONTEXT: Admin workstation
# PURPOSE: Extract secrets and IPs from vault/inventory to create a local .env file
#          for running service tests locally against remote test containers
#
# USAGE:
#   bash scripts/test/generate-local-test-env.sh [service] [environment]
#   bash scripts/test/generate-local-test-env.sh authz test
#   bash scripts/test/generate-local-test-env.sh data test
#   bash scripts/test/generate-local-test-env.sh search test
#   bash scripts/test/generate-local-test-env.sh agent test
#
# This script:
# 1. Decrypts the ansible vault to get secrets
# 2. Reads the test inventory for container IPs
# 3. Generates a .env file for the specified service
# 4. Outputs the path to the generated .env file
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ANSIBLE_DIR="${REPO_ROOT}/provision/ansible"

# Source UI library if available
if [[ -f "${REPO_ROOT}/scripts/lib/ui.sh" ]]; then
    source "${REPO_ROOT}/scripts/lib/ui.sh"
else
    # Minimal fallback
    info() { echo "[INFO] $1"; }
    success() { echo "[SUCCESS] $1"; }
    warn() { echo "[WARNING] $1"; }
    error() { echo "[ERROR] $1"; }
fi

# Parse arguments
SERVICE="${1:-}"
ENV="${2:-test}"

if [[ -z "$SERVICE" ]]; then
    echo "Usage: $0 <service> [environment]"
    echo ""
    echo "Services: authz, data, search, agent, all"
    echo "Environments: test, production (default: test)"
    exit 1
fi

# Validate environment
if [[ "$ENV" != "staging" && "$ENV" != "production" ]]; then
    error "Invalid environment: $ENV. Use 'staging' or 'production'"
    exit 1
fi

# Network configuration based on environment
if [[ "$ENV" == "test" ]]; then
    NETWORK_BASE="10.96.201"
else
    NETWORK_BASE="10.96.200"
fi

# Container IPs
PROXY_IP="${NETWORK_BASE}.200"
APPS_IP="${NETWORK_BASE}.201"
AGENT_IP="${NETWORK_BASE}.202"
POSTGRES_IP="${NETWORK_BASE}.203"
MILVUS_IP="${NETWORK_BASE}.204"
MINIO_IP="${NETWORK_BASE}.205"
DATA_IP="${NETWORK_BASE}.206"
LITELLM_IP="${NETWORK_BASE}.207"
VLLM_IP="${NETWORK_BASE}.208"
OLLAMA_IP="${NETWORK_BASE}.209"
AUTHZ_IP="${NETWORK_BASE}.210"

# Get vault password flags
get_vault_flags() {
    local vault_pass_file="$HOME/.vault_pass"
    
    if [[ -f "$vault_pass_file" ]]; then
        echo "--vault-password-file $vault_pass_file"
    else
        echo "--ask-vault-pass"
    fi
}

VAULT_FLAGS=$(get_vault_flags)
VAULT_FILE="${ANSIBLE_DIR}/roles/secrets/vars/vault.yml"

# Check vault exists
if [[ ! -f "$VAULT_FILE" ]]; then
    error "Vault file not found: $VAULT_FILE"
    exit 1
fi

info "Extracting secrets from vault..."

# Create a temp file for decrypted vault
TEMP_VAULT=$(mktemp)
trap "rm -f $TEMP_VAULT" EXIT

# Try to decrypt vault, or use directly if not encrypted
if head -1 "$VAULT_FILE" | grep -q '^\$ANSIBLE_VAULT'; then
    # File is encrypted, decrypt it
    if ! ansible-vault view "$VAULT_FILE" $VAULT_FLAGS > "$TEMP_VAULT" 2>/dev/null; then
        error "Failed to decrypt vault"
        exit 1
    fi
else
    # File is not encrypted (development mode), use directly
    cp "$VAULT_FILE" "$TEMP_VAULT"
fi

# Extract secrets using Python
extract_secrets() {
    python3 <<PYTHON_EOF
import yaml
import sys
import re

def resolve_jinja_ref(value, secrets):
    """Resolve Jinja2 template references like {{ secrets.minio.minio_access_key }}"""
    if not isinstance(value, str):
        return value
    
    # Check if it's a Jinja2 template reference
    match = re.match(r'\{\{\s*secrets\.([a-z_]+)\.([a-z_]+)\s*\}\}', value)
    if match:
        section, key = match.groups()
        section_data = secrets.get(section, {})
        if isinstance(section_data, dict):
            resolved = section_data.get(key, '')
            # Don't return another Jinja2 reference
            if isinstance(resolved, str) and '{{' in resolved:
                return ''
            return resolved
    
    # Check for simple Jinja2 reference like {{ secrets.jwt_secret }}
    match = re.match(r'\{\{\s*secrets\.([a-z_]+)\s*\}\}', value)
    if match:
        key = match.group(1)
        resolved = secrets.get(key, '')
        if isinstance(resolved, str) and '{{' in resolved:
            return ''
        return resolved
    
    # Return as-is if no Jinja2 reference
    if '{{' in value:
        return ''  # Skip unresolved Jinja2 templates
    return value

try:
    with open('$TEMP_VAULT', 'r') as f:
        vault = yaml.safe_load(f) or {}
    
    secrets = vault.get('secrets', {})
    
    # PostgreSQL
    pg = secrets.get('postgresql', {})
    print(f"POSTGRES_PASSWORD={pg.get('password', '')}")
    
    # Test database credentials (database name is set per-service in bash)
    # The actual database name (authz, data, agent) is determined by the service
    print(f"TEST_DB_USER=busibox_test_user")
    print(f"TEST_DB_PASSWORD={pg.get('password', '')}")
    
    # MinIO - use minio_access_key and minio_secret_key directly
    minio = secrets.get('minio', {})
    access_key = minio.get('minio_access_key', '') or minio.get('access_key', '')
    secret_key = minio.get('minio_secret_key', '') or minio.get('secret_key', '')
    # Resolve if it's a Jinja2 reference
    access_key = resolve_jinja_ref(access_key, secrets) if isinstance(access_key, str) and '{{' in access_key else access_key
    secret_key = resolve_jinja_ref(secret_key, secrets) if isinstance(secret_key, str) and '{{' in secret_key else secret_key
    print(f"MINIO_ROOT_USER={access_key}")
    print(f"MINIO_ROOT_PASSWORD={secret_key}")
    print(f"MINIO_ACCESS_KEY={access_key}")
    print(f"MINIO_SECRET_KEY={secret_key}")
    
    # LiteLLM
    litellm = secrets.get('litellm', {})
    master_key = litellm.get('master_key', '') or secrets.get('litellm_api_key', '')
    print(f"LITELLM_API_KEY={secrets.get('litellm_api_key', '')}")
    print(f"LITELLM_MASTER_KEY={master_key}")
    
    # Authz
    authz = secrets.get('authz', {})
    
    # Master key for envelope encryption
    master_key = authz.get('master_key', '')
    print(f"AUTHZ_MASTER_KEY={master_key}")
    
    # Bootstrap client - uses jwt_secret as the shared secret (same as deployed services)
    # busibox-portal client is created with jwt_secret as its secret
    jwt_secret = secrets.get('jwt_secret', '')
    
    # Test credentials
    test_creds = secrets.get('test_credentials', {})
    print(f"AUTHZ_TEST_CLIENT_ID={test_creds.get('authz_test_client_id', '')}")
    print(f"AUTHZ_TEST_CLIENT_SECRET={test_creds.get('authz_test_client_secret', '')}")
    # TEST_USER_ID must be a valid UUID for token exchange
    test_user_id = test_creds.get('test_user_id', '')
    # Check if it's a valid UUID format (8-4-4-4-12 hex chars)
    import re
    uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
    if not test_user_id or not uuid_pattern.match(test_user_id):
        test_user_id = '93e9baa1-5a96-4c9e-ae72-a3b077abac92'  # Default test user
    print(f"TEST_USER_ID={test_user_id}")
    print(f"TEST_USER_EMAIL={test_creds.get('test_user_email', 'test@busibox.local')}")
    
    # OpenAI (if configured)
    openai = secrets.get('openai', {})
    if openai.get('api_key'):
        print(f"OPENAI_API_KEY={openai.get('api_key', '')}")
    
    # Bedrock (if configured)
    bedrock = secrets.get('bedrock', {})
    if bedrock.get('api_key'):
        print(f"BEDROCK_API_KEY={bedrock.get('api_key', '')}")
    
    # HuggingFace (if configured)
    hf = secrets.get('huggingface', {})
    if hf.get('token'):
        print(f"HUGGINGFACE_TOKEN={hf.get('token', '')}")
    
except Exception as e:
    print(f"# Error extracting secrets: {e}", file=sys.stderr)
    sys.exit(1)
PYTHON_EOF
}

# Generate service-specific environment file
generate_env_file() {
    local service="$1"
    local env_file=""
    
    case "$service" in
        authz)
            env_file="${REPO_ROOT}/srv/authz/.env.local"
            ;;
        data)
            env_file="${REPO_ROOT}/srv/data/.env.local"
            ;;
        search)
            env_file="${REPO_ROOT}/srv/search/.env.local"
            ;;
        agent)
            env_file="${REPO_ROOT}/srv/agent/.env.local"
            ;;
        all)
            env_file="${REPO_ROOT}/.env.local"
            ;;
        *)
            error "Unknown service: $service"
            return 1
            ;;
    esac
    
    # Determine the correct database for this service
    # Each service uses its own dedicated database:
    #   - authz: "authz" database (RBAC, OAuth, encryption keys)
    #   - data: "data" database (documents, chunks)
    #   - search: "data" database (reads from same as data)
    #   - agent: "agent" database
    case "$service" in
        ingest|search)
            POSTGRES_DB_FOR_SERVICE="data"
            ;;
        authz)
            POSTGRES_DB_FOR_SERVICE="authz"
            ;;
        agent)
            POSTGRES_DB_FOR_SERVICE="agent"
            ;;
        all|*)
            # Default to authz for "all" since it's the most commonly needed
            POSTGRES_DB_FOR_SERVICE="authz"
            ;;
    esac
    
    info "Generating $env_file for $service..."
    
    cat > "$env_file" <<ENV_HEADER
# ============================================
# Local Test Environment Configuration
# Generated: $(date)
# Environment: ${ENV}
# Service: ${service}
# ============================================
# 
# This file was auto-generated by scripts/test/generate-local-test-env.sh
# It contains secrets extracted from the ansible vault and IPs from the test inventory.
#
# Usage: source this file or use pytest with --env-file
#   source ${env_file}
#   pytest tests/
#
# ============================================

# Environment
BUSIBOX_ENV=${ENV}
NODE_ENV=development

# ============================================
# Network Configuration (${ENV} containers)
# ============================================
PROXY_IP=${PROXY_IP}
APPS_IP=${APPS_IP}
AGENT_IP=${AGENT_IP}
POSTGRES_IP=${POSTGRES_IP}
MILVUS_IP=${MILVUS_IP}
MINIO_IP=${MINIO_IP}
DATA_IP=${DATA_IP}
LITELLM_IP=${LITELLM_IP}
VLLM_IP=${VLLM_IP}
OLLAMA_IP=${OLLAMA_IP}
AUTHZ_IP=${AUTHZ_IP}

# ============================================
# PostgreSQL (service-specific database)
# ============================================
POSTGRES_HOST=\${POSTGRES_IP}
POSTGRES_PORT=5432
# Database name depends on the service being tested
# This ensures tests use the same database as deployed services
POSTGRES_DB=${POSTGRES_DB_FOR_SERVICE}
POSTGRES_USER=busibox_${ENV}_user

# Test database connection (for integration tests)
TEST_DB_HOST=${POSTGRES_IP}
TEST_DB_PORT=5432
TEST_DB_NAME=${POSTGRES_DB_FOR_SERVICE}
TEST_DB_USER=busibox_${ENV}_user

# ============================================
# Milvus Vector Database
# ============================================
MILVUS_HOST=${MILVUS_IP}
MILVUS_PORT=19530

# ============================================
# MinIO S3 Storage
# ============================================
MINIO_HOST=${MINIO_IP}
MINIO_PORT=9000
MINIO_CONSOLE_PORT=9001
# MinIO client expects host:port without scheme
MINIO_ENDPOINT=${MINIO_IP}:9000
MINIO_BUCKET=documents

# ============================================
# Redis
# ============================================
REDIS_HOST=${DATA_IP}
REDIS_PORT=6379
REDIS_URL=redis://${DATA_IP}:6379
# Use a separate stream for local testing to avoid conflicts with container worker
REDIS_STREAM=jobs:data:local

# ============================================
# LLM Services
# ============================================
LITELLM_HOST=${LITELLM_IP}
LITELLM_PORT=4000
LITELLM_BASE_URL=http://${LITELLM_IP}:4000

VLLM_HOST=${VLLM_IP}
VLLM_PORT=8000

OLLAMA_HOST=${OLLAMA_IP}
OLLAMA_PORT=11434

# ============================================
# Authz Service
# ============================================
AUTHZ_HOST=${AUTHZ_IP}
AUTHZ_PORT=8010
AUTHZ_URL=http://${AUTHZ_IP}:8010
AUTHZ_BASE_URL=http://${AUTHZ_IP}:8010
TEST_AUTHZ_URL=http://${AUTHZ_IP}:8010
AUTHZ_JWKS_URL=http://${AUTHZ_IP}:8010/.well-known/jwks.json

# Issuer for JWT validation
JWT_ISSUER=busibox-authz
AUTHZ_ISSUER=busibox-authz
# Audience must match the service being tested
AUTHZ_AUDIENCE=${SERVICE}-api

# ============================================
# Service URLs
# ============================================
DATA_API_HOST=${DATA_IP}
DATA_API_PORT=8002
DATA_API_URL=http://${DATA_IP}:8002

SEARCH_API_HOST=${MILVUS_IP}
SEARCH_API_PORT=8003
SEARCH_API_URL=http://${MILVUS_IP}:8003

AGENT_API_HOST=${AGENT_IP}
AGENT_API_PORT=8000
AGENT_API_URL=http://${AGENT_IP}:8000

# ============================================
# GPU Services (use PRODUCTION container for GPU)
# ============================================
# ColPali runs on production vLLM container (10.96.200.208:9006)
# This allows local tests to use GPU-accelerated visual embeddings
COLPALI_BASE_URL=http://10.96.200.208:9006/v1
COLPALI_API_KEY=EMPTY
COLPALI_ENABLED=true

# Marker PDF extraction configuration
# When running locally, we can use GPU via data worker on production
# For API tests, Marker runs in the service; for worker tests, it uses local/remote
MARKER_ENABLED=true
MARKER_USE_GPU=true
MARKER_GPU_DEVICE=cuda
# Remote Marker service URL (if using remote Marker API)
MARKER_SERVICE_URL=

# ============================================
# Embedding Configuration
# ============================================
# Embedding model served via LiteLLM
EMBEDDING_MODEL=qwen3-embedding
EMBEDDING_DIMENSION=4096

# ============================================
# Test Documents
# ============================================
# Path to test document repository (busibox-testdocs)
# Local: sibling directory to busibox repo
# Container: /srv/test-docs (set by Ansible)
TEST_DOC_REPO_PATH=${REPO_ROOT}/../busibox-testdocs

# ============================================
# Secrets (from vault)
# ============================================
ENV_HEADER
    
    # Append extracted secrets
    extract_secrets >> "$env_file"
    
    success "Generated: $env_file"
    echo "$env_file"
}

# Generate the environment file
ENV_FILE=$(generate_env_file "$SERVICE")

echo ""
success "Local test environment generated!"
echo ""
info "To use:"
echo "  source $ENV_FILE"
echo ""
info "Or for pytest:"
echo "  cd srv/${SERVICE}"
echo "  source .env.local && pytest tests/ -v"
echo ""

