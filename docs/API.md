# DocuChat API Reference

This document defines the REST API contracts for DocuChat.

---

## Table of Contents

1. [Authentication](#authentication)
2. [Documents API](#documents-api)
3. [RAG API](#rag-api)
4. [Agent API](#agent-api)
5. [Health Endpoints](#health-endpoints)

---

## Authentication

All API endpoints (except health checks) require a valid JWT token from Keycloak.

**Header:**
```
Authorization: Bearer <access_token>
```

**Error Response (401):**
```json
{
  "error": "Invalid or expired token",
  "code": "AUTH_FAILED"
}
```

---

## Documents API

### Upload Document

`POST /api/docs/upload`

Upload a document for indexing. Idempotent - re-uploading identical content returns existing document.

**Request:** `multipart/form-data`
- `file`: The document file (PDF, TXT, MD)

**Response (201 Created):**
```json
{
  "documentId": "uuid",
  "jobId": "uuid",
  "status": "QUEUED",
  "filename": "document.pdf"
}
```

**Response (200 OK - Duplicate):**
```json
{
  "documentId": "uuid",
  "jobId": "uuid",
  "status": "INDEXED",
  "filename": "document.pdf",
  "duplicate": true,
  "message": "Document with identical content already exists"
}
```

### List Documents

`GET /api/docs`

List all documents for the authenticated user.

**Response (200 OK):**
```json
{
  "documents": [
    {
      "id": "uuid",
      "filename": "document.pdf",
      "status": "INDEXED",
      "createdAt": "2024-01-15T10:30:00Z"
    }
  ]
}
```

### Get Document

`GET /api/docs/<id>`

Get details of a specific document.

**Response (200 OK):**
```json
{
  "id": "uuid",
  "filename": "document.pdf",
  "status": "INDEXED",
  "contentType": "application/pdf",
  "sizeBytes": 1048576,
  "createdAt": "2024-01-15T10:30:00Z"
}
```

---

## RAG API

### Ask Question

`POST /api/rag/ask`

Full RAG pipeline: retrieve relevant chunks + LLM generation.

**Request:**
```json
{
  "question": "What is the main topic?",
  "topK": 5,
  "temperature": 0.2,
  "maxTokens": 500
}
```

**Response (200 OK):**
```json
{
  "answer": "Based on the documents, the main topic is...[1]",
  "citations": [
    {
      "docId": "uuid",
      "chunkId": "uuid",
      "chunkIndex": 3,
      "snippet": "...",
      "score": 0.1234,
      "documentTitle": "file.pdf"
    }
  ],
  "model": "llama3.2"
}
```

### Retrieve Chunks

`POST /api/rag/retrieve`

Retrieve relevant chunks without LLM generation (for testing).

**Request:**
```json
{
  "query": "search terms",
  "topK": 5
}
```

**Response (200 OK):**
```json
{
  "query": "search terms",
  "citations": [...]
}
```

---

## Agent API

### Run Agent

`POST /api/agent/run`

Execute bounded agent loop with planning and tool use.

**Request:**
```json
{
  "question": "What are the key findings across all my documents?",
  "mode": "agent",
  "returnTrace": true
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `question` | string | Yes | - | The question to answer (max 1000 chars) |
| `mode` | string | No | `"agent"` | Execution mode (only "agent" supported) |
| `returnTrace` | boolean | No | `false` | Include execution trace in response |

**Response (200 OK):**
```json
{
  "answer": "Based on my analysis of your documents...",
  "citations": [
    {
      "docId": "uuid",
      "chunkId": "uuid",
      "chunkIndex": 3,
      "snippet": "...",
      "score": 0.1234,
      "documentTitle": "file.pdf"
    }
  ],
  "trace": [
    {
      "type": "plan",
      "steps": [
        "Search for key findings across documents",
        "Open top citations to gather details",
        "Synthesize answer with citations"
      ]
    },
    {
      "type": "tool_call",
      "tool": "search_docs",
      "input": { "query": "key findings conclusions" },
      "outputSummary": "Found 5 relevant chunks"
    },
    {
      "type": "tool_call",
      "tool": "open_citation",
      "input": { "docId": "...", "chunkId": "..." },
      "outputSummary": "Retrieved 2.3KB of text"
    },
    {
      "type": "final",
      "notes": "Completed in 3 tool calls"
    }
  ]
}
```

### Agent Hard Limits

These limits are enforced to ensure predictable, bounded execution:

| Limit | Value | Rationale |
|-------|-------|-----------|
| Plan steps | 2–5 | Forces focused planning |
| Max tool calls | 5 | Prevents runaway loops |
| Question length | 1000 chars | Bounded input |
| Query length (search) | 500 chars | Focused searches |
| Max search results | 5 | Top-k retrieval |
| Max citation text | 5000 chars | Bounded context |

### Available Tools

The agent has access to exactly two tools:

#### `search_docs`

Search user's documents for relevant content.

**Input:**
```json
{
  "query": "search terms"
}
```

**Output:**
```json
{
  "results": [
    {
      "docId": "uuid",
      "chunkId": "uuid",
      "chunkIndex": 2,
      "snippet": "First 200 chars of text...",
      "score": 0.12
    }
  ]
}
```

#### `open_citation`

Retrieve full text of a specific citation (with ownership check).

**Input:**
```json
{
  "docId": "uuid",
  "chunkId": "uuid"
}
```

**Output:**
```json
{
  "docId": "uuid",
  "chunkId": "uuid",
  "text": "Full chunk text (up to 5000 chars)...",
  "filename": "document.pdf",
  "chunkIndex": 2
}
```

### Citation Grounding

All citations in the response are verified to exist in the database:

1. The agent only returns citations from actually opened chunks
2. Any `[N]` references in the answer that don't match opened citations are stripped
3. The `citations` array contains only real, accessible chunks

This prevents hallucinated citations and ensures every reference is clickable.

### Error Responses

**400 Bad Request:**
```json
{
  "error": "Question is required",
  "code": "VALIDATION_ERROR"
}
```

**429 Rate Limited:**
```json
{
  "error": "Rate limit exceeded",
  "code": "RATE_LIMITED",
  "retryAfter": 30
}
```

**503 Service Unavailable:**
```json
{
  "error": "LLM service temporarily unavailable",
  "code": "LLM_UNAVAILABLE",
  "retryable": true
}
```

---

## Health Endpoints

### Liveness

`GET /healthz`

Returns 200 if the process is running.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### Readiness

`GET /readyz`

Returns 200 if all dependencies are reachable.

**Response (200 OK):**
```json
{
  "status": "ready",
  "timestamp": "2024-01-15T10:30:00Z",
  "checks": {
    "postgres": "ok",
    "redis": "ok",
    "ollama": "ok"
  }
}
```

**Response (503 Not Ready):**
```json
{
  "status": "not_ready",
  "checks": {
    "postgres": "ok",
    "redis": "error: connection refused",
    "ollama": "ok"
  }
}
```