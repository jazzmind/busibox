# Documentation Organization Rules

**Purpose**: Ensure consistent documentation structure across the Busibox project

## Documentation Directory Structure

All documentation MUST be placed in the `docs/` directory, organized by **audience**:

```
docs/
├── README.md              # Documentation organization guide
├── administrators/        # Deployment, configuration, operations, troubleshooting
│   ├── configuration/     # Vault, Ansible, environment config
│   └── troubleshooting/   # Debug guides, known issues, fixes
├── developers/            # Technical documentation for contributors
│   ├── architecture/      # System design (numbered 00-09 for core docs)
│   ├── agent-api/         # Agent API guides
│   ├── auth-api/          # Auth API guides
│   ├── ingest-api/        # Ingest API guides
│   ├── search-api/        # Search API guides
│   ├── reference/         # Quick reference, commands, env vars
│   ├── security/          # Security plans and procedures
│   ├── decisions/         # Architecture Decision Records (ADRs)
│   ├── tasks/             # Active task tracking
│   └── notes/             # Working notes, troubleshooting tips
├── users/                 # End-user platform guides
└── archive/               # Historical/outdated content (not served by docs-api)
```

## docs-api Integration

The docs-api service serves documentation via REST API. Every doc that should be served MUST have YAML frontmatter:

```yaml
---
title: "Document Title"
category: "administrator"     # administrator, developer, platform, or apps
order: 1                      # Sort order within category
description: "Brief summary"
published: true               # Set false to hide from docs-api
---
```

### Category Mapping

| Directory | docs-api category | Audience |
|---|---|---|
| `docs/administrators/` | `administrator` | Operators, admins |
| `docs/developers/` | `developer` | Developers, contributors |
| `docs/users/` | `platform` | End users |
| `docs/archive/` | N/A (`published: false`) | Historical reference |

### App Documentation

Apps register docs via `docs/portal/` in their repo. On deploy, these are synced to docs-api with `category: "apps"`. See the app-template for a working example.

## File Naming Conventions

- Use `kebab-case` for all files (e.g., `deployment-groups.md`)
- Use numbered prefixes for ordered sequences (e.g., `00-overview.md`)
- Use descriptive, searchable names

## Document Placement Rules

### "Where should this doc go?"

```
Who is the primary audience?
├─ Operators/admins (deploy, configure, troubleshoot)
│  └→ docs/administrators/
│     ├─ Configuration → docs/administrators/configuration/
│     └─ Troubleshooting → docs/administrators/troubleshooting/
├─ Developers (architecture, APIs, reference)
│  └→ docs/developers/
│     ├─ System design → docs/developers/architecture/
│     ├─ API guides → docs/developers/{service}-api/
│     ├─ Reference → docs/developers/reference/
│     ├─ Security → docs/developers/security/
│     ├─ ADRs → docs/developers/decisions/
│     └─ Working notes → docs/developers/notes/
├─ End users (platform features, how-to)
│  └→ docs/users/
└─ Outdated/historical
   └→ docs/archive/
```

## AI Agent Instructions

When asked to create documentation:

1. **Determine audience** - Who will read this? Admin, developer, or end user?
2. **Check existing docs** - Search for similar documentation first
3. **Choose appropriate path** - Follow the placement rules above
4. **Add frontmatter** - Include title, category, order, description, published
5. **Use kebab-case** - For the filename
6. **Inform user** - Explain where the document was created and why

When asked about documentation location:

1. **Search by audience** first, then by topic
2. **Check `docs/README.md`** for the organization guide
3. **Suggest consolidation** if many similar docs exist

## Examples

### Good Examples

- `docs/administrators/02-install.md` - Admin installation guide
- `docs/developers/architecture/00-overview.md` - Core architecture doc
- `docs/developers/agent-api/agent-server-api.md` - API reference
- `docs/users/02-platform-overview.md` - End-user guide
- `docs/administrators/08-troubleshooting.md` - Troubleshooting

### Bad Examples

- `docs/DEPLOYMENT_SUMMARY.md` - Should be in `docs/administrators/`
- `docs/architecture.md` at root - Should be in `docs/developers/architecture/`
- `FIXES.md` in caps - Should be kebab-case in `docs/administrators/troubleshooting/`
- Session notes in `docs/developers/` - Should be in `docs/archive/session-notes/`

## Enforcement

- AI agents MUST follow these rules when creating or moving documentation
- All new docs MUST include docs-api frontmatter
- Outdated docs SHOULD be moved to `docs/archive/` with `published: false`
