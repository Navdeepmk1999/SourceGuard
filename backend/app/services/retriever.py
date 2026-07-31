import uuid
from collections import defaultdict

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.services.embeddings import EmbeddingService


def build_vector_search_query(workspace_id: uuid.UUID, query_embedding: list[float], limit: int = 20) -> Select:
    """ANN query ranking chunks by pgvector cosine distance, scoped to a workspace."""
    distance = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
    return (
        select(DocumentChunk.id, distance)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(Document.workspace_id == workspace_id)
        .order_by(distance.asc())
        .limit(limit)
    )


def build_keyword_search_query(workspace_id: uuid.UUID, query_text: str, limit: int = 20) -> Select:
    """PostgreSQL full-text search query ranking chunks by ts_rank against a tsquery."""
    ts_vector = func.to_tsvector("english", DocumentChunk.content)
    ts_query = func.plainto_tsquery("english", query_text)
    rank = func.ts_rank(ts_vector, ts_query).label("rank")
    return (
        select(DocumentChunk.id, rank)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(Document.workspace_id == workspace_id)
        .where(ts_vector.op("@@")(ts_query))
        .order_by(rank.desc())
        .limit(limit)
    )


def reciprocal_rank_fusion(
    ranked_id_lists: list[list[uuid.UUID]], k: int = 60
) -> list[tuple[uuid.UUID, float]]:
    """Merges ranked ID lists via RRF: score(id) = sum(1 / (k + rank)) across
    every list the id appears in. Returns (id, score) sorted by score desc."""
    scores: dict[uuid.UUID, float] = defaultdict(float)
    for ranked_ids in ranked_id_lists:
        for rank, chunk_id in enumerate(ranked_ids, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


class HybridRetriever:
    """Combines pgvector cosine-similarity search with PostgreSQL full-text
    search, merging both ranked result sets via Reciprocal Rank Fusion."""

    def __init__(
        self,
        session: AsyncSession,
        embedding_service: EmbeddingService,
        vector_limit: int = 20,
        keyword_limit: int = 20,
        rrf_k: int = 60,
    ) -> None:
        self.session = session
        self.embedding_service = embedding_service
        self.vector_limit = vector_limit
        self.keyword_limit = keyword_limit
        self.rrf_k = rrf_k

    async def vector_search(self, workspace_id: uuid.UUID, query_embedding: list[float]) -> list[uuid.UUID]:
        stmt = build_vector_search_query(workspace_id, query_embedding, self.vector_limit)
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def keyword_search(self, workspace_id: uuid.UUID, query_text: str) -> list[uuid.UUID]:
        stmt = build_keyword_search_query(workspace_id, query_text, self.keyword_limit)
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def hybrid_search(
        self, workspace_id: uuid.UUID, query_text: str, top_k: int = 10
    ) -> list[tuple[uuid.UUID, float]]:
        query_embedding = await self.embedding_service.embed(query_text)
        vector_ids = await self.vector_search(workspace_id, query_embedding)
        keyword_ids = await self.keyword_search(workspace_id, query_text)
        fused = reciprocal_rank_fusion([vector_ids, keyword_ids], k=self.rrf_k)
        return fused[:top_k]
