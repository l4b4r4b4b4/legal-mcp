"""LiteLLM client wrapper for RAG pipeline.

Provides a unified async interface for multiple LLM providers using LiteLLM.
Supports Ollama (local), vLLM (local GPU), and OpenAI (external API).

Usage:
    from app.rag.llm_client import LLMClient, get_llm_client

    # Using singleton
    client = get_llm_client()
    response = await client.generate(
        messages=[
            {"role": "system", "content": "You are a legal assistant."},
            {"role": "user", "content": "What is § 433 BGB?"},
        ]
    )

    # Or with custom config
    client = LLMClient(provider="openai", model="gpt-4o-mini")
    response = await client.generate(messages)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import litellm

logger = logging.getLogger(__name__)

# Suppress litellm verbose logging
litellm.set_verbose = False

# Provider type
ProviderType = Literal["ollama", "vllm", "openai"]


@dataclass
class LLMResponse:
    """Response from LLM generation.

    Attributes:
        content: Generated text content
        model: Model name used
        usage: Token usage statistics
        finish_reason: Why generation stopped
        latency_ms: Generation latency in milliseconds
    """

    content: str
    model: str
    usage: dict[str, int]
    finish_reason: str | None
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "content": self.content,
            "model": self.model,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "latency_ms": round(self.latency_ms, 2),
        }


@dataclass
class LLMClient:
    """Unified LLM client using LiteLLM.

    Supports multiple providers with consistent interface:
    - ollama: Local Ollama server (default: http://localhost:11434)
    - vllm: Local vLLM server with OpenAI-compatible API
    - openai: OpenAI API (requires OPENAI_API_KEY)

    Attributes:
        provider: LLM provider type
        model: Model name for the provider
        api_base: Override API base URL
        temperature: Sampling temperature (0-2, lower = more deterministic)
        max_tokens: Maximum tokens to generate
        timeout: Request timeout in seconds
    """

    provider: ProviderType = "ollama"
    model: str = "llama3.2"
    api_base: str | None = None
    temperature: float = 0.1
    max_tokens: int = 1024
    timeout: float = 120.0
    _initialized: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        """Validate configuration and set defaults."""
        self._set_defaults()
        self._initialized = True
        logger.info(
            "LLM client initialized: provider=%s, model=%s, api_base=%s",
            self.provider,
            self.model,
            self.api_base,
        )

    def _set_defaults(self) -> None:
        """Set provider-specific defaults."""
        if self.api_base is None:
            if self.provider == "ollama":
                self.api_base = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
            elif self.provider == "vllm":
                self.api_base = os.getenv("VLLM_API_BASE", "http://localhost:7373/v1")
            # openai uses OPENAI_API_BASE env var automatically

    def _get_model_string(self) -> str:
        """Get the LiteLLM model string.

        LiteLLM uses provider prefixes:
        - ollama/model-name
        - openai/model-name (for vLLM and OpenAI)
        """
        if self.provider == "ollama":
            return f"ollama/{self.model}"
        elif self.provider == "vllm":
            # vLLM uses OpenAI-compatible API
            return f"openai/{self.model}"
        else:
            # OpenAI doesn't need prefix for standard models
            return self.model

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a response from the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Override default temperature
            max_tokens: Override default max_tokens

        Returns:
            LLMResponse with generated content and metadata

        Raises:
            RuntimeError: If generation fails after retries
        """
        model_string = self._get_model_string()
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        # Build kwargs
        kwargs: dict[str, Any] = {
            "model": model_string,
            "messages": messages,
            "temperature": temp,
            "max_tokens": tokens,
            "timeout": self.timeout,
        }

        # Add api_base for providers that need it
        if self.api_base and self.provider in ("ollama", "vllm"):
            kwargs["api_base"] = self.api_base

        start_time = time.perf_counter()

        try:
            response = await litellm.acompletion(**kwargs)
            latency_ms = (time.perf_counter() - start_time) * 1000

            # Extract response data
            choice = response.choices[0]
            content = choice.message.content or ""
            finish_reason = choice.finish_reason

            # Extract usage
            usage_data = response.usage
            usage = {
                "prompt_tokens": usage_data.prompt_tokens if usage_data else 0,
                "completion_tokens": usage_data.completion_tokens if usage_data else 0,
                "total_tokens": usage_data.total_tokens if usage_data else 0,
            }

            return LLMResponse(
                content=content,
                model=response.model or model_string,
                usage=usage,
                finish_reason=finish_reason,
                latency_ms=latency_ms,
            )

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "LLM generation failed: %s (latency: %.2fms)",
                e,
                latency_ms,
            )
            raise RuntimeError(f"LLM generation failed: {e}") from e

    async def health_check(self) -> bool:
        """Check if the LLM provider is available.

        Returns:
            True if provider responds, False otherwise
        """
        try:
            # Simple test generation
            response = await self.generate(
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            return bool(response.content)
        except Exception as e:
            logger.warning("LLM health check failed: %s", e)
            return False

    def stats(self) -> dict[str, Any]:
        """Get client statistics.

        Returns:
            Dictionary with client configuration
        """
        return {
            "provider": self.provider,
            "model": self.model,
            "model_string": self._get_model_string(),
            "api_base": self.api_base,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
        }


# =============================================================================
# Singleton Client
# =============================================================================

_llm_client: LLMClient | None = None


def get_llm_client(
    provider: ProviderType | None = None,
    model: str | None = None,
    api_base: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> LLMClient:
    """Get the global LLM client instance.

    Creates a singleton client on first call. Override parameters are only
    used on first call (when client is created).

    Args:
        provider: LLM provider (ollama, vllm, openai)
        model: Model name
        api_base: Override API base URL
        temperature: Sampling temperature
        max_tokens: Maximum tokens to generate

    Returns:
        Singleton LLMClient instance

    Example:
        # Use defaults from environment/config
        client = get_llm_client()

        # Or specify on first call
        client = get_llm_client(provider="openai", model="gpt-4o-mini")
    """
    global _llm_client

    if _llm_client is None:
        # Import settings here to avoid circular imports
        from app.config import get_settings

        settings = get_settings()

        # Use provided values or fall back to settings/defaults
        _llm_client = LLMClient(
            provider=provider or getattr(settings, "llm_provider", "ollama"),
            model=model or getattr(settings, "llm_model", "llama3.2"),
            api_base=api_base or getattr(settings, "llm_api_base", None),
            temperature=temperature
            if temperature is not None
            else getattr(settings, "llm_temperature", 0.1),
            max_tokens=max_tokens
            if max_tokens is not None
            else getattr(settings, "llm_max_tokens", 1024),
        )

    return _llm_client


def reset_llm_client() -> None:
    """Reset the global LLM client (for testing)."""
    global _llm_client
    _llm_client = None


__all__ = [
    "LLMClient",
    "LLMResponse",
    "ProviderType",
    "get_llm_client",
    "reset_llm_client",
]
