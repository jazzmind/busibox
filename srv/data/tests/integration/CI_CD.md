# CI/CD Integration Test Configuration

## Overview

Integration tests use environment variables that align with Ansible variable names from `vault.example.yml` and inventory files. This allows tests to run in CI/CD pipelines using the same configuration as deployed services.

## Environment Variable Mapping

### Ansible → Environment Variables

The following Ansible variables map directly to environment variables used by tests:

| Ansible Variable | Environment Variable | Source |
|-----------------|---------------------|--------|
| `postgres_host` | `POSTGRES_HOST` | Inventory `group_vars/all/00-main.yml` |
| `postgres_port` | `POSTGRES_PORT` | Inventory (default: `5432`) |
| `postgres_db` | `POSTGRES_DB` | Inventory (test: `busibox_test`, prod: `agent_server`) |
| `postgres_user` | `POSTGRES_USER` | Inventory (test: `busibox_test_user`) |
| `postgres_password` | `POSTGRES_PASSWORD` | Vault `secrets.postgresql.password` |
| `milvus_host` | `MILVUS_HOST` | Inventory `milvus_ip` |
| `milvus_port` | `MILVUS_PORT` | Inventory (default: `19530`) |
| `milvus_collection` | `MILVUS_COLLECTION` | Inventory (default: `documents`) |
| `redis_host` | `REDIS_HOST` | Inventory `ingest_ip` |
| `redis_port` | `REDIS_PORT` | Inventory (default: `6379`) |
| `minio_endpoint` | `MINIO_ENDPOINT` | Inventory `minio_ip:minio_port` |
| `minio_root_user` | `MINIO_ACCESS_KEY` | Inventory or Vault |
| `minio_root_password` | `MINIO_SECRET_KEY` | Inventory or Vault |
| `litellm_base_url` | `LITELLM_BASE_URL` | Inventory `litellm_ip:litellm_port` |
| `litellm_api_key` | `LITELLM_API_KEY` | Vault `secrets.litellm_api_key` |

## CI/CD Setup

### Option 1: Export from Ansible Vault (Recommended)

Create a script to extract and export environment variables from Ansible:

```bash
#!/bin/bash
# scripts/export-test-env.sh
# Exports Ansible variables as environment variables for CI/CD

set -euo pipefail

VAULT_FILE="provision/ansible/roles/secrets/vars/vault.yml"
INVENTORY_FILE="provision/ansible/inventory/test/group_vars/all/00-main.yml"

# Check if vault password is set
if [ -z "${ANSIBLE_VAULT_PASSWORD:-}" ]; then
    echo "Error: ANSIBLE_VAULT_PASSWORD environment variable must be set"
    exit 1
fi

# Export vault password
export ANSIBLE_VAULT_PASSWORD_FILE=<(echo "$ANSIBLE_VAULT_PASSWORD")

# Load Ansible variables
cd provision/ansible

# Export PostgreSQL config
export POSTGRES_HOST=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.postgres_host // "10.96.201.203"')
export POSTGRES_PORT=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.postgres_port // "5432"')
export POSTGRES_DB=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.postgres_db // "busibox_test"')
export POSTGRES_USER=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.postgres_user // "busibox_test_user"')
export POSTGRES_PASSWORD=$(ansible-vault view roles/secrets/vars/vault.yml | grep -A 1 "postgresql:" | grep "password:" | awk '{print $2}' | tr -d '"')

# Export Milvus config
export MILVUS_HOST=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.milvus_host // "10.96.201.204"')
export MILVUS_PORT=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.milvus_port // "19530"')
export MILVUS_COLLECTION=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.milvus_collection // "documents"')

# Export Redis config
export REDIS_HOST=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.redis_host // "10.96.201.206"')
export REDIS_PORT=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.redis_port // "6379"')

# Export MinIO config
export MINIO_ENDPOINT=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.minio_host // "10.96.201.205"):$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.minio_port // "9000"')
export MINIO_ACCESS_KEY=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.minio_root_user // "minioadmin"')
export MINIO_SECRET_KEY=$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.minio_root_password // "minioadminchange"')

# Export liteLLM config
export LITELLM_BASE_URL="http://$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.litellm_host // "10.96.201.207"):$(ansible-inventory -i inventory/test/hosts.yml --list | jq -r '.all.vars.litellm_port // "4000"')"
export LITELLM_API_KEY=$(ansible-vault view roles/secrets/vars/vault.yml | grep "litellm_api_key:" | awk '{print $2}' | tr -d '"')

echo "Environment variables exported successfully"
```

### Option 2: Use Ansible-Generated .env File

If Ansible has already generated `.env` files on the target hosts, copy them:

```bash
# From CI/CD runner, copy .env from deployed service
scp root@10.96.201.206:/srv/ingest-api/.env /tmp/test.env
export $(cat /tmp/test.env | grep -v '^#' | xargs)
```

### Option 3: Direct Environment Variable Export (GitHub Actions Example)

```yaml
# .github/workflows/integration-tests.yml
name: Integration Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  integration-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          cd srv/ingest
          pip install -r requirements.txt
      
      - name: Export environment variables
        env:
          ANSIBLE_VAULT_PASSWORD: ${{ secrets.ANSIBLE_VAULT_PASSWORD }}
        run: |
          # Load from Ansible vault and inventory
          # (use the export script above)
          source scripts/export-test-env.sh
      
      - name: Run integration tests
        env:
          POSTGRES_HOST: ${{ secrets.POSTGRES_HOST }}
          POSTGRES_PORT: ${{ secrets.POSTGRES_PORT }}
          POSTGRES_DB: ${{ secrets.POSTGRES_DB }}
          POSTGRES_USER: ${{ secrets.POSTGRES_USER }}
          POSTGRES_PASSWORD: ${{ secrets.POSTGRES_PASSWORD }}
          MILVUS_HOST: ${{ secrets.MILVUS_HOST }}
          MILVUS_PORT: ${{ secrets.MILVUS_PORT }}
          MILVUS_COLLECTION: ${{ secrets.MILVUS_COLLECTION }}
          REDIS_HOST: ${{ secrets.REDIS_HOST }}
          REDIS_PORT: ${{ secrets.REDIS_PORT }}
          MINIO_ENDPOINT: ${{ secrets.MINIO_ENDPOINT }}
          MINIO_ACCESS_KEY: ${{ secrets.MINIO_ACCESS_KEY }}
          MINIO_SECRET_KEY: ${{ secrets.MINIO_SECRET_KEY }}
          LITELLM_BASE_URL: ${{ secrets.LITELLM_BASE_URL }}
          LITELLM_API_KEY: ${{ secrets.LITELLM_API_KEY }}
        run: |
          cd srv/ingest
          pytest tests/integration/ -v
```

## Variable Sources

### Test Environment (`inventory/test/group_vars/all/00-main.yml`)

```yaml
postgres_host: "{{ postgres_ip }}"  # 10.96.201.203
postgres_port: 5432
postgres_db: busibox_test
postgres_user: busibox_test_user

milvus_host: "{{ milvus_ip }}"  # 10.96.201.204
milvus_port: 19530

redis_host: "{{ ingest_ip }}"  # 10.96.201.206
redis_port: 6379

minio_host: "{{ minio_ip }}"  # 10.96.201.205
minio_port: 9000

litellm_host: "{{ litellm_ip }}"  # 10.96.201.207
litellm_port: 4000
```

### Secrets (`roles/secrets/vars/vault.yml`)

```yaml
secrets:
  postgresql:
    password: "YOUR_POSTGRES_PASSWORD"
  
  litellm_api_key: "YOUR_LITELLM_API_KEY"
```

### MinIO Credentials

MinIO credentials come from inventory variables (not vault):
- `minio_root_user`: Default `minioadmin`
- `minio_root_password`: Default `minioadminchange`

These map to:
- `MINIO_ACCESS_KEY` = `minio_root_user`
- `MINIO_SECRET_KEY` = `minio_root_password`

## Running Tests Locally

Tests automatically load from `.env` file in the busibox root:

```bash
cd srv/ingest
pytest tests/integration/ -v
```

The `.env` file should contain:
```bash
POSTGRES_HOST=10.96.201.203
POSTGRES_PORT=5432
POSTGRES_DB=busibox_test
POSTGRES_USER=busibox_test_user
POSTGRES_PASSWORD=your_password

MILVUS_HOST=10.96.201.204
MILVUS_PORT=19530
MILVUS_COLLECTION=document_embeddings

REDIS_HOST=10.96.201.206
REDIS_PORT=6379

MINIO_ENDPOINT=10.96.201.205:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadminchange

LITELLM_BASE_URL=http://10.96.201.207:4000
LITELLM_API_KEY=your_api_key
```

## Verification

Verify environment variables are set correctly:

```bash
cd srv/ingest
python -c "
from shared.config import Config
config = Config()
print(f'POSTGRES_HOST: {config.postgres_host}')
print(f'MILVUS_HOST: {config.milvus_host}')
print(f'REDIS_HOST: {config.redis_host}')
print(f'MINIO_ENDPOINT: {config.minio_endpoint}')
print(f'LITELLM_BASE_URL: {config.litellm_base_url}')
"
```

## Notes

1. **Test Isolation**: Integration tests create test data that should be cleaned up. Each test should clean up after itself.

2. **Service Availability**: Tests require all services to be running and accessible. Consider using `pytest.mark.skip` if services are unavailable.

3. **Secrets Management**: Never commit `.env` files or vault passwords to version control. Use CI/CD secrets management for sensitive values.

4. **Variable Naming**: All environment variables use uppercase with underscores, matching the Ansible template conventions.

