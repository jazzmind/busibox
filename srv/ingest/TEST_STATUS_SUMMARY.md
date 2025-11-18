# Test Status Summary

**Date**: 2024-11-17  
**Status**: Marker models cached, but extraction failing on complex documents

## Current Status

### ✅ Successfully Completed
1. **Marker models downloaded and cached** (3.2GB)
   - Layout model: 1.4GB
   - Text recognition: 1.3GB
   - OCR error detection: 263MB
   - Table recognition: 214MB
   - Text detection: 73MB

2. **Dependencies installed**
   - `fastembed` ✓
   - `spacy` + `en_core_web_sm` ✓
   - `marker-pdf` ✓
   - All other requirements ✓

3. **ColPali tests mostly passing** (9/11 passed)
   - 2 failures due to ColPali service not running (expected)

### ❌ Current Issues

#### 1. Marker Extraction Failures (6/10 documents)
**Failed documents:**
- `doc05_rslzva1_datasheet` - Broken pipe
- `doc06_urgent_care_whitepaper` - Broken pipe  
- `doc07_nasa_composite_boom` - Broken pipe
- `doc08_us_bancorp_q4_2023_presentation` - Broken pipe
- `doc09_visit_phoenix_destination_brochure` - Broken pipe
- `doc10_nestle_2022_financial_statements` - Broken pipe

**Passed documents:**
- `doc01_rfp_project_management` ✓ (2 pages, simple)
- `doc02_polymer_nanocapsules_patent` ✓ (10 pages)
- `doc03_chartparser_paper` ✓ (5 pages)
- `doc04_zero_shot_reasoners` ✓ (42 pages)

**Root cause:**
`[Errno 32] Broken pipe` indicates Marker is crashing during processing. This typically occurs when:
- Documents are complex (presentations, brochures, financial statements)
- Memory constraints cause Marker subprocess to be killed
- Processing timeout
- Resource exhaustion on specific document types

#### 2. Test Collection Errors (12 test modules)
Many tests cannot be collected due to missing service dependencies:
- Integration tests require running services (Postgres, Milvus, Redis, MinIO)
- These are expected to fail in the test environment

## Analysis

### Marker Failure Pattern
Looking at the pattern:
- **Simple documents work**: RFPs, patents, academic papers
- **Complex documents fail**: Datasheets, presentations, brochures, financial statements
- **Failure point**: During `converter(file_path)` call in text_extractor.py

### Likely Issues
1. **Memory**: Marker models are large (3.2GB) and processing complex layouts requires significant RAM
2. **Timeout**: No timeout configured - complex documents may hang
3. **Layout complexity**: Presentations/brochures have complex multi-column layouts that stress Marker
4. **Subprocess crash**: Marker runs in subprocess which may be killed by OS

## Recommendations

### Immediate Actions
1. **Add error handling & fallback**
   - Catch subprocess errors gracefully
   - Fall back to pdfplumber for failed documents
   - Add configurable timeout

2. **Add resource limits**
   - Set memory limits for Marker subprocess
   - Configure processing timeout (e.g., 5 minutes per document)
   - Implement retry logic with exponential backoff

3. **Test with simpler Marker config**
   - Try `disable_ocr=True` for documents with text
   - Use `disable_image_extraction=True` to save memory
   - Set smaller batch sizes

4. **Document-type specific strategies**
   ```python
   if document_type in ["presentation", "brochure", "financial"]:
       # Use simpler extraction or skip Marker
       use_pdfplumber = True
   ```

### Code Changes Needed

```python
# In text_extractor.py _extract_pdf()

try:
    # Add timeout and resource limits
    import signal
    
    def timeout_handler(signum, frame):
        raise TimeoutError("Marker processing timeout")
    
    # Set 5 minute timeout
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(300)  # 5 minutes
    
    try:
        converter = PdfConverter(artifact_dict=artifact_dict)
        result = converter(file_path)
        signal.alarm(0)  # Cancel timeout
        
    except (BrokenPipeError, TimeoutError) as e:
        logger.warning(
            "Marker failed, falling back to pdfplumber",
            error=str(e),
            file_path=file_path
        )
        markdown_text = None
        
except Exception as e:
    logger.error("Marker crashed", error=str(e))
    markdown_text = None
```

### Testing Strategy
1. Run SIMPLE strategy baseline ✓ (already done - 10/10 passed)
2. Fix Marker timeout/fallback issues
3. Re-run Marker tests
4. Compare extraction quality between strategies
5. Document which strategy works best for each document type

## Next Steps

**Priority 1: Get baseline working**
1. Ensure SIMPLE strategy works for all documents ✓
2. Add LLM cleanup tests
3. Run evaluation metrics

**Priority 2: Fix Marker**
1. Add timeout and error handling
2. Implement graceful fallback to pdfplumber
3. Test on failing documents
4. Document document-type recommendations

**Priority 3: ColPali**
1. Deploy ColPali service to test environment
2. Run ColPali extraction tests
3. Compare visual embeddings quality

## Commands to Continue

```bash
# Run SIMPLE tests (should all pass)
cd /Users/wessonnenreich/Code/sonnenreich/busibox/srv/ingest
source test_venv/bin/activate
python tests/test_pdf_extraction_simple.py

# After fixing Marker, re-run
python tests/test_pdf_extraction_marker.py

# Run full test suite (will have expected failures for services)
python -m pytest tests/ -v --tb=short -k "not integration"
```




