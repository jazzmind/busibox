---
created: 2026-01-18
updated: 2026-01-18
status: completed
category: development
---

# Vault Scripts: Color Output Fix

## Issue

The vault management scripts (`sync-vault.sh`, `generate-env-from-vault.sh`, `migrate-env-to-vault.sh`) were displaying ANSI escape codes literally instead of rendering colors:

```
[INFO] Next steps:

  1. Review removed secrets:
     \033[0;36mcd provision/ansible\033[0m          ← Literal escape codes
     \033[0;36mansible-vault view ...\033[0m
```

## Root Cause

The scripts were using `echo` without the `-e` flag to output colored text:

```bash
# WRONG - escape codes printed literally
echo "     ${CYAN}cd provision/ansible${NC}"

# CORRECT - escape codes interpreted
echo -e "     ${CYAN}cd provision/ansible${NC}"
```

The `-e` flag tells `echo` to interpret backslash escape sequences, including the ANSI color codes defined in `scripts/lib/ui.sh`:

```bash
export CYAN='\033[0;36m'
export NC='\033[0m'    # No Color
export DIM='\033[2m'
```

## Fix

Changed all `echo` statements that use color variables to `echo -e` in three files:

### 1. scripts/vault/sync-vault.sh

```bash
# Before
echo "     ${CYAN}cd provision/ansible${NC}"

# After  
echo -e "     ${CYAN}cd provision/ansible${NC}"
```

Applied to:
- Next steps section (review removed secrets)
- Add missing secrets instructions
- Test vault commands
- Backup location display

### 2. scripts/vault/generate-env-from-vault.sh

```bash
# Before
echo "     ${CYAN}cat .env.local${NC}"
echo "  ${DIM}Note: This file is auto-generated...${NC}"

# After
echo -e "     ${CYAN}cat .env.local${NC}"
echo -e "  ${DIM}Note: This file is auto-generated...${NC}"
```

Applied to:
- Review generated file command
- Start Docker command
- Auto-generation warning note

### 3. scripts/vault/migrate-env-to-vault.sh

```bash
# Before
echo "     ${CYAN}make docker-up${NC}"
echo "     ${DIM}(backup saved at: ...)${NC}"

# After
echo -e "     ${CYAN}make docker-up${NC}"
echo -e "     ${DIM}(backup saved at: ...)${NC}"
```

Applied to:
- Verify vault commands
- Docker test commands
- Delete .env.local command
- Backup location display

## Result

Now the output displays properly with colors:

```
[INFO] Next steps:

  1. Review removed secrets:
     cd provision/ansible                         ← Cyan color
     ansible-vault view ...                       ← Cyan color

  Backups available at:
     provision/ansible/roles/secrets/vars/backups/    ← Dimmed
```

## Why This Matters

Proper color coding:
- ✅ **Improves readability** - Commands stand out from descriptions
- ✅ **Matches other scripts** - Consistent UI across all vault tools
- ✅ **Professional appearance** - No raw escape codes in output
- ✅ **Better UX** - Dimmed text for less important info

## Technical Details

### ANSI Color Codes

```bash
\033[0;36m  # Cyan (commands)
\033[2m     # Dim (paths, notes)
\033[0m     # Reset (end color)
```

### Why `echo -e`?

From `man bash`:
```
-e     enable interpretation of backslash escapes
```

Without `-e`, `echo` treats `\033` as literal characters, not as an escape sequence.

### Consistency with ui.sh

All functions in `scripts/lib/ui.sh` use `echo -e`:

```bash
# From ui.sh
header() {
    echo -e "${CYAN}╔$(printf '═%.0s' $(seq 1 $((width - 2))))╗${NC}"
    #    ^^ -e flag for colors
}
```

The vault scripts now follow the same pattern.

## Files Modified

- `scripts/vault/sync-vault.sh` - 7 lines changed
- `scripts/vault/generate-env-from-vault.sh` - 4 lines changed
- `scripts/vault/migrate-env-to-vault.sh` - 7 lines changed

## Testing

```bash
# Test all three scripts
make vault-sync              # Colors work ✓
make vault-generate-env      # Colors work ✓
make vault-migrate           # Colors work ✓
```

All output now displays with proper colors and formatting.
