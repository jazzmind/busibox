---
title: Application Configuration Architecture
category: deployment
created: 2025-11-22
updated: 2025-11-22
status: active
tags: [deployment, configuration, ansible, architecture]
---

# Application Configuration Architecture

## Overview

This document describes the centralized application configuration architecture for Busibox deployments. The architecture separates application definitions from environment-specific configuration, enabling consistent deployments across test and production environments.

## Problem Statement

**Previous Architecture (Problematic):**
- Application definitions duplicated in `inventory/production/group_vars/all/00-main.yml` and `inventory/test/group_vars/all/00-main.yml`
- Changes to application structure required updates in multiple files
- High risk of configuration drift between environments
- Difficult to maintain consistency

**New Architecture (Solution):**
- Single source of truth for application definitions in `group_vars/apps.yml`
- Environment-specific values (IPs, domains, NODE_ENV) in `inventory/{env}/group_vars/all/00-main.yml`
- Applications reference environment variables for dynamic values
- Easy to add new applications without environment-specific changes

## Architecture Components

### 1. Centralized Application Definitions

**Location:** `provision/ansible/group_vars/all/apps.yml`

**Purpose:** Define all applications deployed in Busibox with their:
- GitHub repository
- Container assignment
- Port allocation
- Deployment path
- Health check endpoint
- Routing configuration
- Required secrets
- Base environment variables

**Example:**
```yaml
applications:
  - name: foundation
    github_repo: jazzmind/foundation
    container: apps-lxc
    container_ip: "{{ apps_ip }}"  # References environment variable
    port: 3003
    deploy_path: /srv/apps/foundation
    health_endpoint: /api/health
    routes:
      - type: subdomain
        subdomain: "foundation{{ env_subdomain_suffix | default('') }}"
    env:
      NODE_ENV: "{{ node_env }}"  # References environment variable
      LITELLM_BASE_URL: "http://{{ litellm_ip }}:{{ litellm_port }}/v1"
```

### 2. Environment-Specific Configuration

**Locations:**
- `provision/ansible/inventory/production/group_vars/all/00-main.yml`
- `provision/ansible/inventory/test/group_vars/all/00-main.yml`

**Purpose:** Define environment-specific values that applications reference:

**Production Values:**
```yaml
busibox_env: production
node_env: production
network_base_octets: "10.96.200"
domain: "ai.{{ base_domain }}"
full_domain: "{{ domain }}"
env_subdomain_suffix: ""  # No suffix for production
log_level: info
```

**Test Values:**
```yaml
busibox_env: test
node_env: development
network_base_octets: "10.96.208"
domain: "ai.{{ base_domain }}"
full_domain: "test.{{ domain }}"
env_subdomain_suffix: "-test"  # Add suffix for test
log_level: debug
```

### 3. Variable Resolution

Ansible resolves variables at deployment time:

**Application Definition (apps.yml):**
```yaml
container_ip: "{{ apps_ip }}"
subdomain: "foundation{{ env_subdomain_suffix | default('') }}"
env:
  NODE_ENV: "{{ node_env }}"
```

**Production Resolution:**
```yaml
container_ip: "10.96.200.201"
subdomain: "foundation"
env:
  NODE_ENV: "production"
```

**Test Resolution:**
```yaml
container_ip: "10.96.208.201"
subdomain: "foundation-test"
env:
  NODE_ENV: "development"
```

## Key Variables Reference

### Network Variables
- `network_base_octets` - First three octets of IP range (e.g., "10.96.200")
- `apps_ip` - Calculated as `{{ network_base_octets }}.201`
- `agent_ip` - Calculated as `{{ network_base_octets }}.202`
- `postgres_ip` - Calculated as `{{ network_base_octets }}.203`
- `litellm_ip` - Calculated as `{{ network_base_octets }}.207`

### Domain Variables
- `base_domain` - From vault (deployment-specific, e.g., "jaycashman.com")
- `domain` - Calculated as `ai.{{ base_domain }}`
- `full_domain` - Production: `{{ domain }}`, Test: `test.{{ domain }}`
- `www_domain` - Calculated as `www.{{ full_domain }}`
- `env_subdomain_suffix` - Production: `""`, Test: `"-test"`

### Application Variables
- `node_env` - Production: `production`, Test: `development`
- `log_level` - Production: `info`, Test: `debug`
- `nginx_worker_connections` - Production: `1024`, Test: `512`
- `nginx_client_max_body_size` - Production: `100M`, Test: `50M`

## Adding New Applications

### Step 1: Add to apps.yml

Edit `provision/ansible/group_vars/all/apps.yml`:

```yaml
applications:
  - name: my-new-app
    github_repo: jazzmind/my-new-app
    container: apps-lxc
    container_ip: "{{ apps_ip }}"
    port: 3005  # Next available port
    deploy_path: /srv/apps/my-new-app
    health_endpoint: /api/health
    build_command: "npm run build"
    routes:
      - type: subdomain
        subdomain: "myapp{{ env_subdomain_suffix | default('') }}"
      - type: path
        domain: "{{ full_domain }}"
        path: /myapp
        strip_path: true
    secrets:
      - database_url
      - litellm_api_key
    env:
      NODE_ENV: "{{ node_env }}"
      LITELLM_BASE_URL: "http://{{ litellm_ip }}:{{ litellm_port }}/v1"
      PORT: "3005"
      DEPLOYMENT_MODE: "busibox"
```

### Step 2: Add Secrets to Vault

Edit `provision/ansible/roles/secrets/vars/vault.yml`:

```yaml
secrets:
  my_new_app:
    database_url: "postgresql://..."
    litellm_api_key: "..."
```

### Step 3: Deploy

```bash
cd provision/ansible

# Deploy to test first
make test

# Deploy to production
make production
```

**That's it!** No environment-specific changes needed.

## Port Allocation Strategy

**Defined in apps.yml:**

```yaml
# Port allocation strategy:
# - 3000-3099: Public web applications
# - 4000-4099: Internal API services
# - 8000-8099: LLM/AI services
```

**Current Allocations:**
- 3000: ai-portal
- 3001: agent-client
- 3002: doc-intel
- 3003: foundation
- 3004: project-analysis
- 3005: (next available)
- 4111: agent-server (internal API)

## Routing Patterns

### Domain Routing
Routes multiple domain names to application:

```yaml
routes:
  - type: domain
    domains:
      - "{{ full_domain }}"
      - "{{ www_domain }}"
```

**Result:**
- Production: `ai.jaycashman.com`, `www.ai.jaycashman.com`
- Test: `test.ai.jaycashman.com`, `www.test.ai.jaycashman.com`

### Subdomain Routing
Creates subdomain for application:

```yaml
routes:
  - type: subdomain
    subdomain: "foundation{{ env_subdomain_suffix | default('') }}"
```

**Result:**
- Production: `foundation.ai.jaycashman.com`
- Test: `foundation-test.test.ai.jaycashman.com`

### Path Routing
Routes URL path to application:

```yaml
routes:
  - type: path
    domain: "{{ full_domain }}"
    path: /foundation
    strip_path: true
```

**Result:**
- Production: `ai.jaycashman.com/foundation`
- Test: `test.ai.jaycashman.com/foundation`

## Deployment Mode Support

Applications can support dual deployment modes:

### Vercel Deployment
- Uses OpenAI API directly
- Uses Resend for email
- Uses Neon or external PostgreSQL
- Configured via Vercel environment variables

### Busibox Deployment
- Uses liteLLM proxy (local LLMs)
- Uses local SMTP or Resend fallback
- Uses local PostgreSQL
- Configured via Ansible secrets

### Implementation

**1. Add deployment config to application:**

```typescript
// lib/deployment-config.ts
export function getDeploymentMode(): 'vercel' | 'busibox' {
  return process.env.DEPLOYMENT_MODE === 'busibox' ? 'busibox' : 'vercel';
}

export function getOpenAIConfig() {
  if (getDeploymentMode() === 'busibox') {
    return {
      baseURL: process.env.LITELLM_BASE_URL,
      apiKey: process.env.LITELLM_API_KEY,
    };
  }
  return {
    baseURL: 'https://api.openai.com/v1',
    apiKey: process.env.OPENAI_API_KEY,
  };
}
```

**2. Add DEPLOYMENT_MODE to apps.yml:**

```yaml
env:
  DEPLOYMENT_MODE: "busibox"
```

**3. Update Prisma schema for both modes:**

```prisma
generator client {
  provider = "prisma-client-js"
  binaryTargets = ["native", "rhel-openssl-3.0.x", "debian-openssl-3.0.x"]
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
  directUrl = env("DIRECT_URL")  # Optional for connection pooling
}
```

## Benefits

### 1. Single Source of Truth
- All application definitions in one file
- Easy to see all deployed applications
- Consistent structure across environments

### 2. Environment Flexibility
- Same application definition works in test and production
- Environment-specific values automatically resolved
- Easy to add new environments (staging, dev, etc.)

### 3. Reduced Duplication
- Application structure defined once
- No copy-paste between environments
- Lower risk of configuration drift

### 4. Easy Maintenance
- Add new application: edit one file
- Update application: edit one file
- Port allocation visible in one place

### 5. Type Safety
- Ansible validates variable references
- Deployment fails if required variables missing
- Catch configuration errors before deployment

## Migration Guide

### From Old Architecture

**Before (duplicated):**
```yaml
# inventory/production/group_vars/all/00-main.yml
applications:
  - name: my-app
    port: 3000
    # ... full definition ...

# inventory/test/group_vars/all/00-main.yml
applications:
  - name: my-app
    port: 3000
    # ... full definition (copy-paste) ...
```

**After (centralized):**
```yaml
# group_vars/apps.yml
applications:
  - name: my-app
    port: 3000
    container_ip: "{{ apps_ip }}"  # References env variable
    # ... definition once ...

# inventory/production/group_vars/all/00-main.yml
apps_ip: "10.96.200.201"

# inventory/test/group_vars/all/00-main.yml
apps_ip: "10.96.208.201"
```

### Migration Steps

1. **Backup current configuration:**
   ```bash
   cd provision/ansible
   git checkout -b backup-old-config
   ```

2. **Move app definitions to apps.yml:**
   - Copy application definitions from `inventory/*/group_vars/all/00-main.yml`
   - Replace hardcoded values with variable references
   - Save to `group_vars/apps.yml`

3. **Update environment files:**
   - Remove application definitions
   - Keep only environment-specific variables
   - Add any new required variables (node_env, env_subdomain_suffix)

4. **Test deployment:**
   ```bash
   make test
   ```

5. **Deploy to production:**
   ```bash
   make production
   ```

## Troubleshooting

### Variable Not Found Error

**Error:**
```
FAILED! => {"msg": "The task includes an option with an undefined variable. The error was: 'apps_ip' is undefined"}
```

**Solution:**
Add missing variable to `inventory/{env}/group_vars/all/00-main.yml`:
```yaml
apps_ip: "{{ network_base_octets }}.201"
```

### Application Not Deploying

**Check:**
1. Application defined in `group_vars/apps.yml`
2. All referenced variables defined in environment config
3. Secrets exist in vault for application
4. Port not already in use

**Debug:**
```bash
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags app_deployer -vvv
```

### Wrong Environment Values

**Check:**
1. Correct inventory directory used (`-i inventory/test/` vs `-i inventory/production/`)
2. Variables in correct file (`group_vars/all/00-main.yml`)
3. Variable precedence (inventory > group_vars > defaults)

## Related Documentation

- [Application Data Model](../../specs/002-deploy-app-servers/data-model.md)
- [Deployment Procedures](./deployment-procedures.md)
- [Ansible Setup](../../provision/ansible/SETUP.md)
- [Secrets Management](../configuration/secrets-management.md)

## Summary

The centralized application configuration architecture provides:
- **Consistency** - Same app definition across environments
- **Maintainability** - Single source of truth
- **Flexibility** - Easy to add new apps and environments
- **Safety** - Type-checked variable references
- **Simplicity** - Clear separation of concerns

This architecture aligns with Infrastructure as Code principles and makes Busibox deployments more reliable and maintainable.

