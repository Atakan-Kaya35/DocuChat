# Backend Docker Configuration

This folder contains Docker-related scripts and configuration for the backend service.

## Files

### worker-entrypoint.sh

Entrypoint script for the Celery worker container. The worker uses the **same Docker image** as the backend API but runs with a different command.

```yaml
# docker-compose.yml example
worker:
  build: ./backend
  command: ["./docker/worker-entrypoint.sh"]
  # OR directly:
  # command: ["celery", "-A", "config", "worker", "-l", "info"]
```

## Image Sharing

Both `backend` and `worker` services share the same built image to:
- Reduce build time and disk usage
- Ensure code consistency between API and workers
- Simplify dependency management

## Worker Dependencies

The worker requires:
- PostgreSQL (for task state and app data)
- Redis (as Celery broker/backend)
- Ollama (for LLM inference tasks)
