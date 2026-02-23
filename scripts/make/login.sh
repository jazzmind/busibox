#!/usr/bin/env bash
#
# Busibox Admin Login Helper
# ==========================
#
# Generates a fresh admin magic link token AND a 6-digit TOTP login code,
# then opens the magic link in the browser.
# The TOTP code is a fallback for when the magic link says expired.
# Intended for already-installed environments (primarily Docker/Proxmox).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/profiles.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"
source "${REPO_ROOT}/scripts/lib/services.sh"

profile_init

get_current_env() {
    local active_profile
    active_profile="$(profile_get_active)"
    if [[ -n "$active_profile" ]]; then
        profile_get "$active_profile" "environment"
        return
    fi
    get_state "ENVIRONMENT" "development"
}

get_backend_type() {
    local active_profile
    active_profile="$(profile_get_active)"
    if [[ -n "$active_profile" ]]; then
        profile_get "$active_profile" "backend"
        return
    fi

    local env env_upper backend
    env="$(get_current_env)"
    if [[ "$env" == "development" ]]; then
        echo "docker"
        return
    fi
    env_upper="$(echo "$env" | tr '[:lower:]' '[:upper:]')"
    backend="$(get_state "BACKEND_${env_upper}" "")"
    echo "${backend:-docker}"
}

get_container_prefix() {
    local env="$1"
    case "$env" in
        production) echo "prod" ;;
        staging) echo "staging" ;;
        demo) echo "demo" ;;
        development) echo "dev" ;;
        *) echo "dev" ;;
    esac
}

sql_escape() {
    local s="$1"
    printf "%s" "${s//\'/\'\'}"
}

generate_uuid() {
    if command -v python3 &>/dev/null; then
        python3 -c "import uuid; print(str(uuid.uuid4()))"
    elif command -v uuidgen &>/dev/null; then
        uuidgen | tr '[:upper:]' '[:lower:]'
    else
        openssl rand -hex 16 | sed -E 's/(.{8})(.{4})(.{4})(.{4})(.{12})/\1-\2-\3-\4-\5/'
    fi
}

run_pg_sql() {
    local sql="$1"
    local db="${2:-authz}"
    local backend="$3"
    local env="$4"
    local db_user="${POSTGRES_USER:-busibox_user}"

    if [[ "$backend" == "proxmox" ]]; then
        local pg_host
        pg_host="$(get_service_ip "postgres" "$env" "proxmox" 2>/dev/null || true)"
        if [[ -z "$pg_host" ]]; then
            return 1
        fi
        local sql_escaped
        sql_escaped="$(printf "%q" "$sql")"
        ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@${pg_host}" \
            "sudo -u postgres psql -d ${db} -t -A -c ${sql_escaped}" 2>/dev/null
    else
        local prefix
        prefix="$(get_container_prefix "$env")"
        docker exec "${prefix}-postgres" psql -U "$db_user" -d "$db" -t -A -c "$sql" 2>/dev/null
    fi
}

generate_totp_code() {
    if command -v python3 &>/dev/null; then
        python3 -c "import secrets; print(str(secrets.randbelow(1000000)).zfill(6))"
    else
        printf "%06d" $((RANDOM % 1000000))
    fi
}

sha256_hash() {
    local input="$1"
    if command -v python3 &>/dev/null; then
        python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$input"
    elif command -v shasum &>/dev/null; then
        echo -n "$input" | shasum -a 256 | cut -d' ' -f1
    else
        echo -n "$input" | openssl dgst -sha256 | awk '{print $NF}'
    fi
}

main() {
    local env backend
    env="$(get_current_env)"
    backend="$(get_backend_type)"

    if [[ "$backend" == "k8s" ]]; then
        error "Login magic-link helper is not implemented for K8s backend yet."
        exit 1
    fi

    local admin_email
    admin_email="$(get_state "ADMIN_EMAIL" "")"
    if [[ -z "$admin_email" ]]; then
        read -r -p "Admin email: " admin_email
    fi
    if [[ -z "$admin_email" ]]; then
        error "Admin email is required."
        exit 1
    fi

    local email_lower
    email_lower="$(echo "${admin_email%%,*}" | tr '[:upper:]' '[:lower:]' | xargs)"
    local email_sql
    email_sql="$(sql_escape "$email_lower")"

    info "Generating admin login credentials for ${email_lower} (${env}/${backend})..."

    local user_id
    user_id="$(run_pg_sql "SELECT user_id::text FROM authz_users WHERE lower(email)='${email_sql}' LIMIT 1;" authz "$backend" "$env" | head -1 | tr -d '[:space:]')"

    if [[ -z "$user_id" ]]; then
        user_id="$(generate_uuid)"
        run_pg_sql "INSERT INTO authz_users (user_id, email, status) VALUES ('${user_id}'::uuid, '${email_sql}', 'active');" authz "$backend" "$env" >/dev/null || {
            error "Failed to create admin user record."
            exit 1
        }
    fi

    local admin_role_id
    admin_role_id="$(run_pg_sql "SELECT id::text FROM authz_roles WHERE name='Admin' LIMIT 1;" authz "$backend" "$env" | head -1 | tr -d '[:space:]')"
    if [[ -z "$admin_role_id" ]]; then
        admin_role_id="$(generate_uuid)"
        run_pg_sql "INSERT INTO authz_roles (id, name, description, scopes) VALUES ('${admin_role_id}'::uuid, 'Admin', 'Full system administrator', ARRAY['authz.*', 'busibox-admin.*']);" authz "$backend" "$env" >/dev/null
    fi

    local has_role
    has_role="$(run_pg_sql "SELECT 1 FROM authz_user_roles WHERE user_id='${user_id}'::uuid AND role_id='${admin_role_id}'::uuid;" authz "$backend" "$env" | head -1 | tr -d '[:space:]')"
    if [[ -z "$has_role" ]]; then
        run_pg_sql "INSERT INTO authz_user_roles (user_id, role_id) VALUES ('${user_id}'::uuid, '${admin_role_id}'::uuid);" authz "$backend" "$env" >/dev/null
    fi

    # --- Magic Link ---
    local token token_sql
    token="$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-43)"
    token_sql="$(sql_escape "$token")"

    run_pg_sql "DELETE FROM authz_magic_links WHERE user_id='${user_id}'::uuid;" authz "$backend" "$env" >/dev/null || true
    run_pg_sql "INSERT INTO authz_magic_links (user_id, email, token, expires_at) VALUES ('${user_id}'::uuid, '${email_sql}', '${token_sql}', now() + interval '24 hours');" authz "$backend" "$env" >/dev/null || {
        error "Failed to insert magic link token."
        exit 1
    }

    # --- TOTP Code ---
    local totp_code totp_hash
    totp_code="$(generate_totp_code)"
    totp_hash="$(sha256_hash "$totp_code")"

    run_pg_sql "DELETE FROM authz_totp_codes WHERE user_id='${user_id}'::uuid AND used_at IS NULL;" authz "$backend" "$env" >/dev/null || true
    run_pg_sql "INSERT INTO authz_totp_codes (user_id, code_hash, email, expires_at) VALUES ('${user_id}'::uuid, '${totp_hash}', '${email_sql}', now() + interval '15 minutes');" authz "$backend" "$env" >/dev/null || {
        warn "Failed to insert TOTP code (magic link still available)."
    }

    set_state "ADMIN_EMAIL" "$email_lower"
    set_state "ADMIN_USER_ID" "$user_id"
    set_state "MAGIC_LINK_TOKEN" "$token"

    local site_domain magic_link verify_url
    site_domain="$(get_state "SITE_DOMAIN" "")"
    [[ -z "$site_domain" ]] && site_domain="$(get_state "BASE_DOMAIN" "localhost")"

    if [[ "$site_domain" == "localhost" || -z "$site_domain" ]]; then
        magic_link="https://localhost/portal/verify?token=${token}"
        verify_url="https://localhost/portal/verify?email=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${email_lower}'))" 2>/dev/null || echo "${email_lower}")"
    else
        magic_link="https://${site_domain}/portal/verify?token=${token}"
        verify_url="https://${site_domain}/portal/verify?email=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${email_lower}'))" 2>/dev/null || echo "${email_lower}")"
    fi

    success "Admin login credentials generated."
    echo ""
    echo -e "  ${BOLD}Magic Link${NC} (expires in 24h):"
    echo -e "  ${CYAN}${magic_link}${NC}"
    echo ""
    echo -e "  ${BOLD}Login Code${NC} (expires in 15min):"
    echo -e "  ${CYAN}${totp_code}${NC}"
    echo -e "  Enter at: ${CYAN}${verify_url}${NC}"
    echo ""
    info "Opening browser..."
    if [[ "$(uname -s)" == "Darwin" ]]; then
        open "$magic_link" 2>/dev/null || true
    else
        xdg-open "$magic_link" 2>/dev/null || true
    fi
}

main "$@"
