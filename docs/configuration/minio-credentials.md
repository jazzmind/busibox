# MinIO Credentials Configuration

## Overview

MinIO uses a single set of credentials for both console (web UI) and API access:
- **Console/Web UI**: Login with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`
- **API Access**: Use same credentials as `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`

In Busibox, these credentials are configured via Ansible variables `minio_root_user` and `minio_root_password`.

## Default Credentials

**Default values** (used if not specified in inventory):
- `minio_root_user`: `minioadmin`
- `minio_root_password`: `minioadminchange`

## Where Credentials Are Configured

### 1. MinIO Container Setup

**File**: `provision/ansible/roles/minio/tasks/main.yml`

The MinIO container is configured with these environment variables:
```yaml
MINIO_ROOT_USER={{ minio_root_user | default('minioadmin') }}
MINIO_ROOT_PASSWORD={{ minio_root_password | default('minioadminchange') }}
```

These are stored in `/srv/minio/.env` on the MinIO container.

### 2. Application Environment Files

Applications use the same credentials for API access:

**Ingest API** (`provision/ansible/roles/ingest_api/templates/ingest-api.env.j2`):
```jinja2
MINIO_ACCESS_KEY={{ minio_root_user | default('minioadmin') }}
MINIO_SECRET_KEY={{ minio_root_password | default('minioadminchange') }}
```

**Ingest Worker** (`provision/ansible/roles/ingest_worker/tasks/main.yml`):
```jinja2
MINIO_ACCESS_KEY={{ minio_root_user | default('minioadmin') }}
MINIO_SECRET_KEY={{ minio_root_password | default('minioadminchange') }}
```

**Agent API** (`provision/ansible/roles/agent_api/tasks/main.yml`):
```jinja2
MINIO_ACCESS_KEY={{ minio_root_user | default('minioadmin') }}
MINIO_SECRET_KEY={{ minio_root_password | default('minioadminchange') }}
```

### 3. Inventory Configuration

**Test Environment** (`provision/ansible/inventory/test/group_vars/all/00-main.yml`):
- Currently uses **defaults** (not explicitly set)
- Credentials: `minioadmin` / `minioadminchange`

**Local Environment** (`provision/ansible/inventory/local/group_vars/all.yml`):
```yaml
minio_root_user: minioadmin
minio_root_password: minioadmin
```

**Production Environment** (`provision/ansible/inventory/production/group_vars/all/00-main.yml`):
- Should be set in **Ansible Vault** (see below)

### 4. Ansible Vault (Production Secrets)

**File**: `provision/ansible/roles/secrets/vars/vault.yml` (encrypted)

For production, MinIO credentials should be stored in the vault:
```yaml
secrets:
  agent-server:
    minio_access_key: "YOUR_PRODUCTION_ACCESS_KEY"
    minio_secret_key: "YOUR_PRODUCTION_SECRET_KEY"
```

## How to Change Credentials

### Option 1: Update Inventory Variables (Test/Local)

Add to `provision/ansible/inventory/test/group_vars/all/00-main.yml`:
```yaml
minio_root_user: your_new_username
minio_root_password: your_new_password
```

Then redeploy MinIO:
```bash
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --limit files --tags minio
```

### Option 2: Update Ansible Vault (Production)

1. Edit vault file:
   ```bash
   cd provision/ansible
   ansible-vault edit roles/secrets/vars/vault.yml
   ```

2. Update credentials:
   ```yaml
   secrets:
     agent-server:
       minio_access_key: "new_access_key"
       minio_secret_key: "new_secret_key"
   ```

3. Update inventory to use vault secrets:
   ```yaml
   minio_root_user: "{{ secrets['agent-server'].minio_access_key }}"
   minio_root_password: "{{ secrets['agent-server'].minio_secret_key }}"
   ```

4. Redeploy MinIO and all applications:
   ```bash
   ansible-playbook -i inventory/production/hosts.yml site.yml --limit files --tags minio
   ansible-playbook -i inventory/production/hosts.yml site.yml --limit ingest --tags ingest_api,ingest_worker
   ```

## Current Configuration

Based on your test environment:
- **Console Login**: `minioadmin` / `minioadminchange` ✅
- **API Access**: `minioadmin` / `minioadminchange` ✅
- **Endpoint**: `10.96.201.205:9000`
- **Bucket**: `documents`

## Verification

Test MinIO connectivity:
```python
from minio import Minio

client = Minio(
    '10.96.201.205:9000',
    access_key='minioadmin',
    secret_key='minioadminchange',
    secure=False,
)

buckets = client.list_buckets()
print(f"Connected! Found {len(buckets)} buckets")
```

## Important Notes

1. **Same Credentials**: Console and API use the same credentials (`MINIO_ROOT_USER` = `MINIO_ACCESS_KEY`)

2. **Change Requires Redeploy**: After changing credentials, you must:
   - Redeploy MinIO container (to update `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`)
   - Redeploy all applications (to update `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` in `.env` files)

3. **Security**: Production credentials should be stored in Ansible Vault, not in plain inventory files.

4. **Testing**: The connectivity test (`srv/ingest/tests/integration/test_connectivity.py`) uses environment variables from `.env` file or defaults to `minioadmin`/`minioadminchange`.

