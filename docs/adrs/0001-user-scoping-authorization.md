# ADR-0001: User Scoping and Authorization Model

**Status:** Accepted  
**Date:** 2026-01-15  
**Authors:** DocuChat Team

---

## Context

DocuChat is a multi-user document Q&A application. Users upload personal documents and ask questions about their content. We need to ensure:

1. Users can only access their own documents
2. RAG retrieval only searches user's own documents
3. Agent tools cannot access other users' data
4. The scoping mechanism is simple, auditable, and hard to bypass

## Decision

**Use `owner_user_id` (Keycloak `sub` claim) for all data scoping.**

Every data access query includes `owner_user_id` filtering:

```python
# Document queries
Document.objects.filter(owner_user_id=user_id)

# Chunk queries (via document relationship)
DocumentChunk.objects.filter(document__owner_user_id=user_id)

# Vector search
SELECT * FROM document_chunks dc
JOIN documents d ON dc.document_id = d.id
WHERE d.owner_user_id = $1
ORDER BY embedding <-> $2
LIMIT $3
```

The `owner_user_id` is:
- Extracted from JWT `sub` claim at request entry
- Passed explicitly to all service functions
- Never inferred or defaulted

## Alternatives Considered

1. **Role-based access (RBAC)**
   - Pros: Flexible, supports sharing
   - Cons: Complex, overkill for v1, sharing not in requirements
   - Rejected: Adds complexity without current need

2. **Document-level permissions table**
   - Pros: Supports fine-grained sharing
   - Cons: Requires permission checks on every query, join overhead
   - Rejected: Over-engineering for single-user-per-doc model

3. **Tenant ID column (multi-tenancy)**
   - Pros: Industry standard for SaaS
   - Cons: Same as owner_user_id but with extra abstraction
   - Rejected: We don't have organizational tenancy requirements

## Consequences

### Positive
- Simple to understand and audit
- No complex permission logic
- Every query has explicit scoping (visible in code)
- Impossible to accidentally return other users' data

### Negative
- No document sharing between users (future feature)
- Admin access requires separate implementation
- Can't have "public" documents without special handling

### Neutral
- owner_user_id column on documents table (slight storage overhead)
- All service functions need user_id parameter

## Follow-up Actions

- [x] Add owner_user_id to Document model
- [x] Update all queries to include owner_user_id filter
- [x] Add agent tool ownership checks
- [ ] Consider sharing model for v2 if needed

---

## LLM Disclosure

> This ADR was drafted with assistance from an LLM (Claude). The technical decisions, alternatives analysis, and final choices were reviewed and approved by the development team.
