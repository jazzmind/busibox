-- Migration 010: Data Service Schema
-- Created: 2025-11-05
-- Description: Create tables for production-grade document ingestion service with
--              content deduplication, multi-language support, and real-time status tracking

-- ============================================================================
-- Data Files Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS data_files (
  -- Primary key
  file_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  
  -- User ownership
  user_id UUID NOT NULL,
  
  -- File information
  filename VARCHAR(255) NOT NULL,
  original_filename VARCHAR(255) NOT NULL,
  mime_type VARCHAR(100) NOT NULL,
  size_bytes BIGINT NOT NULL,
  storage_path TEXT NOT NULL,  -- S3 path in MinIO
  
  -- Content deduplication (SHA-256 hash)
  content_hash VARCHAR(64) NOT NULL,  -- SHA-256 hex digest
  
  -- Document classification
  document_type VARCHAR(50),  -- report, article, email, code, etc.
  primary_language VARCHAR(10),  -- ISO 639-1 code (en, es, fr, etc.)
  detected_languages VARCHAR(10)[],  -- Array of all detected languages
  classification_confidence REAL CHECK (classification_confidence >= 0 AND classification_confidence <= 1),
  
  -- Processing metrics
  chunk_count INTEGER DEFAULT 0,
  vector_count INTEGER DEFAULT 0,
  processing_duration_seconds INTEGER,
  
  -- Extracted metadata
  extracted_title VARCHAR(500),
  extracted_author VARCHAR(255),
  extracted_date DATE,
  extracted_keywords TEXT[],
  metadata JSONB DEFAULT '{}',  -- Additional extracted metadata
  
  -- Permissions
  permissions JSONB NOT NULL DEFAULT '{"visibility": "private"}',
  
  -- Timestamps
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Data Status Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS data_status (
  file_id UUID PRIMARY KEY REFERENCES data_files(file_id) ON DELETE CASCADE,
  
  -- Current processing state
  stage VARCHAR(50) NOT NULL CHECK (stage IN (
    'queued', 'parsing', 'classifying', 'extracting_metadata', 
    'chunking', 'cleanup', 'markdown', 'entity_extraction',
    'embedding', 'indexing', 'completed', 'failed'
  )),
  progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
  
  -- Stage-specific metrics
  chunks_processed INTEGER,
  total_chunks INTEGER,
  pages_processed INTEGER,
  total_pages INTEGER,
  
  -- Error handling
  error_message TEXT,
  retry_count INTEGER DEFAULT 0,
  
  -- Timestamps
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Data Chunks Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS data_chunks (
  chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  file_id UUID NOT NULL REFERENCES data_files(file_id) ON DELETE CASCADE,
  
  -- Chunk metadata
  chunk_index INTEGER NOT NULL,  -- Position in document (0-indexed)
  text TEXT NOT NULL,             -- Actual chunk text
  char_offset INTEGER,            -- Character offset in original document
  token_count INTEGER,            -- Number of tokens (for validation)
  
  -- Document structure
  page_number INTEGER,            -- PDF page number (null for non-PDFs)
  section_heading VARCHAR(500),   -- Section/chapter heading (if detected)
  
  -- Additional metadata
  metadata JSONB DEFAULT '{}',
  
  -- Timestamp
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  
  -- Unique constraint to prevent duplicate chunks
  UNIQUE (file_id, chunk_index)
);

-- ============================================================================
-- Indexes for Performance
-- ============================================================================

-- Data files indexes
CREATE INDEX IF NOT EXISTS idx_data_files_user_id ON data_files(user_id);
CREATE INDEX IF NOT EXISTS idx_data_files_content_hash ON data_files(content_hash);  -- For duplicate detection
CREATE INDEX IF NOT EXISTS idx_data_files_document_type ON data_files(document_type);
CREATE INDEX IF NOT EXISTS idx_data_files_created_at ON data_files(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_files_primary_language ON data_files(primary_language);
CREATE INDEX IF NOT EXISTS idx_data_files_detected_languages ON data_files USING gin(detected_languages);  -- GIN index for array queries

-- Full-text search on extracted metadata
CREATE INDEX IF NOT EXISTS idx_data_files_metadata_gin ON data_files USING gin(metadata jsonb_path_ops);

-- Data status indexes
CREATE INDEX IF NOT EXISTS idx_data_status_stage ON data_status(stage);
CREATE INDEX IF NOT EXISTS idx_data_status_updated_at ON data_status(updated_at DESC);

-- Data chunks indexes
CREATE INDEX IF NOT EXISTS idx_data_chunks_file_id ON data_chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_data_chunks_chunk_index ON data_chunks(file_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_data_chunks_page_number ON data_chunks(file_id, page_number);

-- ============================================================================
-- Triggers and Functions
-- ============================================================================

-- Function to update parent table timestamp
CREATE OR REPLACE FUNCTION update_data_file_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  UPDATE data_files SET updated_at = NOW() WHERE file_id = NEW.file_id;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to update parent table timestamp on status updates
CREATE TRIGGER trigger_update_data_file_timestamp
AFTER UPDATE ON data_status
FOR EACH ROW
EXECUTE FUNCTION update_data_file_timestamp();

-- Function to notify on status updates (for SSE)
CREATE OR REPLACE FUNCTION notify_status_update()
RETURNS TRIGGER AS $$
DECLARE
  payload JSON;
BEGIN
  payload = json_build_object(
    'file_id', NEW.file_id,
    'stage', NEW.stage,
    'progress', NEW.progress,
    'chunks_processed', NEW.chunks_processed,
    'total_chunks', NEW.total_chunks,
    'pages_processed', NEW.pages_processed,
    'total_pages', NEW.total_pages,
    'error_message', NEW.error_message
  );
  
  PERFORM pg_notify('status_updates', payload::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to send NOTIFY on status updates
CREATE TRIGGER trigger_notify_status_update
AFTER INSERT OR UPDATE ON data_status
FOR EACH ROW
EXECUTE FUNCTION notify_status_update();

-- ============================================================================
-- Record Migration
-- ============================================================================

INSERT INTO ansible_migrations (version, name, applied_at)
VALUES (10, 'data_schema', CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;

