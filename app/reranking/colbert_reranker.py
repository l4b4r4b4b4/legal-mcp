"""ColBERT re-ranker using VAGOsolutions/SauerkrautLM-Reason-EuroColBERT.

Implements late-interaction (MaxSim) scoring for re-ranking candidate documents
retrieved from ChromaDB. Uses the EuroBERT backbone with a Dense projection head
to compute per-token embeddings, then scores via MaxSim.

Architecture:
    EuroBERT-210m backbone (768-dim token output)
    → Dense head: Linear(768→128, bias=False), Identity activation
    → L2 normalize per-token
    → MaxSim scoring

The model is loaded lazily on first use and automatically cleaned up after
an idle timeout to free GPU/CPU memory.

No PyLate dependency required — loads model via ``transformers`` and
``safetensors`` directly, avoiding the ``sentence-transformers==5.1.1`` pin
conflict.

Usage:
    from app.reranking.colbert_reranker import get_colbert_reranker

    reranker = get_colbert_reranker()
    results = await reranker.rerank(
        query="Was ist ein Kaufvertrag?",
        documents=["Doc 1 text", "Doc 2 text"],
        top_k=5,
    )
"""

from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
from typing import Any, ClassVar

import torch
import torch.nn.functional as functional

from app.config import get_settings
from app.rag.reranker import RerankResult

logger = logging.getLogger(__name__)

# Default skiplist: punctuation characters whose token embeddings are excluded
# from MaxSim query scoring (matches model's config_sentence_transformers.json).
_DEFAULT_SKIPLIST_CHARS: frozenset[str] = frozenset(
    "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
)

# Dense projection head dimensions (from model inspection)
_BACKBONE_DIM = 768
_PROJECTION_DIM = 128

# Token length limits (from model config)
_QUERY_MAX_LENGTH = 256
_DOCUMENT_MAX_LENGTH = 2048

# Prefixes (from model's config_sentence_transformers.json)
_QUERY_PREFIX = "[Q] "
_DOCUMENT_PREFIX = "[D] "


class ColBERTReranker:
    """Local ColBERT re-ranker with MaxSim late-interaction scoring.

    Loads the EuroBERT backbone and Dense projection head from HuggingFace,
    computes per-token embeddings for queries and documents, and scores
    candidates using the MaxSim algorithm.

    Attributes:
        model_name: HuggingFace model ID for the ColBERT model.
        device: Torch device string ('cpu', 'cuda', or 'auto').
        batch_size: Batch size for document encoding.

    Example:
        >>> reranker = ColBERTReranker()
        >>> results = await reranker.rerank(
        ...     query="Kaufvertrag Pflichten",
        ...     documents=["Durch den Kaufvertrag...", "Der Mieter..."],
        ...     top_k=1,
        ... )
        >>> results[0].score  # MaxSim score
        12.34
    """

    IDLE_TIMEOUT_SECONDS: ClassVar[int] = 300  # 5 minutes

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        batch_size: int | None = None,
    ) -> None:
        """Initialize the ColBERT re-ranker.

        Args:
            model_name: HuggingFace model ID. Uses config default if None.
            device: Torch device ('cpu', 'cuda', 'auto'). Uses config if None.
            batch_size: Document encoding batch size. Uses config if None.
        """
        settings = get_settings()
        self.model_name = model_name or settings.colbert_reranking_model
        self._device_setting = device or settings.colbert_device
        self.batch_size = batch_size or settings.colbert_batch_size

        # Resolved device (set during model loading)
        self._resolved_device: str | None = None

        # Model components (lazy-loaded)
        self._backbone: Any | None = None
        self._tokenizer: Any | None = None
        self._projection: torch.nn.Linear | None = None
        self._skiplist_token_ids: set[int] | None = None

        # Thread safety and lifecycle
        self._lock = threading.Lock()
        self._last_used: float = 0.0
        self._loaded = False

        logger.info(
            "ColBERTReranker initialized: model=%s, device=%s, batch_size=%d",
            self.model_name,
            self._device_setting,
            self.batch_size,
        )

    @property
    def device(self) -> str:
        """Resolved torch device string."""
        if self._resolved_device is not None:
            return self._resolved_device
        return self._select_device()

    def _select_device(self) -> str:
        """Select optimal device based on setting and available resources.

        Returns:
            Torch device string ('cpu' or 'cuda').
        """
        if self._device_setting == "cpu":
            return "cpu"

        if self._device_setting == "cuda":
            if not torch.cuda.is_available():
                logger.warning("CUDA requested but not available, falling back to CPU")
                return "cpu"
            return "cuda"

        # Auto-detect
        if not torch.cuda.is_available():
            logger.info("ColBERT reranker: CUDA not available, using CPU")
            return "cpu"

        try:
            free_memory_gb = (
                torch.cuda.get_device_properties(0).total_memory
                - torch.cuda.memory_allocated(0)
            ) / (1024**3)

            # EuroBERT-210m needs ~1.5GB, projection is tiny
            minimum_memory_gb = 2.0
            if free_memory_gb >= minimum_memory_gb:
                logger.info(
                    "ColBERT reranker: using CUDA (%.1fGB free)", free_memory_gb
                )
                return "cuda"
            else:
                logger.warning(
                    "ColBERT reranker: insufficient GPU memory "
                    "(%.1fGB free < %.1fGB required), using CPU",
                    free_memory_gb,
                    minimum_memory_gb,
                )
                return "cpu"
        except Exception as error:
            logger.warning(
                "ColBERT reranker: error checking GPU, falling back to CPU: %s",
                error,
            )
            return "cpu"

    def _load_model(self) -> None:
        """Load the EuroBERT backbone, tokenizer, and Dense projection head.

        Downloads model weights from HuggingFace on first call (~800MB).
        Subsequent calls use the HF Hub cache.

        Raises:
            RuntimeError: If model loading fails.
        """
        logger.info("Loading ColBERT model: %s on %s", self.model_name, self.device)

        try:
            # Clear GPU cache before loading
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                gc.collect()

            self._resolved_device = self._select_device()

            # Load tokenizer
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                trust_remote_code=True,
            )

            # Load backbone
            from transformers import AutoModel

            self._backbone = AutoModel.from_pretrained(
                self.model_name,
                trust_remote_code=True,
            )
            self._backbone.to(self._resolved_device)
            self._backbone.eval()

            # Load Dense projection head from subfolder
            self._projection = self._load_projection_head()
            self._projection.to(self._resolved_device)
            self._projection.eval()

            # Build skiplist token IDs
            self._skiplist_token_ids = self._build_skiplist()

            self._loaded = True
            self._last_used = time.time()

            logger.info(
                "ColBERT model loaded successfully: backbone_dim=%d, "
                "projection_dim=%d, device=%s, skiplist_tokens=%d",
                _BACKBONE_DIM,
                _PROJECTION_DIM,
                self._resolved_device,
                len(self._skiplist_token_ids),
            )

        except Exception as error:
            logger.error("Failed to load ColBERT model: %s", error)
            self._cleanup()

            # Try CPU fallback if CUDA failed
            if self._resolved_device == "cuda":
                logger.info("Retrying ColBERT model load on CPU...")
                self._resolved_device = "cpu"
                self._load_model()
                return

            raise RuntimeError(
                f"Failed to load ColBERT model '{self.model_name}': {error}"
            ) from error

    def _load_projection_head(self) -> torch.nn.Linear:
        """Load the Dense projection head (Linear 768→128, no bias).

        Downloads ``1_Dense/model.safetensors`` from the HF repo and
        loads the weight matrix into a ``torch.nn.Linear`` layer.

        Returns:
            Configured Linear projection layer.

        Raises:
            RuntimeError: If projection head cannot be loaded.
        """
        from huggingface_hub import hf_hub_download
        from safetensors.torch import load_file

        try:
            dense_path = hf_hub_download(
                repo_id=self.model_name,
                filename="1_Dense/model.safetensors",
            )
            state_dict = load_file(dense_path)

            # Create projection layer
            projection = torch.nn.Linear(_BACKBONE_DIM, _PROJECTION_DIM, bias=False)

            # Map state dict keys — the Dense module may use various key names
            mapped_state_dict = self._map_projection_keys(state_dict)
            projection.load_state_dict(mapped_state_dict)

            logger.debug(
                "Loaded Dense projection head: %s → mapped keys: %s",
                list(state_dict.keys()),
                list(mapped_state_dict.keys()),
            )

            return projection

        except Exception as error:
            raise RuntimeError(
                f"Failed to load ColBERT Dense projection head: {error}"
            ) from error

    @staticmethod
    def _map_projection_keys(
        state_dict: dict[str, torch.Tensor],
    ) -> dict[str, torch.Tensor]:
        """Map HF state dict keys to torch.nn.Linear expected keys.

        The Dense module in sentence-transformers/PyLate may store the weight
        under various key names. This method normalizes them.

        Args:
            state_dict: Raw state dict from safetensors file.

        Returns:
            State dict with keys matching ``torch.nn.Linear``.

        Raises:
            RuntimeError: If no weight tensor can be identified.
        """
        # Expected key for torch.nn.Linear
        target_key = "weight"

        # If already correct, return as-is
        if target_key in state_dict:
            return state_dict

        # Common alternative key names in sentence-transformers Dense modules
        alternative_keys = [
            "linear.weight",
            "0.weight",
            "dense.weight",
            "projection.weight",
        ]

        for alternative_key in alternative_keys:
            if alternative_key in state_dict:
                return {target_key: state_dict[alternative_key]}

        # Last resort: if there's exactly one tensor with the right shape, use it
        candidates = [
            (key, tensor)
            for key, tensor in state_dict.items()
            if tensor.shape == torch.Size([_PROJECTION_DIM, _BACKBONE_DIM])
        ]

        if len(candidates) == 1:
            key, tensor = candidates[0]
            logger.warning("ColBERT projection: using tensor '%s' by shape match", key)
            return {target_key: tensor}

        available_keys = {
            key: tuple(tensor.shape) for key, tensor in state_dict.items()
        }
        raise RuntimeError(
            f"Cannot identify projection weight in state dict. "
            f"Available keys and shapes: {available_keys}"
        )

    def _build_skiplist(self) -> set[int]:
        """Build set of token IDs to skip in MaxSim query scoring.

        Encodes each skiplist character individually and collects
        the resulting token IDs.

        Returns:
            Set of token IDs to exclude from query MaxSim computation.
        """
        if self._tokenizer is None:
            return set()

        skiplist_ids: set[int] = set()
        for character in _DEFAULT_SKIPLIST_CHARS:
            token_ids = self._tokenizer.encode(character, add_special_tokens=False)
            skiplist_ids.update(token_ids)

        # Also skip padding token
        if self._tokenizer.pad_token_id is not None:
            skiplist_ids.add(self._tokenizer.pad_token_id)

        return skiplist_ids

    def _ensure_loaded(self) -> None:
        """Ensure model is loaded, handling idle timeout.

        Thread-safe: acquires lock before checking/loading.
        """
        with self._lock:
            # Check idle timeout
            if (
                self._loaded
                and self._last_used > 0
                and time.time() - self._last_used > self.IDLE_TIMEOUT_SECONDS
            ):
                logger.info("ColBERT model idle timeout reached, cleaning up")
                self._cleanup()

            if not self._loaded:
                self._load_model()

            self._last_used = time.time()

    def _encode_tokens(
        self,
        texts: list[str],
        prefix: str,
        max_length: int,
    ) -> torch.Tensor:
        """Encode texts into L2-normalized projected token embeddings.

        Args:
            texts: Input texts to encode.
            prefix: Text prefix to prepend ('[Q] ' or '[D] ').
            max_length: Maximum token length for truncation/padding.

        Returns:
            Tensor of shape ``(batch, seq_len, projection_dim)`` with
            L2-normalized per-token embeddings.
        """
        assert self._tokenizer is not None
        assert self._backbone is not None
        assert self._projection is not None

        # Prepend prefix
        prefixed_texts = [f"{prefix}{text}" for text in texts]

        # Tokenize
        encoding = self._tokenizer(
            prefixed_texts,
            padding="max_length",
            truncation=True,
            max_length=max_length,
            return_tensors="pt",
        )
        encoding = {
            key: value.to(self._resolved_device) for key, value in encoding.items()
        }

        # Forward pass through backbone
        with torch.no_grad():
            outputs = self._backbone(**encoding)

        # Get token embeddings from last hidden state
        # Shape: (batch, seq_len, backbone_dim)
        token_embeddings = outputs.last_hidden_state

        # Project through Dense head
        # Shape: (batch, seq_len, projection_dim)
        projected = self._projection(token_embeddings)

        # L2 normalize each token embedding
        normalized = functional.normalize(projected, p=2, dim=-1)

        return normalized

    def _compute_maxsim(
        self,
        query_embeddings: torch.Tensor,
        document_embeddings: torch.Tensor,
        query_input_ids: torch.Tensor,
    ) -> list[float]:
        """Compute MaxSim scores between a query and multiple documents.

        For each query token (excluding skiplist tokens), finds the maximum
        cosine similarity with any document token, then sums these maxima.

        Args:
            query_embeddings: Shape ``(1, query_len, dim)``.
            document_embeddings: Shape ``(num_docs, doc_len, dim)``.
            query_input_ids: Shape ``(1, query_len)`` — token IDs for
                skiplist filtering.

        Returns:
            List of MaxSim scores, one per document.
        """
        assert self._skiplist_token_ids is not None

        # Build query token mask: True for tokens to KEEP
        # Shape: (1, query_len)
        query_ids = query_input_ids.squeeze(0)  # (query_len,)
        keep_mask = torch.tensor(
            [token_id.item() not in self._skiplist_token_ids for token_id in query_ids],
            dtype=torch.bool,
            device=query_embeddings.device,
        )

        # Filter query embeddings to non-skiplist tokens
        # Shape: (num_kept, dim)
        query_filtered = query_embeddings.squeeze(0)[keep_mask]

        if query_filtered.shape[0] == 0:
            # Edge case: all query tokens are in skiplist
            return [0.0] * document_embeddings.shape[0]

        scores: list[float] = []
        for document_index in range(document_embeddings.shape[0]):
            # Shape: (doc_len, dim)
            document_tokens = document_embeddings[document_index]

            # Cosine similarity matrix: (num_kept, doc_len)
            # Both are already L2-normalized, so dot product = cosine sim
            similarity_matrix = torch.matmul(
                query_filtered, document_tokens.transpose(0, 1)
            )

            # Max over document tokens for each query token: (num_kept,)
            max_similarities, _ = similarity_matrix.max(dim=-1)

            # Sum of max similarities
            score = max_similarities.sum().item()
            scores.append(score)

        return scores

    def _compute_scores(
        self,
        query: str,
        documents: list[str],
    ) -> list[float]:
        """Compute MaxSim scores for a query against multiple documents.

        This is the main scoring method. It encodes the query and documents
        in batches, then computes MaxSim scores.

        Args:
            query: Search query text (without prefix).
            documents: List of document texts (without prefix).

        Returns:
            List of MaxSim scores, one per document.
        """
        assert self._tokenizer is not None

        # Encode query (single item, with prefix)
        query_embeddings = self._encode_tokens(
            [query], _QUERY_PREFIX, _QUERY_MAX_LENGTH
        )

        # Get query input IDs for skiplist filtering
        query_encoding = self._tokenizer(
            [f"{_QUERY_PREFIX}{query}"],
            padding="max_length",
            truncation=True,
            max_length=_QUERY_MAX_LENGTH,
            return_tensors="pt",
        )
        query_input_ids = query_encoding["input_ids"].to(self._resolved_device)

        # Encode documents in batches
        all_scores: list[float] = []
        for batch_start in range(0, len(documents), self.batch_size):
            batch_end = min(batch_start + self.batch_size, len(documents))
            batch_documents = documents[batch_start:batch_end]

            document_embeddings = self._encode_tokens(
                batch_documents, _DOCUMENT_PREFIX, _DOCUMENT_MAX_LENGTH
            )

            batch_scores = self._compute_maxsim(
                query_embeddings, document_embeddings, query_input_ids
            )
            all_scores.extend(batch_scores)

        return all_scores

    def rerank_sync(
        self,
        query: str,
        documents: list[str],
        top_k: int | None = None,
        return_text: bool = True,
    ) -> list[RerankResult]:
        """Synchronously re-rank documents by MaxSim relevance to query.

        Suitable for calling from synchronous code paths (e.g.,
        ``pipeline.search_laws()``).

        Args:
            query: The search query.
            documents: List of document texts to re-rank.
            top_k: Number of top results to return (default: all).
            return_text: Whether to include document text in results.

        Returns:
            List of ``RerankResult`` sorted by score descending.

        Raises:
            RuntimeError: If model loading or scoring fails.
        """
        if not documents:
            return []

        self._ensure_loaded()

        try:
            scores = self._compute_scores(query, documents)
        except Exception as error:
            raise RuntimeError(f"ColBERT re-ranking failed: {error}") from error

        # Build results
        results: list[RerankResult] = []
        for index, score in enumerate(scores):
            results.append(
                RerankResult(
                    index=index,
                    score=score,
                    text=documents[index] if return_text else "",
                )
            )

        # Sort by score descending (higher MaxSim = more relevant)
        results.sort(key=lambda result: result.score, reverse=True)

        # Apply top_k
        if top_k is not None:
            results = results[:top_k]

        logger.debug(
            "ColBERT re-ranked %d documents, returning top %d",
            len(documents),
            len(results),
        )

        return results

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int | None = None,
        return_text: bool = True,
    ) -> list[RerankResult]:
        """Asynchronously re-rank documents by MaxSim relevance to query.

        Wraps ``rerank_sync()`` in ``asyncio.to_thread()`` to avoid blocking
        the event loop during model inference.

        Args:
            query: The search query.
            documents: List of document texts to re-rank.
            top_k: Number of top results to return (default: all).
            return_text: Whether to include document text in results.

        Returns:
            List of ``RerankResult`` sorted by score descending.

        Raises:
            RuntimeError: If model loading or scoring fails.
        """
        return await asyncio.to_thread(
            self.rerank_sync,
            query,
            documents,
            top_k,
            return_text,
        )

    async def health_check(self) -> bool:
        """Check if the ColBERT model can be loaded.

        Returns:
            True if model is loaded or can be loaded, False otherwise.
        """
        try:
            self._ensure_loaded()
            return self._loaded
        except Exception as error:
            logger.warning("ColBERT health check failed: %s", error)
            return False

    def _cleanup(self) -> None:
        """Unload model and free memory."""
        if self._backbone is not None:
            del self._backbone
            self._backbone = None

        if self._projection is not None:
            del self._projection
            self._projection = None

        if self._tokenizer is not None:
            del self._tokenizer
            self._tokenizer = None

        self._skiplist_token_ids = None
        self._loaded = False

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()

        logger.info("ColBERT model unloaded")

    def cleanup(self) -> None:
        """Force cleanup of the model (thread-safe)."""
        with self._lock:
            self._cleanup()

    async def close(self) -> None:
        """Async cleanup for compatibility with TEIReranker interface."""
        self.cleanup()

    def stats(self) -> dict[str, Any]:
        """Get re-ranker statistics.

        Returns:
            Dictionary with re-ranker configuration and status.
        """
        statistics: dict[str, Any] = {
            "type": "colbert",
            "model_name": self.model_name,
            "device": self._resolved_device or self._device_setting,
            "batch_size": self.batch_size,
            "model_loaded": self._loaded,
            "last_used": self._last_used,
            "idle_timeout": self.IDLE_TIMEOUT_SECONDS,
            "backbone_dim": _BACKBONE_DIM,
            "projection_dim": _PROJECTION_DIM,
        }

        if self._skiplist_token_ids is not None:
            statistics["skiplist_token_count"] = len(self._skiplist_token_ids)

        if torch.cuda.is_available():
            statistics["cuda_available"] = True
            statistics["gpu_memory_allocated_gb"] = round(
                torch.cuda.memory_allocated(0) / (1024**3), 2
            )
        else:
            statistics["cuda_available"] = False

        return statistics


# =============================================================================
# Singleton ColBERT Reranker
# =============================================================================

_colbert_reranker: ColBERTReranker | None = None
_colbert_reranker_lock = threading.Lock()


def get_colbert_reranker(
    model_name: str | None = None,
    device: str | None = None,
    batch_size: int | None = None,
) -> ColBERTReranker:
    """Get the global ColBERT re-ranker instance.

    Creates a singleton on first call. Subsequent calls return
    the existing instance (constructor args are ignored).

    Args:
        model_name: Override model name (only used on first call).
        device: Override device (only used on first call).
        batch_size: Override batch size (only used on first call).

    Returns:
        Singleton ``ColBERTReranker`` instance.
    """
    global _colbert_reranker

    with _colbert_reranker_lock:
        if _colbert_reranker is None:
            _colbert_reranker = ColBERTReranker(
                model_name=model_name,
                device=device,
                batch_size=batch_size,
            )
        return _colbert_reranker


def cleanup_colbert_reranker() -> None:
    """Cleanup the global ColBERT re-ranker to free memory."""
    global _colbert_reranker

    with _colbert_reranker_lock:
        if _colbert_reranker is not None:
            _colbert_reranker.cleanup()


def reset_colbert_reranker() -> None:
    """Reset the global ColBERT re-ranker (for testing)."""
    global _colbert_reranker

    with _colbert_reranker_lock:
        if _colbert_reranker is not None:
            _colbert_reranker.cleanup()
        _colbert_reranker = None


__all__ = [
    "ColBERTReranker",
    "cleanup_colbert_reranker",
    "get_colbert_reranker",
    "reset_colbert_reranker",
]
