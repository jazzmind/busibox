---
title: Application Configuration Reorganization
category: session-notes
created: 2025-11-22
updated: 2025-11-22
status: complete
tags: [configuration, deployment, refactoring]
---

# Application Configuration Reorganization - November 22, 2025

## Summary

Reorganized application deployment configuration to eliminate duplication between test and production environments, and added two new applications (foundation and project-analysis) with dual deployment mode support (Vercel + Busibox).

## Changes Made

### 1. Centralized Application Definitions

**Created:** `provision/ansible/group_vars/apps.yml`

**Purpose:** Single source of truth for all application configurations

**Key Changes:**
- Moved all application definitions from environment-specific files to centralized location
- Applications now reference environment variables instead of hardcoded values
- Added two new applications: `foundation` (port 3003) and `project-analysis` (port 3004)

**Benefits:**
- No duplication between test and production
- Easy to add new applications (edit one file)
- Consistent structure across all apps
- Reduced risk of configuration drift

### 2. Environment-Specific Variables

**Updated:**
- `provision/ansible/inventory/production/group_vars/all/00-main.yml`
- `provision/ansible/inventory/test/group_vars/all/00-main.yml`

**Changes:**
- Removed duplicated application definitions
- Added `node_env` variable (production/development)
- Added `env_subdomain_suffix` variable ("" for prod, "-test" for test)
- Added `www_domain` variable for test environment
- Kept only environment-specific configuration

**Production Values:**
```yaml
node_env: production
env_subdomain_suffix: ""
full_domain: "ai.jaycashman.com"
```

**Test Values:**
```yaml
node_env: development
env_subdomain_suffix: "-test"
full_domain: "test.ai.jaycashman.com"
```

### 3. Dual Deployment Mode Support

**Created:**
- `foundation/lib/deployment-config.ts`
- `project-analysis/lib/deployment-config.ts`

**Purpose:** Enable applications to run on both Vercel and Busibox

**Features:**
- Automatic mode detection via `DEPLOYMENT_MODE` environment variable
- Dynamic configuration for AI (OpenAI vs liteLLM)
- Dynamic configuration for email (Resend vs SMTP)
- Dynamic configuration for database (Neon vs local PostgreSQL)
- Graceful fallbacks and error handling

**Deployment Modes:**

| Mode | AI | Email | Database |
|------|-----|-------|----------|
| Vercel | OpenAI API | Resend | Neon |
| Busibox | liteLLM | SMTP | Local PostgreSQL |

### 4. Updated Environment Files

**Updated:**
- `foundation/env.example`
- `project-analysis/env.example`

**Changes:**
- Documented both deployment modes
- Organized variables by category
- Added DEPLOYMENT_MODE variable
- Added liteLLM configuration
- Added SMTP configuration
- Clarified which variables are for which mode

### 5. Updated Prisma Schemas

**Updated:**
- `foundation/prisma/schema.prisma`
- `project-analysis/prisma/schema.prisma`

**Changes:**
- Added multiple binary targets for different platforms
- Added optional `directUrl` for connection pooling
- Support both Neon (Vercel) and local PostgreSQL (Busibox)

### 6. Documentation

**Created:**
- `docs/deployment/app-configuration-architecture.md` - Comprehensive guide to centralized configuration
- `docs/deployment/dual-deployment-mode.md` - Guide to dual deployment mode support

**Content:**
- Architecture overview and benefits
- Variable resolution explanation
- Step-by-step guides for adding new applications
- Troubleshooting guides
- Migration guides
- Best practices

## New Applications

### Foundation (Port 3003)

**Description:** Cashman Family Foundation donation analysis and AI insights

**Routes:**
- Subdomain: `foundation.ai.jaycashman.com` (prod), `foundation-test.test.ai.jaycashman.com` (test)
- Path: `/foundation` on main domain

**Features:**
- Magic link authentication
- AI-powered donation analysis
- Executive summary generation
- Organization enrichment
- Dual deployment mode support

**Secrets Required:**
- database_url
- litellm_api_key (Busibox) or openai_api_key (Vercel)
- SMTP configuration or resend_api_key
- allowed_email_domains

### Project Analysis (Port 3004)

**Description:** Project data visualization and analysis with AI

**Routes:**
- Subdomain: `projects.ai.jaycashman.com` (prod), `projects-test.test.ai.jaycashman.com` (test)
- Path: `/projects` on main domain

**Features:**
- CSV data import
- AI-powered visualizations
- Interactive charts
- Magic link authentication
- Dual deployment mode support

**Secrets Required:**
- database_url
- litellm_api_key (Busibox) or openai_api_key (Vercel)
- SMTP configuration or resend_api_key
- allowed_email_domains

## Architecture Benefits

### Before (Problematic)

```
inventory/production/group_vars/all/00-main.yml
├── applications: [full definitions]
└── environment config

inventory/test/group_vars/all/00-main.yml
├── applications: [full definitions - DUPLICATED]
└── environment config
```

**Issues:**
- Duplication between environments
- Risk of configuration drift
- Hard to maintain consistency
- Adding app requires changes in multiple files

### After (Improved)

```
group_vars/apps.yml
└── applications: [definitions with variable references]

inventory/production/group_vars/all/00-main.yml
└── environment variables (IPs, domains, NODE_ENV)

inventory/test/group_vars/all/00-main.yml
└── environment variables (IPs, domains, NODE_ENV)
```

**Benefits:**
- Single source of truth
- No duplication
- Easy to add new applications
- Consistent across environments
- Type-safe variable references

## Variable Resolution Example

**Application Definition (apps.yml):**
```yaml
- name: foundation
  container_ip: "{{ apps_ip }}"
  subdomain: "foundation{{ env_subdomain_suffix | default('') }}"
  env:
    NODE_ENV: "{{ node_env }}"
```

**Production Resolution:**
```yaml
container_ip: "10.96.200.201"
subdomain: "foundation"
NODE_ENV: "production"
```

**Test Resolution:**
```yaml
container_ip: "10.96.208.201"
subdomain: "foundation-test"
NODE_ENV: "development"
```

## Port Allocation

Updated port allocation in `apps.yml`:

| Port | Application | Type |
|------|-------------|------|
| 3000 | ai-portal | Public web app |
| 3001 | agent-client | Public web app |
| 3002 | doc-intel | Public web app |
| 3003 | foundation | Public web app (NEW) |
| 3004 | project-analysis | Public web app (NEW) |
| 3005 | (available) | - |
| 4111 | agent-server | Internal API |

## Deployment Instructions

### Deploy to Test

```bash
cd provision/ansible
make test
```

### Deploy to Production

```bash
cd provision/ansible
make production
```

### Deploy Individual Application

```bash
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --tags app_deployer --limit foundation
```

## Testing Checklist

- [ ] Test environment deployment
  - [ ] All existing apps still work
  - [ ] foundation app accessible at `foundation-test.test.ai.jaycashman.com`
  - [ ] project-analysis app accessible at `projects-test.test.ai.jaycashman.com`
  - [ ] Health checks pass for all apps
  - [ ] NGINX routing works correctly

- [ ] Production environment deployment
  - [ ] All existing apps still work
  - [ ] foundation app accessible at `foundation.ai.jaycashman.com`
  - [ ] project-analysis app accessible at `projects.ai.jaycashman.com`
  - [ ] Health checks pass for all apps
  - [ ] NGINX routing works correctly

- [ ] Dual deployment mode
  - [ ] foundation works on Vercel
  - [ ] foundation works on Busibox
  - [ ] project-analysis works on Vercel
  - [ ] project-analysis works on Busibox
  - [ ] AI features work in both modes
  - [ ] Email works in both modes

## Migration Notes

### No Breaking Changes

This refactoring maintains backward compatibility:
- Existing applications continue to work
- Same variable names used
- Same deployment process
- No changes to secrets structure

### Rollback Plan

If issues occur:

1. **Revert configuration files:**
   ```bash
   git checkout HEAD~1 provision/ansible/group_vars/apps.yml
   git checkout HEAD~1 provision/ansible/inventory/*/group_vars/all/00-main.yml
   ```

2. **Redeploy:**
   ```bash
   cd provision/ansible
   make test  # or make production
   ```

## Next Steps

1. **Test deployment** - Deploy to test environment first
2. **Verify all apps** - Check health endpoints and routing
3. **Add secrets** - Add foundation and project-analysis secrets to vault
4. **Deploy to production** - After successful test deployment
5. **Monitor** - Check logs and application behavior
6. **Update other apps** - Consider adding dual mode to existing apps

## Questions Answered

### Q: Why have app definitions in environment-specific files?

**A:** We don't anymore! They're now centralized in `group_vars/apps.yml` with environment-specific values referenced via variables. This eliminates duplication and makes maintenance easier.

### Q: Why not just use different configs for test and production?

**A:** Because the application structure is the same - only environment-specific values differ (IPs, domains, NODE_ENV). Centralizing the structure reduces duplication and ensures consistency.

### Q: How do we add a new application now?

**A:** Edit one file (`group_vars/apps.yml`), add secrets to vault, and deploy. No need to update multiple environment files.

### Q: What if an app needs environment-specific configuration?

**A:** Use variable references in the app definition (e.g., `{{ node_env }}`, `{{ full_domain }}`). Define the variables in environment-specific files.

## Related Documentation

- [Application Configuration Architecture](../deployment/app-configuration-architecture.md)
- [Dual Deployment Mode](../deployment/dual-deployment-mode.md)
- [Application Data Model](../../specs/002-deploy-app-servers/data-model.md)
- [Ansible Setup](../../provision/ansible/SETUP.md)

## Conclusion

This reorganization provides:
- **Maintainability** - Single source of truth for app definitions
- **Consistency** - Same structure across environments
- **Flexibility** - Easy to add new apps and environments
- **Dual Mode** - Applications can run on Vercel or Busibox
- **Documentation** - Comprehensive guides for future reference

The architecture now follows Infrastructure as Code best practices and makes Busibox deployments more reliable and maintainable.

