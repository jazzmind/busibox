---
title: "MCP and Make Internals"
category: "developer"
order: 120
description: "How AI agents and the CLI interact with make targets and vault secrets"
published: true
---

# MCP and Make Internals

This document explains how Busibox service operations work under the hood. It is intended for **AI coding agents** and **core developers** -- not end users. End users should use the Busibox CLI (see [Administrator Quick Start](../../administrators/01-quickstart.md)).

## Architecture

All service operations (deploy, restart, logs, status, etc.) ultimately run through Ansible playbooks. Three interfaces provide access, each handling vault authentication differently:

```
┌─────────────────────┐    ┌──────────────────────┐    ┌────────────────────┐
│   Busibox CLI       │    │   MCP Admin Server   │    │   make targets     │
│   (for humans)      │    │   (for AI agents)    │    │   (internal)       │
│                     │    │                      │    │                    │
│ Prompts for master  │    │ Relies on vault pass │    │ Requires           │
│ password, decrypts  │    │ file on remote host  │    │ ANSIBLE_VAULT_     │
│ vault key, injects  │    │ (or env var)         │    │ PASSWORD env var   │
│ ANSIBLE_VAULT_      │    │                      │    │ or password file   │
│ PASSWORD into env   │    │                      │    │                    │
└────────┬────────────┘    └──────────┬───────────┘    └─────────┬──────────┘
         │                            │                          │
         ▼                            ▼                          ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                        vault.sh / service-deploy.sh                       │
│  • Checks ANSIBLE_VAULT_PASSWORD env var first                           │
│  • Falls back to ~/.busibox-vault-pass-{env} or ~/.vault_pass files      │
│  • Sets --vault-password-file for ansible-playbook                       │
└────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                           Ansible Playbooks                               │
│  ansible-playbook --vault-password-file vault-pass-from-env.sh ...       │
└────────────────────────────────────────────────────────────────────────────┘
```

## Vault Password Flow

### Priority Order

Scripts use this order to find the vault password:

1. **`ANSIBLE_VAULT_PASSWORD` env var** -- set by the CLI when running commands locally or via SSH. The env var is read by `scripts/lib/vault-pass-from-env.sh`, which is used as Ansible's `--vault-password-file`.
2. **Per-environment file** -- `~/.busibox-vault-pass-{dev,staging,prod,demo}` on the host.
3. **Legacy file** -- `~/.vault_pass` on the host.
4. **Interactive prompt** -- `vault.sh` prompts the user (only works in interactive terminals).

### How the CLI Handles It

The Busibox CLI stores vault keys encrypted with AES-256-GCM in `~/.busibox/vault-keys/{profile_id}.enc`. On launch:

1. User enters master password (via rpassword, never echoed)
2. CLI derives key with Argon2id and decrypts the vault password
3. CLI injects `ANSIBLE_VAULT_PASSWORD=<decrypted>` into every subprocess (make, ansible, scripts)
4. For remote operations, the env var is exported in the SSH command before running make

### How the MCP Admin Server Handles It

The `mcp-admin` server SSHes into the Proxmox host and runs `make` targets directly. It **does not currently inject `ANSIBLE_VAULT_PASSWORD`**. This means it relies on vault password files already present on the remote host (`~/.busibox-vault-pass-*` or `~/.vault_pass`).

**Known gap**: The MCP server cannot prompt users for passwords through the MCP protocol. For deployments that require vault access, ensure vault password files exist on the target host, or use the CLI to deploy the busibox binary (which can create these files via profile export).

## Make Targets Reference

Make targets are the internal implementation used by the CLI and MCP server. They are **not intended for direct human use** because they require manual vault password setup.

### Key Make Targets

```bash
# Deploy services (requires vault password)
make install SERVICE=authz
make install SERVICE=all

# Manage running services
make manage SERVICE=authz ACTION=restart
make manage SERVICE=authz ACTION=logs
make manage SERVICE=authz ACTION=status
make manage SERVICE=authz ACTION=redeploy

# Kubernetes
make k8s-deploy
make k8s-status
make k8s-logs SERVICE=authz-api

# Testing
make test-docker SERVICE=authz
make test-local SERVICE=agent INV=staging
```

### Environment Variables

| Variable | Purpose | Set By |
|----------|---------|--------|
| `ANSIBLE_VAULT_PASSWORD` | Vault password for Ansible | CLI (automatic) |
| `USE_MANAGER` | `0` to skip manager container | CLI sets to `0` |
| `SERVICE` | Target service name(s) | CLI / MCP |
| `ACTION` | Management action | CLI / MCP |
| `INV` | Inventory path (e.g. `inventory/staging`) | CLI / MCP |
| `REF` | Git ref for app deployment | CLI / MCP |

### Using Make Targets as an AI Agent

If you are an AI agent that needs to run make targets directly (e.g., via shell):

1. **Check for vault password availability** -- verify `~/.busibox-vault-pass-*` or `~/.vault_pass` exists on the target host
2. **Prefer `mcp-admin` tools** -- use `run_make_target` which handles SSH and targeting
3. **If running locally**, you need `ANSIBLE_VAULT_PASSWORD` set in your environment. Ask the user for the vault password or master password if needed.

## MCP Server Tools

The `mcp-admin` server provides these tools:

| Tool | Description |
|------|-------------|
| `run_make_target` | Run a make target on the Proxmox host |
| `list_make_targets` | List available make targets by category |
| `check_environment_health` | Run `verify-health` |
| `git_pull_busibox` | Pull latest code on Proxmox |
| `git_status` | Check git status on Proxmox |
| `get_container_logs` | View container service logs |
| `get_container_service_status` | Check service status in a container |

Destructive operations (`docker-clean`, `reset_hard`, etc.) require `confirm: true`.

## Related

- [CLI, Vault & Profile Architecture](../architecture/10-cli-vault-profiles.md) -- full CLI internals
- [Administrator Quick Start](../../administrators/01-quickstart.md) -- user-facing setup guide
- [Service Management](../../administrators/06-manage.md) -- user-facing management guide
