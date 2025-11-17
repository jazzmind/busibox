# Ingestion System - Phase 2 Implementation Plan

## Completed ✅

1. **Markdown Rendering in UI** ✅
   - Added ReactMarkdown to chunk viewer
   - Chunks now display with proper formatting
   - Headings, lists, emphasis all render correctly

2. **Text Extraction Fixes** ✅
   - Fixed pdfplumber extraction
   - Proper paragraph detection
   - Title/byline formatting

3. **Local Testing Environment** ✅
   - Created test_local.py script
   - Validated fixes with real PDF
   - All core functionality working

## Remaining Tasks

### Task 1: Fix Export MD→HTML/DOCX/PDF
**Priority**: HIGH
**Complexity**: MEDIUM
**Time Estimate**: 2-3 hours

**Implementation**:

```python
# srv/ingest/src/api/routes/files.py - export endpoint

async def export_file(fileId: str, format: str, request: Request):
    # 1. Fetch chunks from PostgreSQL (they already have markdown)
    chunks = await fetch_chunks_from_db(fileId)
    
    # 2. Combine chunks into single markdown document
    markdown_content = "\n\n".join(chunk["text"] for chunk in chunks)
    
    # 3. Convert based on format
    if format == "markdown":
        return Response(content=markdown_content, media_type="text/markdown")
    
    elif format == "html":
        import markdown
        html = markdown.markdown(markdown_content, extensions=['extra', 'codehilite'])
        html_doc = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; }}
                h1 {{ color: #333; }}
                h2 {{ color: #666; }}
            </style>
        </head>
        <body>{html}</body>
        </html>
        """
        return Response(content=html_doc, media_type="text/html")
    
    elif format == "docx":
        from docx import Document
        from docx.shared import Pt, Inches
        import re
        
        doc = Document()
        
        # Parse markdown and add to docx
        lines = markdown_content.split('\n')
        for line in lines:
            if line.startswith('# '):
                doc.add_heading(line[2:], level=1)
            elif line.startswith('## '):
                doc.add_heading(line[3:], level=2)
            elif line.strip():
                doc.add_paragraph(line)
        
        # Save to bytes
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    
    elif format == "pdf":
        # Option 1: Use weasyprint (HTML→PDF)
        from weasyprint import HTML
        html = markdown.markdown(markdown_content)
        pdf_bytes = HTML(string=html).write_pdf()
        return Response(content=pdf_bytes, media_type="application/pdf")
        
        # Option 2: Use reportlab (direct PDF generation)
        # More control but more complex
```

**Dependencies to Add**:
```txt
weasyprint>=60.0.0  # For HTML→PDF conversion
```

**Files to Modify**:
- `srv/ingest/src/api/routes/files.py`
- `srv/ingest/requirements.txt`

**Testing**:
1. Upload document with markdown
2. Export as HTML - verify headings render
3. Export as DOCX - verify Word formatting
4. Export as PDF - verify layout

### Task 2: Model Purpose Registry
**Priority**: HIGH (Foundation for Task 3)
**Complexity**: MEDIUM
**Time Estimate**: 2 hours

**Implementation**:

```python
# srv/ingest/src/shared/model_registry.py (NEW FILE)

from typing import Dict, Optional
import json
import os

class ModelRegistry:
    """
    Central registry for model purposes.
    
    Maps abstract purposes (embedding, cleanup, chat) to actual model names.
    Allows easy model swapping without code changes.
    """
    
    DEFAULT_MODELS = {
        "embedding": {
            "model": "qwen-3-embedding",
            "description": "Text embedding generation",
            "max_tokens": 8192,
            "provider": "litellm"
        },
        "visual": {
            "model": "colpali-v1.3",
            "description": "Visual document embedding",
            "max_tokens": 4096,
            "provider": "colpali"
        },
        "cleanup": {
            "model": "qwen-2.5-32b",
            "description": "Text cleanup and formatting",
            "max_tokens": 32768,
            "temperature": 0.1,
            "provider": "litellm"
        },
        "chat": {
            "model": "qwen-2.5-72b",
            "description": "General chat and Q&A",
            "max_tokens": 32768,
            "provider": "litellm"
        },
        "classify": {
            "model": "phi-4",
            "description": "Document classification",
            "max_tokens": 4096,
            "provider": "litellm"
        },
        "reranking": {
            "model": "bge-reranker-v2-m3",
            "description": "Search result reranking",
            "max_tokens": 512,
            "provider": "litellm"
        },
        "research": {
            "model": "qwen-2.5-72b",
            "description": "Research and analysis",
            "max_tokens": 32768,
            "provider": "litellm"
        },
        "calculation": {
            "model": "deepseek-r1",
            "description": "Mathematical reasoning",
            "max_tokens": 8192,
            "provider": "litellm"
        },
        "analysis": {
            "model": "qwen-2.5-72b",
            "description": "Data analysis",
            "max_tokens": 32768,
            "provider": "litellm"
        }
    }
    
    def __init__(self, config_path: Optional[str] = None):
        self.models = self.DEFAULT_MODELS.copy()
        
        # Load custom config if provided
        if config_path and os.path.exists(config_path):
            with open(config_path, 'r') as f:
                custom = json.load(f)
                self.models.update(custom.get("purposes", {}))
    
    def get_model(self, purpose: str) -> str:
        """Get model name for a purpose."""
        if purpose not in self.models:
            raise ValueError(f"Unknown purpose: {purpose}. Available: {list(self.models.keys())}")
        return self.models[purpose]["model"]
    
    def get_config(self, purpose: str) -> Dict:
        """Get full config for a purpose."""
        if purpose not in self.models:
            raise ValueError(f"Unknown purpose: {purpose}")
        return self.models[purpose]
    
    def list_purposes(self) -> list:
        """List all available purposes."""
        return list(self.models.keys())

# Global registry instance
_registry = None

def get_registry() -> ModelRegistry:
    """Get global model registry instance."""
    global _registry
    if _registry is None:
        config_path = os.getenv("MODEL_REGISTRY_PATH")
        _registry = ModelRegistry(config_path)
    return _registry
```

**Usage Example**:
```python
# Before (hardcoded):
embedder = Embedder(model="qwen-3-embedding")

# After (using registry):
from shared.model_registry import get_registry
registry = get_registry()
embedder = Embedder(model=registry.get_model("embedding"))
```

**Files to Create**:
- `srv/ingest/src/shared/model_registry.py`

**Files to Modify**:
- `srv/ingest/src/processors/embedder.py`
- `srv/ingest/src/processors/classifier.py`
- Any other files with hardcoded model names

### Task 3: LLM Cleanup Pass
**Priority**: MEDIUM (Nice to have)
**Complexity**: HIGH
**Time Estimate**: 4-5 hours

**Implementation**:

```python
# srv/ingest/src/processors/llm_cleanup.py (NEW FILE)

import structlog
from typing import List
import httpx
from shared.model_registry import get_registry

logger = structlog.get_logger()

class LLMCleanup:
    """
    Clean up text chunks using LLM.
    
    Fixes:
    - Smashed words (actuallyunderstood → actually understood)
    - Missing spaces between sentences
    - Incorrect line breaks
    - Preserves markdown formatting
    """
    
    CLEANUP_PROMPT = """You are a text cleanup assistant. Fix spacing, line breaks, and formatting issues in the following text.

Rules:
1. Fix smashed words by adding spaces where needed
2. Fix missing spaces between sentences
3. Preserve all content - don't summarize or remove anything
4. Preserve markdown formatting (# headings, *italic*, etc.)
5. Output clean, properly formatted markdown

Text to clean:
{text}

Cleaned text:"""
    
    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get("llm_cleanup_enabled", False)
        self.litellm_base_url = config.get("litellm_base_url", "http://litellm-lxc:4000")
        
        # Get model from registry
        registry = get_registry()
        self.model = registry.get_model("cleanup")
        self.model_config = registry.get_config("cleanup")
        
        logger.info(
            "LLM cleanup initialized",
            enabled=self.enabled,
            model=self.model,
            base_url=self.litellm_base_url
        )
    
    async def cleanup_chunk(self, text: str) -> str:
        """Clean up a single chunk of text."""
        if not self.enabled:
            return text
        
        # Skip if text looks clean (no long words)
        if not self._needs_cleanup(text):
            logger.debug("Chunk looks clean, skipping LLM cleanup")
            return text
        
        try:
            prompt = self.CLEANUP_PROMPT.format(text=text)
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.litellm_base_url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": self.model_config.get("temperature", 0.1),
                        "max_tokens": self.model_config.get("max_tokens", 32768),
                    }
                )
                
                if response.status_code != 200:
                    logger.error("LLM cleanup failed", status=response.status_code)
                    return text  # Return original on error
                
                result = response.json()
                cleaned_text = result["choices"][0]["message"]["content"]
                
                logger.info(
                    "Chunk cleaned",
                    original_length=len(text),
                    cleaned_length=len(cleaned_text)
                )
                
                return cleaned_text
        
        except Exception as e:
            logger.error("LLM cleanup error", error=str(e))
            return text  # Return original on error
    
    def _needs_cleanup(self, text: str) -> bool:
        """Check if text needs cleanup (has long words)."""
        import re
        # Find words longer than 40 characters (likely smashed)
        long_words = re.findall(r'\b\w{40,}\b', text)
        return len(long_words) > 0
    
    async def cleanup_chunks(self, chunks: List) -> List:
        """Clean up multiple chunks."""
        if not self.enabled:
            return chunks
        
        cleaned_chunks = []
        for chunk in chunks:
            cleaned_text = await self.cleanup_chunk(chunk.text)
            chunk.text = cleaned_text
            cleaned_chunks.append(chunk)
        
        return cleaned_chunks
```

**Integration in Worker**:
```python
# srv/ingest/src/worker.py

# After chunking:
chunks = chunker.chunk(text)

# Add cleanup pass (optional)
if config.get("llm_cleanup_enabled"):
    llm_cleanup = LLMCleanup(config)
    chunks = await llm_cleanup.cleanup_chunks(chunks)

# Continue with embedding...
```

**Configuration**:
```bash
# .env
LLM_CLEANUP_ENABLED=true
LITELLM_BASE_URL=http://litellm-lxc:4000
```

**Files to Create**:
- `srv/ingest/src/processors/llm_cleanup.py`

**Files to Modify**:
- `srv/ingest/src/worker.py`
- `srv/ingest/src/shared/config.py`

## Deployment Order

1. **Deploy Phase 1** (Already done)
   - Markdown rendering in UI ✅
   - Text extraction fixes ✅

2. **Deploy Task 1** (Export fixes)
   - Add weasyprint to requirements.txt
   - Update export endpoint
   - Test all export formats
   - Deploy to production

3. **Deploy Task 2** (Model registry)
   - Create model_registry.py
   - Update all model references
   - Test with different models
   - Deploy to production

4. **Deploy Task 3** (LLM cleanup)
   - Create llm_cleanup.py
   - Integrate into worker
   - Test with problematic PDFs
   - Make optional (disabled by default)
   - Deploy to production

## Testing Checklist

### Export Testing
- [ ] Export as Markdown - verify formatting
- [ ] Export as HTML - verify headings/lists render
- [ ] Export as DOCX - verify Word formatting
- [ ] Export as PDF - verify layout

### Model Registry Testing
- [ ] Change embedding model in registry
- [ ] Verify embeddings still work
- [ ] Change cleanup model
- [ ] Verify cleanup uses new model

### LLM Cleanup Testing
- [ ] Upload PDF with smashed words
- [ ] Enable LLM cleanup
- [ ] Verify chunks are cleaned
- [ ] Compare before/after quality
- [ ] Verify markdown preserved

## Next Session

Start with Task 1 (Export fixes) as it's highest priority and user-facing.

