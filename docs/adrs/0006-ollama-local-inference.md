# ADR-0006: Model and Runtime Choice (Ollama Local)

**Status:** Accepted  
**Date:** 2026-01-15  
**Authors:** DocuChat Team

---

## Context

DocuChat needs:
1. **Embeddings:** Convert text to vectors for similarity search
2. **Chat completion:** Generate answers from context

Requirements:
- Zero cost during development and evaluation
- No API keys or cloud dependencies
- Works offline
- Easy to switch models later
- Reasonable performance on consumer hardware

## Decision

**Use Ollama for local LLM inference.**

Configuration:
```yaml
# docker-compose.yml
ollama:
  image: ollama/ollama:latest
  volumes:
    - ollama_data:/root/.ollama
  deploy:
    resources:
      reservations:
        devices:
          - capabilities: [gpu]  # Optional, for faster inference
```

Environment variables:
```env
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_CHAT_MODEL=llama3.2
```

### Model Selection

| Task | Model | Rationale |
|------|-------|-----------|
| Embeddings | `nomic-embed-text` | 768 dims, good quality, fast |
| Chat | `llama3.2` (or `gemma2`) | Good instruction following, fits in 8GB VRAM |

### API Usage

```python
# Embeddings
response = httpx.post(
    f"{OLLAMA_BASE_URL}/api/embeddings",
    json={"model": "nomic-embed-text", "prompt": text}
)
vector = response.json()["embedding"]

# Chat completion
response = httpx.post(
    f"{OLLAMA_BASE_URL}/api/chat",
    json={
        "model": "llama3.2",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }
)
answer = response.json()["message"]["content"]
```

## Alternatives Considered

1. **OpenAI API**
   - Pros: Best quality, fast, reliable
   - Cons: Costs money, requires API key, internet dependency
   - Rejected: Violates zero-cost and offline requirements

2. **Anthropic Claude API**
   - Pros: Good quality, structured output support
   - Cons: Same as OpenAI (cost, API key, internet)
   - Rejected: Same reasons

3. **vLLM / Text Generation Inference**
   - Pros: Production-grade serving
   - Cons: More complex setup, heavier resource usage
   - Rejected: Ollama is simpler for local dev

4. **llama.cpp directly**
   - Pros: Maximum control
   - Cons: Manual model management, no API abstraction
   - Rejected: Ollama provides better developer experience

5. **Hugging Face Transformers**
   - Pros: Python-native, many models
   - Cons: Manual quantization, memory management
   - Rejected: Ollama handles this automatically

## Consequences

### Positive
- $0 cost for all inference
- Works completely offline
- Easy model switching (just pull new model)
- GPU acceleration when available
- Good developer experience (hot-reload, logs)

### Negative
- Slower than cloud APIs (especially without GPU)
- Quality may be lower than GPT-4 / Claude
- Model size limited by local hardware
- First request slow (model loading)

### Neutral
- Need to pull models before first use
- Model files stored in Docker volume

## Switching Models

To change models:

1. Pull new model: `docker exec ollama ollama pull <model>`
2. Update environment variable: `OLLAMA_CHAT_MODEL=<model>`
3. Restart backend: `docker-compose restart backend worker`

Embedding model change requires re-indexing (different vector dimensions).

## Hardware Recommendations

| Setup | Embedding | Chat | Notes |
|-------|-----------|------|-------|
| 8GB VRAM | ✓ | ✓ (7B) | RTX 3060/4060, good experience |
| 16GB RAM (CPU) | ✓ | ✓ (slow) | Works but 10-30s per response |
| Apple M1/M2/M3 | ✓ | ✓ | Metal acceleration, good speed |

## Fallback Cloud Option

If local inference is insufficient, cloud fallback can be enabled:

```env
# Disabled by default
LLM_PROVIDER=ollama  # or "openai" for fallback
OPENAI_API_KEY=      # Only if LLM_PROVIDER=openai
LLM_COST_CAP=5.00    # Hard cap in dollars
```

Cloud usage is:
- Opt-in (explicit env flag)
- Capped ($5 max by default)
- Logged for cost tracking

## Follow-up Actions

- [x] Add Ollama to docker-compose.yml
- [x] Implement embedding client
- [x] Implement chat client
- [x] Add retry logic for Ollama calls
- [ ] Add model preload script
- [ ] Consider vLLM for production scaling

---

## LLM Disclosure

> This ADR was drafted with assistance from an LLM (Claude). The technical decisions, alternatives analysis, and final choices were reviewed and approved by the development team.
