#!/usr/bin/env bash
#
# Milvus Collection Reset
# =======================
# Execution context: Admin workstation (via make milvus-reset)
#
# Drops and recreates the Milvus 'documents' collection with the correct
# embedding dimension from the model registry. All existing vectors are lost
# and documents must be re-embedded.
#
# Works for both Docker and Proxmox backends:
#   Docker:  Runs hybrid_schema.py inside a temporary container
#   Proxmox: Runs hybrid_schema.py via SSH on milvus-lxc
#
# Usage:
#   make milvus-reset                    # Interactive confirmation
#   make milvus-reset CONFIRM=yes        # Skip confirmation
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/profiles.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"

profile_init
if [[ -n "${BUSIBOX_ENV:-}" && -n "${BUSIBOX_BACKEND:-}" ]]; then
    _active_profile=""
else
    _active_profile=$(profile_get_active)
    if [[ -n "$_active_profile" ]]; then
        export BUSIBOX_ENV=$(profile_get "$_active_profile" "environment")
    fi
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
    local env
    env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")
    echo "${env:-development}"
}

get_backend_type() {
    local env="$1"
    local backend=""
    if [[ -n "${BUSIBOX_BACKEND:-}" ]]; then
        backend="$BUSIBOX_BACKEND"
    elif [[ -n "$_active_profile" ]]; then
        backend=$(profile_get "$_active_profile" "backend")
    fi
    if [[ -z "$backend" ]]; then
        backend=$(get_backend "$env" 2>/dev/null || echo "")
    fi
    if [[ -z "$backend" ]]; then
        backend="docker"
    fi
    echo "$backend" | tr '[:upper:]' '[:lower:]'
}

get_embedding_dimension() {
    local env="$1"

    # For Proxmox, read from Ansible model registry
    if command -v python3 &>/dev/null; then
        local dim
        dim=$(python3 -c "
import yaml, sys
try:
    with open('${REPO_ROOT}/provision/ansible/group_vars/all/model_registry.yml') as f:
        data = yaml.safe_load(f)
    models = data.get('available_models', {})
    purposes = data.get('model_purposes', {})
    emb_key = purposes.get('embedding', 'bge-large')
    emb_model = models.get(emb_key, {})
    print(emb_model.get('dimension', 768))
except Exception:
    print(768)
" 2>/dev/null)
        echo "${dim:-768}"
    else
        echo "768"
    fi
}

# ============================================================================
# Main
# ============================================================================

main() {
    local confirm="${CONFIRM:-}"
    local env backend dim

    env=$(get_current_env)
    backend=$(get_backend_type "$env")
    dim=$(get_embedding_dimension "$env")

    echo ""
    box_start 70 single "$RED"
    box_header "MILVUS COLLECTION RESET"
    box_empty
    box_line "  Environment: ${BOLD}${env}${NC}"
    box_line "  Backend:     ${BOLD}${backend}${NC}"
    box_line "  New Dim:     ${BOLD}${dim}${NC}"
    box_empty
    box_line "  ${RED}WARNING: This will DELETE all vectors in the 'documents' collection.${NC}"
    box_line "  ${RED}All documents will need to be re-embedded after this operation.${NC}"
    box_empty
    box_footer
    echo ""

    if [[ "$confirm" != "yes" ]]; then
        echo -n "  Type 'yes' to proceed: "
        read -r response
        if [[ "$response" != "yes" ]]; then
            echo ""
            info "Aborted."
            exit 0
        fi
    fi

    echo ""

    case "$backend" in
        docker)
            _reset_docker "$env" "$dim"
            ;;
        proxmox|ansible)
            _reset_proxmox "$env" "$dim"
            ;;
        *)
            error "Unsupported backend: $backend"
            exit 1
            ;;
    esac

    echo ""
    success "Milvus collection reset complete (dim=${dim})"
    echo ""
    info "Next steps:"
    echo "  1. Restart data services:  make manage SERVICE=data ACTION=restart"
    echo "  2. Restart search service: make manage SERVICE=search ACTION=restart"
    echo "  3. Re-embed documents via the UI or data-api"
    echo ""
}

_reset_docker() {
    local env="$1"
    local dim="$2"
    local prefix

    case "$env" in
        demo) prefix="demo" ;;
        development) prefix="dev" ;;
        staging) prefix="staging" ;;
        production) prefix="prod" ;;
        *) prefix="dev" ;;
    esac

    info "Dropping and recreating Milvus collection via Docker..."

    local compose_project="${COMPOSE_PROJECT_NAME:-busibox}"
    local compose_file="${REPO_ROOT}/docker-compose.yml"

    # Run hybrid_schema.py --drop inside a temporary container on the milvus network
    docker run --rm \
        --network "${compose_project}_busibox-net" \
        -v "${REPO_ROOT}/provision/ansible/roles/milvus/files/hybrid_schema.py:/app/hybrid_schema.py:ro" \
        -e MILVUS_HOST=milvus \
        -e MILVUS_PORT=19530 \
        -e EMBEDDING_DIMENSION="${dim}" \
        python:3.11-slim \
        bash -c "pip install -q pymilvus>=2.5.0 && python /app/hybrid_schema.py --drop"
}

_reset_proxmox() {
    local env="$1"
    local dim="$2"

    # Determine milvus IP from environment
    local network_base
    case "$env" in
        production) network_base="10.96.200" ;;
        staging)    network_base="10.96.201" ;;
        *)          network_base="10.96.201" ;;
    esac
    local milvus_ip="${network_base}.204"

    info "Dropping and recreating Milvus collection on ${milvus_ip}..."

    # Copy the latest hybrid_schema.py and run with --drop
    scp -o StrictHostKeyChecking=no \
        "${REPO_ROOT}/provision/ansible/roles/milvus/files/hybrid_schema.py" \
        "root@${milvus_ip}:/root/hybrid_schema.py"

    ssh -o StrictHostKeyChecking=no "root@${milvus_ip}" \
        "MILVUS_HOST=localhost MILVUS_PORT=19530 EMBEDDING_DIMENSION=${dim} /opt/milvus-tools/bin/python /root/hybrid_schema.py --drop"
}

main "$@"
