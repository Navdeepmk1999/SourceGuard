from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.embeddings import EmbeddingService
from app.services.retriever import HybridRetriever


async def get_hybrid_retriever(session: AsyncSession = Depends(get_db)) -> HybridRetriever:
    """Overridable in tests so callers don't need a live Postgres/pgvector instance."""
    return HybridRetriever(session=session, embedding_service=EmbeddingService())
