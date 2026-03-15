#!/usr/bin/env bash
#
# Prerequisite detection and installation helpers
#
# Provides ensure_* functions that check for required tools and offer
# to install them automatically when possible. Each function is
# idempotent — it's safe to call multiple times.
#
# Usage:
#   source scripts/lib/prereqs.sh
#   ensure_ansible   # exits 1 if not installed and user declines
#

# Guard against double-sourcing
[[ -n "${_PREREQS_SH_LOADED:-}" ]] && return 0
_PREREQS_SH_LOADED=1

# Requires ui.sh for info/success/warn/error/confirm
if ! declare -f info &>/dev/null; then
    echo "[prereqs.sh] ERROR: ui.sh must be sourced before prereqs.sh" >&2
    return 1
fi

# =============================================================================
# Ansible
# =============================================================================

# Detect the best installation method for Ansible on this system.
# Returns one of: pipx, pip3, brew, apt, dnf, ""
_ansible_install_method() {
    if command -v pipx &>/dev/null; then
        echo "pipx"
    elif command -v pip3 &>/dev/null; then
        echo "pip3"
    elif command -v brew &>/dev/null; then
        echo "brew"
    elif command -v apt-get &>/dev/null; then
        echo "apt"
    elif command -v dnf &>/dev/null; then
        echo "dnf"
    else
        echo ""
    fi
}

# Install Ansible using the detected method.
# Returns 0 on success, 1 on failure.
_install_ansible() {
    local method="$1"

    case "$method" in
        pipx)
            info "Installing Ansible via pipx..."
            pipx install --include-deps ansible
            ;;
        pip3)
            info "Installing Ansible via pip3..."
            pip3 install --user ansible
            ;;
        brew)
            info "Installing Ansible via Homebrew..."
            brew install ansible
            ;;
        apt)
            info "Installing Ansible via apt..."
            sudo apt-get update -qq && sudo apt-get install -y ansible
            ;;
        dnf)
            info "Installing Ansible via dnf..."
            sudo dnf install -y ansible
            ;;
        *)
            return 1
            ;;
    esac
}

# Ensure ansible-playbook is available. If not, offer to install it.
#
# Args:
#   $1 - "quiet" to skip the success message when already installed (optional)
#
# Returns 0 if available (or just installed), exits 1 if unavailable
# and the user declined.
ensure_ansible() {
    local quiet="${1:-}"

    if command -v ansible-playbook &>/dev/null; then
        [[ "$quiet" != "quiet" ]] && success "Ansible available ($(ansible --version 2>/dev/null | head -1 | awk '{print $NF}'))"
        return 0
    fi

    echo ""
    warn "Ansible is not installed."
    echo ""

    local method
    method=$(_ansible_install_method)

    if [[ -z "$method" ]]; then
        error "Could not detect a package manager to install Ansible."
        echo ""
        info "Install manually:"
        info "  macOS:   brew install ansible"
        info "  Ubuntu:  sudo apt install ansible"
        info "  Fedora:  sudo dnf install ansible"
        info "  pip:     pip3 install --user ansible"
        info "  pipx:    pipx install --include-deps ansible"
        echo ""
        return 1
    fi

    info "Detected install method: ${BOLD}${method}${NC}"

    if confirm "Install Ansible now via ${method}?" "y"; then
        if _install_ansible "$method"; then
            # Rehash so the new binary is found
            hash -r 2>/dev/null || true

            if command -v ansible-playbook &>/dev/null; then
                echo ""
                success "Ansible installed successfully"
                return 0
            else
                echo ""
                error "Ansible was installed but ansible-playbook is not on PATH."
                info "If installed via pip3 --user, add ~/.local/bin to your PATH:"
                info '  export PATH="$HOME/.local/bin:$PATH"'
                return 1
            fi
        else
            error "Ansible installation failed."
            return 1
        fi
    else
        echo ""
        info "Install manually and try again:"
        info "  macOS:   brew install ansible"
        info "  Ubuntu:  sudo apt install ansible"
        info "  pip:     pip3 install --user ansible"
        return 1
    fi
}
