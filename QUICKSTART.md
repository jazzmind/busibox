# Busibox Quickstart Guide

**Complete local LLM infrastructure platform on Proxmox**

For comprehensive documentation, see [`specs/001-create-an-initial/quickstart.md`](specs/001-create-an-initial/quickstart.md)

---

## Prerequisites

**Proxmox Host**:
- Proxmox VE installed and running
- Ubuntu 22.04 LXC template downloaded
- Network bridge configured (vmbr0)
- SSH access to Proxmox host

**Admin Workstation**:
- Ansible 2.15+
- SSH access to Proxmox host and containers
- Python 3.8+ with pip

---

## Quick Start (Production)

### 1. Provision LXC Containers (Proxmox Host)

```bash
# On Proxmox host
cd /root
git clone https://github.com/jazzmind/busibox.git
cd busibox/provision/pct

# Configure your environment
vim vars.env  # Adjust CTIDs, IPs, template, storage if needed

# Create containers
bash create_lxc_base.sh
```

**Creates 7 containers**:
- `200` - proxy-lxc (10.96.200.200)
- `201` - apps-lxc (10.96.200.201)
- `202` - agent-lxc (10.96.200.202)
- `203` - pg-lxc (10.96.200.203)
- `204` - milvus-lxc (10.96.200.204)
- `205` - files-lxc (10.96.200.205)
- `206` - ingest-lxc (10.96.200.206)
- `207` - litellm-lxc (10.96.200.207)
- `208` - vllm-lxc (10.96.200.208)

### 2. Deploy Services (Admin Workstation)

```bash
# On your workstation
cd provision/ansible

# Configure inventory (IPs should match vars.env)
vim inventory/hosts.yml

# Test connectivity
make ping

# Deploy all services
make all
```

This deploys:
- **PostgreSQL** with schema and RLS policies
- **MinIO** with documents bucket
- **Milvus** with vector collection
- **Redis** + Ingest Worker
- **Agent API** (FastAPI)
- **Node.js** environment
- **Deploywatch** for auto-updates

### 3. Verify Deployment

```bash
cd provision/ansible
make verify
```

Expected output:
```
✓ PostgreSQL is healthy
✓ MinIO is healthy
✓ Milvus is healthy
✓ Agent API is healthy (or not deployed yet)
✓ Database schema verified
✓ Database migrations verified
```

### 4. Access Services

| Service | URL | Credentials |
|---------|-----|-------------|
| **MinIO Console** | http://10.96.200.28:9001 | minioadmin /  |
| **PostgreSQL** | 10.96.200.26:5432 | busibox_user / (see Ansible vars) |
| **Milvus** | 10.96.200.27:19530 | (no auth) |
| **Agent API** | http://10.96.200.30:8000/docs | (JWT required) |
| **Redis** | 10.96.200.29:6379 | (no auth - internal) |

---

## Testing Mode

Test infrastructure provisioning safely without affecting production:

```bash
# Create test environment (IDs 301-307, TEST- prefix)
bash test-infrastructure.sh full

# Or step-by-step:
bash test-infrastructure.sh provision  # Create & configure
bash test-infrastructure.sh verify     # Health checks
bash test-infrastructure.sh cleanup    # Clean up
```

See [`docs/testing.md`](docs/testing.md) for details.

---

## Post-Deployment Configuration

### Change Default Passwords

**MinIO** (files-lxc):
```bash
ssh root@10.96.200.28
vim /srv/minio/.env
# Change MINIO_ROOT_USER and MINIO_ROOT_PASSWORD
docker compose -f /srv/minio/docker-compose.yml restart
```

**PostgreSQL** (pg-lxc):
```bash
ssh root@10.96.200.26
sudo -u postgres psql
ALTER USER busibox_user WITH PASSWORD 'new_secure_password';
```

**JWT Secret** (agent-lxc):
```bash
ssh root@10.96.200.30
vim /srv/agent/.env
# Change JWT_SECRET_KEY to a secure random value
systemctl restart agent-api
```

### Configure LLM Provider

The platform uses liteLLM as a unified gateway. Configure your LLM provider:

**Option 1: Ollama (Local)**
```bash
# Install Ollama on agent-lxc or separate container
curl -fsSL https://ollama.com/install.sh | sh
ollama serve

# Update agent API config
vim /srv/agent/.env
# Set LITELLM_BASE_URL=http://localhost:11434
```

**Option 2: OpenAI**
```bash
# Update agent API config
vim /srv/agent/.env
# Set LITELLM_API_KEY=sk-...
# Set LITELLM_BASE_URL=https://api.openai.com/v1
```

**Option 3: Custom Provider**
```bash
# Configure liteLLM on agent-lxc
vim /etc/litellm/config.yaml
# Add your provider configuration
systemctl restart litellm
```

### Initialize First User

```bash
# Connect to PostgreSQL
psql -h 10.96.200.26 -U busibox_user -d busibox

# Create admin user
INSERT INTO users (username, email, password_hash, is_active)
VALUES ('admin', 'admin@example.com', 'hash_here', true);

# Assign admin role
INSERT INTO user_roles (user_id, role_id)
VALUES (
  (SELECT id FROM users WHERE username = 'admin'),
  (SELECT id FROM roles WHERE name = 'admin')
);
```

---

## Troubleshooting

### Services Not Starting

```bash
# Check service status
ssh root@<container-ip>
systemctl status <service-name>

# View logs
journalctl -u <service-name> -n 50 -f
```

### Health Checks Failing

```bash
# Run health checks manually
ssh root@10.96.200.26
/usr/local/bin/minio-health-check && echo "MinIO OK"

ssh root@10.96.200.27
/usr/local/bin/milvus-health-check && echo "Milvus OK"

ssh root@10.96.200.30
/usr/local/bin/agent-api-health-check && echo "Agent OK"
```

### Database Connection Errors

```bash
# Test from agent container
ssh root@10.96.200.30
psql -h 10.96.200.26 -U busibox_user -d busibox -c "SELECT 1"
```

### Ansible Connection Errors

```bash
# Test SSH connectivity
ssh root@10.96.200.26

# Check Ansible inventory
cd provision/ansible
ansible -i inventory/hosts.yml all -m ping
```

---

## Next Steps

1. **Upload Test File**: Use Agent API `/files/upload` endpoint
2. **Verify Ingestion**: Check Redis streams and Milvus collection
3. **Test Search**: Use Agent API `/search` endpoint
4. **Configure OpenWebUI**: Point to Agent API LLM gateway
5. **Deploy Custom Apps**: Add to deploywatch configuration

---

## Additional Resources

- **Architecture**: [`docs/architecture.md`](docs/architecture.md)
- **Testing Guide**: [`docs/testing.md`](docs/testing.md)
- **Full Quickstart**: [`specs/001-create-an-initial/quickstart.md`](specs/001-create-an-initial/quickstart.md)
- **API Documentation**: http://10.96.200.30:8000/docs (after deployment)
- **Constitution**: [`.specify/memory/constitution.md`](.specify/memory/constitution.md)

---

**Version**: 1.0.0  
**Last Updated**: 2025-10-14
