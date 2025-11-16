# Ingestion System Improvements

## Status: In Progress

## Issues to Address

### 1. Markdown Not Visible in Chunks ✅ IDENTIFIED
**Problem**: Chunks are stored with markdown formatting (# headings, *italic*, etc.) but displayed as plain text in the UI.

**Root Cause**: Frontend displays chunks with `whitespace-pre-wrap` but doesn't render markdown.

**Solution**:
- Option A: Render markdown in the chunk viewer (use react-markdown)
- Option B: Store both plain and markdown versions
- **Recommended**: Option A - render markdown in UI

**Files to Change**:
- `ai-portal/src/app/documents/[fileId]/page.tsx` - Add markdown rendering
- `ai-portal/package.json` - Add react-markdown dependency

### 2. Export Not Converting MD→HTML/DOCX/PDF ✅ IDENTIFIED
**Problem**: Export endpoint receives markdown chunks but doesn't properly convert to target formats.

**Current State**: `srv/ingest/src/api/routes/files.py` export endpoint exists but may not be using markdown source.

**Solution**:
- Ensure export reads markdown-formatted chunks from database
- Use proper conversion libraries:
  - MD→HTML: Python `markdown` library
  - MD→DOCX: `python-docx` with markdown parsing
  - MD→PDF: `reportlab` or `weasyprint` (HTML→PDF)

**Files to Change**:
- `srv/ingest/src/api/routes/files.py` - Fix export endpoint
- `srv/ingest/requirements.txt` - Add `weasyprint` or `pdfkit`

### 3. Text Quality Issues (Smashed Words, Bad Newlines) ✅ PLANNED
**Problem**: PDFs with poor text layers have:
- Smashed words: `actuallyunderstoodwhatacomputeroperatingsystem`
- Missing spaces between sentences
- Incorrect line breaks

**Solution**: LLM Cleanup Pass
- After chunking, pass each chunk through LLM for cleanup
- Use local model via liteLLM
- Prompt: "Fix spacing, line breaks, and formatting. Preserve all content. Output clean markdown."

**Implementation**:
1. Add `processors/llm_cleanup.py` module
2. Integrate into worker pipeline after chunking
3. Make it optional (config flag: `llm_cleanup_enabled`)
4. Use streaming for large chunks

**Files to Create/Change**:
- `srv/ingest/src/processors/llm_cleanup.py` (new)
- `srv/ingest/src/worker.py` - Add cleanup step
- `srv/ingest/src/shared/config.py` - Add config flags

### 4. Model Purpose Mapping System ✅ PLANNED
**Problem**: Hardcoded model names throughout codebase. When models change, must update many files.

**Solution**: Model Purpose Registry
- Define model "purposes": embedding, visual, parsing, reranking, chat, classify, research, calculation, analysis, cleanup
- Map purposes to actual model names in config
- All code references purposes, not model names

**Implementation**:
```python
# In liteLLM config or separate model registry
MODEL_PURPOSES = {
    "embedding": "qwen-3-embedding",
    "visual": "colpali-v1.3",
    "parsing": "phi-4",
    "cleanup": "qwen-2.5-32b",
    "chat": "qwen-2.5-72b",
    "classify": "phi-4",
    "reranking": "bge-reranker-v2-m3",
    "research": "qwen-2.5-72b",
    "calculation": "deepseek-r1",
    "analysis": "qwen-2.5-72b",
}

def get_model_for_purpose(purpose: str) -> str:
    return MODEL_PURPOSES.get(purpose, "qwen-2.5-32b")  # Default fallback
```

**Files to Create/Change**:
- `srv/ingest/src/shared/model_registry.py` (new)
- `srv/ingest/src/processors/embedder.py` - Use registry
- `srv/ingest/src/processors/llm_cleanup.py` - Use registry
- Update all model references to use purposes

## Implementation Order

1. **Phase 1: Markdown Rendering** (Quick Win)
   - Add react-markdown to AI Portal
   - Update chunk viewer to render markdown
   - Test with existing chunks

2. **Phase 2: Export Fixes** (Medium)
   - Fix export endpoint to use markdown source
   - Add proper MD→HTML/DOCX/PDF conversion
   - Test all export formats

3. **Phase 3: Model Purpose Registry** (Foundation)
   - Create model registry system
   - Update all model references
   - Document purpose definitions

4. **Phase 4: LLM Cleanup** (Advanced)
   - Implement LLM cleanup processor
   - Integrate into pipeline
   - Make optional with config flag
   - Test with problematic PDFs

## Testing Strategy

### Phase 1 Testing
- Upload document with markdown (titles, headings)
- View chunks - verify markdown renders
- Export as markdown - verify formatting preserved

### Phase 2 Testing
- Export as HTML - verify proper HTML structure
- Export as DOCX - verify headings/formatting
- Export as PDF - verify layout and formatting

### Phase 3 Testing
- Change model in registry
- Verify all services use new model
- No code changes required

### Phase 4 Testing
- Upload PDF with smashed words
- Enable LLM cleanup
- Verify chunks are cleaned
- Compare before/after quality

## Configuration

### Environment Variables
```bash
# LLM Cleanup
LLM_CLEANUP_ENABLED=true
LLM_CLEANUP_MODEL_PURPOSE=cleanup
LITELLM_BASE_URL=http://litellm-lxc:4000

# Model Registry
MODEL_REGISTRY_PATH=/etc/ingest/model_registry.json
```

### Model Registry JSON
```json
{
  "purposes": {
    "embedding": {
      "model": "qwen-3-embedding",
      "description": "Text embedding generation",
      "max_tokens": 8192
    },
    "cleanup": {
      "model": "qwen-2.5-32b",
      "description": "Text cleanup and formatting",
      "max_tokens": 32768,
      "temperature": 0.1
    },
    "chat": {
      "model": "qwen-2.5-72b",
      "description": "General chat and Q&A",
      "max_tokens": 32768
    }
  }
}
```

## Success Criteria

- ✅ Markdown headings visible in chunk viewer
- ✅ Export to HTML shows proper HTML structure
- ✅ Export to DOCX preserves headings/formatting
- ✅ Export to PDF has proper layout
- ✅ Model registry allows easy model swapping
- ✅ LLM cleanup fixes smashed words
- ✅ All tests passing
- ✅ Documentation updated

## Next Steps

1. Start with Phase 1 (markdown rendering) - quick win
2. Move to Phase 2 (export fixes) - user-facing improvement
3. Implement Phase 3 (model registry) - foundation for future
4. Add Phase 4 (LLM cleanup) - advanced feature

## Related Files

- `ai-portal/src/app/documents/[fileId]/page.tsx`
- `srv/ingest/src/api/routes/files.py`
- `srv/ingest/src/processors/chunker.py`
- `srv/ingest/src/worker.py`
- `srv/ingest/src/shared/config.py`
