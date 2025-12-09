# PDF Processing Test Suite

**Created:** 2025-11-17  
**Status:** Complete  
**Purpose:** Comprehensive testing framework for comparing PDF processing strategies

## Overview

This test suite provides a comprehensive framework for testing and comparing different PDF processing strategies (SIMPLE, MARKER, COLPALI) against real-world documents with known evaluation criteria.

## Test Document Collection

### 10 Real-World PDFs Spanning Difficulty Levels

| ID | Document Type | Difficulty | Size | Best Strategy | Key Features |
|----|--------------|------------|------|---------------|--------------|
| doc01 | RFP | Low | 141 KB | SIMPLE | Single-column, numbered lists |
| doc02 | Patent | Medium | 1.2 MB | MARKER | Two-column, nested claims, figures |
| doc03 | Academic Paper (CS) | High | 252 KB | MARKER | Two-column, tables, equations |
| doc04 | Academic Paper (ML) | High | 744 KB | MARKER | Two-column, math notation, algorithms |
| doc05 | Technical Datasheet | Medium | 62 KB | MARKER | Spec tables, diagrams |
| doc06 | Industry White Paper | Medium | 641 KB | MARKER | Charts, statistics |
| doc07 | Technical Conference Paper | High | 3.0 MB | MARKER | Engineering figures, equations |
| doc08 | Investor Presentation | High | 472 KB | COLPALI | Landscape slides, visual-heavy |
| doc09 | Marketing Brochure | High | 3.0 MB | COLPALI | Visual design, image-heavy |
| doc10 | Financial Statements | Very High | 2.2 MB | MARKER | Complex multi-column tables |

### Difficulty Distribution

- **Low:** 1 document (10%)
- **Medium:** 3 documents (30%)
- **High:** 5 documents (50%)
- **Very High:** 1 document (10%)

### Document Type Coverage

- Government/Public Sector: RFP
- Legal: Patent
- Academic: 2 papers (CS, ML)
- Technical: Datasheet, conference paper, white paper
- Financial: Investor presentation, financial statements
- Marketing: Brochure

## Evaluation Criteria

Each document has specific evaluation criteria defined in `eval.json` and `eval.md` files:

### Example Criteria (doc01 - RFP)
- RFP title and issuing agency correctly identified
- All numbered services/tasks preserved in order
- Section headers captured as headings
- Grant/program language captured without corruption
- No loss or duplication from page headers/footers

### Example Criteria (doc10 - Financial Statements)
- Table headers and subheaders captured correctly
- Numeric values aligned to correct line items and years
- Negative values/parentheses preserved
- Note references attached to correct line items
- No table rows truncated or duplicated at page breaks

## Running the Tests

### Quick Start

```bash
# From busibox/srv/ingest directory
bash tests/run_pdf_test_suite.sh
```

### Run Specific Tests

```bash
# Run only document download check
pytest tests/test_pdf_processing_suite.py::TestPDFProcessingSuite::test_all_documents_downloaded -v

# Run extraction tests for specific document
pytest tests/test_pdf_processing_suite.py::TestPDFProcessingSuite::test_simple_extraction[doc01_rfp_project_management] -v

# Run all extraction tests
pytest tests/test_pdf_processing_suite.py::TestPDFProcessingSuite::test_simple_extraction -v

# Generate test report
python tests/test_pdf_processing_suite.py
```

### Test Output

The test runner provides:
- Document-level extraction metrics
- Quality assessment per document
- Difficulty distribution validation
- Document type coverage validation
- Strategy recommendation summary

## Test Implementation

### TestPDFProcessingSuite Class

**Tests included:**
1. `test_all_documents_downloaded` - Verify PDFs are present
2. `test_all_evals_present` - Verify eval files exist
3. `test_strategy_selection` - Test strategy selection logic
4. `test_simple_extraction` - Test SIMPLE strategy on all docs
5. `test_extraction_quality_metrics` - Calculate quality metrics
6. `test_difficulty_distribution` - Validate test coverage
7. `test_document_type_coverage` - Validate document diversity

### TestStrategyComparison Class

**Tests included:**
1. `test_compare_simple_vs_marker` - Compare strategies side-by-side

### Quality Metrics

For each document, tests calculate:
- **text_length** - Total characters extracted
- **page_count** - Number of pages processed
- **avg_chars_per_page** - Average extraction density
- **has_tables** - Whether tables were detected
- **has_images** - Whether images were detected

## Integration with Worker

### Processing Config Application

The worker now applies processing config from the ingestion settings UI:

```python
# LLM Cleanup
if processing_config.get("llm_cleanup_enabled", False):
    chunks = await llm_cleaner.clean(chunks)

# Chunking Parameters
chunk_size_min = processing_config.get("chunk_size_min", 400)
chunk_size_max = processing_config.get("chunk_size_max", 800)
chunk_overlap = processing_config.get("chunk_overlap_pct", 0.12)

# Multi-Flow Processing
if processing_config.get("multi_flow_enabled", False):
    results = multi_flow.process_with_strategies(...)
```

### Worker Integration Points

1. **Stage 4 (Chunking):**
   - Apply custom chunk_size_min/max
   - Apply custom chunk_overlap_pct
   - Temporarily override chunker config

2. **Stage 4.5 (LLM Cleanup):**
   - Check processing_config.llm_cleanup_enabled
   - Override default cleanup enabled flag

3. **Stage 7 (Multi-Flow):**
   - Execute if multi_flow_enabled is true
   - Run SIMPLE, MARKER, COLPALI in parallel
   - Compare results and log metrics

## Expected Strategy Performance

Based on document characteristics:

### SIMPLE Strategy
**Best for:**
- Plain text documents
- Single-column layouts
- Simple structure
- Low difficulty documents

**Expected Success:**
- doc01 (RFP): ✅ Excellent
- doc02 (Patent): ⚠️ Fair (will miss some structure)
- doc05 (Datasheet): ⚠️ Fair (tables may be mangled)

### MARKER Strategy
**Best for:**
- Complex PDFs with tables
- Two-column layouts
- Technical documents
- Medium to high difficulty

**Expected Success:**
- doc02 (Patent): ✅ Excellent
- doc03-04 (Academic Papers): ✅ Excellent
- doc05-07 (Technical Docs): ✅ Excellent
- doc10 (Financial): ✅ Good (complex tables)

### COLPALI Strategy
**Best for:**
- Visual documents
- Presentations
- Image-heavy layouts
- Scanned documents

**Expected Success:**
- doc08 (Presentation): ✅ Excellent
- doc09 (Brochure): ✅ Excellent
- Other docs: ✅ Complementary (visual search)

## Future Enhancements

### Planned Additions

1. **Automated Quality Scoring**
   - Automatic evaluation against criteria
   - Score each strategy per document
   - Generate comparison reports

2. **Ground Truth Comparison**
   - Manual ground truth extraction
   - Automated diff analysis
   - Precision/recall metrics

3. **Performance Benchmarking**
   - Processing time per strategy
   - Memory usage tracking
   - Cost analysis (API calls)

4. **Strategy Selection ML**
   - Train model on document features
   - Predict best strategy automatically
   - Continuous improvement from results

5. **Extended Test Set**
   - Add more document types
   - Include scanned/OCR documents
   - Add non-English documents
   - Include malformed PDFs

## Usage Examples

### Example 1: Test All Documents

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/srv/ingest
bash tests/run_pdf_test_suite.sh
```

### Example 2: Test Specific Document

```python
from tests.test_pdf_processing_suite import TEST_DOCUMENTS
from processors.text_extractor import TextExtractor
from shared.config import Config

config = Config()
extractor = TextExtractor(config.to_dict())

doc = TEST_DOCUMENTS[0]  # doc01_rfp_project_management
pdf_path = f"../../samples/docs/{doc['id']}/source.pdf"

result = extractor.extract(pdf_path, doc['mime_type'])

print(f"Extracted {len(result.text)} chars from {result.page_count} pages")
print(f"Evaluation criteria: {doc['eval_criteria']}")
```

### Example 3: Compare Strategies

```python
from processors.processing_strategy import ProcessingStrategy
from processors.multi_flow_processor import MultiFlowProcessor

processor = MultiFlowProcessor(config)
results = processor.process_with_strategies(
    file_path=pdf_path,
    mime_type="application/pdf",
    file_id="test-001",
    user_id="test-user",
    max_strategies=3,
)

for strategy, result in results.items():
    print(f"{strategy.value}: {len(result.chunks)} chunks in {result.processing_time_seconds}s")
```

## Files

### Test Suite
- `tests/test_pdf_processing_suite.py` - Main test suite
- `tests/run_pdf_test_suite.sh` - Test runner script

### Test Data
- `samples/docs/doc01_*/source.pdf` - Test PDFs
- `samples/docs/doc01_*/eval.json` - Evaluation criteria
- `samples/docs/doc01_*/eval.md` - Human-readable criteria
- `samples/docs/doc01_*/SOURCE_URL.txt` - Original URLs

### Documentation
- `PDF_TEST_SUITE.md` - This file
- `docs/guides/multi-flow-processing.md` - Multi-flow guide
- `MULTI-FLOW-IMPLEMENTATION.md` - Implementation details

## Version History

- **2025-11-17:** Initial test suite with 10 diverse PDFs and comprehensive evaluation criteria

## Related Documentation

- [Multi-Flow Processing Guide](../../docs/guides/multi-flow-processing.md)
- [ColPali Testing Guide](../../docs/guides/colpali-testing.md)
- [Worker Integration](../../MULTI_FLOW_INTEGRATION.md)

