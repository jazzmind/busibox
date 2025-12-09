---
title: Application Deployment Cleanup - Remove innovation and project-analysis
category: session-notes
created: 2025-01-13
updated: 2025-01-13
status: completed
---

# Application Deployment Cleanup

## Overview

Cleaned up the Ansible application deployment configuration to remove `innovation` and `project-analysis` apps from automatic infrastructure deployments. These apps will now be deployed manually via the AI Portal's deployment management UI when needed.

## Changes Made

### 1. Removed Apps from Ansible

**Files Modified:**
- `provision/ansible/inventory/production/group_vars/all/00-main.yml`
- `provision/ansible/inventory/test/group_vars/all/00-main.yml`
- `provision/ansible/roles/secrets/vars/vault.example.yml`

**Apps Removed:**
1. **innovation** (port 3003)
   - GitHub: `jazzmind/innovation`
   - Routes: `innovation.domain.com`, `/innovation`
   
2. **project-analysis** (port 3004)
   - GitHub: `jazzmind/tabular-bells`
   - Routes: `projects.domain.com`, `/projects`

### 2. Updated AI Portal Seed Data

**File Modified:**
- `ai-portal/prisma/seed.ts`

**Default Apps (matching Ansible deployments):**
1. **Agent Client** (port 3001)
   - URL: `/agents`
   - Subdomain: `agents.domain.com`
   - Description: AI agent interaction and management interface

2. **Doc Intel** (port 3002)
   - URL: `/docs`
   - Subdomain: `docs.domain.com`
   - Description: Document intelligence and analysis platform

3. **Video Generator** (port 3000 - part of ai-portal)
   - URL: `/videos`
   - Description: AI-powered video content generation

4. **Video Library** (port 3000 - part of ai-portal)
   - URL: `/videos/library`
   - Description: Browse and manage generated videos

## Rationale

### Why Remove from Ansible?

1. **Flexibility**: Not all environments need these apps
2. **Manual Control**: Deploy only when needed via AI Portal UI
3. **Testing**: Easier to test deployment system with optional apps
4. **Resource Management**: Save resources by not running unused apps

### Why Keep agent-client and doc-intel?

1. **Core Functionality**: These are fundamental to the platform
2. **Default Install**: Every environment should have these
3. **Integration**: AI Portal integrates with these apps for core features
4. **Stable**: Well-tested and production-ready

## Current Application Architecture

### Ansible-Deployed Apps (Always Installed)

```
┌─────────────────────────────────────────────────────────┐
│ Apps Container (10.96.200.31)                           │
├─────────────────────────────────────────────────────────┤
│ Port 3000: ai-portal                                    │
│  - Main portal UI                                       │
│  - Deployment management                                │
│  - Admin interface                                      │
│  - Routes: /, /home, /portal                           │
├─────────────────────────────────────────────────────────┤
│ Port 3001: agent-client                                │
│  - AI agent interface                                   │
│  - Routes: /agents, agents.domain.com                  │
├─────────────────────────────────────────────────────────┤
│ Port 3002: doc-intel                                    │
│  - Document intelligence                                │
│  - Routes: /docs, docs.domain.com                      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Agent Container (10.96.200.207)                         │
├─────────────────────────────────────────────────────────┤
│ Port 4111: agent-server                                │
│  - Internal API only (no UI, no nginx routing)         │
│  - Agent orchestration                                  │
│  - Token service                                        │
└─────────────────────────────────────────────────────────┘
```

### Manually Deployed Apps (Via AI Portal UI)

```
Future deployments can include:
- innovation (port 3003+)
- project-analysis (port 3004+)
- Any other Next.js/Node.js applications
```

## Port Allocation

### Reserved Ports (Ansible)
- **3000**: ai-portal
- **3001**: agent-client
- **3002**: doc-intel

### Available Ports (Manual Deployment)
- **3003+**: Available for manual app deployments

### Internal API Ports
- **4111**: agent-server (no public routes)

## Migration Steps

### For Existing Environments

If you have `innovation` or `project-analysis` already deployed:

1. **Keep Running**: Existing deployments continue to work
2. **Manual Management**: Future updates via AI Portal deployment UI
3. **Remove from Ansible**: Next `make apps` won't redeploy them
4. **Optional Cleanup**: Manually stop/remove if not needed:
   ```bash
   ssh root@10.96.200.31
   pm2 delete innovation
   pm2 delete project-analysis
   pm2 save
   ```

### For Fresh Installations

1. Deploy infrastructure with Ansible:
   ```bash
   cd provision/ansible
   make apps  # Deploys ai-portal, agent-client, doc-intel
   ```

2. Seed AI Portal database:
   ```bash
   ssh root@10.96.200.31
   cd /srv/apps/ai-portal
   npm run db:seed
   ```

3. Deploy additional apps via AI Portal UI:
   - Connect GitHub account
   - Configure deployment for innovation/project-analysis
   - Deploy specific releases
   - Manage secrets via UI

## Benefits of This Approach

### Infrastructure as Code (Core Apps)
- ✅ Reproducible base deployment
- ✅ Version controlled configuration
- ✅ Consistent across environments
- ✅ Minimal manual steps

### Dynamic Deployment (Optional Apps)
- ✅ Deploy only what's needed
- ✅ Test new apps without Ansible changes
- ✅ Quick rollback capability
- ✅ Environment-specific deployments
- ✅ Staging environments per app

## Database Seeding

The AI Portal seed now creates default apps that match Ansible deployments:

```typescript
// Default apps created on first seed
const defaultApps = [
  { name: 'Agent Client', url: '/agents', order: 1 },
  { name: 'Doc Intel', url: '/docs', order: 2 },
  { name: 'Video Generator', url: '/videos', order: 3 },
  { name: 'Video Library', url: '/videos/library', order: 4 },
];
```

**Note**: innovation and project-analysis are NOT created by seed - they must be added manually via the admin UI if needed.

## Deployment Workflow

### Default Apps (Ansible)
```bash
# Update configuration
vi provision/ansible/inventory/production/group_vars/all/00-main.yml

# Deploy
cd provision/ansible
make apps

# Seed database (first time only)
ssh root@10.96.200.31
cd /srv/apps/ai-portal
npm run db:seed
```

### Optional Apps (AI Portal UI)
```bash
# 1. Admin logs into AI Portal
# 2. Admin > Apps > Add New App
# 3. Connect GitHub account (if not already)
# 4. Configure deployment settings
# 5. Add secrets via UI
# 6. Sync releases
# 7. Deploy specific release
# 8. Monitor deployment logs
# 9. Rollback if needed
```

## Secrets Management

### Removed from vault.example.yml
```yaml
# These sections were removed:
innovation:
  database_url: ...
  better_auth_secret: ...
  jwt_secret: ...
  openai_api_key: ...
  sso_jwt_secret: ...
  oauth_client_secret: ...

project-analysis:
  database_url: ...
  openai_api_key: ...
  jwt_secret: ...
  sso_jwt_secret: ...
  oauth_client_secret: ...
```

### Secrets for Optional Apps
Managed via AI Portal deployment management UI:
- Encrypted in database with AES-256-GCM
- Per-environment configuration
- Easy rotation via UI
- Audit trail of changes

## Testing Checklist

After deploying these changes:

- [ ] Deploy to test environment: `INV=test make apps`
- [ ] Verify ai-portal accessible at domain root
- [ ] Verify agent-client at `/agents`
- [ ] Verify doc-intel at `/docs`
- [ ] Check PM2 process list (should only show 3 apps)
- [ ] Verify nginx routing for default apps
- [ ] Test AI Portal deployment management UI
- [ ] Deploy a test app via UI (e.g., innovation to staging)
- [ ] Verify optional app deployment works
- [ ] Test rollback functionality

## Documentation Updates

Related documentation:
- `docs/deployment/manual-deployment-system.md` - Deployment management
- `docs/guides/github-oauth-setup.md` - GitHub integration
- `docs/reference/ai-portal-environment-variables.md` - Environment variables

## Commits

### Busibox Repository
**Commit**: `2adb8b2`
- Removed innovation and project-analysis from inventories
- Removed secrets from vault.example.yml
- Freed ports 3003 and 3004

### AI Portal Repository
**Commit**: `96dd0ac`
- Updated seed.ts with agent-client and doc-intel
- Refactored to use loop for role permissions
- Updated audit log details

## Next Steps

1. **Deploy to Test**: Test the updated Ansible configuration
2. **Verify Seeding**: Ensure new seed creates correct apps
3. **Test Deployment UI**: Deploy innovation via AI Portal UI
4. **Document Workflow**: Update deployment guides with new process
5. **Production Deploy**: Roll out to production when tested

## Notes

- **No Breaking Changes**: Existing installations continue to work
- **Backward Compatible**: Can still manually deploy removed apps
- **Port Management**: Freed ports can be reused for future apps
- **Flexible Architecture**: Easy to add/remove apps via UI

