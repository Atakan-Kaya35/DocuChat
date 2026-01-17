# ADR-0005: Agent Tool Loop Cap and Tool Set

**Status:** Accepted  
**Date:** 2026-01-15  
**Authors:** DocuChat Team

---

## Context

DocuChat includes an "Agent Mode" that can use tools to search documents and gather context before answering. This adds capability but introduces risks:

1. **Runaway loops:** Model keeps calling tools indefinitely
2. **Prompt bloat:** Context grows unboundedly with tool outputs
3. **Unpredictable behavior:** Too many tools = hard to debug
4. **Cost/latency:** Each tool call = LLM roundtrip + potential DB query

We need to make the agent:
- Bounded and predictable
- Debuggable (small action space)
- Secure (no arbitrary code execution)

## Decision

**Hard cap of 5 tool calls, exactly 2 tools.**

### Hard Limits

| Limit | Value | Rationale |
|-------|-------|-----------|
| Max tool calls | 5 | Prevents infinite loops |
| Max iterations | 5 | Same as tool calls for MVP |
| Plan steps | 2-5 | Forces focused planning |
| Tools available | 2 | Small, auditable action space |

### Tool Set

Only these two tools are available:

1. **`search_docs`** - Semantic search across user's documents
   - Input: `{"query": "search terms"}`
   - Output: Top-k chunks (docId, chunkId, snippet, score)
   - Compressed: only metadata, no full text

2. **`open_citation`** - Retrieve full text of a specific chunk
   - Input: `{"docId": "...", "chunkId": "..."}`
   - Output: Chunk text (capped at 1500 chars for prompt)
   - Ownership verified before return

### Strict Action Protocol

```
TOOL_CALL {"tool": "search_docs", "input": {"query": "..."}}
TOOL_CALL {"tool": "open_citation", "input": {"docId": "...", "chunkId": "..."}}
FINAL {"answer": "...", "citations": [1, 2]}
```

If model outputs anything else: re-prompt once, then force synthesis.

## Alternatives Considered

1. **No cap (trust the model)**
   - Pros: Maximum flexibility
   - Cons: Runaway risk, unpredictable costs, hard to debug
   - Rejected: Unacceptable risk

2. **Higher cap (10-20 tool calls)**
   - Pros: More complex reasoning possible
   - Cons: Longer latency, more context bloat, harder to trace
   - Rejected: 5 is sufficient for document Q&A

3. **More tools (summarize, extract, etc.)**
   - Pros: Richer capabilities
   - Cons: Larger action space, more things to break, harder to test
   - Rejected: Two tools cover core use case

4. **Dynamic cap based on question complexity**
   - Pros: Adaptive resource usage
   - Cons: Complexity, still needs a hard max
   - Rejected: Fixed cap is simpler and predictable

## Consequences

### Positive
- Guaranteed termination (max 5 iterations)
- Predictable latency (bounded LLM calls)
- Easy to debug (only 2 possible tools)
- Small trace output (readable by humans)
- Secure (no code execution, no external calls)

### Negative
- May not solve complex multi-hop reasoning
- Can't do tasks requiring more than 5 steps
- Limited to search + read (no write, no external tools)

### Neutral
- Trade-off between capability and predictability
- Users see "Agent Mode" toggle to set expectations

## Tool Output Compression

To avoid prompt bloat:
- `search_docs`: Returns only `docId/chunkId/snippet/score` (no full text)
- `open_citation`: Text capped at 1500 characters
- Rolling window: Only last 3 opened citations kept in context

## Follow-up Actions

- [x] Implement tool loop with hard cap
- [x] Add TOOL_CALL/FINAL parsing
- [x] Implement output compression
- [x] Add trace recording for debugging
- [ ] Consider additional tools for v2 (summarize?)

---

## LLM Disclosure

> This ADR was drafted with assistance from an LLM (Claude). The technical decisions, alternatives analysis, and final choices were reviewed and approved by the development team.
