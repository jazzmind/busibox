#!/usr/bin/env bash
# Setup Busibox MCP App Builder Server for Cursor
# Run from busibox root: make mcp  (builds all) or: cd tools/mcp-app-builder && bash setup.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "Building mcp-shared..."
(cd "$REPO_ROOT/tools/mcp-shared" && npm install && npm run build)
echo "Building mcp-app-builder..."
(cd "$SCRIPT_DIR" && npm install && npm run build)

echo ""
echo "Add to Cursor MCP settings:"
echo '  "busibox-app-builder": { "command": "node", "args": ["'$SCRIPT_DIR/dist/index.js'"] }'
echo ""
