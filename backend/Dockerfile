# =============================================================================
# DocuChat Backend - Dockerfile
# =============================================================================
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Make entrypoint executable and create non-root user and data directories
RUN chmod +x /app/docker/entrypoint.sh && \
    useradd -m -u 1000 appuser && \
    mkdir -p /data/uploads /data/extracted && \
    chown -R appuser:appuser /app /data/uploads /data/extracted
USER appuser

# Expose port
EXPOSE 8000

# Use entrypoint to run migrations before starting
ENTRYPOINT ["/app/docker/entrypoint.sh"]
# Use daphne for ASGI (WebSocket support)
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]