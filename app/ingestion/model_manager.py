"""Singleton embedding model manager with GPU memory optimization.

Provides centralized model management with:
- Singleton pattern for model reuse across the application
- Automatic GPU memory cleanup when idle
- Optimal batching and token length configuration
- Fallback to CPU if GPU memory is insufficient
- Thread-safe model access

Usage:
    from app.ingestion.model_manager import get_embedding_model

    model = get_embedding_model()
    embeddings = model.encode(texts)

    # Optionally free GPU memory when done
    cleanup_embedding_model()
"""

from __future__ import annotations

import contextlib
import gc
import logging
import threading
import time
from typing import Any, ClassVar

import torch
from sentence_transformers import SentenceTransformer

from app.config import get_settings

logger = logging.getLogger(__name__)

# Global model manager instance
_model_manager: EmbeddingModelManager | None = None
_model_lock = threading.Lock()


class EmbeddingModelManager:
    """Singleton manager for embedding models with GPU optimization.

    Features:
    - Automatic device selection (CUDA -> CPU fallback)
    - Memory monitoring and cleanup
    - Optimal batch sizes based on available memory
    - Thread-safe access
    - Automatic unloading after idle time
    """

    # Class-level configuration
    IDLE_TIMEOUT_SECONDS: ClassVar[int] = 300  # 5 minutes
    MIN_GPU_MEMORY_GB: ClassVar[float] = 2.0  # Minimum GPU memory to use CUDA

    def __init__(self, model_name: str | None = None) -> None:
        """Initialize the model manager.

        Args:
            model_name: Override model name (uses config default if None)
        """
        settings = get_settings()
        self.model_name = model_name or settings.embedding_model
        self.device = self._select_device()
        self.max_seq_length = self._get_optimal_seq_length()
        self.batch_size = self._get_optimal_batch_size()

        self._model: SentenceTransformer | None = None
        self._last_used = 0.0
        self._lock = threading.Lock()

        logger.info(
            "EmbeddingModelManager initialized: model=%s, device=%s, "
            "max_seq_length=%d, batch_size=%d",
            self.model_name,
            self.device,
            self.max_seq_length,
            self.batch_size,
        )

    def _select_device(self) -> str:
        """Select optimal device based on available resources."""
        if not torch.cuda.is_available():
            logger.info("CUDA not available, using CPU")
            return "cpu"

        try:
            # Get GPU memory info
            gpu_memory_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            free_memory_gb = (
                torch.cuda.get_device_properties(0).total_memory
                - torch.cuda.memory_allocated(0)
            ) / (1024**3)

            logger.info(
                "GPU memory: %.1fGB total, %.1fGB free",
                gpu_memory_gb,
                free_memory_gb,
            )

            if free_memory_gb >= self.MIN_GPU_MEMORY_GB:
                logger.info("Using CUDA device")
                return "cuda"
            else:
                logger.warning(
                    "Insufficient GPU memory (%.1fGB free < %.1fGB required), using CPU",
                    free_memory_gb,
                    self.MIN_GPU_MEMORY_GB,
                )
                return "cpu"

        except Exception as e:
            logger.warning("Error checking GPU memory, falling back to CPU: %s", e)
            return "cpu"

    def _get_optimal_seq_length(self) -> int:
        """Get optimal sequence length based on device and model."""
        # Jina models support up to 8192, but use 6144 to fit in 12GB VRAM
        base_length = 6144 if "jina" in self.model_name.lower() else 512

        # Reduce sequence length on CPU for faster processing
        if self.device == "cpu":
            return min(base_length, 1024)
        else:
            return base_length

    def _get_optimal_batch_size(self) -> int:
        """Get optimal batch size based on device and available memory."""
        if self.device == "cpu":
            # CPU processing - moderate batch size
            return 8

        # GPU with 8192 seq_length needs batch_size=1 to fit in 12GB VRAM
        # Model ~4.7GB + activations ~5.5GB for long sequences
        return 1

    def _load_model(self) -> SentenceTransformer:
        """Load the embedding model with optimal configuration."""
        logger.info("Loading embedding model: %s on %s", self.model_name, self.device)

        try:
            # Clear GPU cache before loading
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                gc.collect()

            # Load model with trust_remote_code for Jina models
            model_kwargs = {}
            if "jina" in self.model_name.lower():
                model_kwargs["trust_remote_code"] = True

            model = SentenceTransformer(self.model_name, **model_kwargs)

            # Configure model
            model.max_seq_length = self.max_seq_length

            # Move to device
            model = model.to(self.device)

            # Set to eval mode and optimize for inference
            model.eval()
            if self.device == "cuda":
                # Enable memory-efficient attention if available
                with contextlib.suppress(Exception):
                    torch.backends.cuda.enable_flash_sdp(True)

            logger.info(
                "Model loaded successfully: dim=%d, max_seq_length=%d, device=%s",
                model.get_sentence_embedding_dimension(),
                model.max_seq_length,
                next(model.parameters()).device,
            )

            return model

        except Exception as e:
            logger.error("Failed to load model %s: %s", self.model_name, e)

            # Try CPU fallback if CUDA failed
            if self.device == "cuda":
                logger.info("Retrying on CPU...")
                self.device = "cpu"
                return self._load_model()

            raise

    def _cleanup_model(self) -> None:
        """Unload model and free GPU memory."""
        if self._model is not None:
            logger.info("Unloading embedding model to free memory")
            del self._model
            self._model = None

            # Force cleanup
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()

    def get_model(self) -> SentenceTransformer:
        """Get the model instance (thread-safe)."""
        with self._lock:
            # Check if model needs cleanup due to idle timeout
            if (
                self._model is not None
                and time.time() - self._last_used > self.IDLE_TIMEOUT_SECONDS
            ):
                logger.info("Model idle timeout reached, cleaning up")
                self._cleanup_model()

            # Load model if not available
            if self._model is None:
                self._model = self._load_model()

            self._last_used = time.time()
            return self._model

    def encode(
        self,
        sentences: list[str] | str,
        batch_size: int | None = None,
        show_progress_bar: bool = False,
        convert_to_numpy: bool = True,
    ) -> Any:
        """Encode sentences using the managed model.

        Args:
            sentences: Text(s) to encode
            batch_size: Override default batch size
            show_progress_bar: Show encoding progress
            convert_to_numpy: Return numpy arrays instead of tensors

        Returns:
            Encoded embeddings
        """
        model = self.get_model()

        # Use optimal batch size if not specified
        if batch_size is None:
            batch_size = self.batch_size

        return model.encode(
            sentences,
            batch_size=batch_size,
            show_progress_bar=show_progress_bar,
            convert_to_numpy=convert_to_numpy,
        )

    def get_sentence_embedding_dimension(self) -> int:
        """Get embedding dimension."""
        model = self.get_model()
        return model.get_sentence_embedding_dimension()

    def cleanup(self) -> None:
        """Force cleanup of the model."""
        with self._lock:
            self._cleanup_model()

    def stats(self) -> dict[str, Any]:
        """Get model manager statistics."""
        stats = {
            "model_name": self.model_name,
            "device": self.device,
            "max_seq_length": self.max_seq_length,
            "batch_size": self.batch_size,
            "model_loaded": self._model is not None,
            "last_used": self._last_used,
            "idle_timeout": self.IDLE_TIMEOUT_SECONDS,
        }

        if torch.cuda.is_available():
            stats.update(
                {
                    "cuda_available": True,
                    "gpu_memory_allocated_gb": torch.cuda.memory_allocated(0)
                    / (1024**3),
                    "gpu_memory_cached_gb": torch.cuda.memory_reserved(0) / (1024**3),
                }
            )
        else:
            stats["cuda_available"] = False

        return stats


def get_embedding_model(model_name: str | None = None) -> EmbeddingModelManager:
    """Get the global embedding model manager instance.

    Args:
        model_name: Override model name (only used on first call)

    Returns:
        Singleton EmbeddingModelManager instance
    """
    global _model_manager

    with _model_lock:
        if _model_manager is None:
            _model_manager = EmbeddingModelManager(model_name)
        return _model_manager


def cleanup_embedding_model() -> None:
    """Cleanup the global embedding model to free memory."""
    global _model_manager

    with _model_lock:
        if _model_manager is not None:
            _model_manager.cleanup()


def reset_embedding_model() -> None:
    """Reset the global model manager (for testing)."""
    global _model_manager

    with _model_lock:
        if _model_manager is not None:
            _model_manager.cleanup()
        _model_manager = None
