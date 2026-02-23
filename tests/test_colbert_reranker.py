"""Tests for ColBERT re-ranker module.

Tests cover:
- ColBERTReranker initialization and configuration
- MaxSim scoring with deterministic mock embeddings
- Projection head key mapping logic
- Skiplist token filtering
- Sync and async rerank interfaces
- Graceful fallback when model unavailable
- Config toggle (enabled/disabled)
- Integration with pipeline.search_laws (mock ChromaDB + mock reranker)
- RerankResult compatibility with existing RAG pipeline
- Singleton lifecycle (get/cleanup/reset)
- Idle timeout cleanup
- Empty input edge cases

All model components (backbone, tokenizer, projection head, HF Hub downloads)
are mocked to avoid downloading the ~800MB model in CI.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import torch

from app.rag.reranker import RerankResult

# =============================================================================
# Fake / Helper classes
# =============================================================================


@dataclass(frozen=True)
class _FakeSettings:
    """Minimal settings stub for ColBERT reranker tests."""

    colbert_reranking_enabled: bool = True
    colbert_reranking_model: str = "fake-org/fake-colbert-model"
    colbert_reranking_top_k: int = 10
    colbert_retrieval_candidates: int = 100
    colbert_device: str = "cpu"
    colbert_batch_size: int = 32
    # Fields needed by pipeline.search_laws
    embedding_model: str = "fake-model"
    chroma_persist_path: str = "/tmp/fake-chroma"
    chroma_host: str | None = None
    chroma_port: int = 8000


@dataclass(frozen=True)
class _FakeSettingsDisabled(_FakeSettings):
    """Settings with ColBERT reranking disabled."""

    colbert_reranking_enabled: bool = False


class _FakeCudaProperties:
    def __init__(self, total_memory_bytes: int) -> None:
        self.total_memory = total_memory_bytes


class _FakeCuda:
    """Fake torch.cuda module for device selection tests."""

    def __init__(
        self,
        *,
        available: bool = False,
        total_memory_gb: float = 8.0,
        allocated_bytes: int = 0,
    ) -> None:
        self._available = available
        self._total_memory_bytes = int(total_memory_gb * (1024**3))
        self._allocated_bytes = allocated_bytes
        self.empty_cache_calls: int = 0

    def is_available(self) -> bool:
        return self._available

    def get_device_properties(self, index: int) -> _FakeCudaProperties:
        return _FakeCudaProperties(self._total_memory_bytes)

    def memory_allocated(self, index: int) -> int:
        return self._allocated_bytes

    def memory_reserved(self, index: int) -> int:
        return self._allocated_bytes

    def empty_cache(self) -> None:
        self.empty_cache_calls += 1


class _FakeTokenizerOutput:
    """Mimics transformers tokenizer output with .to() support."""

    def __init__(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> None:
        self._data: dict[str, torch.Tensor] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }

    def __getitem__(self, key: str) -> torch.Tensor:
        return self._data[key]

    def items(self) -> Any:
        return self._data.items()

    def keys(self) -> Any:
        return self._data.keys()


class _FakeTokenizer:
    """Minimal tokenizer that produces deterministic token IDs."""

    def __init__(self) -> None:
        self.pad_token_id: int = 0
        self._vocab: dict[str, int] = {
            "!": 100,
            '"': 101,
            "#": 102,
            ".": 103,
            ",": 104,
            "?": 105,
            "[Q]": 200,
            "[D]": 201,
            "kaufvertrag": 300,
            "pflichten": 301,
            "vertrag": 302,
            "miete": 303,
        }

    def __call__(
        self,
        texts: list[str],
        padding: str = "max_length",
        truncation: bool = True,
        max_length: int = 256,
        return_tensors: str = "pt",
    ) -> _FakeTokenizerOutput:
        batch_ids = []
        for text in texts:
            # Simple tokenization: split on spaces, map known words, pad
            tokens = text.lower().split()
            ids = [self._vocab.get(token, 999) for token in tokens]
            # Truncate and pad
            ids = ids[:max_length]
            ids = ids + [self.pad_token_id] * (max_length - len(ids))
            batch_ids.append(ids)

        input_ids = torch.tensor(batch_ids, dtype=torch.long)
        attention_mask = (input_ids != self.pad_token_id).long()
        return _FakeTokenizerOutput(input_ids=input_ids, attention_mask=attention_mask)

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        """Encode a single character/word to token IDs."""
        return [self._vocab.get(text, 999)]

    @classmethod
    def from_pretrained(
        cls, model_name: str, trust_remote_code: bool = False
    ) -> _FakeTokenizer:
        return cls()


class _FakeModelOutput:
    """Mimics transformers model output with last_hidden_state."""

    def __init__(self, last_hidden_state: torch.Tensor) -> None:
        self.last_hidden_state = last_hidden_state


class _FakeBackbone:
    """Fake transformer backbone that returns deterministic embeddings."""

    def __init__(self, embedding_dim: int = 768) -> None:
        self.embedding_dim = embedding_dim
        self._device = "cpu"
        self._eval_called = False

    def to(self, device: str) -> _FakeBackbone:
        self._device = device
        return self

    def eval(self) -> None:
        self._eval_called = True

    def __call__(self, **kwargs: Any) -> _FakeModelOutput:
        input_ids = kwargs["input_ids"]
        batch_size, seq_len = input_ids.shape
        # Produce deterministic embeddings based on token IDs.
        # Use simple math instead of torch.manual_seed (which touches
        # torch.cuda internals and breaks when cuda is patched).
        embeddings = torch.zeros(batch_size, seq_len, self.embedding_dim)
        for batch_index in range(batch_size):
            for token_index in range(seq_len):
                token_id = input_ids[batch_index, token_index].item()
                if token_id != 0:  # Skip padding
                    # Deterministic embedding: place energy at index (token_id % dim)
                    # and spread a small amount across nearby dimensions
                    primary_index = token_id % self.embedding_dim
                    embeddings[batch_index, token_index, primary_index] = 1.0
                    secondary_index = (token_id * 7 + 13) % self.embedding_dim
                    embeddings[batch_index, token_index, secondary_index] = 0.5
        return _FakeModelOutput(last_hidden_state=embeddings)

    @classmethod
    def from_pretrained(
        cls, model_name: str, trust_remote_code: bool = False
    ) -> _FakeBackbone:
        return cls()


@dataclass
class _FakeSearchResult:
    """Mimics app.ingestion.embeddings.SearchResult."""

    doc_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    distance: float = 0.2

    @property
    def similarity(self) -> float:
        return max(0.0, 1.0 - self.distance)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset module-level singletons before and after each test."""
    import app.reranking.colbert_reranker as module

    module._colbert_reranker = None
    yield
    module._colbert_reranker = None


@pytest.fixture
def fake_settings() -> _FakeSettings:
    return _FakeSettings()


@pytest.fixture
def fake_settings_disabled() -> _FakeSettingsDisabled:
    return _FakeSettingsDisabled()


@pytest.fixture
def fake_tokenizer() -> _FakeTokenizer:
    return _FakeTokenizer()


@pytest.fixture
def fake_backbone() -> _FakeBackbone:
    return _FakeBackbone()


def _make_fake_projection() -> torch.nn.Linear:
    """Create a deterministic projection head for testing."""
    projection = torch.nn.Linear(768, 128, bias=False)
    torch.manual_seed(42)
    torch.nn.init.orthogonal_(projection.weight)
    return projection


@pytest.fixture
def fake_projection() -> torch.nn.Linear:
    return _make_fake_projection()


def _patch_model_loading(
    fake_settings: _FakeSettings | None = None,
):
    """Context manager that patches all model loading for ColBERTReranker."""
    settings = fake_settings or _FakeSettings()

    # Create fake projection weights
    projection = _make_fake_projection()
    state_dict = {"linear.weight": projection.weight.data.clone()}

    patches = {
        "settings": patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=settings,
        ),
        "auto_tokenizer": patch(
            "transformers.AutoTokenizer.from_pretrained",
            return_value=_FakeTokenizer(),
        ),
        "auto_model": patch(
            "transformers.AutoModel.from_pretrained",
            return_value=_FakeBackbone(),
        ),
        "hf_download": patch(
            "huggingface_hub.hf_hub_download",
            return_value="/fake/path/model.safetensors",
        ),
        "load_file": patch(
            "safetensors.torch.load_file",
            return_value=state_dict,
        ),
    }
    return patches


# =============================================================================
# Tests: ColBERTReranker Initialization
# =============================================================================


class TestColBERTRerankerInit:
    """Test ColBERTReranker initialization and configuration."""

    def test_init_defaults_from_config(self, fake_settings: _FakeSettings) -> None:
        """Reranker uses config values when no explicit args given."""
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()

        assert reranker.model_name == "fake-org/fake-colbert-model"
        assert reranker._device_setting == "cpu"
        assert reranker.batch_size == 32
        assert reranker._loaded is False

    def test_init_explicit_overrides(self, fake_settings: _FakeSettings) -> None:
        """Explicit constructor args override config values."""
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker(
                model_name="custom/model",
                device="cuda",
                batch_size=16,
            )

        assert reranker.model_name == "custom/model"
        assert reranker._device_setting == "cuda"
        assert reranker.batch_size == 16

    def test_stats_before_loading(self, fake_settings: _FakeSettings) -> None:
        """Stats are available even before model is loaded."""
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            stats = reranker.stats()

        assert stats["type"] == "colbert"
        assert stats["model_loaded"] is False
        assert stats["backbone_dim"] == 768
        assert stats["projection_dim"] == 128


# =============================================================================
# Tests: Device Selection
# =============================================================================


class TestDeviceSelection:
    """Test GPU/CPU device selection logic."""

    def test_cpu_explicit(self, fake_settings: _FakeSettings) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker(device="cpu")

        assert reranker._select_device() == "cpu"

    def test_cuda_explicit_available(self, fake_settings: _FakeSettings) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker(device="cuda")

        fake_cuda = _FakeCuda(available=True, total_memory_gb=8.0)
        with patch.object(torch, "cuda", fake_cuda):
            assert reranker._select_device() == "cuda"

    def test_cuda_explicit_unavailable_falls_back(
        self, fake_settings: _FakeSettings
    ) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker(device="cuda")

        fake_cuda = _FakeCuda(available=False)
        with patch.object(torch, "cuda", fake_cuda):
            assert reranker._select_device() == "cpu"

    def test_auto_no_cuda(self, fake_settings: _FakeSettings) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker(device="auto")

        fake_cuda = _FakeCuda(available=False)
        with patch.object(torch, "cuda", fake_cuda):
            assert reranker._select_device() == "cpu"

    def test_auto_cuda_sufficient_memory(self, fake_settings: _FakeSettings) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker(device="auto")

        fake_cuda = _FakeCuda(available=True, total_memory_gb=8.0, allocated_bytes=0)
        with patch.object(torch, "cuda", fake_cuda):
            assert reranker._select_device() == "cuda"

    def test_auto_cuda_insufficient_memory(self, fake_settings: _FakeSettings) -> None:
        # Nearly all memory allocated
        allocated = int(7.5 * (1024**3))
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker(device="auto")

        fake_cuda = _FakeCuda(
            available=True,
            total_memory_gb=8.0,
            allocated_bytes=allocated,
        )
        with patch.object(torch, "cuda", fake_cuda):
            assert reranker._select_device() == "cpu"


# =============================================================================
# Tests: Projection Head Key Mapping
# =============================================================================


class TestProjectionKeyMapping:
    """Test _map_projection_keys handles various state dict formats."""

    def test_weight_key_already_correct(self) -> None:
        from app.reranking.colbert_reranker import ColBERTReranker

        tensor = torch.randn(128, 768)
        state_dict = {"weight": tensor}
        result = ColBERTReranker._map_projection_keys(state_dict)
        assert "weight" in result
        assert torch.equal(result["weight"], tensor)

    def test_linear_weight_key(self) -> None:
        from app.reranking.colbert_reranker import ColBERTReranker

        tensor = torch.randn(128, 768)
        state_dict = {"linear.weight": tensor}
        result = ColBERTReranker._map_projection_keys(state_dict)
        assert "weight" in result
        assert torch.equal(result["weight"], tensor)

    def test_zero_weight_key(self) -> None:
        from app.reranking.colbert_reranker import ColBERTReranker

        tensor = torch.randn(128, 768)
        state_dict = {"0.weight": tensor}
        result = ColBERTReranker._map_projection_keys(state_dict)
        assert "weight" in result
        assert torch.equal(result["weight"], tensor)

    def test_dense_weight_key(self) -> None:
        from app.reranking.colbert_reranker import ColBERTReranker

        tensor = torch.randn(128, 768)
        state_dict = {"dense.weight": tensor}
        result = ColBERTReranker._map_projection_keys(state_dict)
        assert "weight" in result
        assert torch.equal(result["weight"], tensor)

    def test_projection_weight_key(self) -> None:
        from app.reranking.colbert_reranker import ColBERTReranker

        tensor = torch.randn(128, 768)
        state_dict = {"projection.weight": tensor}
        result = ColBERTReranker._map_projection_keys(state_dict)
        assert "weight" in result
        assert torch.equal(result["weight"], tensor)

    def test_shape_match_fallback(self) -> None:
        from app.reranking.colbert_reranker import ColBERTReranker

        tensor = torch.randn(128, 768)
        state_dict = {"some_weird_key": tensor}
        result = ColBERTReranker._map_projection_keys(state_dict)
        assert "weight" in result
        assert torch.equal(result["weight"], tensor)

    def test_no_matching_key_raises(self) -> None:
        from app.reranking.colbert_reranker import ColBERTReranker

        # Wrong shape, won't match
        tensor = torch.randn(64, 64)
        state_dict = {"unknown_key": tensor}
        with pytest.raises(RuntimeError, match="Cannot identify projection weight"):
            ColBERTReranker._map_projection_keys(state_dict)

    def test_multiple_candidates_raises(self) -> None:
        from app.reranking.colbert_reranker import ColBERTReranker

        tensor_a = torch.randn(128, 768)
        tensor_b = torch.randn(128, 768)
        state_dict = {"key_a": tensor_a, "key_b": tensor_b}
        # Two tensors with right shape but no known key name
        with pytest.raises(RuntimeError, match="Cannot identify projection weight"):
            ColBERTReranker._map_projection_keys(state_dict)


# =============================================================================
# Tests: Skiplist Building
# =============================================================================


class TestSkiplistBuilding:
    """Test skiplist token ID construction."""

    def test_skiplist_includes_punctuation_tokens(
        self, fake_settings: _FakeSettings
    ) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            reranker._tokenizer = _FakeTokenizer()
            skiplist = reranker._build_skiplist()

        # Should include IDs for punctuation chars in our fake vocab
        assert 100 in skiplist  # "!"
        assert 101 in skiplist  # '"'
        assert 103 in skiplist  # "."
        assert 104 in skiplist  # ","
        assert 105 in skiplist  # "?"

    def test_skiplist_includes_pad_token(self, fake_settings: _FakeSettings) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            reranker._tokenizer = _FakeTokenizer()
            skiplist = reranker._build_skiplist()

        assert 0 in skiplist  # pad token

    def test_skiplist_excludes_content_tokens(
        self, fake_settings: _FakeSettings
    ) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            reranker._tokenizer = _FakeTokenizer()
            skiplist = reranker._build_skiplist()

        # Content tokens should NOT be in skiplist
        assert 300 not in skiplist  # "kaufvertrag"
        assert 301 not in skiplist  # "pflichten"

    def test_skiplist_empty_when_no_tokenizer(
        self, fake_settings: _FakeSettings
    ) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=fake_settings,
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            reranker._tokenizer = None
            skiplist = reranker._build_skiplist()

        assert skiplist == set()


# =============================================================================
# Tests: MaxSim Scoring
# =============================================================================


class TestMaxSimScoring:
    """Test MaxSim computation with deterministic embeddings."""

    def test_maxsim_identical_embeddings_gives_high_score(self) -> None:
        """Identical query and document should produce high MaxSim score."""
        from app.reranking.colbert_reranker import ColBERTReranker

        dimension = 128
        query_length = 4
        doc_length = 6

        # Create normalized embeddings
        torch.manual_seed(123)
        query_emb = torch.randn(1, query_length, dimension)
        query_emb = torch.nn.functional.normalize(query_emb, p=2, dim=-1)

        # Document contains same vectors as query (plus extras)
        document_emb = torch.zeros(1, doc_length, dimension)
        document_emb[0, :query_length] = query_emb[0]
        # Add some noise vectors
        document_emb[0, query_length:] = torch.randn(
            doc_length - query_length, dimension
        )
        document_emb = torch.nn.functional.normalize(document_emb, p=2, dim=-1)

        # No tokens in skiplist
        query_ids = torch.tensor([[10, 20, 30, 40]])

        # Instantiate without loading model
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            reranker = ColBERTReranker()
        reranker._skiplist_token_ids = set()

        scores = reranker._compute_maxsim(query_emb, document_emb, query_ids)

        # Each query token should find itself in the document → max sim ≈ 1.0
        # Sum should be close to query_length
        assert len(scores) == 1
        assert scores[0] > query_length * 0.9

    def test_maxsim_orthogonal_embeddings_gives_low_score(self) -> None:
        """Orthogonal query and document should produce low MaxSim score."""
        from app.reranking.colbert_reranker import ColBERTReranker

        dimension = 128

        # Create orthogonal embeddings
        query_emb = torch.zeros(1, 2, dimension)
        query_emb[0, 0, 0] = 1.0  # First basis vector
        query_emb[0, 1, 1] = 1.0  # Second basis vector

        document_emb = torch.zeros(1, 2, dimension)
        document_emb[0, 0, 2] = 1.0  # Third basis vector
        document_emb[0, 1, 3] = 1.0  # Fourth basis vector

        query_ids = torch.tensor([[10, 20]])

        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            reranker = ColBERTReranker()
        reranker._skiplist_token_ids = set()

        scores = reranker._compute_maxsim(query_emb, document_emb, query_ids)

        assert len(scores) == 1
        assert scores[0] == pytest.approx(0.0, abs=1e-6)

    def test_maxsim_skiplist_filtering(self) -> None:
        """Skiplist tokens should be excluded from scoring."""
        from app.reranking.colbert_reranker import ColBERTReranker

        dimension = 128

        # Query: 3 tokens, one of which is in skiplist (token_id=100)
        query_emb = torch.zeros(1, 3, dimension)
        query_emb[0, 0, 0] = 1.0
        query_emb[0, 1, 1] = 1.0
        query_emb[0, 2, 2] = 1.0  # This one will be skipped

        # Document: matches only the skipped token
        document_emb = torch.zeros(1, 2, dimension)
        document_emb[0, 0, 2] = 1.0  # Matches skipped query token
        document_emb[0, 1, 3] = 1.0  # Matches nothing

        # Token 100 is in skiplist
        query_ids = torch.tensor([[10, 20, 100]])

        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            reranker = ColBERTReranker()
        reranker._skiplist_token_ids = {100}

        scores = reranker._compute_maxsim(query_emb, document_emb, query_ids)

        # Only tokens 10, 20 are scored; neither matches document
        assert len(scores) == 1
        assert scores[0] == pytest.approx(0.0, abs=1e-6)

    def test_maxsim_all_skiplist_returns_zero(self) -> None:
        """All query tokens in skiplist should produce zero score."""
        from app.reranking.colbert_reranker import ColBERTReranker

        dimension = 128
        query_emb = torch.randn(1, 3, dimension)
        document_emb = torch.randn(2, 5, dimension)
        query_ids = torch.tensor([[100, 101, 102]])

        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            reranker = ColBERTReranker()
        reranker._skiplist_token_ids = {100, 101, 102}

        scores = reranker._compute_maxsim(query_emb, document_emb, query_ids)

        assert scores == [0.0, 0.0]

    def test_maxsim_multiple_documents(self) -> None:
        """Correctly scores multiple documents independently."""
        from app.reranking.colbert_reranker import ColBERTReranker

        dimension = 128

        # Query: single meaningful token
        query_emb = torch.zeros(1, 1, dimension)
        query_emb[0, 0, 0] = 1.0

        # Doc 0: matches query perfectly, Doc 1: doesn't match
        document_emb = torch.zeros(2, 2, dimension)
        document_emb[0, 0, 0] = 1.0  # Doc 0, token 0 matches
        document_emb[0, 1, 5] = 1.0  # Doc 0, token 1 doesn't match
        document_emb[1, 0, 3] = 1.0  # Doc 1, no match
        document_emb[1, 1, 4] = 1.0  # Doc 1, no match

        query_ids = torch.tensor([[10]])

        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            reranker = ColBERTReranker()
        reranker._skiplist_token_ids = set()

        scores = reranker._compute_maxsim(query_emb, document_emb, query_ids)

        assert len(scores) == 2
        assert scores[0] > scores[1]  # Doc 0 should score higher
        assert scores[0] == pytest.approx(1.0, abs=1e-6)
        assert scores[1] == pytest.approx(0.0, abs=1e-6)


# =============================================================================
# Tests: Sync Rerank
# =============================================================================


class TestRerankSync:
    """Test synchronous rerank interface."""

    def _create_loaded_reranker(self) -> Any:
        """Create a ColBERTReranker with mocked model components loaded."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        from app.reranking.colbert_reranker import ColBERTReranker

        reranker = ColBERTReranker()
        reranker._ensure_loaded()

        for patcher in patches.values():
            patcher.stop()

        return reranker

    def test_empty_documents_returns_empty(self) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()

        result = reranker.rerank_sync(query="test", documents=[])
        assert result == []

    def test_rerank_returns_rerank_results(self) -> None:
        """rerank_sync returns list[RerankResult] sorted by score."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            documents = [
                "Ein Kaufvertrag nach BGB",
                "Der Mietvertrag regelt",
                "Grundrechte im Grundgesetz",
            ]

            results = reranker.rerank_sync(
                query="Kaufvertrag Pflichten",
                documents=documents,
            )

            assert len(results) == 3
            assert all(isinstance(result, RerankResult) for result in results)
            # Results should be sorted by score descending
            for index in range(len(results) - 1):
                assert results[index].score >= results[index + 1].score
            # Each result should have valid index and text
            for result in results:
                assert 0 <= result.index < len(documents)
                assert result.text == documents[result.index]
        finally:
            for patcher in patches.values():
                patcher.stop()

    def test_rerank_top_k(self) -> None:
        """top_k limits returned results."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            documents = [f"Document {index}" for index in range(10)]

            results = reranker.rerank_sync(
                query="test query",
                documents=documents,
                top_k=3,
            )

            assert len(results) == 3
        finally:
            for patcher in patches.values():
                patcher.stop()

    def test_rerank_without_text(self) -> None:
        """return_text=False produces empty text in results."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            documents = ["Document A", "Document B"]

            results = reranker.rerank_sync(
                query="test",
                documents=documents,
                return_text=False,
            )

            for result in results:
                assert result.text == ""
        finally:
            for patcher in patches.values():
                patcher.stop()

    def test_rerank_model_failure_raises(self) -> None:
        """RuntimeError when model scoring fails."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            # Ensure model loads first
            reranker._ensure_loaded()

            # Break the backbone to cause a scoring error
            reranker._backbone = None

            with pytest.raises(RuntimeError, match="ColBERT re-ranking failed"):
                reranker.rerank_sync(query="test", documents=["doc"])
        finally:
            for patcher in patches.values():
                patcher.stop()


# =============================================================================
# Tests: Async Rerank
# =============================================================================


class TestRerankAsync:
    """Test asynchronous rerank interface."""

    async def test_async_rerank_returns_same_as_sync(self) -> None:
        """Async rerank wraps sync and returns identical results."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            documents = ["Document alpha", "Document beta"]

            sync_results = reranker.rerank_sync(
                query="alpha test",
                documents=documents,
                top_k=2,
            )
            async_results = await reranker.rerank(
                query="alpha test",
                documents=documents,
                top_k=2,
            )

            assert len(sync_results) == len(async_results)
            for sync_result, async_result in zip(
                sync_results, async_results, strict=False
            ):
                assert sync_result.index == async_result.index
                assert sync_result.score == pytest.approx(async_result.score)
                assert sync_result.text == async_result.text
        finally:
            for patcher in patches.values():
                patcher.stop()

    async def test_async_rerank_empty_documents(self) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()

        result = await reranker.rerank(query="test", documents=[])
        assert result == []


# =============================================================================
# Tests: Health Check
# =============================================================================


class TestHealthCheck:
    """Test health_check method."""

    async def test_health_check_success(self) -> None:
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            healthy = await reranker.health_check()
            assert healthy is True
        finally:
            for patcher in patches.values():
                patcher.stop()

    async def test_health_check_failure(self) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()

        # Patch model loading to fail
        with patch.object(
            reranker,
            "_load_model",
            side_effect=RuntimeError("Model not found"),
        ):
            healthy = await reranker.health_check()
            assert healthy is False


# =============================================================================
# Tests: Idle Timeout & Cleanup
# =============================================================================


class TestIdleTimeoutAndCleanup:
    """Test model idle timeout and cleanup behavior."""

    def test_cleanup_unloads_model(self) -> None:
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            reranker._ensure_loaded()
            assert reranker._loaded is True

            reranker.cleanup()
            assert reranker._loaded is False
            assert reranker._backbone is None
            assert reranker._tokenizer is None
            assert reranker._projection is None
            assert reranker._skiplist_token_ids is None
        finally:
            for patcher in patches.values():
                patcher.stop()

    def test_idle_timeout_triggers_reload(self) -> None:
        """Model is unloaded and reloaded after idle timeout."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            reranker.IDLE_TIMEOUT_SECONDS = 0  # Immediate timeout

            reranker._ensure_loaded()
            assert reranker._loaded is True

            # Simulate time passing
            reranker._last_used = time.time() - 10

            # This should trigger cleanup + reload
            reranker._ensure_loaded()
            assert reranker._loaded is True
        finally:
            for patcher in patches.values():
                patcher.stop()

    async def test_close_is_async_cleanup(self) -> None:
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            reranker._ensure_loaded()

            await reranker.close()
            assert reranker._loaded is False
        finally:
            for patcher in patches.values():
                patcher.stop()


# =============================================================================
# Tests: Singleton Lifecycle
# =============================================================================


class TestSingletonLifecycle:
    """Test get/cleanup/reset singleton functions."""

    def test_get_returns_singleton(self) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            from app.reranking.colbert_reranker import get_colbert_reranker

            instance_a = get_colbert_reranker()
            instance_b = get_colbert_reranker()

        assert instance_a is instance_b

    def test_reset_allows_new_instance(self) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            from app.reranking.colbert_reranker import (
                get_colbert_reranker,
                reset_colbert_reranker,
            )

            instance_a = get_colbert_reranker()
            reset_colbert_reranker()
            instance_b = get_colbert_reranker()

        assert instance_a is not instance_b

    def test_cleanup_does_not_reset(self) -> None:
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import (
                cleanup_colbert_reranker,
                get_colbert_reranker,
            )

            instance = get_colbert_reranker()
            instance._ensure_loaded()
            assert instance._loaded is True

            cleanup_colbert_reranker()
            assert instance._loaded is False

            # Same instance is returned
            assert get_colbert_reranker() is instance
        finally:
            for patcher in patches.values():
                patcher.stop()

    def test_constructor_args_ignored_after_first_call(self) -> None:
        with patch(
            "app.reranking.colbert_reranker.get_settings",
            return_value=_FakeSettings(),
        ):
            from app.reranking.colbert_reranker import get_colbert_reranker

            instance_a = get_colbert_reranker(model_name="model-1")
            instance_b = get_colbert_reranker(model_name="model-2")

        assert instance_a is instance_b
        # First call's args are used; second call's "model-2" is ignored
        assert instance_a.model_name == "model-1"


# =============================================================================
# Tests: RerankResult Compatibility
# =============================================================================


class TestRerankResultCompatibility:
    """Ensure ColBERT results are compatible with existing RAG pipeline."""

    def test_rerank_result_to_dict(self) -> None:
        """RerankResult.to_dict() works with ColBERT scores."""
        result = RerankResult(index=3, score=15.7892, text="Some legal text")
        result_dict = result.to_dict()
        assert result_dict["index"] == 3
        assert result_dict["score"] == 15.7892  # rounded to 4 decimals
        assert "text" not in result_dict  # to_dict excludes text

    def test_rerank_result_fields(self) -> None:
        """RerankResult has expected fields for pipeline integration."""
        result = RerankResult(index=0, score=10.5, text="doc text")
        assert result.index == 0
        assert result.score == 10.5
        assert result.text == "doc text"


# =============================================================================
# Tests: Config Toggle
# =============================================================================


class TestConfigToggle:
    """Test that ColBERT reranking respects enabled/disabled config."""

    def test_config_defaults_disabled(self) -> None:
        """Default config has ColBERT reranking disabled."""
        from app.config import Settings

        settings = Settings()
        assert settings.colbert_reranking_enabled is False

    def test_config_model_default(self) -> None:
        from app.config import Settings

        settings = Settings()
        assert (
            settings.colbert_reranking_model
            == "VAGOsolutions/SauerkrautLM-Reason-EuroColBERT"
        )

    def test_config_top_k_default(self) -> None:
        from app.config import Settings

        settings = Settings()
        assert settings.colbert_reranking_top_k == 10

    def test_config_retrieval_candidates_default(self) -> None:
        from app.config import Settings

        settings = Settings()
        assert settings.colbert_retrieval_candidates == 100

    def test_config_device_default(self) -> None:
        from app.config import Settings

        settings = Settings()
        assert settings.colbert_device == "auto"

    def test_config_batch_size_default(self) -> None:
        from app.config import Settings

        settings = Settings()
        assert settings.colbert_batch_size == 32


# =============================================================================
# Tests: Integration with pipeline.search_laws
# =============================================================================


class TestPipelineSearchLawsIntegration:
    """Test ColBERT reranking integration in pipeline.search_laws."""

    def test_reranking_disabled_uses_original_n_results(self) -> None:
        """When disabled, search_laws uses original n_results (no over-retrieval)."""
        fake_results = [
            _FakeSearchResult(
                doc_id=f"doc_{index}",
                content=f"Content {index}",
                metadata={"law_abbrev": "BGB", "norm_id": f"§ {index}"},
                distance=0.1 * index,
            )
            for index in range(5)
        ]

        mock_store = MagicMock()
        mock_store.search.return_value = fake_results

        settings = _FakeSettingsDisabled()

        with (
            patch(
                "app.ingestion.pipeline.get_settings",
                return_value=settings,
            ),
            patch(
                "app.ingestion.pipeline.GermanLawEmbeddingStore",
                return_value=mock_store,
            ),
        ):
            from app.ingestion.pipeline import search_laws

            results = search_laws("test query", n_results=5)

        # Should search with n_results=5, not 100
        mock_store.search.assert_called_once()
        call_kwargs = mock_store.search.call_args
        assert (
            call_kwargs[1].get(
                "n_results", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
            )
            == 5
        )
        assert len(results) == 5

    def test_reranking_enabled_over_retrieves(self) -> None:
        """When enabled, search_laws retrieves colbert_retrieval_candidates."""
        fake_results = [
            _FakeSearchResult(
                doc_id=f"doc_{index}",
                content=f"Content {index}",
                metadata={"law_abbrev": "BGB", "norm_id": f"§ {index}"},
                distance=0.1,
            )
            for index in range(5)
        ]

        mock_store = MagicMock()
        mock_store.search.return_value = fake_results

        settings = _FakeSettings()  # colbert_reranking_enabled=True

        with (
            patch(
                "app.ingestion.pipeline.get_settings",
                return_value=settings,
            ),
            patch(
                "app.ingestion.pipeline.GermanLawEmbeddingStore",
                return_value=mock_store,
            ),
            patch(
                "app.ingestion.pipeline._colbert_rerank_results",
                return_value=fake_results[:3],
            ) as mock_rerank,
        ):
            from app.ingestion.pipeline import search_laws

            search_laws("test query", n_results=5)

        # Should have retrieved max(5, 100) = 100 candidates
        call_args = mock_store.search.call_args
        assert (
            call_args[1].get(
                "n_results", call_args[0][1] if len(call_args[0]) > 1 else None
            )
            == 100
        )

        # Should have called reranking
        mock_rerank.assert_called_once()

    def test_colbert_rerank_results_graceful_fallback(self) -> None:
        """_colbert_rerank_results falls back on error."""
        fake_results = [
            _FakeSearchResult(
                doc_id=f"doc_{index}",
                content=f"Content {index}",
                metadata={},
                distance=0.1 * index,
            )
            for index in range(5)
        ]

        with patch(
            "app.reranking.colbert_reranker.get_colbert_reranker",
            side_effect=RuntimeError("Model download failed"),
        ):
            from app.ingestion.pipeline import _colbert_rerank_results

            results = _colbert_rerank_results("test", fake_results, top_k=3)

        # Should fall back to original results, truncated to top_k
        assert len(results) == 3


# =============================================================================
# Tests: Integration with RAG Pipeline
# =============================================================================


class TestRAGPipelineRerankerSelection:
    """Test RAG pipeline selects correct reranker based on config.

    The ``reranker`` property imports ``get_settings`` locally via
    ``from app.config import get_settings``, so we must patch
    ``app.config.get_settings`` (the definition site) rather than
    ``app.rag.pipeline.get_settings`` (which doesn't exist at module scope).

    Similarly, ``get_colbert_reranker`` and ``get_reranker`` are imported
    locally inside the property, so we patch them at their definition sites.
    """

    def test_colbert_enabled_selects_colbert_reranker(self) -> None:
        """When colbert_reranking_enabled=True, RAG pipeline uses ColBERT."""
        settings = _FakeSettings()
        mock_colbert = MagicMock()

        with (
            patch(
                "app.config.get_settings",
                return_value=settings,
            ),
            patch(
                "app.rag.pipeline.get_llm_client",
                return_value=MagicMock(),
            ),
            patch(
                "app.reranking.colbert_reranker.get_colbert_reranker",
                return_value=mock_colbert,
            ),
        ):
            from app.rag.pipeline import RAGPipeline

            pipeline = RAGPipeline(use_reranker=True)
            # Access the reranker property to trigger selection
            reranker = pipeline.reranker

        assert reranker is mock_colbert

    def test_colbert_disabled_selects_tei_reranker(self) -> None:
        """When colbert_reranking_enabled=False, RAG pipeline uses TEI."""
        settings = _FakeSettingsDisabled()
        mock_tei = MagicMock()

        with (
            patch(
                "app.config.get_settings",
                return_value=settings,
            ),
            patch(
                "app.rag.pipeline.get_llm_client",
                return_value=MagicMock(),
            ),
            patch(
                "app.rag.reranker.get_reranker",
                return_value=mock_tei,
            ),
        ):
            from app.rag.pipeline import RAGPipeline

            pipeline = RAGPipeline(use_reranker=True)
            # Access the reranker property
            reranker = pipeline.reranker

        assert reranker is mock_tei

    def test_colbert_init_failure_falls_back_to_tei(self) -> None:
        """If ColBERT fails to init, RAG pipeline falls back to TEI."""
        settings = _FakeSettings()
        mock_tei = MagicMock()

        with (
            patch(
                "app.config.get_settings",
                return_value=settings,
            ),
            patch(
                "app.rag.pipeline.get_llm_client",
                return_value=MagicMock(),
            ),
            patch(
                "app.reranking.colbert_reranker.get_colbert_reranker",
                side_effect=RuntimeError("CUDA error"),
            ),
            patch(
                "app.rag.reranker.get_reranker",
                return_value=mock_tei,
            ),
        ):
            from app.rag.pipeline import RAGPipeline

            pipeline = RAGPipeline(use_reranker=True)
            reranker = pipeline.reranker

        assert reranker is mock_tei

    def test_use_reranker_false_returns_none(self) -> None:
        """When use_reranker=False, reranker property returns None."""
        with patch(
            "app.rag.pipeline.get_llm_client",
            return_value=MagicMock(),
        ):
            from app.rag.pipeline import RAGPipeline

            pipeline = RAGPipeline(use_reranker=False)
            assert pipeline.reranker is None


# =============================================================================
# Tests: Module __init__ exports
# =============================================================================


class TestModuleExports:
    """Test that app.reranking exports expected symbols."""

    def test_module_exports(self) -> None:
        from app.reranking import __all__

        assert "ColBERTReranker" in __all__
        assert "get_colbert_reranker" in __all__
        assert "cleanup_colbert_reranker" in __all__
        assert "reset_colbert_reranker" in __all__

    def test_direct_imports(self) -> None:
        from app.reranking import (
            ColBERTReranker,
            cleanup_colbert_reranker,
            get_colbert_reranker,
            reset_colbert_reranker,
        )

        assert ColBERTReranker is not None
        assert callable(get_colbert_reranker)
        assert callable(cleanup_colbert_reranker)
        assert callable(reset_colbert_reranker)


# =============================================================================
# Tests: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_single_document_reranking(self) -> None:
        """Reranking a single document works correctly."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            results = reranker.rerank_sync(
                query="test",
                documents=["single document"],
                top_k=1,
            )

            assert len(results) == 1
            assert results[0].index == 0
            assert results[0].text == "single document"
        finally:
            for patcher in patches.values():
                patcher.stop()

    def test_top_k_larger_than_documents(self) -> None:
        """top_k > len(documents) returns all documents."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            documents = ["Doc A", "Doc B"]

            results = reranker.rerank_sync(
                query="test",
                documents=documents,
                top_k=100,
            )

            assert len(results) == 2
        finally:
            for patcher in patches.values():
                patcher.stop()

    def test_top_k_none_returns_all(self) -> None:
        """top_k=None returns all documents."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            documents = ["Doc A", "Doc B", "Doc C"]

            results = reranker.rerank_sync(
                query="test",
                documents=documents,
                top_k=None,
            )

            assert len(results) == 3
        finally:
            for patcher in patches.values():
                patcher.stop()

    def test_stats_after_loading(self) -> None:
        """Stats reflect loaded state."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            reranker._ensure_loaded()
            stats = reranker.stats()

            assert stats["model_loaded"] is True
            assert stats["type"] == "colbert"
            assert "skiplist_token_count" in stats
            assert stats["skiplist_token_count"] > 0
        finally:
            for patcher in patches.values():
                patcher.stop()

    def test_double_cleanup_is_safe(self) -> None:
        """Calling cleanup twice does not raise."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            reranker._ensure_loaded()

            reranker.cleanup()
            reranker.cleanup()  # Should not raise
            assert reranker._loaded is False
        finally:
            for patcher in patches.values():
                patcher.stop()

    def test_rerank_after_cleanup_reloads(self) -> None:
        """Reranking after cleanup triggers model reload."""
        patches = _patch_model_loading()
        for patcher in patches.values():
            patcher.start()

        try:
            from app.reranking.colbert_reranker import ColBERTReranker

            reranker = ColBERTReranker()
            reranker._ensure_loaded()
            reranker.cleanup()
            assert reranker._loaded is False

            # Should reload and succeed
            results = reranker.rerank_sync(
                query="test",
                documents=["doc"],
            )
            assert reranker._loaded is True
            assert len(results) == 1
        finally:
            for patcher in patches.values():
                patcher.stop()
