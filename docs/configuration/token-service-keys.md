---
created: 2025-12-05
updated: 2025-12-05
status: active
category: configuration
---

# Token Service Keys Configuration

## Overview

The Agent Server uses Ed25519 cryptographic keys for signing and verifying JWT tokens in the OAuth 2.0 flow. These keys must be generated and configured in Ansible vault before deploying the agent-server.

## Key Generation

### Automated Method (Recommended)

The easiest way to generate and configure TOKEN_SERVICE keys is using the automated script:

**Option 1: Via configure menu**
```bash
cd /path/to/busibox/provision/ansible
make configure
# Select: Secrets & Configuration → Generate TOKEN_SERVICE Keys
```

**Option 2: Direct command**
```bash
cd /path/to/busibox/provision/ansible
make generate-token-keys
```

**Option 3: Run script directly**
```bash
cd /path/to/busibox
bash scripts/generate-token-service-keys.sh
```

This script will:
1. Check if agent-server repository exists (expects it at `../agent-server`)
2. Generate Ed25519 keypair using `npm run setup-auth`
3. Automatically decrypt your vault (using `~/.vault_pass` or prompting)
4. Add the keys to the `agent-server` section
5. Re-encrypt the vault

**Requirements:**
- Python 3.11+ with `cryptography` library (uses standalone key generator in `scripts/lib/`)
- `~/.vault_pass` file OR you'll be prompted for vault password

**Note:** This uses a standalone Python key generator built into busibox - no need for the agent-server repository!

### Manual Method (If Needed)

If you need to generate keys manually:

**Step 1: Generate Keys**

```bash
cd /path/to/agent-server
npm install  # If not already installed
npm run setup-auth
```

This will output:
```
🔑 Generating token service keys...
📋 Summary:
   Agent ID: token-service
   Key ID (kid): a1b2c3d4-e5f6-7890-abcd-ef1234567890
   Algorithm: EdDSA (Ed25519)

🔒 Environment Variables (for deployment):
TOKEN_SERVICE_PRIVATE_KEY='{"kty":"OKP",...}'
TOKEN_SERVICE_PUBLIC_KEY='{"kty":"OKP",...}'
```

**Step 2: Add to Vault**

```bash
cd /path/to/busibox/provision/ansible
ansible-vault edit roles/secrets/vars/vault.yml
```

Add to the `agent-server` section:
```yaml
secrets:
  agent-server:
    # ... existing config ...
    token_service_private_key: '{"kty":"OKP","crv":"Ed25519","d":"...","x":"...","kid":"...","use":"sig","alg":"EdDSA","agent_id":"token-service"}'
    token_service_public_key: '{"kty":"OKP","crv":"Ed25519","x":"...","kid":"...","use":"sig","alg":"EdDSA","agent_id":"token-service"}'
```

**Important:** Copy the entire JSON string including outer single quotes.

## Deployment

After generating/adding the keys to vault, deploy the agent-server:

```bash
cd /path/to/busibox/provision/ansible

# Deploy to test environment
make deploy-agent-server INV=inventory/test

# Deploy to production
make deploy-agent-server

# Or deploy all services
make all
```

## Key Format

The keys are in JWK (JSON Web Key) format with the following structure:

**Private Key:**
```json
{
  "kty": "OKP",           // Key type: Octet Key Pair
  "crv": "Ed25519",       // Curve: Ed25519
  "d": "...",             // Private exponent (secret!)
  "x": "...",             // Public point
  "kid": "...",           // Key ID (UUID)
  "use": "sig",           // Usage: signature
  "alg": "EdDSA",         // Algorithm: EdDSA
  "agent_id": "token-service"
}
```

**Public Key:**
```json
{
  "kty": "OKP",
  "crv": "Ed25519",
  "x": "...",             // Public point only
  "kid": "...",
  "use": "sig",
  "alg": "EdDSA",
  "agent_id": "token-service"
}
```

## Security Best Practices

1. **Never commit keys to version control**
   - Keys are stored in encrypted Ansible vault only
   - The `keys/` directory in agent-server is gitignored

2. **Rotate keys periodically**
   - Generate new keys every 6-12 months
   - Update vault and redeploy

3. **Backup keys securely**
   - Store backup copies in secure password manager
   - Keep private keys encrypted at rest

4. **Separate keys per environment**
   - Use different keys for test vs production
   - Never share keys between environments

## Troubleshooting

### "Token service not configured" Error

**Symptom:** Agent-client or other services show "Token service not configured" error.

**Cause:** `TOKEN_SERVICE_PRIVATE_KEY` or `TOKEN_SERVICE_PUBLIC_KEY` environment variables are missing or invalid.

**Solution:**
1. Verify keys are in vault: `ansible-vault view roles/secrets/vars/vault.yml`
2. Check keys are listed in `apps.yml` secrets for agent-server
3. Redeploy agent-server: `make deploy-agent-server`
4. Verify environment variables are set:
   ```bash
   ssh root@<agent-ip>
   systemctl cat agent-server | grep TOKEN_SERVICE
   ```

### "Invalid token signature" Error

**Symptom:** Token validation fails with signature errors.

**Cause:** Public/private key mismatch or wrong keys being used.

**Solution:**
1. Ensure both keys have the same `kid` value
2. Regenerate keys if needed: `npm run setup-auth`
3. Update vault with matching key pair
4. Redeploy all services that use the keys

### Keys Not Loading

**Symptom:** Agent-server logs show "Failed to parse TOKEN_SERVICE_PRIVATE_KEY"

**Cause:** Invalid JSON format in vault.

**Solution:**
1. Verify JSON is valid (no missing quotes, commas, braces)
2. Ensure entire JSON is wrapped in single quotes in vault
3. Check for special characters that might need escaping
4. Regenerate keys and copy carefully

## Quick Reference

### Generate Keys
```bash
cd provision/ansible
make generate-token-keys
```

### Configure (Interactive Menu)
```bash
cd provision/ansible
make configure
# → Secrets & Configuration → Generate TOKEN_SERVICE Keys
```

### Deploy Agent Server
```bash
cd provision/ansible
make deploy-agent-server              # Production
make deploy-agent-server INV=inventory/test  # Test
```

### Verify Keys in Vault
```bash
cd provision/ansible
ansible-vault view roles/secrets/vars/vault.yml | grep token_service
```

## Related Documentation

- **Agent Server**: `docs/architecture/06-agents.md`
- **Authentication**: `docs/architecture/02-ai.md#authentication`
- **Ansible Secrets**: `docs/configuration/ansible-secrets.md`
- **Deployment**: `docs/deployment/agent-server.md`
- **Configure Script**: `scripts/configure.sh`

## References

- **Ed25519**: High-performance elliptic curve signature algorithm
- **JWK**: JSON Web Key (RFC 7517)
- **EdDSA**: Edwards-curve Digital Signature Algorithm (RFC 8032)
- **OAuth 2.0**: Authorization framework (RFC 6749)







