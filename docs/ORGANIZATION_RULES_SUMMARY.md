# Organization Rules Implementation Summary

**Created**: 2025-10-30
**Status**: Complete
**Purpose**: Summary of new organization rules for documentation and scripts

## What Was Created

### 1. Cursor Rules System

**Location**: `.cursor/rules/`

A comprehensive rules system that AI agents (Cursor, Claude Code) automatically follow:

- **`.cursor/rules/README.md`** - Rules system overview and guidelines
- **`.cursor/rules/001-documentation-organization.md`** - Where to place documentation
- **`.cursor/rules/002-script-organization.md`** - Where to place scripts based on execution context

### 2. Project Guide for AI Agents

**Files**:
- **`CLAUDE.md`** - Complete project guide for AI agents
- **`.cursorrules`** - Quick reference rules for Cursor IDE

These files provide:
- Project overview and architecture
- Quick decision trees for file placement
- Common commands and workflows
- Best practices and common pitfalls

### 3. Migration Tools

**Files**:
- **`docs/REORGANIZATION_PLAN.md`** - Detailed migration plan
- **`scripts/reorganize-docs.sh`** - Automated reorganization script

## Problem Solved

### Before

**Documentation**:
- 24 files in flat `docs/` directory
- Inconsistent naming (MixedCase, UPPERCASE, lowercase)
- No clear categorization
- Hard for AI agents to find relevant docs

**Scripts**:
- Unclear where new scripts should go
- No distinction between execution contexts
- Risk of placing scripts in wrong location

### After

**Documentation**:
```
docs/
├── architecture/       # System design
├── deployment/         # Deployment guides
├── configuration/      # Setup guides
├── troubleshooting/    # Debug and fixes
├── reference/          # Reference material
├── guides/             # How-to guides
└── session-notes/      # Working notes
```

**Scripts**:
- Clear rules based on **execution context**
- `scripts/` - Admin workstation (orchestration)
- `provision/pct/` - Proxmox host (container lifecycle)
- `roles/*/files/` - Container scripts (static)
- `roles/*/templates/` - Container scripts (templated)

## Key Benefits

### 1. Discoverability
AI agents can now:
- Find documentation by category
- Understand where to create new files
- Locate related documentation

### 2. Consistency
- Standardized naming (`kebab-case`)
- Required metadata in docs
- Comprehensive headers in scripts
- Clear execution context

### 3. Maintainability
- Related docs grouped together
- Easy to add new documentation
- Clear organization patterns

### 4. Clarity
- Purpose obvious from directory structure
- Execution context clear from script location
- Metadata provides context

## Documentation Rules Quick Reference

### File Placement

**Architecture/Design** → `docs/architecture/`
- System design, component architecture
- Architecture Decision Records (ADRs)
- Testing strategy

**Deployment** → `docs/deployment/`
- Service deployment guides
- Environment-specific procedures
- Infrastructure setup

**Configuration** → `docs/configuration/`
- Setup guides
- Configuration options
- Secrets management

**Troubleshooting** → `docs/troubleshooting/`
- Known issues
- Debug procedures
- Fix summaries

**Reference** → `docs/reference/`
- API specifications
- Command references
- Quick reference guides

**Guides** → `docs/guides/`
- How-to tutorials
- Best practices
- Step-by-step workflows

**Session Notes** → `docs/session-notes/`
- Session summaries
- Working notes
- Implementation notes

### Naming Conventions

- Use `kebab-case` for all files
- Use descriptive, searchable names
- Include metadata header:
  ```markdown
  # Title
  
  **Created**: YYYY-MM-DD
  **Last Updated**: YYYY-MM-DD
  **Status**: [Draft|Active|Deprecated]
  **Category**: [Architecture|Deployment|Configuration|etc]
  ```

## Script Rules Quick Reference

### File Placement by Execution Context

**Proxmox Host** → `provision/pct/`
- Uses `pct`, `pvesm`, `pvesh` commands
- Creates/manages LXC containers
- Configures host settings
- Examples: `create_lxc_base.sh`, `configure-gpu-passthrough.sh`

**Admin Workstation** → `scripts/`
- Orchestrates Ansible deployments
- Coordinates multi-step operations
- High-level automation
- Examples: `deploy-ai-portal.sh`, `test-infrastructure.sh`

**Inside Container (static)** → `provision/ansible/roles/{role}/files/`
- Service-specific utilities
- No environment variables needed
- Examples: `deploywatch.sh`, health check scripts

**Inside Container (templated)** → `provision/ansible/roles/{role}/templates/`
- Needs Ansible variables
- Environment-specific values
- Uses Jinja2 templating (`.j2` extension)
- Examples: `check-cert-expiry.sh.j2`, `deploywatch-app.sh.j2`

### Naming Conventions

Use descriptive prefixes:
- `deploy-*.sh` - Deployment/orchestration
- `setup-*.sh` - One-time setup
- `test-*.sh` - Testing and validation
- `create_*.sh` - Resource creation
- `configure-*.sh` - Configuration
- `check-*.sh` - Validation
- `list-*.sh` - Information gathering

### Script Headers

All scripts must include:
```bash
#!/usr/bin/env bash
#
# Script Name
#
# Purpose: [One-line description]
# Execution Context: [Proxmox Host|Admin Workstation|LXC Container]
# Required Privileges: [root|user|sudo]
# Dependencies: [ansible|pct|docker|etc]
#
# Usage:
#   bash script-name.sh [options]
#

set -euo pipefail  # REQUIRED
```

## Decision Trees

### "Where should this documentation go?"

```
What is the primary purpose?
├─ System design/architecture → docs/architecture/
├─ Deployment procedure → docs/deployment/
├─ Setup/configuration → docs/configuration/
├─ Fixing issues → docs/troubleshooting/
├─ API/reference → docs/reference/
├─ Tutorial/guide → docs/guides/
└─ Session notes → docs/session-notes/
```

### "Where should this script go?"

```
Where will this script execute?
├─ Proxmox host (uses pct/pvesm)
│  └→ provision/pct/
├─ Admin workstation (orchestration)
│  └→ scripts/
└─ Inside LXC container
   ├─ Needs Ansible variables?
   │  ├─ Yes → provision/ansible/roles/{role}/templates/{name}.sh.j2
   │  └─ No  → provision/ansible/roles/{role}/files/{name}.sh
```

## Migration Instructions

### Step 1: Run Migration Script

Test first with dry-run:
```bash
bash scripts/reorganize-docs.sh --dry-run
```

Apply changes:
```bash
bash scripts/reorganize-docs.sh
```

### Step 2: Review Changes

Check that files moved correctly:
```bash
tree -L 2 docs/
```

### Step 3: Update Cross-References

Search for broken links:
```bash
find docs -name "*.md" -exec grep -l "](../" {} \;
```

Update links in moved files to use new paths.

### Step 4: Commit Changes

```bash
# Stage all changes
git add docs/ .cursor/ .cursorrules CLAUDE.md scripts/reorganize-docs.sh

# Commit with descriptive message
git commit -m "docs: implement organization rules and reorganize documentation

- Create .cursor/rules/ with comprehensive organization rules
- Add CLAUDE.md as project guide for AI agents
- Add .cursorrules for Cursor IDE integration
- Reorganize docs/ into categorical subdirectories
- Create migration script and documentation

See docs/ORGANIZATION_RULES_SUMMARY.md for details"
```

## How AI Agents Use These Rules

### Cursor IDE
1. Automatically loads `.cursorrules` on project open
2. References `.cursor/rules/` for detailed guidance
3. Follows organization patterns when creating files

### Claude Code
1. Reads `CLAUDE.md` for project overview
2. References rules in `.cursor/rules/`
3. Uses decision trees to determine file placement

### Future Agents
1. Comprehensive rules in version control
2. Examples showing correct patterns
3. Clear reasoning for organization decisions

## Validation

After implementation, verify:

✅ **Rules exist**: `.cursor/rules/` directory with 3+ files
✅ **Guide exists**: `CLAUDE.md` and `.cursorrules` in root
✅ **Structure exists**: `docs/` subdirectories created
✅ **Script exists**: `scripts/reorganize-docs.sh` is executable
✅ **Plan exists**: `docs/REORGANIZATION_PLAN.md` documents migration

## Examples

### Creating New Documentation

**Before** (unclear where to place):
```bash
touch docs/NEW_FEATURE_DEPLOYMENT.md  # Where does this go?
```

**After** (clear category):
```bash
touch docs/deployment/new-feature.md  # Clear: deployment guide
```

### Creating New Scripts

**Before** (unclear execution context):
```bash
touch scripts/configure-container.sh  # Runs where?
```

**After** (clear from location):
```bash
touch provision/pct/configure-container.sh  # Clear: Proxmox host
```

## Common Questions

### Q: Where do I place a new deployment guide?
**A**: `docs/deployment/{service-name}.md`

### Q: Where do I place a script that creates LXC containers?
**A**: `provision/pct/create_*.sh` (runs on Proxmox host)

### Q: Where do I place a script that orchestrates Ansible?
**A**: `scripts/deploy-*.sh` (runs from admin workstation)

### Q: How do I name a troubleshooting document?
**A**: `docs/troubleshooting/{issue-description}.md` (kebab-case)

### Q: What if a document fits multiple categories?
**A**: Choose primary purpose, then cross-reference from other categories

## Related Files

- `.cursor/rules/001-documentation-organization.md` - Detailed doc rules
- `.cursor/rules/002-script-organization.md` - Detailed script rules
- `.cursor/rules/README.md` - Rules system overview
- `CLAUDE.md` - Complete project guide
- `.cursorrules` - Quick reference for Cursor
- `docs/REORGANIZATION_PLAN.md` - Migration plan
- `scripts/reorganize-docs.sh` - Migration script

## Next Steps

1. **Execute migration**: Run `scripts/reorganize-docs.sh`
2. **Update references**: Fix any broken cross-references
3. **Commit changes**: Stage and commit with descriptive message
4. **Test with AI**: Ask AI agent to create a new doc/script
5. **Validate**: Ensure AI follows new organization rules

## Success Criteria

✅ AI agents can find documentation by category
✅ AI agents place new files in correct location
✅ Clear reasoning for file placement decisions
✅ Consistent naming across all files
✅ Maintainable and scalable structure

---

**Status**: Ready for migration
**Approval**: Pending user review
**Execution**: Run `scripts/reorganize-docs.sh` after approval



