#!/bin/bash
# =============================================================================
# DocuChat Backend Entrypoint Script
# =============================================================================
# This script runs migrations and then starts the application.
# It ensures the database schema is always up-to-date before the app starts.
# =============================================================================

set -e

echo "Waiting for PostgreSQL to be ready..."
# Wait for postgres to be available (simple retry loop)
MAX_RETRIES=30
RETRY_COUNT=0
until python -c "
import psycopg2
import os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
conn.close()
print('PostgreSQL is ready!')
" 2>/dev/null; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "ERROR: PostgreSQL not available after $MAX_RETRIES attempts"
        exit 1
    fi
    echo "PostgreSQL not ready yet (attempt $RETRY_COUNT/$MAX_RETRIES)..."
    sleep 2
done

echo "Running database migrations..."
python manage.py migrate --noinput

echo "Migrations complete. Starting application..."
exec "$@"
