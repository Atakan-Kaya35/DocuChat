-- =============================================================================
-- 001-enable-pgvector.sql
-- Runs on first PostgreSQL initialization to enable pgvector extension.
-- =============================================================================

-- Enable the vector extension for similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify extension is enabled (will show in logs)
DO $$
BEGIN
    RAISE NOTICE 'pgvector extension enabled successfully';
END $$;
