import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

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
