"""Tests for RAG pipeline components.

Tests cover:
- Prompt formatting
- Context building
- LLM client (mocked)
- RAG pipeline integration (mocked)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rag.context import (
    RAGContext,
    SourceContext,
    build_context_from_results,
)
from app.rag.llm_client import LLMClient, LLMResponse
from app.rag.pipeline import RAGPipeline, RAGResult
from app.rag.prompts import (
    SYSTEM_PROMPT,
    format_source,
    format_sources,
    format_user_prompt,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_source() -> dict[str, str]:
    """Sample source dictionary for testing."""
    return {
        "law_abbrev": "BGB",
        "norm_id": "§ 433",
        "title": "Vertragstypische Pflichten beim Kaufvertrag",
        "content": "Durch den Kaufvertrag wird der Verkäufer einer Sache verpflichtet, dem Käufer die Sache zu übergeben und das Eigentum an der Sache zu verschaffen.",
    }


@pytest.fixture
def sample_sources() -> list[dict[str, str]]:
    """Multiple sample sources for testing."""
    return [
        {
            "law_abbrev": "BGB",
            "norm_id": "§ 433",
            "title": "Vertragstypische Pflichten beim Kaufvertrag",
            "content": "Durch den Kaufvertrag wird der Verkäufer verpflichtet...",
        },
        {
            "law_abbrev": "BGB",
            "norm_id": "§ 434",
            "title": "Sachmangel",
            "content": "Die Sache ist frei von Sachmängeln, wenn sie...",
        },
        {
            "law_abbrev": "BGB",
            "norm_id": "§ 437",
            "title": "Rechte des Käufers bei Mängeln",
            "content": "Ist die Sache mangelhaft, kann der Käufer...",
        },
    ]


@pytest.fixture
def mock_search_result() -> MagicMock:
    """Mock SearchResult from embedding store."""
    result = MagicMock()
    result.doc_id = "BGB_433_norm"
    result.content = "Durch den Kaufvertrag wird der Verkäufer verpflichtet..."
    result.metadata = {
        "law_abbrev": "BGB",
        "norm_id": "§ 433",
        "title": "Vertragstypische Pflichten beim Kaufvertrag",
        "level": "norm",
    }
    result.distance = 0.15
    result.similarity = 0.85
    return result


@pytest.fixture
def mock_search_results(mock_search_result: MagicMock) -> list[MagicMock]:
    """List of mock search results."""
    result2 = MagicMock()
    result2.doc_id = "BGB_434_norm"
    result2.content = "Die Sache ist frei von Sachmängeln..."
    result2.metadata = {
        "law_abbrev": "BGB",
        "norm_id": "§ 434",
        "title": "Sachmangel",
        "level": "norm",
    }
    result2.distance = 0.2
    result2.similarity = 0.8

    return [mock_search_result, result2]


# =============================================================================
# Prompt Tests
# =============================================================================


class TestPrompts:
    """Tests for prompt formatting."""

    def test_system_prompt_exists(self) -> None:
        """System prompt should be defined and non-empty."""
        assert SYSTEM_PROMPT
        assert len(SYSTEM_PROMPT) > 100
        assert "Rechtsassistent" in SYSTEM_PROMPT

    def test_system_prompt_has_citation_instructions(self) -> None:
        """System prompt should instruct to use citations."""
        assert "[1]" in SYSTEM_PROMPT or "Zitiere" in SYSTEM_PROMPT

    def test_format_source_basic(self, sample_source: dict[str, str]) -> None:
        """Format a single source with all fields."""
        result = format_source(
            index=1,
            law_abbrev=sample_source["law_abbrev"],
            norm_id=sample_source["norm_id"],
            title=sample_source["title"],
            content=sample_source["content"],
        )

        assert "[1]" in result
        assert "BGB" in result
        assert "§ 433" in result
        assert "Kaufvertrag" in result

    def test_format_source_truncates_long_content(self) -> None:
        """Long content should be truncated."""
        long_content = "A" * 3000
        result = format_source(
            index=1,
            law_abbrev="BGB",
            norm_id="§ 1",
            title="Test",
            content=long_content,
            max_content_length=100,
        )

        assert len(result) < 3000
        assert "..." in result

    def test_format_sources_multiple(
        self, sample_sources: list[dict[str, str]]
    ) -> None:
        """Format multiple sources with sequential indices."""
        result = format_sources(sample_sources, max_sources=3)

        assert "[1]" in result
        assert "[2]" in result
        assert "[3]" in result
        assert "§ 433" in result
        assert "§ 434" in result
        assert "§ 437" in result

    def test_format_sources_respects_max_sources(
        self, sample_sources: list[dict[str, str]]
    ) -> None:
        """Should limit number of sources."""
        result = format_sources(sample_sources, max_sources=2)

        assert "[1]" in result
        assert "[2]" in result
        assert "[3]" not in result

    def test_format_sources_empty_list(self) -> None:
        """Empty sources should return empty string."""
        result = format_sources([])
        assert result == ""

    def test_format_user_prompt_with_sources(
        self, sample_sources: list[dict[str, str]]
    ) -> None:
        """User prompt should include sources and question."""
        question = "Was ist ein Kaufvertrag?"
        result = format_user_prompt(question, sample_sources)

        assert "Relevante Gesetzestexte" in result
        assert question in result
        assert "[1]" in result

    def test_format_user_prompt_no_sources(self) -> None:
        """User prompt without sources should indicate no sources found."""
        question = "Was ist ein Kaufvertrag?"
        result = format_user_prompt(question, [])

        assert "Keine relevanten Gesetzestexte" in result
        assert question in result


# =============================================================================
# Context Tests
# =============================================================================


class TestContext:
    """Tests for context building."""

    def test_source_context_to_dict(self) -> None:
        """SourceContext should serialize correctly."""
        source = SourceContext(
            index=1,
            law_abbrev="BGB",
            norm_id="§ 433",
            title="Kaufvertrag",
            content="Der Verkäufer ist verpflichtet...",
            doc_id="BGB_433_norm",
            similarity=0.85,
        )

        result = source.to_dict()

        assert result["citation"] == "[1]"
        assert result["law"] == "BGB"
        assert result["norm_id"] == "§ 433"
        assert result["similarity"] == 0.85

    def test_source_context_excerpt(self) -> None:
        """SourceContext should create excerpt from long content."""
        long_content = "A" * 500
        source = SourceContext(
            index=1,
            law_abbrev="BGB",
            norm_id="§ 1",
            title="Test",
            content=long_content,
            doc_id="test",
            similarity=0.9,
        )

        result = source.to_dict()
        assert len(result["excerpt"]) <= 203  # 200 + "..."

    def test_rag_context_has_sources(self) -> None:
        """RAGContext.has_sources should work correctly."""
        context_with = RAGContext(
            question="Test?",
            sources=[
                SourceContext(
                    index=1,
                    law_abbrev="BGB",
                    norm_id="§ 1",
                    title="Test",
                    content="Content",
                    doc_id="test",
                    similarity=0.9,
                )
            ],
            total_retrieved=1,
        )
        context_without = RAGContext(
            question="Test?",
            sources=[],
            total_retrieved=0,
        )

        assert context_with.has_sources is True
        assert context_without.has_sources is False

    def test_build_context_from_results(
        self, mock_search_results: list[MagicMock]
    ) -> None:
        """Build context from search results."""
        context = build_context_from_results(
            question="Was ist ein Kaufvertrag?",
            search_results=mock_search_results,
            max_sources=5,
        )

        assert context.question == "Was ist ein Kaufvertrag?"
        assert len(context.sources) == 2
        assert context.sources[0].law_abbrev == "BGB"
        assert context.sources[0].norm_id == "§ 433"
        assert context.total_retrieved == 2

    def test_build_context_respects_max_sources(
        self, mock_search_results: list[MagicMock]
    ) -> None:
        """Should limit sources to max_sources."""
        context = build_context_from_results(
            question="Test?",
            search_results=mock_search_results,
            max_sources=1,
        )

        assert len(context.sources) == 1

    def test_build_context_filters_low_similarity(
        self, mock_search_results: list[MagicMock]
    ) -> None:
        """Should filter sources below min_similarity."""
        # Set second result to low similarity
        mock_search_results[1].similarity = 0.2

        context = build_context_from_results(
            question="Test?",
            search_results=mock_search_results,
            min_similarity=0.5,
        )

        assert len(context.sources) == 1


# =============================================================================
# LLM Client Tests
# =============================================================================


class TestLLMClient:
    """Tests for LLM client."""

    def test_client_initialization(self) -> None:
        """Client should initialize with defaults."""
        client = LLMClient()

        assert client.provider == "ollama"
        assert client.model == "llama3.2"
        assert client.temperature == 0.1
        assert client.max_tokens == 1024

    def test_client_custom_config(self) -> None:
        """Client should accept custom config."""
        client = LLMClient(
            provider="openai",
            model="gpt-4o-mini",
            temperature=0.5,
            max_tokens=2048,
        )

        assert client.provider == "openai"
        assert client.model == "gpt-4o-mini"
        assert client.temperature == 0.5
        assert client.max_tokens == 2048

    def test_get_model_string_ollama(self) -> None:
        """Ollama model string should have ollama/ prefix."""
        client = LLMClient(provider="ollama", model="llama3.2")
        assert client._get_model_string() == "ollama/llama3.2"

    def test_get_model_string_vllm(self) -> None:
        """vLLM model string should have openai/ prefix."""
        client = LLMClient(provider="vllm", model="uai/lm-small")
        assert client._get_model_string() == "openai/uai/lm-small"

    def test_get_model_string_openai(self) -> None:
        """OpenAI model string should not have prefix."""
        client = LLMClient(provider="openai", model="gpt-4o-mini")
        assert client._get_model_string() == "gpt-4o-mini"

    def test_llm_response_to_dict(self) -> None:
        """LLMResponse should serialize correctly."""
        response = LLMResponse(
            content="Test answer",
            model="ollama/llama3.2",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            finish_reason="stop",
            latency_ms=1234.5678,
        )

        result = response.to_dict()

        assert result["content"] == "Test answer"
        assert result["model"] == "ollama/llama3.2"
        assert result["latency_ms"] == 1234.57
        assert result["usage"]["total_tokens"] == 150

    def test_client_stats(self) -> None:
        """Client should return stats."""
        client = LLMClient(provider="ollama", model="llama3.2")
        stats = client.stats()

        assert stats["provider"] == "ollama"
        assert stats["model"] == "llama3.2"
        assert "temperature" in stats

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        """Generate should return response on success."""
        client = LLMClient()

        # Mock litellm.acompletion
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Test answer"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "ollama/llama3.2"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_response.usage.total_tokens = 150

        with patch(
            "app.rag.llm_client.litellm.acompletion", new_callable=AsyncMock
        ) as mock_completion:
            mock_completion.return_value = mock_response

            response = await client.generate(
                messages=[{"role": "user", "content": "Test"}]
            )

            assert response.content == "Test answer"
            assert response.model == "ollama/llama3.2"
            mock_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_failure(self) -> None:
        """Generate should raise on failure."""
        client = LLMClient()

        with patch(
            "app.rag.llm_client.litellm.acompletion", new_callable=AsyncMock
        ) as mock_completion:
            mock_completion.side_effect = Exception("Connection failed")

            with pytest.raises(RuntimeError, match="LLM generation failed"):
                await client.generate(messages=[{"role": "user", "content": "Test"}])


# =============================================================================
# RAG Pipeline Tests
# =============================================================================


class TestRAGPipeline:
    """Tests for RAG pipeline."""

    def test_pipeline_initialization(self) -> None:
        """Pipeline should initialize with defaults."""
        with patch("app.rag.pipeline.get_llm_client") as mock_get_client:
            mock_get_client.return_value = MagicMock()
            pipeline = RAGPipeline()

            assert pipeline.max_sources == 5
            assert pipeline.retrieval_count == 20  # Higher for reranking
            assert pipeline.min_similarity == 0.3
            assert pipeline.use_reranker is True

    def test_rag_result_to_dict(self) -> None:
        """RAGResult should serialize correctly."""
        result = RAGResult(
            question="Was ist ein Kaufvertrag?",
            answer="Ein Kaufvertrag ist...",
            sources=[{"citation": "[1]", "law": "BGB", "norm_id": "§ 433"}],
            model="ollama/llama3.2",
            retrieval_count=10,
            sources_used=3,
            retrieval_time_ms=50.5,
            generation_time_ms=1000.5,
            total_time_ms=1051.0,
            usage={"total_tokens": 500},
        )

        data = result.to_dict()

        assert data["question"] == "Was ist ein Kaufvertrag?"
        assert data["answer"] == "Ein Kaufvertrag ist..."
        assert len(data["sources"]) == 1
        assert data["timing"]["total_ms"] == 1051.0

    @pytest.mark.asyncio
    async def test_pipeline_ask_success(
        self, mock_search_results: list[MagicMock]
    ) -> None:
        """Pipeline should return result on successful ask."""
        # Mock components
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value=LLMResponse(
                content="Ein Kaufvertrag nach § 433 BGB verpflichtet...",
                model="ollama/llama3.2",
                usage={"total_tokens": 500},
                finish_reason="stop",
                latency_ms=1000.0,
            )
        )

        mock_store = MagicMock()
        mock_store.search.return_value = mock_search_results

        with patch("app.rag.pipeline.get_llm_client", return_value=mock_llm):
            pipeline = RAGPipeline()
            pipeline._embedding_store = mock_store

            result = await pipeline.ask("Was ist ein Kaufvertrag?")

            assert result.question == "Was ist ein Kaufvertrag?"
            assert "§ 433" in result.answer
            assert len(result.sources) == 2
            assert result.model == "ollama/llama3.2"

    @pytest.mark.asyncio
    async def test_pipeline_ask_with_law_filter(
        self, mock_search_results: list[MagicMock]
    ) -> None:
        """Pipeline should apply law filter to search."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value=LLMResponse(
                content="Answer",
                model="test",
                usage={},
                finish_reason="stop",
                latency_ms=100.0,
            )
        )

        mock_store = MagicMock()
        mock_store.search.return_value = mock_search_results

        with patch("app.rag.pipeline.get_llm_client", return_value=mock_llm):
            pipeline = RAGPipeline()
            pipeline._embedding_store = mock_store

            await pipeline.ask("Test?", law_filter="BGB")

            # Check that search was called with filter
            call_args = mock_store.search.call_args
            assert call_args.kwargs.get("where") is not None

    @pytest.mark.asyncio
    async def test_pipeline_health_check(self) -> None:
        """Pipeline health check should report component status."""
        mock_llm = MagicMock()
        mock_llm.health_check = AsyncMock(return_value=True)

        mock_store = MagicMock()
        mock_store.count.return_value = 193371

        with patch("app.rag.pipeline.get_llm_client", return_value=mock_llm):
            pipeline = RAGPipeline()
            pipeline._embedding_store = mock_store

            health = await pipeline.health_check()

            assert health["embedding_store"] is True
            assert health["llm"] is True
            assert health["document_count"] == 193371
            assert health["healthy"] is True

    def test_pipeline_stats(self) -> None:
        """Pipeline should return stats."""
        mock_llm = MagicMock()
        mock_llm.stats.return_value = {"provider": "ollama", "model": "llama3.2"}

        with patch("app.rag.pipeline.get_llm_client", return_value=mock_llm):
            pipeline = RAGPipeline()
            stats = pipeline.stats()

            assert "max_sources" in stats
            assert "llm" in stats
            assert stats["llm"]["provider"] == "ollama"
