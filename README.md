# Busibox (Proxmox IaC for secure file ingestion + RAG)

This repo provisions a role-secured data layer on a Proxmox host using LXC containers:
- **files-lxc**: MinIO (S3) for file storage + webhook events.
- **pg-lxc**: PostgreSQL for users/roles/metadata with RLS.
- **milvus-lxc**: Milvus Standalone (via Docker) for embeddings.
- **agent-lxc**: API gateway (Node) to enforce RBAC, issue presigned URLs, search Milvus.
- **ingest-lxc**: Worker to extract/chunk/embed and write to Milvus + Postgres.
- **queue**: Redis Streams running in **ingest-lxc** for ingestion jobs.

It also ships a **deploywatch** systemd timer to poll GitHub Releases and redeploy services.

## Quick Start

From the repository root, use the interactive command system:

```bash
# Initial setup (on Proxmox host)
make setup

# Configure models and GPUs
make configure

# Deploy services
make deploy

# Run tests
make test

# Build MCP server (for Cursor AI)
make mcp
```

All commands are interactive and will guide you through the process. See the [Interactive Commands Guide](docs/guides/interactive-commands.md) for detailed information about each command.

## For Cursor Users

**Use the Busibox MCP Server** for structured access to documentation and scripts:

```bash
make mcp
```

Or manually:

```bash
cd tools/mcp-server && bash setup.sh
```

The MCP server provides:
- Browse documentation by category
- Search documentation by keyword
- Get script information and usage
- Guided assistance for common tasks

See [MCP Server Usage Guide](docs/guides/mcp-server-usage.md) for details.
