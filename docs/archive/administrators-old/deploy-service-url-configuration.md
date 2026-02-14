---
title: "Deploy Service URL Configuration"
category: "administrator"
order: 22
description: "Automatic configuration of DEPLOYMENT_SERVICE_URL for AI Portal"
published: true
---

# Deploy Service URL Configuration

## Summary

The `DEPLOYMENT_SERVICE_URL` environment variable is now automatically configured during deployment and should **never** be set manually in user `.env` files.

## Changes Made

### 1. Ansible Configuration

**File**: `provision/ansible/group_vars/all/apps.yml`

Added to ai-portal app configuration:

```yaml
- name: ai-portal
  env:
    # ... existing vars ...
    # Deployment Service (deployed to authz-lxc)
    DEPLOYMENT_SERVICE_URL: "http://{{ authz_ip }}:8011/api/v1/deployment"
```

**Result**: 
- Staging: `http://10.96.200.210:8011/api/v1/deployment`
- Production: `http://10.96.200.210:8011/api/v1/deployment`

### 2. Docker Development

**File**: `docker-compose.dev.yml`

Added to ai-portal service:

```yaml
services:
  ai-portal:
    environment:
      # ... existing vars ...
      # Deployment Service - internal container URL for server-side deployment
      DEPLOYMENT_SERVICE_URL: http://deploy-api:8011/api/v1/deployment
```

**Result**: Uses Docker service name for internal networking.

### 3. Docker Production

**File**: `docker-compose.prod.yml`

Added to ai-portal service (same as dev):

```yaml
services:
  ai-portal:
    environment:
      # ... existing vars ...
      # Deployment Service - internal container URL for server-side deployment
      DEPLOYMENT_SERVICE_URL: http://deploy-api:8011/api/v1/deployment
```

### 4. Documentation Updates

**File**: `ai-portal/env.example`

Added comment explaining automatic configuration:

```bash
# Deployment Service
# NOTE: This is automatically configured during deployment via Ansible/Docker.
# Do NOT set this in your .env file - it's set dynamically based on environment
# DEPLOYMENT_SERVICE_URL - DO NOT SET (configured automatically)
```

**File**: `ai-portal/docs/deployment/DEPLOY_SERVICE_CONNECTION.md`

Complete documentation on:
- How automatic configuration works
- Network architecture for each environment
- Troubleshooting connection issues
- How to make changes properly

## Why This Approach?

### Problems with Manual Configuration

❌ **User confusion** - Users don't know what URL to use  
❌ **Environment mismatch** - Wrong URL for wrong environment  
❌ **Deployment errors** - Forgot to set the variable  
❌ **Security risks** - Hardcoded IPs in version control  

### Benefits of Automatic Configuration

✅ **Environment-aware** - Correct URL for each environment automatically  
✅ **Zero user configuration** - Works out of the box  
✅ **Consistent** - Same approach as other service URLs  
✅ **Maintainable** - Change once in config, applies everywhere  

## Configuration Matrix

| Environment | Method | URL | Resolved By |
|-------------|--------|-----|-------------|
| Docker Dev | `docker-compose.dev.yml` | `http://deploy-api:8011/api/v1/deployment` | Docker DNS |
| Docker Prod | `docker-compose.prod.yml` | `http://deploy-api:8011/api/v1/deployment` | Docker DNS |
| Ansible Staging | `apps.yml` + inventory | `http://10.96.200.210:8011/api/v1/deployment` | Jinja2 template |
| Ansible Production | `apps.yml` + inventory | `http://10.96.200.210:8011/api/v1/deployment` | Jinja2 template |
| Local Dev (fallback) | Client code | `http://localhost:8011/api/v1/deployment` | Hardcoded fallback |

## Deployment Process

### Docker

1. **Start services**:
   ```bash
   cd /Users/wsonnenreich/Code/busibox
   docker compose -f docker-compose.local.yml -f docker-compose.dev.yml up
   ```

2. **Environment variable is set automatically** in the ai-portal container

3. **Verify**:
   ```bash
   docker compose -f docker-compose.local.yml exec ai-portal env | grep DEPLOYMENT_SERVICE_URL
   # Output: DEPLOYMENT_SERVICE_URL=http://deploy-api:8011/api/v1/deployment
   ```

### Ansible

1. **Deploy ai-portal**:
   ```bash
   cd /root/busibox/provision/ansible
   make deploy-ai-portal INV=inventory/production
   ```

2. **Ansible generates `.env` file** with resolved variables:
   ```bash
   # On apps-lxc container
   cat /srv/apps/ai-portal/.env | grep DEPLOYMENT_SERVICE_URL
   # Output: DEPLOYMENT_SERVICE_URL="http://10.96.200.210:8011/api/v1/deployment"
   ```

3. **Systemd service loads the env file** and starts ai-portal

## Testing

### Test Deploy Service Connection

**From Docker**:
```bash
docker compose -f docker-compose.local.yml exec ai-portal curl http://deploy-api:8011/health/live
```

**From Ansible (Production)**:
```bash
ssh root@10.96.200.201
curl http://10.96.200.210:8011/health/live
```

**Expected Response**:
```json
{"status":"ok"}
```

### Test from AI Portal

1. Navigate to AI Portal admin
2. Click "Add App"
3. Enter a GitHub URL
4. Click "Register App"
5. Check logs for deployment service connection

## Troubleshooting

### Connection Refused

**Symptom**: `ECONNREFUSED` when deploying apps

**Docker**:
```bash
# Check deploy-api is running
docker compose -f docker-compose.local.yml ps deploy-api

# Check logs
docker compose -f docker-compose.local.yml logs deploy-api

# Restart if needed
docker compose -f docker-compose.local.yml restart deploy-api
```

**Ansible**:
```bash
# Check service status
ssh root@10.96.200.210
systemctl status deploy-api

# Check logs
journalctl -u deploy-api -n 50

# Restart if needed
systemctl restart deploy-api
```

### Missing Environment Variable

**Symptom**: `DEPLOYMENT_SERVICE_URL` is undefined

**Docker**:
```bash
# Rebuild with correct compose file
docker compose -f docker-compose.local.yml -f docker-compose.dev.yml up --build ai-portal
```

**Ansible**:
```bash
# Redeploy ai-portal
cd /root/busibox/provision/ansible
make deploy-ai-portal INV=inventory/production
```

## Future Apps

When adding new apps that need deployment service access:

1. **Add to `apps.yml`**:
   ```yaml
   - name: my-app
     env:
       DEPLOYMENT_SERVICE_URL: "http://{{ authz_ip }}:8011/api/v1/deployment"
   ```

2. **Add to Docker compose**:
   ```yaml
   services:
     my-app:
       environment:
         DEPLOYMENT_SERVICE_URL: http://deploy-api:8011/api/v1/deployment
   ```

3. **No user configuration needed** - it just works!

## Related Documentation

- [Deploy Service Implementation](../../DEPLOYMENT_SERVICE_IMPLEMENTATION.md)
- [AI Portal Deploy Service Connection](../../../ai-portal/docs/deployment/DEPLOY_SERVICE_CONNECTION.md)
- [Apps Configuration](../architecture/apps-configuration.md)

## Files Modified

- `provision/ansible/group_vars/all/apps.yml`
- `docker-compose.dev.yml`
- `docker-compose.prod.yml`
- `ai-portal/env.example`
- `ai-portal/docs/deployment/DEPLOY_SERVICE_CONNECTION.md` (created)
- `busibox/docs/deployment/deploy-service-url-configuration.md` (this file)
