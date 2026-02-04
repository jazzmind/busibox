-- Migration 011: Web Search Providers Table
-- Created: 2025-12-15
-- Description: Add web_search_providers table for centralized web search API key management

-- Create web_search_providers table
CREATE TABLE IF NOT EXISTS web_search_providers (
    id SERIAL PRIMARY KEY,
    provider VARCHAR(50) UNIQUE NOT NULL,  -- 'tavily', 'duckduckgo', 'serpapi', 'perplexity', 'bing'
    api_key TEXT,  -- Encrypted API key (NULL for providers that don't need keys like DuckDuckGo)
    endpoint TEXT,  -- Optional custom endpoint URL
    is_enabled BOOLEAN NOT NULL DEFAULT true,
    is_default BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    CONSTRAINT valid_provider CHECK (provider IN ('tavily', 'duckduckgo', 'serpapi', 'perplexity', 'bing'))
);

-- Create index on provider for fast lookups
CREATE INDEX IF NOT EXISTS idx_web_search_providers_provider ON web_search_providers(provider);

-- Create index on enabled providers
CREATE INDEX IF NOT EXISTS idx_web_search_providers_enabled ON web_search_providers(is_enabled) WHERE is_enabled = true;

-- Create index on default provider
CREATE UNIQUE INDEX IF NOT EXISTS idx_web_search_providers_default ON web_search_providers(is_default) WHERE is_default = true;

-- Create trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_web_search_providers_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_web_search_providers_updated_at
    BEFORE UPDATE ON web_search_providers
    FOR EACH ROW
    EXECUTE FUNCTION update_web_search_providers_updated_at();

-- Grant permissions to app_user
GRANT SELECT, INSERT, UPDATE, DELETE ON web_search_providers TO app_user;
GRANT USAGE, SELECT ON SEQUENCE web_search_providers_id_seq TO app_user;

-- Insert default DuckDuckGo provider (no API key required)
INSERT INTO web_search_providers (provider, api_key, is_enabled, is_default)
VALUES ('duckduckgo', NULL, true, true)
ON CONFLICT (provider) DO NOTHING;

-- Record migration
INSERT INTO ansible_migrations (version, name, applied_at)
VALUES (11, 'web_search_providers', CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration 011: web_search_providers table created successfully';
END $$;

