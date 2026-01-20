# ADR-0004: Idempotency Policy for Document Re-uploads

**Status:** Accepted  
**Date:** 2026-01-15  
**Authors:** DocuChat Team

---

## Context

Users may upload the same document multiple times:
- Accidental re-upload
- Re-upload after browser refresh
- Testing the same file repeatedly

Without idempotency handling, this causes:
- Duplicate storage consumption
- Duplicate embeddings (wasted compute)
- Duplicate chunks (confusing citations)
- Multiple index jobs for same content

We need a policy that:
1. Prevents duplicate processing
2. Returns sensible response for re-uploads
3. Allows re-indexing when explicitly requested

## Decision

**Content hash uniqueness per user, with existing document return.**

Implementation:
1. Compute SHA-256 hash of file content during upload
2. Check for existing `(owner_user_id, content_hash)` combination
3. If exists: return existing document (no new job)
4. If not: create new document and index job

```python
def handle_upload(file, user_id):
    content_hash = hashlib.sha256(file.read()).hexdigest()
    
    existing = Document.objects.filter(
        owner_user_id=user_id,
        content_hash=content_hash
    ).first()
    
    if existing:
        return {
            "documentId": existing.id,
            "status": existing.status,
            "duplicate": True,
            "message": "Document with identical content already exists"
        }
    
    # Create new document
    doc = Document.objects.create(
        owner_user_id=user_id,
        content_hash=content_hash,
        filename=file.name,
        ...
    )
    job = IndexJob.objects.create(document=doc, status="QUEUED")
    enqueue_indexing(job.id)
    
    return {"documentId": doc.id, "jobId": job.id, "status": "QUEUED"}
```

Database constraint as backup:
```sql
UNIQUE(owner_user_id, content_hash)
```

## Alternatives Considered

1. **Filename-based deduplication**
   - Pros: Simple user mental model
   - Cons: Different content, same filename = lost updates
   - Rejected: Content changes are common, filename is not reliable

2. **Always create new document**
   - Pros: Simple implementation
   - Cons: Wasted storage, compute, confusing duplicates
   - Rejected: Poor user experience, wasteful

3. **Global content hash (across users)**
   - Pros: Maximum deduplication
   - Cons: Information leakage (know if someone else has same doc)
   - Rejected: Privacy concern

4. **Ask user what to do on duplicate**
   - Pros: User control
   - Cons: Friction, requires UI flow
   - Rejected: Automatic behavior is better UX for v1

## Consequences

### Positive
- Zero wasted compute on identical re-uploads
- Users can safely retry failed uploads
- Simple API response tells user it's a duplicate
- Database constraint prevents race conditions

### Negative
- Same content with different filename = treated as duplicate
- No way to force re-index without deleting first (future: add reindex endpoint)
- Hash computation adds small latency to upload

### Neutral
- SHA-256 is fast enough for our file sizes
- content_hash stored as 64-char hex string

## Hash Computation

```python
import hashlib

def compute_content_hash(file) -> str:
    """Compute SHA-256 hash of file content."""
    hasher = hashlib.sha256()
    file.seek(0)
    for chunk in iter(lambda: file.read(65536), b''):
        hasher.update(chunk)
    file.seek(0)
    return hasher.hexdigest()
```

## Follow-up Actions

- [x] Add content_hash column to Document model
- [x] Add unique constraint (owner_user_id, content_hash)
- [x] Update upload endpoint to check for duplicates
- [x] Return appropriate response for duplicates
- [ ] Add explicit reindex endpoint for v2

---

## LLM Disclosure

> This ADR was drafted with assistance from an LLM (Claude). The technical decisions, alternatives analysis, and final choices were reviewed and approved by the development team.
