# Ingest Test Runner Reference

## Overview

The `ingest-test` command provides a convenient way to run tests on the ingest service in production or test environments.

## Location

- **Script**: `/usr/local/bin/ingest-test`
- **Tests**: `/srv/ingest/tests/`
- **Config**: `/srv/ingest/pytest.ini`

## Usage

### Basic Commands

```bash
# Run all tests
ingest-test

# Run chunker tests only
ingest-test chunker

# Run with coverage report
ingest-test coverage

# Run with short traceback (quick)
ingest-test quick
```

### Advanced Usage

```bash
# Run specific test file
ingest-test tests/test_chunker.py

# Run specific test class
ingest-test tests/test_chunker.py::TestMilvusLimit

# Run specific test method
ingest-test tests/test_chunker.py::TestMilvusLimit::test_very_long_paragraph

# Run with verbose output
ingest-test -vv

# Run and stop on first failure
ingest-test -x

# Run only failed tests from last run
ingest-test --lf

# Run tests matching pattern
ingest-test -k "milvus"
```

## Test Modes

### 1. Standard Mode (default)

```bash
ingest-test
```

**Output**: Verbose test results with short traceback

**Use when**: Running all tests for validation

### 2. Chunker Mode

```bash
ingest-test chunker
```

**Output**: Only chunker tests (fast, ~5 seconds)

**Use when**: 
- Validating chunking fixes
- After updating chunker.py
- Quick smoke test

### 3. Coverage Mode

```bash
ingest-test coverage
```

**Output**: 
- Test results
- Coverage percentage
- HTML report in `/srv/ingest/htmlcov/`

**Use when**:
- Measuring test coverage
- Identifying untested code
- Generating coverage reports

### 4. Quick Mode

```bash
ingest-test quick
```

**Output**: Minimal traceback, fast execution

**Use when**:
- Quick validation
- CI/CD pipelines
- Smoke testing

## Test Organization

### Test Files

```
/srv/ingest/tests/
├── test_chunker.py          # Chunking tests (15+ classes)
├── test_embedder.py          # Embedding tests (future)
├── test_text_extractor.py   # Extraction tests (future)
└── test_integration.py       # End-to-end tests (future)
```

### Test Classes in test_chunker.py

1. **TestBasicChunking**: Basic functionality
2. **TestHeadingDetection**: Heading parsing
3. **TestListHandling**: List detection
4. **TestTokenLimits**: Token enforcement
5. **TestMilvusLimit**: Character limit (CRITICAL)
6. **TestChunkOverlap**: Overlap validation
7. **TestMarkdownConversion**: Structure preservation
8. **TestEdgeCases**: Edge cases
9. **TestRealWorldDocuments**: Real document structures
10. **TestPerformance**: Large document handling

## Configuration

### pytest.ini

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = 
    -v
    --tb=short
    --strict-markers
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    integration: marks tests as integration tests
```

### Customizing Test Runs

```bash
# Skip slow tests
ingest-test -m "not slow"

# Run only integration tests
ingest-test -m integration

# Run with full traceback
ingest-test --tb=long

# Run with no output capture (see print statements)
ingest-test -s
```

## Common Workflows

### After Deployment

```bash
# 1. SSH to ingest container
ssh root@10.96.200.30

# 2. Run chunker tests to validate deployment
ingest-test chunker

# 3. If all pass, run full suite
ingest-test

# 4. Generate coverage report
ingest-test coverage
```

### Debugging Test Failures

```bash
# 1. Run failed test with full output
ingest-test tests/test_chunker.py::TestMilvusLimit::test_very_long_paragraph -vv -s

# 2. Check test logs
journalctl -u ingest-worker -n 100 --no-pager

# 3. Verify dependencies
cd /srv/ingest && source venv/bin/activate
python -c "import spacy; print(spacy.__version__)"
python -c "import pytest; print(pytest.__version__)"
```

### Performance Testing

```bash
# Run performance tests
ingest-test tests/test_chunker.py::TestPerformance -v

# Time test execution
time ingest-test chunker

# Profile test execution
ingest-test --profile
```

## Expected Results

### Successful Test Run

```
========================= test session starts =========================
platform linux -- Python 3.11.x, pytest-7.4.x
collected 42 items

tests/test_chunker.py::TestBasicChunking::test_simple_paragraphs PASSED
tests/test_chunker.py::TestBasicChunking::test_single_paragraph PASSED
tests/test_chunker.py::TestBasicChunking::test_empty_text PASSED
...
tests/test_chunker.py::TestMilvusLimit::test_very_long_paragraph PASSED
tests/test_chunker.py::TestMilvusLimit::test_multiple_long_paragraphs PASSED
...

========================= 42 passed in 5.23s ==========================
```

### Failed Test Run

```
========================= test session starts =========================
...
tests/test_chunker.py::TestMilvusLimit::test_very_long_paragraph FAILED

=========================== FAILURES ===================================
_____________ TestMilvusLimit.test_very_long_paragraph _________________

    def test_very_long_paragraph(self, chunker):
        long_text = "This is a very long sentence. " * 3000
        chunks = chunker.chunk(long_text)
        
        for chunk in chunks:
>           assert len(chunk.text) <= 65535
E           AssertionError: assert 70000 <= 65535

tests/test_chunker.py:123: AssertionError
========================= 1 failed, 41 passed in 5.45s =================
```

## Troubleshooting

### Issue: Import Errors

**Symptom**: `ModuleNotFoundError: No module named 'processors'`

**Solution**:
```bash
# Ensure you're in the correct directory
cd /srv/ingest

# Verify PYTHONPATH
export PYTHONPATH=/srv/ingest:$PYTHONPATH

# Or run via the script which handles this
ingest-test
```

### Issue: spaCy Model Not Found

**Symptom**: `OSError: [E050] Can't find model 'en_core_web_sm'`

**Solution**:
```bash
source /srv/ingest/venv/bin/activate
python -m spacy download en_core_web_sm
```

### Issue: Permission Denied

**Symptom**: `PermissionError: [Errno 13] Permission denied`

**Solution**:
```bash
# Ensure correct ownership
chown -R root:root /srv/ingest/tests/

# Ensure correct permissions
chmod 644 /srv/ingest/tests/*.py
```

## Integration with CI/CD

### Example: Run Tests in Pipeline

```bash
#!/bin/bash
# deploy-and-test.sh

# Deploy ingest service
ansible-playbook -i inventory/production/hosts.yml site.yml --tags ingest

# Wait for service to start
sleep 5

# Run tests on production
ssh root@10.96.200.30 'ingest-test quick'

# Check exit code
if [ $? -eq 0 ]; then
    echo "✓ Tests passed - deployment successful"
    exit 0
else
    echo "✗ Tests failed - consider rollback"
    exit 1
fi
```

## Coverage Reports

### Viewing Coverage

```bash
# Generate coverage report
ingest-test coverage

# Coverage report location
ls -lh /srv/ingest/htmlcov/index.html

# Copy to local machine for viewing
scp -r root@10.96.200.30:/srv/ingest/htmlcov ./ingest-coverage
open ingest-coverage/index.html
```

### Coverage Metrics

**Target Coverage**:
- Overall: > 80%
- Critical paths (chunker, embedder): > 90%
- Edge cases: > 70%

**Current Coverage** (after chunker tests):
- chunker.py: ~85%
- Overall: ~45% (other modules not yet tested)

## Best Practices

1. **Always run tests after deployment**
   ```bash
   ingest-test chunker
   ```

2. **Generate coverage reports weekly**
   ```bash
   ingest-test coverage
   ```

3. **Run full suite before major releases**
   ```bash
   ingest-test
   ```

4. **Use quick mode for rapid iteration**
   ```bash
   ingest-test quick
   ```

5. **Debug with verbose output**
   ```bash
   ingest-test -vv -s
   ```

## Related Documentation

- **Chunking Implementation**: `/CHUNKING_IMPLEMENTATION.md`
- **Deployment Guide**: `/DEPLOY_CHUNKING_FIXES.md`
- **Testing Strategy**: `/TESTING.md`

## Quick Reference Card

```
Command                     Description
--------------------------  ----------------------------------
ingest-test                 Run all tests
ingest-test chunker         Run chunker tests only
ingest-test coverage        Run with coverage report
ingest-test quick           Run with short traceback
ingest-test -k "milvus"     Run tests matching "milvus"
ingest-test -x              Stop on first failure
ingest-test --lf            Run last failed tests
ingest-test -vv             Very verbose output
ingest-test -s              Show print statements
ingest-test -m "not slow"   Skip slow tests
```

