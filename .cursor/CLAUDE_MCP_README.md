# Claude Desktop MCP Setup

To add Busibox MCP servers to Claude Desktop:

1. Open `claude-mcp.json` and replace `__BUSIBOX_ROOT__` with your busibox path (e.g. `/path/to/busibox`).
2. Copy the `mcpServers` object into your Claude config:
   - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
   - **Linux:** `~/.config/Claude/claude_desktop_config.json`
3. Merge the servers into the existing `mcpServers` object (or replace if empty).
4. Restart Claude Desktop.
