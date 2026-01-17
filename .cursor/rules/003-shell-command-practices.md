# Shell Command Best Practices

**Created**: 2026-01-17  
**Status**: Active  
**Category**: Development Practices

## Rule: Don't Pipe Shell Commands to head/tail

When running shell commands via the Shell tool, **DO NOT** pipe output to `head` or `tail` unless absolutely necessary for performance reasons.

### Problem

Piping to `head` or `tail` truncates output, which can hide critical errors at the beginning or end of command output. This makes debugging harder and can cause you to miss important information.

### Examples

❌ **Bad**:
```bash
grep -r "pattern" . | head -20
git status --short | head -10
npm run build 2>&1 | tail -50
```

✅ **Good**:
```bash
grep -r "pattern" .
git status --short
npm run build
```

### When It's Acceptable

Only use `head`/`tail` when:
1. Output is known to be extremely large (thousands of lines)
2. You're specifically looking for first/last N entries
3. You use the `head_limit` parameter in Grep tool instead

### Alternative Approaches

Instead of piping to head/tail in shell commands:
- Use tool-specific parameters (e.g., `head_limit` in Grep)
- Run the full command and review all output
- If output is too large, use more specific search criteria
- For counting, use `wc -l` instead of viewing truncated output

### Rationale

Shell tool output is already limited by timeouts, and the tool display handles large outputs well. By keeping full output, you can:
- See all errors and warnings
- Spot patterns across entire output
- Make better decisions based on complete information
- Debug issues more effectively
