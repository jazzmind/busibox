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
- **Admin workstation** with `git`, `ansible`, and `make` installed
- A clone of the Busibox repository

```bash
git clone <busibox-repo-url>
cd busibox
```

## Step 1: Configure Your Environment

Copy the example variables and edit them for your environment:

```bash
cp provision/pct/vars.env.example provision/pct/vars.env
```

Key settings to configure:

| Setting | What It Does |
|---------|-------------|
| `SSH_PUBKEY_PATH` | Your SSH public key for container access |
| `TEMPLATE` | Proxmox container template |
| Container IPs | Network addresses for each service |

For Docker deployments, configuration is in `provision/ansible/inventory/local/`.

## Step 2: Create Containers (Proxmox Only)

On the Proxmox host:

```bash
cd /root/busibox/provision/pct
bash create_lxc_base.sh production
```

This creates all LXC containers with the correct networking and storage.

## Step 3: Install Ansible Dependencies

```bash
cd provision/ansible
make deps
```

## Step 4: Deploy Everything

```bash
make install SERVICE=all
```

This deploys all infrastructure (PostgreSQL, Redis, MinIO, Milvus), all API services (AuthZ, Data, Search, Agent, Embedding), the LLM gateway, and the frontend applications.

Deployment takes 10-20 minutes depending on your hardware.

## Step 5: Verify

```bash
make manage SERVICE=all ACTION=status
```

All services should report as running. You can also check individual health endpoints:

```bash
curl http://<authz-ip>:8010/health/live
curl http://<data-ip>:8002/health
curl http://<search-ip>:8003/health
```

## Step 6: Access the Portal

Open your browser and navigate to the Busibox Portal URL (typically `https://your-domain.com` or `http://<apps-ip>:3000`).

Create the first admin user account and you're ready to go.

## What's Next

| Task | Guide |
|------|-------|
| Configure settings | [Configure](03-configure.md) |
| Install apps | [Apps](04-apps.md) |
| Set up AI models | [AI Models & Services](05-ai-models.md) |
| Learn management commands | [Command-Line Management](06-manage.md) |
| Set up staging environment | [Multiple Deployments](07-multiple-deployments.md) |

## Common First-Time Issues

- **"Connection refused"** -- services may still be starting. Wait 2-3 minutes and retry.
- **"Authentication failed"** -- always use `make` commands, never run `docker compose` or `ansible-playbook` directly. Secrets are injected at runtime.
- **Container creation fails** -- verify `vars.env` settings and that the Proxmox template exists.

See [Troubleshooting](08-troubleshooting.md) for more help.
