# Local Development Setup

This guide shows how to test the ingestion pipeline locally without deploying to production.

## Quick Start

### 1. Activate Virtual Environment

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/srv/ingest
source test_venv/bin/activate
```

### 2. Install Dependencies (if needed)

```bash
pip install -r requirements.txt
```

### 3. Test with a PDF

```bash
# Test extraction and chunking
python test_local.py /path/to/your/document.pdf

# Example with the Neal Stephenson PDF
python test_local.py ~/Downloads/commandline.pdf
```

## What the Test Does

The `test_local.py` script:

1. **Extracts text** from the PDF using pdfplumber
   - Shows first 1000 characters
   - Reports page count and text length

2. **Chunks the text** using semantic chunking
   - Shows chunk count and token statistics
   - Displays first 3 chunks

3. **Validates output**
   - Checks for smashed words (missing spaces)
   - Verifies markdown heading formatting
   - Ensures chunks don't exceed size limits

## Expected Output

```
================================================================================
Testing: /path/to/document.pdf
================================================================================

1. EXTRACTING TEXT
--------------------------------------------------------------------------------
✓ Extraction complete
  - Pages: 77
  - Text length: 234567 characters
  - Method: pdfplumber

First 1000 characters of extracted text:
--------------------------------------------------------------------------------
In the Beginning was the Command Line

Neal Stephenson

About twenty years ago Jobs and Wozniak...
--------------------------------------------------------------------------------

2. CHUNKING TEXT
--------------------------------------------------------------------------------
✓ Chunking complete
  - Total chunks: 76
  - Total tokens: 45678
  - Avg tokens per chunk: 600.5

First 3 chunks:
--------------------------------------------------------------------------------

--- Chunk 1 (tokens: 512, chars: 2456) ---
# In the Beginning was the Command Line

Neal Stephenson

About twenty years ago Jobs and Wozniak, the founders of Apple...
...
--------------------------------------------------------------------------------

3. VALIDATION
--------------------------------------------------------------------------------
✓ No issues detected

================================================================================
Test complete!
================================================================================
```

## Common Issues

### Issue: Smashed words (e.g., "mountedinanother")

**Symptom**: Words run together without spaces
**Cause**: pdfplumber not preserving word boundaries
**Fix**: Use `layout=True` and adjust `x_tolerance`/`y_tolerance`

### Issue: No markdown headings

**Symptom**: Titles not formatted as `# Title`
**Cause**: Heading detection not recognizing patterns
**Fix**: Improve `_convert_to_markdown()` logic

### Issue: Only 1 chunk for large document

**Symptom**: 76 chunks created but only 1 showing in database
**Cause**: Chunking logic treating entire document as single paragraph
**Fix**: Improve paragraph detection in `_chunk_simple()`

## Testing Specific Components

### Test Text Extraction Only

```python
from processors.text_extractor import TextExtractor

config = {"temp_dir": "/tmp/test", "marker_enabled": False}
extractor = TextExtractor(config)
result = extractor.extract("document.pdf", "application/pdf")

print(result.text[:1000])
```

### Test Chunking Only

```python
from processors.chunker import Chunker

chunker = Chunker(max_tokens=512, min_tokens=100, overlap_pct=0.1)
chunks = chunker.chunk(your_text_here)

for i, chunk in enumerate(chunks[:3]):
    print(f"Chunk {i+1}: {chunk.text[:200]}")
```

## Running Unit Tests

```bash
# Run all chunker tests
pytest tests/test_chunker.py -v

# Run specific test
pytest tests/test_chunker.py::TestChunking::test_respects_max_tokens -v

# Run with coverage
pytest tests/test_chunker.py --cov=src/processors/chunker --cov-report=term
```

## Debugging Tips

### Enable Verbose Logging

```python
import structlog
import logging

logging.basicConfig(level=logging.DEBUG)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
)
```

### Inspect Raw PDF Text

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for i, page in enumerate(pdf.pages):
        text = page.extract_text(layout=True, x_tolerance=3, y_tolerance=3)
        print(f"=== Page {i+1} ===")
        print(text[:500])
        print()
```

### Check spaCy Sentence Detection

```python
import spacy

nlp = spacy.load("en_core_web_sm")
doc = nlp(your_text_here)

for sent in doc.sents:
    print(f"- {sent.text}")
```

## Next Steps

Once local testing passes:

1. **Commit changes**
   ```bash
   git add -A
   git commit -m "Fix text extraction and chunking"
   ```

2. **Deploy to test environment**
   ```bash
   cd /root/busibox/provision/ansible
   make ingest
   ```

3. **Run remote tests**
   ```bash
   make test-ingest
   ```

4. **Deploy to production**
   ```bash
   make production
   ```

