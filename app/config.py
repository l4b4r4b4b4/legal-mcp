"""Configuration module for Legal-MCP.

Uses pydantic-settings for environment-based configuration with validation.

Environment Variables:
    CACHE_BACKEND: Cache backend type - memory, sqlite, redis (default: auto)
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    SQLITE_PATH: SQLite database path (default: XDG data dir)
    FASTMCP_PORT: Server port for HTTP modes (default: 9685)
    FASTMCP_HOST: Server host for HTTP modes (default: 0.0.0.0)
    LANGFUSE_PUBLIC_KEY: Langfuse public key (optional)
    LANGFUSE_SECRET_KEY: Langfuse secret key (optional)
    LANGFUSE_HOST: Langfuse host URL (default: https://cloud.langfuse.com)
    CHROMA_HOST: ChromaDB server hostname (default: chromadb)
    CHROMA_PORT: ChromaDB server HTTP port (default: 8000)
    EMBEDDING_MODEL: Sentence-transformers model name (default: jina-embeddings-v2-base-de)
    USE_TEI: Use TEI server for embeddings instead of local model (default: false)
    TEI_URL: TEI server URL (default: http://localhost:9721)
    RERANKER_URL: TEI reranker server URL (default: http://localhost:9722)
    LLM_PROVIDER: LLM provider - ollama, vllm, openai (default: ollama)
    LLM_MODEL: Model name for the provider (default: llama3.2)
    LLM_API_BASE: Override API base URL (optional)
    LLM_TEMPERATURE: Sampling temperature 0-2 (default: 0.1)
    LLM_MAX_TOKENS: Maximum tokens to generate (default: 1024)
    LEGAL_MCP_INGEST_ROOT: Allowlisted root directory for file-based ingestion tools (required for ingest_markdown_files)
    WARMUP_ON_STARTUP: Enable automatic corpus warm-up on server start (default: false)
    WARMUP_MAX_LAWS: Maximum laws to ingest during warm-up (default: all)
    WARMUP_HTML_ROOT: Path to pre-downloaded HTML corpus (default: data/html)
    WARMUP_BATCH_SIZE: Documents per embedding batch during warm-up (default: 256)
    WARMUP_MAX_WORKERS: Concurrent HTML parsing workers during warm-up (default: 8)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_xdg_data_home() -> Path:
    """Get XDG-compliant data home directory."""
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home)
    return Path.home() / ".local" / "share"


def _get_default_sqlite_path() -> str:
    """Get XDG-compliant default SQLite path."""
    return str(_get_xdg_data_home() / "legal-mcp" / "cache.db")


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    # Cache backend configuration
    cache_backend: Literal["memory", "sqlite", "redis", "auto"] = Field(
        default="auto",
        description=(
            "Cache backend type. 'auto' selects sqlite for stdio, redis for HTTP modes."
        ),
    )
    redis_url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL for distributed caching.",
    )
    sqlite_path: str = Field(
        default_factory=_get_default_sqlite_path,
        description="SQLite database path for local persistence.",
    )

    # ChromaDB HTTP server configuration
    chroma_host: str | None = Field(
        default=None,
        description=(
            "ChromaDB HTTP server hostname (e.g., 'chromadb', 'localhost'). "
            "When set, HttpClient is used; when None, falls back to local "
            "PersistentClient at chroma_persist_path."
        ),
    )
    chroma_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="ChromaDB HTTP server port.",
    )
    embedding_model: str = Field(
        default="jinaai/jina-embeddings-v2-base-de",
        description="Sentence-transformers model name for embeddings. Default is Jina's German-English bilingual model with 8192 token context.",
    )

    # TEI (Text Embeddings Inference) configuration
    use_tei: bool = Field(
        default=False,
        description="Use TEI server for embeddings instead of local model. Much better GPU memory management.",
    )
    tei_url: str = Field(
        default="http://localhost:9721",
        description="TEI server URL for HTTP-based embeddings.",
    )
    reranker_url: str = Field(
        default="http://localhost:9722",
        description="TEI reranker server URL for two-stage retrieval.",
    )

    # ColBERT re-ranking configuration
    colbert_reranking_enabled: bool = Field(
        default=False,
        description=(
            "Enable local ColBERT re-ranking using MaxSim late interaction. "
            "When enabled, search results from ChromaDB are re-ranked using "
            "VAGOsolutions/SauerkrautLM-Reason-EuroColBERT for improved precision. "
            "Requires ~800MB model download on first use."
        ),
    )
    colbert_reranking_model: str = Field(
        default="VAGOsolutions/SauerkrautLM-Reason-EuroColBERT",
        description=(
            "HuggingFace model ID for the ColBERT re-ranker. Must be a model "
            "with EuroBERT backbone + Dense projection head in sentence-transformers "
            "ColBERT format."
        ),
    )
    colbert_reranking_top_k: int = Field(
        default=10,
        ge=1,
        le=200,
        description=(
            "Number of top results to return after ColBERT re-ranking. "
            "This is the final result count presented to the user or RAG pipeline."
        ),
    )
    colbert_retrieval_candidates: int = Field(
        default=100,
        ge=10,
        le=1000,
        description=(
            "Number of candidate documents to retrieve from ChromaDB before "
            "ColBERT re-ranking. More candidates improve recall but increase "
            "re-ranking latency. Should be significantly larger than "
            "colbert_reranking_top_k."
        ),
    )
    colbert_device: str = Field(
        default="auto",
        description=(
            "Device for ColBERT model inference. 'auto' selects CUDA if available "
            "with sufficient memory, otherwise CPU. Explicit values: 'cpu', 'cuda'."
        ),
    )
    colbert_batch_size: int = Field(
        default=32,
        ge=1,
        le=256,
        description=(
            "Batch size for document encoding during ColBERT re-ranking. "
            "Larger batches are faster but use more memory."
        ),
    )

    # LLM configuration for RAG
    llm_provider: Literal["ollama", "vllm", "openai"] = Field(
        default="vllm",
        description="LLM provider for RAG generation. 'ollama' for local, 'vllm' for GPU server, 'openai' for API.",
    )
    llm_model: str = Field(
        default="uai/lm-small",
        description="Model name for the LLM provider. Default matches docker-compose.gpu.yml vLLM served-model-name.",
    )
    llm_api_base: str | None = Field(
        default="http://localhost:9723/v1",
        description="Override API base URL for LLM provider. Default matches docker-compose.gpu.yml vLLM port.",
    )
    llm_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="Sampling temperature (0-2). Lower values are more deterministic.",
    )
    llm_max_tokens: int = Field(
        default=1024,
        ge=1,
        le=8192,
        description="Maximum tokens to generate in response.",
    )

    # Allowlisted ingest root for file-based ingestion tools
    ingest_root_path: str | None = Field(
        default=None,
        description=(
            "Allowlisted root directory for file-based ingestion tools. "
            "When set, tools like ingest_markdown_files may only read files under this directory. "
            "If unset, file-based ingestion tools should fail fast."
        ),
    )

    # Corpus warm-up configuration
    warmup_on_startup: bool = Field(
        default=False,
        description=(
            "Enable automatic corpus warm-up on server start. "
            "When true, the server ingests the pre-downloaded HTML corpus into "
            "ChromaDB in a background thread. The server remains available for "
            "non-law tools while warm-up runs."
        ),
    )
    warmup_max_laws: int | None = Field(
        default=None,
        ge=1,
        le=10000,
        description=(
            "Maximum number of laws to ingest during warm-up. "
            "None means all available laws (~6400). Use 10-50 for quick testing."
        ),
    )
    warmup_html_root: str = Field(
        default="data/html",
        description=(
            "Path to pre-downloaded HTML corpus directory. Each subdirectory "
            "should contain .html files for one law (e.g., data/html/bgb/*.html)."
        ),
    )
    warmup_batch_size: int = Field(
        default=256,
        ge=1,
        le=4096,
        description=(
            "Documents per embedding batch during warm-up. Larger batches are "
            "more efficient but use more memory."
        ),
    )
    warmup_max_workers: int = Field(
        default=8,
        ge=1,
        le=64,
        description="Concurrent workers for HTML parsing during warm-up.",
    )

    # Server configuration (for HTTP modes)
    fastmcp_port: int = Field(
        default=9685,
        ge=1,
        le=65535,
        description="Server port for SSE and streamable-http modes.",
    )
    fastmcp_host: str = Field(
        default="0.0.0.0",  # nosec B104 - intentional for Docker/container deployments
        description="Server host for SSE and streamable-http modes.",
    )

    # Langfuse configuration (optional)
    langfuse_public_key: str | None = Field(
        default=None,
        description="Langfuse public key for tracing.",
    )
    langfuse_secret_key: str | None = Field(
        default=None,
        description="Langfuse secret key for tracing.",
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        description="Langfuse host URL.",
    )

    @field_validator("sqlite_path", "ingest_root_path")
    @classmethod
    def expand_path(cls, value: str | None) -> str | None:
        """Expand ~ in file paths."""
        if value is None:
            return None
        return str(Path(value).expanduser())

    @property
    def chroma_url(self) -> str | None:
        """Full ChromaDB server URL for display and logging.

        Returns the URL when ``chroma_host`` is configured, signaling that
        ``chromadb.HttpClient`` should be used.  Returns ``None`` when no
        host is set, signaling fallback to ``PersistentClient``.

        Note:
            Do **not** pass this value to ``chromadb.HttpClient(host=...)``.
            That parameter expects a bare hostname, not a URL.  Use
            ``chroma_host`` and ``chroma_port`` separately instead.
        """
        if self.chroma_host is None:
            return None
        return f"http://{self.chroma_host}:{self.chroma_port}"

    @property
    def chroma_persist_path(self) -> str:
        """XDG-compliant local ChromaDB persistence path.

        Used as fallback when ``chroma_host`` is not set (i.e. no remote
        ChromaDB server).
        """
        return str(_get_xdg_data_home() / "legal-mcp" / "chroma")

    @property
    def langfuse_enabled(self) -> bool:
        """Check if Langfuse credentials are configured."""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    def get_cache_backend_for_transport(
        self,
        transport: Literal["stdio", "sse", "streamable-http"],
    ) -> Literal["memory", "sqlite", "redis"]:
        """Get the appropriate cache backend for the given transport.

        Args:
            transport: The MCP transport mode being used.

        Returns:
            The cache backend to use.
        """
        if self.cache_backend != "auto":
            # Explicit backend configured
            return self.cache_backend

        # Auto-select based on transport
        if transport == "stdio":
            return "sqlite"
        else:
            # HTTP modes (sse, streamable-http) use Redis for distributed caching
            return "redis"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Returns:
        Singleton Settings instance loaded from environment.
    """
    return Settings()


# Convenience export for direct access
settings = get_settings()
