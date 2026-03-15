#!/usr/bin/env bash
#
# Busibox CLI Launcher
#
# EXECUTION CONTEXT: Admin workstation (macOS or Linux)
# PURPOSE: Build and run the Busibox Rust TUI, with prerequisite checks
#          and automatic Rust installation if needed.
#
# USAGE:
#   bash scripts/make/busibox-cli.sh          # Build (if needed) and run
#   bash scripts/make/busibox-cli.sh build    # Build only
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLI_DIR="${REPO_ROOT}/cli/busibox"
BINARY="${CLI_DIR}/target/release/busibox"
ACTION="${1:-run}"

source "${SCRIPT_DIR}/../lib/ui.sh"

# Load cargo env if it exists (rustup installs here)
load_cargo_env() {
    if [[ -f "$HOME/.cargo/env" ]]; then
        source "$HOME/.cargo/env"
    fi
}

# Check if cargo is available (after loading env)
has_cargo() {
    load_cargo_env
    command -v cargo &>/dev/null
}

# Check if the existing binary is up to date vs source files
binary_is_current() {
    [[ -f "$BINARY" ]] || return 1

    # Compare binary mtime against all .rs and Cargo.toml files
    local src_files
    src_files=$(find "${CLI_DIR}/src" -name '*.rs' -newer "$BINARY" 2>/dev/null)
    if [[ -n "$src_files" ]]; then
        return 1
    fi
    if [[ "${CLI_DIR}/Cargo.toml" -nt "$BINARY" ]]; then
        return 1
    fi

    return 0
}

# Attempt to install Rust via rustup
install_rust() {
    echo ""
    info "Rust is required to build the Busibox CLI."
    info "The standard installer (rustup) will set up Rust in ~/.cargo."
    echo ""

    if confirm "Install Rust now via rustup?" "y"; then
        echo ""
        info "Installing Rust..."
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
        load_cargo_env

        if command -v cargo &>/dev/null; then
            echo ""
            success "Rust installed successfully ($(rustc --version))"
            return 0
        else
            error "Rust installation completed but cargo is not on PATH."
            error "Try opening a new terminal and running 'make busibox' again."
            return 1
        fi
    else
        echo ""
        info "To install Rust manually:"
        info "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
        info ""
        info "Then run 'make busibox' again."
        return 1
    fi
}

# Build the CLI binary
build_cli() {
    if binary_is_current; then
        return 0
    fi

    if ! has_cargo; then
        # No cargo and no current binary — need to install Rust
        if [[ -f "$BINARY" ]]; then
            warn "Rust/cargo not found. Using existing binary (may be outdated)."
            warn "Install Rust to build from latest source: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
            return 0
        fi

        install_rust || return 1
    fi

    info "Building Busibox CLI..."
    (cd "$CLI_DIR" && CARGO_TARGET_DIR=target cargo build --release) || {
        error "Build failed."
        return 1
    }
    success "Built: ${BINARY}"
    return 0
}

case "$ACTION" in
    build)
        build_cli
        ;;
    run)
        build_cli || exit 1
        if [[ ! -f "$BINARY" ]]; then
            error "No binary found at ${BINARY}"
            error "Rust is required to build the CLI. Install with:"
            error "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
            exit 1
        fi
        exec "$BINARY"
        ;;
    *)
        error "Unknown action: $ACTION (use 'build' or 'run')"
        exit 1
        ;;
esac
