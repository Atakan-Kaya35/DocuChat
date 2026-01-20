# DocuChat API Reference

This document defines the REST API contracts for DocuChat.

---

> [!NOTE]
> **API Path Convention:** The spec originally defined `POST /api/chat/ask` for the RAG endpoint. During implementation, this was changed to `POST /api/rag/ask` for clearer naming semantics:
> - `/api/rag/*` — Retrieval-Augmented Generation (retrieve + answer)
> - `/api/agent/*` — Multi-step agent with planning and tools
> - `/api/docs/*` — Document management (CRUD)
>
> This separation makes API organization clearer and distinguishes the simpler RAG flow from the more complex agent flow. The functionality is identical to what the spec describes for `/api/chat/ask`.

---

## Table of Contents

1. [Base Conventions](#base-conventions)
2. [Authentication](#authentication)
3. [Error Handling](#error-handling)
4. [Rate Limiting](#rate-limiting)
5. [Documents API](#documents-api)
6. [RAG API](#rag-api)
7. [Agent API](#agent-api)
8. [Health Endpoints](#health-endpoints)

---

## Base Conventions

### Base URL

All API endpoints are prefixed with `/api`:

```
http://localhost/api/...
```

### Content Types

- **Request:** `application/json` (except file uploads)
- **Response:** `application/json`
- **File Upload:** `multipart/form-data`

### Date/Time Format

All timestamps use ISO 8601 format in UTC:

```
2026-01-15T10:30:00Z
```

---

## Authentication

All API endpoints (except `/health/*`) require a valid JWT token from Keycloak.

### Request Header

```
Authorization: Bearer <access_token>
```

### Token Acquisition

Obtain tokens via Keycloak OIDC:

```bash
# Token endpoint
POST /auth/realms/docuchat/protocol/openid-connect/token

# Example (for testing)
curl -X POST http://localhost/auth/realms/docuchat/protocol/openid-connect/token \
  -d "client_id=docuchat-frontend" \
  -d "grant_type=password" \
  -d "username=testuser" \
  -d "password=testpassword"
```

### User Identity

The `sub` claim in the JWT is used as `owner_user_id` for all data scoping.

---

## Error Handling

All errors follow a consistent format:

```json
{
  "error": "Human-readable error message",
  "code": "ERROR_CODE",
  "details": {}  // Optional, for validation errors
}
```

### Standard Error Codes

| HTTP Status | Code | Description |
|-------------|------|-------------|
| 400 | `VALIDATION_ERROR` | Invalid request data |
| 401 | `AUTH_FAILED` | Missing or invalid token |
| 403 | `FORBIDDEN` | Access denied to resource |
| 404 | `NOT_FOUND` | Resource does not exist |
| 413 | `FILE_TOO_LARGE` | Upload exceeds size limit |
| 415 | `UNSUPPORTED_TYPE` | File type not supported |
| 429 | `RATE_LIMITED` | Too many requests |
| 500 | `INTERNAL_ERROR` | Server error |
| 503 | `LLM_UNAVAILABLE` | Ollama service unavailable |

### Validation Error Example

```json
{
  "error": "Validation failed",
  "code": "VALIDATION_ERROR",
  "details": {
    "question": "Question is required",
    "topK": "Must be between 1 and 10"
  }
}
```

---

## Rate Limiting

Rate limits are applied per-user (based on JWT `sub` claim).

### Limits

| Endpoint | Limit | Window |
|----------|-------|--------|
| `POST /api/docs/upload` | 5 requests | 1 minute |
| `POST /api/docs/upload` | 50 requests | 1 hour |
| `POST /api/rag/ask` | 20 requests | 1 minute |
| `POST /api/agent/run` | 20 requests | 1 minute |

### Rate Limit Headers

```
X-RateLimit-Limit: 20
X-RateLimit-Remaining: 15
X-RateLimit-Reset: 1705312800
```

### Rate Limited Response (429)

```json
{
  "error": "Rate limit exceeded. Try again in 30 seconds.",
  "code": "RATE_LIMITED",
  "retryAfter": 30
}
```

```
Retry-After: 30
```

---

## Documents API

### Get Current User

`GET /api/me`

Returns information about the authenticated user.

**Request:**
```bash
curl -X GET http://localhost/api/me \
  -H "Authorization: Bearer <token>"
```

**Response (200 OK):**
```json
{
  "id": "keycloak-user-uuid",
  "username": "testuser",
  "email": "user@example.com",
  "roles": ["user"]
}
```

### Upload Document

`POST /api/docs/upload`

Upload a document for indexing. Idempotent - re-uploading identical content returns existing document.

**Request:**
```bash
curl -X POST http://localhost/api/docs/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@document.pdf"
```

**Supported Types:** PDF, TXT, MD  
**Max Size:** 10 MB

**Response (201 Created):**
```json
{
  "documentId": "550e8400-e29b-41d4-a716-446655440000",
  "jobId": "550e8400-e29b-41d4-a716-446655440001",
  "status": "QUEUED",
  "filename": "document.pdf"
}
```

**Response (200 OK - Duplicate):**
```json
{
  "documentId": "550e8400-e29b-41d4-a716-446655440000",
  "jobId": "550e8400-e29b-41d4-a716-446655440001",
  "status": "INDEXED",
  "filename": "document.pdf",
  "duplicate": true,
  "message": "Document with identical content already exists"
}
```

**Error (415 Unsupported Type):**
```json
{
  "error": "Unsupported file type: application/zip",
  "code": "UNSUPPORTED_TYPE",
  "supportedTypes": ["application/pdf", "text/plain", "text/markdown"]
}
```

**Error (413 File Too Large):**
```json
{
  "error": "File size exceeds limit of 10 MB",
  "code": "FILE_TOO_LARGE",
  "maxSizeBytes": 10485760
}
```

### List Documents

`GET /api/docs`

List all documents for the authenticated user.

**Request:**
```bash
curl -X GET http://localhost/api/docs \
  -H "Authorization: Bearer <token>"
```

**Response (200 OK):**
```json
{
  "documents": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "filename": "document.pdf",
      "contentType": "application/pdf",
      "sizeBytes": 102400,
      "status": "INDEXED",
      "createdAt": "2026-01-15T10:30:00Z",
      "updatedAt": "2026-01-15T10:31:00Z",
      "latestJob": {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "status": "INDEXED",
        "stage": "COMPLETED",
        "progress": 100
      }
    }
  ]
}
```

### Get Document Chunk

`GET /api/docs/<docId>/chunks/<chunkIndex>`

Retrieve a specific chunk from a document (for viewing citations).

**Request:**
```bash
curl -X GET http://localhost/api/docs/550e8400.../chunks/3 \
  -H "Authorization: Bearer <token>"
```

**Response (200 OK):**
```json
{
  "docId": "550e8400-e29b-41d4-a716-446655440000",
  "chunkId": "550e8400-e29b-41d4-a716-446655440010",
  "chunkIndex": 3,
  "text": "Full text of the chunk...",
  "filename": "document.pdf"
}
```

**Error (403 Forbidden):**
```json
{
  "error": "You do not have access to this document",
  "code": "FORBIDDEN"
}
```
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
```bash
curl -X POST http://localhost/api/rag/ask \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the main topic?"}'
```

```json
{
  "question": "What is the main topic?",
  "topK": 5,
  "temperature": 0.2,
  "maxTokens": 500
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `question` | string | Yes | - | The question to answer |
| `topK` | integer | No | 5 | Number of chunks to retrieve (1-10) |
| `temperature` | number | No | 0.2 | LLM temperature (0.0-1.0) |
| `maxTokens` | integer | No | 500 | Max response tokens |

**Response (200 OK):**
```json
{
  "answer": "Based on the documents, the main topic is...[1]",
  "citations": [
    {
      "docId": "550e8400-e29b-41d4-a716-446655440000",
      "chunkId": "550e8400-e29b-41d4-a716-446655440010",
      "chunkIndex": 3,
      "snippet": "The document discusses the importance of...",
      "score": 0.1234,
      "documentTitle": "report.pdf"
    }
  ],
  "model": "llama3.2"
}
```

**Response (200 OK - Insufficient Sources):**

When no relevant documents are found or content doesn't answer the question:

```json
{
  "answer": "I don't know based on the provided documents.",
  "citations": [],
  "model": "llama3.2"
}
```

This response is expected when:
- No documents have been uploaded
- Uploaded documents don't contain relevant information
- The question is about topics not covered in the documents

### Retrieve Chunks

`POST /api/rag/retrieve`

Retrieve relevant chunks without LLM generation (for testing/debugging).

**Request:**
```bash
curl -X POST http://localhost/api/rag/retrieve \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "search terms"}'
```

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
  "citations": [
    {
      "docId": "550e8400-e29b-41d4-a716-446655440000",
      "chunkId": "550e8400-e29b-41d4-a716-446655440010",
      "chunkIndex": 3,
      "snippet": "...",
      "score": 0.1234,
      "documentTitle": "report.pdf"
    }
  ]
}
```

---

## Agent API

### Run Agent

`POST /api/agent/run`

Execute bounded agent loop with planning and tool use.

**Request:**
```bash
curl -X POST http://localhost/api/agent/run \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the key findings?", "returnTrace": true}'
```

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
  "answer": "Based on my analysis of your documents, the key findings are...[1][2]",
  "citations": [
    {
      "docId": "550e8400-e29b-41d4-a716-446655440000",
      "chunkId": "550e8400-e29b-41d4-a716-446655440010",
      "chunkIndex": 3,
      "snippet": "The study found that...",
      "score": 0.1234,
      "documentTitle": "report.pdf"
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
      "notes": "Synthesized from 2 citations"
    }
  ]
}
```

**Response (200 OK - Insufficient Sources):**

When the agent cannot find relevant information:

```json
{
  "answer": "I don't know based on the provided documents.",
  "citations": [],
  "trace": [
    {
      "type": "plan",
      "steps": [
        "Search for information about the topic",
        "Synthesize answer from findings"
      ]
    },
    {
      "type": "tool_call",
      "tool": "search_docs",
      "input": { "query": "mars capital city" },
      "outputSummary": "No results found"
    },
    {
      "type": "final",
      "notes": "No relevant sources found"
    }
  ]
}
```

The grounding contract:
- If sources exist and are relevant → cite them
- If sources don't contain the answer → admit "I don't know"
- Never make up information not in the documents

### Agent Hard Limits

These limits are enforced to ensure predictable, bounded execution:

| Limit | Value | Rationale |
|-------|-------|-----------|
| Plan steps | 2–5 | Forces focused planning |
| Max tool calls | 5 | Prevents runaway loops |
| Question length | 1000 chars | Bounded input |
| Query length (search) | 500 chars | Focused searches |
| Max search results | 5 | Top-k retrieval |
| Max citation text | 1500 chars | Bounded context per LLM call |

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