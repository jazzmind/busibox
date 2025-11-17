-- Migration: Add Multi-Flow Processing Support
-- Description: Adds support for storing results from multiple processing strategies
-- Created: 2025-11-16
-- Status: Optional - Not yet applied

-- Add processing_strategy column to identify which strategy was used
ALTER TABLE ingestion_chunks
ADD COLUMN IF NOT EXISTS processing_strategy VARCHAR(50) DEFAULT 'simple';

-- Add strategy metadata to files table
ALTER TABLE ingestion_files
ADD COLUMN IF NOT EXISTS processing_strategies JSONB DEFAULT '[]'::jsonb;

-- Create index for strategy filtering
CREATE INDEX IF NOT EXISTS idx_chunks_strategy 
ON ingestion_chunks(processing_strategy);

-- Create table for strategy comparison results
CREATE TABLE IF NOT EXISTS processing_strategy_results (
    result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
    processing_strategy VARCHAR(50) NOT NULL,
    success BOOLEAN NOT NULL DEFAULT false,
    text_length INTEGER,
    chunk_count INTEGER,
    embedding_count INTEGER,
    visual_embedding_count INTEGER,
    processing_time_seconds NUMERIC(10, 3),
    error_message TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(file_id, processing_strategy)
);

CREATE INDEX IF NOT EXISTS idx_strategy_results_file 
ON processing_strategy_results(file_id);

CREATE INDEX IF NOT EXISTS idx_strategy_results_strategy 
ON processing_strategy_results(processing_strategy);

-- Add comments
COMMENT ON COLUMN ingestion_chunks.processing_strategy IS 
'Processing strategy used: simple, marker, or colpali';

COMMENT ON TABLE processing_strategy_results IS 
'Stores results from each processing strategy for comparison';

COMMENT ON COLUMN processing_strategy_results.processing_strategy IS 
'Strategy used: simple (fast baseline), marker (enhanced PDF), colpali (visual embeddings)';

-- Migration status
-- To apply: Run this SQL against your PostgreSQL database
-- Note: This is optional and not required for basic functionality

