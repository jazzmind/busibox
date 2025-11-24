-- Migration: Add processing history table
-- Description: Track detailed processing steps, errors, and timing for each document
-- Date: 2025-11-24

CREATE TABLE IF NOT EXISTS processing_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
    stage VARCHAR(50) NOT NULL,
    step_name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('started', 'completed', 'failed', 'skipped')),
    message TEXT,
    error_message TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    duration_ms INTEGER,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_processing_history_file_id ON processing_history(file_id);
CREATE INDEX IF NOT EXISTS idx_processing_history_created_at ON processing_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_processing_history_file_stage ON processing_history(file_id, stage, created_at);

