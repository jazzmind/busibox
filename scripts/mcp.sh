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

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MCP_DIR="${REPO_ROOT}/tools/mcp-server"

# Source UI library
source "${SCRIPT_DIR}/lib/ui.sh"

# Display welcome
clear
box "Busibox MCP Server" 70
echo ""
info "MCP (Model Context Protocol) server provides structured access to"
info "Busibox documentation and scripts for Cursor AI and Claude Desktop."
echo ""

# Check if MCP directory exists
if [[ ! -d "$MCP_DIR" ]]; then
    error "MCP server directory not found: $MCP_DIR"
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

# Build MCP server
build_mcp() {
    header "Building MCP Server" 70
    
    cd "$MCP_DIR"
    
    info "Installing dependencies..."
    npm install || {
        error "Failed to install dependencies"
        return 1
    }
    
    echo ""
    info "Building TypeScript..."
    npm run build || {
        error "Failed to build MCP server"
        return 1
    }
    
    cd "$REPO_ROOT"
    
    echo ""
    success "MCP server built successfully!"
    return 0
}

# Clean build artifacts
clean_mcp() {
    header "Cleaning Build Artifacts" 70
    
    cd "$MCP_DIR"
    
    if [[ -d "dist" ]]; then
        info "Removing dist directory..."
        rm -rf dist
        success "Cleaned dist/"
    else
        info "No dist directory to clean"
    fi
    
    if [[ -d "node_modules" ]]; then
        if confirm "Remove node_modules? (will require reinstall)" "n"; then
            info "Removing node_modules..."
            rm -rf node_modules
            success "Cleaned node_modules/"
        else
            info "Keeping node_modules/"
        fi
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
    info "To use the MCP server with Cursor AI, add this to your Cursor settings:"
    echo ""
    separator 70
    echo ""
    echo -e "${CYAN}File: .cursorrules or Cursor Settings${NC}"
    echo ""
    cat << 'EOF'
{
  "mcpServers": {
    "busibox": {
      "command": "node",
      "args": ["/path/to/busibox/tools/mcp-server/dist/index.js"]
    }
  }
}
EOF
    echo ""
    separator 70
    echo ""
    info "Replace /path/to/busibox with your actual busibox repository path:"
    echo "  ${CYAN}${REPO_ROOT}${NC}"
    echo ""
    info "After configuring, restart Cursor AI to load the MCP server"
    echo ""
    
    pause
}

# Install dependencies only
install_deps() {
    header "Installing Dependencies" 70
    
    cd "$MCP_DIR"
    
    info "Installing npm packages..."
    npm install || {
        error "Failed to install dependencies"
        return 1
    }
    
    cd "$REPO_ROOT"
    
    echo ""
    success "Dependencies installed!"
    return 0
}

# Main menu
main_menu() {
    while true; do
        echo ""
        menu "MCP Server Management" \
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

# Run main menu
main_menu

exit 0

