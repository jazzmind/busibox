#!/usr/bin/env bash
#
# Busibox Secret Rotation
# =======================
#
# Interactive menu for rotating deployment secrets.
# Generates new random values, updates the vault, and restarts affected services.
#
# Usage:
#   make rotate-secrets       # Interactive menu
#   bash scripts/make/rotate-secrets.sh
#
# Execution Context: Admin workstation
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/profiles.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"
source "${REPO_ROOT}/scripts/lib/vault.sh"
source "${REPO_ROOT}/scripts/lib/backends/common.sh"

# Initialize profiles
profile_init

# ============================================================================
# Profile / Backend Detection
# ============================================================================

_active_profile=$(profile_get_active)

_get_env() {
    if [[ -n "$_active_profile" ]]; then
        profile_get "$_active_profile" "environment"
    else
        get_state "ENVIRONMENT" "development"
    fi
}

_get_backend() {
    if [[ -n "$_active_profile" ]]; then
        profile_get "$_active_profile" "backend"
    else
        local env
        env=$(_get_env)
        local env_upper
        env_upper=$(echo "$env" | tr '[:lower:]' '[:upper:]')
        get_state "BACKEND_${env_upper}" "docker"
    fi
}

_get_vault_prefix() {
    if [[ -n "$_active_profile" ]]; then
        profile_get_vault_prefix "$_active_profile"
    else
        local env
        env=$(_get_env)
        case "$env" in
            development) echo "dev" ;;
            demo) echo "demo" ;;
            staging) echo "staging" ;;
            production) echo "prod" ;;
            *) echo "dev" ;;
        esac
    fi
}

_get_container_prefix() {
    _get_vault_prefix
}

CURRENT_ENV=$(_get_env)
CURRENT_BACKEND=$(_get_backend)
VAULT_PREFIX=$(_get_vault_prefix)
CONTAINER_PREFIX=$(_get_container_prefix)

# Load backend
load_backend "$CURRENT_BACKEND"

# ============================================================================
# Secret Definitions
# ============================================================================
# Each secret: vault_key | display_name | affected_services | category
#
# Notes:
#   - session_secret: Legacy from better-auth era. Auth is now handled by AuthZ
#     service with RS256 JWTs. Only busibox-agents receives it; rotating is harmless
#     (worst case: users re-login). Consider removing entirely.
#   - litellm_master_key, litellm_api_key, litellm_salt_key: All three participate
#     in LiteLLM's DB encryption. Rotating any of them invalidates encrypted
#     model configs, credentials, and env vars. The rotation handler purges the
#     LiteLLM DB so agent-api can re-sync config-file models on restart; cloud
#     provider API keys must be re-entered by the admin afterward.

declare -a SAFE_SECRETS=(
    "secrets.postgresql.password|PostgreSQL password|postgres,authz-api,data-api,data-worker,search-api,agent-api,litellm|safe"
    "secrets.minio.root_user,secrets.minio.root_password|MinIO credentials|minio,data-api,data-worker,search-api|safe"
    "secrets.session_secret|Session secret (legacy)|core-apps|safe"
)

declare -a DANGEROUS_SECRETS=(
    "secrets.authz_master_key|AuthZ master key (KEK re-encryption)|authz-api|dangerous"
    "secrets.jwt_secret|JWT secret / key passphrase (signing key re-encryption)|authz-api|dangerous"
    "secrets.litellm_master_key,secrets.litellm_api_key,secrets.litellm_salt_key|LiteLLM keys (DB re-encryption)|litellm,agent-api,search-api,data-worker|dangerous"
)

# ============================================================================
# Helper Functions
# ============================================================================

# Generate a random secret
_gen_secret() {
    local length="${1:-32}"
    openssl rand -base64 "$length" | tr -d '/+=' | head -c "$length"
}

# Parse a secret definition entry
_parse_secret() {
    local entry="$1"
    local field="$2"
    case "$field" in
        keys)     echo "$entry" | cut -d'|' -f1 ;;
        name)     echo "$entry" | cut -d'|' -f2 ;;
        services) echo "$entry" | cut -d'|' -f3 ;;
        category) echo "$entry" | cut -d'|' -f4 ;;
    esac
}

# Count affected services for a secret
_count_services() {
    local services="$1"
    echo "$services" | tr ',' '\n' | wc -l | tr -d ' '
}

# ============================================================================
# Rotation Logic
# ============================================================================

# Rotate a single secret
# Args: vault_keys (comma-separated), display_name, affected_services
rotate_secret() {
    local vault_keys="$1"
    local display_name="$2"
    local affected_services="$3"
    
    echo ""
    info "Rotating: ${display_name}"
    
    # Set up vault access
    set_vault_environment "$VAULT_PREFIX" 2>/dev/null || true
    if ! ensure_vault_access 2>/dev/null; then
        error "Cannot access vault for prefix: ${VAULT_PREFIX}"
        return 1
    fi
    
    # Generate new values for each key
    local updates=()
    for key in $(echo "$vault_keys" | tr ',' ' '); do
        local new_value
        case "$key" in
            secrets.litellm_master_key)
                new_value="sk-$(_gen_secret 24)"
                ;;
            secrets.litellm_api_key)
                new_value="sk-$(_gen_secret 16)"
                ;;
            secrets.litellm_salt_key)
                new_value="salt-$(_gen_secret 32)"
                ;;
            secrets.minio.root_user)
                # Don't rotate the username, only the password
                info "  Keeping MinIO username unchanged"
                continue
                ;;
            secrets.authz_master_key)
                new_value=$(openssl rand -base64 32)
                ;;
            *)
                new_value=$(_gen_secret 32)
                ;;
        esac
        updates+=("${key}=${new_value}")
        info "  Generated new value for: ${key}"
    done
    
    if [[ ${#updates[@]} -eq 0 ]]; then
        warn "  No values to update"
        return 0
    fi
    
    # Special handling for PostgreSQL: ALTER USER before updating vault
    if [[ "$vault_keys" == *"postgresql.password"* ]]; then
        _rotate_postgres_password "${updates[0]#*=}"
    fi
    
    # Special handling for LiteLLM salt key: purge encrypted data from DB
    # All three LiteLLM keys participate in DB encryption. Purge encrypted
    # data before changing any of them; agent-api re-syncs config models on
    # restart and the admin must re-enter cloud API keys.
    if [[ "$vault_keys" == *"litellm_master_key"* ]]; then
        _rotate_litellm_keys
    fi
    
    # Update vault
    info "  Updating vault..."
    if update_vault_secrets "${updates[@]}"; then
        success "  Vault updated"
    else
        error "  Failed to update vault"
        return 1
    fi
    
    # Restart affected services
    _restart_affected_services "$affected_services"
    
    success "  Rotation complete: ${display_name}"
}

# Special PostgreSQL password rotation
_rotate_postgres_password() {
    local new_password="$1"
    
    info "  Updating PostgreSQL user password..."
    
    case "$CURRENT_BACKEND" in
        docker)
            local pg_container="${CONTAINER_PREFIX}-postgres"
            if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${pg_container}$"; then
                # Execute ALTER USER inside the running postgres container
                if docker exec "$pg_container" psql -U postgres -c \
                    "ALTER USER busibox_user WITH PASSWORD '${new_password}';" 2>/dev/null; then
                    success "  PostgreSQL password updated in database"
                else
                    error "  Failed to ALTER USER in PostgreSQL"
                    error "  You may need to manually update the password"
                    return 1
                fi
            else
                warn "  PostgreSQL container not running - password will be updated on next deploy"
            fi
            ;;
        k8s)
            # Get kubeconfig
            local kubeconfig=""
            if [[ -n "$_active_profile" ]]; then
                kubeconfig=$(profile_get_kubeconfig "$_active_profile" 2>/dev/null)
            fi
            kubeconfig="${kubeconfig:-${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml}"
            
            # Find postgres pod
            local pg_pod
            pg_pod=$(KUBECONFIG="$kubeconfig" kubectl get pods -n busibox \
                -l app=postgres -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
            
            if [[ -n "$pg_pod" ]]; then
                if KUBECONFIG="$kubeconfig" kubectl exec -n busibox "$pg_pod" -- \
                    psql -U postgres -c "ALTER USER busibox_user WITH PASSWORD '${new_password}';" 2>/dev/null; then
                    success "  PostgreSQL password updated in database"
                else
                    error "  Failed to ALTER USER in PostgreSQL"
                    return 1
                fi
            else
                warn "  PostgreSQL pod not found - password will be updated on next deploy"
            fi
            ;;
        proxmox)
            warn "  Proxmox PostgreSQL password rotation requires SSH access"
            warn "  Password will be updated in vault; redeploy postgres to apply"
            ;;
    esac
}

# Special LiteLLM salt key rotation
# Purge all encrypted data from LiteLLM's DB before changing keys.
# After this, agent-api startup re-syncs config-file models automatically.
# Cloud provider API keys must be re-entered by the admin.
_rotate_litellm_keys() {
    info "  Purging encrypted data from LiteLLM database..."

    local _psql_cmd=""

    case "$CURRENT_BACKEND" in
        docker)
            local pg_container="${CONTAINER_PREFIX}-postgres"
            if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${pg_container}$"; then
                _psql_cmd="docker exec $pg_container psql -U busibox_user -d litellm -t -A -c"
            else
                warn "  PostgreSQL container not running - encrypted data will be stale"
                warn "  After deploying, run: make manage SERVICE=litellm ACTION=restart"
                return 0
            fi
            ;;
        k8s)
            local kubeconfig=""
            if [[ -n "$_active_profile" ]]; then
                kubeconfig=$(profile_get_kubeconfig "$_active_profile" 2>/dev/null)
            fi
            kubeconfig="${kubeconfig:-${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml}"
            local pg_pod
            pg_pod=$(KUBECONFIG="$kubeconfig" kubectl get pods -n busibox \
                -l app=postgres -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
            if [[ -n "$pg_pod" ]]; then
                _psql_cmd="KUBECONFIG=$kubeconfig kubectl exec -n busibox $pg_pod -- psql -U busibox_user -d litellm -t -A -c"
            else
                warn "  PostgreSQL pod not found - encrypted data will be stale"
                return 0
            fi
            ;;
        proxmox)
            warn "  Proxmox LiteLLM salt rotation requires SSH access to pg-lxc"
            warn "  After rotation, run: make install SERVICE=litellm,agent-api"
            return 0
            ;;
    esac

    if [[ -n "$_psql_cmd" ]]; then
        # Purge model deployments (re-created by agent-api on next startup)
        eval "$_psql_cmd 'DELETE FROM \"LiteLLM_ProxyModelTable\"'" 2>/dev/null && \
            info "    Purged LiteLLM_ProxyModelTable" || \
            warn "    Failed to purge LiteLLM_ProxyModelTable (table may not exist yet)"

        # Purge environment variables config
        eval "$_psql_cmd 'DELETE FROM \"LiteLLM_Config\" WHERE param_name = '\\''environment_variables'\\'''" 2>/dev/null && \
            info "    Purged LiteLLM_Config environment_variables" || \
            warn "    Failed to purge LiteLLM_Config"

        # Purge stored credentials
        eval "$_psql_cmd 'DELETE FROM \"LiteLLM_CredentialsTable\"'" 2>/dev/null && \
            info "    Purged LiteLLM_CredentialsTable" || \
            warn "    Failed to purge LiteLLM_CredentialsTable"

        success "  LiteLLM encrypted data purged"
        warn "  Cloud provider API keys must be re-entered in Settings > AI Models"
    fi
}

# Restart affected services after rotation
_restart_affected_services() {
    local services_csv="$1"
    
    info "  Restarting affected services..."
    
    local service_list
    IFS=',' read -ra service_list <<< "$services_csv"
    
    case "$CURRENT_BACKEND" in
        docker)
            for service in "${service_list[@]}"; do
                local container="${CONTAINER_PREFIX}-${service}"
                if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
                    info "    Restarting: ${container}"
                    # Redeploy to pick up new secrets from vault
                    cd "$REPO_ROOT"
                    make install SERVICE="$service" 2>/dev/null || {
                        # Fallback to simple restart
                        docker restart "$container" 2>/dev/null || true
                    }
                fi
            done
            ;;
        k8s)
            local kubeconfig=""
            if [[ -n "$_active_profile" ]]; then
                kubeconfig=$(profile_get_kubeconfig "$_active_profile" 2>/dev/null)
            fi
            kubeconfig="${kubeconfig:-${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml}"
            
            # First update K8s secrets
            info "    Regenerating K8s secrets..."
            cd "$REPO_ROOT"
            make k8s-secrets 2>/dev/null || true
            
            # Then rollout restart affected deployments
            for service in "${service_list[@]}"; do
                local deployment
                deployment=$(get_k8s_deployment_name "$service" 2>/dev/null || echo "$service")
                info "    Rolling restart: ${deployment}"
                KUBECONFIG="$kubeconfig" kubectl rollout restart deployment/"$deployment" \
                    -n busibox 2>/dev/null || true
            done
            ;;
        proxmox)
            # Proxmox: use Ansible to redeploy affected services
            for service in "${service_list[@]}"; do
                local tag
                tag=$(get_ansible_tag "$service" 2>/dev/null || echo "$service")
                info "    Redeploying via Ansible: ${tag}"
                cd "${REPO_ROOT}/provision/ansible"
                make "$tag" INV="inventory/${CURRENT_ENV}" 2>/dev/null || true
            done
            ;;
    esac
}

# ============================================================================
# JWT Key Rotation (special - via authz API)
# ============================================================================

rotate_jwt_signing_keys() {
    echo ""
    info "Rotating JWT signing keys..."
    info "  AuthZ generates a new RSA key pair. Old keys remain valid for verification."
    info "  This is a zero-downtime operation."
    
    # Simply restart authz-api - it generates a new signing key on startup
    # while keeping old keys in the JWKS for verification
    case "$CURRENT_BACKEND" in
        docker)
            local container="${CONTAINER_PREFIX}-authz-api"
            info "  Restarting authz-api to generate new signing key..."
            docker restart "$container" 2>/dev/null || true
            ;;
        k8s)
            local kubeconfig=""
            if [[ -n "$_active_profile" ]]; then
                kubeconfig=$(profile_get_kubeconfig "$_active_profile" 2>/dev/null)
            fi
            kubeconfig="${kubeconfig:-${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml}"
            info "  Rolling restart authz-api to generate new signing key..."
            KUBECONFIG="$kubeconfig" kubectl rollout restart deployment/authz-api \
                -n busibox 2>/dev/null || true
            ;;
        proxmox)
            info "  Restarting authz-api via Ansible..."
            cd "${REPO_ROOT}/provision/ansible"
            make authz INV="inventory/${CURRENT_ENV}" 2>/dev/null || true
            ;;
    esac
    
    success "  JWT signing keys rotated (new key active, old keys still valid for verification)"
}

# ============================================================================
# Interactive Menu
# ============================================================================

show_rotation_menu() {
    clear
    box_start 70 double "$CYAN"
    
    local profile_display=""
    if [[ -n "$_active_profile" ]]; then
        profile_display=$(profile_get_display "$_active_profile")
        box_header "SECRET ROTATION" "$profile_display"
    else
        box_header "SECRET ROTATION" "${CURRENT_ENV} (${CURRENT_BACKEND})"
    fi
    box_empty
    
    box_line "  ${BOLD}Rotatable Secrets:${NC}"
    box_line "  ${DIM}──────────────────${NC}"
    
    local idx=1
    for entry in "${SAFE_SECRETS[@]}"; do
        local name services svc_count
        name=$(_parse_secret "$entry" "name")
        services=$(_parse_secret "$entry" "services")
        svc_count=$(_count_services "$services")
        printf "    ${BOLD}%d)${NC} %-28s ${DIM}[affects %d services]${NC}\n" "$idx" "$name" "$svc_count"
        ((idx++))
    done
    
    echo ""
    printf "    ${BOLD}%d)${NC} JWT signing keys             ${DIM}[rotate via authz - no downtime]${NC}\n" "$idx"
    local jwt_idx=$idx
    ((idx++))
    
    echo ""
    printf "    ${BOLD}a)${NC} Rotate ALL safe secrets (1-%d)\n" "${#SAFE_SECRETS[@]}"
    
    echo ""
    box_line "  ${RED}${BOLD}Dangerous (requires data migration):${NC}"
    box_line "  ${DIM}─────────────────────────────────────${NC}"
    
    for entry in "${DANGEROUS_SECRETS[@]}"; do
        local name services svc_count
        name=$(_parse_secret "$entry" "name")
        services=$(_parse_secret "$entry" "services")
        svc_count=$(_count_services "$services")
        printf "    ${BOLD}%d)${NC} ${RED}%-28s${NC} ${DIM}[affects %d services]${NC}\n" "$idx" "$name" "$svc_count"
        ((idx++))
    done
    
    echo ""
    box_line "  ${DIM}b = back${NC}"
    box_empty
    box_footer
    echo ""
    
    read -r -p "Select option: " choice
    
    case "$choice" in
        [1-9]|[1-9][0-9])
            local total_safe=${#SAFE_SECRETS[@]}
            
            if [[ "$choice" -le "$total_safe" ]]; then
                # Safe secret
                local entry="${SAFE_SECRETS[$((choice-1))]}"
                local keys name services
                keys=$(_parse_secret "$entry" "keys")
                name=$(_parse_secret "$entry" "name")
                services=$(_parse_secret "$entry" "services")
                
                echo ""
                warn "This will rotate: ${name}"
                warn "Affected services: ${services}"
                read -r -p "Continue? [y/N]: " confirm
                if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
                    rotate_secret "$keys" "$name" "$services"
                fi
                
            elif [[ "$choice" -eq "$jwt_idx" ]]; then
                # JWT signing keys
                echo ""
                warn "This will generate a new JWT signing key."
                warn "Old keys remain valid for token verification."
                read -r -p "Continue? [y/N]: " confirm
                if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
                    rotate_jwt_signing_keys
                fi
                
            elif [[ "$choice" -gt "$jwt_idx" ]]; then
                # Dangerous secret
                local danger_idx=$((choice - jwt_idx - 1))
                if [[ "$danger_idx" -lt "${#DANGEROUS_SECRETS[@]}" ]]; then
                    local entry="${DANGEROUS_SECRETS[$danger_idx]}"
                    local keys name services
                    keys=$(_parse_secret "$entry" "keys")
                    name=$(_parse_secret "$entry" "name")
                    services=$(_parse_secret "$entry" "services")
                    
                    echo ""
                    echo -e "${RED}╔══════════════════════════════════════════════════════════════╗${NC}"
                    echo -e "${RED}║  ${BOLD}⚠  DANGEROUS OPERATION - DATA LOSS POSSIBLE  ⚠${NC}${RED}              ║${NC}"
                    echo -e "${RED}╚══════════════════════════════════════════════════════════════╝${NC}"
                    echo ""
                    
                    if [[ "$keys" == *"authz_master_key"* ]]; then
                        echo -e "  ${BOLD}Rotating the AuthZ master key requires re-encrypting all${NC}"
                        echo -e "  ${BOLD}Key Encryption Keys (KEKs) in the authz_key_encryption_keys table.${NC}"
                        echo ""
                        echo -e "  ${YELLOW}This is NOT YET AUTOMATED and may cause data loss.${NC}"
                    elif [[ "$keys" == *"jwt_secret"* ]]; then
                        echo -e "  ${BOLD}Rotating the JWT secret requires re-encrypting the RSA${NC}"
                        echo -e "  ${BOLD}signing key PEM stored in the database.${NC}"
                        echo ""
                        echo -e "  ${YELLOW}This is NOT YET AUTOMATED and may cause auth failures.${NC}"
                    elif [[ "$keys" == *"litellm_master_key"* ]]; then
                        echo -e "  ${BOLD}Rotating LiteLLM keys will purge all encrypted data from${NC}"
                        echo -e "  ${BOLD}LiteLLM's database: model configs, credentials, env vars.${NC}"
                        echo ""
                        echo -e "  ${YELLOW}Config-file models are re-synced automatically on restart.${NC}"
                        echo -e "  ${YELLOW}Cloud provider API keys must be re-entered in Settings > AI Models.${NC}"
                    fi
                    
                    echo ""
                    echo -e "  ${RED}Type ${BOLD}DANGEROUS${NC}${RED} to confirm you understand the risks:${NC}"
                    echo ""
                    read -r -p "  > " confirm
                    if [[ "$confirm" == "DANGEROUS" ]]; then
                        echo ""
                        echo -e "  ${RED}Are you REALLY sure? This cannot be undone.${NC}"
                        read -r -p "  Type the secret name to confirm [${name}]: " confirm2
                        if [[ "$confirm2" == "$name" ]]; then
                            rotate_secret "$keys" "$name" "$services"
                        else
                            echo ""
                            echo "  Confirmation did not match. Cancelled."
                        fi
                    else
                        echo ""
                        echo "  Cancelled."
                    fi
                fi
            fi
            ;;
        a|A)
            echo ""
            warn "This will rotate ALL safe secrets:"
            for entry in "${SAFE_SECRETS[@]}"; do
                local name
                name=$(_parse_secret "$entry" "name")
                echo "  - ${name}"
            done
            echo ""
            read -r -p "Continue? [y/N]: " confirm
            if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
                for entry in "${SAFE_SECRETS[@]}"; do
                    local keys name services
                    keys=$(_parse_secret "$entry" "keys")
                    name=$(_parse_secret "$entry" "name")
                    services=$(_parse_secret "$entry" "services")
                    rotate_secret "$keys" "$name" "$services"
                    echo ""
                done
                success "All safe secrets rotated!"
            fi
            ;;
        b|B)
            return 0
            ;;
        *)
            warn "Invalid option"
            sleep 1
            ;;
    esac
    
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
}

# ============================================================================
# Main
# ============================================================================

main() {
    while true; do
        show_rotation_menu
        local ret=$?
        if [[ $ret -ne 0 ]]; then
            break
        fi
    done
}

main "$@"
