#!/usr/bin/env bash
#
# Busibox MCP Server Management Script
#
# EXECUTION CONTEXT: Any (admin workstation or Proxmox host)
# PURPOSE: Build and manage the Busibox MCP server for Cursor AI integration
#
# USAGE:
#   make mcp
#   OR
#   bash scripts/mcp.sh
#
set -euo pipefail

# Get script directory and repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MCP_SERVERS=("mcp-shared" "mcp-core-dev" "mcp-app-builder" "mcp-admin")

# Source UI library (scripts/lib/ui.sh)
source "${SCRIPT_DIR}/../lib/ui.sh"

# Non-interactive: skip welcome, go straight to build
if [[ "${MCP_BUILD:-0}" != "1" ]] && [[ "${1:-}" != "build" ]]; then
    clear
    box "Busibox MCP Server" 70
    echo ""
    info "MCP (Model Context Protocol) server provides structured access to"
    info "Busibox documentation and scripts for Cursor AI and Claude Desktop."
    echo ""
fi

# Check if tools directory exists
if [[ ! -d "${REPO_ROOT}/tools/mcp-shared" ]]; then
    error "MCP shared directory not found: ${REPO_ROOT}/tools/mcp-shared"
    exit 1
fi

# Check if Node.js is installed
check_nodejs() {
    if ! command -v node &>/dev/null; then
        error "Node.js is not installed"
        echo ""
        info "Install Node.js from: https://nodejs.org/"
        return 1
    fi
    
    local node_version=$(node --version | sed 's/v//')
    local major_version=$(echo "$node_version" | cut -d. -f1)
    
    if [[ $major_version -lt 18 ]]; then
        error "Node.js version $node_version is too old (requires >= 18.0.0)"
        return 1
    fi
    
    success "Node.js $node_version detected"
    return 0
}

# Build all MCP servers (shared first, then dependent servers)
build_mcp() {
    header "Building MCP Servers" 70
    
    for mcp in "${MCP_SERVERS[@]}"; do
        local mcp_dir="${REPO_ROOT}/tools/${mcp}"
        if [[ ! -d "$mcp_dir" ]]; then
            error "MCP directory not found: $mcp_dir"
            return 1
        fi
        
        echo ""
        info "Building ${mcp}..."
        cd "$mcp_dir"
        npm install || {
            error "Failed to install dependencies for ${mcp}"
            cd "$REPO_ROOT"
            return 1
        }
        npm run build || {
            error "Failed to build ${mcp}"
            cd "$REPO_ROOT"
            return 1
        }
    done
    
    cd "$REPO_ROOT"
    
    cd "$REPO_ROOT"
    
    echo ""
    success "All MCP servers built successfully!"
    
    # Write config files for Cursor and Claude
    write_config
    return 0
}

# Write MCP config to .cursor/ for Cursor (project-level) and Claude (template)
# Idempotent: uses atomic writes; skips claude-mcp.json if user has customized it
write_config() {
    local cursor_dir="${REPO_ROOT}/.cursor"
    mkdir -p "$cursor_dir"
    
    # Cursor: .cursor/mcp.json (atomic write - safe for repeat runs)
    local mcp_tmp
    mcp_tmp=$(mktemp)
    cat > "$mcp_tmp" << 'MCPJSON'
{
  "mcpServers": {
    "busibox-core-dev": {
      "command": "node",
      "args": ["tools/mcp-core-dev/dist/index.js"]
    },
    "busibox-app-builder": {
      "command": "node",
      "args": ["tools/mcp-app-builder/dist/index.js"]
    },
    "busibox-admin": {
      "command": "node",
      "args": ["tools/mcp-admin/dist/index.js"]
    }
  }
}
MCPJSON
    mv "$mcp_tmp" "${cursor_dir}/mcp.json"
    
    # Claude: .cursor/claude-mcp.json - only write if missing or still has placeholder
    # (user may have replaced __BUSIBOX_ROOT__ with their path - don't overwrite)
    local claude_file="${cursor_dir}/claude-mcp.json"
    local wrote_claude=""
    if [[ ! -f "$claude_file" ]] || grep -q '__BUSIBOX_ROOT__' "$claude_file" 2>/dev/null; then
        local claude_tmp
        claude_tmp=$(mktemp)
        cat > "$claude_tmp" << 'CLAUDEJSON'
{
  "mcpServers": {
    "busibox-core-dev": {
      "command": "node",
      "args": ["__BUSIBOX_ROOT__/tools/mcp-core-dev/dist/index.js"]
    },
    "busibox-app-builder": {
      "command": "node",
      "args": ["__BUSIBOX_ROOT__/tools/mcp-app-builder/dist/index.js"]
    },
    "busibox-admin": {
      "command": "node",
      "args": ["__BUSIBOX_ROOT__/tools/mcp-admin/dist/index.js"]
    }
  }
}
CLAUDEJSON
        mv "$claude_tmp" "$claude_file"
        wrote_claude="1"
    fi
    
    # Claude instructions (always overwrite - static docs)
    cat > "${cursor_dir}/CLAUDE_MCP_README.md" << 'READMEEOF'
# Claude Desktop MCP Setup

To add Busibox MCP servers to Claude Desktop:

1. Open `claude-mcp.json` and replace `__BUSIBOX_ROOT__` with your busibox path (e.g. `/path/to/busibox`).
2. Copy the `mcpServers` object into your Claude config:
   - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
   - **Linux:** `~/.config/Claude/claude_desktop_config.json`
3. Merge the servers into the existing `mcpServers` object (or replace if empty).
4. Restart Claude Desktop.
READMEEOF
    
    if [[ "${MCP_BUILD:-0}" == "1" ]] || [[ "${1:-}" == "build" ]]; then
        if [[ -n "$wrote_claude" ]]; then
            info "Config written: .cursor/mcp.json (Cursor), .cursor/claude-mcp.json (Claude template)"
        else
            info "Config written: .cursor/mcp.json (Cursor); .cursor/claude-mcp.json preserved (customized)"
        fi
    fi
}

# Clean build artifacts
clean_mcp() {
    header "Cleaning Build Artifacts" 70
    
    for mcp in "${MCP_SERVERS[@]}"; do
        local mcp_dir="${REPO_ROOT}/tools/${mcp}"
        if [[ -d "$mcp_dir" ]]; then
            cd "$mcp_dir"
            if [[ -d "dist" ]]; then
                info "Removing ${mcp}/dist..."
                rm -rf dist
                success "Cleaned ${mcp}/dist/"
            fi
        fi
    done
    
    if confirm "Remove node_modules from all MCP packages? (will require reinstall)" "n"; then
        for mcp in "${MCP_SERVERS[@]}"; do
            local mcp_dir="${REPO_ROOT}/tools/${mcp}"
            if [[ -d "${mcp_dir}/node_modules" ]]; then
                rm -rf "${mcp_dir}/node_modules"
                success "Removed ${mcp}/node_modules/"
            fi
        done
    fi
    
    cd "$REPO_ROOT"
    
    echo ""
    success "Cleanup complete"
    return 0
}

# Show Cursor configuration
show_config() {
    header "Cursor AI Configuration" 70
    
    echo ""
    info "Config is auto-written to .cursor/mcp.json when you build (make mcp)."
    info "Cursor loads it automatically when you open this project."
    echo ""
    separator 70
    echo ""
    info "For Claude Desktop: see .cursor/CLAUDE_MCP_README.md"
    info "Template: .cursor/claude-mcp.json (replace __BUSIBOX_ROOT__)"
    echo ""
    separator 70
    echo ""
    info "Server purposes: core-dev (build/test), app-builder (apps), admin (deploy/manage)"
    echo ""
    
    pause
}

# Install dependencies only
install_deps() {
    header "Installing Dependencies" 70
    
    for mcp in "${MCP_SERVERS[@]}"; do
        local mcp_dir="${REPO_ROOT}/tools/${mcp}"
        if [[ -d "$mcp_dir" ]]; then
            info "Installing ${mcp}..."
            (cd "$mcp_dir" && npm install) || {
                error "Failed to install ${mcp}"
                return 1
            }
        fi
    done
    
    cd "$REPO_ROOT"
    
    echo ""
    success "Dependencies installed!"
    return 0
}

# Main menu
main_menu() {
    while true; do
        echo ""
        menu "MCP Servers Management" \
            "Build MCP Server" \
            "Clean Build Artifacts" \
            "Show Cursor Configuration" \
            "Install Dependencies Only" \
            "Exit"
        
        read -p "$(echo -e "${BOLD}Select option [1-5]:${NC} ")" choice
        
        case $choice in
            1)
                if check_nodejs; then
                    build_mcp
                fi
                pause
                ;;
            2)
                clean_mcp
                pause
                ;;
            3)
                show_config
                ;;
            4)
                if check_nodejs; then
                    install_deps
                fi
                pause
                ;;
            5)
                echo ""
                info "Exiting..."
                exit 0
                ;;
            *)
                error "Invalid selection. Please enter 1-5."
                ;;
        esac
    done
}

# Non-interactive build (e.g. make mcp or MCP_BUILD=1 bash mcp.sh)
if [[ "${MCP_BUILD:-0}" == "1" ]] || [[ "${1:-}" == "build" ]]; then
    if check_nodejs; then
        build_mcp
    fi
    exit $?
fi

# Interactive menu
main_menu

exit 0

