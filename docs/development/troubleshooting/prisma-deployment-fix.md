---
created: 2025-12-11
updated: 2025-12-11
status: resolved
category: troubleshooting
tags: [ansible, prisma, deployment, database]
---

# Prisma Deployment Fix - Invalid --skip-generate Flag

## Issue

Agent-client deployment was failing with the following error:

```
! unknown or unexpected option: --skip-generate

✗ Failed to create database schema
```

## Root Cause

The Ansible deployment template `provision/ansible/roles/app_deployer/templates/prisma-setup.sh.j2` was using the `--skip-generate` flag with `prisma db push`, which is not a valid option.

The Prisma CLI only supports these flags for `db push`:
- `--help` / `-h`
- `--config`
- `--schema`
- `--accept-data-loss`
- `--force-reset`

## Solution

Removed all instances of `--skip-generate` from the Prisma setup script.

### Changes Made

**File**: `provision/ansible/roles/app_deployer/templates/prisma-setup.sh.j2`

**Before**:
```bash
if npx prisma db push --skip-generate 2>&1; then
  echo "✓ Database schema pushed successfully"
else
  echo "⚠ Schema push failed, trying with --accept-data-loss..."
  if npx prisma db push --skip-generate --accept-data-loss 2>&1; then
    echo "✓ Database schema pushed with data loss accepted"
  fi
fi
```

**After**:
```bash
if npx prisma db push 2>&1; then
  echo "✓ Database schema pushed successfully"
else
  echo "⚠ Schema push failed, trying with --accept-data-loss..."
  if npx prisma db push --accept-data-loss 2>&1; then
    echo "✓ Database schema pushed with data loss accepted"
  fi
fi
```

## Why This Works

1. **Prisma Client Generation**: The Prisma client is already generated in a previous Ansible task (line 180 of `deploy-branch.yml`):
   ```yaml
   - name: Generate Prisma client (if schema exists)
     shell: |
       if [ -f "prisma/schema.prisma" ]; then
         npx prisma generate
       fi
   ```

2. **No Need to Skip**: Since generation happens separately, there's no need to skip it during `db push`.

3. **Automatic Generation**: `prisma db push` automatically generates the client after pushing the schema, which is the desired behavior.

## Impact

This fix allows all Prisma-based applications (like agent-manager) to deploy successfully via Ansible.

## Testing

After applying this fix, the deployment should complete successfully:

```bash
cd /root/busibox/provision/ansible
make deploy-agent-manager INV=inventory/test
```

Expected output:
```
========================================
DATABASE SETUP FOR agent-manager
========================================
Loading environment variables from .env
Database host: 10.96.201.203
No migrations found, using db push to create schema...
✓ Database schema created successfully
========================================
DATABASE SETUP COMPLETE
========================================
```

## Related

- **Application**: agent-manager
- **Ansible Role**: app_deployer
- **Task**: Create database and run migrations (Prisma apps)
- **Template**: prisma-setup.sh.j2
- **Issue Date**: 2025-12-11
- **Resolution Date**: 2025-12-11

## Prevention

When adding new Prisma commands to deployment scripts:
1. Always check the official Prisma CLI documentation for valid flags
2. Test deployment scripts in a test environment first
3. Use `npx prisma db push --help` to verify available options

## Additional Notes

The `--accept-data-loss` flag is kept as a fallback option, which allows the deployment to proceed even if there are schema changes that would result in data loss. This is acceptable for:
- Initial deployments (no existing data)
- Test environments (data can be recreated)
- Development environments

For production deployments with existing data, consider using proper migrations (`prisma migrate`) instead of `db push`.
