"""Unit tests for the TEI (Text Embeddings Inference) HTTP client.

These tests cover the behavior of `app.ingestion.tei_client.TEIEmbeddingClient`
without making any network calls. We fake `httpx.Client` and patch `time.sleep`
to validate retry/backoff behavior deterministically.

Focus areas:
- Round-robin endpoint selection
- Health checks and model info caching
- Embedding requests, including retry logic on overload/connection failures
- `encode()` input normalization and output shape
- Singleton client getter/reset behavior
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
import pytest

from app.ingestion import tei_client as tei_client_module


@dataclass(frozen=True)
class _FakeResponse:
    status_code: int
    json_data: Any

    def json(self) -> Any:
        return self.json_data

    def raise_for_status(self) -> None:
        if 200 <= self.status_code < 300:
            return
        request = httpx.Request("GET", "http://example.invalid")
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError("error", request=request, response=response)


class _FakeHttpxClient:
    """A minimal fake for httpx.Client used by TEIEmbeddingClient.

    Behavior is controlled by per-instance maps and counters:
    - get_routes: map path -> list of status codes (cycled by calls)
    - post_routes: map path -> list of outcomes where outcome is:
      - ("ok", json_payload)
      - ("status", status_code)
      - ("connect_error", message)
      - ("timeout", message)
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        limits: httpx.Limits,
        get_routes: dict[str, list[int]] | None = None,
        post_routes: dict[str, list[tuple[str, Any]]] | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.limits = limits
        self._get_routes = get_routes or {}
        self._post_routes = post_routes or {}
        self._get_counts: dict[str, int] = {}
        self._post_counts: dict[str, int] = {}
        self.closed = False

    def get(self, path: str) -> _FakeResponse:
        count = self._get_counts.get(path, 0)
        self._get_counts[path] = count + 1
        status_codes = self._get_routes.get(path, [404])
        status_code = status_codes[min(count, len(status_codes) - 1)]
        if path == "/info" and status_code == 200:
            return _FakeResponse(
                200, {"model_id": "fake-model", "max_input_length": 512}
            )
        return _FakeResponse(status_code, {"status": "ok"})

    def post(self, path: str, json: dict[str, Any]) -> _FakeResponse:
        count = self._post_counts.get(path, 0)
        self._post_counts[path] = count + 1
        outcomes = self._post_routes.get(path, [("status", 404)])
        outcome_kind, outcome_value = outcomes[min(count, len(outcomes) - 1)]

        if outcome_kind == "ok":
            return _FakeResponse(200, outcome_value)

        if outcome_kind == "status":
            return _FakeResponse(int(outcome_value), {"error": "status"})

        if outcome_kind == "connect_error":
            raise httpx.ConnectError(
                str(outcome_value),
                request=httpx.Request("POST", "http://example.invalid"),
            )

        if outcome_kind == "timeout":
            raise httpx.TimeoutException(
                str(outcome_value),
                request=httpx.Request("POST", "http://example.invalid"),
            )

        raise RuntimeError(f"Unknown fake outcome kind: {outcome_kind}")

    def close(self) -> None:
        self.closed = True


def _install_fake_httpx_clients(
    monkeypatch: pytest.MonkeyPatch,
    *,
    routes_by_base_url: dict[str, dict[str, Any]],
) -> list[_FakeHttpxClient]:
    """Patch `httpx.Client` to return `_FakeHttpxClient` instances."""
    created_clients: list[_FakeHttpxClient] = []

    def fake_httpx_client_factory(
        *,
        base_url: str,
        timeout: float,
        limits: httpx.Limits,
    ) -> _FakeHttpxClient:
        routes = routes_by_base_url.get(base_url, {})
        client = _FakeHttpxClient(
            base_url=base_url,
            timeout=timeout,
            limits=limits,
            get_routes=routes.get("get_routes"),
            post_routes=routes.get("post_routes"),
        )
        created_clients.append(client)
        return client

    monkeypatch.setattr(
        tei_client_module.httpx, "Client", fake_httpx_client_factory, raising=True
    )
    return created_clients


def _install_sleep_spy(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(float(seconds))

    monkeypatch.setattr(tei_client_module.time, "sleep", fake_sleep, raising=True)
    return sleep_calls


def test_get_tei_client_is_singleton_and_reset_recreates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes_by_base_url = {"http://tei-1": {"get_routes": {"/health": [200]}}}
    created_clients = _install_fake_httpx_clients(
        monkeypatch, routes_by_base_url=routes_by_base_url
    )

    tei_client_module.reset_tei_client()
    client_a = tei_client_module.get_tei_client(base_urls=["http://tei-1"])
    client_b = tei_client_module.get_tei_client(base_urls=["http://tei-1"])

    assert client_a is client_b
    assert len(created_clients) == 1  # created once per base_url in __post_init__

    tei_client_module.reset_tei_client()
    client_c = tei_client_module.get_tei_client(base_urls=["http://tei-1"])
    assert client_c is not client_a


def test_round_robin_get_next_url_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    routes_by_base_url = {
        "http://tei-1": {
            "post_routes": {
                "/embed": [("status", 503), ("status", 503), ("ok", [[0.0, 1.0]])]
            }
        },
        "http://tei-2": {"post_routes": {"/embed": [("ok", [[2.0, 3.0]])]}},
    }
    _install_fake_httpx_clients(monkeypatch, routes_by_base_url=routes_by_base_url)
    _install_sleep_spy(monkeypatch)

    client = tei_client_module.TEIEmbeddingClient(
        base_urls=["http://tei-1", "http://tei-2"], max_retries=1
    )

    # First attempt picks tei-1 (503), second picks tei-2 (ok)
    embeddings = client._embed_batch(["hello"])
    assert embeddings == [[2.0, 3.0]]


def test_health_check_true_if_any_endpoint_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes_by_base_url = {
        "http://tei-1": {"get_routes": {"/health": [500]}},
        "http://tei-2": {"get_routes": {"/health": [200]}},
    }
    _install_fake_httpx_clients(monkeypatch, routes_by_base_url=routes_by_base_url)

    client = tei_client_module.TEIEmbeddingClient(
        base_urls=["http://tei-1", "http://tei-2"]
    )
    assert client.health_check() is True


def test_health_check_false_if_all_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    routes_by_base_url = {
        "http://tei-1": {"get_routes": {"/health": [500]}},
        "http://tei-2": {"get_routes": {"/health": [404]}},
    }
    _install_fake_httpx_clients(monkeypatch, routes_by_base_url=routes_by_base_url)

    client = tei_client_module.TEIEmbeddingClient(
        base_urls=["http://tei-1", "http://tei-2"]
    )
    assert client.health_check() is False


def test_get_model_info_caches_after_first_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes_by_base_url = {
        "http://tei-1": {"get_routes": {"/info": [200]}},
    }
    created_clients = _install_fake_httpx_clients(
        monkeypatch, routes_by_base_url=routes_by_base_url
    )

    client = tei_client_module.TEIEmbeddingClient(base_urls=["http://tei-1"])
    info_first = client.get_model_info()
    info_second = client.get_model_info()

    assert info_first == {"model_id": "fake-model", "max_input_length": 512}
    assert info_second == info_first
    # The underlying fake client would only be created once; caching happens in client._model_info
    assert len(created_clients) == 1


def test_encode_normalizes_string_and_returns_numpy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes_by_base_url = {
        "http://tei-1": {"post_routes": {"/embed": [("ok", [[1.0, 2.0, 3.0]])]}},
    }
    _install_fake_httpx_clients(monkeypatch, routes_by_base_url=routes_by_base_url)

    client = tei_client_module.TEIEmbeddingClient(base_urls=["http://tei-1"])
    result = client.encode("hello", batch_size=64)

    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32
    assert result.shape == (1, 3)
    assert np.allclose(result[0], np.array([1.0, 2.0, 3.0], dtype=np.float32))


def test_encode_empty_list_returns_empty_array(monkeypatch: pytest.MonkeyPatch) -> None:
    routes_by_base_url: dict[str, dict[str, Any]] = {"http://tei-1": {}}
    _install_fake_httpx_clients(monkeypatch, routes_by_base_url=routes_by_base_url)

    client = tei_client_module.TEIEmbeddingClient(base_urls=["http://tei-1"])
    result = client.encode([])
    assert isinstance(result, np.ndarray)
    assert result.size == 0


def test_embed_batch_retries_on_503_with_backoff_when_all_tried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes_by_base_url = {
        "http://tei-1": {
            "post_routes": {
                # tei-1 overloaded twice, then recovers
                "/embed": [("status", 503), ("status", 503), ("ok", [[0.0, 0.0]])]
            }
        },
        "http://tei-2": {
            "post_routes": {
                # tei-2 overloaded twice, then recovers
                "/embed": [("status", 503), ("status", 503), ("ok", [[1.0, 1.0]])]
            }
        },
    }
    _install_fake_httpx_clients(monkeypatch, routes_by_base_url=routes_by_base_url)
    sleep_calls = _install_sleep_spy(monkeypatch)

    # With 2 endpoints, attempts = max_retries * len(base_urls).
    # We need enough attempts to reach the 3rd call for a given endpoint (the first "ok").
    client = tei_client_module.TEIEmbeddingClient(
        base_urls=["http://tei-1", "http://tei-2"], max_retries=3
    )

    embeddings = client._embed_batch(["hello"])
    assert embeddings in ([[0.0, 0.0]], [[1.0, 1.0]])

    # We expect at least one backoff sleep when both endpoints have been tried and still overloaded.
    assert any(seconds >= 1.0 for seconds in sleep_calls)


def test_embed_batch_retries_on_connect_and_timeout_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes_by_base_url = {
        "http://tei-1": {
            "post_routes": {
                # first call fails, second call succeeds when we come back around
                "/embed": [("connect_error", "no route"), ("ok", [[3.0, 3.0]])]
            }
        },
        "http://tei-2": {
            "post_routes": {
                # first call fails, second call succeeds when we come back around
                "/embed": [("timeout", "slow"), ("ok", [[4.0, 4.0]])]
            }
        },
    }
    _install_fake_httpx_clients(monkeypatch, routes_by_base_url=routes_by_base_url)
    _install_sleep_spy(monkeypatch)

    # Need enough attempts to revisit an endpoint after its first failure.
    client = tei_client_module.TEIEmbeddingClient(
        base_urls=["http://tei-1", "http://tei-2"], max_retries=2
    )

    embeddings = client._embed_batch(["hello"])
    assert embeddings in ([[3.0, 3.0]], [[4.0, 4.0]])


def test_embed_batch_raises_after_exhausting_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes_by_base_url = {
        "http://tei-1": {"post_routes": {"/embed": [("connect_error", "down")]}},
        "http://tei-2": {"post_routes": {"/embed": [("timeout", "down")]}},
    }
    _install_fake_httpx_clients(monkeypatch, routes_by_base_url=routes_by_base_url)
    _install_sleep_spy(monkeypatch)

    client = tei_client_module.TEIEmbeddingClient(
        base_urls=["http://tei-1", "http://tei-2"], max_retries=1
    )

    with pytest.raises(RuntimeError) as exc_info:
        client._embed_batch(["hello"])

    assert "Failed to embed" in str(exc_info.value)


def test_get_sentence_embedding_dimension_uses_dim_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes_by_base_url = {"http://tei-1": {"get_routes": {"/info": [200]}}}
    _install_fake_httpx_clients(monkeypatch, routes_by_base_url=routes_by_base_url)

    client = tei_client_module.TEIEmbeddingClient(base_urls=["http://tei-1"])
    client._model_info = {"dim": 768}
    assert client.get_sentence_embedding_dimension() == 768


def test_get_sentence_embedding_dimension_falls_back_to_encode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes_by_base_url = {
        "http://tei-1": {
            "get_routes": {"/info": [200]},
            "post_routes": {"/embed": [("ok", [[1.0, 2.0, 3.0, 4.0]])]},
        }
    }
    _install_fake_httpx_clients(monkeypatch, routes_by_base_url=routes_by_base_url)

    client = tei_client_module.TEIEmbeddingClient(base_urls=["http://tei-1"])
    client._model_info = {}  # no dim
    assert client.get_sentence_embedding_dimension() == 4


def test_cleanup_closes_all_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    routes_by_base_url = {
        "http://tei-1": {"get_routes": {"/health": [200]}},
        "http://tei-2": {"get_routes": {"/health": [200]}},
    }
    created_clients = _install_fake_httpx_clients(
        monkeypatch, routes_by_base_url=routes_by_base_url
    )

    client = tei_client_module.TEIEmbeddingClient(
        base_urls=["http://tei-1", "http://tei-2"]
    )
    assert len(created_clients) == 2
    client.cleanup()

    assert all(fake_client.closed for fake_client in created_clients)
    assert client._clients == {}
