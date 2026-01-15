# PostgreSQL with pgvector

This folder contains PostgreSQL configuration and initialization scripts.

## pgvector Extension

DocuChat uses the [pgvector](https://github.com/pgvector/pgvector) extension for vector similarity search, enabling semantic document retrieval in RAG pipelines.

### How pgvector is enabled

1. We use the `pgvector/pgvector:pg16` Docker image (or similar) which has pgvector pre-installed.
2. The `initdb/001-enable-pgvector.sql` script runs automatically on first container start.
3. This script creates the `vector` extension in the application database.

### Manual verification

```sql
-- Check if pgvector is installed
SELECT * FROM pg_extension WHERE extname = 'vector';

-- Check available vector operations
\dx vector
```

## Volumes

- `postgres_data` volume persists database files across container restarts.
- `initdb/` scripts only run on **first initialization** (empty data directory).

## Environment Variables

Required environment variables (set in `.env`):
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
