# External LLM API Mode

This document explains how to run DocuChat with external LLM APIs (Gemini, OpenAI) instead of local Ollama inference.

## Overview

DocuChat supports multiple LLM providers for chat and reasoning:

| Provider | Setting | Use Case |
|----------|---------|----------|
| **Ollama** (default) | `LLM_PROVIDER=ollama` | Local inference, privacy-focused, GPU required |
| **Gemini** | `LLM_PROVIDER=gemini` | Google's API, fast, no GPU needed |
| **OpenAI** | `LLM_PROVIDER=openai` | OpenAI or compatible APIs (Azure, Groq, Together) |

> **Note:** Embeddings always use Ollama (`nomic-embed-text`) regardless of the LLM provider. This ensures vector search works offline and keeps embedding costs at zero.

## Benefits of API Mode

- **No GPU required** for LLM inference (only CPU for embeddings)
- **Faster startup** (no large model downloads)
- **Access to more capable models** (GPT-4o, Gemini 1.5 Pro, etc.)
- **Reduced memory footprint** (~2GB instead of 10GB+)
- **Consistent performance** regardless of local hardware

## Quick Start

### 1. Configure API Keys

Copy the API sample environment file:

```bash
cp backend/.env.api.sample backend/.env
```

Edit `backend/.env` and set your API key:

```env
# For Gemini (recommended)
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-1.5-flash

# OR for OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-openai-api-key
OPENAI_MODEL=gpt-4o-mini
```

### 2. Start with API Compose File

```bash
docker compose -f docker-compose-api.yml up -d
```

This uses `docker-compose-api.yml` which:
- Only downloads the embedding model (nomic-embed-text)
- Skips large LLM model downloads
- Starts much faster

### 3. Verify

Check the logs to confirm API mode is active:

```bash
docker logs docuchat-backend 2>&1 | grep -i "llm\|gemini\|openai"
```

You should see: `Using Gemini API for LLM inference` or similar.

## Provider Configuration

### Google Gemini

1. Get an API key from [Google AI Studio](https://aistudio.google.com/apikey)
2. Configure in `.env`:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-api-key
GEMINI_MODEL=gemini-1.5-flash  # or gemini-1.5-pro, gemini-2.0-flash
GEMINI_TIMEOUT=120
```

**Available Gemini Models:**
| Model | Speed | Capability | Notes |
|-------|-------|------------|-------|
| `gemini-1.5-flash` | Fast | Good | Recommended for most use cases |
| `gemini-1.5-pro` | Medium | Excellent | Better for complex reasoning |
| `gemini-2.0-flash` | Very Fast | Good | Latest, experimental |

### OpenAI

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
OPENAI_TIMEOUT=120
```

### Azure OpenAI

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your-azure-key
OPENAI_BASE_URL=https://your-resource.openai.azure.com/openai/deployments/your-deployment
OPENAI_MODEL=gpt-4o-mini
```

### Groq (Fast Open-Source Models)

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=gsk_your-groq-key
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=llama-3.3-70b-versatile
```

### Together AI

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your-together-key
OPENAI_BASE_URL=https://api.together.xyz/v1
OPENAI_MODEL=meta-llama/Llama-3.3-70B-Instruct-Turbo
```

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│   Frontend  │────▶│   Backend   │────▶│ LLM Provider │
│  (React)    │     │  (Django)   │     │ (API calls)  │
└─────────────┘     └──────┬──────┘     └──────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Ollama    │
                    │ (Embeddings)│
                    └─────────────┘
```

In API mode:
- **LLM calls** go to external APIs (Gemini/OpenAI)
- **Embedding calls** still go to local Ollama

## Switching Between Modes

### Switch to API Mode

```bash
# Stop current containers
docker compose down

# Use API compose file
docker compose -f docker-compose-api.yml up -d
```

### Switch Back to Local Mode

```bash
# Stop current containers  
docker compose -f docker-compose-api.yml down

# Use default compose file (will download LLM models)
docker compose up -d
```

## Troubleshooting

### API Key Errors

```
LLMError: GEMINI_API_KEY not configured
```

Ensure your API key is set in `backend/.env` and the container was restarted.

### Rate Limiting

If you see `429 Too Many Requests`, you've hit API rate limits. Solutions:
- Wait and retry
- Use a paid API tier
- Switch to a different provider

### Timeout Errors

Increase the timeout setting:

```env
GEMINI_TIMEOUT=300  # 5 minutes
OPENAI_TIMEOUT=300
```

### Verify Provider

Check which provider is active:

```bash
docker exec docuchat-backend python -c "
from django.conf import settings
print(f'Provider: {settings.LLM_PROVIDER}')
"
```

## Cost Considerations

| Provider | Pricing | Notes |
|----------|---------|-------|
| Ollama | Free | Requires GPU |
| Gemini Flash | Free tier available | 15 RPM free |
| Gemini Pro | ~$0.00125/1K tokens | Higher limits |
| GPT-4o-mini | ~$0.00015/1K tokens | Very affordable |
| Groq | Free tier available | Fast inference |

## Security Notes

- API keys are stored in environment variables, not in code
- Keys are never logged or exposed to the frontend
- Consider using secrets management (Vault, AWS Secrets Manager) in production
