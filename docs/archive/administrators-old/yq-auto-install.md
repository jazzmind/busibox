---
title: "YQ Auto-Installation on Linux"
category: "administrator"
order: 52
description: "Auto-install yq on Linux/Proxmox when running make install"
published: true
---

# YQ Auto-Installation on Linux

## Problem

When running `make install` on a Proxmox host (or any Linux system), the installation would fail with:

```
[INFO] Syncing secrets and protected config to vault...
[ERROR] yq is required for writing vault secrets. Install with: brew install yq
[ERROR] Failed to sync values to vault
Installation interrupted or failed (exit code: 1)
```

The issue is that `brew` (Homebrew) doesn't exist on Linux systems - it's macOS-only.

## Solution

The `scripts/lib/vault.sh` library now includes an `ensure_yq_installed()` function that:

1. **Detects if yq is already installed** - returns immediately if available
2. **Auto-installs on Linux** - downloads from GitHub releases
3. **Guides macOS users** - shows `brew install yq` message
4. **Handles errors gracefully** - provides manual install instructions if auto-install fails

## Implementation Details

### Auto-Install Behavior

**On Linux (Proxmox hosts)**:
- Downloads yq v4.35.2 from GitHub releases
- Installs to `/usr/local/bin/yq`
- Requires root access (which `make install` already has)
- Uses `wget` or falls back to `curl`
- Makes binary executable with `chmod +x`

**On macOS**:
- Shows error message with `brew install yq` instructions
- Does not attempt auto-install (respects Homebrew ecosystem)

**On other systems**:
- Shows error with GitHub releases URL

### Code Location

The auto-install function is in `scripts/lib/vault.sh`:

```bash
ensure_yq_installed() {
    # Check if yq is already available
    if command -v yq &>/dev/null; then
        return 0
    fi
    
    _vault_info "yq not found, attempting to install..."
    
    # ... auto-install logic ...
}
```

### Usage

The function is called automatically by:
- `write_vault_secret()` - writes single secret to vault
- `update_vault_secrets()` - writes multiple secrets to vault

Both functions now call `ensure_yq_installed` instead of just checking if yq exists.

## Testing

To test the auto-install:

```bash
# On a fresh Proxmox host without yq:
make install SERVICE=authz

# Should see:
[INFO] yq not found, attempting to install...
[SUCCESS] yq installed successfully to /usr/local/bin/yq
```

## Manual Installation

If auto-install fails, you can install manually:

**Linux (Proxmox)**:
```bash
wget https://github.com/mikefarah/yq/releases/download/v4.35.2/yq_linux_amd64 -O /usr/local/bin/yq
chmod +x /usr/local/bin/yq
```

**macOS**:
```bash
brew install yq
```

**Other systems**:
See https://github.com/mikefarah/yq/releases

## Related

- Similar pattern used for `jq` auto-install in `scripts/make/test.sh`
- Vault library: `scripts/lib/vault.sh`
- Make install script: `scripts/make/install.sh`

## Commit

Fixed in commit: ae819593c291167f1636ee245bb975449689242d
