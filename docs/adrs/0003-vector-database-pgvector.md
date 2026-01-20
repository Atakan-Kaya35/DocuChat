# ADR-0003: Vector Database Choice (pgvector)

**Status:** Accepted  
**Date:** 2026-01-15  
**Authors:** DocuChat Team

---

## Context

DocuChat needs vector similarity search for RAG retrieval. Requirements:
1. Store embeddings (768-1024 dimensions)
2. Fast approximate nearest neighbor (ANN) search
3. Support filtering by owner_user_id
4. Integrate with existing Postgres data model
5. Work in local Docker deployment (no cloud dependencies)

## Decision

**Use PostgreSQL with pgvector extension.**

Configuration:
- PostgreSQL 16 with pgvector 0.5+
- VECTOR(768) column type for embeddings
- IVFFlat index for approximate search
- Filtering done in SQL with vector search

```sql
-- Table schema
CREATE TABLE document_chunks (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id),
    embedding VECTOR(768)
);

-- Index for similarity search
CREATE INDEX ON document_chunks 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Query with owner scoping
SELECT dc.*, d.filename,
       dc.embedding <-> $1 AS distance
FROM document_chunks dc
JOIN documents d ON dc.document_id = d.id
WHERE d.owner_user_id = $2
ORDER BY dc.embedding <-> $1
LIMIT $3;
```

## Alternatives Considered

1. **Pinecone / Weaviate / Qdrant (managed)**
   - Pros: Purpose-built, highly optimized, scalable
   - Cons: Cloud dependency, cost, network latency, API key management
   - Rejected: Violates local-first, zero-cost requirements

2. **Qdrant / Milvus (self-hosted)**
   - Pros: Purpose-built, good performance
   - Cons: Additional container, operational complexity, sync with Postgres
   - Rejected: Adds complexity without clear benefit at our scale

3. **ChromaDB (embedded)**
   - Pros: Simple, Python-native
   - Cons: Separate data store, no ACID with relational data, limited filtering
   - Rejected: Would need sync with Postgres, weaker guarantees

4. **SQLite + sqlite-vss**
   - Pros: Simple, embedded
   - Cons: Not suitable for concurrent access, no filtering during search
   - Rejected: Multi-user concurrency requirement

## Consequences

### Positive
- Single database for all data (documents, chunks, vectors)
- ACID guarantees, transactional consistency
- Owner filtering in same query as vector search
- No additional services to manage
- Familiar SQL tooling

### Negative
- Not as optimized as purpose-built vector DBs
- IVFFlat requires periodic reindexing for optimal performance
- Scale ceiling (fine for 10K-100K chunks, may need HNSW for more)

### Neutral
- Need to manage index parameters (lists, probes)
- Embedding dimension fixed at table creation

## Index Choice: IVFFlat vs HNSW

We chose IVFFlat for v1:
- Simpler to understand and tune
- Good enough for our scale (< 100K chunks)
- Can migrate to HNSW later if needed

```sql
-- IVFFlat (current)
CREATE INDEX ON document_chunks 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- HNSW (future, if needed)
CREATE INDEX ON document_chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

## Follow-up Actions

- [x] Enable pgvector extension in Postgres init
- [x] Create document_chunks table with VECTOR column
- [x] Add IVFFlat index
- [ ] Benchmark query performance at scale
- [ ] Consider HNSW if query latency becomes issue

---

## LLM Disclosure

> This ADR was drafted with assistance from an LLM (Claude). The technical decisions, alternatives analysis, and final choices were reviewed and approved by the development team.
