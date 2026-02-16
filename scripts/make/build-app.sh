#!/usr/bin/env bash
#
# Build App - Clone busibox-template and install MCP app-builder
#
# EXECUTION CONTEXT: Admin workstation
# PURPOSE: Clone busibox-template into peer dir, add busibox-app-builder MCP to that project
#
# USAGE:
#   bash scripts/make/build-app.sh
#   (or via launcher: Build App)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_TEMPLATE_REPO="https://github.com/jazzmind/busibox-template.git"
PARENT_DIR="$(cd "${REPO_ROOT}/.." && pwd)"
APP_TEMPLATE_DIR="${PARENT_DIR}/busibox-template"

source "${SCRIPT_DIR}/../lib/ui.sh"

# Check if MCP is installed (mcp-app-builder built)
check_mcp_installed() {
    [[ -f "${REPO_ROOT}/tools/mcp-app-builder/dist/index.js" ]]
}

# Install MCP app-builder config into busibox-template's .cursor/mcp.json
# Path from busibox-template to busibox: ../<busibox-dir> (sibling)
install_mcp_in_app_template() {
    local cursor_dir="${APP_TEMPLATE_DIR}/.cursor"
    mkdir -p "$cursor_dir"
    
    local busibox_dirname
    busibox_dirname=$(basename "$REPO_ROOT")
    local mcp_path="../${busibox_dirname}/tools/mcp-app-builder/dist/index.js"
    
    local mcp_file="${cursor_dir}/mcp.json"
    local mcp_tmp
    mcp_tmp=$(mktemp)
    
    # Write config with relative path from busibox-template to busibox
    cat > "$mcp_tmp" << MCPJSON
{
  "mcpServers": {
    "busibox-app-builder": {
      "command": "node",
      "args": ["${mcp_path}"]
    }
  }
}
MCPJSON
    mv "$mcp_tmp" "$mcp_file"
    success "Installed busibox-app-builder MCP in busibox-template/.cursor/mcp.json"
}

main() {
    header "Build App" 70
    echo ""
    
    # 1. Check MCP installed
    if ! check_mcp_installed; then
        warn "MCP server not installed. Build App requires the app-builder MCP server."
        echo ""
        read -r -p "Install MCP now? [Y/n]: " install_choice
        if [[ "${install_choice:-y}" =~ ^[nN] ]]; then
            info "Cancelled. Run 'make mcp' or select 'Install MCP locally' from the menu first."
            return 1
        fi
        echo ""
        MCP_BUILD=1 bash "${SCRIPT_DIR}/mcp.sh" build || return 1
        echo ""
    fi
    
    # 2. Clone or update busibox-template
    if [[ -d "$APP_TEMPLATE_DIR" ]]; then
        info "busibox-template already exists at: $APP_TEMPLATE_DIR"
        read -r -p "Pull latest? [Y/n]: " pull_choice
        if [[ "${pull_choice:-y}" =~ ^[yY] ]] || [[ -z "${pull_choice}" ]]; then
            (cd "$APP_TEMPLATE_DIR" && git pull origin main 2>/dev/null) || true
        fi
    else
        info "Cloning busibox-template into: $APP_TEMPLATE_DIR"
        if ! git clone "$APP_TEMPLATE_REPO" "$APP_TEMPLATE_DIR"; then
            error "Failed to clone busibox-template. Check network and repo access."
            return 1
        fi
        success "Cloned busibox-template"
    fi
    
    # 3. Install MCP app-builder config into busibox-template
    echo ""
    install_mcp_in_app_template
    
    echo ""
    success "Build App setup complete!"
    info "Open busibox-template in Cursor: $APP_TEMPLATE_DIR"
    info "The busibox-app-builder MCP server will be available for app development."
    echo ""
}

main "$@"
