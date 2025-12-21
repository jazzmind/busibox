---
created: 2025-12-21
updated: 2025-12-21
status: resolved
category: session-notes
---

# Ingest Test Suite redis.asyncio Import Error - 2025-12-21

## Problem Statement

When running `make test SERVICE=ingest INV=test`, the test suite fails with:

```
ModuleNotFoundError: No module named 'redis.asyncio'; 'redis' is not a package
```

However, the same tests pass when run directly on the container or when run individually.

## ROOT CAUSE FOUND ✅

**File:** `tests/test_pdf_extraction_simple.py`

**Lines 15-18:**
```python
# Mock dependencies that require database/network
sys.modules['asyncpg'] = type(sys)('asyncpg')
sys.modules['pymilvus'] = type(sys)('pymilvus')
sys.modules['redis'] = type(sys)('redis')  # <-- THIS IS THE PROBLEM!
sys.modules['minio'] = type(sys)('minio')
```

### Why This Causes the Error

1. `test_pdf_extraction_simple.py` is collected BEFORE `tests/api/` (alphabetical order: `test_p*` < `api/`)
2. At collection time, Python executes the top-level code in each test file
3. This file inserts a **fake module** into `sys.modules['redis']` - just a bare `ModuleType` object
4. When pytest later collects `tests/api/test_files.py`, it imports `api.routes.files` which does `import redis.asyncio`
5. Python finds `redis` in `sys.modules`, but it's the fake one with no `asyncio` attribute
6. Error: `'redis' is not a package`

### Why Individual Test Files Work

When running individual files or subdirectories, `test_pdf_extraction_simple.py` is not collected, so the mock is never installed.

## The Fix

Refactor `test_pdf_extraction_simple.py` to use `unittest.mock.patch` or `pytest-mock` instead of directly manipulating `sys.modules` at module level.

**Option 1: Use pytest fixtures (preferred)**
```python
@pytest.fixture(autouse=True)
def mock_db_modules(monkeypatch):
    """Mock database modules that aren't needed for extraction tests."""
    import sys
    from unittest.mock import MagicMock
    monkeypatch.setitem(sys.modules, 'asyncpg', MagicMock())
    monkeypatch.setitem(sys.modules, 'pymilvus', MagicMock())
    # Don't mock redis - it has async submodules we need
    monkeypatch.setitem(sys.modules, 'minio', MagicMock())
```

**Option 2: Move mocking inside the test function**
```python
def test_pdf_extraction():
    # Mock only what's needed, only during this test
    with patch.dict('sys.modules', {'asyncpg': MagicMock(), ...}):
        from processors.text_extractor import TextExtractor
        # test code
```

**Option 3: Don't mock redis at all**
Since the test just does PDF extraction, it might not actually need the redis mock.

## Key Observations

### What Works

1. **Individual test files**: `pytest tests/api/test_health.py` ✅
2. **Subdirectories**: `pytest tests/api/` ✅
3. **Direct Python import**: `python -c 'import redis.asyncio'` ✅
4. **Most root-level tests + api**: `pytest tests/test_chunker.py tests/api/` ✅

### What Fails

1. **Full test suite**: `pytest tests/` ❌
2. **Any test combined with test_pdf_extraction_simple.py**:
   `pytest tests/test_pdf_extraction_simple.py tests/api/` ❌

## Investigation Process

Used binary search to isolate:
1. `tests/api/` alone → works
2. `tests/integration/` alone → works
3. `tests/test_chunker.py tests/api/` → works
4. All `tests/test_*.py tests/api/` → fails
5. Narrowed to `tests/test_pdf_extraction_simple.py` + `tests/api/` → fails

## Session Log

### Attempt 1: Stale Files (False Lead)
- Found old `redis.py` and `minio.py` in src/api/services/
- Removed them, updated Ansible to sync with delete
- Result: Same error - this wasn't the cause

### Attempt 2: Pycache Cleanup (False Lead)
- Added cache cleanup to Ansible
- Result: Same error - pycache wasn't the cause

### Attempt 3: Binary Search (Success!)
- Systematically tested subsets of test files
- Found `test_pdf_extraction_simple.py` as the trigger
- Confirmed root cause: `sys.modules` manipulation at module level

## Lessons Learned

1. **Never modify `sys.modules` at module level in test files** - use fixtures or context managers
2. **Test collection order matters** - alphabetical order affects which files get collected first
3. **Module-level code runs during collection** - pytest executes imports and top-level statements when discovering tests
4. **Binary search is effective** - combine test files systematically to isolate the culprit
5. **Mock carefully** - replacing `sys.modules['redis']` with a bare module breaks submodule imports like `redis.asyncio`

## Related Issues

This is similar to the previous stale file issue where `/srv/ingest/src/api/services/redis.py` was shadowing the redis package. Both involve import system corruption, but:
- Previous: File on disk shadowing package
- This: `sys.modules` entry shadowing package

## Related Documentation

- [Ingest Test Runner Reference](../reference/ingest-test-runner.md)
- [Ingestion Fixes Complete](./ingestion-fixes-complete.md)

