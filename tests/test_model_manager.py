"""Unit tests for `app.ingestion.model_manager`.

These tests are designed to:
- Avoid loading real embedding models or touching GPU hardware.
- Exercise the public singleton API: `get_embedding_model`, `cleanup_embedding_model`,
  and `reset_embedding_model`.
- Cover key behaviors: device selection, model loading, CPU fallback, encode delegation,
  idle-timeout cleanup, and stats reporting.

We patch module-level dependencies (`torch`, `SentenceTransformer`, and `get_settings`)
inside `app.ingestion.model_manager` to keep tests deterministic and fast.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import pytest


@dataclass(frozen=True)
class _FakeSettings:
    embedding_model: str = "fake-model"
    # model_manager only uses embedding_model today (but keep room for extension)


class _FakeCudaProperties:
    def __init__(self, total_memory_bytes: int) -> None:
        self.total_memory = total_memory_bytes


class _FakeCuda:
    def __init__(
        self, *, available: bool, total_memory_gb: float, allocated_bytes: int
    ):
        self._available = available
        self._allocated_bytes = allocated_bytes
        self._total_memory_bytes = int(total_memory_gb * (1024**3))
        self.empty_cache_calls: int = 0
        self.memory_reserved_calls: int = 0

    def is_available(self) -> bool:
        return self._available

    def get_device_properties(self, index: int) -> _FakeCudaProperties:
        assert index == 0
        return _FakeCudaProperties(total_memory_bytes=self._total_memory_bytes)

    def memory_allocated(self, index: int) -> int:
        assert index == 0
        return self._allocated_bytes

    def memory_reserved(self, index: int) -> int:
        assert index == 0
        self.memory_reserved_calls += 1
        # Return something >= allocated for realism
        return max(self._allocated_bytes, int(0.5 * self._total_memory_bytes))

    def empty_cache(self) -> None:
        self.empty_cache_calls += 1


class _FakeTorchBackendsCuda:
    def __init__(self) -> None:
        self.flash_sdp_enabled_calls: int = 0

    def enable_flash_sdp(self, enabled: bool) -> None:
        # Called in a suppress(Exception) block; just record that it happened.
        assert enabled is True
        self.flash_sdp_enabled_calls += 1


class _FakeTorchBackends:
    def __init__(self) -> None:
        self.cuda = _FakeTorchBackendsCuda()


class _FakeParameter:
    def __init__(self, device: str) -> None:
        self.device = device


class _FakeSentenceTransformerModel:
    """Mimics enough of SentenceTransformer for this module."""

    def __init__(self, model_name: str, *, init_kwargs: dict[str, Any] | None = None):
        self.model_name = model_name
        self.init_kwargs = init_kwargs or {}
        self.max_seq_length: int = 0
        self._device: str = "cpu"
        self._eval_calls: int = 0
        self.encode_calls: list[dict[str, Any]] = []

    def to(self, device: str) -> _FakeSentenceTransformerModel:
        self._device = device
        return self

    def eval(self) -> None:
        self._eval_calls += 1

    def parameters(self) -> Any:
        # The production code calls `next(model.parameters()).device`, so this must
        # return an iterator, not a list.
        yield _FakeParameter(device=self._device)

    def get_sentence_embedding_dimension(self) -> int:
        return 3

    def encode(
        self,
        sentences: list[str] | str,
        batch_size: int | None = None,
        show_progress_bar: bool = False,
        convert_to_numpy: bool = True,
    ) -> np.ndarray:
        if isinstance(sentences, str):
            normalized_sentences = [sentences]
        else:
            normalized_sentences = list(sentences)

        self.encode_calls.append(
            {
                "sentences_count": len(normalized_sentences),
                "batch_size": batch_size,
                "show_progress_bar": show_progress_bar,
                "convert_to_numpy": convert_to_numpy,
            }
        )

        vectors = np.array(
            [
                [float(index), float(index), float(index)]
                for index in range(len(normalized_sentences))
            ],
            dtype=np.float32,
        )
        return vectors


class _FakeSentenceTransformerFactory:
    """Factory callable to patch `SentenceTransformer` in the module."""

    def __init__(self) -> None:
        self.created_models: list[_FakeSentenceTransformerModel] = []
        self.raise_on_init: Exception | None = None

    def __call__(self, model_name: str, **kwargs: Any) -> _FakeSentenceTransformerModel:
        if self.raise_on_init is not None:
            raise self.raise_on_init
        model = _FakeSentenceTransformerModel(model_name, init_kwargs=kwargs)
        self.created_models.append(model)
        return model


def _patch_model_manager_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cuda_available: bool,
    total_gpu_memory_gb: float = 12.0,
    allocated_gpu_bytes: int = 0,
    sentence_transformer_factory: _FakeSentenceTransformerFactory | None = None,
    settings: _FakeSettings | None = None,
) -> dict[str, Any]:
    """Patch `app.ingestion.model_manager` dependencies in one place."""
    from app.ingestion import model_manager as model_manager_module

    fake_settings = settings or _FakeSettings()

    monkeypatch.setattr(
        model_manager_module,
        "get_settings",
        lambda: fake_settings,
        raising=True,
    )

    fake_cuda = _FakeCuda(
        available=cuda_available,
        total_memory_gb=total_gpu_memory_gb,
        allocated_bytes=allocated_gpu_bytes,
    )

    # Patch torch module used by model_manager.
    fake_torch = type(
        "_FakeTorch",
        (),
        {
            "cuda": fake_cuda,
            "backends": _FakeTorchBackends(),
        },
    )()
    monkeypatch.setattr(model_manager_module, "torch", fake_torch, raising=True)

    factory = sentence_transformer_factory or _FakeSentenceTransformerFactory()
    monkeypatch.setattr(
        model_manager_module, "SentenceTransformer", factory, raising=True
    )

    # Ensure clean singleton per test.
    model_manager_module.reset_embedding_model()

    return {
        "module": model_manager_module,
        "torch": fake_torch,
        "cuda": fake_cuda,
        "factory": factory,
    }


def test_get_embedding_model_is_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = _patch_model_manager_dependencies(monkeypatch, cuda_available=False)
    model_manager_module = patched["module"]

    first_manager = model_manager_module.get_embedding_model()
    second_manager = model_manager_module.get_embedding_model()

    assert first_manager is second_manager


def test_reset_embedding_model_creates_new_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = _patch_model_manager_dependencies(monkeypatch, cuda_available=False)
    model_manager_module = patched["module"]

    first_manager = model_manager_module.get_embedding_model()
    model_manager_module.reset_embedding_model()
    second_manager = model_manager_module.get_embedding_model()

    assert second_manager is not first_manager


def test_select_device_uses_cpu_when_cuda_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = _patch_model_manager_dependencies(monkeypatch, cuda_available=False)
    model_manager_module = patched["module"]

    manager = model_manager_module.get_embedding_model()
    assert manager.device == "cpu"
    # CPU defaults
    assert manager.batch_size == 8
    assert manager.max_seq_length <= 1024


def test_select_device_uses_cuda_when_memory_sufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Plenty of free memory: total 12GB, allocated 0GB
    patched = _patch_model_manager_dependencies(
        monkeypatch,
        cuda_available=True,
        total_gpu_memory_gb=12.0,
        allocated_gpu_bytes=0,
    )
    model_manager_module = patched["module"]

    manager = model_manager_module.get_embedding_model()
    assert manager.device == "cuda"
    # GPU defaults
    assert manager.batch_size == 1


def test_select_device_falls_back_to_cpu_when_memory_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Free memory < MIN_GPU_MEMORY_GB (2GB). Example: total 3GB, allocated 2.5GB => free 0.5GB.
    allocated_bytes = int(2.5 * (1024**3))
    patched = _patch_model_manager_dependencies(
        monkeypatch,
        cuda_available=True,
        total_gpu_memory_gb=3.0,
        allocated_gpu_bytes=allocated_bytes,
    )
    model_manager_module = patched["module"]

    manager = model_manager_module.get_embedding_model()
    assert manager.device == "cpu"


def test_load_model_passes_trust_remote_code_for_jina_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _FakeSentenceTransformerFactory()
    patched = _patch_model_manager_dependencies(
        monkeypatch,
        cuda_available=False,
        sentence_transformer_factory=factory,
        settings=_FakeSettings(embedding_model="jinaai/jina-embeddings-v2-base-de"),
    )
    model_manager_module = patched["module"]

    manager = model_manager_module.get_embedding_model()
    _ = manager.get_model()

    assert factory.created_models, "Expected model to be created"
    created = factory.created_models[0]
    assert created.model_name == "jinaai/jina-embeddings-v2-base-de"
    assert created.init_kwargs.get("trust_remote_code") is True


def test_encode_delegates_to_model_with_default_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _FakeSentenceTransformerFactory()
    patched = _patch_model_manager_dependencies(
        monkeypatch, cuda_available=False, sentence_transformer_factory=factory
    )
    model_manager_module = patched["module"]

    manager = model_manager_module.get_embedding_model()
    embeddings = manager.encode(["a", "b"])

    assert isinstance(embeddings, np.ndarray)
    assert embeddings.shape == (2, 3)

    created_model = factory.created_models[0]
    assert created_model.encode_calls[-1]["batch_size"] == manager.batch_size


def test_get_sentence_embedding_dimension_uses_loaded_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _FakeSentenceTransformerFactory()
    patched = _patch_model_manager_dependencies(
        monkeypatch, cuda_available=False, sentence_transformer_factory=factory
    )
    model_manager_module = patched["module"]

    manager = model_manager_module.get_embedding_model()
    assert manager.get_sentence_embedding_dimension() == 3
    assert factory.created_models, "Expected the model to load on dimension request"


def test_idle_timeout_triggers_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = _patch_model_manager_dependencies(monkeypatch, cuda_available=False)
    model_manager_module = patched["module"]

    manager = model_manager_module.get_embedding_model()
    _ = manager.get_model()
    assert (
        manager._model is not None
    )  # internal check OK for behavioral branch coverage

    # Force idle condition by moving last_used far into the past.
    manager._last_used = 0.0
    manager.IDLE_TIMEOUT_SECONDS = 1  # make it small for the check

    # Patch time.time() to exceed idle timeout.
    monkeypatch.setattr(
        model_manager_module.time, "time", lambda: 10_000.0, raising=True
    )

    # Calling get_model should clean up and reload.
    _ = manager.get_model()
    assert manager._model is not None


def test_cleanup_embedding_model_calls_manager_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = _patch_model_manager_dependencies(monkeypatch, cuda_available=False)
    model_manager_module = patched["module"]

    manager = model_manager_module.get_embedding_model()
    _ = manager.get_model()
    assert manager._model is not None

    model_manager_module.cleanup_embedding_model()
    assert manager._model is None


def test_stats_includes_cuda_fields_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = _patch_model_manager_dependencies(monkeypatch, cuda_available=True)
    model_manager_module = patched["module"]

    manager = model_manager_module.get_embedding_model()
    stats = manager.stats()

    assert stats["cuda_available"] is True
    assert "gpu_memory_allocated_gb" in stats
    assert "gpu_memory_cached_gb" in stats


def test_load_model_cpu_fallback_when_cuda_load_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If model loading fails on CUDA, the manager retries on CPU."""
    # Start with CUDA available and plenty of memory so device selection picks cuda.
    factory = _FakeSentenceTransformerFactory()

    patched = _patch_model_manager_dependencies(
        monkeypatch,
        cuda_available=True,
        total_gpu_memory_gb=12.0,
        allocated_gpu_bytes=0,
        sentence_transformer_factory=factory,
    )
    model_manager_module = patched["module"]

    manager = model_manager_module.get_embedding_model()
    assert manager.device == "cuda"

    # Simulate first load failure (e.g., CUDA OOM) then success on retry.
    # NOTE: Do not set `factory.raise_on_init` here because we replace the factory
    # with a wrapper below; leaving it set would conflict with the wrapper intent.
    call_count = {"count": 0}

    def factory_wrapper(
        model_name: str, **kwargs: Any
    ) -> _FakeSentenceTransformerModel:
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise RuntimeError("CUDA load failed")
        return _FakeSentenceTransformerModel(model_name, init_kwargs=kwargs)

    monkeypatch.setattr(
        model_manager_module, "SentenceTransformer", factory_wrapper, raising=True
    )

    model = manager.get_model()
    assert model is not None
    assert manager.device == "cpu"
