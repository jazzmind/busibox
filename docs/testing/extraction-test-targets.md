# Extraction Test Targets

**Created**: 2024-11-18  
**Status**: Active  
**Category**: testing

## Overview

Makefile targets for testing different PDF extraction strategies on the ingest service.

## Available Test Targets

### Basic Extraction Tests

#### `test-extraction-simple`
**Description**: Test SIMPLE extraction strategy (pdfplumber only)  
**Location**: ingest-lxc container  
**Requirements**: None (lightweight)  
**Expected Result**: ✅ All documents should pass

```bash
cd provision/ansible
make test-extraction-simple INV=inventory/test
```

**What it tests:**
- Basic PDF text extraction using pdfplumber
- No ML models, no external services
- Baseline for comparing other strategies

---

#### `test-extraction-llm`
**Description**: Test SIMPLE extraction + LLM cleanup  
**Location**: ingest-lxc container  
**Requirements**: LiteLLM service on agent-lxc  
**Expected Result**: ✅ Improved text quality vs SIMPLE

```bash
cd provision/ansible
make test-extraction-llm INV=inventory/test
```

**What it tests:**
- SIMPLE extraction followed by LLM text cleanup
- Fixes formatting, removes artifacts
- Improves readability of extracted text

---

### Advanced Extraction Tests

#### `test-extraction-marker`
**Description**: Test Marker PDF extraction  
**Location**: ingest-lxc container  
**Requirements**: 
- 3.2GB model cache
- High memory/CPU resources
- **Should only run on server (not local dev machine)**

**Expected Result**: 
- ✅ Should pass on properly provisioned server
- ⚠️  May fail on resource-constrained systems

```bash
cd provision/ansible
make test-extraction-marker INV=inventory/test
```

**What it tests:**
- Advanced PDF extraction with Marker
- Layout analysis, OCR, table detection
- Better handling of complex document layouts

**Known Issues:**
- Fails with `[Errno 32] Broken pipe` on low-resource systems
- Complex documents (presentations, brochures) are resource-intensive
- Subprocess may be killed by OS under memory pressure

**Document Types:**
- ✅ Works well: Academic papers, patents, simple documents
- ⚠️ May fail: Presentations, datasheets, financial statements, brochures

---

#### `test-extraction-colpali`
**Description**: Test ColPali visual embeddings extraction  
**Location**: ingest-lxc container  
**Requirements**: 
- ColPali service running on vllm-lxc
- GPU-enabled container
- Visual embedding model loaded

**Expected Result**: ✅ Visual embeddings generated

```bash
cd provision/ansible
make test-extraction-colpali INV=inventory/test
```

**What it tests:**
- Visual document understanding
- Page-level embeddings for image-rich documents
- Multimodal search capabilities

**When to use:**
- Documents with important visual elements
- Diagrams, charts, infographics
- Layout-dependent content

---

## Usage Examples

### Running on Test Environment

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Test baseline SIMPLE extraction
make test-extraction-simple INV=inventory/test

# Test with LLM cleanup
make test-extraction-llm INV=inventory/test

# Test Marker (server only)
make test-extraction-marker INV=inventory/test

# Test ColPali (requires GPU service)
make test-extraction-colpali INV=inventory/test
```

### Running on Production

```bash
cd provision/ansible

# Same commands, different inventory
make test-extraction-simple INV=inventory/production
make test-extraction-llm INV=inventory/production
make test-extraction-marker INV=inventory/production
make test-extraction-colpali INV=inventory/production
```

### Running Directly on Container

If you need more control or debugging:

```bash
# SSH into ingest container
ssh root@10.96.200.206  # test environment
ssh root@10.96.200.29   # production environment

# Activate environment and run tests
cd /srv/ingest
source venv/bin/activate

# Run specific test file
python -m pytest tests/test_pdf_extraction_simple.py -v --tb=short
python -m pytest tests/test_pdf_extraction_llm.py -v --tb=short
python -m pytest tests/test_pdf_extraction_marker.py -v --tb=short
python -m pytest tests/test_colpali_extraction.py -v --tb=short

# Run with specific test
python -m pytest tests/test_pdf_extraction_simple.py::test_doc01 -v

# Run with coverage
python -m pytest tests/test_pdf_extraction_simple.py --cov=src --cov-report=html
```

## Test Document Set

All extraction tests use the same 10 test documents in `tests/fixtures/`:

1. `doc01_rfp_project_management.pdf` - 2 pages, simple text
2. `doc02_polymer_nanocapsules_patent.pdf` - 10 pages, technical
3. `doc03_chartparser_paper.pdf` - 5 pages, academic
4. `doc04_zero_shot_reasoners.pdf` - 42 pages, research paper
5. `doc05_rslzva1_datasheet.pdf` - Technical datasheet
6. `doc06_urgent_care_whitepaper.pdf` - Healthcare document
7. `doc07_nasa_composite_boom.pdf` - Engineering document
8. `doc08_us_bancorp_q4_2023_presentation.pdf` - Financial presentation
9. `doc09_visit_phoenix_destination_brochure.pdf` - Marketing brochure
10. `doc10_nestle_2022_financial_statements.pdf` - Financial statements

## Test Results Location

Results are saved to JSON files on the ingest container:

```bash
/srv/ingest/tests/extraction_results_simple.json
/srv/ingest/tests/extraction_results_llm.json
/srv/ingest/tests/extraction_results_marker.json
/srv/ingest/tests/extraction_results_colpali.json
```

## Comparison and Evaluation

After running tests, compare results:

```bash
# On ingest container
cd /srv/ingest
python tests/compare_extraction_strategies.py

# Generates comparison report:
# - Character counts per strategy
# - Quality metrics
# - Processing times
# - Failure analysis
```

## Testing Strategy by Environment

### Local Development Machine
```
Strategy       | Test?  | Notes
-------------- | ------ | -----
SIMPLE         | ✅ YES | Lightweight, always works
LLM Cleanup    | ✅ YES | If LiteLLM accessible
Marker         | ❌ NO  | Too resource intensive
ColPali        | ❌ NO  | Requires GPU
```

### Test Environment (ingest-lxc)
```
Strategy       | Test?  | Notes
-------------- | ------ | -----
SIMPLE         | ✅ YES | Baseline
LLM Cleanup    | ✅ YES | With LiteLLM service
Marker         | ⚠️ TRY | May work if resources sufficient
ColPali        | ✅ YES | If GPU service deployed
```

### Production Environment
```
Strategy       | Test?  | Notes
-------------- | ------ | -----
SIMPLE         | ✅ YES | Always test baseline
LLM Cleanup    | ✅ YES | Primary production strategy
Marker         | ✅ YES | For complex documents
ColPali        | ✅ YES | For visual documents
```

## Troubleshooting

### Test Collection Errors

If tests fail to collect due to import errors:
1. Ensure dependencies are installed: `pip install -r requirements.txt`
2. Check Python path: `export PYTHONPATH=/srv/ingest:$PYTHONPATH`
3. Verify test files exist in `/srv/ingest/tests/`

### Marker Failures

If Marker tests fail with "Broken pipe":
1. Check available memory: `free -h`
2. Check CPU load: `top`
3. Try with simpler documents first
4. Consider increasing container resources
5. This is expected on low-resource systems

### ColPali Failures

If ColPali tests fail:
1. Check ColPali service is running: `curl http://10.96.200.210:8000/health`
2. Verify GPU is available in vllm container
3. Check model is loaded
4. Review ColPali logs: `ssh root@10.96.200.210 journalctl -u colpali`

## Related Documentation

- [Test Status Summary](TEST_STATUS_SUMMARY.md) - Current test results
- [Test Strategy](TEST_STRATEGY.md) - Overall testing approach
- [Master Test Guide](master-guide.md) - Complete testing documentation
- [Makefile Test Targets](makefile-test-targets.md) - All available test targets

## Next Steps

1. ✅ Run `test-extraction-simple` to establish baseline
2. ⏭️  Run `test-extraction-llm` to test with cleanup
3. 📦 Deploy Marker and run `test-extraction-marker`
4. 🎨 Deploy ColPali and run `test-extraction-colpali`
5. 📊 Compare results and document findings

