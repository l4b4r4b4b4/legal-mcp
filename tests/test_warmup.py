"""Tests for app.warmup — corpus warm-up state tracking.

Covers the unit-testable parts of warmup.py:
- WarmupState enum
- _WarmupStatus thread-safe status container
- Public API: get_warmup_status, is_warmup_running, is_corpus_ready

The background worker (_warmup_worker) and startup retry helpers
(_wait_for_chromadb, _wait_for_tei) require running external services
and are NOT tested here — they are covered by local E2E tests.
"""

from __future__ import annotations

import threading
import time

from app.warmup import (
    WarmupState,
    _WarmupStatus,
    get_warmup_status,
    is_corpus_ready,
    is_warmup_running,
)

# ===========================================================================
# WarmupState enum
# ===========================================================================


class TestWarmupState:
    """Tests for WarmupState enum values."""

    def test_idle_value(self):
        assert WarmupState.IDLE == "idle"
        assert WarmupState.IDLE.value == "idle"

    def test_checking_value(self):
        assert WarmupState.CHECKING == "checking"

    def test_skipped_value(self):
        assert WarmupState.SKIPPED == "skipped"

    def test_running_value(self):
        assert WarmupState.RUNNING == "running"

    def test_completed_value(self):
        assert WarmupState.COMPLETED == "completed"

    def test_failed_value(self):
        assert WarmupState.FAILED == "failed"

    def test_is_string_subclass(self):
        """WarmupState inherits from str for JSON serialization."""
        assert isinstance(WarmupState.IDLE, str)


# ===========================================================================
# _WarmupStatus
# ===========================================================================


class TestWarmupStatus:
    """Tests for _WarmupStatus thread-safe container."""

    def test_initial_state_is_idle(self):
        status = _WarmupStatus()
        assert status.state == WarmupState.IDLE
        assert status.started_at is None
        assert status.completed_at is None
        assert status.documents_added == 0
        assert status.laws_processed == 0
        assert status.total_laws == 0
        assert status.errors == []
        assert status.error_message is None
        assert status.skip_reason is None

    def test_set_checking(self):
        """set_checking transitions to CHECKING and records start time."""
        status = _WarmupStatus()
        before = time.time()

        status.set_checking()

        assert status.state == WarmupState.CHECKING
        assert status.started_at is not None
        assert status.started_at >= before

    def test_set_skipped(self):
        """set_skipped transitions to SKIPPED with reason."""
        status = _WarmupStatus()
        status.set_checking()

        status.set_skipped("Corpus already populated")

        assert status.state == WarmupState.SKIPPED
        assert status.skip_reason == "Corpus already populated"
        assert status.completed_at is not None

    def test_set_running(self):
        """set_running transitions to RUNNING with total_laws count."""
        status = _WarmupStatus()

        status.set_running(total_laws=100)

        assert status.state == WarmupState.RUNNING
        assert status.total_laws == 100

    def test_update_progress(self):
        """update_progress updates law and document counters."""
        status = _WarmupStatus()
        status.set_running(total_laws=50)

        status.update_progress(laws_processed=10, documents_added=500)

        assert status.laws_processed == 10
        assert status.documents_added == 500

    def test_set_completed(self):
        """set_completed transitions to COMPLETED with final counts."""
        status = _WarmupStatus()
        status.set_checking()
        status.set_running(total_laws=50)

        status.set_completed(
            documents_added=1000,
            laws_processed=50,
            errors=["minor issue 1", "minor issue 2"],
        )

        assert status.state == WarmupState.COMPLETED
        assert status.documents_added == 1000
        assert status.laws_processed == 50
        assert len(status.errors) == 2
        assert status.completed_at is not None

    def test_set_completed_caps_errors_at_50(self):
        """set_completed caps stored errors at 50."""
        status = _WarmupStatus()
        many_errors = [f"error_{i}" for i in range(100)]

        status.set_completed(
            documents_added=0,
            laws_processed=0,
            errors=many_errors,
        )

        assert len(status.errors) == 50

    def test_set_failed(self):
        """set_failed transitions to FAILED with error message."""
        status = _WarmupStatus()
        status.set_checking()

        status.set_failed("ChromaDB connection refused")

        assert status.state == WarmupState.FAILED
        assert status.error_message == "ChromaDB connection refused"
        assert status.completed_at is not None

    def test_to_dict_idle(self):
        """to_dict returns correct structure in IDLE state."""
        status = _WarmupStatus()

        result = status.to_dict()

        assert result["state"] == "idle"
        assert result["started_at"] is None
        assert result["completed_at"] is None
        assert result["elapsed_seconds"] is None
        assert result["documents_added"] == 0
        assert result["laws_processed"] == 0
        assert result["total_laws"] == 0
        assert result["error_count"] == 0
        assert result["error_message"] is None
        assert result["skip_reason"] is None

    def test_to_dict_running_has_elapsed_seconds(self):
        """to_dict computes elapsed_seconds while running."""
        status = _WarmupStatus()
        status.set_checking()
        # Simulate a tiny time passage
        time.sleep(0.01)

        result = status.to_dict()

        assert result["state"] == "checking"
        assert result["elapsed_seconds"] is not None
        assert result["elapsed_seconds"] >= 0.0

    def test_to_dict_completed_has_final_elapsed(self):
        """to_dict uses completed_at for elapsed when done."""
        status = _WarmupStatus()
        status.set_checking()
        time.sleep(0.01)
        status.set_completed(documents_added=100, laws_processed=5, errors=[])

        result = status.to_dict()

        assert result["state"] == "completed"
        assert result["elapsed_seconds"] is not None
        assert result["documents_added"] == 100

    def test_to_dict_failed_has_error(self):
        """to_dict includes error details when failed."""
        status = _WarmupStatus()
        status.set_checking()
        status.set_failed("Something broke")

        result = status.to_dict()

        assert result["state"] == "failed"
        assert result["error_message"] == "Something broke"

    def test_to_dict_skipped_has_reason(self):
        """to_dict includes skip reason."""
        status = _WarmupStatus()
        status.set_skipped("Already done")

        result = status.to_dict()

        assert result["state"] == "skipped"
        assert result["skip_reason"] == "Already done"


# ===========================================================================
# Public API (module-level functions)
# ===========================================================================


class TestPublicAPI:
    """Tests for module-level public functions."""

    def test_get_warmup_status_returns_dict(self):
        """get_warmup_status returns a dictionary."""
        result = get_warmup_status()

        assert isinstance(result, dict)
        assert "state" in result
        assert "documents_added" in result

    def test_is_warmup_running_false_when_no_thread(self):
        """is_warmup_running returns False when no thread is active."""
        assert is_warmup_running() is False

    def test_is_corpus_ready_depends_on_state(self):
        """is_corpus_ready returns True only for COMPLETED or SKIPPED."""
        # The module-level singleton state depends on prior runs,
        # but we can at least verify the function returns a bool.
        result = is_corpus_ready()
        assert isinstance(result, bool)


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    """Verify _WarmupStatus is safe under concurrent access."""

    def test_concurrent_updates(self):
        """Multiple threads updating status concurrently don't crash."""
        status = _WarmupStatus()
        status.set_running(total_laws=100)
        error_occurred = threading.Event()

        def updater(thread_index):
            try:
                for iteration in range(50):
                    status.update_progress(
                        laws_processed=thread_index * 50 + iteration,
                        documents_added=thread_index * 1000 + iteration * 10,
                    )
                    status.to_dict()  # concurrent reads
            except Exception:
                error_occurred.set()

        threads = [threading.Thread(target=updater, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert not error_occurred.is_set(), "Thread safety violation detected"
        # Final state should be consistent
        result = status.to_dict()
        assert result["state"] == "running"
        assert result["total_laws"] == 100


# ===========================================================================
# start_background_warmup / run_warmup_sync (mocked worker)
# ===========================================================================


class TestBackgroundWarmup:
    """Tests for start_background_warmup with mocked _warmup_worker."""

    def test_start_background_warmup_returns_true(self):
        """Starting warmup when idle returns True."""
        import app.warmup as warmup_module

        original_worker = warmup_module._warmup_worker

        def noop_worker(*args, **kwargs):
            pass

        warmup_module._warmup_worker = noop_worker
        warmup_module._warmup_thread = None

        try:
            result = warmup_module.start_background_warmup(
                html_root="/tmp/fake",
                max_laws=1,
            )
            assert result is True
            # Wait for thread to finish
            if warmup_module._warmup_thread is not None:
                warmup_module._warmup_thread.join(timeout=2)
        finally:
            warmup_module._warmup_worker = original_worker
            warmup_module._warmup_thread = None

    def test_start_background_warmup_already_running_returns_false(self):
        """Starting warmup when already running returns False."""
        import app.warmup as warmup_module

        stop_event = __import__("threading").Event()

        def blocking_worker(*args, **kwargs):
            stop_event.wait(timeout=5)

        original_worker = warmup_module._warmup_worker
        warmup_module._warmup_worker = blocking_worker
        warmup_module._warmup_thread = None

        try:
            # Start first
            warmup_module.start_background_warmup(html_root="/tmp/fake")
            # Try to start again while running
            result = warmup_module.start_background_warmup(html_root="/tmp/fake")
            assert result is False
        finally:
            stop_event.set()
            if warmup_module._warmup_thread is not None:
                warmup_module._warmup_thread.join(timeout=2)
            warmup_module._warmup_worker = original_worker
            warmup_module._warmup_thread = None

    def test_run_warmup_sync_returns_status_dict(self):
        """run_warmup_sync calls worker and returns status dict."""
        import app.warmup as warmup_module

        original_worker = warmup_module._warmup_worker

        def noop_worker(*args, **kwargs):
            pass

        warmup_module._warmup_worker = noop_worker

        try:
            result = warmup_module.run_warmup_sync(
                html_root="/tmp/fake",
                max_laws=1,
            )
            assert isinstance(result, dict)
            assert "state" in result
        finally:
            warmup_module._warmup_worker = original_worker


# ===========================================================================
# _resolve_html_root
# ===========================================================================


class TestResolveHtmlRoot:
    """Tests for _resolve_html_root path resolution."""

    def test_explicit_path_string(self):
        """Explicit string path is returned as Path."""
        from pathlib import Path

        from app.warmup import _resolve_html_root

        result = _resolve_html_root("/tmp/my/html")
        assert result == Path("/tmp/my/html")

    def test_explicit_path_object(self):
        """Explicit Path object is returned as-is."""
        from pathlib import Path

        from app.warmup import _resolve_html_root

        path = Path("/tmp/test")
        result = _resolve_html_root(path)
        assert result == path

    def test_none_uses_config_default(self):
        """None falls back to config warmup_html_root."""
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from app.warmup import _resolve_html_root

        mock_settings = MagicMock()
        mock_settings.warmup_html_root = "/config/default/html"

        with patch("app.config.get_settings", return_value=mock_settings):
            result = _resolve_html_root(None)

        assert result == Path("/config/default/html")
