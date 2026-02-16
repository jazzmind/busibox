#!/usr/bin/env bash
#
# Busibox MCP Server Setup
#
# Purpose: Install and configure the Busibox MCP server for Cursor
#
# Execution Context: Admin Workstation
# Required Privileges: user
# Dependencies: node, npm
#
# Usage:
#   bash setup.sh
#
# Examples:
#   bash setup.sh                    # Install and show Cursor config
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Check Node.js
info "Checking Node.js installation..."
if ! command -v node &> /dev/null; then
    error "Node.js not found. Please install Node.js 18 or higher."
    exit 1
fi

NODE_VERSION=$(node --version | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
    error "Node.js version 18 or higher required. Current: $(node --version)"
    exit 1
fi

success "Node.js $(node --version) detected"

# Install dependencies
info "Installing dependencies..."
cd "$SCRIPT_DIR"
npm install

# Build server
info "Building MCP server..."
npm run build

if [ ! -f "$SCRIPT_DIR/dist/index.js" ]; then
    error "Build failed - dist/index.js not found"
    exit 1
fi

success "MCP server built successfully"

# Get absolute path to built server
SERVER_PATH="$SCRIPT_DIR/dist/index.js"

# Display Cursor configuration
info "Configuring Cursor..."

cat << EOF

${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}
${GREEN}Cursor Configuration${NC}
${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}

To add the Busibox MCP server to Cursor:

${BLUE}Method 1: Cursor Settings UI${NC}
  1. Open Cursor Settings
  2. Navigate to: Features > MCP Servers
  3. Click "Add Server"
  4. Add the following configuration:

${YELLOW}Server Name:${NC} busibox
${YELLOW}Command:${NC} node
${YELLOW}Args:${NC} $SERVER_PATH

${BLUE}Method 2: Edit Settings JSON${NC}
  Add this to your Cursor settings JSON:

{
  "mcpServers": {
    "busibox": {
      "command": "node",
      "args": ["$SERVER_PATH"]
    }
  }
}

${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}

EOF

success "Server built successfully!"

# Display usage information
cat << EOF

${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}
${GREEN}MCP Server Setup Complete!${NC}
${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}

Server Location: $SERVER_PATH

${BLUE}Quick Test:${NC}
  node "$SERVER_PATH"

${BLUE}Example Usage in Cursor:${NC}
  "Show me the architecture documentation"
  "Search docs for GPU passthrough"
  "Tell me about deploy-busibox-portal.sh"
  "How do I deploy agent-lxc to test?"

${BLUE}Available Resources:${NC}
  busibox://docs/{category}    - Browse documentation
  busibox://scripts/index      - List all scripts
  busibox://rules              - Organization rules
  busibox://architecture       - Main architecture doc
  busibox://quickstart         - Quick start guide

${BLUE}Available Tools:${NC}
  search_docs                  - Search documentation
  get_script_info              - Get script details
  find_scripts                 - Find scripts by context
  get_doc                      - Get full document
  list_containers              - List LXC containers
  get_deployment_info          - Get deployment config

${BLUE}Available Prompts:${NC}
  deploy_service               - Deployment guide
  troubleshoot_issue           - Troubleshooting guide
  add_service                  - Add new service guide
  create_documentation         - Documentation guide

${BLUE}Documentation:${NC}
  README: $SCRIPT_DIR/README.md
  Reference: $PROJECT_ROOT/docs/reference/mcp-server.md

${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}

EOF

success "Setup complete! 🎉"



