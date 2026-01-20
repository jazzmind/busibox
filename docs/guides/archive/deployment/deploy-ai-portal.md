---
title: Deploy ai-portal Application
created: 2025-10-30
updated: 2025-10-30
status: active
category: deployment
tags: [ai-portal, nextjs, deployment, test]
---

# Deploy ai-portal Application

## Overview

This guide walks through deploying the ai-portal application to the test environment.

## Prerequisites

- [x] Containers created and running
- [x] Vault configured with all secrets
- [x] GitHub token added to vault (for private repo access)
- [x] SSL certificates deployed
- [x] NGINX placeholder routing tested

## Deployment Steps

### 1. Deploy Application (Without Placeholder Mode)

From your Proxmox host or admin workstation:

```bash
cd /root/busibox/provision/ansible

# Deploy to apps container - this will:
# - Deploy .env files with secrets
# - Generate deploywatch scripts
# - Trigger initial app deployment
# - Start apps with PM2
ansible-playbook -i inventory/test/hosts.yml site.yml \
  --tags app_deployer \
  --ask-vault-pass \
  --limit TEST-apps-lxc
```

This will:
1. ✅ Deploy environment files to `/srv/apps/ai-portal/.env`
2. ✅ Generate deploywatch script in `/srv/deploywatch/apps/`
3. ✅ Clone from `jazzmind/ai-portal` GitHub repo
4. ✅ Run `npm install`
5. ✅ Run `npm run build`
6. ✅ Start with PM2

**Expected time**: 3-5 minutes (depending on build time)

### 2. Monitor Deployment

While deployment is running, monitor in another terminal:

```bash
# SSH into apps container
ssh root@10.96.201.201

# Watch deployment logs
tail -f /srv/apps/ai-portal/logs/deploy.log

# Watch PM2 status
watch -n 1 pm2 status

# Check application logs
pm2 logs ai-portal --lines 50
```

### 3. Verify Application Health

Check that ai-portal is running:

```bash
# Check PM2 status
ssh root@10.96.201.201 pm2 status

# Should show:
# ai-portal | online | 0 | ...

# Test health endpoint directly
curl http://10.96.201.201:3000/api/health

# Should return 200 OK
```

### 4. Update NGINX to Remove Placeholder

Once the app is confirmed healthy, update NGINX to proxy to the real app:

```bash
cd /root/busibox/provision/ansible

# Deploy NGINX without placeholder_mode
ansible-playbook -i inventory/test/hosts.yml site.yml \
  --tags nginx \
  --ask-vault-pass \
  --limit TEST-proxy-lxc
```

This will:
1. ✅ Remove placeholder vhosts
2. ✅ Generate real proxy vhosts
3. ✅ Configure routing to `http://10.96.201.201:3000`
4. ✅ Reload NGINX

### 5. Test Access

Test that the application is accessible:

```bash
# Test main domain
curl -k https://test.ai.localhost

# Or in browser:
# https://test.ai.localhost
```

You should see the actual ai-portal interface, not the placeholder page.

## Troubleshooting

### Application Won't Start

**Check deployment logs:**
```bash
ssh root@10.96.201.201
cat /srv/apps/ai-portal/logs/deploy.log
```

**Common issues:**
1. **Missing secrets**: Check `.env` file has all required variables
2. **Build failed**: Check Node.js version, dependencies
3. **Port conflict**: Ensure port 3000 is not in use

**Fix: Redeploy**
```bash
# Force redeployment
ansible-playbook -i inventory/test/hosts.yml site.yml \
  --tags app_deployer \
  --ask-vault-pass \
  --limit TEST-apps-lxc \
  -e force_redeploy=true
```

### PM2 Shows "Errored" Status

**Check PM2 logs:**
```bash
ssh root@10.96.201.201
pm2 logs ai-portal --err --lines 100
```

**Common issues:**
1. **Database connection failed**: Check `DATABASE_URL` in `.env`
2. **Missing environment variables**: Check all secrets are set
3. **Build artifacts missing**: Run build manually

**Fix: Restart PM2**
```bash
ssh root@10.96.201.201
cd /srv/apps/ai-portal/current
pm2 restart ai-portal
pm2 logs ai-portal
```

### NGINX 502 Bad Gateway

**Symptoms:**
- NGINX shows 502 error
- Can't reach application

**Check:**
```bash
# 1. Is app running?
ssh root@10.96.201.201 pm2 status

# 2. Is app listening on correct port?
ssh root@10.96.201.201 netstat -tlnp | grep 3000

# 3. Can proxy reach app?
curl http://10.96.201.201:3000/api/health
```

**Fix:**
1. Ensure app is running: `pm2 restart ai-portal`
2. Check firewall: Container-to-container should be open
3. Verify NGINX upstream config

### GitHub Clone Failed

**Symptoms:**
- Deployment log shows "Failed to clone repository"
- "Authentication failed" error

**Check GitHub token:**
```bash
ssh root@10.96.201.201
cat ~/.github_token
# Should show your GitHub personal access token
```

**Fix:**
1. Verify token in vault has `repo` scope
2. Test token: `curl -H "Authorization: token $(cat ~/.github_token)" https://api.github.com/user`
3. Redeploy with correct token in vault

## Manual Deployment (If Needed)

If automated deployment fails, deploy manually:

```bash
# SSH into apps container
ssh root@10.96.201.201

# Clone repository
cd /srv/apps/ai-portal
git clone https://github.com/jazzmind/ai-portal.git releases/main-$(date +%s)
ln -sfn releases/main-$(date +%s) current

# Install and build
cd current
npm install
npm run build

# Start with PM2
pm2 start npm --name ai-portal -- start
pm2 save
```

## Verification Checklist

After deployment, verify:

- [ ] PM2 shows ai-portal as "online"
- [ ] Health endpoint returns 200: `http://10.96.201.201:3000/api/health`
- [ ] Application accessible: `https://test.ai.localhost`
- [ ] No NGINX errors in logs: `ssh root@10.96.201.200 tail /var/log/nginx/error.log`
- [ ] Application logs show no errors: `pm2 logs ai-portal`
- [ ] Can log in to application
- [ ] Database connection working

## Next Steps

After ai-portal is deployed:

1. **Deploy other apps**: agent-manager, doc-intel, innovation
2. **Test integration**: Verify apps can communicate
3. **Monitor logs**: Check for runtime errors
4. **Performance test**: Verify response times
5. **Backup data**: Ensure database backups are running

## Related Documentation

- [Application Deployment Guide](app-deployment.md)
- [GitHub Token Setup](../configuration/github-token-setup.md)
- [Troubleshooting](../troubleshooting/app-deployment-issues.md)
- [PM2 Management](../reference/pm2-commands.md)


