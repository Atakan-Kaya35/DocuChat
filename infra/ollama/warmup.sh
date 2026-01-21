#!/bin/sh
# =============================================================================
# Ollama Model Warmup Script
# =============================================================================
# This script ensures models are pulled and loaded into VRAM before the
# system is considered ready. It's run by the ollama-init service.
# =============================================================================

set -e

OLLAMA_HOST="${OLLAMA_BASE_URL:-http://ollama:11434}"
EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"
CHAT_MODEL="${OLLAMA_CHAT_MODEL:-gemma:7b}"
ALT_CHAT_MODEL="${OLLAMA_ALT_CHAT_MODEL:-llama3.2}"

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

warmup_model() {
    local model=$1
    local prompt=$2
    log "Warming up model: $model (loading into VRAM)"
    
    # Send a simple request to load the model into memory
    response=$(curl -sf "$OLLAMA_HOST/api/generate" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"$model\", \"prompt\": \"$prompt\", \"stream\": false, \"options\": {\"num_predict\": 1}}" \
        --max-time 300)
    
    if [ $? -eq 0 ]; then
        log "Model $model warmed up and loaded into VRAM"
    else
        log "WARNING: Failed to warm up $model, but continuing..."
    fi
}

warmup_embedding() {
    local model=$1
    log "Warming up embedding model: $model"
    
    response=$(curl -sf "$OLLAMA_HOST/api/embeddings" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"$model\", \"prompt\": \"warmup\"}" \
        --max-time 120)
    
    if [ $? -eq 0 ]; then
        log "Embedding model $model warmed up"
    else
        log "WARNING: Failed to warm up embedding $model, but continuing..."
    fi
}

# =============================================================================
# Main
# =============================================================================

log "=== Ollama Model Warmup Starting ==="

wait_for_ollama

# Pull models first (embedding + chat models)
log "Pulling embedding model..."
pull_model "$EMBED_MODEL"

log "Pulling primary chat model..."
pull_model "$CHAT_MODEL"

log "Pulling alternate chat model..."
pull_model "$ALT_CHAT_MODEL"

# Warm them up (load into VRAM)
log "Warming up models..."
warmup_embedding "$EMBED_MODEL"
warmup_model "$CHAT_MODEL" "Hello"
warmup_model "$ALT_CHAT_MODEL" "Hello"

log "=== All models pulled and warmed up! ==="
log "Models ready: $EMBED_MODEL, $CHAT_MODEL, $ALT_CHAT_MODEL"
log "System is ready for inference."
