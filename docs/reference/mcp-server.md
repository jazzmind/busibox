# Busibox MCP Server Reference

**Created**: 2025-11-06
**Last Updated**: 2025-11-06
**Status**: Active
**Category**: Reference
**Related Docs**:
- [CLAUDE.md](../../CLAUDE.md)
- [Organization Rules Summary](../ORGANIZATION_RULES_SUMMARY.md)
- [Architecture](../architecture/architecture.md)

## Overview

The Busibox MCP (Model Context Protocol) Server is a local server that provides AI coding agents and maintainers with structured access to Busibox documentation, scripts, and project organization. It implements the Model Context Protocol, allowing AI assistants like Claude, Cursor, and others to browse, search, and understand the Busibox project structure.

## Quick Start

### Installation

```bash
# Navigate to MCP server directory
cd tools/mcp-server

# Install dependencies
npm install

# Build the server
npm run build
```

### Configuration for Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "busibox": {
      "command": "node",
      "args": ["/absolute/path/to/busibox/tools/mcp-server/dist/index.js"]
    }
  }
}
```

### Configuration for Cursor

Add to Cursor MCP settings (Settings > MCP Servers):

```json
{
  "busibox": {
    "command": "node",
    "args": ["/absolute/path/to/busibox/tools/mcp-server/dist/index.js"]
  }
}
```

## Resources

Resources provide browsable access to project content.

### Documentation Resources

| URI | Description |
|-----|-------------|
| `busibox://docs/architecture` | Architecture documentation |
| `busibox://docs/deployment` | Deployment guides |
| `busibox://docs/configuration` | Configuration guides |
| `busibox://docs/troubleshooting` | Troubleshooting guides |
| `busibox://docs/reference` | Reference documentation |
| `busibox://docs/guides` | How-to guides |
| `busibox://docs/session-notes` | Session notes |
| `busibox://docs/all` | Complete documentation index |

### System Resources

| URI | Description |
|-----|-------------|
| `busibox://scripts/index` | Index of all scripts by context |
| `busibox://rules` | Organization rules from `.cursor/rules/` |
| `busibox://architecture` | Main architecture document |
| `busibox://quickstart` | Quick start guide (CLAUDE.md) |

## Tools

Tools provide interactive capabilities for searching and retrieving information.

### search_docs

Search documentation by keyword or phrase.

**Parameters:**
- `query` (string, required): Search query
- `category` (string, optional): Limit to specific category (architecture, deployment, configuration, troubleshooting, reference, guides, session-notes, all)

**Returns:** List of matching files with context snippets

**Example:**
```json
{
  "query": "GPU passthrough",
  "category": "guides"
}
```

### get_script_info

Get detailed information about a specific script.

**Parameters:**
- `script_path` (string, required): Path to script relative to project root

**Returns:** Script metadata including purpose, execution context, privileges, dependencies, and usage

**Example:**
```json
{
  "script_path": "scripts/deploy-ai-portal.sh"
}
```

### find_scripts

Find scripts by execution context or purpose.

**Parameters:**
- `context` (string, required): Execution context (admin-workstation, proxmox-host, container, all)
- `purpose` (string, optional): Filter by purpose (deploy, test, setup, etc.)

**Returns:** List of matching scripts with metadata

**Example:**
```json
{
  "context": "admin-workstation",
  "purpose": "deploy"
}
```

### get_doc

Get the full content of a specific documentation file.

**Parameters:**
- `path` (string, required): Path relative to `docs/` directory

**Returns:** Complete file content

**Example:**
```json
{
  "path": "architecture/architecture.md"
}
```

### list_containers

Get information about LXC containers and their purposes.

**Parameters:** None

**Returns:** List of containers with IDs, IPs, and purposes

**Example:**
```json
{}
```

### get_deployment_info

Get deployment configuration for a specific environment.

**Parameters:**
- `environment` (string, required): Target environment (test, production)

**Returns:** Environment-specific configuration from Ansible inventory

**Example:**
```json
{
  "environment": "test"
}
```

## Prompts

Prompts provide guided assistance for common tasks.

### deploy_service

Interactive guide for deploying a service.

**Arguments:**
- `service` (string, required): Service name (e.g., ai-portal, agent-lxc)
- `environment` (string, required): Target environment (test, production)

**Provides:**
- Prerequisites checklist
- Step-by-step deployment commands
- Validation procedures
- Reference documentation links

### troubleshoot_issue

Interactive guide for troubleshooting issues.

**Arguments:**
- `issue_type` (string, required): Type of issue (deployment, container, service, network)

**Provides:**
- Initial diagnostic steps
- Context-specific troubleshooting commands
- Log inspection procedures
- Next steps and documentation references

### add_service

Interactive guide for adding a new service to Busibox.

**Arguments:**
- `service_name` (string, required): Name of the new service

**Provides:**
- Complete service addition workflow
- Container configuration steps
- Ansible role creation guide
- Documentation requirements

### create_documentation

Interactive guide for creating documentation following organization rules.

**Arguments:**
- `topic` (string, required): Topic to document

**Provides:**
- Category selection guidance
- Filename conventions
- Document structure template
- Organization rules reference

## Usage Patterns

### For AI Coding Agents

AI agents can use the MCP server to:

1. **Understand project structure:**
   ```
   Read busibox://rules
   Read busibox://quickstart
   ```

2. **Find relevant documentation:**
   ```
   Tool: search_docs
   {
     "query": "deployment procedure",
     "category": "deployment"
   }
   ```

3. **Get script information:**
   ```
   Tool: get_script_info
   {
     "script_path": "scripts/test-infrastructure.sh"
   }
   ```

4. **Get guided assistance:**
   ```
   Prompt: deploy_service
   {
     "service": "ai-portal",
     "environment": "test"
   }
   ```

### For Maintainers

Maintainers can use the MCP server through AI assistants to:

1. **Quickly find documentation:**
   - "Show me the GPU passthrough guide"
   - "What's in the troubleshooting docs?"

2. **Understand scripts:**
   - "What does deploy-production.sh do?"
   - "Show me all test scripts"

3. **Get deployment help:**
   - "How do I deploy agent-lxc to production?"
   - "Walk me through adding a new service"

4. **Search for solutions:**
   - "Search docs for SSL certificate errors"
   - "Find troubleshooting guides for nginx"

## Implementation Details

### Project Root Detection

The server automatically detects the Busibox project root by navigating up from its own location:

```
dist/index.js -> mcp-server -> tools -> busibox
```

This allows the server to work regardless of where it's executed from.

### File System Access

The server has read-only access to:
- `docs/` - All documentation
- `scripts/` - Admin workstation scripts
- `provision/pct/` - Proxmox host scripts
- `provision/ansible/` - Ansible roles and scripts
- `.cursor/rules/` - Organization rules
- Project root files (CLAUDE.md, TESTING.md, etc.)

### Script Header Parsing

The server extracts information from script headers following the organization rules:
- Purpose
- Execution Context
- Required Privileges
- Dependencies
- Usage examples

### Search Implementation

Documentation search:
- Case-insensitive keyword matching
- Context extraction (line before and after match)
- Limited to 5 matches per file
- Category filtering support

## Performance Considerations

- **Lazy Loading**: Documentation is read on-demand, not cached
- **Recursive Scanning**: Directory listing uses glob patterns for efficiency
- **Memory Efficient**: Large files are read in streams where possible
- **Fast Startup**: Minimal initialization, no database connections

## Security

- **Read-Only**: Server never writes files
- **Local Access**: Runs locally, no network exposure
- **Sandboxed**: Can only access files within project root
- **No Secrets**: Does not read or expose Ansible vault contents

## Troubleshooting

### Server Not Starting

**Symptom:** Server fails to start or crashes immediately

**Solutions:**
1. Verify Node.js version: `node --version` (requires 18+)
2. Rebuild: `npm run build`
3. Check logs: Look for error messages in console
4. Verify project structure: Ensure running from correct directory

### Cannot Find Resources

**Symptom:** "Resource not found" errors

**Solutions:**
1. Verify URI format: `busibox://docs/architecture` not `busibox://docs-architecture`
2. Check project structure: Ensure `docs/` directory exists
3. Verify category name: Must match one of the defined categories

### Search Returns No Results

**Symptom:** `search_docs` returns empty results

**Solutions:**
1. Check query spelling
2. Try broader search terms
3. Search in "all" category instead of specific one
4. Verify documentation files exist

### Script Info Not Found

**Symptom:** `get_script_info` returns "Script not found"

**Solutions:**
1. Use path relative to project root: `scripts/deploy-ai-portal.sh`
2. Verify script exists: Check file system
3. Check for typos in path

## Development

### Adding New Resources

1. Add to `ListResourcesRequestSchema` handler
2. Implement reading logic in `ReadResourceRequestSchema` handler
3. Document in README and this reference

### Adding New Tools

1. Add to `ListToolsRequestSchema` handler
2. Define input schema
3. Implement logic in `CallToolRequestSchema` handler
4. Add error handling
5. Document in README and this reference

### Adding New Prompts

1. Add to `ListPromptsRequestSchema` handler
2. Define arguments
3. Implement conversation in `GetPromptRequestSchema` handler
4. Document in README and this reference

## Related Specifications

- [MCP Protocol](https://modelcontextprotocol.io/docs/specification/protocol) - Official protocol specification
- [MCP SDK](https://github.com/modelcontextprotocol/sdk) - TypeScript SDK documentation
- [Busibox Organization Rules](../../.cursor/rules/) - Project organization rules

## API Reference

### Server Information

- **Name**: `busibox-mcp-server`
- **Version**: `1.0.0`
- **Capabilities**: resources, tools, prompts
- **Transport**: stdio

### Error Handling

All tools and resources return appropriate error messages:

```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "Documentation not found: invalid/path.md"
  }
}
```

Common error codes:
- `RESOURCE_NOT_FOUND` - Requested resource doesn't exist
- `TOOL_ERROR` - Tool execution failed
- `INVALID_PARAMS` - Invalid parameters provided
- `FILE_READ_ERROR` - Cannot read file from filesystem

## Best Practices

### For AI Agents

1. **Start with quickstart**: Read `busibox://quickstart` first
2. **Check organization rules**: Read `busibox://rules` to understand structure
3. **Search before asking**: Use `search_docs` to find existing documentation
4. **Use prompts for guidance**: Prompts provide step-by-step guidance for common tasks

### For Maintainers

1. **Keep documentation updated**: Server reads from filesystem, so keep docs current
2. **Follow naming conventions**: Server expects kebab-case filenames
3. **Update script headers**: Server extracts metadata from headers
4. **Document new services**: Add documentation when adding services

## Version History

- **1.0.0** (2025-11-06): Initial release
  - Documentation resources
  - Script search and information
  - Common task prompts
  - Organization rules access

## Support

For issues or questions:
1. Check this reference document
2. Review [README](../../tools/mcp-server/README.md)
3. Check [CLAUDE.md](../../CLAUDE.md) for project overview
4. Review organization rules in `.cursor/rules/`

## License

Part of the Busibox project. See project root for license information.








