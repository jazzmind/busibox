---
title: "Deployment Guide"
category: "administrator"
order: 3
description: "Step-by-step deployment instructions for Busibox services and infrastructure"
published: true
---

# Deployment Guide

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Guide  
**Related Docs**:  
- `guides/00-setup.md`  
- `guides/01-configuration.md`  
- `guides/deployment/app-servers.md`  
- `guides/deployment/search-api.md`

## Provision Containers (Proxmox Host)
1. Update `provision/pct/vars.env` with correct IPs/CTIDs, template path, and SSH key.
2. Create containers:
   ```bash
   cd /root/busibox/provision/pct
   bash create_lxc_base.sh        # production vars
   ```

## Configure & Deploy Services (Admin Workstation)
1. Install Ansible deps:
   ```bash
   cd provision/ansible
   make deps
   ```
2. Deploy full stack (production):
   ```bash
   make all
   ```
3. Deploy to test inventory:
   ```bash
   make all INV=inventory/test
   ```
4. Deploy specific components:
   ```bash
   make ingest          # ingest API + worker + redis
   make search-api      # search service
   make agent           # agent skeleton (currently stub)
   make apps            # app bundle on apps-lxc
   ```

## Post-Deploy Validation
- Health:
  - Ingest: `curl http://10.96.200.206:8000/health`
  - Search: `curl http://10.96.200.204:8003/health`
  - AuthZ: `curl http://10.96.200.210:8010/health/live`
- Storage: MinIO console `http://10.96.200.205:9001`.
- Database: `psql -h 10.96.200.203 -U postgres -c 'select 1'`.
- Milvus: `nc -zv 10.96.200.204 19530`.

## Rollouts & Updates
- Re-run `make <role>` to update a single service.
- Apps deploy via `make deploy-ai-portal`, `make deploy-agent-manager` (see CLAUDE.md) from admin workstation.
- Keep proxy rules updated when adding services; only apps are internet-facing.
