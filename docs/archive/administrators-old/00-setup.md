---
title: "Getting Started"
category: "administrator"
order: 1
description: "Quick start guide for setting up Busibox on your infrastructure"
published: true
---

# Setup Guide

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Guide  
**Related Docs**:  
- `architecture/01-containers.md`  
- `guides/01-configuration.md`  
- `guides/02-deployment.md`

## Prerequisites
- Proxmox host with access to create LXC containers (`pct` available).
- Admin workstation with SSH access to the host.
- Repository cloned: `git clone git@github.com:.../busibox.git`.
- SSH key present on host (referenced by `provision/pct/vars.env` `SSH_PUBKEY_PATH`).

## Quick Start (Test or Prod)
1. **Review container map**: `provision/pct/vars.env` for CTIDs/IPs.
2. **Install workstation deps**: `sudo apt-get install ansible make` (or equivalent).  
3. **Proxmox templates**: Ensure Debian 12 template path matches `TEMPLATE` in `vars.env`.
4. **Provision base containers** (run on Proxmox host):
   ```bash
   cd /root/busibox/provision/pct
   bash create_lxc_base.sh       # uses vars.env
   ```
5. **Prepare Ansible** (admin workstation):
   ```bash
   cd provision/ansible
   make deps           # install Ansible collections/roles
   ```

## Local Development Notes
- Service code lives under `srv/` (ingest, search, authz, agent).
- Each service has `requirements.txt` or package manifest; install in a venv when iterating locally.
- Use container IPs from `vars.env` for `.env` values when running services outside LXC.

## Next Steps
- Configure secrets and env (see `guides/01-configuration.md`).
- Deploy services (see `guides/02-deployment.md`).
