#!/bin/bash
# =============================================================================
# Worker Entrypoint (Placeholder)
# =============================================================================
# This script starts the Celery worker using the same backend image.
# 
# Usage in docker-compose:
#   command: ["./docker/worker-entrypoint.sh"]
#
# Or override directly:
#   command: ["celery", "-A", "config", "worker", "-l", "info"]
# =============================================================================

set -e

echo "Starting Celery worker..."

# TODO: Add any pre-flight checks here (e.g., wait for broker)

exec celery -A config worker --loglevel=info
