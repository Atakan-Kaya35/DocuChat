-- =============================================================================
-- 01-pgvector.sql
-- Runs on first PostgreSQL initialization to enable pgvector extension.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
