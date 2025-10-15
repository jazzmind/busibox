# Agent-Server Deployment Guide

**Created**: 2025-10-15  
**Status**: Ready for deployment  
**Prerequisites**: Infrastructure provisioned and tested via `test-infrastructure.sh`

---

## ✅ Prerequisites Checklist

Before deploying the agent-server, verify:

### 1. Infrastructure Health
```bash
# On Proxmox host
cd /root/busibox
bash test-infrastructure.sh verify

# Expected: All health checks pass
# ✓ PostgreSQL health
# ✓ MinIO health  
# ✓ Milvus health
```

### 2. GitHub Repository Exists

The `jazzmind/agent-server` repository must:
- ✅ Exist on GitHub (public or accessible via SSH)
- ✅ Have at least one **published Release** (not just tags)
- ✅ Include a `package.json` (for Node.js) or `requirements.txt` (for Python)

**Check if repository exists:**
```bash
curl -I https://api.github.com/repos/jazzmind/agent-server
# Expected: 200 OK

# Check for releases
curl https://api.github.com/repos/jazzmind/agent-server/releases/latest | jq '.tag_name'
# Expected: "v1.0.0" or similar (NOT 404)
```

**If repository doesn't exist yet:**
1. Create it: https://github.com/jazzmind/agent-server
2. Push your agent-server code
3. Create a release: GitHub → Releases → Create Release → Tag version (e.g., `v1.0.0`) → Publish

### 3. Application Configuration

Agent-server is already configured in `provision/ansible/group_vars/apps.yml`:

```yaml
applications:
  - name: agent-server
    github_repo: jazzmind/agent-server
    container: agent-lxc
    container_ip: "{{ agent_ip }}"  # 10.96.201.30 for test
    port: 8000
    deploy_path: /srv/agent
    health_endpoint: /health
    routes: []  # Internal only - no public routing
    secrets:
      - database_url
      - minio_access_key
      - minio_secret_key
      - redis_url
    env:
      LOG_LEVEL: "{{ log_level }}"
      MILVUS_HOST: "{{ milvus_host }}"
      MILVUS_PORT: "{{ milvus_port }}"
```

### 4. Secrets Exist

Required secrets are already defined in `provision/ansible/roles/secrets/vars/vault.yml`:

```yaml
secrets:
  agent_server:
    database_url: "postgresql://busibox_user:busibox_password_change_me@{{ postgres_host }}/{{ postgres_db }}"
    minio_access_key: "minioadmin"
    minio_secret_key: "minioadminchange"
    redis_url: "redis://{{ redis_host }}:{{ redis_port }}"
```

**⚠️ For production:** Encrypt the secrets file:
```bash
ansible-vault encrypt provision/ansible/roles/secrets/vars/vault.yml
```

---

## 🚀 Deployment Methods

Choose one of the following deployment methods:

### Method 1: Ansible Deployment (Recommended)

This generates the deploywatch script and `.env` file automatically.

```bash
# From Proxmox host (or workstation with Ansible)
cd /root/busibox/provision/ansible

# Deploy to TEST environment
ansible-playbook -i inventory/test-hosts.yml \
  --limit agent \
  --tags app_deployer,secrets \
  site.yml

# Or use the Makefile shortcut (for production)
make deploy-apps
```

**What this does:**
1. Loads application definitions from `apps.yml`
2. Validates configuration and secrets
3. Generates `/srv/deploywatch/apps/agent-server.sh` deployment script
4. Creates `/srv/agent/.env` with secrets and environment variables
5. Sets up file permissions

**Expected output:**
```
PLAY [agent] ***********************************************************

TASK [app_deployer : Check if applications are defined] ***************
ok: [10.96.201.30] => {
    "msg": "Applications defined: True, Count: 1"
}

TASK [app_deployer : Include validation tasks] ************************
included: validate.yml

TASK [app_deployer : Generate deploywatch script for agent-server] ****
changed: [10.96.201.30]

TASK [secrets : Create /etc/busibox/secrets/agent_server.env] *********
changed: [10.96.201.30]

PLAY RECAP *************************************************************
10.96.201.30               : ok=X    changed=2    unreachable=0    failed=0
```

### Method 2: Manual Deployment

If you prefer to deploy manually or troubleshoot:

```bash
# SSH to agent container
ssh root@10.96.201.30  # or: ssh TEST-agent-lxc

# Run the generated deployment script
bash /srv/deploywatch/apps/agent-server.sh

# Or manually:
cd /srv/agent
git clone https://github.com/jazzmind/agent-server.git .

# For Node.js app:
npm install
npm run build  # if needed
pm2 start ecosystem.config.js --env production
pm2 save

# For Python app:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pm2 start "python main.py" --name agent-server
```

### Method 3: Deploywatch Timer (Automated)

The `deploywatch` systemd timer runs every 5 minutes and checks for new GitHub releases.

```bash
# Check deploywatch status
ssh root@10.96.201.30
systemctl status deploywatch.timer
systemctl status deploywatch.service

# View recent deployment logs
journalctl -u deploywatch.service -n 50 --no-pager

# Trigger deployment manually (don't wait for timer)
systemctl start deploywatch.service
```

---

## 🔍 Verification

### 1. Check if agent-server is running

```bash
# SSH to agent container
ssh root@10.96.201.30

# Check process
pm2 list
# Expected: agent-server | online | ...

# Or if using systemd:
systemctl status agent-api
```

### 2. Health Check

```bash
# From Proxmox host or any machine in the network
curl http://10.96.201.30:8000/health

# Expected response:
# {
#   "status": "healthy",
#   "timestamp": "2025-10-15T...",
#   "services": {
#     "postgres": "ok",
#     "milvus": "ok",
#     "minio": "ok",
#     "redis": "ok"
#   }
# }
```

### 3. Check Logs

```bash
# PM2 logs
pm2 logs agent-server

# Systemd logs (if using systemd)
journalctl -u agent-api -f

# Application logs (if configured)
tail -f /var/log/agent-server.log
```

### 4. Test API Endpoints

```bash
# Test authentication endpoint
curl -X POST http://10.96.201.30:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"test"}'

# Test file upload (requires auth token)
curl -X POST http://10.96.201.30:8000/api/v1/files/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@test.pdf"
```

---

## 🐛 Troubleshooting

### Issue: "Application failed to start"

**Check PM2 error logs:**
```bash
ssh root@10.96.201.30
pm2 logs agent-server --err --lines 50
```

**Common causes:**
1. **Missing environment variables**: Check `/srv/agent/.env`
2. **Database connection failed**: Verify PostgreSQL is running and credentials are correct
3. **Port already in use**: Check if port 8000 is available: `netstat -tlnp | grep 8000`
4. **Missing dependencies**: Re-run `npm install` or `pip install -r requirements.txt`

### Issue: "Health check fails"

**Check service dependencies:**
```bash
# PostgreSQL
psql -h 10.96.201.26 -U busibox_user -d busibox -c "SELECT 1"

# MinIO
curl http://10.96.201.28:9000/minio/health/live

# Milvus
curl http://10.96.201.27:9091/healthz

# Redis
redis-cli -h 10.96.201.29 ping
```

### Issue: "GitHub clone/pull fails"

**Check SSH keys:**
```bash
ssh root@10.96.201.30
ssh -T git@github.com
# Expected: "Hi jazzmind! You've successfully authenticated..."
```

**If using HTTPS with private repo:**
- Set up GitHub Personal Access Token
- Use: `https://<token>@github.com/jazzmind/agent-server.git`

### Issue: "Deploywatch doesn't deploy new releases"

**Check deploywatch logs:**
```bash
ssh root@10.96.201.30
journalctl -u deploywatch.service -n 100 --no-pager

# Check timer status
systemctl list-timers deploywatch.timer

# Manually trigger deployment
systemctl start deploywatch.service
```

---

## 🔄 Updating the Agent-Server

### Option 1: Create a new GitHub release

1. Push code changes to repository
2. Create a new release on GitHub (e.g., `v1.0.1`)
3. Wait for deploywatch timer (max 5 minutes) or trigger manually:
   ```bash
   ssh root@10.96.201.30
   systemctl start deploywatch.service
   ```

### Option 2: Manual update

```bash
ssh root@10.96.201.30
cd /srv/agent
git pull origin main  # or master
npm install  # or pip install -r requirements.txt
pm2 restart agent-server
```

### Option 3: Re-run Ansible

```bash
cd /root/busibox/provision/ansible
ansible-playbook -i inventory/test-hosts.yml \
  --limit agent \
  --tags app_deployer \
  site.yml

ssh root@10.96.201.30
bash /srv/deploywatch/apps/agent-server.sh
```

---

## 📊 Monitoring

### PM2 Monitoring Dashboard

```bash
ssh root@10.96.201.30
pm2 monit  # Real-time monitoring

pm2 status  # Current status
pm2 info agent-server  # Detailed info
```

### Resource Usage

```bash
# CPU and memory
pm2 list

# Disk usage
df -h /srv/agent

# Network connections
netstat -tlnp | grep node  # or python
```

### Application Metrics

If agent-server exposes metrics:
```bash
curl http://10.96.201.30:8000/metrics
```

---

## 🛡️ Security Notes

1. **Change default passwords** in production:
   ```bash
   # PostgreSQL
   psql -h 10.96.201.26 -U postgres
   ALTER USER busibox_user WITH PASSWORD 'new_secure_password';
   
   # Update vault.yml with new password
   ansible-vault edit provision/ansible/roles/secrets/vars/vault.yml
   ```

2. **Encrypt secrets vault** before production:
   ```bash
   ansible-vault encrypt provision/ansible/roles/secrets/vars/vault.yml
   ```

3. **Limit network access**:
   - Agent-server port (8000) should NOT be exposed to public internet
   - Only accessible from NGINX proxy or internal network

4. **Enable firewall** on agent container:
   ```bash
   ufw allow from 10.96.200.0/21 to any port 8000
   ufw enable
   ```

---

## 📝 Next Steps

After agent-server is deployed and verified:

1. **Configure MinIO webhook** (now that agent API is running):
   ```bash
   ssh root@10.96.201.28
   mc event add local/documents arn:minio:sqs::primary:webhook \
     --event put \
     --suffix .pdf --suffix .docx --suffix .txt
   ```

2. **Deploy NGINX proxy** (Phase 5 - already implemented):
   ```bash
   cd /root/busibox/provision/ansible
   make openwebui  # Deploys NGINX on openwebui-lxc container
   ```

3. **Deploy additional applications** (cashman-portal, agent-client):
   - Uncomment desired apps in `apps.yml`
   - Add required secrets to `vault.yml`
   - Run `make deploy-apps`

---

## 📞 Support

For issues or questions:
- Review logs: `pm2 logs agent-server` or `journalctl -u agent-api`
- Check infrastructure: `bash test-infrastructure.sh verify`
- Validate configuration: `ansible-playbook --check site.yml`
- Consult specs: `specs/002-deploy-app-servers/spec.md`

---

**Status**: ✅ Ready for deployment  
**Last Updated**: 2025-10-15  
**Version**: 1.0.0

