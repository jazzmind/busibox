---
title: "Application Auto-Deploy"
category: "administrator"
order: 21
description: "Configure which applications deploy automatically during provisioning"
published: true
---

# Application Auto-Deploy Configuration

## Overview

The `auto_deploy` flag in `apps.yml` controls whether applications are automatically deployed during initial provisioning or require manual deployment via web UI or explicit targeting.

## Purpose

This feature allows us to:
1. Reduce initial deployment time by only deploying essential services
2. Avoid resource waste on apps that may not be needed immediately
3. Separate core infrastructure apps from add-on/optional apps
4. Prevent deployment failures of optional apps from blocking infrastructure setup

## Configuration

### In apps.yml

```yaml
applications:
  - name: ai-portal
    auto_deploy: true  # Deploy automatically during provisioning
    # ... other config ...
  
  - name: doc-intel
    auto_deploy: false  # Deploy only via web UI or explicit targeting
    # ... other config ...
```

**Default behavior**: If `auto_deploy` is not specified, it defaults to `true` for backwards compatibility.

## Core vs Add-On Apps

### Core Applications (`auto_deploy: true`)

Essential services required for system operation:
- `ai-portal` - Main web interface
- `agent-manager` - Agent management interface

These are automatically deployed during:
- Initial provisioning (`make all`)
- App deployment runs (`make deploy-apps`)

### Add-On Applications (`auto_deploy: false`)

Optional services deployed on-demand:
- `doc-intel` - Document intelligence
- `foundation` - Foundation app
- `project-analysis` - Project analysis tools
- `innovation` - Innovation tools

These must be deployed explicitly via:
- Web UI deployment interface
- Manual Ansible targeting
- Direct deploywatch script execution

## Deployment Methods

### Automatic Deployment (Core Apps)

```bash
# Deploy all core apps (auto_deploy: true)
cd provision/ansible
make deploy-apps
```

### Manual Deployment (Add-On Apps)

#### Method 1: Ansible with Explicit Targeting

```bash
cd provision/ansible
ansible-playbook site.yml --tags app_deployer -e "deploy_app=doc-intel"
```

#### Method 2: Direct Deploywatch Execution

```bash
ssh apps-lxc
bash /srv/deploywatch/apps/doc-intel.sh
```

#### Method 3: Web UI (Recommended)

Use the AI Portal's deployment management interface to deploy add-on apps.

## Implementation Details

### Ansible Task Filtering

The `deploy.yml` task file filters applications during initial deployment:

```yaml
- name: Initial deployment of applications
  command: "bash {{ deploywatch_apps_dir }}/{{ item.item.name }}.sh"
  when:
    # ... other conditions ...
    - item.item.auto_deploy | default(true)
```

This ensures only apps with `auto_deploy: true` (or unspecified) are deployed automatically.

### Deploywatch Scripts

All deploywatch scripts are still generated regardless of `auto_deploy` setting. This allows:
- Manual deployment of add-on apps at any time
- Automated updates via deploywatch timer for all deployed apps
- Web UI deployment interface for add-on apps

## Benefits

1. **Faster Initial Setup**: Core infrastructure deploys in minutes instead of hours
2. **Resource Efficiency**: Only allocate resources to apps that are actively used
3. **Failure Isolation**: Optional app deployment failures don't block core infrastructure
4. **Flexible Management**: Deploy add-ons when needed via user-friendly web UI

## Migration Guide

### Existing Deployments

Existing deployments are not affected. The `auto_deploy` flag only controls initial deployment behavior.

### New Deployments

1. Core apps (ai-portal, agent-manager) deploy automatically
2. Add-on apps are available for deployment via web UI after infrastructure is ready
3. All apps receive automated updates via deploywatch once deployed

## Related

- `provision/ansible/group_vars/all/apps.yml` - Application definitions
- `provision/ansible/roles/app_deployer/README.md` - Full app deployer documentation
- `provision/ansible/roles/app_deployer/tasks/deploy.yml` - Deployment task implementation

## NPM Dependency Resolution

### Issue

Some Node.js applications (e.g., `doc-intel`) may encounter peer dependency conflicts during `npm install`:

```
npm error ERESOLVE unable to resolve dependency tree
npm error peer @mastra/core@">=0.18.1-0 <0.21.0-0" from @mastra/evals@0.13.10
```

### Solution

The deploywatch script template now uses `--legacy-peer-deps` flag by default:

```bash
npm install --legacy-peer-deps
```

This allows npm to bypass strict peer dependency checking while still installing all required packages.

**Location**: `provision/ansible/roles/app_deployer/templates/deploywatch-app.sh.j2`

**Why this works**: Modern npm versions use strict peer dependency resolution which can fail when packages have slightly mismatched version ranges. The `--legacy-peer-deps` flag uses npm v4-v6 behavior which is more permissive while still secure.
