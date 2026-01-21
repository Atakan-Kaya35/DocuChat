# ADR-0008: Cross-Encoder Reranking

**Status:** Accepted  
**Date:** 2026-01-15  
**Authors:** DocuChat Team

> **⚠️ ADDITIONAL FEATURE**
> 
> This ADR documents an enhancement that was implemented **in addition to the original project requirements**. Cross-encoder reranking is an optional feature that can be disabled without affecting core functionality.

---

## Context

The base RAG implementation uses bi-encoder embeddings (nomic-embed-text via Ollama) for vector similarity search. While fast and effective, bi-encoders have limitations:

1. **Independent Encoding**: Query and document are embedded separately, missing interaction signals
2. **Semantic Drift**: Similar embeddings don't always mean relevant content
3. **Top-K Cutoff**: Borderline-relevant chunks may be included while highly relevant ones are excluded

Observed issues:
- Vector search returns 5 chunks at 0.7-0.8 similarity, but only 2 are truly helpful
- Subtle relevance (same topic, different phrasing) often missed
- Users ask "why are these results so random?"

The challenge: improve precision without sacrificing the speed of vector search.

## Decision

Implement an **optional two-stage retrieval pipeline** with cross-encoder reranking:

```
Stage 1: Vector Search (bi-encoder)
   → Fast, high recall
   → Retrieve 20 candidates

Stage 2: Cross-Encoder Reranking  
   → Slower, high precision
   → Score each (query, chunk) pair
   → Keep top 5
```

Key design choices:

- **Model**: `cross-encoder/ms-marco-MiniLM-L-6-v2` (small, fast, well-tested)
- **Lazy Loading**: Model loads on first use, stays in memory
- **GPU/CPU Fallback**: Uses CUDA if available, otherwise CPU
- **Singleton Pattern**: Single model instance shared across requests
- **Server-wide Toggle**: `ENABLE_RERANKER` environment variable
- **Per-request Toggle**: `rerank` parameter in API request

## Alternatives Considered

### 1. Use a Larger Bi-Encoder

**Description:** Replace nomic-embed-text with a larger, more accurate embedding model.

**Pros:**
- Single-stage pipeline (simpler)
- No additional model to load

**Cons:**
- Larger models are slower for every request
- Still limited by bi-encoder architecture
- May require more VRAM

**Verdict:** Rejected. Bi-encoder limitations are architectural, not size-related.

### 2. LLM-Based Reranking

**Description:** Use the chat LLM (gemma:7b) to rank chunks.

**Pros:**
- Already loaded, no new model
- Could provide explanations

**Cons:**
- Very slow (seconds per chunk)
- High token cost
- Overkill for relevance scoring

**Verdict:** Rejected. Cross-encoders are purpose-built for this task and 100x faster.

### 3. Always-On Reranking

**Description:** Apply reranking to every search automatically.

**Pros:**
- Consistent result quality
- No user decision needed

**Cons:**
- Adds 50-200ms to every request
- Model loading delay on first request
- May not improve already-good results

**Verdict:** Rejected. Let users choose when precision matters.

### 4. ColBERT or Dense Retrieval

**Description:** Use late-interaction models like ColBERT.

**Pros:**
- Better than bi-encoders
- Can be indexed for speed

**Cons:**
- Requires special indexing
- More complex infrastructure
- Less mature tooling

**Verdict:** Deferred. Good future option, but cross-encoder meets current needs.

## Consequences

### Positive

- **Better Precision**: Top-5 results are more consistently relevant
- **Transparent Scoring**: Both vector_score and rerank_score visible in response
- **Flexible**: Toggle per-request based on user needs
- **Efficient**: Cross-encoder is small (~80MB) and fast (~100ms)
- **GPU Acceleration**: Automatically uses CUDA when available

### Negative

- **Added Latency**: 50-200ms when enabled (first request slower due to model loading)
- **Memory Usage**: ~300MB GPU memory or ~80MB CPU memory
- **Dependency**: Requires sentence-transformers and torch packages
- **Cold Start**: First rerank request loads model (~500ms-2s)

### Neutral

- Model is downloaded on first use (from HuggingFace Hub)
- Reranking uses the refined query if query refinement is also enabled
- Feature is OFF by default (opt-in)

## Implementation Details

### Pipeline Configuration

```python
# Retrieve more candidates than needed
RERANK_TOP_K = 20  # Vector search returns 20

# Keep fewer after reranking
RERANK_KEEP_N = 5  # Cross-encoder keeps 5
```

This "retrieve more, keep fewer" strategy maximizes recall in stage 1 and precision in stage 2.

### Model Loading

```python
class CrossEncoderReranker:
    """Singleton reranker with lazy loading."""
    
    _instance = None
    _model = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def _load_model(self):
        if self._model is not None:
            return
        
        # Auto-detect GPU
        device = "cuda" if torch.cuda.is_available() else "cpu"
        
        # Load (downloads on first use)
        self._model = CrossEncoder(
            "cross-encoder/ms-marco-MiniLM-L-6-v2",
            device=device,
        )
```

### Scoring

```python
def rerank(self, query, candidates, top_n=None):
    # Prepare pairs
    pairs = [(query, chunk.text) for chunk in candidates]
    
    # Batch score
    scores = self._model.predict(pairs)
    
    # Attach scores and sort
    for chunk, score in zip(candidates, scores):
        chunk.rerank_score = float(score)
    
    return sorted(candidates, key=lambda c: c.rerank_score, reverse=True)[:top_n]
```

### API Response

```json
{
  "answer": "...",
  "sources": [
    {
      "chunk_id": "abc123",
      "snippet": "...",
      "vector_score": 0.8234,
      "rerank_score": 0.9512
    }
  ],
  "metadata": {
    "rerank_used": true,
    "rerank_latency_ms": 120
  }
}
```

## Follow-up Actions

- [x] Implement CrossEncoderReranker class
- [x] Add ChunkCandidate dataclass
- [x] Integrate with /api/rag/ask pipeline
- [x] Add frontend toggle
- [x] Write comprehensive tests
- [x] Document in ENHANCEMENTS.md

---

## LLM Disclosure

> This ADR was drafted with assistance from an LLM (Claude). The technical decisions, alternatives analysis, and final choices were reviewed and approved by the development team.
