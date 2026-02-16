# Busibox Documentation

## Organization

Documentation is organized by **audience** to make it easy to find what you need:

```
docs/
  README.md              # This file
  administrators/        # Deployment, configuration, operations, troubleshooting
  developers/            # Architecture, API guides, reference, security, dev notes
  users/                 # End-user platform guides (served via docs-api)
  archive/               # Historical/outdated content (not served by docs-api)
```

### administrators/

For people who deploy, configure, and operate Busibox infrastructure.

1. **Quick Start** -- get up and running fast
2. **Installation** -- full install for Proxmox and Docker
3. **Configuration** -- AI Portal settings and environment variables
4. **Apps** -- installing and managing applications
5. **AI Models & Services** -- model providers, local inference, LLM gateway
6. **Command-Line Management** -- `make` commands reference
7. **Multiple Deployments** -- staging/production environments
8. **Troubleshooting** -- diagnosing and resolving issues

docs-api category: `administrator`

### developers/

For people who build on or contribute to Busibox.

```
developers/
  01-testing.md              # Testing guide (how to run, write, debug tests)
  02-security.md             # API security testing (OWASP Top 10)
  doc-code-inconsistencies.md  # Tracking doc/code mismatches
  architecture/              # System design (00-overview through 09-databases)
  services/                  # Per-service API docs and architecture
    agents/                  # Agent API, architecture, testing, conversations
    authz/                   # OAuth2 token exchange, bootstrap credentials
    data/                    # Chunking, multi-flow processing, ZFS storage
    search/                  # Search service, AI search features
  reference/                 # Quick-reference docs, env vars, commands
    decisions/               # Architecture Decision Records (ADRs)
  tasks/                     # Active planning docs
```

docs-api category: `developer`

### users/

For end users of the Busibox platform.

1. **Getting Started** -- first steps with Busibox
2. **Platform Overview** -- what Busibox is and what it does
3. **AI Models** -- how AI powers your experience
4. **Documents** -- uploading, processing, and searching
5. **AI Agents** -- chatting with agents that know your documents
6. **Applications** -- apps available on the platform
7. **Data & Security** -- how your data is protected
8. **Troubleshooting** -- common issues and solutions

docs-api category: `platform`

### archive/

Historical content that is no longer current. Not served by docs-api.

- **Session notes** from development sessions
- **Task archives** from completed work
- **Old guides** that have been superseded
- **Architecture/deployment** docs from before restructuring

## docs-api Integration

The docs-api service (`srv/docs/`) serves documentation to the AI Portal and other consumers. It reads markdown files with YAML frontmatter and serves them via REST API.

### Categories

| Category | Audience | Directory |
|---|---|---|
| `platform` | End users | `docs/users/` |
| `administrator` | Operators/admins | `docs/administrators/` |
| `developer` | Developers | `docs/developers/` |
| `apps` | Per-app docs | Synced from app repos via `docs/portal/` |

### Required Frontmatter

Every doc served by docs-api must have YAML frontmatter:

```yaml
---
title: "Document Title"
category: "platform"          # platform, administrator, developer, or apps
order: 1                      # Sort order within category
description: "Brief summary"
published: true               # Set false to hide from docs-api
---
```

For app docs (category `apps`), also include:

```yaml
app_id: "my-app"              # App identifier
app_name: "My App"            # Human-readable name
```

### App Documentation

Apps register their docs by including a `docs/portal/` directory in their repo. On deploy, the deploy-api copies these files into the docs-api content directory. See the busibox-template for a working example.

## File Conventions

- **Filenames**: Use `kebab-case.md` (e.g., `deployment-groups.md`)
- **Numbered files**: Use `NN-name.md` for ordered sequences (e.g., `00-overview.md`)
- **Metadata**: Include Created, Last Updated, Status at the top of each doc
- **Archive policy**: Move outdated docs to `archive/` with `published: false`

## Script Organization

Scripts are organized by **execution context** (not documented here -- see `.cursor/rules/002-script-organization.md`):

| Context | Location |
|---|---|
| Admin workstation | `scripts/` |
| Proxmox host | `provision/pct/` |
| Inside container (static) | `provision/ansible/roles/{role}/files/` |
| Inside container (templated) | `provision/ansible/roles/{role}/templates/` |
