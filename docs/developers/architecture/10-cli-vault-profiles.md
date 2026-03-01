---
title: "CLI, Vault & Profile Architecture"
category: "developer"
order: 10
description: "Busibox Rust CLI TUI, vault password management, and deployment profile architecture"
published: true
---

# CLI, Vault & Profile Architecture

The Busibox CLI is a Rust-based TUI application (`cli/busibox/`) that provides guided setup, installation, and ongoing management of Busibox infrastructure — both local (Docker) and remote (Proxmox/Docker over SSH).

## Overview

```
┌──────────────────────────────────────────────────────────┐
│  Management Machine (admin workstation)                  │
│                                                          │
│  ~/.busibox/                                             │
│    ├── profiles.json          ← all profile configs      │
│    └── vault-keys/                                       │
│        ├── prod-server.enc    ← AES-256-GCM encrypted    │
│        └── staging.enc        ← one per profile          │
│                                                          │
│  busibox CLI (Rust TUI)                                  │
│    ├── Profile selection → prompt master password         │
│    ├── Decrypt vault key → hold in memory                │
│    ├── Install/manage → inject via ANSIBLE_VAULT_PASSWORD │
│    └── Export profile → re-encrypt for remote user       │
│                                                          │
│        │ SSH + env var                                    │
│        ▼                                                 │
│  ┌─────────────────────────────────────────────────┐     │
│  │  Target Host (remote or local)                  │     │
│  │                                                 │     │
│  │  ~/busibox/                                     │     │
│  │    ├── provision/ansible/roles/secrets/vars/     │     │
│  │    │   ├── vault.example.yml  ← template        │     │
│  │    │   ├── vault.prod.yml     ← encrypted       │     │
│  │    │   └── vault.staging.yml  ← encrypted       │     │
│  │    └── scripts/lib/                             │     │
│  │        └── vault-pass-from-env.sh               │     │
│  │                                                 │     │
│  │  ~/.busibox/  (optional, after export)          │     │
│  │    └── vault-keys/{profile}.enc                 │     │
│  └─────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────┘
```

## Vault Password Architecture

### Design Principles

- **Env var only**: The vault password is delivered exclusively via the `ANSIBLE_VAULT_PASSWORD` environment variable. No temporary password files are created on disk.
- **Single transport mechanism**: Ansible's `--vault-password-file` always points to `scripts/lib/vault-pass-from-env.sh`, a script that echoes the env var. This is the only bridge between the CLI and Ansible.
- **Encrypted at rest**: Vault passwords are stored encrypted with AES-256-GCM (Argon2id key derivation) on the management machine. They are only decrypted into memory when the user enters their master password.
- **Per-profile isolation**: Each deployment profile has its own vault password and encrypted key file. Switching profiles clears the in-memory password and requires re-authentication.

### Cryptographic Details

| Component | Algorithm |
|-----------|-----------|
| Vault key encryption | AES-256-GCM |
| Key derivation | Argon2id (32-byte salt) |
| Nonce | 96-bit random (GCM standard) |
| Vault password | 32-char random alphanumeric |
| Storage format | JSON (`EncryptedVault` struct) |

Key files:
- `cli/busibox/src/modules/vault.rs` — All cryptographic operations
- `~/.busibox/vault-keys/{profile_id}.enc` — Encrypted vault key (JSON)

### Password Flow

```
1. User selects profile (or app starts with active profile)
       │
2. CLI checks: does ~/.busibox/vault-keys/{profile}.enc exist?
       │
    ┌──┴──┐
    │ Yes │ → Prompt master password → Argon2id derive key
    └──┬──┘   → AES-256-GCM decrypt → vault password in memory
       │
    ┌──┴──┐
    │ No  │ → First-time setup:
    └──┬──┘   → Generate random 32-char vault password
       │      → Prompt user to set master password
       │      → Encrypt and save to ~/.busibox/vault-keys/
       │
3. Vault password cached in app.vault_password (memory only)
       │
4. On install/deploy: injected as ANSIBLE_VAULT_PASSWORD env var
       │
    ┌──┴──────────────┐
    │ Local            │ → env var set on child process
    │ Remote (SSH)     │ → export ANSIBLE_VAULT_PASSWORD='...' in SSH command
    └─────────────────┘
       │
5. Ansible picks up via vault-pass-from-env.sh
       │
6. Service deployed with decrypted secrets
```

### Password Delivery Chain

The vault password flows through these components:

1. **CLI** (`app.vault_password`) → in-memory only
2. **remote.rs** → sets `ANSIBLE_VAULT_PASSWORD` in SSH command or child process env
3. **service-deploy.sh** → detects env var, points `--vault-password-file` to `vault-pass-from-env.sh`
4. **vault.sh** `ensure_vault_access()` → same: env var present → use `vault-pass-from-env.sh`
5. **vault-pass-from-env.sh** → `echo "${ANSIBLE_VAULT_PASSWORD}"` → Ansible reads it
6. **Ansible** → decrypts `vault.{env}.yml` → injects secrets into roles

No temporary files are created at any point in this chain.

## Profile Management

### Profile Structure

Profiles are stored in `~/.busibox/profiles.json`:

```json
{
  "active": "prod-server",
  "profiles": {
    "prod-server": {
      "label": "Production Server",
      "environment": "production",
      "backend": "docker",
      "remote": true,
      "host": "10.96.200.1",
      "ssh_user": "admin",
      "ssh_key": "~/.ssh/id_ed25519",
      "remote_path": "~/busibox",
      "hardware": {
        "os": "linux",
        "arch": "x86_64",
        "ram_gb": 128,
        "memory_tier": "enhanced",
        "llm_backend": "vllm",
        "gpus": [{"name": "RTX 4090", "vram_gb": 24}]
      }
    },
    "local-dev": {
      "label": "Local Development",
      "environment": "development",
      "backend": "docker",
      "remote": false,
      "hardware": { ... }
    }
  }
}
```

### Profile Lifecycle

1. **Creation**: Setup wizard detects hardware, selects models, configures SSH
2. **Selection**: Switching profiles clears the vault password and triggers re-authentication
3. **Install/Update**: Profile determines target, environment, backend, and model tier
4. **Export**: Vault key re-encrypted with a new master password and deployed to remote host
5. **Password Change**: Master password can be changed without affecting the vault password itself

### CLI Keyboard Shortcuts (Welcome Screen)

| Key | Action |
|-----|--------|
| `Enter` | Select menu item |
| `↑/↓` | Navigate menu |
| `s` | Setup new profile |
| `m` | Check model cache |
| `x` | Export profile to remote host |
| `p` | Change master password |
| `b` | Deploy CLI binary to remote host |
| `q` | Quit |

## Profile Export

Exporting a profile allows a remote host user to run the busibox CLI locally with their own master password.

### What Gets Exported

1. **Vault key** (`~/.busibox/vault-keys/{profile}.enc`): The same vault password, re-encrypted with a new master password chosen for the remote user
2. **Profile config** (`~/.busibox/profile-{id}.json`): The profile metadata

### Export Flow

```
Admin workstation                    Remote host
─────────────────                    ───────────
1. Admin presses 'x'
2. Prompted for remote
   user's master password
3. Vault password re-encrypted
   with new master password
4. ──── SSH ────────────────────► ~/.busibox/vault-keys/{profile}.enc
5. ──── SSH ────────────────────► ~/.busibox/profile-{id}.json
6. (Separately) 'b' deploys
   the CLI binary             ──► ~/busibox/busibox
```

The remote user can then run `./busibox` and enter their own master password to unlock and manage the deployment.

## Binary Deployment

The `b` key deploys the busibox CLI binary to the remote host:

- Detects architecture compatibility (OS + arch) between local and remote
- Uses `rsync` to copy the current binary to `~/busibox/busibox`
- Makes it executable
- If architectures don't match, instructs the user to cross-compile first

## Ansible Vault Files

### Per-Environment Vaults

Each environment has its own vault file:

- `vault.dev.yml` — Development
- `vault.staging.yml` — Staging
- `vault.prod.yml` — Production

These are created from `vault.example.yml` during first install and encrypted with the profile's vault password.

### Secret Generation

On first install, CHANGE_ME placeholders in the vault file are automatically replaced with cryptographically random values:

- PostgreSQL password (24 chars)
- MinIO credentials
- JWT secret (32 chars)
- AuthZ master key (32 chars)
- LiteLLM API key, master key, salt

Optional placeholders (SMTP, GitHub OAuth) remain as CHANGE_ME for manual configuration.

## Key Source Files

| File | Purpose |
|------|---------|
| `cli/busibox/src/modules/vault.rs` | Crypto: AES-256-GCM encrypt/decrypt, key derivation, storage |
| `cli/busibox/src/modules/profile.rs` | Profile CRUD, serialization, path helpers |
| `cli/busibox/src/modules/remote.rs` | SSH execution with vault env var injection |
| `cli/busibox/src/modules/ssh.rs` | SSH connection management |
| `cli/busibox/src/screens/install.rs` | Install orchestration, vault creation, secret generation |
| `cli/busibox/src/screens/welcome.rs` | Main menu, system info, keyboard shortcuts |
| `cli/busibox/src/screens/profile_select.rs` | Profile selection with vault unlock trigger |
| `cli/busibox/src/main.rs` | App loop, vault setup, export, password change, binary deploy |
| `scripts/lib/vault-pass-from-env.sh` | Bridge: echoes ANSIBLE_VAULT_PASSWORD for Ansible |
| `scripts/lib/vault.sh` | Bash vault library (ensure_vault_access) |
| `scripts/make/service-deploy.sh` | Service deployment with vault integration |

## Security Considerations

- **No plaintext on disk**: Vault passwords never touch the filesystem in plaintext. They exist only in memory (`app.vault_password`) and in the SSH command's environment.
- **Session-scoped**: The vault password is cleared when switching profiles and not persisted between CLI sessions.
- **Argon2id**: Key derivation uses Argon2id with a 32-byte random salt, making brute-force attacks on the encrypted vault key files impractical.
- **File permissions**: Encrypted vault key files are created with `0600` permissions.
- **SSH transport**: The vault password is set as an environment variable in the SSH session. It's protected by SSH's encrypted channel but visible in the remote process environment for the duration of the command.
