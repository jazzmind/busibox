# Manual Deployment System via AI Portal

**Status**: Implementation Complete (Database & UI)  
**Created**: 2025-01-13  
**Updated**: 2025-01-13  
**Category**: Deployment

## Overview

The deployment system has been transitioned from automatic deploywatch polling to manual, on-demand deployments managed through the AI Portal admin interface. This provides better control, visibility, and safety for production deployments.

## Architecture Changes

### Previous System (Deploywatch)

- **Automatic**: Polled GitHub every hour for new releases
- **Uncontrolled**: Deployed immediately without approval
- **Limited visibility**: Only systemd logs
- **No rollback**: Manual intervention required
- **No staging**: Direct to production

### New System (AI Portal Deployment Manager)

- **Manual**: Admin triggers deployments via UI
- **Controlled**: Requires explicit approval for each deployment
- **Full visibility**: Deployment history, logs, and status tracking
- **Rollback support**: One-click rollback to previous version
- **Staging support**: Test deployments before production

## Database Schema

### New Models

#### GitHubConnection
- Stores encrypted GitHub OAuth tokens per admin user
- Enables access to private repositories
- Tracks token expiry and scopes

#### AppDeploymentConfig
- Links apps to GitHub repositories
- Stores deployment configuration (paths, ports, commands)
- Enables/disables staging environments

#### Deployment
- Tracks each deployment attempt
- Records status, logs, and timing
- Links to previous deployment for rollback

#### AppSecret
- Stores encrypted environment variables
- Managed per-app configuration
- Secure secret storage with AES-256-GCM

#### GitHubRelease
- Caches GitHub release information
- Enables offline viewing of releases
- Tracks which release is currently deployed

## API Endpoints

### GitHub OAuth
- `GET /api/admin/github/connect` - Initiate OAuth flow
- `GET /api/admin/github/callback` - OAuth callback handler
- `GET /api/admin/github/status` - Check connection status
- `DELETE /api/admin/github/disconnect` - Disconnect GitHub

### Deployment Configuration
- `GET /api/admin/deployments/config` - List all configs
- `POST /api/admin/deployments/config` - Create config
- `GET /api/admin/deployments/config/[id]` - Get config details
- `PATCH /api/admin/deployments/config/[id]` - Update config
- `DELETE /api/admin/deployments/config/[id]` - Delete config

### Releases
- `GET /api/admin/deployments/releases/[configId]` - List releases
- `POST /api/admin/deployments/releases/[configId]/sync` - Sync from GitHub

### Deployments
- `POST /api/admin/deployments/deploy` - Trigger deployment
- `POST /api/admin/deployments/rollback` - Rollback deployment

### Secrets
- `GET /api/admin/deployments/secrets?configId=xxx` - List secrets
- `POST /api/admin/deployments/secrets` - Create/update secret
- `DELETE /api/admin/deployments/secrets/[id]` - Delete secret

## UI Components

### DeploymentManager Component
Located at: `src/components/admin/DeploymentManager.tsx`

**Features**:
- GitHub connection status
- Release listing and syncing
- One-click deployment to production/staging
- Current deployment status
- Configuration display

**Integration**:
- Embedded in app detail page (`/admin/apps/[appId]`)
- Only shown for INTERNAL apps
- Requires Admin role

## Deployment Flow

### 1. Initial Setup

1. **Connect GitHub**:
   - Admin navigates to any app detail page
   - Clicks "Connect GitHub Account"
   - Authorizes OAuth with `repo` scope
   - Token stored encrypted in database

2. **Configure Deployment**:
   - Enter repository owner/name
   - Set deploy path and port
   - Configure build/start commands
   - Enable staging if desired

3. **Configure Secrets**:
   - Add environment variables
   - Secrets encrypted with AES-256-GCM
   - Managed per-app

### 2. Deploying a Release

1. **Sync Releases**:
   - Click "Sync from GitHub"
   - Fetches latest releases from GitHub API
   - Caches in database

2. **Select Release**:
   - View release notes
   - Check version number
   - See if pre-release

3. **Deploy**:
   - Click "Deploy to Production" (or Staging)
   - Confirm deployment
   - System creates deployment record
   - Triggers async deployment process

4. **Monitor**:
   - View deployment status
   - Check logs (when implemented)
   - Verify health check

### 3. Rollback (If Needed)

1. **Identify Failed Deployment**:
   - Check deployment status
   - Review error logs

2. **Trigger Rollback**:
   - Click "Rollback" button
   - System deploys previous version
   - Marks failed deployment as ROLLED_BACK

## Staging Environment Support

### Configuration

Enable staging in app deployment config:
```json
{
  "stagingEnabled": true,
  "stagingPort": 3100,
  "stagingPath": "/home-stage"
}
```

### Staging Deployment Process

1. **Deploy to Staging**:
   - Click "Deploy to Staging" for a release
   - System deploys to staging port
   - Creates nginx route at `/<apppath>-stage/`

2. **Test Staging**:
   - Access app at `https://domain.com/<apppath>-stage/`
   - Verify functionality
   - Check logs

3. **Promote to Production**:
   - If staging tests pass, deploy same release to production
   - Or rollback staging and try different release

### Database Cloning (TODO)

For staging environments that need database isolation:
- Clone production database to staging schema
- Update staging .env with staging database URL
- Automatic cleanup of old staging databases

## Security Considerations

### Encryption

- **GitHub Tokens**: Encrypted with AES-256-GCM
- **App Secrets**: Encrypted with AES-256-GCM
- **Key Derivation**: PBKDF2 with 100,000 iterations
- **Key Source**: `ENCRYPTION_KEY` or `BETTER_AUTH_SECRET` env var

### Access Control

- **Admin Only**: All deployment operations require Admin role
- **Audit Logging**: All deployments logged to database
- **OAuth Scopes**: Minimal required scopes (`repo`, `read:user`)

### Token Management

- **Expiry Tracking**: Token expiration monitored
- **Refresh Support**: Refresh tokens stored for renewal
- **Revocation**: Admins can disconnect GitHub anytime

## Migration from Deploywatch

### Steps to Migrate

1. **Deploy Updated AI Portal**:
   - Includes new schema and UI
   - Run `prisma db push` to update database

2. **Add Environment Variables**:
   ```bash
   GITHUB_CLIENT_ID=your_github_oauth_app_id
   GITHUB_CLIENT_SECRET=your_github_oauth_app_secret
   GITHUB_REDIRECT_URI=https://your-domain.com/api/admin/github/callback
   ENCRYPTION_KEY=your_encryption_key  # Optional, uses BETTER_AUTH_SECRET if not set
   ```

3. **Connect GitHub**:
   - Log in as admin
   - Navigate to any app
   - Click "Connect GitHub"

4. **Configure Each App**:
   - For each app in `00-main.yml`
   - Create deployment configuration
   - Add secrets from Ansible vault

5. **Disable Deploywatch** (Optional):
   ```bash
   # On apps-lxc container
   systemctl stop deploywatch.timer
   systemctl disable deploywatch.timer
   ```

### Rollback Plan

If issues occur, deploywatch can be re-enabled:
```bash
systemctl enable deploywatch.timer
systemctl start deploywatch.timer
```

## Implementation Status

### ✅ Completed

- [x] Database schema with all models
- [x] Encryption utilities for secrets
- [x] GitHub OAuth integration
- [x] API routes for all operations
- [x] DeploymentManager UI component
- [x] Integration with app detail page
- [x] AI Portal routing to /home
- [x] Nginx configuration updates

### 🚧 TODO

- [ ] Actual deployment execution logic
  - SSH to container
  - Download release tarball
  - Extract and install dependencies
  - Generate .env with secrets
  - Run build command
  - Restart application
  - Health check verification
  
- [ ] Deployment logs streaming
  - Real-time log display in UI
  - WebSocket or SSE for live updates
  
- [ ] Database cloning for staging
  - PostgreSQL schema cloning
  - Automatic cleanup
  
- [ ] Deployment notifications
  - Email on success/failure
  - Slack/Discord webhooks
  
- [ ] Deployment queue
  - Prevent concurrent deployments
  - Queue management UI

## Testing

### Manual Testing Checklist

- [ ] GitHub OAuth connection
- [ ] Repository access verification
- [ ] Release syncing
- [ ] Deployment configuration creation
- [ ] Secret management
- [ ] Production deployment
- [ ] Staging deployment (if enabled)
- [ ] Rollback functionality
- [ ] Health check verification

### Integration Testing

Create test cases for:
- OAuth flow with invalid tokens
- Deployment with missing secrets
- Rollback with no previous deployment
- Concurrent deployment attempts
- Failed health checks

## Troubleshooting

### GitHub Connection Issues

**Problem**: "Cannot access repository"  
**Solution**: 
- Check OAuth scopes include `repo`
- Verify repository owner/name are correct
- Ensure GitHub token hasn't expired

### Deployment Failures

**Problem**: Deployment status shows FAILED  
**Solution**:
- Check deployment logs
- Verify secrets are configured
- Test SSH access to container
- Check disk space on container

### Secret Decryption Errors

**Problem**: "Failed to decrypt secret"  
**Solution**:
- Verify `ENCRYPTION_KEY` or `BETTER_AUTH_SECRET` is set
- Ensure key hasn't changed since secrets were encrypted
- Re-create secrets if key was rotated

## Future Enhancements

### Planned Features

1. **Multi-Environment Support**:
   - Development, staging, production
   - Environment-specific secrets
   - Promotion workflows

2. **Deployment Pipelines**:
   - Pre-deployment checks
   - Post-deployment tests
   - Automatic rollback on failure

3. **Blue-Green Deployments**:
   - Zero-downtime deployments
   - Traffic switching
   - Gradual rollout

4. **Deployment Scheduling**:
   - Schedule deployments for off-hours
   - Maintenance windows
   - Automatic deployments (with approval)

5. **Advanced Monitoring**:
   - Performance metrics
   - Error rate tracking
   - Resource usage monitoring

## References

- Database Schema: `ai-portal/prisma/schema.prisma`
- API Routes: `ai-portal/src/app/api/admin/deployments/`
- UI Component: `ai-portal/src/components/admin/DeploymentManager.tsx`
- Encryption: `ai-portal/src/lib/crypto.ts`
- GitHub Integration: `ai-portal/src/lib/github.ts`

