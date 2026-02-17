#!/usr/bin/env bash
#
# Busibox Targeted Update
# =======================
#
# Rebuild/redeploy selected service layers from latest source while preserving data.
# Scope (intentionally limited):
#   - APIs (srv/*) are redeployed from current host source
#   - Core apps (busibox-portal, busibox-agents) are optional and ref-selectable
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
    if [[ -n "$_active_profile" ]]; then
        profile_get "$_active_profile" "backend"
        return
    fi
    local env="$1"
    local backend
    backend=$(get_backend "$env" 2>/dev/null || echo "")
    if [[ -z "$backend" ]]; then
        backend="docker"
    fi
    echo "$backend"
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

deploy_core_app_ref() {
    local app="$1"
    local ref="$2"
    local backend="$3"
    local inventory="$4"

    info "Deploying ${app} with ref '${ref}'..."

    if [[ "$backend" == "docker" ]]; then
        (cd "${REPO_ROOT}/provision/ansible" && make install SERVICE="${app}" REF="${ref}")
    else
        (cd "${REPO_ROOT}/provision/ansible" && make deploy-app-ref APP="${app}" REF="${ref}" INV="${inventory}")
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
    box_line "  ${BOLD}Will update:${NC} API services + selected core apps"
    box_line "  ${BOLD}Will skip:${NC}   postgres, nginx, neo4j, milvus, minio, user apps"
    box_empty
    box_footer
    echo ""

    read -r -p "Proceed with update? [y/N]: " proceed
    if [[ "${proceed,,}" != "y" ]]; then
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

    local app
    for app in "busibox-portal" "busibox-agents"; do
        echo ""
        read -r -p "Update ${app}? [y/N]: " update_app
        if [[ "${update_app,,}" != "y" ]]; then
            info "Skipping ${app}"
            continue
        fi

        echo ""
        echo "Select ref type for ${app}:"
        echo "  1) branch"
        echo "  2) release tag"
        read -r -p "Choice [1-2]: " ref_choice

        local ref_type ref
        case "$ref_choice" in
            1) ref_type="branch" ;;
            2) ref_type="release tag" ;;
            *)
                warn "Invalid choice, skipping ${app}"
                continue
                ;;
        esac

        read -r -p "Enter ${ref_type} for ${app}: " ref
        if [[ -z "$ref" ]]; then
            warn "Empty ref, skipping ${app}"
            continue
        fi

        deploy_core_app_ref "$app" "$ref" "$backend" "$inventory"
    done

    echo ""
    success "Update action complete."
}

main "$@"
