import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

from app.services.embeddings import EMBEDDING_DIMENSIONS, EmbeddingService
from app.services.nli_verifier import EntailmentLabel, NLIVerifierService
from app.services.retriever import (
    HybridRetriever,
    build_keyword_search_query,
    build_vector_search_query,
    reciprocal_rank_fusion,
)


def compile_sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))


class TestReciprocalRankFusion:
    def test_single_list_preserves_relative_order(self):
        ids = [uuid.uuid4() for _ in range(3)]
        fused = reciprocal_rank_fusion([ids])
        assert [chunk_id for chunk_id, _ in fused] == ids

    def test_ids_present_in_both_lists_rank_above_single_list_ids(self):
        shared = uuid.uuid4()
        vector_only = uuid.uuid4()
        keyword_only = uuid.uuid4()

        vector_results = [shared, vector_only]
        keyword_results = [shared, keyword_only]

        fused = reciprocal_rank_fusion([vector_results, keyword_results])
        fused_ids = [chunk_id for chunk_id, _ in fused]

        assert fused_ids[0] == shared
        assert set(fused_ids[1:]) == {vector_only, keyword_only}

    def test_score_matches_rrf_formula(self):
        chunk_id = uuid.uuid4()
        k = 60
        # rank 1 in list A, rank 2 in list B
        fused = reciprocal_rank_fusion([[chunk_id], [uuid.uuid4(), chunk_id]], k=k)
        score = dict(fused)[chunk_id]
        expected = 1.0 / (k + 1) + 1.0 / (k + 2)
        assert score == pytest.approx(expected)

    def test_empty_lists_return_empty_result(self):
        assert reciprocal_rank_fusion([]) == []
        assert reciprocal_rank_fusion([[], []]) == []

    def test_custom_k_changes_relative_weighting(self):
        chunk_id = uuid.uuid4()
        score_small_k = dict(reciprocal_rank_fusion([[chunk_id]], k=1))[chunk_id]
        score_large_k = dict(reciprocal_rank_fusion([[chunk_id]], k=1000))[chunk_id]
        assert score_small_k > score_large_k


class TestHybridSearchQueryGeneration:
    def test_vector_query_uses_cosine_distance_operator(self):
        workspace_id = uuid.uuid4()
        stmt = build_vector_search_query(workspace_id, [0.1] * EMBEDDING_DIMENSIONS, limit=5)
        sql = compile_sql(stmt)

        assert "<=>" in sql
        assert "document_chunks.embedding" in sql
        assert "documents.workspace_id" in sql
        assert "ORDER BY distance ASC" in sql
        assert "LIMIT" in sql

    def test_keyword_query_uses_full_text_search_functions(self):
        workspace_id = uuid.uuid4()
        stmt = build_keyword_search_query(workspace_id, "python security", limit=5)
        sql = compile_sql(stmt)

        assert "to_tsvector" in sql
        assert "plainto_tsquery" in sql
        assert "ts_rank" in sql
        assert "@@" in sql
        assert "ORDER BY rank DESC" in sql

    def test_keyword_query_binds_query_text_as_parameter(self):
        workspace_id = uuid.uuid4()
        stmt = build_keyword_search_query(workspace_id, "python security", limit=5)
        compiled = stmt.compile(dialect=postgresql.dialect())
        assert "python security" in compiled.params.values()

    def test_vector_query_scopes_to_workspace(self):
        workspace_id = uuid.uuid4()
        stmt = build_vector_search_query(workspace_id, [0.1] * EMBEDDING_DIMENSIONS, limit=5)
        compiled = stmt.compile(dialect=postgresql.dialect())
        assert compiled.params["workspace_id_1"] == workspace_id


class TestHybridRetrieverOrchestration:
    async def test_hybrid_search_merges_vector_and_keyword_results(self):
        workspace_id = uuid.uuid4()
        shared = uuid.uuid4()
        vector_only = uuid.uuid4()
        keyword_only = uuid.uuid4()

        embedding_service = AsyncMock()
        embedding_service.embed.return_value = [0.1] * EMBEDDING_DIMENSIONS

        retriever = HybridRetriever(session=AsyncMock(), embedding_service=embedding_service)
        retriever.vector_search = AsyncMock(return_value=[shared, vector_only])
        retriever.keyword_search = AsyncMock(return_value=[shared, keyword_only])

        results = await retriever.hybrid_search(workspace_id, "test query", top_k=10)
        result_ids = [chunk_id for chunk_id, _ in results]

        embedding_service.embed.assert_awaited_once_with("test query")
        retriever.vector_search.assert_awaited_once_with(workspace_id, [0.1] * EMBEDDING_DIMENSIONS)
        retriever.keyword_search.assert_awaited_once_with(workspace_id, "test query")
        assert result_ids[0] == shared
        assert set(result_ids) == {shared, vector_only, keyword_only}

    async def test_hybrid_search_respects_top_k(self):
        workspace_id = uuid.uuid4()
        many_ids = [uuid.uuid4() for _ in range(5)]

        embedding_service = AsyncMock()
        embedding_service.embed.return_value = [0.1] * EMBEDDING_DIMENSIONS

        retriever = HybridRetriever(session=AsyncMock(), embedding_service=embedding_service)
        retriever.vector_search = AsyncMock(return_value=many_ids)
        retriever.keyword_search = AsyncMock(return_value=[])

        results = await retriever.hybrid_search(workspace_id, "query", top_k=2)
        assert len(results) == 2


class TestEmbeddingServiceMock:
    async def test_mock_embedding_has_correct_dimensions(self):
        service = EmbeddingService()
        assert service.is_live is False
        embedding = await service.embed("hello world")
        assert len(embedding) == EMBEDDING_DIMENSIONS

    async def test_mock_embedding_is_deterministic(self):
        service = EmbeddingService()
        first = await service.embed("SourceGuard verification")
        second = await service.embed("SourceGuard verification")
        assert first == second

    async def test_mock_embedding_differs_for_different_text(self):
        service = EmbeddingService()
        first = await service.embed("claim one")
        second = await service.embed("a completely different claim")
        assert first != second

    async def test_mock_embedding_is_unit_normalized(self):
        service = EmbeddingService()
        embedding = await service.embed("normalize me")
        norm = sum(x * x for x in embedding) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)


class TestClaimDecomposition:
    def test_splits_multiple_sentences(self):
        verifier = NLIVerifierService()
        claims = verifier.decompose_claims(
            "SourceGuard uses pgvector for search. It also uses Redis for caching!"
        )
        assert claims == [
            "SourceGuard uses pgvector for search.",
            "It also uses Redis for caching!",
        ]

    def test_empty_answer_returns_no_claims(self):
        verifier = NLIVerifierService()
        assert verifier.decompose_claims("") == []
        assert verifier.decompose_claims("   ") == []

    def test_single_sentence_answer(self):
        verifier = NLIVerifierService()
        assert verifier.decompose_claims("Only one claim here.") == ["Only one claim here."]


class TestNLIEntailmentScoring:
    def test_claim_fully_supported_by_chunk_is_entailed(self):
        verifier = NLIVerifierService()
        result = verifier.verify_claim(
            "SourceGuard uses pgvector for vector search.",
            ["SourceGuard relies on pgvector to perform vector search efficiently."],
        )
        assert result.label == EntailmentLabel.ENTAILED
        assert result.score >= verifier.entailment_threshold
        assert result.supporting_chunk_index == 0

    def test_claim_with_no_overlap_is_insufficient_evidence(self):
        verifier = NLIVerifierService()
        result = verifier.verify_claim(
            "The mitochondria is the powerhouse of the cell.",
            ["SourceGuard uses Redis for caching query results."],
        )
        assert result.label == EntailmentLabel.INSUFFICIENT_EVIDENCE
        assert result.supporting_chunk_index is None

    def test_claim_with_partial_overlap_is_not_entailed(self):
        verifier = NLIVerifierService(entailment_threshold=0.8, insufficient_threshold=0.2)
        result = verifier.verify_claim(
            "SourceGuard uses pgvector and Redis and Groq for its stack.",
            ["SourceGuard uses pgvector for storage."],
        )
        assert result.label == EntailmentLabel.NOT_ENTAILED

    def test_word_boundary_prevents_partial_substring_match(self):
        """CRITICAL regression test: the keyword 'cat' must not match inside
        'category' — verifying the mandatory \\b word-boundary enforcement."""
        verifier = NLIVerifierService()
        result = verifier.verify_claim(
            "The cat sat on the mat.",
            ["This category can be tricky to matriculate through."],
        )
        # Without word boundaries, "cat"->"category", "sat"->(none), "mat"->"matriculate"
        # would inflate the score. With boundaries enforced, none of the
        # claim's whole words appear in the chunk, so evidence is insufficient.
        assert result.label == EntailmentLabel.INSUFFICIENT_EVIDENCE
        assert result.score == 0.0

    def test_verify_answer_aggregates_claims(self):
        verifier = NLIVerifierService()
        answer = "SourceGuard uses pgvector for search. It runs on FastAPI."
        source_chunks = [
            "SourceGuard uses pgvector for vector search.",
            "The backend runs on FastAPI with async support.",
        ]
        result = verifier.verify_answer(answer, source_chunks)

        assert len(result.claims) == 2
        assert result.is_fully_supported is True
        assert result.overall_score > 0

    def test_verify_answer_not_fully_supported_when_one_claim_fails(self):
        verifier = NLIVerifierService()
        answer = "SourceGuard uses pgvector for search. Bananas are a good source of potassium."
        source_chunks = ["SourceGuard uses pgvector for vector search."]
        result = verifier.verify_answer(answer, source_chunks)

        assert result.is_fully_supported is False

    def test_verify_answer_empty_answer_returns_zero_score(self):
        verifier = NLIVerifierService()
        result = verifier.verify_answer("", ["some source text"])
        assert result.claims == []
        assert result.overall_score == 0.0
        assert result.is_fully_supported is False

    def test_invalid_thresholds_raise_value_error(self):
        with pytest.raises(ValueError):
            NLIVerifierService(entailment_threshold=0.2, insufficient_threshold=0.6)


class TestEmbeddingServiceLiveRequest:
    """Covers the live HTTP path, which previously had no test at all.

    The pgvector column is fixed at VECTOR(EMBEDDING_DIMENSIONS) by DDL and
    `create_all` will not alter it, so any drift between the configured width
    and what the provider actually returns must surface as a loud 502 rather
    than an opaque INSERT failure.
    """

    @staticmethod
    def _service(handler) -> EmbeddingService:
        settings = get_settings().model_copy(
            update={"together_api_key": "test-key", "embedding_model": "gemini-embedding-001"}
        )
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://example.invalid/v1"
        )
        # Zero intervals: the pacing and backoff *policy* is asserted
        # separately; making every test wait 4s per request would add minutes
        # to the suite and test asyncio.sleep rather than this code.
        return EmbeddingService(
            settings=settings, client=client, min_request_interval=0, base_retry_delay=0
        )

    async def test_request_omits_the_dimensions_field(self):
        """Gemini 400s if `dimensions` is present - regression guard.

        Sending it is the intuitive way to pin the vector width, and it is
        what the OpenAI embeddings spec allows, so it is an easy thing to
        re-add. Gemini's compatibility layer rejects the whole request, which
        takes down ingestion and query together. The width is instead matched
        by setting EMBEDDING_DIMENSIONS to the model's native output.
        """
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200, json={"data": [{"embedding": [0.1] * EMBEDDING_DIMENSIONS}]}
            )

        service = self._service(handler)
        embedding = await service.embed("hello")

        assert "dimensions" not in captured, "Gemini returns 400 when `dimensions` is sent"
        assert isinstance(captured["input"], str), "Gemini returns 400 when `input` is a list"
        assert captured["model"] == "gemini-embedding-001"
        assert len(embedding) == EMBEDDING_DIMENSIONS

    async def test_wrong_width_is_rejected_not_persisted(self):
        """A width other than the configured one must fail loudly.

        Realistic trigger: swapping embedding_model to a model with a
        different native width without updating EMBEDDING_DIMENSIONS. Without
        this check the vector reaches the INSERT and either errors opaquely
        or, on a permissive backend, silently corrupts search.
        """
        wrong_width = EMBEDDING_DIMENSIONS // 4

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"embedding": [0.1] * wrong_width}]})

        service = self._service(handler)
        with pytest.raises(HTTPException) as exc:
            await service.embed("hello")

        assert exc.value.status_code == 502
        assert str(wrong_width) in exc.value.detail
        assert str(EMBEDDING_DIMENSIONS) in exc.value.detail

    async def test_rejected_dimensions_parameter_surfaces_as_502(self):
        """If the compatibility layer rejects `dimensions`, it must not 500."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "Unknown field: dimensions"}})

        service = self._service(handler)
        with pytest.raises(HTTPException) as exc:
            await service.embed("hello")

        assert exc.value.status_code == 502


class TestEmbeddingBatchFanOut:
    """Gemini rejects a list `input`, so batching is done client-side.

    That turns one call into N, which makes two properties load-bearing:
    every request must carry a single string, and the returned vectors must
    stay in the caller's order - chunks are matched to embeddings by position,
    so a reordered result would attach each vector to the wrong chunk without
    raising anything.
    """

    @staticmethod
    def _service(handler) -> EmbeddingService:
        settings = get_settings().model_copy(
            update={"together_api_key": "test-key", "embedding_model": "gemini-embedding-001"}
        )
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://example.invalid/v1"
        )
        # Zero intervals: the pacing and backoff *policy* is asserted
        # separately; making every test wait 4s per request would add minutes
        # to the suite and test asyncio.sleep rather than this code.
        return EmbeddingService(
            settings=settings, client=client, min_request_interval=0, base_retry_delay=0
        )

    async def test_one_request_per_text_each_with_a_string_input(self):
        seen: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append(body["input"])
            return httpx.Response(
                200, json={"data": [{"embedding": [0.1] * EMBEDDING_DIMENSIONS}]}
            )

        service = self._service(handler)
        texts = ["alpha", "beta", "gamma", "delta"]
        results = await service.embed_batch(texts)

        assert len(seen) == len(texts), "expected one request per text"
        assert all(isinstance(i, str) for i in seen), "`input` must never be a list"
        assert sorted(seen) == sorted(texts)
        assert len(results) == len(texts)

    async def test_results_keep_caller_order_despite_concurrency(self):
        """Slow-first responses must not reorder the returned vectors."""
        order = {"alpha": 0, "beta": 1, "gamma": 2, "delta": 3}

        async def handler(request: httpx.Request) -> httpx.Response:
            text = json.loads(request.content)["input"]
            # Invert completion order relative to submission order.
            await asyncio.sleep((len(order) - order[text]) * 0.01)
            vector = [float(order[text])] * EMBEDDING_DIMENSIONS
            return httpx.Response(200, json={"data": [{"embedding": vector}]})

        service = self._service(handler)
        results = await service.embed_batch(list(order))

        assert [r[0] for r in results] == [0.0, 1.0, 2.0, 3.0]

    async def test_empty_input_makes_no_requests(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be sent for an empty batch")

        assert await self._service(handler).embed_batch([]) == []

    async def test_malformed_payload_is_502_not_500(self):
        """A 200 with an unexpected shape must not surface as an unhandled error."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        with pytest.raises(HTTPException) as exc:
            await self._service(handler).embed("hello")
        assert exc.value.status_code == 502

    async def test_one_failing_request_fails_the_batch(self):
        """Fail fast: a partial batch would persist chunks with no embedding."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 2:
                return httpx.Response(500, json={"error": "upstream exploded"})
            return httpx.Response(
                200, json={"data": [{"embedding": [0.1] * EMBEDDING_DIMENSIONS}]}
            )

        with pytest.raises(HTTPException) as exc:
            await self._service(handler).embed_batch(["a", "b", "c"])
        assert exc.value.status_code == 502


class TestEmbeddingRateLimitHandling:
    """Gemini's free tier allows 15 requests/minute and 429s beyond it.

    One text per request means a single document can exceed that on its own,
    so a 429 has to pause and retry rather than fail the upload.
    """

    @staticmethod
    def _service(handler, **kwargs) -> EmbeddingService:
        settings = get_settings().model_copy(
            update={"together_api_key": "test-key", "embedding_model": "gemini-embedding-001"}
        )
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://example.invalid/v1"
        )
        kwargs.setdefault("min_request_interval", 0)
        kwargs.setdefault("base_retry_delay", 0)
        return EmbeddingService(settings=settings, client=client, **kwargs)

    async def test_429_is_retried_and_eventually_succeeds(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429, json={"error": "rate limit"})
            return httpx.Response(
                200, json={"data": [{"embedding": [0.1] * EMBEDDING_DIMENSIONS}]}
            )

        embedding = await self._service(handler).embed("hello")
        assert calls["n"] == 3, "expected two retries then success"
        assert len(embedding) == EMBEDDING_DIMENSIONS

    async def test_one_rate_limited_chunk_does_not_fail_the_batch(self):
        """The whole point: a 429 on one chunk must not abort the upload."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            text = json.loads(request.content)["input"]
            seen[text] = seen.get(text, 0) + 1
            if text == "beta" and seen[text] == 1:
                return httpx.Response(429, json={"error": "rate limit"})
            return httpx.Response(
                200, json={"data": [{"embedding": [0.1] * EMBEDDING_DIMENSIONS}]}
            )

        results = await self._service(handler).embed_batch(["alpha", "beta", "gamma"])
        assert len(results) == 3
        assert seen["beta"] == 2, "the rate-limited chunk should have been retried"

    async def test_persistent_429_eventually_gives_up_as_502(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429, json={"error": "rate limit"})

        with pytest.raises(HTTPException) as exc:
            await self._service(handler).embed("hello")
        assert exc.value.status_code == 502
        assert calls["n"] > 1, "should have retried before giving up"

    async def test_non_429_errors_are_not_retried(self):
        """A 400 is deterministic - retrying burns quota and delays the error."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(400, json={"error": "bad request"})

        with pytest.raises(HTTPException) as exc:
            await self._service(handler).embed("hello")
        assert exc.value.status_code == 502
        assert calls["n"] == 1, "a 400 must not be retried"

    async def test_retry_after_header_is_honoured(self):
        service = self._service(lambda r: httpx.Response(200), base_retry_delay=999)
        response = httpx.Response(429, headers={"Retry-After": "7"})
        assert service._retry_delay(response, attempt=0) == 7.0

    async def test_backoff_grows_and_is_jittered(self):
        service = self._service(lambda r: httpx.Response(200), base_retry_delay=2)
        response = httpx.Response(429)
        first = service._retry_delay(response, attempt=0)
        third = service._retry_delay(response, attempt=2)
        assert 2 <= first < 3, "base delay plus proportional jitter"
        assert 8 <= third < 12, "exponential growth plus proportional jitter"
        assert third > first, "backoff must grow with attempt"

    async def test_zero_base_delay_produces_no_wait(self):
        """Jitter must scale with the delay, not be a flat addend.

        A flat jitter term would keep retries sleeping even when backoff is
        configured to zero, which is what makes these tests fast.
        """
        service = self._service(lambda r: httpx.Response(200), base_retry_delay=0)
        assert service._retry_delay(httpx.Response(429), attempt=3) == 0

    async def test_pacer_spaces_request_starts(self):
        """The pacer, not the semaphore, is what bounds the request rate."""
        from app.services.embeddings import _RequestPacer

        pacer = _RequestPacer(0.05)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.gather(*(pacer.wait() for _ in range(4)))
        elapsed = loop.time() - start
        # Four starts spaced 0.05s apart: the first is immediate, so >= 0.15s.
        assert elapsed >= 0.15, f"expected pacing, took only {elapsed:.3f}s"
