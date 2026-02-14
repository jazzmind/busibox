# Rename "test" Environment to "staging"

**Date**: 2026-01-17  
**Status**: Complete  
**Category**: Infrastructure  
**Impact**: Breaking change for MCP server API

## Summary

Renamed all references to the "test" environment to "staging" to align with the actual Ansible inventory structure. The pre-production environment has always been called "staging" in the `inventory/staging` directory, but documentation and tooling incorrectly referred to it as "test".

## Changes Made

### 1. MCP Server (v2.2.0 → v3.0.0)

**Breaking Changes:**
- All API parameters changed from `environment: 'test'` to `environment: 'staging'`
- All inventory references changed from `inventory/test` to `inventory/staging`
- Variable names changed: `isTest` → `isStaging`
- Enum values updated in all tool schemas

**Files Modified:**
- `tools/mcp-server/src/index.ts` - Complete find/replace
- `tools/mcp-server/package.json` - Version bump to 3.0.0
- `tools/mcp-server/README.md` - Documentation and changelog
- `tools/mcp-server/OVERVIEW.md` - Usage examples
- `tools/mcp-server/dist/index.js` - Rebuilt

### 2. Ansible Makefile

**Files Modified:**
- `provision/ansible/Makefile`
  - Updated comments: `INV=inventory/test` → `INV=inventory/staging`
  - Updated conditionals: `ifeq ($(INV),inventory/test)` → `ifeq ($(INV),inventory/staging)`
  - Updated IP assignment comments

### 3. Shell Scripts - Hard Cutover

**Container Creation Scripts** (provision/pct/containers/):
- `create_lxc_base.sh` - Main orchestrator
- `create-core-services.sh` - proxy, apps, agent
- `create-data-services.sh` - postgres, milvus, minio
- `create-worker-services.sh` - ingest, litellm
- `create-vllm.sh` - vLLM service
- `create-ollama.sh` - Ollama service

**Host Setup Scripts** (provision/pct/host/):
- `add-data-mounts.sh`
- `check-container-memory.sh`
- `setup-llm-models.sh`
- `configure-container-gpus.sh`
- `configure-gpu-allocation.sh`
- `configure-gpu-passthrough.sh`
- `configure-vllm-model-routing.sh`
- `install-nvidia-drivers.sh`
- `setup-proxmox-host.sh`
- `setup-vllm-alias.sh`
- `setup-zfs-storage.sh`
- `update-model-config.sh`

**Test & Utility Scripts**:
- `scripts/test/bootstrap-test-credentials.sh`
- `scripts/test/generate-local-test-env.sh`
- `scripts/test/test-bedrock-setup.sh`
- `scripts/test/test-colpali.sh`
- `scripts/test/test-signal-bot.sh`
- `scripts/test/test-infrastructure.sh`
- `scripts/test/test-llm-containers.sh`
- `scripts/test/test-vllm-embedding.sh`
- `scripts/generate/update-bedrock-credentials.sh`
- `scripts/generate/generate-token-service-keys.sh`
- `tests/security/run_tests.sh`

**Deprecated Scripts** (also updated for consistency):
- All scripts in `scripts/deprecated/` directory

**Documentation**:
- `provision/pct/README.md`

**Changes Applied:**
- `[test|production]` → `[staging|production]` in usage strings
- `MODE == "test"` → `MODE == "staging"` in conditionals
- `ENV != "test"` → `ENV != "staging"` in validations
- Error messages updated: `Use 'test' or 'production'` → `Use 'staging' or 'production'`
- All documentation strings updated

### 4. Documentation (NOT updated)

**Note:** There are 155+ references to `INV=inventory/test` in documentation files (*.md). These were intentionally NOT updated in this session to avoid overwhelming changes. These should be updated in a future session or via a script.

Affected areas:
- `docs/guides/**/*.md` - Deployment guides
- `docs/development/**/*.md` - Development documentation  
- `specs/**/*.md` - Specifications
- `provision/ansible/SETUP.md` - Setup guide
- `provision/ansible/roles/**/README.md` - Role documentation

## Important Notes

### Container Naming Convention
The **TEST-*** container prefix remains unchanged and refers to staging environment containers:
- Production: `authz-lxc` (10.96.200.210)
- Staging: `TEST-authz-lxc` (10.96.201.210)

This is intentional - the TEST- prefix is a naming convention that predates the Ansible inventory naming.

### Network Ranges
- **Production**: 10.96.200.0/21 (containers 200-210)
- **Staging**: 10.96.201.0/21 (containers 300-310, named TEST-*)

### Migration Guide for Users

If you're using the MCP server, update your calls:

**Before (v2.2.0):**
```typescript
run_make_target({ target: 'authz', environment: 'test' })
```

**After (v3.0.0):**
```typescript
run_make_target({ target: 'authz', environment: 'staging' })
```

**Before (command line):**
```bash
make authz INV=inventory/test
```

**After (command line):**
```bash
make authz INV=inventory/staging
```

## Verification

1. MCP server builds successfully: ✅
2. TypeScript type checking passes: ✅
3. No linter errors: ✅
4. Makefile syntax valid: ✅

## Related Issue

This change was prompted while troubleshooting the authz deployment failure on staging (TEST-authz-lxc). The deployment command referenced `inventory/test` which doesn't exist - the actual inventory is `inventory/staging`.

## Future Work

1. Create a script to update all documentation references from `inventory/test` to `inventory/staging`
2. Update role README files in `provision/ansible/roles/*/README.md`
3. Update setup guide `provision/ansible/SETUP.md`
4. Consider adding a deprecation warning if old `inventory/test` path is used

## Testing

The authz service deployment now works correctly with the staging inventory after fixing the database ownership issue that was discovered during investigation.
