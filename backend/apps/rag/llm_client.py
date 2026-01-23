"""
LLM Client Abstraction Layer.

Provides a unified interface for LLM calls that can switch between:
- Ollama (local inference)
- Gemini API (Google's cloud API)
- Other providers (easily extensible)

The embedding model always uses Ollama (nomic-embed-text) regardless of the
LLM provider setting.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class LLMMessage:
    """A message in a chat conversation."""
    role: str  # "system", "user", or "assistant"
    content: str


@dataclass
class LLMResponse:
    """Response from an LLM call."""
    content: str
    model: str
    usage: Optional[Dict[str, int]] = None  # token usage if available


class LLMError(Exception):
    """Raised when LLM call fails."""
    pass


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients."""
    
    @abstractmethod
    def chat(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int = 500,
    ) -> LLMResponse:
        """
        Send a chat completion request.
        
        Args:
            messages: List of messages in the conversation
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens in response
            
        Returns:
            LLMResponse with the model's response
            
        Raises:
            LLMError: If the request fails
        """
        pass
    
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model name being used."""
        pass


class OllamaClient(BaseLLMClient):
    """LLM client for Ollama local inference."""
    
    def __init__(self):
        self.base_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://ollama:11434')
        self.model = getattr(settings, 'OLLAMA_CHAT_MODEL', 'llama3.2')
        self.timeout = getattr(settings, 'OLLAMA_CHAT_TIMEOUT', 600)
    
    @property
    def model_name(self) -> str:
        return self.model
    
    def chat(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int = 500,
    ) -> LLMResponse:
        """Send chat request to Ollama."""
        logger.info(f"Calling Ollama chat: model={self.model}, temp={temperature}")
        
        # Convert to Ollama format
        ollama_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        try:
            with httpx.Client(timeout=float(self.timeout)) as client:
                response = client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": ollama_messages,
                        "stream": False,
                        "options": {
                            "temperature": temperature,
                            "num_predict": max_tokens,
                        }
                    }
                )
                response.raise_for_status()
                data = response.json()
                
                content = data.get("message", {}).get("content", "")
                if not content:
                    raise LLMError("Empty response from Ollama")
                
                logger.info(f"Ollama response: {len(content)} chars")
                return LLMResponse(content=content, model=self.model)
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error: {e}")
            raise LLMError(f"Ollama service error: {e.response.status_code}")
        except httpx.TimeoutException:
            logger.error("Ollama request timed out")
            raise LLMError("Ollama service timed out")
        except httpx.RequestError as e:
            logger.error(f"Ollama connection error: {e}")
            raise LLMError("Could not connect to Ollama")


class GeminiClient(BaseLLMClient):
    """LLM client for Google Gemini API."""
    
    def __init__(self):
        self.api_key = getattr(settings, 'GEMINI_API_KEY', '')
        self.model = getattr(settings, 'GEMINI_MODEL', 'gemini-1.5-flash')
        self.timeout = getattr(settings, 'GEMINI_TIMEOUT', 120)
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY not configured")
    
    @property
    def model_name(self) -> str:
        return self.model
    
    def chat(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int = 500,
    ) -> LLMResponse:
        """Send chat request to Gemini API."""
        logger.info(f"Calling Gemini API: model={self.model}, temp={temperature}")
        
        # Gemini "thinking" models (like gemini-3-pro-preview) use internal reasoning tokens.
        # We need to ensure enough tokens for both thinking and response.
        # Apply a minimum of 1000 tokens for thinking models.
        effective_max_tokens = max_tokens
        if "preview" in self.model or "thinking" in self.model or "-3-" in self.model:
            effective_max_tokens = max(max_tokens, 1000)
            if effective_max_tokens != max_tokens:
                logger.debug(f"Adjusted max_tokens from {max_tokens} to {effective_max_tokens} for thinking model")
        
        # Convert messages to Gemini format
        # Gemini uses "contents" with "parts" structure
        # System messages need to be handled separately
        system_instruction = None
        gemini_contents = []
        
        for msg in messages:
            if msg.role == "system":
                # Gemini handles system prompts as systemInstruction
                system_instruction = msg.content
            else:
                # Map roles: user -> user, assistant -> model
                role = "model" if msg.role == "assistant" else "user"
                gemini_contents.append({
                    "role": role,
                    "parts": [{"text": msg.content}]
                })
        
        # Build request body
        request_body: Dict[str, Any] = {
            "contents": gemini_contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": effective_max_tokens,
                "topP": 0.95,
            }
        }
        
        # Add system instruction if present
        if system_instruction:
            request_body["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }
        
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"
        
        try:
            with httpx.Client(timeout=float(self.timeout)) as client:
                response = client.post(
                    url,
                    json=request_body,
                    headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
                data = response.json()
                
                # Extract content from Gemini response
                # Response format: {"candidates": [{"content": {"parts": [{"text": "..."}]}}]}
                candidates = data.get("candidates", [])
                if not candidates:
                    # Check for safety blocks
                    if data.get("promptFeedback", {}).get("blockReason"):
                        reason = data["promptFeedback"]["blockReason"]
                        raise LLMError(f"Request blocked by Gemini: {reason}")
                    raise LLMError("No response from Gemini API")
                
                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    raise LLMError("Empty response from Gemini API")
                
                content = parts[0].get("text", "")
                if not content:
                    raise LLMError("Empty text in Gemini response")
                
                # Extract usage if available
                usage = None
                if "usageMetadata" in data:
                    meta = data["usageMetadata"]
                    usage = {
                        "prompt_tokens": meta.get("promptTokenCount", 0),
                        "completion_tokens": meta.get("candidatesTokenCount", 0),
                        "total_tokens": meta.get("totalTokenCount", 0),
                    }
                
                logger.info(f"Gemini response: {len(content)} chars")
                return LLMResponse(content=content, model=self.model, usage=usage)
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Gemini HTTP error: {e}")
            # Try to get error details
            try:
                error_data = e.response.json()
                error_msg = error_data.get("error", {}).get("message", str(e))
            except Exception:
                error_msg = str(e)
            raise LLMError(f"Gemini API error: {error_msg}")
        except httpx.TimeoutException:
            logger.error("Gemini request timed out")
            raise LLMError("Gemini API timed out")
        except httpx.RequestError as e:
            logger.error(f"Gemini connection error: {e}")
            raise LLMError("Could not connect to Gemini API")


class OpenAICompatibleClient(BaseLLMClient):
    """
    LLM client for OpenAI-compatible APIs.
    
    Works with: OpenAI, Azure OpenAI, Groq, Together, local servers, etc.
    """
    
    def __init__(self):
        self.api_key = getattr(settings, 'OPENAI_API_KEY', '')
        self.base_url = getattr(settings, 'OPENAI_BASE_URL', 'https://api.openai.com/v1')
        self.model = getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini')
        self.timeout = getattr(settings, 'OPENAI_TIMEOUT', 120)
        
        if not self.api_key:
            raise LLMError("OPENAI_API_KEY not configured")
    
    @property
    def model_name(self) -> str:
        return self.model
    
    def chat(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.2,
        max_tokens: int = 500,
    ) -> LLMResponse:
        """Send chat request to OpenAI-compatible API."""
        logger.info(f"Calling OpenAI API: model={self.model}, temp={temperature}")
        
        # Convert to OpenAI format (same as our internal format)
        openai_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        try:
            with httpx.Client(timeout=float(self.timeout)) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": openai_messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    }
                )
                response.raise_for_status()
                data = response.json()
                
                choices = data.get("choices", [])
                if not choices:
                    raise LLMError("No choices in OpenAI response")
                
                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    raise LLMError("Empty response from OpenAI")
                
                # Extract usage
                usage = None
                if "usage" in data:
                    usage = data["usage"]
                
                logger.info(f"OpenAI response: {len(content)} chars")
                return LLMResponse(content=content, model=self.model, usage=usage)
                
        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI HTTP error: {e}")
            raise LLMError(f"OpenAI API error: {e.response.status_code}")
        except httpx.TimeoutException:
            logger.error("OpenAI request timed out")
            raise LLMError("OpenAI API timed out")
        except httpx.RequestError as e:
            logger.error(f"OpenAI connection error: {e}")
            raise LLMError("Could not connect to OpenAI API")


# =============================================================================
# Client Factory
# =============================================================================

_client_instance: Optional[BaseLLMClient] = None


def get_llm_client() -> BaseLLMClient:
    """
    Get the configured LLM client instance.
    
    Uses LLM_PROVIDER setting to determine which client to use:
    - "ollama" (default): Local Ollama inference
    - "gemini": Google Gemini API
    - "openai": OpenAI or compatible API
    
    Returns:
        Configured LLM client instance
    """
    global _client_instance
    
    # Return cached instance if available
    if _client_instance is not None:
        return _client_instance
    
    provider = getattr(settings, 'LLM_PROVIDER', 'ollama').lower()
    
    if provider == 'gemini':
        logger.info("Using Gemini API for LLM inference")
        _client_instance = GeminiClient()
    elif provider == 'openai':
        logger.info("Using OpenAI-compatible API for LLM inference")
        _client_instance = OpenAICompatibleClient()
    else:
        logger.info("Using Ollama for LLM inference")
        _client_instance = OllamaClient()
    
    return _client_instance


def reset_llm_client():
    """Reset the cached client instance. Useful for testing."""
    global _client_instance
    _client_instance = None


# =============================================================================
# Convenience Functions
# =============================================================================

def chat_completion(
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 500,
) -> str:
    """
    Convenience function for simple chat completions.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        temperature: Sampling temperature
        max_tokens: Maximum tokens in response
        
    Returns:
        The model's response text
        
    Raises:
        LLMError: If the request fails
    """
    client = get_llm_client()
    llm_messages = [LLMMessage(role=m["role"], content=m["content"]) for m in messages]
    response = client.chat(llm_messages, temperature=temperature, max_tokens=max_tokens)
    return response.content


def get_model_name() -> str:
    """Get the name of the configured model."""
    client = get_llm_client()
    return client.model_name
