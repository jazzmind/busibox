---
created: 2025-01-17
updated: 2025-01-17
status: completed
category: testing
tags: [authz, testing, shared-library, deployment]
---

# Authz Shared Testing Library Integration - 2025-01-17

## Summary

Integrated the shared testing library into the authz service to enable the pytest failed test filter plugin and maintain consistency with other services (ingest, search, agent).

## Problem

Authz service was not using the shared testing library, causing:
1. Import error when trying to use `pytest_plugins = ["testing.pytest_failed_filter"]`
2. Inconsistency with other services that all use the shared library
3. Missing pytest plugin for failed test filter generation

**Error**:
```
ImportError: Error importing plugin "testing.pytest_failed_filter": No module named 'testing'
```

## Solution

### 1. Deploy Shared Testing Library to Authz

**File**: `provision/ansible/roles/authz/tasks/main.yml`

Added task to copy shared testing library (same as ingest, search, agent):

```yaml
- name: Copy shared testing library
  synchronize:
    src: "{{ playbook_dir }}/../../srv/shared/testing/"
    dest: "{{ authz_app_dir }}/src/testing/"
    delete: yes
    rsync_opts:
      - "--exclude=__pycache__"
      - "--exclude=*.pyc"
  tags: [authz, authz_tests]
```

**Deployed Structure**:
```
/srv/authz/app/
├── src/
│   ├── testing/           # ← Shared testing library
│   │   ├── auth.py
│   │   ├── pytest_failed_filter.py
│   │   ├── fixtures.py
│   │   └── ...
│   └── routes/
│       └── ...
└── tests/
    ├── conftest.py
    └── ...
```

### 2. Update Authz Conftest to Use Shared Library

**File**: `srv/authz/tests/conftest.py`

Added path setup and plugin import:

```python
# Add shared testing library to path (deployed to ../src/testing/ relative to tests/)
# When deployed: /srv/authz/app/src/testing/
# When local: srv/authz/src/testing/
_authz_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_testing_path = os.path.join(_authz_root, "src", "testing")
if os.path.exists(_testing_path):
    if _testing_path not in sys.path:
        sys.path.insert(0, _testing_path)
else:
    # Fallback: try parent directory (for local dev)
    _testing_path_alt = os.path.join(os.path.dirname(_authz_root), "shared", "testing")
    if os.path.exists(_testing_path_alt) and _testing_path_alt not in sys.path:
        sys.path.insert(0, _testing_path_alt)

# Enable pytest plugin for failed test filter generation
pytest_plugins = ["testing.pytest_failed_filter"]
```

## Benefits

1. **Consistent Testing Infrastructure**: All services now use the same shared testing library
2. **Failed Test Filters**: Authz tests now generate rerun filters like other services
3. **Shared Utilities**: Authz can now use shared auth helpers, fixtures, and utilities
4. **Maintainability**: Single source of truth for testing utilities

## Deployment

The shared testing library will be deployed automatically when running:

```bash
# Full deployment
cd provision/ansible
make authz INV=inventory/test

# Or just update tests
ansible-playbook -i inventory/test/hosts.yml playbooks/authz.yml --tags authz_tests
```

## Testing

After deployment, authz tests will automatically:
1. Load the shared testing library from `src/testing/`
2. Enable the pytest failed test filter plugin
3. Generate rerun commands when tests fail

**Example Output**:
```
═══════════════════════════════════════════════════════════════════════
Failed Test Rerun Filter
═══════════════════════════════════════════════════════════════════════

To rerun 2 failed test(s):
  pytest tests/test_oauth.py::test_token_exchange tests/test_sessions.py::test_validate

Or using -k filter:
  pytest -k "test_token_exchange or test_validate"

Failed tests saved to: /tmp/pytest-failed-tests.txt
```

## Files Modified

### Deployment
- `provision/ansible/roles/authz/tasks/main.yml` - Added shared testing library deployment

### Test Configuration
- `srv/authz/tests/conftest.py` - Added path setup and plugin import

## Verification

To verify the integration works:

```bash
# SSH to authz container
ssh root@<authz-ip>

# Check shared testing library is deployed
ls -la /srv/authz/app/src/testing/
# Should show: auth.py, pytest_failed_filter.py, fixtures.py, etc.

# Run tests
cd /srv/authz/app
source ../venv/bin/activate
export PYTHONPATH=/srv/authz/app/src
pytest tests/ -v

# Should see failed test filter at end if any tests fail
```

## Related Services

All services now use the shared testing library:
- ✅ **ingest** - `/srv/ingest/src/testing/`
- ✅ **search** - `/opt/search/src/testing/`
- ✅ **agent** - `/srv/agent/src/testing/`
- ✅ **authz** - `/srv/authz/app/src/testing/` (NEW)

## Related Documentation

- Test improvements: `docs/development/session-notes/2025-01-17-test-improvements.md`
- Shared testing library: `srv/shared/testing/`
- Pytest plugin: `srv/shared/testing/pytest_failed_filter.py`

## Rules Applied

Following `.cursorrules`:
- Documentation in `docs/development/session-notes/`
- Used kebab-case for filename
- Included metadata header with tags
- Deployment task follows existing patterns
- No breaking changes to existing functionality
