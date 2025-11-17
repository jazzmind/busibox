# Search API Tests

Comprehensive test suite for the Busibox Search API.

## Test Structure

```
tests/
├── conftest.py              # Pytest fixtures and configuration
├── pytest.ini               # Pytest settings
├── run_tests.sh            # Test runner script
├── unit/                   # Unit tests
│   ├── test_milvus_search.py
│   ├── test_highlighter.py
│   ├── test_reranker.py
│   └── ...
└── integration/            # Integration tests
    └── test_search_api.py
```

## Setup

### 1. Create Virtual Environment

```bash
cd srv/search
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

```bash
# Install application dependencies
pip install -r requirements.txt

# Install test dependencies
pip install pytest pytest-asyncio pytest-mock pytest-cov
```

## Running Tests

### Run All Tests

```bash
bash tests/run_tests.sh
```

### Run Specific Test Categories

```bash
# Unit tests only
bash tests/run_tests.sh --unit

# Integration tests only
bash tests/run_tests.sh --integration

# With coverage report
bash tests/run_tests.sh --coverage

# Fast (skip slow tests)
bash tests/run_tests.sh --fast
```

### Run Specific Tests

```bash
# Run single test file
pytest tests/unit/test_highlighter.py -v

# Run single test class
pytest tests/unit/test_highlighter.py::TestHighlightingService -v

# Run single test method
pytest tests/unit/test_highlighter.py::TestHighlightingService::test_highlight_exact_match -v

# Run by marker
pytest -m unit  # All unit tests
pytest -m integration  # All integration tests
pytest -m "not slow"  # Skip slow tests
```

## Test Coverage

The test suite includes:

### Unit Tests

- **test_milvus_search.py**: Milvus search operations
  - Keyword search (BM25)
  - Semantic search (dense vectors)
  - Hybrid search with RRF fusion
  - Document retrieval
  - Health checks

- **test_highlighter.py**: Search term highlighting
  - Exact match highlighting
  - Stemming and fuzzy matching
  - Multiple term highlighting
  - Fragment extraction
  - HTML markup generation

- **test_reranker.py**: Cross-encoder reranking
  - Score computation
  - Result sorting
  - Top-K selection
  - Score explanation

### Integration Tests

- **test_search_api.py**: Full API endpoints
  - Hybrid search flow
  - Keyword and semantic modes
  - Filtering by file IDs
  - Authentication
  - Health checks
  - Complete search pipeline

## Test Features

### Fixtures

Available fixtures (defined in `conftest.py`):

- `mock_config`: Mock configuration
- `sample_query`: Sample search query
- `sample_user_id`: Sample user ID
- `sample_document_text`: Sample document
- `sample_embedding`: Sample 1536-dim embedding
- `sample_search_results`: Mock Milvus results
- `mock_milvus_service`: Mock Milvus service
- `mock_embedder`: Mock embedding service
- `mock_reranker`: Mock reranking service
- `mock_highlighter`: Mock highlighting service
- `mock_alignment_service`: Mock semantic alignment
- `test_client`: FastAPI test client

### Markers

Use pytest markers to organize tests:

- `@pytest.mark.unit`: Unit tests
- `@pytest.mark.integration`: Integration tests
- `@pytest.mark.slow`: Slow-running tests
- `@pytest.mark.requires_milvus`: Needs Milvus running
- `@pytest.mark.requires_services`: Needs all services

## Coverage Report

Generate HTML coverage report:

```bash
pytest --cov=src --cov-report=html tests/
```

View report:

```bash
open htmlcov/index.html
```

## Continuous Integration

### GitHub Actions

```yaml
- name: Run tests
  run: |
    cd srv/search
    python -m pytest tests/ --cov=src
```

### Pre-commit Hook

```bash
# .git/hooks/pre-commit
#!/bin/bash
cd srv/search
pytest tests/unit/ -v || exit 1
```

## Writing New Tests

### Unit Test Template

```python
import pytest
from services.my_service import MyService

@pytest.mark.unit
class TestMyService:
    def test_feature(self, mock_config):
        service = MyService(mock_config)
        result = service.do_something()
        assert result == expected
```

### Integration Test Template

```python
import pytest
from unittest.mock import patch

@pytest.mark.integration
def test_api_endpoint(test_client):
    response = test_client.post(
        "/endpoint",
        json={"key": "value"},
        headers={"X-User-Id": "user-123"},
    )
    assert response.status_code == 200
```

## Troubleshooting

### Import Errors

Make sure PYTHONPATH includes src directory:

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
pytest tests/
```

### Async Tests Failing

Ensure pytest-asyncio is installed:

```bash
pip install pytest-asyncio
```

### Mock Issues

Install pytest-mock:

```bash
pip install pytest-mock
```

## Test Metrics

Target metrics:

- **Code Coverage**: > 80%
- **Unit Tests**: Fast (< 1s per test)
- **Integration Tests**: Moderate (< 5s per test)
- **All Tests**: < 1 minute total

## Best Practices

1. **Isolation**: Each test should be independent
2. **Mocking**: Mock external dependencies (Milvus, PostgreSQL, etc.)
3. **Naming**: Use descriptive test names
4. **Assertions**: One clear assertion per test
5. **Fixtures**: Reuse common setup via fixtures
6. **Markers**: Tag tests appropriately

## References

- [Pytest Documentation](https://docs.pytest.org/)
- [Pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [FastAPI Testing](https://fastapi.tiangolo.com/tutorial/testing/)

