# ADR-0007: Query Refinement (Prompt Rewriting)

**Status:** Accepted  
**Date:** 2026-01-15  
**Authors:** DocuChat Team

> **⚠️ ADDITIONAL FEATURE**
> 
> This ADR documents an enhancement that was implemented **in addition to the original project requirements**. Query refinement is an optional feature that can be disabled without affecting core functionality.

---

## Context

In the base RAG implementation, user queries are passed directly to the vector search. This works well for precise, keyword-rich queries but can produce suboptimal results when users ask conversational or vague questions.

Common issues observed:
- Users say "how do I fix the login thing" instead of "troubleshoot authentication failure"
- Abbreviations and slang aren't in the indexed document vocabulary
- Important constraints ("only in the runbooks") are buried in natural language
- Multiple questions in one message compete for retrieval

The challenge: improve retrieval quality without changing the user's intent or adding significant latency.

## Decision

Implement an **optional LLM-based query rewriting step** that runs before vector search. The rewriter:

1. Transforms conversational queries into retrieval-optimized form
2. Extracts keywords, entities, and constraints
3. Preserves the original intent (never answers the question)
4. Falls back silently to the original query on any failure

Key design choices:
- **Separate endpoint** (`/api/rag/rewrite`) for immediate UI feedback
- **Low temperature (0.1)** for predictable JSON output
- **Strict JSON validation** with fallback on parse errors
- **Server-wide toggle** via `ENABLE_QUERY_REFINEMENT` environment variable
- **Per-request toggle** via `refine_prompt` parameter

## Alternatives Considered

### 1. Rule-Based Query Expansion

**Description:** Use regex patterns and synonym dictionaries to expand queries.

**Pros:**
- Fast (no LLM call)
- Deterministic
- No additional infrastructure

**Cons:**
- Limited flexibility
- Requires manual rule maintenance
- Can't understand context

**Verdict:** Rejected. Too rigid for natural language queries.

### 2. Always-On Query Refinement

**Description:** Run rewriting on every query automatically.

**Pros:**
- Consistent behavior
- No user decision required

**Cons:**
- Adds 500-2000ms to every request
- May hurt queries that are already well-formed
- Users can't see/control what's happening

**Verdict:** Rejected. Latency impact too high for unconditional use.

### 3. Client-Side Query Refinement

**Description:** Run rewriting in the frontend before API call.

**Pros:**
- Keeps backend simple
- Could use different models

**Cons:**
- Requires frontend LLM integration
- Harder to control/audit
- Duplicates infrastructure

**Verdict:** Rejected. Centralizing in backend is simpler and more secure.

## Consequences

### Positive

- **Better Retrieval**: Vague queries now match relevant chunks
- **User Transparency**: Refined query shown in UI
- **Graceful Degradation**: Silent fallback on failure
- **Flexible**: Toggle per-request or server-wide
- **Extensible**: Schema captures constraints, entities, ambiguities for future use

### Negative

- **Added Latency**: 500-2000ms when enabled
- **LLM Dependency**: Requires chat model to be available
- **Complexity**: Another moving part in the pipeline
- **Token Cost**: Additional LLM calls (though using local Ollama)

### Neutral

- Rewriting uses the same chat model as answer generation
- Feature is OFF by default (opt-in)
- Original query always used for final answer generation

## Implementation Details

### API Flow

```
1. POST /api/rag/rewrite (optional, called by frontend)
   - Returns refined query immediately
   - Frontend shows in message bubble

2. POST /api/rag/ask with refine_prompt=true
   - Backend calls rewriter if not already done
   - Uses refined query for vector search
   - Uses original query for LLM answer
```

### Fallback Conditions

The system falls back to the original query when:
- `ENABLE_QUERY_REFINEMENT=false` (server-wide disable)
- LLM request times out (30s limit)
- JSON response fails validation
- Required fields missing
- LLM returns empty response

### Security Measures

- Prompt injection attempts logged in `security_flags`
- Rewriter never executes user instructions
- Output strictly validated against schema
- No extra keys allowed in response

## Follow-up Actions

- [x] Implement query rewriter module
- [x] Add `/api/rag/rewrite` endpoint
- [x] Integrate with `/api/rag/ask` pipeline
- [x] Add frontend toggle and display
- [x] Write comprehensive tests
- [x] Document in ENHANCEMENTS.md

---

## LLM Disclosure

> This ADR was drafted with assistance from an LLM (Claude). The technical decisions, alternatives analysis, and final choices were reviewed and approved by the development team.
