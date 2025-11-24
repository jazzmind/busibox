# Busibox MCP Server Usage Guide

**Created**: 2025-11-06
**Last Updated**: 2025-11-06
**Status**: Active
**Category**: Guide
**Related Docs**:
- [MCP Server Reference](../reference/mcp-server.md)
- [CLAUDE.md](../../CLAUDE.md)
- [Organization Rules](../../.cursor/rules/)

## Overview

This guide shows you how to effectively use the Busibox MCP Server with AI coding assistants. The MCP server provides structured access to all Busibox documentation, scripts, and project organization.

## Prerequisites

- Node.js 18 or higher installed
- Claude Desktop or Cursor (or another MCP-compatible client)
- Access to the Busibox project directory

## Installation

### Quick Setup (Recommended)

Using the interactive menu:

```bash
make mcp
```

This provides an interactive menu to:
1. Build the MCP server
2. Install dependencies
3. Show Cursor configuration
4. Clean build artifacts

See [Interactive Commands Guide](interactive-commands.md) for details.

### Direct Setup

```bash
cd tools/mcp-server
bash setup.sh
```

This will:
1. Install dependencies
2. Build the server
3. Configure Claude Desktop and/or Cursor
4. Display usage information

### Manual Setup

If you prefer to set up manually:

```bash
# Install and build
cd tools/mcp-server
npm install
npm run build

# Add to Claude Desktop config
# Edit: ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "busibox": {
      "command": "node",
      "args": ["/absolute/path/to/busibox/tools/mcp-server/dist/index.js"]
    }
  }
}

# Or add to Cursor MCP settings
# Settings > MCP Servers > Add Server
```

## Common Use Cases

### 1. Understanding the Project

**Goal**: Get familiar with Busibox architecture and structure

**Approach**:
```
AI: Read busibox://quickstart
AI: Read busibox://architecture
AI: Read busibox://rules
```

**Natural Language** (in Claude/Cursor):
- "Show me the Busibox quick start guide"
- "What's the system architecture?"
- "What are the organization rules for this project?"

### 2. Finding Documentation

**Goal**: Locate specific documentation quickly

**Using Tools**:
```json
Tool: search_docs
{
  "query": "GPU passthrough",
  "category": "guides"
}
```

**Natural Language**:
- "Search documentation for GPU passthrough"
- "Find guides about GPU configuration"
- "Show me deployment documentation"
- "What troubleshooting guides exist?"

### 3. Understanding Scripts

**Goal**: Learn what a script does and how to use it

**Using Tools**:
```json
Tool: get_script_info
{
  "script_path": "scripts/deploy-ai-portal.sh"
}
```

**Natural Language**:
- "Tell me about the deploy-ai-portal.sh script"
- "What does test-infrastructure.sh do?"
- "Show me all deployment scripts"
- "Find scripts that run on Proxmox host"

### 4. Getting Deployment Help

**Goal**: Deploy a service to test or production

**Using Prompts**:
```json
Prompt: deploy_service
{
  "service": "ai-portal",
  "environment": "test"
}
```

**Natural Language**:
- "How do I deploy ai-portal to test?"
- "Walk me through deploying agent-lxc to production"
- "What are the steps to deploy a service?"

### 5. Troubleshooting Issues

**Goal**: Diagnose and fix problems

**Using Prompts**:
```json
Prompt: troubleshoot_issue
{
  "issue_type": "container"
}
```

**Natural Language**:
- "Help me troubleshoot a container issue"
- "How do I debug deployment problems?"
- "Service won't start, what should I check?"

### 6. Adding New Features

**Goal**: Add a new service to Busibox

**Using Prompts**:
```json
Prompt: add_service
{
  "service_name": "my-new-service"
}
```

**Natural Language**:
- "How do I add a new service called analytics?"
- "Walk me through creating a new LXC container"
- "What's the process for adding a new component?"

### 7. Creating Documentation

**Goal**: Write documentation following project standards

**Using Prompts**:
```json
Prompt: create_documentation
{
  "topic": "new-feature"
}
```

**Natural Language**:
- "How do I document the new analytics service?"
- "Where should deployment documentation go?"
- "What's the format for architecture docs?"

## New Make Targets (2025-01)

The Busibox Makefile has been significantly enhanced with new targets for deployment, testing, and verification.

### Deployment Targets

```bash
# Deploy specific services
make search-api          # Deploy search API
make agent               # Deploy agent service
make ingest              # Deploy ingest service
make ingest-api          # Deploy ingest API only
make ingest-worker       # Deploy ingest worker only
make apps                # Deploy all apps

# Deploy individual applications
make deploy-ai-portal    # Deploy AI Portal
make deploy-agent-client # Deploy Agent Client
make deploy-doc-intel    # Deploy Doc Intel
make deploy-foundation   # Deploy Foundation
make deploy-project-analysis
make deploy-innovation

# Deploy to test environment
make deploy-ai-portal INV=inventory/test
```

### Testing Targets

```bash
# Interactive test menu (recommended)
make test-menu           # Shows interactive menu

# Service tests
make test-ingest         # Test ingest service
make test-search         # Test search service
make test-agent          # Test agent service
make test-apps           # Test applications

# Extraction strategy tests
make test-extraction-simple   # Basic PDF extraction
make test-extraction-llm      # LLM-enhanced extraction
make test-extraction-marker   # Marker extraction (GPU)
make test-extraction-colpali  # ColPali visual extraction

# Coverage reports
make test-ingest-coverage
make test-search-coverage

# Specific test types
make test-search-unit         # Unit tests only
make test-search-integration  # Integration tests only
make test-ingest-all          # All ingest tests including integration
```

### Verification Targets

```bash
make verify              # Run all verification checks
make verify-health       # Service health checks
make verify-smoke        # Database smoke tests
```

### Using MCP with Make Targets

**Natural Language**:
- "Run the test menu"
- "Deploy ai-portal to test"
- "Run ingest tests with coverage"
- "Execute the simple extraction test"
- "Verify all services are healthy"

**Using execute_proxmox_command**:
```
AI: Call execute_proxmox_command
Parameters:
  command: "cd /root/busibox/provision/ansible && make test-menu"
  working_directory: "/root/busibox"
```

## Workflow Examples

### Example 1: Deploying to Test Environment

**Scenario**: You need to deploy the ai-portal service to the test environment.

**Step 1**: Understand the deployment process
```
You: "How do I deploy ai-portal to test?"
[MCP uses deploy_service prompt]
```

**Step 2**: Get detailed deployment info
```
You: "Show me the test environment configuration"
[MCP uses get_deployment_info tool]
```

**Step 3**: Review the deployment script
```
You: "Tell me about scripts/deploy-ai-portal.sh"
[MCP uses get_script_info tool]
```

**Step 4**: Check related documentation
```
You: "Show me the ai-portal deployment guide"
[MCP uses get_doc tool or searches docs]
```

### Example 2: Troubleshooting a Container

**Scenario**: A container isn't starting properly.

**Step 1**: Get troubleshooting guidance
```
You: "Help me troubleshoot a container issue"
[MCP uses troubleshoot_issue prompt]
```

**Step 2**: Find container information
```
You: "Show me all LXC containers and their IPs"
[MCP uses list_containers tool]
```

**Step 3**: Search for similar issues
```
You: "Search troubleshooting docs for container startup"
[MCP uses search_docs tool]
```

**Step 4**: Review architecture
```
You: "Show me the container architecture documentation"
[MCP reads architecture resource]
```

### Example 3: Understanding Project Organization

**Scenario**: New developer needs to understand where things go.

**Step 1**: Read organization rules
```
You: "Show me the project organization rules"
[MCP reads busibox://rules]
```

**Step 2**: Understand documentation structure
```
You: "Search organization docs for documentation placement"
[MCP uses search_docs on organization docs]
```

**Step 3**: Find script organization
```
You: "Where do admin workstation scripts go?"
[MCP reads script organization rules]
```

**Step 4**: Browse examples
```
You: "Show me all documentation in the architecture category"
[MCP reads busibox://docs/architecture]
```

## Tips and Best Practices

### For Effective Searching

1. **Use specific keywords**: "GPU passthrough" rather than just "GPU"
2. **Specify category when known**: Limit search to relevant category
3. **Try variations**: "deploy", "deployment", "deploying" may yield different results
4. **Search before asking**: Check if documentation exists before requesting new docs

### For Script Information

1. **Use relative paths**: `scripts/deploy-ai-portal.sh` not `/full/path/to/script`
2. **Check execution context**: Know whether script runs on workstation, host, or container
3. **Review dependencies**: Check what tools the script needs
4. **Read usage examples**: Script headers contain usage information

### For Documentation

1. **Start with quickstart**: Always begin with `busibox://quickstart`
2. **Check organization rules**: Understand structure before creating docs
3. **Browse by category**: Use category resources to see what exists
4. **Cross-reference**: Look at related docs mentioned in metadata

### For Deployments

1. **Test first**: Always deploy to test before production
2. **Follow prompts**: Use deployment prompts for step-by-step guidance
3. **Validate after**: Check service status after deployment
4. **Document issues**: Add troubleshooting docs for any problems encountered

## Advanced Usage

### Combining Multiple Queries

You can combine resources and tools for comprehensive understanding:

```
1. Read busibox://architecture (understand system)
2. Tool: list_containers (see what exists)
3. Tool: find_scripts with context="proxmox-host" (see host scripts)
4. Tool: get_deployment_info for environment="test" (see test config)
5. Tool: search_docs for query="LXC creation" (find docs)
```

### Custom Workflows

Create your own workflows for common tasks:

**Adding a Feature Workflow**:
1. Search docs for similar features
2. Review architecture to understand integration points
3. Find scripts that might need updating
4. Use add_service prompt for new components
5. Use create_documentation prompt for docs

**Debugging Workflow**:
1. Use troubleshoot_issue prompt for initial guidance
2. Search troubleshooting docs for similar issues
3. Get script info for relevant diagnostic scripts
4. List containers to identify affected services
5. Get deployment info to verify configuration

### Integration with Development

Use the MCP server during development:

**Before Making Changes**:
- Review organization rules
- Check existing documentation
- Find related scripts

**During Development**:
- Search for similar implementations
- Get script usage information
- Reference architecture docs

**After Changes**:
- Use create_documentation prompt
- Update relevant guides
- Add troubleshooting info if needed

## Troubleshooting the MCP Server

### Server Not Responding

**Symptoms**:
- Commands hang
- No response from server
- Connection errors

**Solutions**:
1. Check server is running: `ps aux | grep mcp-server`
2. Restart Claude/Cursor
3. Verify build: `ls -la tools/mcp-server/dist/index.js`
4. Rebuild: `cd tools/mcp-server && npm run build`

### Resources Not Found

**Symptoms**:
- "Resource not found" errors
- Empty results

**Solutions**:
1. Verify URI format: `busibox://docs/category` not `busibox://docs-category`
2. Check category is valid (architecture, deployment, configuration, etc.)
3. Ensure documentation exists in that category

### Search Returns Nothing

**Symptoms**:
- Search tool returns empty results
- Can't find known documentation

**Solutions**:
1. Try broader search terms
2. Search "all" categories instead of specific one
3. Check spelling of search terms
4. Try different variations of keywords

### Configuration Issues

**Symptoms**:
- Server doesn't appear in Claude/Cursor
- "Unknown server" errors

**Solutions**:
1. Verify config file location:
   - Claude: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Cursor: Settings > MCP Servers
2. Check JSON syntax is valid
3. Ensure absolute path to dist/index.js is correct
4. Restart application after config changes

## Examples by Role

### For Developers

```
# Understanding codebase
"Show me the architecture documentation"
"What services are in the system?"
"How do services communicate?"

# Making changes
"Where should I put deployment scripts?"
"How do I add a new Ansible role?"
"Search docs for similar features"

# Testing
"Show me test scripts"
"How do I test infrastructure?"
"What's the testing strategy?"
```

### For DevOps

```
# Deployment
"How do I deploy to production?"
"Show me all deployment scripts"
"What's the deployment checklist?"

# Troubleshooting
"Help troubleshoot deployment issues"
"Show me container logs location"
"Search docs for nginx errors"

# Infrastructure
"List all LXC containers"
"Show me Proxmox host scripts"
"What's the network configuration?"
```

### For Architects

```
# System design
"Show me the system architecture"
"What design decisions were made?"
"How is data flowing through the system?"

# Documentation
"Show me all architecture docs"
"Are there any ADRs?"
"What's documented about security?"

# Planning
"How do I add a new service?"
"What's the pattern for new features?"
"Show me configuration patterns"
```

## Next Steps

After familiarizing yourself with the MCP server:

1. **Explore Resources**: Browse each documentation category
2. **Try Tools**: Experiment with search and script info tools
3. **Use Prompts**: Walk through deployment or troubleshooting prompts
4. **Read Reference**: Review [MCP Server Reference](../reference/mcp-server.md)
5. **Contribute**: Add documentation or improve the server

## Related Documentation

- [MCP Server Reference](../reference/mcp-server.md) - Complete API reference
- [MCP Server README](../../tools/mcp-server/README.md) - Technical documentation
- [CLAUDE.md](../../CLAUDE.md) - Project quick start
- [Organization Rules](../../.cursor/rules/) - Project standards
- [Architecture](../architecture/architecture.md) - System architecture

## Feedback and Improvements

If you encounter issues or have suggestions:
1. Document the issue in `docs/troubleshooting/`
2. Update this guide with solutions
3. Improve the server code if needed
4. Share knowledge with the team

## Summary

The Busibox MCP Server provides:
- ✅ Easy access to all documentation
- ✅ Script information and search
- ✅ Guided assistance for common tasks
- ✅ Integration with AI assistants
- ✅ Consistent with project organization

Use it to:
- Onboard new team members
- Find documentation quickly
- Understand scripts and tools
- Get deployment guidance
- Maintain project standards

Happy coding! 🚀




