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

### Organization Rules (001-099)

- **001-documentation-organization.md** - Where to place documentation files
- **002-script-organization.md** - Where to place scripts based on execution context

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

### "Where should I put this documentation?"

1. **Architecture/Design** → `docs/architecture/`
2. **Deployment guides** → `docs/deployment/`
3. **Configuration** → `docs/configuration/`
4. **Troubleshooting** → `docs/troubleshooting/`
5. **Reference/API** → `docs/reference/`
6. **How-to guides** → `docs/guides/`
7. **Session notes** → `docs/session-notes/`

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



