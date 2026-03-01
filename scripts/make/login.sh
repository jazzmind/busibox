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

# Flags: --debug for verbose output, --json for machine-readable output
DEBUG="${DEBUG:-0}"
JSON_OUTPUT="${JSON_OUTPUT:-0}"
for arg in "$@"; do
    case "$arg" in
        --debug) DEBUG=1 ;;
        --json) JSON_OUTPUT=1 ;;
    esac
done

debug() {
    [[ "$DEBUG" == "1" ]] && echo -e "${DIM}[DEBUG] $*${NC}" >&2 || true
}

profile_init

# ============================================================================
# Environment Detection & Confirmation
# ============================================================================
# Environment can be passed via env var to skip profile/state lookups

_active_profile=""
if [[ -z "${BUSIBOX_ENV:-}" ]]; then
    _active_profile="$(profile_get_active)"
fi

get_current_env() {
    if [[ -n "${BUSIBOX_ENV:-}" ]]; then
        echo "$BUSIBOX_ENV"
        return
    fi
    if [[ -n "$_active_profile" ]]; then
        profile_get "$_active_profile" "environment"
        return
    fi
    get_state "ENVIRONMENT" "development"
}

get_backend_type() {
    if [[ -n "${BUSIBOX_BACKEND:-}" ]]; then
        echo "$BUSIBOX_BACKEND" | tr '[:upper:]' '[:lower:]'
        return
    fi
    local backend=""
    if [[ -n "$_active_profile" ]]; then
        backend=$(profile_get "$_active_profile" "backend")
    else
        local env env_upper
        env="$(get_current_env)"
        if [[ "$env" == "development" ]]; then
            backend="docker"
        else
            env_upper="$(echo "$env" | tr '[:lower:]' '[:upper:]')"
            backend="$(get_state "BACKEND_${env_upper}" "")"
            backend="${backend:-docker}"
        fi
    fi
    echo "$backend" | tr '[:upper:]' '[:lower:]'
}

confirm_environment() {
    local env="$1"
    local backend="$2"
    local count

    # Skip interactive confirmation in JSON mode
    if [[ "$JSON_OUTPUT" == "1" ]]; then
        return 0
    fi

    echo ""
    if [[ -n "$_active_profile" ]]; then
        local display
        display="$(profile_get_display "$_active_profile")"
        echo -e "  ${CYAN}Profile:${NC}     ${BOLD}${_active_profile}${NC} (${display})"
    else
        echo -e "  ${CYAN}Environment:${NC} ${BOLD}${env}${NC} (${backend})"
    fi

    count="$(profile_count)"
    if [[ "$count" -gt 1 ]]; then
        echo ""
        echo -e "  ${DIM}Available profiles:${NC}"
        profile_list
        echo ""
        read -r -p "  Press Enter to continue, or type profile number to switch: " choice || choice=""
        if [[ -n "$choice" && "$choice" =~ ^[0-9]+$ ]]; then
            local target_id
            target_id="$(profile_get_by_index "$choice" 2>/dev/null || true)"
            if [[ -n "$target_id" ]]; then
                profile_set_active "$target_id"
                _active_profile="$target_id"
                info "Switched to profile: $target_id ($(profile_get_display "$target_id"))"
                return 1
            else
                warn "Invalid selection, using current profile."
            fi
        fi
    else
        echo ""
    fi
    return 0
}

# ============================================================================
# Helpers
# ============================================================================

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

# ============================================================================
# SQL Execution
# ============================================================================

# Resolved once in main() and used by run_pg_sql
_PG_HOST=""

run_pg_sql() {
    local sql="$1"
    local db="${2:-authz}"
    local backend="$3"
    local env="$4"
    local db_user="${POSTGRES_USER:-busibox_user}"

    debug "run_pg_sql: backend=${backend} env=${env} db=${db} pg_host=${_PG_HOST}"
    debug "run_pg_sql: SQL=${sql}"

    if [[ "$backend" == "proxmox" ]]; then
        local result exit_code
        result="$(echo "$sql" | ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@${_PG_HOST}" \
            "cd /tmp && sudo -u postgres psql -d ${db} -t -A" 2>/dev/null)"
        exit_code=$?
        debug "run_pg_sql: exit_code=${exit_code} result='${result}'"
        if [[ $exit_code -ne 0 ]]; then
            local stderr_out
            stderr_out="$(echo "$sql" | ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@${_PG_HOST}" \
                "cd /tmp && sudo -u postgres psql -d ${db} -t -A" 2>&1 >/dev/null)"
            debug "run_pg_sql: FAILED stderr: ${stderr_out}"
        fi
        echo "$result"
        return $exit_code
    else
        local prefix
        prefix="$(get_container_prefix "$env")"
        debug "run_pg_sql: docker container=${prefix}-postgres"
        docker exec "${prefix}-postgres" psql -U "$db_user" -d "$db" -t -A -c "$sql" 2>/dev/null
    fi
}

# ============================================================================
# Main
# ============================================================================

main() {
    local env backend
    env="$(get_current_env)"
    backend="$(get_backend_type)"

    debug "active_profile=${_active_profile}"
    debug "env=${env} backend=${backend}"
    debug "hostname=$(hostname) uname=$(uname -n)"

    if [[ "$backend" == "k8s" ]]; then
        error "Login magic-link helper is not implemented for K8s backend yet."
        exit 1
    fi

    # Show environment and let user confirm or switch profile
    if ! confirm_environment "$env" "$backend"; then
        # User switched profile — re-detect
        env="$(get_current_env)"
        backend="$(get_backend_type)"
        debug "After switch: env=${env} backend=${backend}"
    fi

    # Resolve postgres host
    _PG_HOST="$(get_service_ip "postgres" "$env" "$backend")"
    if [[ -z "$_PG_HOST" ]]; then
        error "Cannot resolve postgres host for env=${env}, backend=${backend}"
        exit 1
    fi
    [[ "$JSON_OUTPUT" != "1" ]] && info "Database: ${_PG_HOST} (${env})"

    local admin_email
    admin_email="${ADMIN_EMAIL:-$(get_state "ADMIN_EMAIL" "")}"
    debug "admin_email from state/env: '${admin_email}'"
    if [[ -z "$admin_email" ]]; then
        if [[ "$JSON_OUTPUT" == "1" ]]; then
            echo '{"error":"No admin email configured. Set ADMIN_EMAIL in state."}' >&2
            exit 1
        fi
        read -r -p "Admin email: " admin_email || admin_email=""
    fi
    if [[ -z "$admin_email" ]]; then
        error "Admin email is required."
        exit 1
    fi

    local email_lower
    email_lower="$(echo "${admin_email%%,*}" | tr '[:upper:]' '[:lower:]' | xargs)"
    local email_sql
    email_sql="$(sql_escape "$email_lower")"

    [[ "$JSON_OUTPUT" != "1" ]] && info "Generating admin login credentials for ${email_lower} (${env}/${backend})..."

    # Test DB connectivity
    debug "Testing DB connectivity..."
    local db_test
    db_test="$(run_pg_sql "SELECT 'connected' as test;" authz "$backend" "$env" | head -1 | tr -d '[:space:]')"
    debug "DB connectivity test: '${db_test}'"
    if [[ "$db_test" != "connected" ]]; then
        error "Cannot connect to authz database at ${_PG_HOST}"
        error "DB test returned: '${db_test}'"
        exit 1
    fi

    local user_id
    user_id="$(run_pg_sql "SELECT user_id::text FROM authz_users WHERE lower(email)='${email_sql}' LIMIT 1;" authz "$backend" "$env" | head -1 | tr -d '[:space:]')"
    debug "user_id lookup: '${user_id}'"

    if [[ -z "$user_id" ]]; then
        user_id="$(generate_uuid)"
        debug "Creating new user: ${user_id}"
        run_pg_sql "INSERT INTO authz_users (user_id, email, status) VALUES ('${user_id}'::uuid, '${email_sql}', 'active');" authz "$backend" "$env" >/dev/null || {
            error "Failed to create admin user record."
            exit 1
        }
    fi
    debug "user_id=${user_id}"

    local admin_role_id
    admin_role_id="$(run_pg_sql "SELECT id::text FROM authz_roles WHERE name='Admin' LIMIT 1;" authz "$backend" "$env" | head -1 | tr -d '[:space:]')"
    debug "admin_role_id=${admin_role_id}"
    if [[ -z "$admin_role_id" ]]; then
        admin_role_id="$(generate_uuid)"
        debug "Creating Admin role: ${admin_role_id}"
        run_pg_sql "INSERT INTO authz_roles (id, name, description, scopes) VALUES ('${admin_role_id}'::uuid, 'Admin', 'Full system administrator', ARRAY['authz.*', 'busibox-admin.*']);" authz "$backend" "$env" >/dev/null
    fi

    local has_role
    has_role="$(run_pg_sql "SELECT 1 FROM authz_user_roles WHERE user_id='${user_id}'::uuid AND role_id='${admin_role_id}'::uuid;" authz "$backend" "$env" | head -1 | tr -d '[:space:]')"
    debug "has_role=${has_role}"
    if [[ -z "$has_role" ]]; then
        debug "Assigning Admin role to user"
        run_pg_sql "INSERT INTO authz_user_roles (user_id, role_id) VALUES ('${user_id}'::uuid, '${admin_role_id}'::uuid);" authz "$backend" "$env" >/dev/null
    fi

    # --- Magic Link ---
    local token token_sql
    token="$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-43)"
    token_sql="$(sql_escape "$token")"
    debug "Generated token: ${token}"

    run_pg_sql "DELETE FROM authz_magic_links WHERE user_id='${user_id}'::uuid;" authz "$backend" "$env" >/dev/null || true
    run_pg_sql "INSERT INTO authz_magic_links (user_id, email, token, expires_at) VALUES ('${user_id}'::uuid, '${email_sql}', '${token_sql}', now() + interval '24 hours');" authz "$backend" "$env" >/dev/null || {
        error "Failed to insert magic link token."
        exit 1
    }

    # Verify the token was saved
    local verify_token
    verify_token="$(run_pg_sql "SELECT token FROM authz_magic_links WHERE user_id='${user_id}'::uuid AND token='${token_sql}' LIMIT 1;" authz "$backend" "$env" | head -1 | tr -d '[:space:]')"
    if [[ "$verify_token" != "$token" ]]; then
        error "Token verification FAILED - token was not saved to database!"
        error "Expected: '${token}'"
        error "Got:      '${verify_token}'"
        exit 1
    fi
    debug "Token verified in DB"

    # --- TOTP Code ---
    local totp_code totp_hash
    totp_code="$(generate_totp_code)"
    totp_hash="$(sha256_hash "$totp_code")"
    debug "TOTP code: ${totp_code} hash: ${totp_hash}"

    run_pg_sql "DELETE FROM authz_totp_codes WHERE user_id='${user_id}'::uuid AND used_at IS NULL;" authz "$backend" "$env" >/dev/null || true
    run_pg_sql "INSERT INTO authz_totp_codes (user_id, code_hash, email, expires_at) VALUES ('${user_id}'::uuid, '${totp_hash}', '${email_sql}', now() + interval '15 minutes');" authz "$backend" "$env" >/dev/null || {
        warn "Failed to insert TOTP code (magic link still available)."
    }

    # Only persist to state if not in headless/JSON mode (state file may not exist)
    if [[ "$JSON_OUTPUT" != "1" ]]; then
        set_state "ADMIN_EMAIL" "$email_lower"
        set_state "ADMIN_USER_ID" "$user_id"
        set_state "MAGIC_LINK_TOKEN" "$token"
    fi

    local site_domain magic_link verify_url
    site_domain="${SITE_DOMAIN:-}"
    if [[ -z "$site_domain" && "$JSON_OUTPUT" != "1" ]]; then
        site_domain="$(get_state "SITE_DOMAIN" "")"
        [[ -z "$site_domain" ]] && site_domain="$(get_state "BASE_DOMAIN" "localhost")"
    fi
    [[ -z "$site_domain" ]] && site_domain="localhost"

    local encoded_email
    encoded_email="$(python3 -c "import urllib.parse; print(urllib.parse.quote('${email_lower}'))" 2>/dev/null || echo "${email_lower}")"

    if [[ "$site_domain" == "localhost" || -z "$site_domain" ]]; then
        magic_link="https://localhost/portal/verify?token=${token}"
        verify_url="https://localhost/portal/verify?email=${encoded_email}"
    else
        magic_link="https://${site_domain}/portal/verify?token=${token}"
        verify_url="https://${site_domain}/portal/verify?email=${encoded_email}"
    fi

    if [[ "$JSON_OUTPUT" == "1" ]]; then
        # Machine-readable JSON output for CLI/TUI consumption
        cat <<EOF
{"magic_link":"${magic_link}","totp_code":"${totp_code}","verify_url":"${verify_url}","email":"${email_lower}"}
EOF
    else
        echo ""
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
    fi
}

main "$@"
