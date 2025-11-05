# Documentation Organization Rules

**Purpose**: Ensure consistent documentation structure across the Busibox project

## Documentation Directory Structure

All documentation MUST be placed in the `docs/` directory at the project root, organized by category:

```
docs/
├── architecture/           # Architecture and design decisions
│   ├── architecture.md     # Main architecture document (REQUIRED)
│   └── decisions/          # Architecture Decision Records (ADRs)
├── deployment/             # Deployment guides and procedures
├── configuration/          # Configuration and setup guides
├── troubleshooting/        # Debug guides and known issues
├── reference/              # Reference documentation and API specs
├── guides/                 # How-to guides and tutorials
└── session-notes/          # Session summaries and working notes
```

## File Naming Conventions

### General Rules
- Use `kebab-case` for all documentation files
- Use descriptive, searchable names
- Prefix with numbers ONLY for ordered sequences (e.g., `01-initial-setup.md`)
- Single-word files should be lowercase: `architecture.md`, `testing.md`, `README.md`

### Category-Specific Naming

**Architecture Documents**:
- `architecture.md` - Main architecture document (REQUIRED at `docs/architecture.md`)
- `decisions/adr-NNNN-{description}.md` - Architecture Decision Records
- `{component}-design.md` - Component-specific design docs

**Deployment Documents**:
- `{service}-deployment.md` - Service-specific deployment (e.g., `ai-portal-deployment.md`)
- `deployment-{environment}.md` - Environment-specific deployment (e.g., `deployment-test.md`)
- `deployment-troubleshooting.md` - Deployment issue resolution

**Configuration Documents**:
- `{service}-configuration.md` - Service configuration guide
- `vault-secrets-guide.md` - Secrets management
- `github-token-setup.md` - Specific setup procedures

**Reference Documents**:
- `{topic}-reference.md` - Reference material
- `quick-reference.md` - Quick lookup guide

**Session/Working Documents**:
- `session-YYYY-MM-DD-{topic}.md` - Session summaries with date
- `fixes-{topic}.md` - Fix documentation
- `migration-{topic}.md` - Migration guides

## Document Placement Rules

### When Creating New Documentation

**IF** creating architecture or design documentation:
- **PLACE IN**: `docs/architecture/`
- **EXAMPLE**: System design, component architecture, ADRs

**IF** creating deployment or infrastructure documentation:
- **PLACE IN**: `docs/deployment/`
- **EXAMPLE**: Service deployment guides, infrastructure setup

**IF** creating configuration or setup documentation:
- **PLACE IN**: `docs/configuration/`
- **EXAMPLE**: Environment setup, secrets management, tool configuration

**IF** creating troubleshooting or debug documentation:
- **PLACE IN**: `docs/troubleshooting/`
- **EXAMPLE**: Known issues, debug procedures, fix summaries

**IF** creating reference or specification documentation:
- **PLACE IN**: `docs/reference/`
- **EXAMPLE**: API specs, command references, data models

**IF** creating how-to or tutorial documentation:
- **PLACE IN**: `docs/guides/`
- **EXAMPLE**: Step-by-step guides, best practices, workflows

**IF** creating session summary or working notes:
- **PLACE IN**: `docs/session-notes/`
- **EXAMPLE**: Session summaries, refactoring notes, implementation notes

## Document Metadata

Every documentation file MUST include metadata at the top:

```markdown
# Document Title

**Created**: YYYY-MM-DD
**Last Updated**: YYYY-MM-DD
**Status**: [Draft|Active|Deprecated]
**Category**: [Architecture|Deployment|Configuration|Guide|Reference|Troubleshooting]
**Related Docs**: [list of related doc paths]

## Overview
[Brief description of what this document covers]
```

## Cross-References

When referencing other documentation:
- Use relative paths from `docs/` root
- Include document title in link text
- Example: `See [Architecture Overview](architecture/architecture.md)`

## Document Types

### Architecture Documents
- **Purpose**: Define system structure, patterns, and decisions
- **Audience**: Developers, architects
- **Must Include**: Diagrams, rationale, constraints, alternatives considered

### Deployment Documents
- **Purpose**: Guide deployment and infrastructure setup
- **Audience**: DevOps, system administrators
- **Must Include**: Prerequisites, step-by-step procedures, verification steps

### Configuration Documents
- **Purpose**: Explain configuration options and setup
- **Audience**: Developers, system administrators
- **Must Include**: All configuration options, examples, defaults, security notes

### Troubleshooting Documents
- **Purpose**: Help diagnose and fix issues
- **Audience**: All users
- **Must Include**: Symptoms, causes, solutions, prevention

### Reference Documents
- **Purpose**: Provide quick lookup information
- **Audience**: All users
- **Must Include**: Comprehensive coverage, examples, organized for scanning

### Session Notes
- **Purpose**: Capture work-in-progress and decisions
- **Audience**: Future AI agents, developers
- **Must Include**: Date, context, decisions made, next steps

## Migration from Flat Structure

When consolidating existing documentation:

1. **Identify document purpose** - Read first section to determine category
2. **Create target directory** if it doesn't exist
3. **Move file** to appropriate category subdirectory
4. **Update cross-references** in other documents
5. **Update any scripts** that reference the old path
6. **Commit with clear message**: `docs: move X to docs/{category}/`

## AI Agent Instructions

When asked to create documentation:

1. **Determine category** - Ask yourself: "What is the primary purpose?"
2. **Check existing docs** - Search for similar documentation first
3. **Choose appropriate name** - Follow naming conventions above
4. **Use correct path** - Place in appropriate category subdirectory
5. **Add metadata** - Include required metadata block
6. **Link related docs** - Add cross-references to related documentation
7. **Inform user** - Tell user where the document was created

When asked about documentation location:

1. **Search by category** first, not by filename
2. **Check multiple categories** if topic spans concerns
3. **Suggest consolidation** if many similar docs exist

## Examples

### Good Examples

✅ `docs/architecture/architecture.md` - Main architecture doc
✅ `docs/deployment/ai-portal-deployment.md` - Specific service deployment
✅ `docs/configuration/vault-secrets-guide.md` - Configuration guide
✅ `docs/troubleshooting/deployment-issues.md` - Troubleshooting guide
✅ `docs/session-notes/session-2025-10-30-ssl-setup.md` - Dated session note

### Bad Examples

❌ `DEPLOYMENT_SUMMARY.md` in root - Should be in `docs/deployment/`
❌ `FIXES_SUMMARY.md` in docs root - Should be in `docs/troubleshooting/`
❌ `SESSION_SUMMARY.md` undated - Should include date in filename
❌ `README_REFACTORING.md` - Should be in `docs/session-notes/`
❌ `QUICK_REFERENCE.md` in caps - Should be `quick-reference.md`

## Migration Plan for Existing Docs

Current `docs/` files to reorganize:

```bash
# Architecture
docs/ARCHITECTURE_DECISION.md → docs/architecture/decisions/adr-0001-container-isolation.md
docs/architecture.md → docs/architecture/architecture.md (keep as-is)
docs/testing.md → docs/architecture/testing-strategy.md

# Deployment
docs/AI_PORTAL_DEPLOYMENT.md → docs/deployment/ai-portal.md
docs/APP_DEPLOYMENT.md → docs/deployment/app-servers.md
docs/DEBUG_DEPLOYMENT.md → docs/troubleshooting/deployment-debug.md
docs/DEPLOY_TEST.md → docs/deployment/test-environment.md
docs/DEPLOYMENT_FIXES.md → docs/troubleshooting/deployment-fixes.md
docs/DEPLOYMENT_SPECIFIC.md → docs/deployment/environment-specific.md
docs/DEPLOYMENT_SUMMARY.md → docs/session-notes/deployment-implementation.md

# Configuration
docs/CONFIGURATION_GUIDE.md → docs/configuration/ansible-configuration.md
docs/GITHUB_TOKEN_SETUP.md → docs/configuration/github-token.md
docs/LOCAL_DEVELOPMENT.md → docs/configuration/local-development.md
docs/SUBDOMAIN_PATTERN.md → docs/configuration/subdomain-pattern.md
docs/VAULT_MIGRATION.md → docs/session-notes/vault-migration.md
docs/VAULT_SECRETS_GUIDE.md → docs/configuration/vault-secrets.md
docs/ZFS_RECOMMENDATIONS.md → docs/reference/zfs-recommendations.md
docs/ZFS_STORAGE_STRATEGY.md → docs/architecture/zfs-storage.md

# Troubleshooting
docs/FIXES_SUMMARY.md → docs/troubleshooting/fixes-summary.md

# Reference
docs/QUICK_REFERENCE.md → docs/reference/quick-reference.md

# Session Notes
docs/MIGRATION_CHECKLIST.md → docs/session-notes/migration-checklist.md
docs/README_REFACTORING.md → docs/session-notes/readme-refactoring.md
docs/REFACTORING_SUMMARY.md → docs/session-notes/refactoring-summary.md
docs/SESSION_SUMMARY.md → docs/session-notes/session-2025-10-30.md (add proper date)
```

## Enforcement

- AI agents MUST follow these rules when creating or moving documentation
- Code reviews SHOULD check documentation placement
- Periodic audits SHOULD identify misplaced documentation



