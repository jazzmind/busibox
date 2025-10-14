# Quickstart Guide: Busibox Local LLM Infrastructure

**Feature**: 001-create-an-initial  
**Created**: 2025-10-14  
**Status**: Complete

This guide walks you through provisioning the complete busibox local LLM infrastructure platform on a Proxmox host.

## Prerequisites

### Proxmox Host Requirements

- **Proxmox VE** 7.4+ or 8.x installed and configured
- **Minimum resources**:
  - 32 GB RAM (64 GB recommended)
  - 8 CPU cores (16 recommended)
  - 500 GB storage (1 TB+ recommended for file storage)
  - Network interface with static IP capability

- **Access**:
  - SSH root access to Proxmox host
  - Proxmox web UI access for monitoring (optional)

### Admin Workstation Requirements

- **Ansible** 2.15+ installed
- **SSH access** to Proxmox host
- **Python** 3.8+ (for Ansible)
- **Git** for cloning the repository

### Network Planning

Choose an IP range for your containers. Default suggested IPs (adjust in `vars.env`):

| Container | Service | IP | Port |
|-----------|---------|-----|------|
| files-lxc | MinIO | 10.96.200.21 | 9000 (API), 9001 (Console) |
| pg-lxc | PostgreSQL | 10.96.200.22 | 5432 |
| milvus-lxc | Milvus | 10.96.200.23 | 19530 |
| agent-lxc | Agent API | 10.96.200.24 | 3001 |
| ingest-lxc | Ingest Worker + Redis | 10.96.200.25 | 6379 (Redis), 3002 (Health) |

---

## Step 1: Clone Repository

On your **admin workstation**:

```bash
git clone https://github.com/jazzmind/busibox.git
cd busibox
```

---

## Step 2: Configure Variables

Edit the provisioning variables file:

```bash
vim provision/pct/vars.env
```

**Key settings to adjust**:

```bash
# Network configuration
BRIDGE="vmbr0"                    # Proxmox network bridge
SUBNET="10.96.200"                # IP subnet (first 3 octets)

# Container IDs (must be unique on your Proxmox host)
CTID_FILES=121
CTID_PG=122
CTID_MILVUS=123
CTID_AGENT=124
CTID_INGEST=125

# LXC template (must be downloaded to Proxmox first)
TEMPLATE="local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst"

# Storage pool for container disks
STORAGE="local-lvm"

# Storage pool for container root filesystems
ROOTFS_STORAGE="local-lvm"
```

**Download LXC template** (if not already available on Proxmox):

```bash
# On Proxmox host
pveam update
pveam download local debian-12-standard_12.2-1_amd64.tar.zst
```

---

## Step 3: Provision Containers on Proxmox Host

**Copy repository to Proxmox host**:

```bash
# From admin workstation
scp -r busibox root@proxmox-host:/root/
```

**SSH into Proxmox host**:

```bash
ssh root@proxmox-host
cd /root/busibox/provision/pct
```

**Run container creation script**:

```bash
bash create_lxc_base.sh
```

**Expected output**:
```
Creating container 121 (files-lxc)...
Container 121 created successfully
Starting container 121...
Container 121 started

Creating container 122 (pg-lxc)...
Container 122 created successfully
Starting container 122...
Container 122 started

[... similar for all containers ...]

All containers created and started successfully!
```

**Verify containers**:

```bash
pct list | grep -E "121|122|123|124|125"
```

Expected output:
```
121 running   files-lxc
122 running   pg-lxc
123 running   milvus-lxc
124 running   agent-lxc
125 running   ingest-lxc
```

---

## Step 4: Configure Ansible Inventory

Back on your **admin workstation**, configure the Ansible inventory with the container IPs:

```bash
cd busibox/provision/ansible
vim inventory/hosts.yml
```

**Example inventory** (adjust IPs if you changed subnet):

```yaml
all:
  children:
    files:
      hosts:
        10.96.200.21:
          service_name: minio
          health_port: 9000
    
    pg:
      hosts:
        10.96.200.22:
          service_name: postgresql
          health_port: 5432
    
    milvus:
      hosts:
        10.96.200.23:
          service_name: milvus
          health_port: 19530
    
    agent:
      hosts:
        10.96.200.24:
          service_name: agent-api
          health_port: 3001
    
    ingest:
      hosts:
        10.96.200.25:
          service_name: ingest-worker
          health_port: 3002

  vars:
    ansible_user: root
    ansible_ssh_private_key_file: ~/.ssh/id_rsa
    ansible_python_interpreter: /usr/bin/python3
```

**Test connectivity**:

```bash
ansible all -i inventory/hosts.yml -m ping
```

Expected output:
```
10.96.200.21 | SUCCESS => {"changed": false, "ping": "pong"}
10.96.200.22 | SUCCESS => {"changed": false, "ping": "pong"}
...
```

---

## Step 5: Deploy Services with Ansible

**Run the full deployment**:

```bash
make all
```

This runs `ansible-playbook -i inventory/hosts.yml site.yml` and configures all services.

**Expected duration**: 15-25 minutes depending on host performance and network speed.

**What gets deployed**:
1. **Node.js common setup** on agent and ingest containers
2. **PostgreSQL** database with schema and RLS policies
3. **MinIO** S3 storage with buckets and webhook configuration
4. **Milvus** vector database (Docker-in-LXC)
5. **Redis** queue service
6. **Agent API** service (FastAPI application)
7. **Ingest worker** service (file processing)
8. **Deploywatch** auto-deployment service

**Monitor progress**:

Ansible will output detailed logs. Look for `PLAY RECAP` at the end:

```
PLAY RECAP ***********************************************************
10.96.200.21               : ok=12   changed=8    unreachable=0    failed=0
10.96.200.22               : ok=15   changed=10   unreachable=0    failed=0
10.96.200.23               : ok=18   changed=12   unreachable=0    failed=0
10.96.200.24               : ok=14   changed=9    unreachable=0    failed=0
10.96.200.25               : ok=16   changed=11   unreachable=0    failed=0
```

**If errors occur**:
- Check logs in Ansible output
- Verify network connectivity to containers
- Ensure sufficient disk space on Proxmox host
- Re-run `make all` (playbooks are idempotent)

---

## Step 6: Initialize Milvus Vector Database

**Install Python dependencies** (on admin workstation or any machine with network access):

```bash
pip install pymilvus==2.3.0
```

**Run initialization script**:

```bash
cd busibox
python tools/milvus_init.py
```

**Expected output**:
```
Connecting to Milvus at 10.96.200.23:19530...
Connected successfully
Creating collection 'document_embeddings'...
Collection created
Creating index on vector field...
Index created
Loading collection...
Collection loaded and ready
Milvus initialization complete!
```

---

## Step 7: Verify Deployment

### 7.1 Health Checks

**Automated verification**:

```bash
cd provision/ansible
make verify-quick
```

**Expected output**:
```
Checking service health...
✓ minio (10.96.200.21:9000) is healthy
✓ postgres (10.96.200.22:5432) is healthy
✓ milvus (10.96.200.23:19530) is healthy
✓ agent-api (10.96.200.24:3001) is healthy
✓ ingest-worker (10.96.200.25:3002) is healthy
All services are healthy!
```

### 7.2 Manual Service Checks

**MinIO Console**:
```bash
# Open in browser
http://10.96.200.21:9001

# Default credentials (change after first login)
Username: minioadmin
Password: minioadmin
```

**PostgreSQL Connection**:
```bash
# From admin workstation
psql -h 10.96.200.22 -U appuser -d busibox -c "SELECT version();"
```

**Milvus Status**:
```bash
python -c "
from pymilvus import connections, utility
connections.connect('default', host='10.96.200.23', port='19530')
print('Collections:', utility.list_collections())
"
```

**Agent API**:
```bash
curl http://10.96.200.24:3001/health | jq
```

Expected response:
```json
{
  "status": "healthy",
  "service": "agent-api",
  "version": "1.0.0",
  "checks": {
    "database": "ok",
    "milvus": "ok",
    "minio": "ok",
    "redis": "ok"
  }
}
```

### 7.3 Test File Upload (End-to-End)

**Create test user**:

```bash
# SSH into pg-lxc container
ssh root@10.96.200.22

# Create test user in PostgreSQL
psql -U postgres -d busibox <<EOF
-- Create user
INSERT INTO users (username, email, password_hash)
VALUES (
  'testuser',
  'test@example.com',
  '\$2b\$12\$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/8kSC0wMkMx1kG4Y9u'  -- password: test123
);

-- Get user ID
\set user_id (SELECT id FROM users WHERE username = 'testuser')

-- Assign 'user' role
INSERT INTO user_roles (user_id, role_id)
SELECT id, (SELECT id FROM roles WHERE name = 'user')
FROM users WHERE username = 'testuser';

SELECT 'User created with ID: ' || id FROM users WHERE username = 'testuser';
EOF
```

**Login and get token**:

```bash
curl -X POST http://10.96.200.24:3001/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser", "password": "test123"}' | jq
```

Save the `token` value from the response.

**Initiate file upload**:

```bash
TOKEN="<your-token-here>"

curl -X POST http://10.96.200.24:3001/files/upload \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "test-document.txt",
    "content_type": "text/plain",
    "size_bytes": 1024
  }' | jq
```

Save the `upload_url` and `file_id`.

**Upload file content**:

```bash
UPLOAD_URL="<presigned-url-from-previous-step>"

echo "This is a test document for the busibox platform." > test-document.txt

curl -X PUT "$UPLOAD_URL" \
  --data-binary @test-document.txt \
  -H "Content-Type: text/plain"
```

**Check file status**:

```bash
FILE_ID="<file-id-from-initiate-step>"

curl http://10.96.200.24:3001/files/$FILE_ID \
  -H "Authorization: Bearer $TOKEN" | jq
```

**Monitor ingestion**:

```bash
# Check ingestion worker logs
ssh root@10.96.200.25
journalctl -u ingest-worker -f
```

You should see logs showing:
1. File retrieved from MinIO
2. Text extracted
3. Content chunked
4. Embeddings generated
5. Data stored in Milvus and PostgreSQL

**Verify embeddings in Milvus**:

```bash
python -c "
from pymilvus import connections, Collection
connections.connect('default', host='10.96.200.23', port='19530')
collection = Collection('document_embeddings')
print('Total embeddings:', collection.num_entities)
"
```

**Test semantic search**:

```bash
curl -X POST http://10.96.200.24:3001/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is the busibox platform?",
    "limit": 5
  }' | jq
```

Expected: Results containing chunks from your uploaded test document.

---

## Step 8: Post-Deployment Configuration

### 8.1 Change Default Credentials

**MinIO**:

```bash
# SSH into files-lxc
ssh root@10.96.200.21

# Edit MinIO environment file
vim /srv/minio/.env

# Change MINIO_ROOT_USER and MINIO_ROOT_PASSWORD
# Restart MinIO
systemctl restart minio
```

**PostgreSQL**:

```bash
# SSH into pg-lxc
ssh root@10.96.200.22

# Change postgres user password
psql -U postgres -c "ALTER USER postgres PASSWORD 'new-secure-password';"

# Change appuser password (used by services)
psql -U postgres -c "ALTER USER appuser PASSWORD 'new-secure-password';"
```

Update service configurations to use new password.

### 8.2 Configure LLM Providers

**Install Ollama** (or other LLM provider) on a host accessible from containers:

```bash
# Example: Install on Proxmox host or dedicated LXC container
curl https://ollama.ai/install.sh | sh

# Pull models
ollama pull llama2
ollama pull codellama
```

**Configure liteLLM gateway** (on agent-lxc):

```bash
ssh root@10.96.200.24

# Create liteLLM config
vim /etc/litellm/config.yaml
```

```yaml
model_list:
  - model_name: llama2-7b
    litellm_params:
      model: ollama/llama2
      api_base: http://<ollama-host-ip>:11434
  
  - model_name: codellama-13b
    litellm_params:
      model: ollama/codellama
      api_base: http://<ollama-host-ip>:11434
```

**Restart agent API**:

```bash
systemctl restart agent-api
```

### 8.3 Enable TLS (Production)

For production use, configure nginx reverse proxy with TLS certificates (Let's Encrypt recommended).

---

## Common Troubleshooting

### Container won't start

```bash
# Check container status
pct status <CTID>

# View container logs
pct enter <CTID>
journalctl -xe
```

### Service not responding

```bash
# Check service status
ssh root@<container-ip>
systemctl status <service-name>

# View service logs
journalctl -u <service-name> -n 50
```

### Ansible playbook fails

- **SSH connection issues**: Verify inventory IPs match container IPs
- **Permission errors**: Ensure ansible_user has sudo/root access
- **Disk space**: Check Proxmox storage with `pvesm status`
- **Re-run playbook**: Ansible is idempotent, safe to re-run

### Milvus won't start

Milvus runs in Docker within milvus-lxc. Ensure container has Docker support:

```bash
pct config <CTID> | grep features
# Should show: features: nesting=1
```

### File upload webhook not triggering

Check MinIO webhook configuration:

```bash
ssh root@10.96.200.21
mc admin config get myminio notify_webhook:1
```

Verify webhook endpoint is accessible from MinIO container.

---

## Next Steps

With infrastructure provisioned and verified:

1. **Create additional users** via PostgreSQL or build admin UI
2. **Upload documents** for testing RAG capabilities
3. **Deploy custom applications** to app-server container (future)
4. **Configure monitoring** (Prometheus/Grafana) if desired
5. **Set up backups** for PostgreSQL, MinIO, and Milvus data

## Support & Documentation

- **GitHub**: https://github.com/jazzmind/busibox
- **API Documentation**: See `specs/001-create-an-initial/contracts/agent-api.yaml`
- **Data Model**: See `specs/001-create-an-initial/data-model.md`
- **Architecture**: See `docs/architecture.md`

---

**Congratulations!** Your busibox local LLM infrastructure platform is now operational. 🎉

