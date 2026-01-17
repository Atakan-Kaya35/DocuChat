# DECISIONS.md — DocuChat

This file records:
- high-level time log
- cost cap + usage guardrails
- major trade-offs + rationale
- “done enough” notes for an evaluation-ready build

---

## Project Execution Principles

- **Small victories first**: prioritize stack boot + thin vertical slices.
- **Bounded intelligence**: strict caps (agent steps, context size, tokens).
- **Doc-grounded answers**: citations are mandatory for claims from docs; otherwise say “I don’t know”.
- **Documentation closes the loop**: when a subsystem decision is finalized, we update docs/ADRs before moving on.

---

## Repository Layout Decisions

- `docker-compose.yml` is at **repo root** (canonical entrypoint).
- `infra/` holds infra assets (nginx, keycloak realm export, ollama, postgres init scripts, etc.).
- Application code lives in `frontend/` and `backend/`.
- Secrets are never committed. Only `.env.sample` files live in the repo.

---

## Local Cost Cap

**Target cost during evaluation:** **$0** (local inference)

- **LLM provider:** Ollama (local)
- **LLM model:** TBD (Gemma variant)
- **Embeddings model:** TBD (Ollama embeddings model)

If a fallback cloud provider is ever used for debugging, hard cap target is **≤ $5 total** and must be explicitly enabled via env flags.

---

## Usage Guardrails (Prevent Runaway)

### API & Agent Limits
- `/api/chat/ask` rate limited (server-side).
- `/api/docs/upload` rate limited (server-side).
- Agent tool loop hard cap: **max 5 tool calls**
- Agent planning cap: **2–5 steps**
- Retrieved context cap: **max K chunks**, plus **max total characters/tokens**
- Generation cap: **max output tokens** (configured via backend settings)

### Safety & Reliability
- Bounded retries with exponential backoff for embedding/LLM calls.
- Idempotency behavior for re-upload (documented & enforced).
- Multi-user scoping enforced on retrieval and doc access.

---

## Major Trade-offs (Record as We Decide)

> Add an entry here whenever we intentionally choose a simpler approach.

### Trade-off Template
- **Decision:** ...
- **Alternatives considered:** ...
- **Why chosen:** ...
- **Risks:** ...
- **Mitigations:** ...
- **Follow-up:** ...

### Current Known Trade-offs
- Compose-first local deployment (vs Kubernetes) for evaluation speed and clarity.
- Ollama local inference (vs paid API) for zero cost and no secrets; acceptance criteria still relies on strict citations + “I don’t know”.

---

## Time Log (High-Level)

> Keep this honest and short. Update as you work. Hours can be rough.

### Day 0 — Repo skeleton + CI (Xh)
- Repo structure + docs placeholders
- `.github/workflows/ci.yml` baseline
- `.env.sample` created for frontend/backend/infra

### Day 1 — Infra boot smoke tests (Xh)
- Postgres + pgvector init verified
- Redis verified
- Ollama container verified + models pulled
- Keycloak realm import verified + reachable via NGINX proxy path

### Day 2 — Auth vertical slice (Xh)
- Backend JWT validation (JWKS, issuer/audience, basic role)
- `/api/me`
- Frontend login (OIDC PKCE) calling `/api/me`

### Day 3 — Upload vertical slice (Xh)
- Upload endpoint + Document model
- UI: Uploads list + statuses
- IndexJob creation + queueing

### Day 4 — Worker indexing + progress (Xh)
- Celery worker pipeline: extract → chunk → embed → store
- WebSocket progress events + UI wiring

### Day 5 — RAG ask endpoint (Xh)
- Retrieval scoped to user/workspace
- Prompting + citation format
- UI: Chat renders citations clearly

### Day 6–7 — Production-leaning hardening (Xh)
- Rate limiting, retries/backoff
- Health endpoints + runbooks
- Audit-ish logs (metadata only)

### Day 8–9 — Agent mode (Xh)
- `/api/agent/run` bounded loop + tools
- Optional tool trace in response
- UI toggle / agent run screen

### Day 10 — Docs + ADR completion (Xh)
- ARCHITECTURE, API, OPERATIONS polished
- 5–6 ADRs with LLM assistance disclosure
- Final acceptance checklist run

---
## Architecture Decision Records (ADRs)

Detailed architectural decisions are documented in the `/docs/adrs/` folder:

| ADR | Title | Summary |
|-----|-------|---------|
| [0001](adrs/0001-user-scoping-authorization.md) | User Scoping & Authorization | owner_user_id scoping everywhere |
| [0002](adrs/0002-chunking-strategy.md) | Chunking Strategy | Fixed-size with overlap, deterministic |
| [0003](adrs/0003-vector-database-pgvector.md) | Vector Database (pgvector) | Single Postgres for all data |
| [0004](adrs/0004-idempotency-policy.md) | Idempotency Policy | Content hash uniqueness per user |
| [0005](adrs/0005-agent-tool-loop-cap.md) | Agent Tool Loop Cap | Max 5 calls, exactly 2 tools |
| [0006](adrs/0006-ollama-local-inference.md) | Ollama Local Inference | Zero-cost local LLM |

All ADRs include an LLM disclosure statement per project policy.

---
## “Re-Alignment” Habit (LLM + Docs)

- Before starting a new subsystem (Auth, Indexing, RAG, Agent), paste a short “alignment prompt” to the coding LLM:
  - repo layout policy
  - “skeleton vs implementation” scope
  - caps/guardrails
- When subsystem is stable, immediately update:
  - relevant ADR
  - OPERATIONS runbook entries
  - API examples (if endpoint touched)
