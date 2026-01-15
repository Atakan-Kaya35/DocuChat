# Redis Configuration

This folder contains Redis configuration for DocuChat.

## Purpose

Redis is used for:
- **Celery message broker**: Task queue for async document processing
- **Celery result backend**: Storing task results
- **Caching** (optional): Session or query caching

## Configuration

The `redis.conf` file is an optional placeholder for custom Redis settings.
Default Redis configuration is sufficient for development.

### Production considerations

For production, consider:
- Enabling persistence (RDB/AOF)
- Setting `maxmemory` and eviction policy
- Configuring authentication (`requirepass`)

## Volumes

- `redis_data` volume (optional) persists data across restarts.

## Healthcheck

Redis health is checked via `redis-cli ping` which returns `PONG` when healthy.
