# Worker Refactoring Plan

## Current Problems

1. **Monolithic File**: 1,167 lines in a single file
2. **Mixed Concerns**: Job orchestration, error handling, retry logic, processing steps all in one place
3. **Hard to Test**: Can't easily test individual stages without running full pipeline
4. **Hard to Maintain**: Any change requires understanding the entire flow
5. **Difficult to Extend**: Adding new processing strategies or steps is cumbersome

## Proposed Structure

```
srv/ingest/src/
├── worker.py                       # Main worker (orchestration only, ~200 lines)
├── worker/
│   ├── __init__.py
│   ├── job_processor.py           # Core job processing logic
│   ├── error_handler.py           # Error classification & retry logic
│   ├── history_logger.py          # Processing history logging
│   └── stages/
│       ├── __init__.py
│       ├── base.py                # Base stage class
│       ├── parsing_stage.py       # Stage 1: Download & extract
│       ├── classification_stage.py # Stage 2: Classify document
│       ├── metadata_stage.py      # Stage 3: Extract metadata
│       ├── chunking_stage.py      # Stage 4: Chunk text (+ cleanup)
│       ├── embedding_stage.py     # Stage 5: Generate embeddings
│       ├── indexing_stage.py      # Stage 6: Store in Milvus
│       └── multiflow_stage.py     # Stage 7: Multi-flow comparison
```

## Refactoring Benefits

### 1. **Testability**
Each stage can be tested independently:
```python
def test_parsing_stage():
    stage = ParsingStage(config, services)
    result = stage.execute(file_info)
    assert result.text_length > 0
    assert result.page_count > 0
```

### 2. **Maintainability**
Changes to one stage don't affect others:
- Update chunking logic → Edit `chunking_stage.py`
- Add new error type → Edit `error_handler.py`
- Change history format → Edit `history_logger.py`

### 3. **Extensibility**
Easy to add new stages or strategies:
```python
class CustomProcessingStage(BaseStage):
    def execute(self, context):
        # Custom logic
        pass
```

### 4. **Clarity**
Each file has a single responsibility:
- `worker.py`: Redis stream consumption, job routing
- `job_processor.py`: Orchestrate stages, manage context
- `parsing_stage.py`: Only parsing logic
- `error_handler.py`: Only error handling

## Proposed Classes

### BaseStage
```python
class BaseStage:
    """Base class for all processing stages."""
    
    def __init__(self, config, services, history_logger):
        self.config = config
        self.services = services
        self.history = history_logger
        
    @property
    def name(self) -> str:
        """Stage name for logging."""
        raise NotImplementedError
        
    @property
    def stage_key(self) -> str:
        """Stage key for database status."""
        raise NotImplementedError
    
    def execute(self, context: ProcessingContext) -> StageResult:
        """Execute the stage. Returns result or raises exception."""
        raise NotImplementedError
        
    def can_retry(self, error: Exception) -> bool:
        """Whether this stage's errors are retryable."""
        return True
```

### ProcessingContext
```python
@dataclass
class ProcessingContext:
    """Shared context passed between stages."""
    file_id: str
    user_id: str
    storage_path: str
    mime_type: str
    original_filename: str
    temp_file_path: Optional[str] = None
    extraction_result: Optional[ExtractionResult] = None
    chunks: Optional[List[Chunk]] = None
    embeddings: Optional[List] = None
    # ... other shared state
    
    def get(self, key: str, default=None):
        """Dict-like access for optional fields."""
        return getattr(self, key, default)
```

### JobProcessor
```python
class JobProcessor:
    """Orchestrates job processing through stages."""
    
    def __init__(self, config, services, history_logger, error_handler):
        self.stages = [
            ParsingStage(config, services, history_logger),
            ClassificationStage(config, services, history_logger),
            MetadataStage(config, services, history_logger),
            ChunkingStage(config, services, history_logger),
            EmbeddingStage(config, services, history_logger),
            IndexingStage(config, services, history_logger),
            MultiFlowStage(config, services, history_logger),
        ]
        
    def process(self, job_data: dict) -> ProcessingResult:
        """Process job through all stages."""
        context = self._create_context(job_data)
        
        for stage in self.stages:
            try:
                result = stage.execute(context)
                context.update(result)  # Pass results to next stage
            except Exception as e:
                self.error_handler.handle(stage, context, e)
                
        return ProcessingResult(context)
```

### ErrorHandler
```python
class ErrorHandler:
    """Handles errors and retry logic."""
    
    def is_transient(self, error: Exception) -> bool:
        """Classify error as transient or permanent."""
        # Current logic from worker._is_transient_error()
        
    def should_retry(self, file_id: str, error: Exception) -> bool:
        """Determine if job should be retried."""
        # Check retry count, error type, etc.
        
    def handle(self, stage: BaseStage, context: ProcessingContext, error: Exception):
        """Handle error from a stage."""
        self.history.log_error(context.file_id, stage.name, error)
        
        if self.should_retry(context.file_id, error):
            self.requeue_job(context)
        else:
            self.mark_failed(context)
```

### HistoryLogger
```python
class HistoryLogger:
    """Wrapper around ProcessingHistoryService with convenience methods."""
    
    def __init__(self, history_service):
        self.service = history_service
        
    def log_stage_start(self, file_id: str, stage: str, metadata: dict = None):
        """Log stage start."""
        self.service.log_step(
            file_id, stage, "stage_start", "started",
            metadata=metadata
        )
        
    def log_stage_complete(self, file_id: str, stage: str, result: dict, duration: float):
        """Log stage completion."""
        # ...
        
    def log_error(self, file_id: str, stage: str, error: Exception):
        """Log error with stack trace."""
        # ...
```

## Migration Strategy

### Phase 1: Extract Error Handling
1. Create `worker/error_handler.py`
2. Move `_is_transient_error()`, retry logic
3. Update `worker.py` to use ErrorHandler
4. Test: Existing error handling works

### Phase 2: Extract History Logging
1. Create `worker/history_logger.py`
2. Move `_log_step()` and add convenience methods
3. Update stages to use HistoryLogger
4. Test: History still logs correctly

### Phase 3: Create Stage Base Class
1. Create `worker/stages/base.py` with BaseStage
2. Create `ProcessingContext` dataclass
3. Test: Base class pattern works

### Phase 4: Extract Stages (One at a Time)
1. Start with `parsing_stage.py` (simplest)
2. Extract parsing logic from `process_job()`
3. Update `JobProcessor` to use ParsingStage
4. Test: Parsing still works
5. Repeat for each stage

### Phase 5: Create JobProcessor
1. Create `worker/job_processor.py`
2. Move stage orchestration from `process_job()`
3. Update `worker.py` to use JobProcessor
4. Test: Full pipeline works

### Phase 6: Cleanup
1. Remove old code from `worker.py`
2. Update imports
3. Final integration tests

## Testing Strategy

### Unit Tests (New)
```python
def test_parsing_stage_success():
    """Test parsing stage with valid PDF."""
    stage = ParsingStage(config, mock_services, mock_history)
    context = ProcessingContext(file_id="123", ...)
    result = stage.execute(context)
    assert result.text_length > 0

def test_parsing_stage_failure():
    """Test parsing stage handles errors."""
    stage = ParsingStage(config, mock_services, mock_history)
    context = ProcessingContext(file_id="123", ...)
    with pytest.raises(ExtractionError):
        stage.execute(context)
```

### Integration Tests (Update Existing)
```python
def test_full_pipeline():
    """Test complete job processing."""
    processor = JobProcessor(config, services, history, error_handler)
    result = processor.process(job_data)
    assert result.status == "completed"
```

## File Size Targets

After refactoring:
- `worker.py`: ~200 lines (orchestration)
- `job_processor.py`: ~150 lines (stage coordination)
- `error_handler.py`: ~100 lines (error logic)
- `history_logger.py`: ~80 lines (logging helpers)
- Each stage file: ~100-150 lines (single stage logic)

**Total: ~1,000 lines across 10+ files vs 1,167 in one file**

## Decision Points

### Should we refactor now or after completing features?

**Arguments for NOW:**
- ✅ Easier to add remaining history logging with stage structure
- ✅ Easier to add multi-flow logging as separate stage
- ✅ Prevents further growth of monolithic file
- ✅ Makes testing new features easier

**Arguments for LATER:**
- ⚠️ Takes time away from feature development
- ⚠️ Risk of introducing bugs during refactor
- ⚠️ Current code works

### Recommendation: **Hybrid Approach**

1. **Quick wins first** (1-2 hours):
   - Extract `ErrorHandler` (self-contained)
   - Extract `HistoryLogger` (wrapper)
   - These don't change logic, just organize

2. **Add remaining features** using current structure:
   - Complete history logging
   - Add processing tab to UI
   - Test end-to-end

3. **Full refactor later** when we have:
   - Working history system
   - UI displaying results
   - Clear understanding of all edge cases

This way we get immediate organization benefits without blocking feature delivery.

## Next Steps

**Option A: Quick Wins Now**
1. Extract ErrorHandler (30 min)
2. Extract HistoryLogger (30 min)
3. Continue with history logging in cleaner structure

**Option B: Feature First**
1. Complete history logging in current file
2. Add UI tab
3. Test everything
4. Then do full refactor

**What's your preference?**

