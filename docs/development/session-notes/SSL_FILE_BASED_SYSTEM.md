# SSL Certificate System - File-Based Implementation

**Date**: 2026-02-02  
**Status**: Complete  
**Category**: Infrastructure, Security

## Overview

Implemented a new file-based SSL certificate system that replaces the vault-based approach. SSL certificates are now stored in the `ssl/` directory and automatically discovered by Ansible during deployment.

## Problem

The previous SSL system had several issues:
1. Certificates stored as multiline text in Ansible vault (hard to manage)
2. Required multiple SSL "modes" (letsencrypt, provisioned, selfsigned)
3. Complex conditional logic based on `ssl_mode` variable
4. Mixing secrets with certificates in the vault
5. Certificate file was empty/corrupted causing nginx test failures

## Solution

### File-Based Certificate Storage

SSL certificates are now stored in `ssl/` directory with a clear naming convention:

```
ssl/
├── {domain}.crt              # Certificate
├── {domain}.key              # Private key
├── {domain}.fullchain.crt    # Full chain (recommended)
└── README.md                 # Documentation
```

### Automatic Certificate Discovery

The new `ssl-certificates.yml` task automatically discovers certificates with this priority:

1. **Exact domain match**: `staging.ai.example.com.crt` + `.key`
2. **Wildcard parent**: `ai.example.com.crt` + `.key` (covers `*.ai.example.com`)
3. **Self-signed fallback**: Auto-generated if no certificates found
4. **Fullchain preference**: Uses `.fullchain.crt` if available

### Example: Staging Deployment

For domain `staging.ai.jaycashman.com`:
1. Looks for `staging.ai.jaycashman.com.crt` (not found)
2. Extracts parent domain: `ai.jaycashman.com`
3. Finds wildcard certificate: `ai.jaycashman.com.fullchain.crt` + `.key` ✅
4. Copies to container as `/etc/ssl/busibox/staging.ai.jaycashman.com.crt`
5. nginx uses the wildcard certificate (valid for all subdomains)

## Implementation Details

### New Files Created

1. **`provision/ansible/roles/nginx/tasks/ssl-certificates.yml`**
   - SSL certificate discovery logic
   - Certificate copying to target containers
   - Self-signed fallback generation

2. **`scripts/organize-ssl-files.sh`**
   - Helper script to rename existing SSL files
   - Validates certificate + key pairs

3. **`ssl/README.md`**
   - Complete SSL certificate documentation
   - Naming conventions
   - How to add new certificates
   - Verification commands

### Modified Files

1. **`provision/ansible/roles/nginx/tasks/main.yml`**
   - Removed old `ssl_mode` detection
   - Replaced with `ssl-certificates.yml` include
   - Updated status messages to use `ssl_mode_used`

2. **`provision/ansible/roles/nginx/tasks/configure.yml`**
   - Removed vault loading for SSL certificates
   - Removed `detected_ssl_mode` conditional

3. **`provision/ansible/roles/nginx/tasks/configure-placeholders.yml`**
   - Removed vault loading for SSL certificates
   - Updated SSL mode display

4. **`provision/ansible/roles/secrets/vars/vault.example.yml`**
   - Removed `ssl_certificates` section
   - Added documentation about file-based SSL

### Deprecated Files

Moved to `provision/ansible/roles/nginx/tasks/_deprecated/`:
- `letsencrypt.yml` - Old Let's Encrypt mode
- `provisioned.yml` - Old vault-based certificate loading
- `selfsigned.yml` - Old self-signed generation

## Current SSL Setup

### Production Wildcard Certificate

```
ssl/ai.jaycashman.com.crt          # Certificate (covers *.ai.jaycashman.com)
ssl/ai.jaycashman.com.key          # Private key
ssl/ai.jaycashman.com.fullchain.crt # Full chain with intermediates
```

This wildcard certificate works for:
- `staging.ai.jaycashman.com`
- `prod.ai.jaycashman.com`
- Any subdomain of `ai.jaycashman.com`

### Certificate Verification

```bash
# Verify certificate matches private key
openssl x509 -noout -modulus -in ssl/ai.jaycashman.com.crt | openssl md5
openssl rsa -noout -modulus -in ssl/ai.jaycashman.com.key | openssl md5
# Hashes should match: 6bf68f6a70d380dc2b7c310e39bd55f3

# Check certificate details
openssl x509 -in ssl/ai.jaycashman.com.crt -noout -text

# Verify SAN includes wildcard
openssl x509 -in ssl/ai.jaycashman.com.crt -noout -text | grep -A2 "Subject Alternative Name"
# Output: DNS:*.ai.jaycashman.com, DNS:ai.jaycashman.com
```

## Benefits

1. **Simpler Management**: Drop certificate files in `ssl/` directory
2. **No Vault Clutter**: Secrets vault only contains actual secrets
3. **Version Control**: Certificates can be committed (if desired)
4. **Works Everywhere**: Same approach for Docker and Proxmox
5. **Automatic Discovery**: No manual configuration needed
6. **Wildcard Support**: One certificate for all subdomains
7. **Fallback**: Auto-generates self-signed if needed

## Usage

### Adding a New Certificate

```bash
# Place certificate files in ssl/ directory
cp your-cert.crt ssl/example.com.crt
cp your-key.key ssl/example.com.key

# Set correct permissions
chmod 644 ssl/example.com.crt
chmod 600 ssl/example.com.key

# Deploy (Ansible will automatically discover)
make install SERVICE=nginx
```

### For Wildcard Certificates

```bash
# Name with parent domain
cp wildcard-cert.crt ssl/example.com.crt
cp wildcard-key.key ssl/example.com.key

# This will work for:
# - example.com
# - *.example.com (staging.example.com, prod.example.com, etc.)
```

## Testing

Ansible will log the certificate discovery process:

```
TASK [nginx : Display SSL certificate decision]
ok: [host] => {
    "msg": "SSL Certificate Mode: wildcard-fullchain\nCertificate: /path/to/ssl/ai.jaycashman.com.fullchain.crt\nKey: /path/to/ssl/ai.jaycashman.com.key\n"
}
```

## Security Considerations

1. **Private Keys**: Should have `chmod 600` (owner read/write only)
2. **Git**: Consider if SSL files should be in `.gitignore` for public repos
3. **Fullchain**: Use `.fullchain.crt` to include intermediate certificates
4. **Backups**: Keep backup copies of certificates and keys separately

## Future Enhancements

### Let's Encrypt Integration (Optional)

Could add automatic certificate generation and renewal:

```bash
certbot certonly --dns-cloudflare \
  --dns-cloudflare-credentials ~/.secrets/cloudflare.ini \
  -d "*.ai.jaycashman.com" \
  -d "ai.jaycashman.com"
```

This would automatically place certificates in the correct location and handle renewals.

## Related Files

- `ssl/README.md` - SSL directory documentation
- `provision/ansible/roles/nginx/tasks/ssl-certificates.yml` - Discovery logic
- `scripts/organize-ssl-files.sh` - Helper script
- `provision/ansible/roles/nginx/tasks/_deprecated/` - Old SSL mode files

## Notes

- Old `ssl_mode` variable is deprecated but not removed (for backward compatibility)
- Certificate discovery runs on Ansible controller (using `delegate_to: localhost`)
- Self-signed fallback ensures deployment always succeeds
- Wildcard parent domain extraction: `staging.ai.example.com` → `ai.example.com`
