-- Migration: Add Markdown Storage Support
-- Description: Adds columns to store markdown content and extracted images in MinIO
-- Created: 2025-11-24

-- Add markdown storage path column
ALTER TABLE ingestion_files
ADD COLUMN IF NOT EXISTS markdown_path VARCHAR(512);

-- Add flag to indicate if markdown is available
ALTER TABLE ingestion_files
ADD COLUMN IF NOT EXISTS has_markdown BOOLEAN DEFAULT false;

-- Add path for extracted images directory
ALTER TABLE ingestion_files
ADD COLUMN IF NOT EXISTS images_path VARCHAR(512);

-- Add image count
ALTER TABLE ingestion_files
ADD COLUMN IF NOT EXISTS image_count INTEGER DEFAULT 0;

-- Create index for markdown availability queries
CREATE INDEX IF NOT EXISTS idx_ingestion_files_has_markdown ON ingestion_files(has_markdown);

-- Comments for clarity
COMMENT ON COLUMN ingestion_files.markdown_path IS 'Path to generated markdown file in MinIO';
COMMENT ON COLUMN ingestion_files.has_markdown IS 'Flag indicating if markdown version is available';
COMMENT ON COLUMN ingestion_files.images_path IS 'Path to directory containing extracted images in MinIO';
COMMENT ON COLUMN ingestion_files.image_count IS 'Number of images extracted from the document';


