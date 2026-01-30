# Ingestion Service Tests

Comprehensive test suite for the document ingestion service, including multi-flow processing and ColPali visual embeddings.

## Quick Start

```bash
# Navigate to ingest directory
cd srv/ingest

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_multi_flow.py -v
pytest tests/test_colpali.py -v

# Run with markers
pytest tests/ -v -m integration
pytest tests/ -v -m slow
```

## Test Suites

### Multi-Flow Processing Tests

**File:** `test_multi_flow.py`

Tests the parallel document processing system with 3 strategies:
- SIMPLE: Fast baseline extraction
- MARKER: Enhanced PDF processing
- COLPALI: Visual embeddings

**Coverage:**
- 40+ test cases
- Strategy selection logic
- Parallel processing
- Result comparison
- Best strategy selection
- Integration tests

**Run:**
```bash
pytest tests/test_multi_flow.py -v

# Specific test classes
pytest tests/test_multi_flow.py::TestProcessingStrategy -v
pytest tests/test_multi_flow.py::TestStrategySelector -v
pytest tests/test_multi_flow.py::TestMultiFlowProcessor -v

# Integration tests only
pytest tests/test_multi_flow.py -v -m integration

# Diagnostic report
python tests/test_multi_flow.py
```

### ColPali Tests

**File:** `test_colpali.py`

Tests the ColPali visual embedding service for semantic image search.

**Coverage:**
- 30+ test cases
- Service availability
- Image encoding/processing
- Embedding generation
- API compatibility
- Error handling
- Performance benchmarks

**Run:**
```bash
pytest tests/test_colpali.py -v

# Specific test classes
pytest tests/test_colpali.py::TestServiceAvailability -v
pytest tests/test_colpali.py::TestEmbeddingGeneration -v
pytest tests/test_colpali.py::TestPerformance -v

# Performance benchmarks
pytest tests/test_colpali.py::TestPerformance -v -m slow

# Diagnostic report
python tests/test_colpali.py
```

### Other Test Suites

- `test_chunker.py` - Text chunking tests
- `test_llm_cleanup.py` - LLM text cleanup tests
- `integration/` - End-to-end integration tests

## Test Markers

Tests are organized with pytest markers:

```bash
# Integration tests (may require services running)
pytest -v -m integration

# Slow tests (performance benchmarks)
pytest -v -m slow

# Skip slow tests
pytest -v -m "not slow"
```

## Environment Variables

Some tests require services to be available:

```bash
# ColPali service
export COLPALI_BASE_URL=http://10.96.200.31:8002/v1
export COLPALI_API_KEY=EMPTY
export COLPALI_ENABLED=true

# LiteLLM service
export LITELLM_BASE_URL=http://10.96.200.30:4000

# For test environment
export COLPALI_BASE_URL=http://10.96.201.208:8002/v1
```

## Running Integration Tests

Integration tests require services to be running:

### Prerequisites

1. **ColPali Service** (for ColPali tests):
   ```bash
   # Check health
   curl http://10.96.200.31:8002/health
   
   # If not running, deploy
   cd provision/ansible
   make colpali ENV=production
   ```

2. **LiteLLM Service** (for embedding tests):
   ```bash
   # Check health
   curl http://10.96.200.30:4000/health
   ```

3. **Other Services** (for full pipeline tests):
   - PostgreSQL
   - Milvus
   - MinIO
   - Redis

### Run Integration Tests

```bash
# With all services running
pytest tests/ -v -m integration

# Specific integration tests
pytest tests/test_multi_flow.py -v -m integration
pytest tests/integration/ -v
```

## Performance Testing

### ColPali Performance

```bash
# Run ColPali benchmarks
pytest tests/test_colpali.py::TestPerformance -v -s

# Expected metrics:
# - Single image: 0.5-2.0s
# - Batch (4): 1.5-4.0s
# - Memory: Stable over iterations
```

### Multi-Flow Performance

```bash
# Run multi-flow integration tests
pytest tests/test_multi_flow.py::TestMultiFlowIntegration -v -s

# Expected metrics:
# - SIMPLE: 1-2s
# - MARKER: 10-30s
# - COLPALI: 20-50s
# - Parallel: ~30-50s (limited by slowest)
```

## Diagnostic Reports

Generate detailed diagnostic reports:

```bash
# Multi-flow diagnostic
python tests/test_multi_flow.py

# ColPali diagnostic
python tests/test_colpali.py
```

Output includes:
- Configuration status
- Service health checks
- Test embedding generation
- Recommendations for issues

## Troubleshooting

### ColPali Tests Fail

**Issue:** `ColPali service not available`

**Solution:**
```bash
# Check service health
bash scripts/test-colpali.sh test

# View logs
ssh root@10.96.200.31
journalctl -u colpali -n 50 --no-pager

# Restart service
systemctl restart colpali
```

### Integration Tests Fail

**Issue:** Services not running

**Solution:**
```bash
# Check all services
bash scripts/test-infrastructure.sh

# Deploy services if needed
cd provision/ansible
make test  # or make production
```

### Import Errors

**Issue:** `ModuleNotFoundError`

**Solution:**
```bash
# Ensure you're in the right directory
cd srv/ingest

# Install dependencies
pip install -r requirements.txt

# Run from ingest directory
pytest tests/ -v
```

## Test Structure

```
tests/
├── README.md                   # This file
├── __init__.py
├── conftest.py                 # Shared fixtures
├── fixtures/                   # Test data
│   └── sample_files/
├── test_chunker.py             # Chunking tests
├── test_llm_cleanup.py         # LLM cleanup tests
├── test_multi_flow.py          # Multi-flow processing tests
├── test_colpali.py             # ColPali tests
├── api/                        # API tests
│   ├── test_health.py
│   ├── test_upload.py
│   └── ...
├── integration/                # Integration tests
│   ├── test_pipeline.py
│   ├── test_connectivity.py
│   └── ...
└── worker/                     # Worker tests
```

## Writing New Tests

### Test Template

```python
import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

class TestMyFeature:
    """Test my feature."""
    
    @pytest.fixture
    def my_fixture(self):
        """Create test data."""
        return {"key": "value"}
    
    def test_basic_functionality(self, my_fixture):
        """Test basic functionality."""
        assert my_fixture["key"] == "value"
        print("\n✓ Test passed")
    
    @pytest.mark.integration
    async def test_integration(self, my_fixture):
        """Test with services."""
        # Async test example
        result = await some_async_function()
        assert result is not None
```

### Markers

```python
@pytest.mark.integration  # Requires services
@pytest.mark.slow         # Takes >5 seconds
@pytest.mark.asyncio      # Async test
```

## Coverage

Generate coverage reports:

```bash
# Run with coverage
pytest tests/ --cov=src --cov-report=html

# View report
open htmlcov/index.html

# Coverage by module
pytest tests/ --cov=src --cov-report=term-missing
```

## CI/CD

Tests are run in CI/CD pipeline:

```yaml
# .github/workflows/test.yml (example)
- name: Run tests
  run: |
    cd srv/ingest
    pytest tests/ -v --cov=src
```

## References

- **Multi-flow guide:** `docs/guides/multi-flow-processing.md`
- **ColPali guide:** `docs/guides/colpali-testing.md`
- **Implementation:** `MULTI-FLOW-IMPLEMENTATION.md`
- **pytest docs:** https://docs.pytest.org/

## Quick Reference

```bash
# Run all tests
pytest tests/ -v

# Run specific file
pytest tests/test_colpali.py -v

# Run specific class
pytest tests/test_multi_flow.py::TestProcessingStrategy -v

# Run specific test
pytest tests/test_colpali.py::TestServiceAvailability::test_health_check_endpoint -v

# Skip slow tests
pytest tests/ -v -m "not slow"

# Run with output
pytest tests/ -v -s

# Parallel execution (if pytest-xdist installed)
pytest tests/ -v -n 4

# Stop on first failure
pytest tests/ -v -x

# Show local variables on failure
pytest tests/ -v -l
```

