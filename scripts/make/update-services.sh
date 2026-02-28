#!/usr/bin/env bash
#
# Busibox Targeted Update
# =======================
#
# Rebuild/redeploy selected service layers from latest source while preserving data.
# Scope (intentionally limited):
#   - APIs (srv/*) are redeployed from current host source
#   - All core frontend apps (busibox-frontend monorepo) are deployed from a single ref
#   - Core infrastructure services are explicitly skipped
#   - User apps are intentionally skipped
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/profiles.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"

profile_init
_active_profile="$(profile_get_active)"

# All core frontend apps in the busibox-frontend monorepo
FRONTEND_APPS=(
    "busibox-portal"
    "busibox-admin"
    "busibox-agents"
    "busibox-chat"
    "busibox-appbuilder"
    "busibox-media"
    "busibox-documents"
)

get_current_env() {
    if [[ -n "$_active_profile" ]]; then
        profile_get "$_active_profile" "environment"
        return
    fi
    local env
    env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")
    if [[ -z "$env" ]]; then
        env="development"
    fi
    echo "$env"
}

get_backend_type() {
    local backend=""
    if [[ -n "$_active_profile" ]]; then
        backend=$(profile_get "$_active_profile" "backend")
    else
        local env="$1"
        backend=$(get_backend "$env" 2>/dev/null || echo "")
        if [[ -z "$backend" ]]; then
            backend="docker"
        fi
    fi
    # Normalize to lowercase (profiles may store mixed case)
    echo "$backend" | tr '[:upper:]' '[:lower:]'
}

get_inventory_path() {
    local env="$1"
    local backend="$2"
    if [[ "$backend" == "docker" ]]; then
        echo "inventory/docker"
        return
    fi
    case "$env" in
        staging) echo "inventory/staging" ;;
        production) echo "inventory/production" ;;
        *) echo "inventory/staging" ;;
    esac
}

deploy_frontend_apps() {
    local ref="$1"
    local backend="$2"
    local inventory="$3"

    info "Deploying all busibox-frontend apps with ref '${ref}'..."

    if [[ "$backend" == "docker" ]]; then
        # Docker: deploy via make install which triggers the core_apps role
        (cd "${REPO_ROOT}/provision/ansible" && make install SERVICE=core-apps REF="${ref}")
    else
        # Proxmox: deploy each app via the deploy API
        for app in "${FRONTEND_APPS[@]}"; do
            info "Deploying ${app}..."
            (cd "${REPO_ROOT}/provision/ansible" && make deploy-app-ref APP="${app}" REF="${ref}" INV="${inventory}")
        done
    fi
}

main() {
    local env backend inventory
    env="$(get_current_env)"
    backend="$(get_backend_type "$env")"
    inventory="$(get_inventory_path "$env" "$backend")"

    if [[ "$backend" == "k8s" ]]; then
        error "Update action currently supports only docker and proxmox backends."
        exit 1
    fi

    echo ""
    box_start 78 double "$CYAN"
    box_header "UPDATE SERVICES"
    box_empty
    box_line "  Environment: ${BOLD}${env}${NC}"
    box_line "  Backend:     ${BOLD}${backend}${NC}"
    if [[ "$backend" != "docker" ]]; then
        box_line "  Inventory:   ${inventory}"
    fi
    box_empty
    box_separator
    box_empty
    box_line "  ${BOLD}Will update:${NC} API services + all core frontend apps"
    box_line "  ${BOLD}Frontend:${NC}    busibox-frontend monorepo (7 apps)"
    box_line "  ${BOLD}Will skip:${NC}   postgres, nginx, neo4j, milvus, minio, user apps"
    box_empty
    box_footer
    echo ""

    read -r -p "Proceed with update? [y/N]: " proceed
    if [[ "$proceed" != "y" && "$proceed" != "Y" ]]; then
        info "Update cancelled."
        exit 0
    fi

    echo ""
    info "Pulling latest source for busibox repository..."
    if (cd "${REPO_ROOT}" && git pull --ff-only); then
        success "Repository updated"
    else
        warn "git pull could not fast-forward (continuing with current host source)"
    fi

    echo ""
    info "Redeploying API services from host source..."
    (cd "${REPO_ROOT}" && make install SERVICE=apis)

    echo ""
    info "Core infrastructure services skipped by design (for migration safety)."
    info "User apps skipped by request."

    # Prompt for busibox-frontend ref
    echo ""
    echo "Update busibox-frontend apps?"
    echo "  Select ref type:"
    echo "  1) branch (e.g., main)"
    echo "  2) release tag (e.g., v1.0.0)"
    echo "  3) skip frontend update"
    read -r -p "Choice [1-3]: " ref_choice

    case "$ref_choice" in
        1)
            read -r -p "Enter branch name [main]: " ref
            ref="${ref:-main}"
            deploy_frontend_apps "$ref" "$backend" "$inventory"
            ;;
        2)
            read -r -p "Enter release tag: " ref
            if [[ -z "$ref" ]]; then
                warn "Empty ref, skipping frontend update"
            else
                deploy_frontend_apps "$ref" "$backend" "$inventory"
            fi
            ;;
        3|*)
            info "Skipping frontend update"
            ;;
    esac

    echo ""
    success "Update action complete."
}

main "$@"
