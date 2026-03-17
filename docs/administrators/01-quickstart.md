---
title: "Administrator Quick Start"
category: "administrator"
order: 1
description: "Get Busibox up and running in minutes"
published: true
---

# Administrator Quick Start

This guide gets you from zero to a running Busibox instance as fast as possible. For detailed explanations, see the individual guides linked throughout.

## Prerequisites

- **Proxmox host** (or Docker on Linux/macOS) with SSH access
- **Admin workstation** with `git` and Rust toolchain installed
- A clone of the Busibox repository

```bash
git clone <busibox-repo-url>
cd busibox
```

## Step 1: Launch the Busibox CLI

All setup, deployment, and management is done through the **Busibox CLI** -- an interactive terminal UI that guides you through every step. The CLI handles SSH connectivity, encrypted vault passwords, model selection, service deployment, and health monitoring.

```bash
cd cli/busibox
cargo build --release
./target/release/busibox
```

> **Note for AI agents**: If you are an AI coding agent, do not use the CLI directly. Use the `mcp-admin` MCP server tools, which handle vault authentication and provide the same operations programmatically. See [docs/developers/reference/mcp-and-make-internals.md](../developers/reference/mcp-and-make-internals.md).

## Step 2: Set Up Your Profile

On first launch, the CLI walks you through creating a deployment profile:

1. **Deployment target** -- choose local Docker, remote Proxmox, or Kubernetes
2. **SSH & Tailscale** -- establish secure connectivity to your host (remote deployments)
3. **Master password** -- create an encrypted vault key for secrets management (AES-256-GCM, Argon2id key derivation)

Your profile is stored in `~/.busibox/profiles.json` and your encrypted vault key in `~/.busibox/vault-keys/`. On subsequent launches, you enter your master password to unlock the vault.

## Step 3: Hardware Profiling & Model Selection

The CLI detects your hardware (GPUs, memory, CPU) and recommends AI models that fit your resources. You can:

- Browse available models with resource requirements
- Download models to your deployment target
- Benchmark model performance on your hardware

## Step 4: Deploy

From the CLI main menu, select **Install** to deploy services. The CLI:

1. Decrypts vault secrets using your master password
2. Generates any missing secrets (database passwords, API keys)
3. Deploys infrastructure (PostgreSQL, Redis, MinIO, Milvus)
4. Deploys API services (AuthZ, Agent, Data, Search, Embedding)
5. Deploys the LLM gateway and frontend applications

Deployment takes 10-20 minutes depending on your hardware.

## Step 5: Verify & Access

The CLI shows service health status after deployment. Navigate to the Busibox Portal URL shown in the CLI (typically `https://your-domain.com` or `http://<apps-ip>:3000`).

Create the first admin user account and you're ready to go.

## Ongoing Management

Press `m` in the CLI to enter the **Manage** screen, where you can:

- View service status with health indicators
- Restart, stop, start, or redeploy services
- View live logs
- Switch between deployment profiles (manage multiple installations)

Press `m` again (or from the main menu) for the **Models** screen to manage AI models and run benchmarks.

## Managing Multiple Installations

The CLI manages deployment profiles, so you can control multiple Busibox installations from a single workstation:

- Docker on your laptop (development)
- Proxmox cluster (production)
- Kubernetes on cloud (scaling)

Each profile has its own vault key, SSH configuration, and deployment settings. Switch between profiles from the main menu.

## What's Next

| Task | Guide |
|------|-------|
| Configure settings | [Configure](03-configure.md) |
| Install apps | [Apps](04-apps.md) |
| Set up AI models | [AI Models & Services](05-ai-models.md) |
| Set up staging environment | [Multiple Deployments](07-multiple-deployments.md) |
| Deploy to Kubernetes | [Kubernetes Deployment](11-kubernetes.md) |

## Common First-Time Issues

- **"Connection refused"** -- services may still be starting. Wait 2-3 minutes and retry.
- **"Authentication failed"** -- always use the Busibox CLI for operations. Secrets are decrypted from the vault and injected at runtime.
- **Container creation fails** -- verify Proxmox template exists and network settings are correct.

See [Troubleshooting](08-troubleshooting.md) for more help.
