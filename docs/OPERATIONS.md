# DocuChat Operations Guide

This document contains operational procedures, policies, and runbooks for DocuChat.

---

## Table of Contents

1. [Idempotency Policy](#idempotency-policy)
2. [Retry & Backoff Policy](#retry--backoff-policy)
3. [Rate Limiting](#rate-limiting)
4. [Audit Logging](#audit-logging)
5. [Health Checks](#health-checks)
6. [Agent Limits](#agent-limits)
7. [Runbooks](#runbooks)

---

## Idempotency Policy

### What Counts as "Same Document"?

A document is considered **the same** when:
- Same `owner_user_id` (Keycloak sub claim)
- Same `content_hash` (SHA-256 of file bytes)

**Key principle: Content wins over filename.**

| Scenario | Behavior |
|----------|----------|
| Same filename, same content | Same document (returns existing) |
| Same filename, different content | New document (different hash) |
| Different filename, same content | Same document (content wins) |
| Same content, different user | Different documents (user-scoped) |

### Re-upload Behavior

When a user uploads a file with matching `(owner_user_id, content_hash)`:

1. **Return existing document record** - no duplicate created
2. **Return current status** - INDEXED, INDEXING, QUEUED, or FAILED
3. **Return existing jobId** if still processing
4. **Do NOT create new IndexJob** unless explicitly requesting reindex

This prevents:
- Duplicate storage consumption
- Duplicate embeddings in vector DB
- Wasted compute on re-indexing identical content

### Implementation

- `content_hash` column: SHA-256 hex string (64 chars)
- Database constraint: `UNIQUE(owner_user_id, content_hash)`
- Hash computed during upload streaming (memory efficient)
- Check performed before INSERT (atomic with constraint as backup)

---

## Retry & Backoff Policy

### Design Principles

1. **Fail fast for user-facing requests** - don't block HTTP response
2. **Retry with backoff for background tasks** - worker is patient
3. **Bounded retries** - don't retry forever; give up and mark FAILED
4. **Distinguish retriable from non-retriable** - 5xx vs 4xx, timeouts vs validation

### Retriable vs Non-Retriable Errors

| Error Type | Retriable | Example |
|------------|-----------|---------|
| Network timeout | Yes | Ollama connection refused |
| 5xx from LLM | Yes | Ollama 503 overloaded |
| 4xx from LLM | No | Model not found |
| Empty response | Yes | LLM returned empty embedding |
| Validation error | No | Invalid document format |
| Out of memory | No | Document too large to process |

### Retry Parameters

#### Embedding Generation (Indexing Worker)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max retries | 3 | Total 4 attempts (1 initial + 3 retries) |
| Initial backoff | 2 seconds | Let Ollama recover |
| Backoff multiplier | 2x | Exponential: 2s → 4s → 8s |
| Max backoff | 30 seconds | Don't wait forever |
| Jitter | ±25% | Prevent thundering herd |

Total max wait time: ~14 seconds before final failure

#### Answer Generation (Ask Endpoint)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max retries | 2 | User is waiting, be quick |
| Initial backoff | 1 second | Fast first retry |
| Backoff multiplier | 2x | 1s → 2s |
| Max backoff | 5 seconds | User patience limit |
| Jitter | ±10% | Minimal variance |

Total max wait time: ~3 seconds before error response

### Failure States

#### Worker (Indexing) Failures

After all retries exhausted:
1. Set `IndexJob.status = FAILED`
2. Store error message in `IndexJob.error_message`
3. Log structured error with attempt count
4. WebSocket notification sent with failure status
5. **Do NOT delete the document** - user can retry manually

#### Ask Endpoint Failures

After all retries exhausted:
1. Return HTTP 503 Service Unavailable
2. Include `Retry-After` header (suggest 30 seconds)
3. Include error code: `LLM_UNAVAILABLE`
4. Log structured error with context

### Circuit Breaker (Future)

Not implemented in v1, but planned:
- Track failure rate over sliding window
- Open circuit if >50% failures in 1 minute
- Return 503 immediately while circuit is open
- Half-open after 30 seconds to test recovery

---

## Rate Limiting

### Design Principles

1. **Protect shared resources** - Ollama is single-threaded, slow
2. **Per-user fairness** - one user can't starve others
3. **Graceful degradation** - return 429 with Retry-After header
4. **Transparency** - include rate limit headers in all responses

### Rate Limit Configuration

#### Upload Endpoint (`POST /api/docs/upload`)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Algorithm | Fixed window | Simple, predictable |
| Window size | 1 minute | Short enough to recover |
| Max requests | 10/user/minute | ~6 seconds between uploads minimum |
| Max burst | 5 | Allow short bursts |

#### Ask Endpoint (`POST /api/rag/ask`)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Algorithm | Token bucket | Smooth rate limiting |
| Rate | 12/user/minute | 1 request per 5 seconds average |
| Bucket capacity | 5 | Allow short bursts of questions |
| Refill rate | 0.2/second | 12 per minute |

### Response Headers

All rate-limited endpoints include:

```
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 7
X-RateLimit-Reset: 1699999999
```

### Rate Limit Exceeded Response

HTTP 429 with body:

```json
{
  "error": "Rate limit exceeded",
  "code": "RATE_LIMITED",
  "retryAfter": 45
}
```

And header:
```
Retry-After: 45
```

### Implementation

- Backend: Redis-based rate limiter
- Key format: `ratelimit:{endpoint}:{user_id}`
- TTL: Window size + small buffer
- Atomic operations via Lua script

### Bypass (Development Only)

For development/testing, rate limiting can be disabled:

```bash
export DISABLE_RATE_LIMITING=true
```

**Never disable in production.**

---

## Audit Logging

### Design Principles

1. **Metadata only** - log what happened, not sensitive content
2. **Structured JSON** - machine-parseable for analysis
3. **Consistent schema** - all audit events share common fields
4. **Security focus** - auth events, data access, mutations

### Audit Event Schema

All audit log entries are structured JSON with these fields:

```json
{
  "timestamp": "2024-01-15T10:30:00.000Z",
  "event_type": "document.uploaded",
  "user_id": "abc123",
  "request_id": "req-uuid",
  "client_ip": "192.168.1.1",
  "metadata": {
    "document_id": "doc-uuid",
    "filename": "report.pdf",
    "size_bytes": 1048576
  },
  "outcome": "success"
}
```

### Event Types

| Event Type | Description | Metadata Fields |
|------------|-------------|-----------------|
| `auth.token_validated` | JWT validated | iss, exp, scope |
| `auth.token_rejected` | JWT validation failed | reason |
| `document.uploaded` | New document uploaded | document_id, filename, size_bytes, content_hash |
| `document.duplicate` | Duplicate upload detected | document_id, existing_id |
| `document.deleted` | Document deleted | document_id |
| `indexing.started` | Indexing job started | job_id, document_id |
| `indexing.completed` | Indexing successful | job_id, document_id, chunk_count |
| `indexing.failed` | Indexing failed | job_id, document_id, error |
| `rag.query` | User asked a question | question_length, top_k, citation_count |
| `ratelimit.exceeded` | Rate limit hit | endpoint, limit, window |

### What NOT to Log

- Full document content
- Full question/answer text
- Personally identifiable info (PII)
- JWT tokens or secrets
- Query embeddings

### Log Output

Audit logs are written via Python's logging module to:
1. **stdout** (for container logs / CloudWatch / Loki)
2. **Structured JSON format** (not plain text)

### Logger Configuration

```python
LOGGING = {
    'handlers': {
        'audit': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
        },
    },
    'loggers': {
        'audit': {
            'handlers': ['audit'],
            'level': 'INFO',
        },
    },
}
```

### Retention

Audit log retention is handled by the log aggregation system:
- **Development**: None (ephemeral)
- **Production**: 90 days minimum
- **Security events**: 1 year

---

## Health Checks

### Endpoints

| Endpoint | Purpose | Auth Required |
|----------|---------|---------------|
| `GET /healthz` | Liveness probe (is the process running?) | No |
| `GET /readyz` | Readiness probe (can we serve traffic?) | No |

### /healthz (Liveness)

Returns 200 OK if the Django process is running. Used by Kubernetes/Docker for restart decisions.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:00.000Z"
}
```

This endpoint does NOT check dependencies. If the process can respond, it's alive.

### /readyz (Readiness)

Returns 200 OK only if all critical dependencies are reachable. Used to determine if the pod should receive traffic.

**Checks performed:**
1. **PostgreSQL** - Can execute `SELECT 1`
2. **Redis** - Can execute `PING`
3. **Ollama** - Can reach `/api/version` (optional, degrades gracefully)

**Response (200 OK):**
```json
{
  "status": "ready",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "checks": {
    "postgres": "ok",
    "redis": "ok",
    "ollama": "ok"
  }
}
```

**Response (503 Service Unavailable):**
```json
{
  "status": "not_ready",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "checks": {
    "postgres": "ok",
    "redis": "error: connection refused",
    "ollama": "ok"
  }
}
```

### Worker Health

The indexing worker has a separate health mechanism:

1. **Heartbeat file** - Worker touches `/tmp/worker_heartbeat` periodically
2. **Docker health check** - Checks heartbeat file age < 30 seconds

```yaml
# docker-compose.yml
worker:
  healthcheck:
    test: ["CMD", "test", "-f", "/tmp/worker_heartbeat", "-a", "$(find /tmp/worker_heartbeat -mmin -1)"]
    interval: 30s
    timeout: 5s
    retries: 3
```

### Probing Strategy

| Probe | Initial Delay | Period | Timeout | Failure Threshold |
|-------|---------------|--------|---------|-------------------|
| Liveness | 10s | 10s | 3s | 3 |
| Readiness | 5s | 5s | 3s | 3 |

---

## Agent Limits

The agent mode provides bounded, predictable execution with hard limits.

### Hard Limits

| Limit | Value | Rationale |
|-------|-------|-----------|
| Plan steps | 2–5 | Forces focused, actionable plans |
| Max tool calls per run | 5 | Prevents runaway loops |
| Max iterations | 5 | Same as tool calls for MVP |
| Question length | 1000 chars | Bounded input size |
| Search query length | 500 chars | Focused searches |
| Max search results (k) | 5 | Top-k retrieval |
| Max citation text for LLM | 1500 chars | Avoid prompt bloat |
| Rolling context window | 3 citations | Only last N opened citations |

### Available Tools

The agent has access to exactly **two** tools:

1. **`search_docs`** - Semantic search across user's documents
2. **`open_citation`** - Retrieve full text of a specific chunk

No additional tools are available. This is intentional for:
- Debuggability (small, fixed action space)
- Security (no arbitrary code execution)
- Predictability (bounded behavior)

### Strict Action Format

The agent uses a strict TOOL_CALL/FINAL protocol:

```
TOOL_CALL {"tool": "search_docs", "input": {"query": "..."}}
TOOL_CALL {"tool": "open_citation", "input": {"docId": "...", "chunkId": "..."}}
FINAL {"answer": "...", "citations": [1, 2]}
```

**Malformed output handling:**
- If model outputs anything else, re-prompt once
- After 2 malformed outputs, force synthesis
- Caps are always obeyed regardless of model behavior

### Tool Result Compression

To avoid prompt bloat:
- **Search results**: Only `docId/chunkId/snippet/score` (no full text)
- **Open citation**: Text capped at 1500 chars
- **Rolling context**: Only last 3 opened citations kept

### Citation Grounding

All citations in the final response are grounded:
1. Citations reference actual opened chunks (by number)
2. Hallucinated `[N]` references are stripped from answer
3. Only citations that exist in DB are returned

### Execution Flow

```
1. Plan      → LLM generates 2-5 step plan
2. Execute   → Agent calls tools via TOOL_CALL/FINAL protocol (max 5)
3. Ground    → Map citation refs to actual opened chunks
4. Return    → Answer + grounded citations + optional trace
```

### Fallback Behavior

If plan parsing fails, agent uses default 3-step plan:
1. Search documents for relevant information
2. Open best citations to read details  
3. Synthesize answer from gathered context

### Trace Structure (when `returnTrace=true`)

```json
{
  "trace": [
    {"type": "plan", "steps": ["Search...", "Open...", "Synthesize..."]},
    {"type": "tool_call", "tool": "search_docs", "input": {...}, "outputSummary": "..."},
    {"type": "tool_call", "tool": "open_citation", "input": {...}, "outputSummary": "..."},
    {"type": "final", "notes": "Synthesized from 2 citations"}
  ]
}
```

The trace is minimal:
- No full document text leaked
- Only tool names, inputs, and summaries
- Errors included with truncated messages

### Timeout

Agent execution has a 60-second timeout. If exceeded:
- Partial results returned if available
- Error logged with trace
- HTTP 504 returned if no partial results

---

## Runbooks

### Runbook: Reindex a Document

**When to use:** Document content was updated, indexing failed, or embeddings need refresh.

**Prerequisites:**
- Access to the database (psql or Django shell)
- Worker container is running

**Steps:**

1. **Find the document ID**
   ```bash
   docker exec -it docuchat-backend python manage.py shell
   ```
   ```python
   from apps.docs.models import Document
   doc = Document.objects.get(filename='myfile.pdf', owner_user_id='user123')
   print(f"Document ID: {doc.id}")
   ```

2. **Delete existing chunks** (will be recreated)
   ```python
   from apps.indexing.models import DocumentChunk
   deleted, _ = DocumentChunk.objects.filter(document=doc).delete()
   print(f"Deleted {deleted} chunks")
   ```

3. **Reset document and job status**
   ```python
   from apps.docs.models import IndexJob, DocumentStatus, IndexJobStatus
   
   # Reset document status
   doc.status = DocumentStatus.QUEUED
   doc.save(update_fields=['status', 'updated_at'])
   
   # Create new index job
   job = IndexJob.objects.create(
       document=doc,
       status=IndexJobStatus.QUEUED,
       stage='RECEIVED',
       progress=0
   )
   print(f"Created job {job.id}")
   ```

4. **Verify worker picks up the job**
   ```bash
   docker logs -f docuchat-worker
   ```

5. **Verify completion**
   ```python
   job.refresh_from_db()
   print(f"Job status: {job.status}, progress: {job.progress}%")
   ```

---

### Runbook: Delete a Document

**When to use:** User requests deletion, or document needs complete removal.

**Prerequisites:**
- Access to the database
- Access to file storage

**Steps:**

1. **Find the document**
   ```python
   from apps.docs.models import Document
   doc = Document.objects.get(id='<document-uuid>')
   ```

2. **Delete chunks from vector DB**
   ```python
   from apps.indexing.models import DocumentChunk
   deleted, _ = DocumentChunk.objects.filter(document=doc).delete()
   print(f"Deleted {deleted} chunks")
   ```

3. **Delete index jobs**
   ```python
   from apps.docs.models import IndexJob
   deleted, _ = IndexJob.objects.filter(document=doc).delete()
   print(f"Deleted {deleted} index jobs")
   ```

4. **Delete file from storage**
   ```python
   from pathlib import Path
   from django.conf import settings
   
   file_path = Path(settings.UPLOAD_ROOT) / doc.storage_path
   if file_path.exists():
       file_path.unlink()
       print(f"Deleted file: {file_path}")
   
   # Also delete extracted text if exists
   extracted_path = Path(settings.EXTRACTED_ROOT) / f"{doc.id}.txt"
   if extracted_path.exists():
       extracted_path.unlink()
       print(f"Deleted extracted: {extracted_path}")
   ```

5. **Delete document record**
   ```python
   doc.delete()
   print("Document deleted")
   ```

6. **Verify deletion**
   ```python
   from apps.docs.models import Document
   try:
       Document.objects.get(id='<document-uuid>')
       print("ERROR: Document still exists!")
   except Document.DoesNotExist:
       print("Confirmed: Document deleted")
   ```

---

### Runbook: Change LLM/Embedding Model

**When to use:** Switching to a different model (e.g., gemma → llama3).

**Impact:** All existing embeddings become incompatible. Full reindex required.

**Steps:**

1. **Stop the worker**
   ```bash
   docker stop docuchat-worker
   ```

2. **Pull new model in Ollama**
   ```bash
   docker exec -it docuchat-ollama ollama pull llama3.2
   docker exec -it docuchat-ollama ollama pull nomic-embed-text  # if changing embedding model
   ```

3. **Update configuration**
   Edit `backend/.env.sample`:
   ```bash
   OLLAMA_CHAT_MODEL=llama3.2
   # OLLAMA_EMBED_MODEL=nomic-embed-text  # if changing
   ```

4. **If embedding model changed: Delete ALL chunks**
   ```bash
   docker exec -it docuchat-backend python manage.py shell
   ```
   ```python
   from apps.indexing.models import DocumentChunk
   count = DocumentChunk.objects.count()
   DocumentChunk.objects.all().delete()
   print(f"Deleted {count} chunks")
   ```

5. **Reset all documents to QUEUED**
   ```python
   from apps.docs.models import Document, IndexJob, DocumentStatus, IndexJobStatus
   
   docs = Document.objects.filter(status=DocumentStatus.INDEXED)
   for doc in docs:
       doc.status = DocumentStatus.QUEUED
       doc.save(update_fields=['status', 'updated_at'])
       
       # Create new index job
       IndexJob.objects.create(
           document=doc,
           status=IndexJobStatus.QUEUED,
           stage='RECEIVED',
           progress=0
       )
   print(f"Queued {docs.count()} documents for reindexing")
   ```

6. **Restart services**
   ```bash
   docker-compose up -d backend worker
   ```

7. **Monitor reindexing**
   ```bash
   docker logs -f docuchat-worker
   ```

---

### Runbook: Keycloak User/Realm Management

**When to use:** Adding users, updating realm settings, or rotating keys.

#### Add a New User

1. **Access Keycloak Admin Console**
   - URL: `http://localhost:80/auth/admin`
   - Login with admin credentials from `.env`

2. **Select the `docuchat` realm** (top-left dropdown)

3. **Create user**
   - Navigate to Users → Add user
   - Fill in username, email
   - Click Save

4. **Set password**
   - Go to Credentials tab
   - Set password, toggle "Temporary" off
   - Click Set Password

5. **Verify login**
   ```bash
   curl -X POST http://localhost/auth/realms/docuchat/protocol/openid-connect/token \
     -d "grant_type=password" \
     -d "client_id=docuchat-app" \
     -d "username=newuser" \
     -d "password=newpassword"
   ```

#### Export Realm Config (for backup/version control)

```bash
docker exec -it docuchat-keycloak /opt/keycloak/bin/kc.sh export \
  --realm docuchat \
  --dir /tmp/export

docker cp docuchat-keycloak:/tmp/export/docuchat-realm.json ./infra/keycloak/realm-export.json
```

#### Rotate Keys (if compromised)

1. Open Admin Console → Realm Settings → Keys
2. Click "Rotate" for each key type (RS256, HS256)
3. Restart backend to fetch new JWKS:
   ```bash
   docker restart docuchat-backend
   ```

**Note:** After key rotation, all existing tokens become invalid. Users must re-authenticate.