# Documentation Reorganization Plan

**Created**: 2025-10-30
**Purpose**: Plan for reorganizing docs/ directory according to new organization rules
**Status**: Ready for Execution

## Overview

The `docs/` directory currently contains 24 files in a flat structure. This plan reorganizes them into categorical subdirectories following the new organization rules in `.cursor/rules/001-documentation-organization.md`.

## New Directory Structure

```
docs/
├── architecture/           # System design and decisions
├── deployment/             # Deployment procedures
├── configuration/          # Setup and configuration
├── troubleshooting/        # Debug and fixes
├── reference/              # Reference material
├── guides/                 # How-to guides
└── session-notes/          # Session summaries and working notes
```

## Migration Mapping

### Architecture (5 files)

```bash
# Create directory
mkdir -p docs/architecture/decisions

# Keep main architecture doc
docs/architecture.md → docs/architecture/architecture.md (rename for consistency)

# Move architecture decisions
docs/ARCHITECTURE_DECISION.md → docs/architecture/decisions/adr-0001-container-isolation.md

# Move testing strategy (part of architecture)
docs/testing.md → docs/architecture/testing-strategy.md

# Move storage design
docs/ZFS_STORAGE_STRATEGY.md → docs/architecture/zfs-storage-strategy.md
```

### Deployment (6 files)

```bash
# Create directory
mkdir -p docs/deployment

# Move deployment guides
docs/AI_PORTAL_DEPLOYMENT.md → docs/deployment/ai-portal.md
docs/APP_DEPLOYMENT.md → docs/deployment/app-servers.md
docs/DEPLOY_TEST.md → docs/deployment/test-environment.md
docs/DEPLOYMENT_SPECIFIC.md → docs/deployment/environment-specific.md
```

### Configuration (5 files)

```bash
# Create directory
mkdir -p docs/configuration

# Move configuration guides
docs/CONFIGURATION_GUIDE.md → docs/configuration/ansible-configuration.md
docs/VAULT_SECRETS_GUIDE.md → docs/configuration/vault-secrets.md
docs/GITHUB_TOKEN_SETUP.md → docs/configuration/github-token.md
docs/LOCAL_DEVELOPMENT.md → docs/configuration/local-development.md
docs/SUBDOMAIN_PATTERN.md → docs/configuration/subdomain-pattern.md
```

### Troubleshooting (3 files)

```bash
# Create directory
mkdir -p docs/troubleshooting

# Move troubleshooting docs
docs/DEBUG_DEPLOYMENT.md → docs/troubleshooting/deployment-debug.md
docs/DEPLOYMENT_FIXES.md → docs/troubleshooting/deployment-fixes.md
docs/FIXES_SUMMARY.md → docs/troubleshooting/fixes-summary.md
```

### Reference (2 files)

```bash
# Create directory
mkdir -p docs/reference

# Move reference material
docs/QUICK_REFERENCE.md → docs/reference/quick-reference.md
docs/ZFS_RECOMMENDATIONS.md → docs/reference/zfs-recommendations.md
```

### Session Notes (4 files)

```bash
# Create directory
mkdir -p docs/session-notes

# Move session summaries and working notes
docs/DEPLOYMENT_SUMMARY.md → docs/session-notes/deployment-implementation.md
docs/MIGRATION_CHECKLIST.md → docs/session-notes/migration-checklist.md
docs/README_REFACTORING.md → docs/session-notes/readme-refactoring.md
docs/REFACTORING_SUMMARY.md → docs/session-notes/refactoring-summary.md
docs/SESSION_SUMMARY.md → docs/session-notes/session-2025-10-30.md
docs/VAULT_MIGRATION.md → docs/session-notes/vault-migration.md
```

## Migration Script

```bash
#!/usr/bin/env bash
#
# Reorganize Documentation
#
# Purpose: Reorganize docs/ into categorical subdirectories
# Execution Context: Project root
# Required Privileges: user

set -euo pipefail

DOCS_DIR="docs"

echo "Starting documentation reorganization..."

# Create new directory structure
echo "Creating directories..."
mkdir -p "$DOCS_DIR/architecture/decisions"
mkdir -p "$DOCS_DIR/deployment"
mkdir -p "$DOCS_DIR/configuration"
mkdir -p "$DOCS_DIR/troubleshooting"
mkdir -p "$DOCS_DIR/reference"
mkdir -p "$DOCS_DIR/guides"
mkdir -p "$DOCS_DIR/session-notes"

# Architecture
echo "Moving architecture docs..."
if [ -f "$DOCS_DIR/architecture.md" ]; then
    mv "$DOCS_DIR/architecture.md" "$DOCS_DIR/architecture/architecture.md"
fi
if [ -f "$DOCS_DIR/ARCHITECTURE_DECISION.md" ]; then
    mv "$DOCS_DIR/ARCHITECTURE_DECISION.md" "$DOCS_DIR/architecture/decisions/adr-0001-container-isolation.md"
fi
if [ -f "$DOCS_DIR/testing.md" ]; then
    mv "$DOCS_DIR/testing.md" "$DOCS_DIR/architecture/testing-strategy.md"
fi
if [ -f "$DOCS_DIR/ZFS_STORAGE_STRATEGY.md" ]; then
    mv "$DOCS_DIR/ZFS_STORAGE_STRATEGY.md" "$DOCS_DIR/architecture/zfs-storage-strategy.md"
fi

# Deployment
echo "Moving deployment docs..."
if [ -f "$DOCS_DIR/AI_PORTAL_DEPLOYMENT.md" ]; then
    mv "$DOCS_DIR/AI_PORTAL_DEPLOYMENT.md" "$DOCS_DIR/deployment/ai-portal.md"
fi
if [ -f "$DOCS_DIR/APP_DEPLOYMENT.md" ]; then
    mv "$DOCS_DIR/APP_DEPLOYMENT.md" "$DOCS_DIR/deployment/app-servers.md"
fi
if [ -f "$DOCS_DIR/DEPLOY_TEST.md" ]; then
    mv "$DOCS_DIR/DEPLOY_TEST.md" "$DOCS_DIR/deployment/test-environment.md"
fi
if [ -f "$DOCS_DIR/DEPLOYMENT_SPECIFIC.md" ]; then
    mv "$DOCS_DIR/DEPLOYMENT_SPECIFIC.md" "$DOCS_DIR/deployment/environment-specific.md"
fi

# Configuration
echo "Moving configuration docs..."
if [ -f "$DOCS_DIR/CONFIGURATION_GUIDE.md" ]; then
    mv "$DOCS_DIR/CONFIGURATION_GUIDE.md" "$DOCS_DIR/configuration/ansible-configuration.md"
fi
if [ -f "$DOCS_DIR/VAULT_SECRETS_GUIDE.md" ]; then
    mv "$DOCS_DIR/VAULT_SECRETS_GUIDE.md" "$DOCS_DIR/configuration/vault-secrets.md"
fi
if [ -f "$DOCS_DIR/GITHUB_TOKEN_SETUP.md" ]; then
    mv "$DOCS_DIR/GITHUB_TOKEN_SETUP.md" "$DOCS_DIR/configuration/github-token.md"
fi
if [ -f "$DOCS_DIR/LOCAL_DEVELOPMENT.md" ]; then
    mv "$DOCS_DIR/LOCAL_DEVELOPMENT.md" "$DOCS_DIR/configuration/local-development.md"
fi
if [ -f "$DOCS_DIR/SUBDOMAIN_PATTERN.md" ]; then
    mv "$DOCS_DIR/SUBDOMAIN_PATTERN.md" "$DOCS_DIR/configuration/subdomain-pattern.md"
fi

# Troubleshooting
echo "Moving troubleshooting docs..."
if [ -f "$DOCS_DIR/DEBUG_DEPLOYMENT.md" ]; then
    mv "$DOCS_DIR/DEBUG_DEPLOYMENT.md" "$DOCS_DIR/troubleshooting/deployment-debug.md"
fi
if [ -f "$DOCS_DIR/DEPLOYMENT_FIXES.md" ]; then
    mv "$DOCS_DIR/DEPLOYMENT_FIXES.md" "$DOCS_DIR/troubleshooting/deployment-fixes.md"
fi
if [ -f "$DOCS_DIR/FIXES_SUMMARY.md" ]; then
    mv "$DOCS_DIR/FIXES_SUMMARY.md" "$DOCS_DIR/troubleshooting/fixes-summary.md"
fi

# Reference
echo "Moving reference docs..."
if [ -f "$DOCS_DIR/QUICK_REFERENCE.md" ]; then
    mv "$DOCS_DIR/QUICK_REFERENCE.md" "$DOCS_DIR/reference/quick-reference.md"
fi
if [ -f "$DOCS_DIR/ZFS_RECOMMENDATIONS.md" ]; then
    mv "$DOCS_DIR/ZFS_RECOMMENDATIONS.md" "$DOCS_DIR/reference/zfs-recommendations.md"
fi

# Session Notes
echo "Moving session notes..."
if [ -f "$DOCS_DIR/DEPLOYMENT_SUMMARY.md" ]; then
    mv "$DOCS_DIR/DEPLOYMENT_SUMMARY.md" "$DOCS_DIR/session-notes/deployment-implementation.md"
fi
if [ -f "$DOCS_DIR/MIGRATION_CHECKLIST.md" ]; then
    mv "$DOCS_DIR/MIGRATION_CHECKLIST.md" "$DOCS_DIR/session-notes/migration-checklist.md"
fi
if [ -f "$DOCS_DIR/README_REFACTORING.md" ]; then
    mv "$DOCS_DIR/README_REFACTORING.md" "$DOCS_DIR/session-notes/readme-refactoring.md"
fi
if [ -f "$DOCS_DIR/REFACTORING_SUMMARY.md" ]; then
    mv "$DOCS_DIR/REFACTORING_SUMMARY.md" "$DOCS_DIR/session-notes/refactoring-summary.md"
fi
if [ -f "$DOCS_DIR/SESSION_SUMMARY.md" ]; then
    mv "$DOCS_DIR/SESSION_SUMMARY.md" "$DOCS_DIR/session-notes/session-2025-10-30.md"
fi
if [ -f "$DOCS_DIR/VAULT_MIGRATION.md" ]; then
    mv "$DOCS_DIR/VAULT_MIGRATION.md" "$DOCS_DIR/session-notes/vault-migration.md"
fi

echo "✓ Documentation reorganization complete!"
echo ""
echo "New structure:"
tree -L 2 "$DOCS_DIR"
```

## Post-Migration Tasks

### 1. Update Cross-References

Search for broken links in all moved files:

```bash
# Find all markdown files
find docs -name "*.md" -type f | while read file; do
    # Check for links to old paths
    grep -l "](../" "$file" || true
done
```

Common patterns to update:
- `](architecture.md)` → `](architecture/architecture.md)`
- `](DEPLOYMENT_*.md)` → `](deployment/*.md)`
- Etc.

### 2. Update CLAUDE.md References

Update `CLAUDE.md` to reference new paths:
- Architecture link
- Deployment guides
- Configuration guides

### 3. Update README.md References

Update main `README.md` if it references any moved docs.

### 4. Update Script Comments

Check scripts for documentation references:

```bash
# Search for doc references in scripts
grep -r "docs/" scripts/ provision/pct/ || true
```

### 5. Git Commit

```bash
# Stage the reorganization
git add docs/ .cursor/ .cursorrules CLAUDE.md

# Commit with descriptive message
git commit -m "docs: reorganize documentation into categorical subdirectories

- Create .cursor/rules/ with organization rules
- Reorganize docs/ into subdirectories by category
- Add CLAUDE.md with project guidance
- Update cross-references and links

See docs/REORGANIZATION_PLAN.md for details"
```

## Validation

After migration, verify:

1. **All files moved**: `ls docs/` should only show subdirectories
2. **No broken links**: Test major documentation files
3. **Rules accessible**: Verify `.cursor/rules/` exists
4. **CLAUDE.md complete**: Review for accuracy

## Rollback Plan

If issues arise:

```bash
# Revert the git commit
git revert HEAD

# Or reset to before reorganization
git reset --hard HEAD~1
```

## Benefits

After reorganization:

✅ **Discoverability** - AI agents can find docs by category
✅ **Consistency** - Clear naming and structure
✅ **Scalability** - Easy to add new docs in right place
✅ **Maintainability** - Related docs grouped together
✅ **Clarity** - Purpose obvious from directory structure

## Related Documentation

- `.cursor/rules/001-documentation-organization.md` - Organization rules
- `.cursor/rules/002-script-organization.md` - Script organization rules
- `CLAUDE.md` - Project guide for AI agents



