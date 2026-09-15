---
title: "Configuration"
category: "administrator"
order: 3
description: "Configure Busibox settings through the Busibox Portal and environment"
published: true
---

# Configuration

Busibox is configured through a combination of the Busibox Portal admin interface and environment variables managed by Ansible. Most day-to-day settings are accessible through the portal.

## Busibox Portal Admin Settings

The Busibox Portal provides a web-based admin interface for common configuration tasks. Access it at **Admin** in the portal navigation (requires admin role).

### User Management

- **Create users** -- add new users with email and assign roles
- **Assign roles** -- control what each user can access (documents, apps, admin features)
- **Authentication methods** -- users can set up passkeys, TOTP, or use magic links

### Role-Based Access Control

Roles determine what users can see and do:

| Role | Capabilities |
|------|-------------|
| **Admin** | Full access to all features, user management, app deployment |
| **User** | Upload documents, search, chat with agents, use assigned apps |
| **Guest** | Limited access, typically read-only |

Custom roles can be created to match your organization's needs. Roles control:
- Which documents are visible (via shared visibility)
- Which apps can be launched
- Which agents are available
- Admin panel access

### Ingestion Settings

Configure how documents are processed:

| Setting | Options | Default |
|---------|---------|---------|
| **Extraction strategy** | Simple, Marker, ColPali | Simple |
| **LLM cleanup** | Enable/disable | Disabled |
| **Chunk size** | Min/max tokens | 400-800 |
| **Chunk overlap** | Percentage | 12% |
| **ColPali** | Enable/disable visual embeddings | Disabled |

- **Simple** extraction works for most text-based documents
- **Marker** uses GPU-accelerated layout analysis for complex PDFs
- **ColPali** uses a vision-language model for scanned documents and images
- **LLM cleanup** passes extracted text through an LLM to fix OCR artifacts

### App Management

See [Apps](04-apps.md) for details on installing and managing applications.

## Environment Configuration

Environment-specific settings are managed through Ansible inventory files.

### Configuration Files

| File | Purpose |
|------|---------|
| `provision/pct/vars.env` | Container IDs, IPs, SSH keys (Proxmox only) |
| `provision/ansible/inventory/*/group_vars/` | Environment-specific variables |
| `provision/ansible/group_vars/all/` | Shared variables across environments |
| `provision/ansible/roles/secrets/vars/vault.yml` | Encrypted secrets |

### Key Environment Variables

#### Data Processing

| Variable | Purpose | Default |
|----------|---------|---------|
| `CHUNK_SIZE_MIN` | Minimum chunk size (tokens) | 400 |
| `CHUNK_SIZE_MAX` | Maximum chunk size (tokens) | 800 |
| `CHUNK_OVERLAP_PCT` | Overlap between chunks | 12 |
| `LLM_CLEANUP_ENABLED` | Enable LLM text cleanup | false |
| `FASTEMBED_MODEL` | Text embedding model | BAAI/bge-large-en-v1.5 |
| `COLPALI_ENABLED` | Enable visual embeddings | false |
| `EMBEDDING_BATCH_SIZE` | Batch size for embeddings | 32 |

#### AI Models

| Variable | Purpose | Default |
|----------|---------|---------|
| `LITELLM_BASE_URL` | LiteLLM gateway URL | (auto-configured) |
| `LITELLM_API_KEY` | Gateway API key | (from vault) |
| `AGENT_SERVER_DEFAULT_MODEL` | Default model for agents | gpt-4o-mini |
| `ENABLE_RERANKING` | Enable search result reranking | false |
| `RERANKER_MODEL` | Model for reranking | (configurable) |

#### AI Chat (agent-api)

Chat responses run as server-side jobs that outlive the browser
connection; see `docs/developers/chat-detached-turns.md`.

| Variable | Purpose | Default |
|----------|---------|---------|
| `CHAT_NOTIFY_EMAIL_ENABLED` | Email the user when a response finishes while they are not connected | true |
| `CHAT_NOTIFY_MIN_SECONDS` | Responses at least this long also email a connected user | 120 |
| `CHAT_MAX_RUNNING_TURNS_PER_USER` | Concurrent responses per user (429 beyond this) | 3 |
| `CHAT_TURN_STOP_GRACE_SECONDS` | Seconds a stopped response gets to wind down before it is cancelled | 5 |
| `CHAT_TURN_EVENT_TTL_SECONDS` | How long a finished response can still be reattached to (Redis) | 86400 |
| `CHAT_TURN_EVENT_MAXLEN` | Events kept per response in Redis | 5000 |
| `PORTAL_BASE_URL` | Base URL used in email links | (auto-configured) |
| `BRIDGE_API_URL` | Email transport for notifications (else SMTP via `EMAIL_PROVIDER`) | (auto-configured) |

#### Personal memory (agent-api)

Per-user memory files, encrypted under the user's own key and readable only
in that user's chat turns; see `docs/developers/user-memory.md` — including
the one-time row-level-security policy to apply.

| Variable | Purpose | Default |
|----------|---------|---------|
| `MEMORY_ENABLED` | Platform switch for personal memory | true |
| `MEMORY_ENCRYPTION_REQUIRED` | Refuse to store memory in clear if the authz keystore is unreachable | true |
| `MEMORY_CURATOR_PURPOSE` | Model purpose that curates memory after turns (`agent` = local) | agent |
| `MEMORY_GATE_PURPOSE` | Model purpose for the cheap "anything durable here?" check | fast |
| `MEMORY_MAX_FILES` | Files per user | 40 |
| `MEMORY_MAX_FILE_BYTES` | Size cap per file | 8000 |
| `MEMORY_CORE_MAX_CHARS` | profile + preferences characters injected per turn | 3000 |

#### Security

| Variable | Purpose | Default |
|----------|---------|---------|
| `AUTHZ_TOKEN_TTL` | Token lifetime (seconds) | 900 (15 min) |
| `JWT_ISSUER` | JWT issuer identifier | (auto-configured) |

### Applying Configuration Changes

After editing environment variables:

```bash
# Redeploy affected services
make install SERVICE=data,search

# Or restart without redeploying
make manage SERVICE=data ACTION=restart
```

**Important**: Use `make install` (not `make manage ACTION=restart`) when changing environment variables, because `install` re-injects secrets from the vault. A simple restart uses the existing environment.

## SSL/TLS Configuration

### Production (Proxmox)

SSL is terminated at the nginx reverse proxy. Certificates are configured in the nginx role:

```bash
# Deploy/update nginx with SSL
make install SERVICE=nginx
```

### Local Development (Docker)

Generate self-signed certificates for local HTTPS:

```bash
bash scripts/setup/generate-local-ssl.sh
```

On macOS, trust the certificate:

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain ssl/localhost.crt
```

## Nginx API Gateway

The nginx reverse proxy routes all traffic and provides unified API access:

| Path | Backend Service |
|------|----------------|
| `/` | Busibox Portal |
| `/agents/` | Busibox Agents |
| `/api/authz/` | AuthZ Service |
| `/api/ingest/` | Data API |
| `/api/search/` | Search API |
| `/api/agent/` | Agent API |
| `/api/llm/` | LiteLLM Gateway |

Custom apps are automatically added to nginx routing when deployed.

## Vault Management

### Editing Secrets

```bash
cd provision/ansible
ansible-vault edit roles/secrets/vars/vault.yml
```

### Vault Backup

Back up the vault file and your vault password separately. Without the password, encrypted secrets cannot be recovered.

### Rotating Secrets

After changing secrets in the vault, redeploy affected services:

```bash
make install SERVICE=authz,data,agent
```

## Reference

- [Busibox Portal Environment Variables](../developers/reference/busibox-portal-environment-variables.md) — Full env var reference
- [Config API](../developers/reference/config-api.md) — Runtime config via database
- [LiteLLM Master Key](../developers/reference/litellm-master-key.md) — LLM gateway auth
- [Optional Secrets](../developers/reference/optional-secrets.md) — Required vs optional vault keys

## Next Steps

- [Install and manage apps](04-apps.md)
- [Configure AI models](05-ai-models.md)
- [Command-line management](06-manage.md)
