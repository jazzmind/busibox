-- Migration: Add missing columns to processing_history table
-- The processing_history_service.py expects step_name, message, metadata, and created_at
-- columns that weren't in the original schema.
-- This migration is idempotent (uses IF NOT EXISTS checks).

-- Add step_name column
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'processing_history' AND column_name = 'step_name'
    ) THEN
        ALTER TABLE processing_history ADD COLUMN step_name VARCHAR(100);
    END IF;
END $$;

-- Add message column
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'processing_history' AND column_name = 'message'
    ) THEN
        ALTER TABLE processing_history ADD COLUMN message TEXT;
    END IF;
END $$;

-- Add metadata column (the original schema had 'details' JSONB, but the service uses 'metadata')
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'processing_history' AND column_name = 'metadata'
    ) THEN
        -- If the old 'details' column exists, rename it to 'metadata'
        IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'processing_history' AND column_name = 'details'
        ) THEN
            ALTER TABLE processing_history RENAME COLUMN details TO metadata;
        ELSE
            ALTER TABLE processing_history ADD COLUMN metadata JSONB DEFAULT '{}';
        END IF;
    END IF;
END $$;

-- Add created_at column
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'processing_history' AND column_name = 'created_at'
    ) THEN
        ALTER TABLE processing_history ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW();
    END IF;
END $$;
