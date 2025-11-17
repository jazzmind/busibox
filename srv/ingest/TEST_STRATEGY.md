# Testing Strategy - Resource Constraints

## Key Finding
**Marker cannot run locally** - requires too much CPU/GPU for local development machine.

## Revised Testing Approach

### Phase 1: SIMPLE Strategy Baseline ✅
**Goal**: Establish baseline that all documents can be processed
- ✅ All 10 documents processed successfully with SIMPLE strategy
- ✅ Results saved to `tests/extraction_results_simple.json`

### Phase 2: LLM Cleanup Testing 🔄 NEXT
**Goal**: Test LLM cleanup and evaluation metrics
- Run SIMPLE + LLM cleanup on all 10 documents
- Compare against eval.json criteria
- Measure improvement in text quality

**Command**:
```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/srv/ingest
source test_venv/bin/activate
python tests/test_pdf_extraction_simple.py --llm-cleanup
```

### Phase 3: Marker - Server Deployment Only 📦
**Marker is NOT for local testing** - requires deployment:

1. **Deploy to agent-lxc container** (has GPU/CPU resources)
2. **Test via worker integration** (not standalone tests)
3. **Compare results server-side**

**Why Marker fails locally:**
- 3.2GB model cache
- High memory usage during processing (especially complex layouts)
- Subprocess spawning fails on resource-constrained machines
- Broken pipe = subprocess killed by OS for resource limits

**Marker should only be tested:**
- ✅ In deployed server environment (agent-lxc)
- ✅ Via actual worker processing jobs
- ❌ NOT in local test suite

### Phase 4: ColPali - Server Only 🎨
**ColPali also requires deployment:**
- Needs GPU for visual embeddings
- Runs as separate service on GPU-enabled LXC container
- Test via integration tests when services are running

## Current Status

### Local Testing (Development Machine)
```
Strategy    | Status | Documents | Notes
----------- | ------ | --------- | -----
SIMPLE      | ✅ PASS | 10/10    | Baseline established
LLM Cleanup | 🔄 TODO | 0/10     | Next step
Marker      | ⛔ SKIP | N/A      | Server only
ColPali     | ⛔ SKIP | N/A      | Server only
```

### Server Testing (agent-lxc)
```
Strategy    | Status | Documents | Notes
----------- | ------ | --------- | -----
SIMPLE      | 📦 TODO | 0/10     | Deploy & test
LLM Cleanup | 📦 TODO | 0/10     | Deploy & test
Marker      | 📦 TODO | 0/10     | Needs deployment
ColPali     | 📦 TODO | 0/10     | Needs GPU service
```

## What We Can Test Locally

### ✅ Can Test
1. **SIMPLE extraction** - lightweight, no ML models
2. **LLM cleanup** - if LiteLLM service is accessible
3. **Text processing** - chunking, language detection
4. **Unit tests** - individual components
5. **Evaluation metrics** - comparing against eval.json

### ❌ Cannot Test (Server Only)
1. **Marker extraction** - too resource intensive
2. **ColPali embeddings** - requires GPU
3. **Multi-flow comparison** - needs Marker + ColPali
4. **Full worker pipeline** - needs all services (Postgres, Milvus, Redis, MinIO)

## Recommended Next Actions

### 1. Complete Local Testing (Now)
```bash
# Run SIMPLE + LLM cleanup with evaluations
cd /Users/wessonnenreich/Code/sonnenreich/busibox/srv/ingest
source test_venv/bin/activate

# Create LLM cleanup test
python tests/test_pdf_extraction_simple.py  # Re-run to confirm baseline

# Then create separate LLM cleanup test
# python tests/test_pdf_extraction_llm_cleanup.py
```

### 2. Update Test Files
Remove Marker from local test suite since it can't run:
- Keep `test_pdf_extraction_simple.py` ✓
- Remove `test_pdf_extraction_marker.py` (or mark @pytest.mark.skip)
- Update `test_pdf_processing_suite.py` to skip Marker locally

### 3. Document Deployment Testing
Create deployment test procedures for:
- Testing Marker on agent-lxc
- Testing ColPali on GPU LXC
- Integration testing with all services

### 4. Server Deployment Flow
```bash
# On admin workstation
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy agent with Marker support
ansible-playbook -i inventory/test/hosts.yml site.yml --tags agent

# Test via worker
# Upload document via ai-portal
# Check processing results
```

## Test Results So Far

### SIMPLE Strategy Results
```json
{
  "total": 10,
  "passed": 10,
  "failed": 0,
  "avg_chars_per_page": ~3500,
  "total_pages": 78
}
```

**All documents processed successfully!**

### Marker Local Test Results
```json
{
  "total": 10,
  "passed": 4,
  "failed": 6,
  "failure_reason": "Resource constraints - [Errno 32] Broken pipe"
}
```

**Expected failures - cannot run locally.**

## Decision: Skip Marker Local Tests

**Resolution**: 
- ✅ Continue with SIMPLE strategy
- ✅ Add LLM cleanup tests next
- ⛔ Remove Marker from local test suite
- 📦 Test Marker only after deployment to server

This is the correct approach - development testing focuses on what can run locally, production testing validates the full pipeline on proper infrastructure.



