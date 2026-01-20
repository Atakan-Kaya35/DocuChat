# ADR-0002: Chunking Strategy and Determinism

**Status:** Accepted  
**Date:** 2026-01-15  
**Authors:** DocuChat Team

---

## Context

For RAG to work effectively, documents must be split into chunks suitable for:
1. Embedding (fit within model context)
2. Retrieval (semantically coherent units)
3. Citation (stable references that persist across re-indexing)

Key requirements:
- Chunks should be semantic units (paragraphs/sections)
- Chunk IDs must be stable for citation links
- Re-indexing same document should produce identical chunks
- Chunks should be small enough for good retrieval precision

## Decision

**Use deterministic fixed-size chunking with overlap.**

Configuration:
- **Chunk size:** 1000 characters
- **Overlap:** 200 characters (20%)
- **Chunk ID:** Derived from `(document_id, chunk_index)`

Chunking algorithm:
```python
def chunk_text(text: str, chunk_size=1000, overlap=200) -> List[Chunk]:
    chunks = []
    start = 0
    index = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(Chunk(
            index=index,
            text=text[start:end],
            char_start=start,
            char_end=end
        ))
        start = end - overlap
        index += 1
    return chunks
```

Database constraint ensures stability:
```sql
UNIQUE(document_id, chunk_index)
```

## Alternatives Considered

1. **Semantic chunking (sentence boundaries)**
   - Pros: More coherent chunks
   - Cons: Non-deterministic (depends on sentence detection), variable sizes
   - Rejected: Determinism more important for stable citations

2. **LLM-based chunking**
   - Pros: Optimal semantic units
   - Cons: Expensive, slow, non-deterministic, model-dependent
   - Rejected: Overkill, adds latency and cost

3. **Recursive text splitter (LangChain-style)**
   - Pros: Tries to split on natural boundaries
   - Cons: More complex, still size-variable, harder to debug
   - Rejected: Simple fixed-size is sufficient for v1

4. **Larger chunks (2000+ chars)**
   - Pros: More context per chunk
   - Cons: Lower retrieval precision, embedding dilution
   - Rejected: 1000 chars balances precision and context

## Consequences

### Positive
- Fully deterministic: same document → same chunks every time
- Stable chunk IDs for persistent citation links
- Simple to understand and debug
- Overlap prevents information loss at boundaries

### Negative
- May split mid-sentence (semantic loss)
- Fixed size doesn't adapt to document structure
- Overlap means ~20% storage overhead

### Neutral
- chunk_index is sequential (0, 1, 2, ...)
- char_start/char_end stored for potential UI highlighting

## Follow-up Actions

- [x] Implement chunking in indexing worker
- [x] Add UNIQUE constraint on (document_id, chunk_index)
- [ ] Consider sentence-aware chunking for v2
- [ ] Evaluate chunk size impact on retrieval quality

---

## LLM Disclosure

> This ADR was drafted with assistance from an LLM (Claude). The technical decisions, alternatives analysis, and final choices were reviewed and approved by the development team.
