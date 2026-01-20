# DocuChat - Local Development Guide

This guide covers how to run DocuChat locally from a clean clone.

---

## Prerequisites

- **Docker Desktop** (with Docker Compose v2)
- **Git**
- ~8GB free disk space (for Ollama models)
- ~4GB RAM available for containers

---

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/Atakan-Kaya35/DocuChat.git
cd DocuChat
```

### 2. Set Up Environment Files

Copy sample environment files (no secrets committed):

```bash
# Backend
cp backend/.env.sample backend/.env

# Frontend  
cp frontend/.env.sample frontend/.env

# Infrastructure (Postgres, Keycloak, etc.)
cp infra/.env.sample infra/.env
```

> **Note:** The `.env.sample` files contain working defaults for local development. Review and adjust passwords for production.

### 3. Start All Services

```bash
docker compose up
```

This starts 8 containers:
- `nginx` - Reverse proxy (port 80)
- `frontend` - React app
- `backend` - Django API
- `worker` - Indexing worker
- `postgres` - Database with pgvector
- `redis` - Task queue
- `keycloak` - Identity provider
- `ollama` - Local LLM

**First run takes 5-10 minutes** to pull images and download LLM models (~4GB for gemma:7b + nomic-embed-text).

### 4. Access the Application

| URL | Description |
|-----|-------------|
| http://localhost/ | DocuChat frontend |
| http://localhost/auth/ | Keycloak admin console |
| http://localhost/api/healthz | Backend health check |

### 5. Login

Default test user credentials (created by Keycloak realm import):
- **Username:** `testuser`
- **Password:** `testpassword`

Or create a new user via Keycloak admin console:
1. Go to http://localhost/auth/admin
2. Login with `admin` / `admin` (default)
3. Select "docuchat" realm → Users → Add user

---

## Verify Everything Works

### Health Checks

```bash
# Backend health
curl http://localhost/api/healthz

# Readiness (checks all dependencies)  
curl http://localhost/api/readyz
```

### Upload a Document

```bash
# Get a token first
TOKEN=$(curl -s -X POST http://localhost/auth/realms/docuchat/protocol/openid-connect/token \
  -d "grant_type=password" \
  -d "client_id=docuchat-app" \
  -d "username=testuser" \
  -d "password=testpassword" | jq -r '.access_token')

# Upload a file
curl -X POST http://localhost/api/docs/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@seed_docs/docuchat_runbook.txt"
```

### Ask a Question

```bash
curl -X POST http://localhost/api/rag/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "How do I reindex a document?"}'
```

---

## Stopping and Cleanup

```bash
# Stop all containers
docker compose down

# Stop and remove volumes (full reset)
docker compose down -v
```

---

## Troubleshooting

### Containers not starting?

```bash
# Check logs
docker compose logs backend
docker compose logs worker
docker compose logs keycloak
```

### Ollama models not loading?

Models are downloaded on first start. Check progress:
```bash
docker logs docuchat-ollama-init
```

### Database issues?

Reset and recreate:
```bash
docker compose down -v
docker compose up
```

---

## Next Steps

- [ARCHITECTURE.md](ARCHITECTURE.md) - System design and data flow
- [API.md](API.md) - Complete API reference
- [OPERATIONS.md](OPERATIONS.md) - Runbooks and operational procedures
- [DECISIONS.md](DECISIONS.md) - Design decisions and trade-offs