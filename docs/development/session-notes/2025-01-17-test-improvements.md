---
created: 2025-01-17
updated: 2025-01-17
status: completed
category: testing
tags: [testing, pytest, makefile, ci-cd, developer-experience]
---

# Test Infrastructure Improvements - 2025-01-17

## Summary

Improved test infrastructure to provide better developer experience when tests fail:
1. **Non-blocking test execution** - Makefile continues after failures to show complete summary
2. **Failed test filters** - Automatically generates pytest commands to rerun only failed tests
3. **Fixed remaining test issues** - ColPali and database schema bugs

## Changes Made

### 1. Non-Blocking Test Execution

**File**: `scripts/make/test.sh`

**Problem**: When tests failed, the script would `exit 1` immediately, preventing:
- Running remaining test suites
- Showing comprehensive summary
- Seeing all failures at once

**Solution**: Changed from `|| { error "..."; exit 1; }` to conditional execution with `return 1`:

```bash
# Before:
ssh "root@${ingest_ip}" "..." || {
    error "Ingest tests failed"
    exit 1
}

# After:
if ssh "root@${ingest_ip}" "..."; then
    success "Ingest tests passed!"
else
    error "Ingest tests failed"
    warn "To rerun failed tests, check output above for pytest filter"
    return 1  # Return instead of exit
fi
```

**Benefits**:
- All test suites run even if one fails
- Complete summary shown at the end
- Failed services are tracked and reported
- Menu system stays open for next action

### 2. Failed Test Filter Generation

**File**: `srv/shared/testing/pytest_failed_filter.py` (NEW)

Created a pytest plugin that automatically:
- Captures failed test node IDs during test run
- Generates pytest command to rerun only failed tests
- Saves failed tests to `/tmp/pytest-failed-tests.txt`
- Shows both direct path filter and `-k` pattern filter

**Output Example**:
```
═══════════════════════════════════════════════════════════════════════
Failed Test Rerun Filter
═══════════════════════════════════════════════════════════════════════

To rerun 3 failed test(s):

  pytest tests/test_foo.py::test_bar tests/test_baz.py::test_qux tests/test_foo.py::test_zap

Or using -k filter:
  pytest -k "test_bar or test_qux or test_zap"

Failed tests saved to: /tmp/pytest-failed-tests.txt
```

**Enabled in**:
- `srv/ingest/tests/conftest.py`
- `srv/search/tests/conftest.py`
- `srv/agent/tests/conftest.py`
- `srv/authz/tests/conftest.py` (with try/except for compatibility)

### 3. Fixed Remaining Test Issues

#### 3a. ColPali Test - Disabled by Default

**File**: `srv/ingest/tests/test_colpali.py`

**Issue**: `test_full_workflow` returned None because ColPali was disabled in config.

**Fix**: Explicitly enable ColPali in the test:
```python
config_dict = config.to_dict()
config_dict["colpali_enabled"] = True  # Enable for this test
embedder = ColPaliEmbedder(config_dict)
```

#### 3b. Ingest API - Wrong Table for Metadata

**File**: `srv/ingest/src/api/routes/files.py` (line 242)

**Issue**: Query tried to select `metadata` column from `processing_history` table, but that column only exists in `processing_strategy_results` table.

**Error**: `column "metadata" does not exist`

**Fix**: Changed query to use correct table:
```python
# Before:
SELECT metadata FROM processing_history WHERE file_id = $1

# After:
SELECT metadata FROM processing_strategy_results 
WHERE file_id = $1 AND success = true
```

#### 3c. PDF Extraction Tests - Already Fixed

Tests now skip gracefully when test documents aren't available (fixed earlier).

## Usage

### Running Tests with New Features

```bash
# Run all tests - will show summary even if some fail
cd provision/ansible
make test-all INV=inventory/test

# Run specific service tests
make test-ingest INV=inventory/test
make test-search INV=inventory/test
make test-agent INV=inventory/test

# After failures, rerun using generated filter
ssh root@10.96.201.206  # ingest server
cd /srv/ingest && source venv/bin/activate
pytest tests/test_foo.py::test_bar tests/test_baz.py::test_qux  # From output
```

### Pytest Plugin Usage

The plugin is automatically enabled in all service test suites. It will:
1. Track all failed tests during the run
2. Display a rerun filter at the end of the test session
3. Save failed tests to `/tmp/pytest-failed-tests.txt`

To manually enable in a test file:
```python
# In conftest.py
pytest_plugins = ["testing.pytest_failed_filter"]
```

## Test Summary Output

### Before
```
FAILED tests/test_foo.py::test_bar
FAILED tests/test_baz.py::test_qux
[ERROR] ingest tests failed!
make[1]: *** [test-ingest] Error 1
make: *** [menu] Error 2
# Menu exits, no summary shown
```

### After
```
FAILED tests/test_foo.py::test_bar
FAILED tests/test_baz.py::test_qux

═══════════════════════════════════════════════════════════════════════
Failed Test Rerun Filter
═══════════════════════════════════════════════════════════════════════

To rerun 2 failed test(s):
  pytest tests/test_foo.py::test_bar tests/test_baz.py::test_qux

Failed tests saved to: /tmp/pytest-failed-tests.txt

[ERROR] ingest tests failed

═══════════════════════════════════════════════════════════════════════
Test Summary
═══════════════════════════════════════════════════════════════════════

[ERROR] Failed services: ingest
[✓] Passed services: authz search agent

Review output above for pytest filters to rerun failed tests
# Menu stays open, summary shown
```

## Implementation Details

### Test Execution Flow

1. **test.sh** runs test command via SSH
2. **pytest** executes with plugin enabled
3. **Plugin** captures failed test node IDs
4. **Plugin** generates rerun filter in terminal summary
5. **test.sh** returns 1 (not exit 1) on failure
6. **test.sh** continues to next service or shows summary
7. **Menu** stays open for user to take action

### Failed Test Tracking

The plugin hooks into pytest's reporting system:
- `pytest_runtest_logreport()` - Captures failed tests
- `pytest_terminal_summary()` - Displays rerun filter
- Uses `report.nodeid` for full test path

### Service Test Tracking

The "all" test command now tracks which services failed:
```bash
local failed_services=()
for svc in authz ingest search agent; do
    if ! run_container_tests "$svc" "$env"; then
        failed_services+=("$svc")
    fi
done

# Show summary
if [[ ${#failed_services[@]} -eq 0 ]]; then
    success "All service tests passed!"
else
    error "Failed services: ${failed_services[*]}"
    return 1
fi
```

## Files Modified

### Core Changes
- `scripts/make/test.sh` - Updated authz, ingest, search, agent test sections
- `srv/shared/testing/pytest_failed_filter.py` - NEW pytest plugin

### Test Configuration
- `srv/ingest/tests/conftest.py` - Added plugin
- `srv/search/tests/conftest.py` - Added plugin
- `srv/agent/tests/conftest.py` - Added plugin
- `srv/authz/tests/conftest.py` - Added plugin (with fallback)

### Bug Fixes
- `srv/ingest/src/api/routes/files.py` - Fixed metadata query table
- `srv/ingest/tests/test_colpali.py` - Explicitly enable ColPali in test

## Testing

### Verify Non-Blocking Behavior
```bash
# Run all tests - should continue even if one service fails
make test-all INV=inventory/test

# Should see:
# - All services tested
# - Summary with passed/failed services
# - Menu stays open
```

### Verify Filter Generation
```bash
# Run tests that will fail
ssh root@10.96.201.206
cd /srv/ingest && source venv/bin/activate
pytest tests/test_pdf_extraction_simple.py -v

# Should see at end:
# ═══════════════════════════════════════════════════════════════════════
# Failed Test Rerun Filter
# ═══════════════════════════════════════════════════════════════════════
# To rerun X failed test(s):
#   pytest tests/...
```

## Related Documentation

- Test execution: `scripts/make/test.sh`
- Pytest configuration: `srv/*/tests/conftest.py`
- Test utilities: `srv/shared/testing/`
- Bootstrap scripts: `scripts/test/bootstrap-test-credentials.sh`

## Future Improvements

1. **Parallel test execution** - Run multiple services in parallel
2. **Test result caching** - Skip tests that haven't changed
3. **Flaky test detection** - Track intermittent failures
4. **Coverage aggregation** - Combine coverage from all services
5. **Test timing analysis** - Identify slow tests

## Rules Applied

Following `.cursorrules`:
- Scripts in `scripts/` (orchestration from admin workstation)
- Documentation in `docs/development/session-notes/`
- Used kebab-case for filenames
- Included comprehensive headers in scripts
- No breaking changes to existing functionality
