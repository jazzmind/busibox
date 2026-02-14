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

### Step 1: Generate Keys

On your development machine with the agent-server repository:

```bash
cd /path/to/agent-server
npm install  # If not already installed
npm run setup-auth
```

This will output something like:

```
🔑 Generating token service keys...

📋 Summary:
   Agent ID: token-service
   Key ID (kid): a1b2c3d4-e5f6-7890-abcd-ef1234567890
   Algorithm: EdDSA (Ed25519)

🔒 Environment Variables (for deployment):
TOKEN_SERVICE_PRIVATE_KEY='{"kty":"OKP","crv":"Ed25519","d":"...","x":"...","kid":"...","use":"sig","alg":"EdDSA","agent_id":"token-service"}'
TOKEN_SERVICE_PUBLIC_KEY='{"kty":"OKP","crv":"Ed25519","x":"...","kid":"...","use":"sig","alg":"EdDSA","agent_id":"token-service"}'

📁 Local Files (for backup/development):
   Public key: keys/a1b2c3d4-e5f6-7890-abcd-ef1234567890.public.jwk.json
   Private key: keys/a1b2c3d4-e5f6-7890-abcd-ef1234567890.private.jwk.json
   JWKS: keys/a1b2c3d4-e5f6-7890-abcd-ef1234567890.jwks.json

⚠️  Keep the private key secure and never commit it to version control!
```

### Step 2: Copy Keys to Ansible Vault

1. **Edit your Ansible vault:**

```bash
cd /path/to/busibox/provision/ansible
ansible-vault edit roles/secrets/vars/vault.yml
```

2. **Add the keys to the `agent-server` section:**

```yaml
secrets:
  agent-server:
    # ... existing config ...
    
    # Token Service Keys (Ed25519 JWK format)
    # Copy the ENTIRE JSON string from the setup-auth output above
    token_service_private_key: '{"kty":"OKP","crv":"Ed25519","d":"...","x":"...","kid":"...","use":"sig","alg":"EdDSA","agent_id":"token-service"}'
    token_service_public_key: '{"kty":"OKP","crv":"Ed25519","x":"...","kid":"...","use":"sig","alg":"EdDSA","agent_id":"token-service"}'
```

**Important Notes:**
- Copy the **entire JSON object** including the outer single quotes
- The private key includes the `"d"` field (private exponent) - keep this secret!
- The public key only includes the `"x"` field (public point)
- Both keys must have the same `"kid"` (key ID)

### Step 3: Deploy

After adding the keys to vault, deploy the agent-server:

```bash
cd /path/to/busibox/provision/ansible

# Deploy to test environment
make deploy-agent-server INV=inventory/test

# Deploy to production
make deploy-agent-server
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

## Related Documentation

- **Agent Server**: `docs/architecture/06-agents.md`
- **Authentication**: `docs/architecture/02-ai.md#authentication`
- **Ansible Secrets**: `docs/configuration/ansible-secrets.md`
- **Deployment**: `docs/deployment/agent-server.md`

## References

- **Ed25519**: High-performance elliptic curve signature algorithm
- **JWK**: JSON Web Key (RFC 7517)
- **EdDSA**: Edwards-curve Digital Signature Algorithm (RFC 8032)
- **OAuth 2.0**: Authorization framework (RFC 6749)
