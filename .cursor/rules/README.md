# Busibox Cursor Rules

This directory contains rules for AI agents (Cursor, Claude Code, etc.) to follow when working on the Busibox project.

## Purpose

These rules ensure:
- **Consistency** - Files are placed in predictable locations
- **Discoverability** - Future AI agents can find documentation and scripts
- **Maintainability** - Clear organization makes updates easier
- **Clarity** - Execution context is obvious from file location

## Rule Files

Rules are numbered and categorized:

### ⚠️ CRITICAL: Make Commands (010)

- **010-make-commands.md** - **READ FIRST** - ALWAYS use `make` commands, NEVER docker/ansible directly

### Organization Rules (001-002)

- **001-documentation-organization.md** - Where to place documentation files
- **002-script-organization.md** - Where to place scripts based on execution context

### Infrastructure Rules (003-004)

- **003-model-registry.md** - LLM model configuration and registry
- **003-shell-command-practices.md** - Shell command best practices
- **003-zero-trust-authentication.md** - Zero-trust authentication patterns
- **004-embedding-configuration.md** - Embedding model configuration
- **004-shell-troubleshooting.md** - Shell troubleshooting guide

### Database Rules (005-009)

- **005-database-practices.md** - Safe database migration practices (NEVER use --accept-data-loss)

### Architecture Rules (100-199)
*Reserved for future architecture and design rules*

### Development Rules (200-299)
*Reserved for future development workflow rules*

### Testing Rules (300-399)
*Reserved for future testing and validation rules*

## How Rules Are Applied

### Cursor IDE
Cursor automatically loads rules from `.cursor/rules/` directory.

### Claude Code (claude.ai/code)
Rules are loaded from `CLAUDE.md` in project root, which should reference this directory.

### Rule Priority
1. **Numbered rules** (001-999) are applied in order
2. **Higher numbers** override lower numbers if conflicts exist
3. **Specific rules** take precedence over general rules

## Quick Reference

### "How do I deploy/restart/manage a service?"

**ALWAYS use `make` commands:**
```bash
# Deploy/redeploy a service
make install SERVICE=authz

# Restart/stop/start a service
make manage SERVICE=authz ACTION=restart

# View logs
make manage SERVICE=authz ACTION=logs

# Check status
make manage SERVICE=authz ACTION=status
```

**NEVER use these directly:**
```bash
❌ docker compose up -d authz-api
❌ docker restart prod-authz-api
❌ ansible-playbook -i inventory/docker docker.yml --tags authz
```

### "Where should I put this documentation?"

1. **Operators/Admins** → `docs/administrators/`
2. **Developers** → `docs/developers/` (architecture, services, reference, tasks)
3. **End users** → `docs/users/`
4. **Historical** → `docs/archive/`

See `.cursor/rules/001-documentation-organization.md` for full decision tree.

### "Which MCP server should I use?"

- **Core development** (build, test, debug services) → `busibox-core-dev`
- **Building apps** (Next.js apps for busibox) → `busibox-app-builder`
- **Deployment/operations** (manage staging/production) → `busibox-admin`

Build with `make mcp`. See CLAUDE.md for Cursor configuration.

### "Where should I put this script?"

1. **Runs on Proxmox host** → `provision/pct/`
   - Uses `pct`, `pvesm`, `pvesh` commands
   - Creates/manages LXC containers
   - Configures host settings

2. **Runs from admin workstation** → `scripts/`
   - Orchestrates Ansible deployments
   - Coordinates multi-step operations
   - High-level automation

3. **Runs inside container (static)** → `provision/ansible/roles/{role}/files/`
   - Service-specific scripts
   - No environment variables needed

4. **Runs inside container (templated)** → `provision/ansible/roles/{role}/templates/`
   - Needs Ansible variables
   - Environment-specific values

## Adding New Rules

When adding rules:

1. **Choose appropriate number range** - See categories above
2. **Use descriptive filename** - `NNN-{purpose}.md`
3. **Include metadata** - Purpose, scope, examples
4. **Provide decision trees** - Help AI agents decide
5. **Show examples** - Good and bad examples
6. **Update this README** - Add to appropriate category

### Rule Template

```markdown
# Rule Title

**Purpose**: One-line description of what this rule enforces

## Overview
[Explain the problem this rule solves]

## Rules
[Specific, actionable rules]

## Decision Tree
[Flowchart or decision logic]

## Examples
### Good Examples ✅
[Examples of correct usage]

### Bad Examples ❌
[Examples of incorrect usage with corrections]

## AI Agent Instructions
[Specific instructions for AI agents]

## Related Rules
[Links to related rule files]
```

## Testing Rules

Before committing changes that add/modify rules:

1. **Read through** - Ensure rules are clear and unambiguous
2. **Check for conflicts** - Verify no contradictions with existing rules
3. **Test with AI** - Ask AI to apply the rules to a scenario
4. **Document changes** - Update this README if adding new categories

## Maintenance

### Quarterly Review
- Review all rules for relevance
- Update examples to match current codebase
- Archive outdated rules

### When Project Structure Changes
- Update affected rules immediately
- Document migration paths
- Update examples

### Deprecation
To deprecate a rule:
1. Add `**Status**: DEPRECATED - [reason]` to top of file
2. Add `**Replaced By**: [new rule]` if applicable
3. Keep file for 3 months before removal
4. Update this README

## Questions?

If rules are unclear or contradictory:
1. Check related rules for context
2. Look at examples in codebase
3. Ask user for clarification
4. Propose rule update

## Contributing

When proposing new rules:
- Explain the problem being solved
- Provide concrete examples
- Consider edge cases
- Keep rules simple and actionable



