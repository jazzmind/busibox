# Make Commands - The ONLY Way to Manage Services

**Purpose**: Ensure all service operations use the unified `make` interface  
**Critical**: NEVER run `docker compose`, `docker`, or `ansible-playbook` commands directly

## The Golden Rule

**ALWAYS use `make` commands for ALL deployment and service management operations.**

```
❌ NEVER DO THIS:
   docker compose up -d authz-api
   docker restart prod-authz-api
   ansible-playbook -i inventory/docker docker.yml --tags authz
   
✅ ALWAYS DO THIS:
   make install SERVICE=authz
   make manage SERVICE=authz ACTION=restart
```

## Why This Matters

1. **Secrets Management**: `make` commands automatically inject secrets from Ansible Vault
2. **Environment Detection**: Automatically detects the current environment (production, staging, development, demo)
3. **Backend Awareness**: Works correctly for both Docker and Proxmox deployments
4. **Consistency**: Same commands work across all environments
5. **No Credential Leaks**: Secrets never appear in `.env` files or command line

## Quick Reference

### Deploy a Service

```bash
# Deploy single service
make install SERVICE=authz

# Deploy multiple services  
make install SERVICE=authz,agent,ingest

# Deploy service groups
make install SERVICE=apis          # All API services
make install SERVICE=infrastructure  # postgres, redis, minio, milvus
make install SERVICE=frontend      # core-apps, nginx
make install SERVICE=all           # Everything
```

### Manage Running Services

```bash
# Restart a service
make manage SERVICE=authz ACTION=restart

# Stop a service
make manage SERVICE=authz ACTION=stop

# Start a service
make manage SERVICE=authz ACTION=start

# View logs (follows)
make manage SERVICE=authz ACTION=logs

# Check status
make manage SERVICE=authz ACTION=status

# Full rebuild and redeploy via Ansible
make manage SERVICE=authz ACTION=redeploy

# Multiple services
make manage SERVICE=postgres,redis ACTION=status
```

### Available Services

**Infrastructure**: `postgres`, `redis`, `minio`, `milvus`  
**APIs**: `authz`, `agent`, `ingest`, `search`, `deploy`, `docs`, `embedding`  
**LLM**: `litellm`, `ollama`, `vllm`  
**Frontend**: `core-apps`, `nginx`  
**User Apps**: `user-apps`  
**Utilities**: `internal-dns` (updates /etc/hosts on all containers)

### Service Groups

| Group | Services |
|-------|----------|
| `infrastructure` | postgres, redis, minio, milvus |
| `apis` | authz, agent, ingest, search, deploy, docs, embedding |
| `llm` | litellm |
| `frontend` | core-apps, nginx |
| `all` | Everything |

## How It Works

When you run a `make` command:

1. **Reads State**: Gets current environment from `.busibox-state-*` file
2. **Detects Backend**: Determines if using Docker or Proxmox
3. **Loads Vault**: Accesses encrypted secrets via `~/.vault_pass`
4. **Runs Ansible**: Executes the appropriate Ansible playbook with:
   - Correct inventory (docker/staging/production)
   - Correct tags for the requested service
   - Secrets injected as environment variables (never written to files)
5. **Reports Status**: Shows deployment results

## Environment Auto-Detection

The commands automatically detect your environment:

| State File | Environment | Container Prefix |
|------------|-------------|-----------------|
| `.busibox-state-prod` | production | `prod-` |
| `.busibox-state-staging` | staging | `staging-` |
| `.busibox-state-demo` | demo | `demo-` |
| `.busibox-state-dev` | development | `dev-` |

## Common Scenarios

### "I need to restart authz after code changes"

```bash
make manage SERVICE=authz ACTION=redeploy
```
This rebuilds the container with new code AND injects fresh secrets.

### "I need to check if services are healthy"

```bash
make manage SERVICE=authz,postgres,minio ACTION=status
```

### "I want to see what's happening in the logs"

```bash
make manage SERVICE=authz ACTION=logs
```

### "I need to deploy all APIs"

```bash
make install SERVICE=apis
```

### "I need to restart everything after a config change"

```bash
make manage SERVICE=all ACTION=restart
```

### "I need to update /etc/hosts on all containers"

After adding new services or changing IPs:

```bash
make internal-dns INV=inventory/staging
# or via the manage menu:
make manage  # then press 'd' for internal DNS
```

## What Happens Behind the Scenes

When you run `make install SERVICE=authz`:

```
1. Detects environment: production
2. Detects backend: docker  
3. Sets CONTAINER_PREFIX=prod, BUSIBOX_ENV=production
4. Runs: ansible-playbook -i inventory/docker docker.yml --tags authz --vault-password-file ~/.vault_pass
5. Ansible:
   - Loads secrets from vault
   - Builds container if needed
   - Starts container with secrets as environment variables
   - Waits for health check
```

## Troubleshooting

### "I accidentally ran docker compose directly and now my service won't start"

The service is missing secrets. Fix it:
```bash
make manage SERVICE=<service> ACTION=redeploy
```

### "I see 'password authentication failed' errors"

Secrets weren't injected. Always use make:
```bash
make install SERVICE=<service>
```

### "The wrong environment's containers are starting"

Check which state file exists:
```bash
ls -la .busibox-state-*
```

### "I don't have ~/.vault_pass"

You need the vault password file. Get it from your team or create one:
```bash
echo "your-vault-password" > ~/.vault_pass
chmod 600 ~/.vault_pass
```

## AI Agent Instructions

When asked to deploy, start, stop, restart, or manage any service:

1. **ALWAYS use `make` commands**
2. **NEVER suggest `docker compose`, `docker`, or `ansible-playbook` directly**
3. **Use the appropriate command**:
   - Deploying/rebuilding → `make install SERVICE=x`
   - Managing running service → `make manage SERVICE=x ACTION=y`
4. **Explain the command** so the user understands what it does
5. **Check the result** by suggesting `make manage SERVICE=x ACTION=status`

## Related Documentation

- [Makefile Quick Reference](../../CLAUDE.md#common-commands)
- [Service Organization](002-script-organization.md)
- [Zero Trust Authentication](003-zero-trust-authentication.md)
