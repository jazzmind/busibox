---
title: Multi-Flow Document Processing Guide
created: 2025-11-16
updated: 2025-11-16
status: active
category: guides
tags: [processing, strategies, marker, colpali, comparison]
---

# Multi-Flow Document Processing Guide

Complete guide to processing documents through multiple extraction strategies in parallel to compare effectiveness and optimize for different document types.

## Overview

Multi-flow processing enables you to process the same document using different extraction methods simultaneously:

1. **SIMPLE** - Fast baseline extraction using standard libraries (pypdf, python-docx)
2. **MARKER** - Enhanced PDF processing with better structure, tables, and formulas
3. **COLPALI** - Visual embeddings for semantic image search without OCR

Each strategy produces separate results that can be compared to determine which works best for specific document types.

## Why Multi-Flow Processing?

### Benefits

- **Compare Effectiveness**: See which extraction method works best for your documents
- **Optimization**: Choose the right strategy based on speed vs. quality trade-offs
- **Flexibility**: Enable/disable strategies based on available resources
- **Research**: A/B test different approaches for document processing

### Use Cases

1. **Document Type Analysis**: Determine best strategy for invoices vs. reports vs. research papers
2. **Quality Benchmarking**: Compare text extraction quality across methods
3. **Performance Testing**: Measure speed vs. accuracy trade-offs
4. **Visual vs. Text Search**: Compare traditional text search with visual semantic search

## Processing Strategies

### SIMPLE Strategy

**Best for:**
- Well-formatted text documents
- Simple PDFs with clean text
- Text files, Markdown, HTML
- Fast processing requirements

**Characteristics:**
- **Speed**: Fast (< 1 second for small docs)
- **GPU**: Not required
- **Accuracy**: Good for well-formatted documents
- **Limitations**: May miss complex tables, formulas, or layouts

**Supported MIME Types:**
- `application/pdf`
- `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- `text/plain`
- `text/html`
- `text/markdown`
- `text/csv`
- `application/json`

### MARKER Strategy

**Best for:**
- Complex PDFs with tables
- Scientific papers with formulas
- Documents with mixed layouts
- Scanned documents (with OCR)

**Characteristics:**
- **Speed**: Slow (5-30 seconds per document)
- **GPU**: Optional (faster with GPU)
- **Accuracy**: Excellent for complex documents
- **Limitations**: Only works with PDFs, memory intensive

**Supported MIME Types:**
- `application/pdf`

**What Marker Excels At:**
- Table detection and structure preservation
- Mathematical formula extraction
- Multi-column layouts
- Headers, footers, and document structure
- OCR on scanned documents

### COLPALI Strategy

**Best for:**
- Visual documents (infographics, charts)
- Documents where layout matters
- Mixed content (text + images)
- Semantic visual search
- Scanned documents with poor OCR

**Characteristics:**
- **Speed**: Medium (2-5 seconds per page)
- **GPU**: Required (GPU 2)
- **Accuracy**: Excellent for visual understanding
- **Limitations**: Requires ColPali service running

**Supported MIME Types:**
- `application/pdf`
- `image/png`
- `image/jpeg`
- `image/tiff`

**What ColPali Excels At:**
- Visual semantic search (find similar-looking documents)
- Chart and diagram understanding
- Layout-aware search
- Documents where OCR fails
- Multi-modal document understanding

## Configuration

### Enable/Disable Strategies

```python
# In configuration or environment variables
config = {
    "marker_enabled": True,   # Enable Marker for PDFs
    "colpali_enabled": True,  # Enable ColPali for visual embeddings
    "max_parallel_strategies": 3,  # Process up to 3 strategies in parallel
}
```

### Environment Variables

```bash
# Marker configuration
MARKER_ENABLED=true

# ColPali configuration
COLPALI_ENABLED=true
COLPALI_BASE_URL=http://10.96.200.31:8002/v1
COLPALI_API_KEY=EMPTY

# Parallel processing
MAX_PARALLEL_STRATEGIES=3
```

## Usage

### Basic Usage

```python
from processors.multi_flow_processor import MultiFlowProcessor
from shared.config import Config

# Initialize
config = Config().to_dict()
processor = MultiFlowProcessor(config)

# Process document with all applicable strategies
results = await processor.process_document(
    file_path="/path/to/document.pdf",
    mime_type="application/pdf",
    file_id="doc-123",
    original_filename="document.pdf",
)

# results is a dict: {strategy_name: ProcessingResult}
# e.g., {"simple": ProcessingResult(...), "marker": ProcessingResult(...), ...}
```

### Check Results

```python
# Check which strategies succeeded
for strategy_name, result in results.items():
    print(f"{strategy_name}: {' ✓' if result.success else '✗'}")
    if result.success:
        print(f"  Text length: {len(result.text)}")
        print(f"  Processing time: {result.processing_time_seconds:.2f}s")
        print(f"  Chunks/embeddings: {len(result.embeddings) if result.embeddings else 0}")
```

### Compare Results

```python
from processors.processing_strategy import compare_strategy_results

# Get comparison
comparison = compare_strategy_results(list(results.values()))

print(f"Fastest strategy: {comparison['fastest']}")
print(f"Most text extracted: {comparison['most_text']}")
print(f"Most chunks: {comparison['most_chunks']}")

# Get recommendations
for rec in comparison["recommendations"]:
    print(f"• {rec}")
```

### Select Best Strategy

```python
# Choose based on optimization goal
best_for_speed = processor.get_best_strategy(results, optimization_goal="speed")
best_for_quality = processor.get_best_strategy(results, optimization_goal="quality")
best_balanced = processor.get_best_strategy(results, optimization_goal="balanced")

print(f"Use {best_for_speed} for fastest processing")
print(f"Use {best_for_quality} for best quality")
print(f"Use {best_balanced} for balanced approach")
```

## Strategy Selection Logic

### Automatic Selection

The system automatically selects applicable strategies based on MIME type:

```python
from processors.processing_strategy import StrategySelector

selector = StrategySelector(config)

# PDF → SIMPLE, MARKER, COLPALI (all enabled)
pdf_strategies = selector.get_applicable_strategies("application/pdf")

# Text → SIMPLE only
text_strategies = selector.get_applicable_strategies("text/plain")

# Image → SIMPLE, COLPALI (no MARKER)
image_strategies = selector.get_applicable_strategies("image/png")
```

### Manual Override

```python
# Get all supported strategies regardless of config
strategies = selector.get_applicable_strategies(
    mime_type="application/pdf",
    force_all=True,  # Ignore enabled/disabled settings
)
```

## Processing Results

### Result Structure

Each strategy returns a `ProcessingResult` object:

```python
class ProcessingResult:
    strategy: ProcessingStrategy  # Which strategy was used
    success: bool  # Did processing succeed?
    text: Optional[str]  # Extracted text
    markdown: Optional[str]  # Markdown representation (Marker)
    page_images: Optional[List[str]]  # Paths to page images
    page_count: int  # Number of pages
    tables: Optional[List[Dict]]  # Extracted tables (Marker)
    metadata: Optional[Dict]  # Document metadata
    embeddings: Optional[List[List[float]]]  # Text chunk embeddings
    visual_embeddings: Optional[List[List[List[float]]]]  # ColPali embeddings
    error: Optional[str]  # Error message if failed
    processing_time_seconds: float  # Processing duration
```

### Example Results

```python
# SIMPLE result
simple = results["simple"]
print(f"Text: {simple.text[:100]}...")
print(f"Embeddings: {len(simple.embeddings)} chunks")
print(f"Time: {simple.processing_time_seconds:.2f}s")

# MARKER result
marker = results["marker"]
print(f"Markdown: {marker.markdown[:100]}...")
print(f"Tables: {len(marker.tables)} detected")
print(f"Time: {marker.processing_time_seconds:.2f}s")

# COLPALI result
colpali = results["colpali"]
print(f"Visual embeddings: {len(colpali.visual_embeddings)} pages")
print(f"Patches per page: {[len(page) for page in colpali.visual_embeddings]}")
print(f"Time: {colpali.processing_time_seconds:.2f}s")
```

## Comparison and Analysis

### Performance Metrics

```python
# Compare processing times
for strategy_name, result in sorted(
    results.items(),
    key=lambda x: x[1].processing_time_seconds
):
    if result.success:
        print(f"{strategy_name}: {result.processing_time_seconds:.2f}s")
```

### Quality Metrics

```python
# Compare text extraction quality
for strategy_name, result in sorted(
    results.items(),
    key=lambda x: len(x[1].text) if x[1].text else 0,
    reverse=True
):
    if result.success:
        print(f"{strategy_name}: {len(result.text)} characters")
```

### Comprehensive Comparison

```python
from processors.processing_strategy import compare_strategy_results

comparison = compare_strategy_results(list(results.items()))

# Print detailed comparison
print(f"Strategies compared: {comparison['strategies_compared']}")
print(f"Fastest: {comparison['fastest']}")
print(f"Most text: {comparison['most_text']}")
print(f"Most chunks: {comparison['most_chunks']}")

# Print all recommendations
for rec in comparison["recommendations"]:
    print(f"• {rec}")
```

## Best Practices

### When to Use Each Strategy

| Document Type | Recommended Strategy | Why |
|--------------|---------------------|-----|
| Simple text PDFs | SIMPLE | Fast, sufficient quality |
| Complex PDFs with tables | MARKER | Better table detection |
| Scanned documents | MARKER + COLPALI | OCR + visual understanding |
| Infographics | COLPALI | Visual semantic search |
| Research papers | MARKER | Formula and structure extraction |
| Invoices | SIMPLE or MARKER | Structured data extraction |
| Reports with charts | COLPALI | Visual content understanding |

### Resource Optimization

**For Speed:**
```python
config = {
    "marker_enabled": False,  # Disable slow Marker
    "colpali_enabled": False,  # Disable GPU-intensive ColPali
}
# Only SIMPLE will run → fastest processing
```

**For Quality:**
```python
config = {
    "marker_enabled": True,   # Enable all strategies
    "colpali_enabled": True,
}
# All strategies run → choose best result
```

**For Balanced:**
```python
config = {
    "marker_enabled": False,  # Disable slowest
    "colpali_enabled": True,  # Keep visual embeddings
}
# SIMPLE + COLPALI → good balance
```

### Parallel Processing

The system automatically processes strategies in parallel:

```python
# Control parallelism
config = {
    "max_parallel_strategies": 3,  # Process up to 3 strategies simultaneously
}

# Strategies run in ThreadPoolExecutor
# Results are collected as they complete
# No need to manually manage threads
```

## Testing

### Run Multi-Flow Tests

```bash
cd srv/ingest

# Run all multi-flow tests
pytest tests/test_multi_flow.py -v

# Run specific test class
pytest tests/test_multi_flow.py::TestProcessingStrategy -v

# Run integration tests
pytest tests/test_multi_flow.py -v -m integration

# Run with diagnostic output
python tests/test_multi_flow.py
```

### Test Strategy Selection

```python
from processors.processing_strategy import StrategySelector

config = {"marker_enabled": True, "colpali_enabled": True}
selector = StrategySelector(config)

# Test PDF
strategies = selector.get_applicable_strategies("application/pdf")
assert len(strategies) == 3  # SIMPLE, MARKER, COLPALI

# Test text
strategies = selector.get_applicable_strategies("text/plain")
assert len(strategies) == 1  # Only SIMPLE
```

## Troubleshooting

### Strategy Fails

If a strategy fails, check the error message:

```python
result = results["marker"]
if not result.success:
    print(f"Marker failed: {result.error}")
    # Common issues:
    # - "marker-pdf not installed" → install Marker
    # - "PDF file corrupted" → check PDF validity
    # - Memory errors → reduce batch size or disable Marker
```

### ColPali Unavailable

```python
result = results["colpali"]
if not result.success:
    print(f"ColPali failed: {result.error}")
    # Common issues:
    # - "ColPali service not available" → check service health
    # - "No page images extracted" → check PDF is valid
    # - Connection errors → verify COLPALI_BASE_URL
```

### All Strategies Fail

```python
successful = [r for r in results.values() if r.success]
if not successful:
    print("All strategies failed!")
    for strategy_name, result in results.items():
        print(f"  {strategy_name}: {result.error}")
```

## Performance Benchmarks

Expected performance for a 10-page PDF:

| Strategy | Time | CPU | Memory | GPU |
|----------|------|-----|--------|-----|
| SIMPLE | 1-2s | Low | 100MB | No |
| MARKER | 10-30s | High | 1-2GB | Optional |
| COLPALI | 20-50s | Medium | 500MB | Required (2GB VRAM) |

**Parallel Processing:**
- All 3 strategies: ~30-50s (limited by slowest)
- SIMPLE + COLPALI: ~20-50s
- SIMPLE only: ~1-2s

## Migration from Single-Flow

### Before (Single Flow)

```python
# Old approach: single extraction method
extraction = text_extractor.extract(file_path, mime_type)
chunks = chunker.chunk(extraction.text)
embeddings = await embedder.embed_chunks([c.text for c in chunks])
```

### After (Multi-Flow)

```python
# New approach: multiple strategies
processor = MultiFlowProcessor(config)
results = await processor.process_document(
    file_path, mime_type, file_id, filename
)

# Use best result
best_strategy = processor.get_best_strategy(results, "balanced")
best_result = results[best_strategy]
# Use best_result.embeddings, best_result.text, etc.
```

## Examples

### Example 1: Process PDF with All Strategies

```python
processor = MultiFlowProcessor(config)
results = await processor.process_document(
    file_path="research_paper.pdf",
    mime_type="application/pdf",
    file_id="paper-001",
    original_filename="research_paper.pdf",
)

# Compare results
comparison = compare_strategy_results(list(results.values()))
print(f"Fastest: {comparison['fastest']}")  # Probably "simple"
print(f"Most text: {comparison['most_text']}")  # Probably "marker"

# Use Marker result for best quality
if results["marker"].success:
    text = results["marker"].text
    tables = results["marker"].tables
    markdown = results["marker"].markdown
```

### Example 2: Optimize for Speed

```python
# Disable slow strategies
config = {"marker_enabled": False, "colpali_enabled": False}
processor = MultiFlowProcessor(config)

results = await processor.process_document(...)
# Only SIMPLE runs → fastest result
```

### Example 3: Visual Search with ColPali

```python
config = {"colpali_enabled": True}
processor = MultiFlowProcessor(config)

results = await processor.process_document(
    file_path="infographic.pdf",
    mime_type="application/pdf",
    ...
)

if results["colpali"].success:
    visual_embeddings = results["colpali"].visual_embeddings
    # Use for visual semantic search
```

## Related Documentation

- [ColPali Testing Guide](colpali-testing.md)
- [Ingestion Pipeline](../reference/ingestion-pipeline.md)
- [Architecture Overview](../architecture/architecture.md)

## References

- Processing strategies: `srv/ingest/src/processors/processing_strategy.py`
- Multi-flow processor: `srv/ingest/src/processors/multi_flow_processor.py`
- Tests: `srv/ingest/tests/test_multi_flow.py`
- ColPali tests: `srv/ingest/tests/test_colpali.py`

