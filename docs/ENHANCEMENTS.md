# DocuChat Enhancements

> **⚠️ ADDITIONAL FEATURES**
> 
> This document describes features that were implemented **in addition to the original project requirements**. These are optional enhancements designed to improve retrieval quality and user experience. The core DocuChat functionality works without these features enabled.

---

## Table of Contents

1. [Overview](#overview)
2. [Query Refinement (Prompt Rewriting)](#query-refinement-prompt-rewriting)
3. [Cross-Encoder Reranking](#cross-encoder-reranking)
4. [Configuration Reference](#configuration-reference)
5. [Performance Considerations](#performance-considerations)

---

## Overview

DocuChat implements two optional retrieval enhancements that work together to improve the quality of RAG responses:

| Feature | Purpose | Default State |
|---------|---------|---------------|
| **Query Refinement** | Rewrites user queries into retrieval-optimized form | OFF (toggle in UI) |
| **Cross-Encoder Reranking** | Re-scores vector search results for better relevance | OFF (toggle in UI) |

Both features can be:
- Toggled per-request via UI controls
- Disabled server-wide via environment variables
- Used independently or together

---

## Query Refinement (Prompt Rewriting)

### What It Does

Query refinement uses an LLM pre-pass to transform user questions into more retrieval-friendly queries **before** searching the vector index. This improves recall without changing the user's intent.

### How It Works

```
User Question
    │
    ▼
┌─────────────────────────────────────────────────┐
│            Query Rewriter (LLM)                 │
│  "how do i fix the login bug" →                 │
│  "troubleshoot authentication login failure"    │
└─────────────────────────────────────────────────┘
    │
    ▼
Vector Search (uses refined query)
    │
    ▼
LLM Answer Generation (uses original question)
```

### Key Features

- **Intent Preservation**: The rewriter is instructed to never change the user's intent, only improve query phrasing
- **Keyword Extraction**: Identifies important keywords and named entities for better matching
- **Constraint Detection**: Extracts constraints like time ranges, document scope, or language preferences
- **Ambiguity Detection**: Identifies vague terms that might need clarification
- **Silent Fallback**: If rewriting fails (timeout, parse error, LLM unavailable), the system silently uses the original query

### LLM Output Schema

The query rewriter produces a structured JSON response:

```json
{
  "rewritten_query": "optimized query for retrieval",
  "alternate_queries": ["variant 1", "variant 2"],
  "keywords": ["key", "terms"],
  "named_entities": ["DocuChat", "PostgreSQL"],
  "constraints": {
    "time_range": null,
    "document_scope": "runbooks only",
    "language": "en",
    "response_format": null
  },
  "intent": "troubleshooting",
  "ambiguities": [],
  "clarifying_questions": [],
  "security_flags": []
}
```

### Security Considerations

The query rewriter includes security features:
- Detects prompt injection attempts in `security_flags`
- Never executes or answers the user's question
- Low temperature (0.1) for consistent, predictable output
- Short timeout (30s) to avoid blocking

### API Usage

The rewrite endpoint is called separately to provide immediate feedback:

```http
POST /api/rag/rewrite
Content-Type: application/json
Authorization: Bearer <token>

{
  "question": "how do i fix the login bug"
}
```

Response:
```json
{
  "rewritten_query": "troubleshoot authentication login failure error resolution",
  "original_query": "how do i fix the login bug"
}
```

Or on fallback:
```json
{
  "rewritten_query": "how do i fix the login bug",
  "original_query": "how do i fix the login bug",
  "fallback": true
}
```

### UI Integration

The frontend displays the refined query in the user's message bubble, showing exactly what was searched:

```
┌─────────────────────────────────────────────────┐
│ You: how do i fix the login bug                 │
│ ✨ Refined: troubleshoot authentication login   │
│    failure error resolution                     │
└─────────────────────────────────────────────────┘
```

---

## Cross-Encoder Reranking

### What It Does

After retrieving candidates from vector search, cross-encoder reranking re-scores each chunk against the query using a more powerful model. This improves precision by promoting truly relevant chunks.

### How It Works

```
Vector Search (top-k candidates)
    │
    ▼
┌─────────────────────────────────────────────────┐
│         Cross-Encoder Reranker                  │
│  Scores each (query, chunk) pair directly       │
│  Model: ms-marco-MiniLM-L-6-v2                  │
└─────────────────────────────────────────────────┘
    │
    ▼
Top-N reranked results (better precision)
```

### Why Two-Stage Retrieval?

| Stage | Model | Speed | Accuracy |
|-------|-------|-------|----------|
| **Vector Search** | Bi-encoder (nomic-embed-text) | Fast (milliseconds) | Good |
| **Reranking** | Cross-encoder (ms-marco) | Slower (~100ms) | Excellent |

Vector search is fast but can miss nuances. Cross-encoders see query and document together, capturing subtle relevance signals that bi-encoders miss.

### Key Features

- **Lazy Loading**: Model loads on first use, stays in memory for subsequent requests
- **GPU Acceleration**: Automatically uses CUDA if available, falls back to CPU
- **Batch Scoring**: Scores all candidates in a single forward pass
- **Text Truncation**: Safely handles long chunks (1500 char limit)
- **Configurable Pipeline**: Retrieve more candidates (top-20), keep fewer after reranking (top-5)

### Model Details

| Property | Value |
|----------|-------|
| Model | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Max Sequence Length | 512 tokens |
| Size | ~22M parameters (~80MB) |
| Input | (query, passage) pairs |
| Output | Relevance score (higher = more relevant) |

### Pipeline Configuration

The reranking pipeline uses a "retrieve more, keep fewer" strategy:

```python
# Default settings
RERANK_TOP_K = 20   # Retrieve 20 candidates from vector search
RERANK_KEEP_N = 5   # Keep top 5 after reranking
```

This means:
1. Vector search returns 20 candidates (more recall)
2. Cross-encoder scores all 20
3. Only top 5 are used for answer generation (more precision)

### Response Metadata

When reranking is used, the API response includes additional metrics:

```json
{
  "answer": "...",
  "sources": [...],
  "metadata": {
    "retrieval_latency_ms": 45,
    "rerank_used": true,
    "rerank_latency_ms": 120,
    "llm_latency_ms": 2500
  }
}
```

Each source chunk includes both scores:

```json
{
  "chunk_id": "abc123",
  "doc_title": "runbook.pdf",
  "snippet": "To reset the password...",
  "vector_score": 0.8234,
  "rerank_score": 0.9512
}
```

---

## Configuration Reference

### Environment Variables

Add these to `backend/.env` to control feature behavior:

```bash
# Query Refinement
ENABLE_QUERY_REFINEMENT=true    # Master switch (default: true)

# Cross-Encoder Reranking
ENABLE_RERANKER=true            # Master switch (default: true)
RERANK_TOP_K=20                 # Candidates to retrieve for reranking
RERANK_KEEP_N=5                 # Candidates to keep after reranking
```

### Disabling Features

To completely disable a feature server-wide:

```bash
# Disable query refinement (refine toggle has no effect)
ENABLE_QUERY_REFINEMENT=false

# Disable reranking (rerank toggle has no effect)
ENABLE_RERANKER=false
```

### API Request Parameters

Both features can be toggled per-request:

```json
POST /api/rag/ask
{
  "question": "how do i reset passwords",
  "refine_prompt": true,
  "rerank": true,
  "topK": 5,
  "temperature": 0.7
}
```

---

## Performance Considerations

### Latency Impact

| Feature | Typical Latency | Notes |
|---------|-----------------|-------|
| Query Refinement | +500-2000ms | LLM call, depends on model/hardware |
| Reranking | +50-200ms | Cross-encoder inference, GPU helps significantly |

### When to Use Each Feature

**Enable Query Refinement when:**
- Users ask vague or conversational questions
- Document vocabulary differs from user vocabulary
- You want to see what the system is actually searching

**Enable Reranking when:**
- Vector search returns loosely relevant results
- Precision matters more than speed
- You have GPU resources available

**Consider disabling when:**
- Ultra-low latency is required
- Running on limited hardware (no GPU)
- Queries are already well-formed (e.g., keyword searches)

### Memory Requirements

| Component | Memory |
|-----------|--------|
| Cross-encoder model | ~300MB (GPU) or ~80MB (CPU) |
| Query rewriter | Uses main chat model (already loaded) |

---

## Testing

Both features have comprehensive test coverage:

```bash
# Run query rewriter tests
pytest tests/test_query_rewriter.py -v

# Run reranker tests
pytest tests/test_reranker.py -v
```

Tests cover:
- Normal operation
- Fallback scenarios (LLM timeout, parse errors)
- Edge cases (empty input, missing fields)
- Integration with RAG pipeline

---

## LLM Disclosure

> This documentation was drafted with assistance from an LLM (Claude). The feature designs, implementation details, and architectural decisions were made by the development team.
