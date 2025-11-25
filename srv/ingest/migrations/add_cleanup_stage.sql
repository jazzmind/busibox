-- Migration: Add 'cleanup' stage to ingestion_status
-- Description: Adds LLM cleanup stage between chunking and embedding
-- Date: 2025-11-24

-- Drop the old constraint
ALTER TABLE ingestion_status 
DROP CONSTRAINT IF EXISTS ingestion_status_stage_check;

-- Add new constraint with 'cleanup' stage
ALTER TABLE ingestion_status
ADD CONSTRAINT ingestion_status_stage_check 
CHECK (stage IN (
    'queued',
    'parsing', 
    'classifying',
    'extracting_metadata',
    'chunking',
    'cleanup',
    'embedding',
    'indexing',
    'completed',
    'failed'
));

