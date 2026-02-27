"""Deterministic unit tests for `app.ingestion.pipeline`.

These tests aim to increase coverage of the ingestion pipeline without touching:
- network (gesetze-im-internet.de)
- ChromaDB
- real embedding models

Strategy:
- Patch discovery (`GermanLawDiscovery`) to return small, controlled iterables
- Patch HTML loader (`GermanLawHTMLLoader`) and rate limiting (`time.sleep`)
- Patch embedding store (`GermanLawEmbeddingStore`) to capture calls
- Patch settings (`get_settings`) to avoid reading env/config
- Keep concurrency deterministic by using `max_workers=1` and small batch sizes

We test behavior (inputs/outputs, shaping, control-flow) rather than implementation.

Additional focus:
- Add targeted tests for `ingest_single_law` to cover remaining high-miss regions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from app.ingestion import pipeline as pipeline_module

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


@dataclass(frozen=True)
class _FakeSettings:
    embedding_model: str = "fake-embedding-model"
    chroma_persist_path: str = "/tmp/fake-chroma-persist"


@dataclass(frozen=True)
class _FakeLawInfo:
    abbreviation: str
    title: str
    url: str


@dataclass(frozen=True)
class _FakeNormInfo:
    url: str


@dataclass(frozen=True)
class _FakeDocument:
    """Minimal replacement for LangChain `Document` for our pipeline tests."""

    page_content: str
    metadata: dict[str, Any]


class _FakeDiscovery:
    """Deterministic fake for `GermanLawDiscovery`."""

    def __init__(
        self,
        *,
        laws: list[_FakeLawInfo],
        norms_by_law_abbrev: dict[str, list[_FakeNormInfo]],
        discover_norms_error_by_law_abbrev: dict[str, Exception] | None = None,
    ) -> None:
        self._laws = list(laws)
        self._norms_by_law_abbrev = dict(norms_by_law_abbrev)
        self._discover_norms_error_by_law_abbrev = (
            discover_norms_error_by_law_abbrev or {}
        )

        self.discover_laws_calls: int = 0
        self.discover_norms_calls: list[str] = []

    def discover_laws(self) -> list[_FakeLawInfo]:
        self.discover_laws_calls += 1
        return list(self._laws)

    def discover_norms(self, law: _FakeLawInfo) -> list[_FakeNormInfo]:
        self.discover_norms_calls.append(law.abbreviation)

        if law.abbreviation in self._discover_norms_error_by_law_abbrev:
            raise self._discover_norms_error_by_law_abbrev[law.abbreviation]

        return list(self._norms_by_law_abbrev.get(law.abbreviation, []))


class _FakeEmbeddingStore:
    """Deterministic fake for `GermanLawEmbeddingStore`."""

    init_calls: ClassVar[list[dict[str, Any]]] = []
    add_documents_calls: ClassVar[list[list[Any]]] = []
    search_calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, model_name: str, persist_path: Path) -> None:
        type(self).init_calls.append(
            {"model_name": model_name, "persist_path": persist_path}
        )

    def add_documents(self, documents: list[Any], show_progress: bool = False) -> int:
        # Capture a shallow copy to avoid mutation surprises.
        type(self).add_documents_calls.append(list(documents))
        return len(documents)

    def search(
        self, query: str, n_results: int, where: dict[str, Any] | None
    ) -> list[Any]:
        type(self).search_calls.append(
            {"query": query, "n_results": n_results, "where": where}
        )

        # Return objects shaped like what `pipeline.search_laws` expects.
        @dataclass(frozen=True)
        class _Hit:
            doc_id: str
            content: str
            similarity: float
            metadata: dict[str, Any]

        return [
            _Hit(
                doc_id="doc_1",
                content="Lorem ipsum dolor sit amet",
                similarity=0.12345,
                metadata={"law_abbrev": "BGB", "norm_id": "§ 433", "level": "norm"},
            )
        ]


def _patch_discovery_class(
    monkeypatch: pytest.MonkeyPatch, discovery_instance: _FakeDiscovery
) -> None:
    """Patch `legal_mcp.loaders.discovery.GermanLawDiscovery` to return our fake instance."""

    class _DiscoveryFactory:
        def __call__(self) -> _FakeDiscovery:
            return discovery_instance

    monkeypatch.setattr(
        "legal_mcp.loaders.discovery.GermanLawDiscovery",
        _DiscoveryFactory(),
        raising=True,
    )


def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "get_settings",
        lambda: _FakeSettings(),
        raising=True,
    )


def _patch_embedding_store(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeEmbeddingStore.init_calls = []
    _FakeEmbeddingStore.add_documents_calls = []
    _FakeEmbeddingStore.search_calls = []

    monkeypatch.setattr(
        pipeline_module,
        "GermanLawEmbeddingStore",
        _FakeEmbeddingStore,
        raising=True,
    )


def test_search_laws_builds_where_filter_for_abbrev_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    _patch_embedding_store(monkeypatch)

    _ = pipeline_module.search_laws(
        query="kaufvertrag",
        n_results=3,
        law_abbrev="BGB",
        level=None,
        persist_path="/tmp/ignored",
    )

    assert _FakeEmbeddingStore.search_calls[-1]["where"] == {
        "law_abbrev": {"$eq": "BGB"}
    }


def test_search_laws_builds_where_filter_for_level_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    _patch_embedding_store(monkeypatch)

    _ = pipeline_module.search_laws(
        query="grundrechte",
        n_results=2,
        law_abbrev=None,
        level="norm",
        persist_path="/tmp/ignored",
    )

    assert _FakeEmbeddingStore.search_calls[-1]["where"] == {"level": {"$eq": "norm"}}


def test_search_laws_builds_where_filter_for_abbrev_and_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    _patch_embedding_store(monkeypatch)

    _ = pipeline_module.search_laws(
        query="miete",
        n_results=5,
        law_abbrev="bgb",
        level="paragraph",
        persist_path="/tmp/ignored",
    )

    assert _FakeEmbeddingStore.search_calls[-1]["where"] == {
        "$and": [
            {"law_abbrev": {"$eq": "bgb"}},
            {"level": {"$eq": "paragraph"}},
        ]
    }


def test_search_laws_builds_where_filter_none_when_no_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    _patch_embedding_store(monkeypatch)

    _ = pipeline_module.search_laws(
        query="irgendwas",
        n_results=1,
        law_abbrev=None,
        level=None,
        persist_path="/tmp/ignored",
    )

    assert _FakeEmbeddingStore.search_calls[-1]["where"] is None


def test_load_norm_documents_sleeps_when_delay_positive_and_uses_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(float(seconds))

    monkeypatch.setattr(pipeline_module.time, "sleep", fake_sleep, raising=True)

    # Patch GermanLawHTMLLoader lazy import target.
    class _FakeLoader:
        def __init__(self, url: str, law_abbrev: str) -> None:
            self.url = url
            self.law_abbrev = law_abbrev

        def load(self) -> list[_FakeDocument]:
            return [
                _FakeDocument(
                    page_content="content",
                    metadata={"law_abbrev": self.law_abbrev, "url": self.url},
                )
            ]

    monkeypatch.setattr(
        "legal_mcp.loaders.GermanLawHTMLLoader",
        _FakeLoader,
        raising=True,
    )

    documents = pipeline_module._load_norm_documents(
        law_abbrev="BGB",
        norm_url="https://example.invalid/norm",
        delay=0.25,
    )

    assert sleep_calls == [0.25]
    assert len(documents) == 1
    assert documents[0].metadata["law_abbrev"] == "BGB"


def test_ingest_german_laws_happy_path_batches_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    _patch_embedding_store(monkeypatch)

    laws = [
        _FakeLawInfo(abbreviation="BGB", title="BGB", url="https://example.invalid/bgb")
    ]
    norms_by_law = {
        "BGB": [
            _FakeNormInfo(url="https://example.invalid/bgb/1"),
            _FakeNormInfo(url="https://example.invalid/bgb/2"),
        ]
    }
    discovery = _FakeDiscovery(laws=laws, norms_by_law_abbrev=norms_by_law)
    _patch_discovery_class(monkeypatch, discovery)

    # Patch norm loader to return one fake document per norm.
    def fake_load_norm_documents(
        law_abbrev: str, norm_url: str, delay: float = 0.0
    ) -> list[_FakeDocument]:
        return [
            _FakeDocument(
                page_content=f"doc for {law_abbrev} {norm_url}",
                metadata={"law_abbrev": law_abbrev, "norm_url": norm_url},
            )
        ]

    monkeypatch.setattr(
        pipeline_module,
        "_load_norm_documents",
        fake_load_norm_documents,
        raising=True,
    )

    progress_updates: list[dict[str, Any]] = []

    def progress_callback(progress: Any) -> None:
        # Store only safe, small progress dict info.
        progress_updates.append(progress.to_dict())

    result = pipeline_module.ingest_german_laws(
        max_laws=1,
        max_norms_per_law=None,
        batch_size=1,  # insert each doc immediately
        persist_path="/tmp/ignored",
        progress_callback=progress_callback,
        max_workers=1,  # deterministic
    )

    assert result.documents_added == 2
    assert result.laws_processed == 1
    assert result.norms_processed == 2
    assert result.errors == []

    # Ensure store add_documents was invoked on each batch (batch_size=1).
    assert len(_FakeEmbeddingStore.add_documents_calls) == 2
    assert all(len(batch) == 1 for batch in _FakeEmbeddingStore.add_documents_calls)

    # Ensure progress callback was called at least once.
    assert len(progress_updates) >= 1
    assert progress_updates[-1]["total_laws"] == 1


def test_ingest_german_laws_records_discovery_errors_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    _patch_embedding_store(monkeypatch)

    laws = [
        _FakeLawInfo(abbreviation="OK", title="OK", url="https://example.invalid/ok"),
        _FakeLawInfo(
            abbreviation="BAD", title="BAD", url="https://example.invalid/bad"
        ),
    ]
    norms_by_law = {"OK": [_FakeNormInfo(url="https://example.invalid/ok/1")]}
    discovery = _FakeDiscovery(
        laws=laws,
        norms_by_law_abbrev=norms_by_law,
        discover_norms_error_by_law_abbrev={"BAD": RuntimeError("boom")},
    )
    _patch_discovery_class(monkeypatch, discovery)

    monkeypatch.setattr(
        pipeline_module,
        "_load_norm_documents",
        lambda law_abbrev, norm_url, delay=0.0: [
            _FakeDocument(page_content="x", metadata={"law_abbrev": law_abbrev})
        ],
        raising=True,
    )

    result = pipeline_module.ingest_german_laws(
        max_laws=None,
        max_norms_per_law=None,
        batch_size=10,  # all docs inserted at end
        persist_path="/tmp/ignored",
        progress_callback=None,
        max_workers=1,
    )

    # One norm from OK law -> 1 doc added
    assert result.documents_added == 1
    assert result.laws_processed == 1  # only "OK" processed via norms
    assert result.norms_processed == 1
    # `IngestionResult` does not expose `error_count`; verify via `errors` length instead.
    assert len(result.errors) == 1
    assert any("Error discovering norms for BAD" in err for err in result.errors)


def test_ingest_single_law_happy_path_batches_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    _patch_embedding_store(monkeypatch)

    # Patch discovery/LawInfo used inside ingest_single_law
    from legal_mcp.loaders import discovery as discovery_module

    @dataclass(frozen=True)
    class _FakeLawInfoForSingle:
        abbreviation: str
        title: str
        url: str

    class _FakeDiscoveryForSingle:
        def __init__(self) -> None:
            self.discover_norms_calls: list[str] = []

        def discover_norms(self, law: Any) -> list[Any]:
            self.discover_norms_calls.append(law.abbreviation)
            return [
                _FakeNormInfo(url="https://example.invalid/single/1"),
                _FakeNormInfo(url="https://example.invalid/single/2"),
            ]

    fake_discovery = _FakeDiscoveryForSingle()

    monkeypatch.setattr(
        discovery_module,
        "GermanLawDiscovery",
        lambda: fake_discovery,
        raising=True,
    )
    monkeypatch.setattr(
        discovery_module,
        "LawInfo",
        _FakeLawInfoForSingle,
        raising=True,
    )

    # Patch norm loader to return one fake document per norm.
    def fake_load_norm_documents(
        law_abbrev: str, norm_url: str, delay: float = 0.0
    ) -> list[_FakeDocument]:
        return [
            _FakeDocument(
                page_content=f"single {law_abbrev} {norm_url}",
                metadata={"law_abbrev": law_abbrev, "norm_url": norm_url},
            )
        ]

    monkeypatch.setattr(
        pipeline_module,
        "_load_norm_documents",
        fake_load_norm_documents,
        raising=True,
    )

    result = pipeline_module.ingest_single_law(
        law_abbrev="BGB",
        persist_path="/tmp/ignored",
        max_workers=1,
        batch_size=1,  # force immediate insertion each time
    )

    assert result.documents_added == 2
    assert result.laws_processed == 1
    assert result.norms_processed == 2
    assert result.errors == []

    # Ensure discovery was invoked with the requested abbreviation.
    assert fake_discovery.discover_norms_calls == ["BGB"]

    # Ensure store add_documents was invoked per-batch (batch_size=1).
    assert len(_FakeEmbeddingStore.add_documents_calls) == 2
    assert all(len(batch) == 1 for batch in _FakeEmbeddingStore.add_documents_calls)


def test_ingest_single_law_records_loader_errors_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    _patch_embedding_store(monkeypatch)

    from legal_mcp.loaders import discovery as discovery_module

    @dataclass(frozen=True)
    class _FakeLawInfoForSingle:
        abbreviation: str
        title: str
        url: str

    class _FakeDiscoveryForSingle:
        def discover_norms(self, law: Any) -> list[Any]:
            return [
                _FakeNormInfo(url="https://example.invalid/single/ok"),
                _FakeNormInfo(url="https://example.invalid/single/bad"),
            ]

    monkeypatch.setattr(
        discovery_module,
        "GermanLawDiscovery",
        lambda: _FakeDiscoveryForSingle(),
        raising=True,
    )
    monkeypatch.setattr(
        discovery_module,
        "LawInfo",
        _FakeLawInfoForSingle,
        raising=True,
    )

    def fake_load_norm_documents(
        law_abbrev: str, norm_url: str, delay: float = 0.0
    ) -> list[_FakeDocument]:
        if norm_url.endswith("/bad"):
            raise RuntimeError("load failed")
        return [
            _FakeDocument(
                page_content=f"single {law_abbrev} {norm_url}",
                metadata={"law_abbrev": law_abbrev, "norm_url": norm_url},
            )
        ]

    monkeypatch.setattr(
        pipeline_module,
        "_load_norm_documents",
        fake_load_norm_documents,
        raising=True,
    )

    result = pipeline_module.ingest_single_law(
        law_abbrev="BGB",
        persist_path="/tmp/ignored",
        max_workers=1,
        batch_size=10,  # only flush at end
    )

    # One norm succeeds -> one doc added; one norm fails -> error recorded.
    assert result.documents_added == 1
    assert result.laws_processed == 1
    assert result.norms_processed == 1
    assert len(result.errors) == 1
    assert "Error loading https://example.invalid/single/bad" in result.errors[0]


def test_ingest_single_law_top_level_exception_is_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    _patch_embedding_store(monkeypatch)

    from legal_mcp.loaders import discovery as discovery_module

    @dataclass(frozen=True)
    class _FakeLawInfoForSingle:
        abbreviation: str
        title: str
        url: str

    class _FakeDiscoveryForSingle:
        def discover_norms(self, law: Any) -> list[Any]:
            raise RuntimeError("discover_norms exploded")
            return []

    monkeypatch.setattr(
        discovery_module,
        "GermanLawDiscovery",
        lambda: _FakeDiscoveryForSingle(),
        raising=True,
    )
    monkeypatch.setattr(
        discovery_module,
        "LawInfo",
        _FakeLawInfoForSingle,
        raising=True,
    )

    # Should not raise; it should capture the exception into errors and return a result.
    result = pipeline_module.ingest_single_law(
        law_abbrev="BGB",
        persist_path="/tmp/ignored",
        max_workers=1,
        batch_size=1,
    )

    assert result.documents_added == 0
    assert result.laws_processed == 0
    assert result.norms_processed == 0
    assert len(result.errors) == 1
    assert "Error processing BGB" in result.errors[0]
    assert "discover_norms exploded" in result.errors[0]
