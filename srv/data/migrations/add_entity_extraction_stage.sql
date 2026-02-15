-- Migration: Add entity_extraction and markdown stages to data_status CHECK constraint
-- The worker uses these stages for entity/keyword extraction and markdown generation,
-- but they were missing from the allowed values in the CHECK constraint.
-- This migration is idempotent.

DO $$ BEGIN
    -- Check if entity_extraction is already in the constraint
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'data_status_stage_check'
          AND consrc LIKE '%entity_extraction%'
    ) THEN
        -- Drop the old constraint and recreate with new stages
        ALTER TABLE data_status DROP CONSTRAINT IF EXISTS data_status_stage_check;
        ALTER TABLE data_status ADD CONSTRAINT data_status_stage_check
            CHECK (stage IN (
                'queued', 'parsing', 'classifying', 'extracting_metadata',
                'chunking', 'cleanup', 'markdown', 'entity_extraction',
                'embedding', 'indexing', 'completed', 'failed'
            ));
    END IF;
END $$;
