# Agent Mode Architecture

This document describes the Agent Mode implementation in DocuChat, a bounded multi-step tool-using agent for document question-answering.

## Overview

Agent Mode provides an advanced question-answering capability that goes beyond simple RAG (Retrieval Augmented Generation). Instead of a single search-and-answer flow, it implements a **bounded agent loop** that can:

- Execute multiple searches across different topics
- Open and read specific citations in full
- Validate answers against constraints before finalizing
- Handle complex multi-part questions with structured output

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER REQUEST                                    │
│                    "Produce operational runbook with..."                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         1. CONSTRAINT ANALYSIS                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ analyze_constraints(prompt)                                          │    │
│  │ Detects:                                                             │    │
│  │   • min_searches: 3 (from "separate tool searches for each topic")  │    │
│  │   • min_open_citations: 2 (from "open_citation for at least two")   │    │
│  │   • requires_exact_quote: true (from "quote one exact SQL")         │    │
│  │   • requires_insufficiency_disclosure: true                         │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           2. PLAN GENERATION                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ generate_plan(question) → Plan with 2-5 steps                       │    │
│  │ Example:                                                             │    │
│  │   1. Search for 'reindex sql' and 'delete order verification'       │    │
│  │   2. Open top citations for each query                              │    │
│  │   3. Search for 'rate limit' and 'retry policy'                     │    │
│  │   4. Extract and synthesize with citations                          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      3. BOUNDED TOOL LOOP                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Hard Limits:                                                         │    │
│  │   • MAX_TOOL_CALLS = 5                                               │    │
│  │   • MAX_ITERATIONS = 10                                              │    │
│  │   • MAX_REPROMPTS = 3                                                │    │
│  │                                                                      │    │
│  │ Loop:                                                                │    │
│  │   while iteration < MAX_ITERATIONS and tools < MAX_TOOL_CALLS:      │    │
│  │       prompt = build_iteration_prompt(state, constraints)           │    │
│  │       response = call_llm(prompt)                                   │    │
│  │       action = parse_strict_json_action(response)                   │    │
│  │                                                                      │    │
│  │       if TOOL_CALL:                                                  │    │
│  │           execute_tool(action) → update state                       │    │
│  │       elif FINAL:                                                    │    │
│  │           validation = validate_agent_state(...)  ─────────────┐    │    │
│  │           if valid:                                             │    │    │
│  │               break (accept answer)                             │    │    │
│  │           else:                                                 │    │    │
│  │               reprompt with errors ◄───────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        4. VALIDATION GATE                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ validate_agent_state() checks:                                       │    │
│  │   ✓ min_searches satisfied (e.g., ≥3 searches done)                 │    │
│  │   ✓ min_open_citations satisfied (e.g., ≥2 citations opened)        │    │
│  │   ✓ citation references are valid (no hallucinated [5])             │    │
│  │   ✓ claims are grounded (no "pg_reindex" if not in docs)            │    │
│  │   ✓ exact quote requirements met                                     │    │
│  │   ✓ insufficiency disclosure present when required                   │    │
│  │                                                                      │    │
│  │ On failure:                                                          │    │
│  │   • Generate reprompt message with unmet constraints                 │    │
│  │   • Remind remaining tool budget                                     │    │
│  │   • Force TOOL_CALL if budget allows                                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       5. CITATION GROUNDING                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ ground_citations_from_state():                                       │    │
│  │   • Map [1], [2] markers to actual opened citations                 │    │
│  │   • Strip hallucinated references (e.g., [5] if only 2 opened)      │    │
│  │   • Build GroundedCitation objects with real doc/chunk IDs          │    │
│  │   • Fallback to search snippets if no citations opened              │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FINAL RESPONSE                                     │
│  {                                                                           │
│    "answer": "## Operational Runbook\n\n### Reindex\n...[1]...[2]...",     │
│    "citations": [                                                            │
│      {"docId": "...", "chunkId": "...", "snippet": "...", "filename": "..."}│
│    ],                                                                        │
│    "insufficiencies": [                                                      │
│      {"section": "Delete", "missing": "exact SQL", "queriesTried": [...]}   │
│    ],                                                                        │
│    "trace": [...]                                                            │
│  }                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Module Structure

```
backend/apps/agent/
├── __init__.py
├── constraints.py      # Prompt constraint analysis
├── validator.py        # Validation gate for FINAL actions
├── executor_v2.py      # Main agent loop with validation
├── executor.py         # Legacy executor (v1)
├── planner.py          # Plan generation (2-5 steps)
├── tools.py            # search_docs and open_citation
├── views.py            # API endpoints
├── urls.py             # URL routing
└── health.py           # Health check endpoints
```

## Key Components

### 1. Constraint Analyzer (`constraints.py`)

Parses the user's prompt to detect implicit and explicit requirements:

```python
@dataclass
class PromptConstraints:
    min_searches: int = 1
    required_search_topics: List[str] = []
    min_open_citations: int = 0
    requires_exact_quote: bool = False
    exact_quote_indicators: List[str] = []
    requires_conflict_resolution: bool = False
    conflict_resolution_rule: Optional[str] = None
    required_sections: List[str] = []
    requires_insufficiency_disclosure: bool = False
```

**Detection Patterns:**

| Requirement | Pattern Examples |
|-------------|------------------|
| Multiple searches | "separate tool searches", "at least 3 tool calls" |
| Open citations | "open_citation for at least two", "read full text" |
| Exact quotes | "quote exact SQL", "verbatim", "exact line" |
| Conflict resolution | "newest dated document", "resolve conflicts" |
| Insufficiency | "explicitly say 'Insufficient documentation'" |

### 2. Validator (`validator.py`)

The **validation gate** is the key fix for premature finalization. It checks:

```python
def validate_agent_state(
    answer: str,
    citation_refs: List[int],
    constraints: PromptConstraints,
    snapshot: AgentStateSnapshot,
    insufficiencies: Optional[List[Dict]] = None
) -> ValidationResult:
```

**Validation Checks:**

| Check | Description |
|-------|-------------|
| `validate_min_searches` | Ensures required number of search_docs calls |
| `validate_min_open_citations` | Ensures required open_citation calls |
| `validate_citation_references` | No hallucinated [N] markers |
| `validate_grounded_claims` | Technical terms must appear in sources |
| `validate_exact_quote_requirement` | Quoted text matches opened citations |
| `validate_insufficiency_disclosure` | "Insufficient documentation" present when needed |

**Ungrounded Claim Detection:**

The validator maintains a list of suspicious terms that might indicate hallucination:

```python
SUSPICIOUS_TERMS = [
    'pg_reindex', 'reindex', 'vacuum', 'vacuum analyze',
    'kubectl', 'helm', 'docker compose', 'systemctl',
    'drop table', 'truncate', 'alter table',
]
```

If these appear in the answer but not in any retrieved source, the validator flags an "UNGROUNDED_CLAIM" error.

### 3. Executor V2 (`executor_v2.py`)

The main agent loop with validation gates.

**Strict JSON Action Format:**

```json
// TOOL_CALL
{
  "type": "tool_call",
  "tool": "search_docs",
  "input": {"query": "reindex sql"}
}

// FINAL
{
  "type": "final",
  "answer": "Based on [1] and [2]...",
  "used_citations": [
    {"docId": "uuid", "chunkId": "uuid", "chunkIndex": 0}
  ],
  "insufficiencies": [
    {"section": "Delete", "missing": "exact SQL", "queries_tried": ["delete query"]}
  ]
}
```

**Agent State Tracking:**

```python
class AgentState:
    tool_calls_used: int = 0
    search_results: List[SearchResultItem] = []
    opened_citations: List[OpenedCitation] = []
    notes: List[str] = []
    insufficiencies: List[Insufficiency] = []
    _search_queries: List[str] = []  # For validation
```

**Reprompt Strategy:**

When validation fails:

```
VALIDATION FAILED - Your answer does not meet requirements.

ERRORS:
- Required at least 3 separate searches, but only 1 were performed.
- Required to open at least 2 citation(s), but only 0 were opened.

REMAINING TOOL BUDGET: 4 calls

You MUST output a TOOL_CALL to gather more information before finalizing.
```

### 4. Planner (`planner.py`)

Generates 2-5 step execution plans:

```python
PLAN_SYSTEM_PROMPT = """
Your task is to create a SHORT, FOCUSED plan to answer the user's question.

AVAILABLE TOOLS:
1. search_docs(query) - Search the user's documents
2. open_citation(docId, chunkId) - Retrieve full text of a chunk

RULES:
1. Output EXACTLY 2-5 steps
2. Each step must be ONE clear, actionable instruction
3. Steps should reference tools by name
4. The final step should always be about synthesizing/answering

OUTPUT FORMAT:
Return a JSON array of strings, each string being one step.
"""
```

**Plan Parsing Strategies:**

1. JSON array: `["Step 1", "Step 2", "Step 3"]`
2. Numbered list: `1. Step one\n2. Step two`
3. Bullet points: `- Step one\n- Step two`
4. Fallback to default plan if parsing fails

### 5. Tools (`tools.py`)

**search_docs:**
- Searches user's documents using vector similarity
- Returns top-k results (default: 5)
- Scoped to user's documents only
- Returns: `{docId, chunkId, chunkIndex, snippet, score}`

**open_citation:**
- Retrieves full text of a specific chunk
- Validates document ownership
- Returns: `{docId, chunkId, chunkIndex, text, filename}`

**Security:**
- Both tools enforce `owner_user_id` filtering
- Documents must have status `INDEXED`
- UUIDs are validated before database queries

## Hard Limits

| Limit | Value | Purpose |
|-------|-------|---------|
| `MAX_TOOL_CALLS` | 5 | Prevent infinite loops |
| `MAX_ITERATIONS` | 10 | Allow for reprompts within tool budget |
| `MAX_REPROMPTS` | 3 | Prevent reprompt loops |
| `MAX_QUESTION_LENGTH` | 1000 | Bound prompt size |
| `MAX_CONTEXT_CITATIONS` | 5 | Limit context window |
| `MAX_CITATION_TEXT_FOR_LLM` | 2000 | Prevent context overflow |

## API Endpoints

### POST `/api/agent/run`

Synchronous agent execution.

**Request:**
```json
{
  "question": "What are the deployment steps?",
  "mode": "agent",
  "returnTrace": true
}
```

**Response:**
```json
{
  "answer": "Based on [1]...",
  "citations": [...],
  "trace": [...]
}
```

### POST `/api/agent/stream`

Server-Sent Events for real-time updates.

**Events:**
```
event: trace
data: {"type": "plan", "steps": [...]}

event: trace
data: {"type": "tool_call", "tool": "search_docs", "outputSummary": "Found 5 chunks"}

event: trace
data: {"type": "validation", "validationErrors": ["..."]}

event: complete
data: {"answer": "...", "citations": [...], "trace": [...]}
```

## Execution Trace

The trace provides visibility into agent execution:

```python
class TraceType(Enum):
    PLAN = "plan"           # Initial plan generation
    TOOL_CALL = "tool_call" # search_docs or open_citation
    VALIDATION = "validation" # Validation gate result
    REPROMPT = "reprompt"   # Correction sent to LLM
    FINAL = "final"         # Accepted answer
    ERROR = "error"         # Error during execution
```

**Example Trace:**
```json
[
  {"type": "plan", "steps": ["Search for X", "Open citations", "Synthesize"]},
  {"type": "tool_call", "tool": "search_docs", "input": {"query": "X"}, "outputSummary": "Found 5 chunks"},
  {"type": "tool_call", "tool": "open_citation", "input": {"docId": "...", "chunkId": "..."}, "outputSummary": "Retrieved 1.2KB"},
  {"type": "validation", "validationErrors": ["Required 2 citations, got 1"], "notes": "Validation failed (1/3)"},
  {"type": "reprompt", "notes": "MIN_OPEN_CITATIONS_UNMET"},
  {"type": "tool_call", "tool": "open_citation", "outputSummary": "Retrieved 0.8KB"},
  {"type": "final", "notes": "Validated with 2 citations"}
]
```

## Prompt Engineering

### Tool Loop Prompt

The tool loop prompt is carefully structured:

```
STRICT OUTPUT FORMAT:
You MUST output EXACTLY ONE valid JSON object per response.

AVAILABLE TOOLS:
1. search_docs - Input: {"query": "..."}
2. open_citation - Input: {"docId": "FULL-UUID", "chunkId": "FULL-UUID"}
   IMPORTANT: Use COMPLETE UUID strings (36 characters with dashes)

CRITICAL RULES:
1. MUST call open_citation before citing
2. Use FULL docId and chunkId from search results
3. Citation numbers must match opened citations
4. Do NOT include information not in opened citations
5. Include insufficiencies for missing info
6. NEVER invent tools or procedures not in documents

CURRENT CONTEXT:
Plan: ...
Current step: 2 of 4
Question: ...

=== SEARCH RESULTS ===
Query: "reindex sql"
  - operations.md: "To reindex..."
    docId=c5bd8bfc-1234-5678-abcd-1234567890ab
    chunkId=a1b2c3d4-5678-90ab-cdef-1234567890ab

=== OPENED CITATIONS ===
[1] operations.md (chunk 0):
Full text here...

TOOL BUDGET: 3 calls remaining
SEARCHES DONE: 2
CITATIONS OPENED: 1

REQUIREMENTS:
- Perform at least 3 separate searches
- Open at least 2 citation(s)
- Quote exact text for: SQL statement

Output your next action as JSON:
```

### Synthesis Prompt

When forced to synthesize (no explicit FINAL):

```
Based on the gathered information, answer the question.

STRICT RULES:
1. Use ONLY the provided context
2. Cite sources using [1], [2] notation
3. If context doesn't answer, say: "I don't know based on the provided documents."
4. Explicitly state "Insufficient documentation" for missing parts
5. Be factual and concise

Question: ...

Available sources:
[1] file.md (chunk 0):
...

[2] file2.md (chunk 1):
...

Answer (use [1], [2] to cite):
```

## Error Handling

### Common Errors

| Error | Cause | Resolution |
|-------|-------|------------|
| `UUID is not valid` | LLM truncated docId/chunkId | Fixed by showing full UUIDs in prompt |
| `timed out` | LLM took too long | Configurable timeouts in settings |
| `Validation failed` | Constraints not met | Reprompt with errors |
| `Tool budget exhausted` | MAX_TOOL_CALLS reached | Force synthesis with insufficiencies |

### Timeout Configuration

```python
# settings.py
OLLAMA_PLAN_TIMEOUT = int(os.getenv('OLLAMA_PLAN_TIMEOUT', '300'))   # 5 min
OLLAMA_CHAT_TIMEOUT = int(os.getenv('OLLAMA_CHAT_TIMEOUT', '600'))   # 10 min
OLLAMA_EMBED_TIMEOUT = int(os.getenv('OLLAMA_EMBED_TIMEOUT', '120')) # 2 min
```

## Testing

### Unit Tests (`tests/test_agent_v2.py`)

**Constraint Analyzer Tests:**
- `test_detects_multiple_search_requirement`
- `test_detects_open_citation_requirement`
- `test_detects_exact_quote_requirement`
- `test_simple_prompt_has_minimal_constraints`

**Validator Tests:**
- `test_validates_min_searches_failure`
- `test_validates_min_open_citations_failure`
- `test_validates_grounded_claims`
- `test_valid_state_passes`

**Integration Tests:**
- `test_validator_rejects_early_final` - Reproduces the failure case
- `test_agent_completes_with_proper_citations`

### Running Tests

```bash
docker exec docuchat-backend pytest tests/test_agent_v2.py -v
```

## Known Limitations

1. **Context Window**: Large documents may be truncated to fit context limits
2. **Tool Limit**: Complex queries may require more than 5 tool calls
3. **Validation Heuristics**: Constraint detection uses pattern matching, may miss edge cases
4. **LLM Compliance**: Model may not always follow JSON format strictly

## Future Improvements

1. **Parallel Tool Execution**: Execute independent searches concurrently
2. **Smarter Constraint Detection**: Use LLM to analyze prompt requirements
3. **Adaptive Tool Budget**: Adjust limits based on query complexity
4. **Citation Deduplication**: Avoid opening the same chunk twice
5. **Streaming Synthesis**: Stream final answer generation token-by-token
