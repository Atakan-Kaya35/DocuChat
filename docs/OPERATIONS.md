# DocuChat Operations Guide

This document contains operational procedures, policies, and runbooks for DocuChat.

---

## Table of Contents

1. [Idempotency Policy](#idempotency-policy)
2. [Retry & Backoff Policy](#retry--backoff-policy)
3. [Rate Limiting](#rate-limiting)
4. [Audit Logging](#audit-logging)
5. [Health Checks](#health-checks)
6. [Runbooks](#runbooks)

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