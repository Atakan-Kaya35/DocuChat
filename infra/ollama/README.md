# Ollama - Local LLM Inference

DocuChat uses Ollama for local LLM inference, keeping all data on-premises.

## Models Required

| Model | Purpose | Size |
|-------|---------|------|
| `nomic-embed-text` | Document embeddings for vector search | ~275 MB |
| `gemma:7b` | Primary chat/RAG responses and agent reasoning | ~5 GB |
| `llama3.2` | Alternate chat model | ~2 GB |

## Automatic Model Download

**Important:** Model files are NOT stored in Git due to their large size. When you clone this repository and run `docker compose up`, the models will be downloaded automatically.

The `ollama-init` service automatically:

1. **Waits** for Ollama to be healthy
2. **Pulls** required models if not already present (this may take several minutes on first run)
3. **Warms up** models by sending initial requests (loads into VRAM)

This ensures that when `docker compose up` completes, models are ready for immediate inference with no cold-start delay.

### First Run Download Times

On a typical internet connection, expect:
- `nomic-embed-text`: ~1-2 minutes
- `gemma:7b`: ~5-10 minutes  
- `llama3.2`: ~2-5 minutes

Subsequent runs will skip downloads as models are cached locally.

## Manual Model Management

```bash
# Check loaded models
docker exec docuchat-ollama ollama list

# Pull a model manually
docker exec docuchat-ollama ollama pull gemma:7b

# Remove a model
docker exec docuchat-ollama ollama rm gemma:7b

# Check GPU/VRAM usage
docker exec docuchat-ollama ollama ps
```

## Configuration

Models are configured via environment variables in `backend/.env.sample`:

```env
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_CHAT_MODEL=gemma:7b
OLLAMA_ALT_CHAT_MODEL=llama3.2
```

The `ollama-init` service reads these same variables to know which models to pull.

## Model Persistence

Models are stored in a bind mount at `./infra/ollama/models/`. This ensures:

- Models persist across container restarts
- Models survive `docker compose down -v`
- Models are cached locally after first download

**Note:** The `models/` folder is git-ignored. Only a `.gitkeep` file is tracked to preserve the directory structure.

## Troubleshooting

### Models not loading into VRAM

Check ollama-init logs:
```bash
docker logs docuchat-ollama-init
```

### Slow first inference

If ollama-init failed or was skipped, the first request will trigger model loading. This is normal but can take 10-30 seconds.

### Out of VRAM

Ollama automatically manages VRAM. If you see OOM errors:
1. Use a smaller model (e.g., `gemma:2b` instead of `gemma:7b`)
2. Ensure no other GPU-intensive processes are running