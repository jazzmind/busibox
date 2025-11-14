# Busibox MCP Server

**Created**: 2025-11-06
**Status**: Active
**Category**: Tools

A Model Context Protocol (MCP) server that provides AI coding agents and maintainers with easy access to Busibox documentation, scripts, and project structure.

## Overview

This MCP server exposes Busibox's organizational structure through a standardized protocol that AI coding assistants (like Claude, Cursor, etc.) can use to:

- **Browse documentation** by category
- **Search documentation** by keyword
- **Get script information** including purpose, usage, and execution context
- **Find scripts** by execution context or purpose
- **Access organization rules** and best practices
- **Get guided assistance** for common tasks

## Features

### Resources

The server exposes the following resources:

- `busibox://docs/{category}` - Browse documentation by category
  - architecture
  - deployment
  - configuration
  - troubleshooting
  - reference
  - guides
  - session-notes
- `busibox://docs/all` - Complete documentation index
- `busibox://scripts/index` - Index of all scripts by execution context
- `busibox://rules` - Project organization rules
- `busibox://architecture` - Main architecture document
- `busibox://quickstart` - Quick start guide (CLAUDE.md)

### Tools

The server provides the following tools:

1. **search_docs** - Search documentation by keyword
   - Parameters: `query` (required), `category` (optional)
   - Returns: Matching files with context

2. **get_script_info** - Get detailed script information
   - Parameters: `script_path` (required)
   - Returns: Purpose, context, privileges, dependencies, usage

3. **find_scripts** - Find scripts by context or purpose
   - Parameters: `context` (required), `purpose` (optional)
   - Returns: List of matching scripts

4. **get_doc** - Get full content of a documentation file
   - Parameters: `path` (required)
   - Returns: Complete file content

5. **list_containers** - Get LXC container information
   - Parameters: none
   - Returns: Container IDs, IPs, and purposes

6. **get_deployment_info** - Get deployment configuration
   - Parameters: `environment` (required: test|production)
   - Returns: Environment-specific configuration

### Prompts

Guided assistance for common tasks:

1. **deploy_service** - Guide for deploying a service
2. **troubleshoot_issue** - Guide for troubleshooting issues
3. **add_service** - Guide for adding a new service
4. **create_documentation** - Guide for creating documentation

## Installation

### Prerequisites

- Node.js 18 or higher
- npm or yarn
- Access to the Busibox project directory

### Build the Server

```bash
cd tools/mcp-server
npm install
npm run build
```

### Configuration

#### For Claude Desktop

Add to your Claude Desktop configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "busibox": {
      "command": "node",
      "args": [
        "/absolute/path/to/busibox/tools/mcp-server/dist/index.js"
      ]
    }
  }
}
```

#### For Cursor AI

Add to your Cursor MCP settings (Settings > MCP Servers):

```json
{
  "busibox": {
    "command": "node",
    "args": [
      "/absolute/path/to/busibox/tools/mcp-server/dist/index.js"
    ]
  }
}
```

#### For Other MCP Clients

Use the stdio transport with the built server:

```bash
node /path/to/busibox/tools/mcp-server/dist/index.js
```

## Usage Examples

### In Claude or Cursor

Once configured, you can interact with the server naturally:

**Browse documentation:**
```
Show me the architecture documentation
```

**Search for information:**
```
Search documentation for "GPU passthrough"
```

**Get script information:**
```
Tell me about the deploy-ai-portal.sh script
```

**Find deployment scripts:**
```
Show me all admin workstation scripts for deployment
```

**Get deployment guidance:**
```
How do I deploy the agent-lxc service to test?
```

**Troubleshoot issues:**
```
Help me troubleshoot a container issue
```

### Direct MCP Usage

You can also use the MCP inspector or other MCP tools to interact with the server:

```bash
# Install MCP inspector (if not already installed)
npm install -g @modelcontextprotocol/inspector

# Run inspector
mcp-inspector node /path/to/busibox/tools/mcp-server/dist/index.js
```

## Development

### Project Structure

```
mcp-server/
├── src/
│   └── index.ts          # Main server implementation
├── dist/                 # Compiled output (generated)
├── package.json
├── tsconfig.json
└── README.md
```

### Development Workflow

```bash
# Install dependencies
npm install

# Watch mode (rebuilds on changes)
npm run dev

# Build for production
npm run build

# Test the server
node dist/index.js
```

### Adding New Features

1. **Add a new resource:**
   - Update `ListResourcesRequestSchema` handler
   - Update `ReadResourceRequestSchema` handler
   - Document in this README

2. **Add a new tool:**
   - Update `ListToolsRequestSchema` handler
   - Update `CallToolRequestSchema` handler
   - Add input schema validation
   - Document in this README

3. **Add a new prompt:**
   - Update `ListPromptsRequestSchema` handler
   - Update `GetPromptRequestSchema` handler
   - Document in this README

## Architecture

### How It Works

1. **Server Initialization**: Creates an MCP server with stdio transport
2. **Request Handlers**: Registers handlers for resource, tool, and prompt requests
3. **File System Access**: Reads documentation and scripts from the Busibox project
4. **Response Formatting**: Returns data in MCP-compatible format

### Design Decisions

- **Zero Configuration**: Server auto-discovers project root from its own location
- **Read-Only**: Server only reads files, never writes (safe for production)
- **Category-Based**: Follows Busibox organization rules for consistency
- **Context-Aware**: Understands script execution contexts (workstation, host, container)
- **Rich Metadata**: Extracts script headers and documentation metadata

## Troubleshooting

### Server Won't Start

Check that Node.js 18+ is installed:
```bash
node --version
```

Check that the server is built:
```bash
cd tools/mcp-server
npm run build
ls -la dist/index.js
```

### Cannot Find Documentation

Verify the project structure:
```bash
# From busibox root:
ls -la docs/
ls -la scripts/
ls -la provision/pct/
```

The server expects the standard Busibox directory structure.

### Resource Not Found

Make sure you're using the correct resource URI format:
- `busibox://docs/{category}` (not `busibox://docs-{category}`)
- `busibox://scripts/index` (not `busibox://scripts`)

### Tool Returns Empty Results

Check the search parameters:
- For `search_docs`: Ensure query is not empty
- For `find_scripts`: Verify context is valid (admin-workstation, proxmox-host, container, all)
- For `get_script_info`: Use path relative to project root

## Integration with Cursor Rules

This MCP server implements and enforces the Busibox organization rules:

- **Documentation Organization** (`.cursor/rules/001-documentation-organization.md`)
  - Exposes docs by category
  - Validates paths follow kebab-case
  - Extracts metadata from documents

- **Script Organization** (`.cursor/rules/002-script-organization.md`)
  - Categorizes scripts by execution context
  - Extracts script header information
  - Helps locate scripts by purpose

## Contributing

When making changes to the MCP server:

1. Follow TypeScript best practices
2. Add error handling for all file operations
3. Update this README with new features
4. Test with at least one MCP client (Claude, Cursor, or inspector)
5. Ensure backward compatibility with existing resources/tools

## Related Documentation

- [MCP Specification](https://modelcontextprotocol.io/)
- [MCP SDK Documentation](https://github.com/modelcontextprotocol/sdk)
- [Busibox Organization Rules](../../.cursor/rules/)
- [Busibox Architecture](../../docs/architecture/architecture.md)
- [CLAUDE.md](../../CLAUDE.md) - Busibox quick start guide

## License

Part of the Busibox project. See project root for license information.





