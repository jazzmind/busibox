---
title: Branch Deployment Guide
category: guides
created: 2025-01-13
updated: 2025-01-13
status: active
---

# Branch Deployment Guide

## Overview

Deploy applications directly from Git branches without creating release tags. This is perfect for development, testing, and continuous deployment workflows.

## Why Branch Deployment?

**Traditional Release Deployment:**
- ❌ Requires creating a release tag for every change
- ❌ Slower development/testing cycle
- ❌ Extra steps to test changes

**Branch Deployment:**
- ✅ Deploy directly from `main`, `dev`, or feature branches
- ✅ Faster iteration during development
- ✅ Test changes before creating official releases
- ✅ Support continuous deployment workflows

## Two Ways to Deploy from Branch

### 1. Using AI Portal UI

**Steps:**
1. Navigate to Admin → Apps → [Your App] → Deployment Management
2. Find the "Deploy from Branch" section
3. Click "Deploy main to Production" (or Staging)
4. Confirm deployment
5. Monitor deployment logs

**Features:**
- Visual feedback during deployment
- Real-time log streaming
- Deployment history tracking
- Automatic rollback on failure

**When to Use:**
- Testing new features
- Quick fixes that need immediate deployment
- Continuous deployment from main branch

### 2. Using Ansible Script

**Basic Usage:**
```bash
cd /path/to/busibox
bash scripts/deploy-app.sh <app_name> [environment] [branch]
```

**Examples:**
```bash
# Deploy ai-portal from main branch to production
bash scripts/deploy-app.sh ai-portal production main

# Deploy doc-intel from dev branch to test
bash scripts/deploy-app.sh doc-intel test dev

# Deploy agent-client from feature branch to test
bash scripts/deploy-app.sh agent-client test feature/new-ui
```

**What It Does:**
1. Downloads branch archive from GitHub
2. Creates backup of current deployment
3. Extracts files to deploy path
4. Installs dependencies (`npm install`)
5. Generates Prisma client (if applicable)
6. Builds application (`npm run build`)
7. Restarts with PM2
8. Verifies health check

## Deployment Workflow Comparison

### Release Deployment (Traditional)

```mermaid
graph LR
    A[Make Changes] --> B[Commit & Push]
    B --> C[Create Release Tag]
    C --> D[AI Portal: Sync Releases]
    D --> E[AI Portal: Deploy Release]
    E --> F[Application Running]
```

**Time:** ~10-15 minutes (including release creation)

### Branch Deployment (New)

```mermaid
graph LR
    A[Make Changes] --> B[Commit & Push]
    B --> C[Deploy from Branch]
    C --> D[Application Running]
```

**Time:** ~3-5 minutes (no release needed)

## Configuration

### AI Portal

No additional configuration needed! Branch deployment works automatically once you've configured deployment for an app.

**Deployment Type Tracking:**
- The system tracks whether each deployment came from a RELEASE or BRANCH
- This is visible in deployment history
- Example: "main (branch)" vs "v1.2.3 (release)"

### Ansible/Busibox

The `deploy-app.sh` script works with your existing Ansible setup:

**Requirements:**
- Ansible configured (already done for your apps)
- GitHub access token (for private repos)
- SSH access to target containers

**Configuration:**
Apps are defined in inventory files:
- `inventory/production/group_vars/all/00-main.yml`
- `inventory/test/group_vars/all/00-main.yml`

## Use Cases

### Development Workflow

**Scenario:** Testing a new feature before release

```bash
# 1. Push feature branch to GitHub
git push origin feature/new-dashboard

# 2. Deploy to test environment
bash scripts/deploy-app.sh ai-portal test feature/new-dashboard

# 3. Test the feature
# Visit: https://test.your-domain.com

# 4. If good, merge to main
git checkout main
git merge feature/new-dashboard
git push origin main

# 5. Deploy main to production
bash scripts/deploy-app.sh ai-portal production main
```

### Continuous Deployment

**Scenario:** Auto-deploy main branch on every commit

```yaml
# .github/workflows/deploy.yml
name: Deploy to Production
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to production
        run: |
          ssh deploy@your-server "cd /root/busibox && \
            bash scripts/deploy-app.sh ai-portal production main"
```

### Hot Fix Deployment

**Scenario:** Critical bug fix needs immediate deployment

```bash
# 1. Fix bug in local branch
git commit -am "Fix critical auth bug"
git push origin main

# 2. Deploy immediately (no release needed)
bash scripts/deploy-app.sh ai-portal production main

# Time saved: ~5-10 minutes vs creating release
```

## Branch Deployment vs Release Deployment

| Feature | Branch | Release |
|---------|--------|---------|
| **Speed** | Fast (no release creation) | Slower (requires release) |
| **Testing** | Perfect for dev/test | Use for production |
| **Rollback** | Manual (deploy previous commit) | Easy (deploy previous release) |
| **Tracking** | By commit SHA | By release tag (v1.2.3) |
| **Best For** | Development, testing, hot fixes | Production releases |
| **Changelog** | Manual | Included in release notes |

## Best Practices

### When to Use Branch Deployment

✅ **Do use branch deployment for:**
- Testing new features in staging
- Development environments
- Hot fixes that need immediate deployment
- Continuous deployment from main
- Feature branch testing

❌ **Don't use branch deployment for:**
- Major production releases (use tagged releases)
- When you need formal release notes
- When rollback needs to be simple (releases are easier)

### Recommended Workflow

```
Development:
  └─ Deploy feature branches to test
      └─ Test and iterate
          └─ Merge to main
              └─ Deploy main to staging
                  └─ Final testing
                      └─ Create release
                          └─ Deploy release to production
```

### Safety Tips

1. **Always deploy to staging first**
   ```bash
   bash scripts/deploy-app.sh ai-portal test main  # Test first
   bash scripts/deploy-app.sh ai-portal production main  # Then production
   ```

2. **Check deployment logs**
   ```bash
   bash scripts/tail-app-logs.sh ai-portal production
   ```

3. **Verify health check**
   ```bash
   curl -f https://your-domain.com/api/health
   ```

4. **Keep backups enabled**
   - Automatic backups are created before each deployment
   - Located at: `{deploy_path}.backup`

## Rollback

### Via AI Portal

If a branch deployment fails or causes issues:
1. Go to Deployment History
2. Find the last successful deployment
3. Click "Rollback to this version"

### Via Ansible

```bash
# Restore from automatic backup
ssh root@container-ip
mv /srv/apps/ai-portal /srv/apps/ai-portal.failed
mv /srv/apps/ai-portal.backup /srv/apps/ai-portal
pm2 restart ai-portal
```

Or deploy the previous commit:
```bash
# Find previous commit SHA
git log --oneline -5

# Deploy that commit
bash scripts/deploy-app.sh ai-portal production <commit-sha>
```

## Troubleshooting

### "Branch not found or access denied"

**Cause:** Branch doesn't exist or GitHub token doesn't have access

**Solution:**
```bash
# Check branch exists
git ls-remote origin <branch-name>

# Verify GitHub token
cat ~/.github_token  # On target container
```

### "Build failed"

**Cause:** Build errors in the code

**Solution:**
1. Check the build logs in the Ansible output
2. Fix the issue locally
3. Push fix to branch
4. Deploy again

### "Health check failed"

**Cause:** Application didn't start properly

**Solution:**
```bash
# Check application logs
bash scripts/tail-app-logs.sh ai-portal production

# Check PM2 status
ssh root@container-ip "pm2 list"

# Restart manually if needed
ssh root@container-ip "pm2 restart ai-portal"
```

## Database Migrations

**Important:** Branch deployments don't automatically run database migrations.

**For schema changes:**
```bash
# After branch deployment
ssh root@container-ip
cd /srv/apps/ai-portal
npx prisma migrate deploy  # Or prisma db push
```

**Or include in deployment:**
Configure in AI Portal deployment settings:
- Build command: `npm run build && npx prisma migrate deploy`

## Examples

### Deploy Latest Main to Production

```bash
cd /root/busibox
bash scripts/deploy-app.sh ai-portal production main
```

### Deploy Feature Branch to Test

```bash
bash scripts/deploy-app.sh doc-intel test feature/new-processor
```

### Deploy to Multiple Apps

```bash
# Deploy all apps from main
for app in ai-portal agent-client doc-intel; do
  bash scripts/deploy-app.sh $app production main
done
```

## Monitoring

### Check Deployed Version

**Quick version check:**
```bash
bash scripts/check-app-version.sh ai-portal production
```

**Output example:**
```
╔════════════════════════════════════════════════════════════╗
║         Application Version Info                          ║
╚════════════════════════════════════════════════════════════╝

App:         ai-portal
Environment: production
Container:   10.96.200.10
Deploy Path: /srv/apps/ai-portal

✓ Deployment version found

Deployment Type: BRANCH
Branch: main
Commit: abc123d (abc123def456...)
Deployed At: 2025-01-13T12:34:56Z
Deployed By: ansible
Environment: production
```

**Manual check:**
```bash
# SSH to container and read version file
ssh root@container-ip "cat /srv/apps/ai-portal/.deployed-version"

# Or use jq for pretty output
ssh root@container-ip "cat /srv/apps/ai-portal/.deployed-version | jq ."
```

**Version file format:**
```json
{
  "type": "branch",
  "branch": "main",
  "commit": "abc123def456...",
  "deployed_at": "2025-01-13T12:34:56Z",
  "deployed_by": "ansible",
  "environment": "production"
}
```

### Check Deployment Status

```bash
# View logs during deployment
bash scripts/tail-app-logs.sh ai-portal production

# Check application status
ssh root@container-ip "pm2 list"

# Test application
curl https://your-domain.com/api/health
```

### Deployment History

In AI Portal:
- Navigate to app deployment page
- View "Deployment History" section
- Filter by type: "BRANCH" or "RELEASE"
- See commit SHA, timestamp, status

## Related Documentation

- **Manual Deployment System**: `/docs/deployment/manual-deployment-system.md`
- **Deployment Configuration**: `/docs/DEPLOYMENT_CONFIG_IMPROVEMENTS.md`
- **Application Logs**: `/docs/guides/viewing-application-logs.md`
- **Ansible Setup**: `/provision/ansible/SETUP.md`

## Summary

Branch deployment gives you the flexibility to deploy any Git branch directly, making development and testing much faster. Use it for development/testing workflows, and save tagged releases for formal production deployments.

**Quick Commands:**
```bash
# Test
bash scripts/deploy-app.sh ai-portal test main

# Production
bash scripts/deploy-app.sh ai-portal production main

# Feature branch
bash scripts/deploy-app.sh ai-portal test feature/my-feature
```

Happy deploying! 🚀

