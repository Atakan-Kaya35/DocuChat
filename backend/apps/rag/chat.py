"""
LLM Chat service for RAG.

Handles prompt construction and Ollama chat API calls
with strict citation-friendly rules.
"""
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

import httpx
from django.conf import settings

from apps.rag.retrieval import Citation, RetrievalResult

logger = logging.getLogger(__name__)

# Default chat parameters
DEFAULT_TEMPERATURE = 0.2  # Low for factuality
DEFAULT_MAX_TOKENS = 500


class ChatError(Exception):
    """Raised when chat completion fails."""
    pass


# System prompt with strict citation rules
SYSTEM_PROMPT = """You are a helpful document assistant. Your task is to answer questions based ONLY on the provided document context.

STRICT RULES:
1. Use ONLY information from the provided context below.
2. If the answer cannot be found in the context, say exactly: "I don't know based on the provided documents."
3. When citing information, use bracket notation like [1], [2] to reference the source chunks.
4. Be concise and factual.
5. Do not make up information or use external knowledge.

CONTEXT:
{context}

Answer the user's question using only the context above."""


def build_context_block(citations: List[Citation]) -> str:
    """
    Build a numbered context block from citations.
    
    Format:
    [1] (document.pdf, chunk 3): The text content here...
    [2] (other.txt, chunk 1): More content...
    """
    if not citations:
        return "(No relevant documents found)"
    
    parts = []
    for i, citation in enumerate(citations, 1):
        parts.append(
            f"[{i}] ({citation.document_title}, chunk {citation.chunk_index}): "
            f"{citation.snippet}"
        )
    return "\n\n".join(parts)


def build_prompt(question: str, citations: List[Citation]) -> str:
    """
    Build the complete system prompt with context.
    """
    context_block = build_context_block(citations)
    return SYSTEM_PROMPT.format(context=context_block)


@dataclass
class ChatResponse:
    """Response from the chat completion."""
    answer: str
    citations: List[Citation]
    model: str
    
    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "model": self.model,
        }


def call_ollama_chat(
    question: str,
    system_prompt: str,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """
    Call Ollama chat API for completion.
    
    Args:
        question: User's question
        system_prompt: System prompt with context
        temperature: Sampling temperature (0-1)
        max_tokens: Maximum tokens in response
        
    Returns:
        The LLM's response text
        
    Raises:
        ChatError: If the API call fails
    """
    ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://ollama:11434')
    chat_model = getattr(settings, 'OLLAMA_CHAT_MODEL', 'llama3.2')
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    
    logger.info(f"Calling Ollama chat with model={chat_model}, temp={temperature}")
    logger.debug(f"System prompt length: {len(system_prompt)} chars")
    
    try:
        with httpx.Client(timeout=120.0) as client:  # Long timeout for LLM
            response = client.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": chat_model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    }
                }
            )
            response.raise_for_status()
            data = response.json()
            
            # Extract response content
            message = data.get("message", {})
            content = message.get("content", "")
            
            if not content:
                raise ChatError("Empty response from LLM")
            
            logger.info(f"LLM response received: {len(content)} chars")
            return content
            
    except httpx.HTTPStatusError as e:
        logger.error(f"Ollama chat request failed: {e}")
        raise ChatError(f"Chat service error: {e.response.status_code}")
    except httpx.TimeoutException:
        logger.error("Ollama chat request timed out")
        raise ChatError("Chat service timed out")
    except httpx.RequestError as e:
        logger.error(f"Ollama connection error: {e}")
        raise ChatError("Could not connect to chat service")
    except (KeyError, TypeError) as e:
        logger.error(f"Unexpected Ollama response format: {e}")
        raise ChatError("Invalid response from chat service")


# Default response when no context is available
NO_CONTEXT_ANSWER = "I don't know based on the provided documents."


def generate_answer(
    question: str,
    retrieval_result: RetrievalResult,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> ChatResponse:
    """
    Generate an answer using retrieved context.
    
    If no context is available, returns a default "I don't know" response
    without calling the LLM.
    
    Args:
        question: User's question
        retrieval_result: Retrieved chunks with citations
        temperature: LLM temperature setting
        max_tokens: Maximum response tokens
        
    Returns:
        ChatResponse with answer and citations
    """
    chat_model = getattr(settings, 'OLLAMA_CHAT_MODEL', 'llama3.2')
    
    # Safety rail: no context means no LLM call
    if not retrieval_result.citations:
        logger.info("No context available, returning default response")
        return ChatResponse(
            answer=NO_CONTEXT_ANSWER,
            citations=[],
            model=chat_model,
        )
    
    # Build prompt with context
    system_prompt = build_prompt(question, retrieval_result.citations)
    
    # Log the prompt for debugging
    logger.debug(f"Full prompt:\n{system_prompt}")
    
    # Call LLM
    answer = call_ollama_chat(
        question=question,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    
    return ChatResponse(
        answer=answer,
        citations=retrieval_result.citations,
        model=chat_model,
    )
