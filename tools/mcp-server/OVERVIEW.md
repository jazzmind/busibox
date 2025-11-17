# Busibox MCP Server - Project Overview

## What Is This?

The Busibox MCP Server is a **Model Context Protocol server** that makes the Busibox project documentation and scripts easily accessible to AI coding assistants like Claude, Cursor, and others. It's like having an expert assistant that knows exactly where everything is in the project and can guide you through common tasks.

## Why Was It Created?

**Problem**: Busibox has extensive documentation organized across multiple categories, dozens of scripts with different execution contexts, and specific organization rules. Finding the right information or understanding how to do something can be time-consuming.

**Solution**: An MCP server that provides:
- ✅ **Instant documentation access** - Browse by category, search by keyword
- ✅ **Script discovery** - Find scripts by context and purpose
- ✅ **Guided assistance** - Step-by-step help for common tasks
- ✅ **Standards enforcement** - Implements project organization rules
- ✅ **Always up-to-date** - Reads directly from the filesystem

## What Can It Do?

### Browse Documentation by Category

Access organized documentation:
- Architecture and design decisions
- Deployment guides and procedures
- Configuration and setup guides
- Troubleshooting guides
- Reference documentation
- How-to guides
- Session notes

### Search Documentation

Find information quickly:
- Keyword-based search
- Category filtering
- Context-aware results
- Cross-reference discovery

### Understand Scripts

Get detailed script information:
- Purpose and functionality
- Execution context (workstation, host, container)
- Required privileges
- Dependencies
- Usage examples

### Get Guided Assistance

Step-by-step help for:
- Deploying services
- Troubleshooting issues
- Adding new services
- Creating documentation

## How Does It Work?

```
┌─────────────────────────────────────────┐
│         AI Assistant                    │
│    (Claude, Cursor, etc.)              │
└─────────────────┬───────────────────────┘
                  │ MCP Protocol
                  │ (stdio)
┌─────────────────▼───────────────────────┐
│       Busibox MCP Server                │
│   (Node.js/TypeScript)                  │
│                                          │
│  ┌──────────────────────────────────┐  │
│  │ Resources  │ Tools  │ Prompts    │  │
│  └──────────────────────────────────┘  │
└─────────────────┬───────────────────────┘
                  │ File System
┌─────────────────▼───────────────────────┐
│         Busibox Project                 │
│                                          │
│  docs/          scripts/                │
│  provision/     .cursor/rules/          │
│  ...                                    │
└──────────────────────────────────────────┘
```

**Key Points**:
1. Server runs locally on your machine
2. Communicates via standard MCP protocol
3. Reads files directly from project (read-only)
4. Returns structured data to AI assistant
5. AI assistant presents information naturally

## Quick Start

### 1. Install

```bash
cd tools/mcp-server
bash setup.sh
```

This will:
- Check Node.js version
- Install dependencies
- Build the server
- Configure Claude Desktop and/or Cursor
- Display usage information

### 2. Use

In Claude or Cursor, just ask naturally:

```
"Show me the architecture documentation"
"Search docs for GPU passthrough"
"Tell me about deploy-ai-portal.sh"
"How do I deploy agent-lxc to test?"
"Help me troubleshoot a container issue"
```

The AI assistant will use the MCP server to get the information and present it to you.

## Example Interactions

### Getting Documentation

**You**: "Show me the deployment documentation"

**AI** (uses MCP server):
- Reads `busibox://docs/deployment` resource
- Lists all deployment guides
- Summarizes each guide
- Presents organized overview

### Searching for Information

**You**: "Search docs for SSL certificate setup"

**AI** (uses MCP server):
- Calls `search_docs` tool with query "SSL certificate"
- Finds matches across all docs
- Returns matching content with context
- Presents relevant excerpts

### Understanding Scripts

**You**: "What does test-infrastructure.sh do?"

**AI** (uses MCP server):
- Calls `get_script_info` tool
- Extracts script header information
- Returns purpose, context, usage
- Explains functionality

### Getting Deployment Help

**You**: "How do I deploy ai-portal to test?"

**AI** (uses MCP server):
- Uses `deploy_service` prompt
- Provides prerequisites checklist
- Shows deployment commands
- Includes validation steps
- Links to relevant docs

## What's Included?

### Core Files

```
tools/mcp-server/
├── src/
│   └── index.ts           # Main server (1000+ lines)
├── package.json           # Dependencies
├── tsconfig.json          # TypeScript config
├── setup.sh               # Setup script
├── README.md              # Technical docs
└── OVERVIEW.md            # This file
```

### Documentation

```
docs/
├── reference/
│   └── mcp-server.md      # Complete API reference
├── guides/
│   └── mcp-server-usage.md # Usage guide with examples
└── session-notes/
    └── 2025-11-06-mcp-server-implementation.md # Implementation notes
```

### Updated Files

- `CLAUDE.md` - Added MCP server section
- `README.md` - Added AI assistant section

## Capabilities

### 11 Resources

Browse project content:
- 7 documentation categories
- Complete documentation index
- Scripts index
- Organization rules
- Architecture document
- Quick start guide

### 6 Tools

Interactive operations:
- search_docs
- get_script_info
- find_scripts
- get_doc
- list_containers
- get_deployment_info

### 4 Prompts

Guided assistance:
- deploy_service
- troubleshoot_issue
- add_service
- create_documentation

## Requirements

- **Node.js**: Version 18 or higher
- **AI Assistant**: Claude Desktop, Cursor, or another MCP-compatible client
- **Busibox Project**: Access to the project directory

## Benefits

### For New Team Members

- **Instant Context**: Understand project structure immediately
- **Guided Learning**: Step-by-step assistance for common tasks
- **Self-Service**: Find answers without asking team members
- **Standards Compliance**: Learn and follow project conventions

### For Experienced Developers

- **Quick Reference**: Find documentation and scripts instantly
- **Consistent Interface**: Same experience across all AI assistants
- **Time Savings**: Less searching, more building
- **Knowledge Sharing**: Document once, access everywhere

### For the Project

- **Standards Enforcement**: Implements organization rules
- **Better Documentation**: Encourages comprehensive docs
- **Easier Maintenance**: Self-documenting system
- **Improved Quality**: Consistent patterns and practices

## Technical Details

**Protocol**: Model Context Protocol (MCP) v0.5.0  
**Transport**: stdio (standard input/output)  
**Language**: TypeScript (Node.js 18+)  
**SDK**: `@modelcontextprotocol/sdk`

**Key Features**:
- Auto-discovery of project root
- Read-only file system access
- Metadata extraction from headers
- Error handling and validation
- Performance optimizations

## Security

- ✅ **Read-Only**: Never modifies files
- ✅ **Local Only**: No network exposure
- ✅ **Sandboxed**: Can only access project files
- ✅ **No Secrets**: Doesn't read vault contents

## Limitations

- **Read-Only**: Cannot modify files (by design)
- **No Caching**: Reads files on every request
- **Limited Context**: Search results limited per file
- **Keyword Search**: No fuzzy matching or AI search
- **Plain Text**: No syntax highlighting

## Future Ideas

Potential enhancements:
- Enhanced search with fuzzy matching
- Additional tools for validation
- More guided prompts
- Optional caching layer
- Usage analytics

## Troubleshooting

### Server Won't Start

```bash
# Check Node.js version
node --version  # Should be 18+

# Rebuild server
cd tools/mcp-server
npm run build
```

### Resource Not Found

- Verify URI format: `busibox://docs/category`
- Check category name is valid
- Ensure documentation exists

### Empty Search Results

- Try broader search terms
- Search "all" categories
- Check spelling

### Configuration Issues

- Verify config file location
- Check JSON syntax
- Ensure absolute path is correct
- Restart AI assistant

## Getting Help

1. **Read Documentation**:
   - [Usage Guide](../../docs/guides/mcp-server-usage.md)
   - [API Reference](../../docs/reference/mcp-server.md)
   - [Technical README](README.md)

2. **Check Implementation**:
   - Review `src/index.ts` for details
   - Check error messages in console
   - Use MCP inspector for debugging

3. **Ask the AI Assistant**:
   - "Help me troubleshoot the MCP server"
   - "Search docs for MCP issues"

## Contributing

When adding features:

1. **Add Resources**: Update `ListResourcesRequestSchema` handler
2. **Add Tools**: Update `ListToolsRequestSchema` handler
3. **Add Prompts**: Update `ListPromptsRequestSchema` handler
4. **Test**: Verify with AI assistant or MCP inspector
5. **Document**: Update README and reference docs

## Learn More

- **[Usage Guide](../../docs/guides/mcp-server-usage.md)** - Comprehensive usage examples
- **[API Reference](../../docs/reference/mcp-server.md)** - Complete API documentation
- **[README](README.md)** - Technical documentation
- **[MCP Specification](https://modelcontextprotocol.io/)** - Official protocol docs

## Summary

The Busibox MCP Server bridges the gap between AI assistants and the Busibox project. It provides structured, searchable access to all documentation and scripts, making it easy for both humans and AI to understand and work with the project.

**Key Takeaway**: Instead of manually searching files or asking about documentation, AI assistants can now directly access and understand the project structure, find relevant information, and provide guided assistance for common tasks.

It's production-ready, well-documented, and easy to install. Just run `bash setup.sh` and start asking questions!

---

**Version**: 1.0.0  
**Created**: 2025-11-06  
**Status**: Production Ready  
**License**: Part of Busibox project







