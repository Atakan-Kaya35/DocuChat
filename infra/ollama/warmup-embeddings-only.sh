#!/bin/sh
# =============================================================================
# Ollama Embedding Model Warmup Script (API Mode)
# =============================================================================
# This script only pulls and warms up the embedding model.
# LLM inference is handled by external APIs (Gemini/OpenAI).
# 
# This results in:
# - Faster startup (no large LLM model downloads)
# - Lower disk usage
# - Lower memory requirements
# =============================================================================

set -e

OLLAMA_HOST="${OLLAMA_BASE_URL:-http://ollama:11434}"
EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

wait_for_ollama() {
    log "Waiting for Ollama to be ready..."
    until curl -sf "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; do
        sleep 2
    done
    log "Ollama is up!"
}

pull_model() {
    local model=$1
    log "Pulling model: $model"
    
    # Check if model already exists
    if curl -sf "$OLLAMA_HOST/api/tags" | grep -q "\"$model\""; then
        log "Model $model already pulled"
    else
        # Pull the model
        curl -sf "$OLLAMA_HOST/api/pull" \
            -H "Content-Type: application/json" \
            -d "{\"name\": \"$model\", \"stream\": false}"
        log "Model $model pulled successfully"
    fi
}

warmup_embedding_model() {
    local model=$1
    log "Warming up embedding model: $model"
    
    # Send a simple embedding request to load the model into memory
    response=$(curl -sf "$OLLAMA_HOST/api/embeddings" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"$model\", \"prompt\": \"warmup test\"}" \
        --max-time 120)
    
    if [ $? -eq 0 ]; then
        log "Embedding model $model warmed up successfully"
    else
        log "Warning: Failed to warm up embedding model $model"
    fi
}

# Main execution
log "=== Ollama Embedding Model Warmup (API Mode) ==="
log "External LLM APIs will be used for chat/reasoning"
log "Only pulling embedding model: $EMBED_MODEL"

wait_for_ollama

# Pull and warm up embedding model only
pull_model "$EMBED_MODEL"
warmup_embedding_model "$EMBED_MODEL"

log "=== Embedding model ready! ==="
log "Note: Make sure your LLM API keys are configured in backend/.env"
