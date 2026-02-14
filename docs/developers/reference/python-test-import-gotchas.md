---
title: "Python Test Import Gotchas"
category: "developer"
order: 126
description: "Common Python import issues that break pytest test collection"
published: true
---

# Python Test Import Gotchas

## Overview

This document describes common Python import issues that can break pytest test collection, particularly when running the full test suite.

## Critical Rule: Never Modify sys.modules at Module Level

**NEVER do this in test files:**

```python
# ❌ BAD - Corrupts sys.modules for ALL subsequent tests
import sys
sys.modules['redis'] = type(sys)('redis')
sys.modules['minio'] = type(sys)('minio')

from my_module import MyClass  # MyClass imports redis.asyncio
```

**Why this breaks things:**

1. Pytest collects tests in alphabetical order
2. Module-level code runs during collection (not test execution)
3. If `test_aaa.py` sets `sys.modules['redis']` to a fake module...
4. Then `test_bbb.py` tries to `import redis.asyncio`...
5. Python finds the fake `redis` module (no `asyncio` attribute)
6. Error: `ModuleNotFoundError: No module named 'redis.asyncio'; 'redis' is not a package`

## Safe Alternatives

### Option 1: Use pytest-mock fixtures (preferred)

```python
# ✅ GOOD - Uses pytest fixtures for cleanup
import pytest

@pytest.fixture(autouse=True)
def mock_heavy_dependencies(monkeypatch):
    """Mock only during test execution, with automatic cleanup."""
    from unittest.mock import MagicMock
    import sys
    
    # Only mock if not already loaded
    if 'heavy_module' not in sys.modules:
        monkeypatch.setitem(sys.modules, 'heavy_module', MagicMock())
```

### Option 2: Mock inside test functions

```python
# ✅ GOOD - Scoped to single test
from unittest.mock import patch, MagicMock

def test_something():
    with patch.dict('sys.modules', {'heavy_module': MagicMock()}):
        from my_module import MyClass
        # test code
```

### Option 3: Don't mock at all

Often, the module you think you need to mock doesn't actually get imported:

```python
# Before assuming you need mocks, check the import chain:
# Does TextExtractor actually import redis? 
# Check with: grep "import redis" src/processors/text_extractor.py

# If not, just import directly:
from processors.text_extractor import TextExtractor  # ✅ No mocking needed
```

## Debugging Import Errors

### Symptom: "X is not a package"

```
ModuleNotFoundError: No module named 'redis.asyncio'; 'redis' is not a package
```

This means `sys.modules['redis']` contains something that isn't the real redis package.

### Investigation Steps

1. **Binary search to find the culprit file:**
   ```bash
   # Does api/ work alone?
   pytest tests/api/ --collect-only
   
   # Does it work with one root-level test?
   pytest tests/test_chunker.py tests/api/ --collect-only
   
   # Keep adding files until it breaks
   ```

2. **Search for sys.modules manipulation:**
   ```bash
   grep -r "sys\.modules\[" tests/
   ```

3. **Check for files shadowing packages:**
   ```bash
   # A file named redis.py would shadow the redis package
   find tests/ src/ -name "redis.py" -o -name "minio.py"
   ```

## Related Issues We've Encountered

### 1. Stale files shadowing packages (2025-12-21)

Old files left on disk (`src/api/services/redis.py`) shadowed the installed `redis` package.

**Fix:** Use Ansible `synchronize` with `delete: yes` to remove stale files.

### 2. sys.modules pollution from test file (2025-12-21)

`test_pdf_extraction_simple.py` set `sys.modules['redis']` at module level.

**Fix:** Remove unnecessary mocking (the tested module didn't use redis).

See: [Session Notes: 2025-12-21 Redis Import Error](../../archive/session-notes/2025-12-21-ingest-test-redis-import-error.md)

## Checklist for New Test Files

- [ ] No `sys.modules` manipulation at module level
- [ ] All mocking done via fixtures or context managers
- [ ] Verified that imports actually need mocking
- [ ] Test file runs successfully alone
- [ ] Test file runs successfully with the full suite

## When You Need to Mock Heavy Dependencies

If you genuinely need to mock heavy dependencies (GPU, network, etc.):

```python
import pytest
from unittest.mock import MagicMock

@pytest.fixture(scope="module")
def mock_gpu_dependencies():
    """
    Mock GPU-heavy dependencies for faster testing.
    
    Uses module scope so import only happens once per module.
    """
    import sys
    mocks = {}
    
    # Only mock what's not already loaded
    for mod_name in ['torch', 'transformers', 'sentence_transformers']:
        if mod_name not in sys.modules:
            mock = MagicMock()
            sys.modules[mod_name] = mock
            mocks[mod_name] = mock
    
    yield mocks
    
    # Cleanup - remove our mocks (but not if they were already there)
    for mod_name in mocks:
        if mod_name in sys.modules and sys.modules[mod_name] is mocks[mod_name]:
            del sys.modules[mod_name]
```

**Important:** This fixture must be used explicitly, not with `autouse=True`, to avoid affecting other tests.

