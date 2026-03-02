#!/usr/bin/env bash
# =============================================================================
# Certificate Setup Menu
# =============================================================================
#
# Execution Context:
# - Admin workstation (interactive launcher menu)
#
# Purpose:
# - Install/generate localhost HTTPS certs using mkcert when possible
# - Optionally run the same setup on a target Proxmox host over SSH
#
# Usage:
#   bash scripts/make/cert-setup.sh [--env <environment>] [--backend <backend>]
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${REPO_ROOT}/scripts/lib/ui.sh"

ENVIRONMENT=""
BACKEND=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)
            ENVIRONMENT="${2:-}"
            shift 2
            ;;
        --backend)
            BACKEND="${2:-}"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

if [[ -z "${ENVIRONMENT}" ]]; then
    ENVIRONMENT="development"
fi

if [[ -z "${BACKEND}" ]]; then
    BACKEND="docker"
fi

_inventory_for_env() {
    case "$ENVIRONMENT" in
        production) echo "production" ;;
        staging) echo "staging" ;;
        *) echo "staging" ;;
    esac
}

_resolve_proxmox_host() {
    if [[ -n "${PROXMOX_HOST:-}" ]]; then
        echo "${PROXMOX_HOST}"
        return 0
    fi

    local inv
    inv="$(_inventory_for_env)"
    local vars_file="${REPO_ROOT}/provision/ansible/inventory/${inv}/group_vars/all/00-main.yml"
    if [[ -f "${vars_file}" ]]; then
        local host_line
        host_line="$(awk '/^proxmox_host:/ {print $2; exit}' "${vars_file}" 2>/dev/null || true)"
        if [[ -n "${host_line}" && "${host_line}" != *"{{"* ]]; then
            echo "${host_line}"
            return 0
        fi
    fi

    echo "proxmox"
}

install_local_cert() {
    echo ""
    info "Installing certificate on this machine..."
    bash "${REPO_ROOT}/scripts/setup/generate-local-ssl.sh"
    echo ""
    success "Local certificate setup complete."
    info "Restart your browser if it was already open."
}

install_remote_cert() {
    echo ""

    if [[ "${BACKEND}" != "proxmox" ]]; then
        warn "Target-host certificate install is primarily for Proxmox profiles."
        warn "Current backend: ${BACKEND}"
        return 1
    fi

    local default_host
    default_host="$(_resolve_proxmox_host)"
    read -r -p "Target host [${default_host}]: " target_host
    target_host="${target_host:-${default_host}}"

    if [[ -z "${target_host}" ]]; then
        error "No target host provided."
        return 1
    fi

    info "Connecting to ${target_host}..."
    if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "root@${target_host}" "echo connected" >/dev/null 2>&1; then
        error "SSH connection failed: root@${target_host}"
        info "Verify SSH access and try again."
        return 1
    fi

    info "Installing certificate on target host ${target_host}..."
    local remote_cmd
    remote_cmd='set -euo pipefail
REPO=""
for p in /root/busibox /srv/busibox /opt/busibox; do
  if [ -f "$p/scripts/setup/generate-local-ssl.sh" ]; then
    REPO="$p"
    break
  fi
done
if [ -z "$REPO" ]; then
  echo "Could not find busibox repo on target host."
  exit 1
fi
bash "$REPO/scripts/setup/generate-local-ssl.sh"'

    if ! ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "root@${target_host}" "${remote_cmd}"; then
        error "Target host certificate setup failed."
        return 1
    fi

    echo ""
    success "Target host certificate setup complete."
}

menu() {
    while true; do
        clear
        box_start 70 double "${CYAN}"
        box_header "HTTPS CERTIFICATE SETUP"
        box_empty
        box_line "  ${BOLD}1)${NC} Install on management machine"
        box_line "  ${BOLD}2)${NC} Install on target host (Proxmox)"
        box_line "  ${BOLD}3)${NC} Install on both"
        box_empty
        box_line "  ${DIM}b = back${NC}"
        box_footer
        echo ""
        read -r -p "Select option: " choice

        case "${choice}" in
            1)
                install_local_cert || true
                echo ""
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            2)
                install_remote_cert || true
                echo ""
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            3)
                install_local_cert || true
                install_remote_cert || true
                echo ""
                read -n 1 -s -r -p "Press any key to continue..."
                ;;
            b|B)
                return 0
                ;;
            *)
                ;;
        esac
    done
}

menu
