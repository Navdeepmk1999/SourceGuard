import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import ensure_workspace_owner, get_authenticated_db, get_current_user, get_hybrid_retriever
from app.models import DocumentChunk as DocumentChunkModel
from app.models import Workspace
from app.schemas.query import QueryRequest
from app.services.generation import GenerationService
from app.services.nli_verifier import NLIVerifierService
from app.services.retriever import HybridRetriever

router = APIRouter(prefix="/api/v1/query", tags=["query"])


async def _fetch_chunks_in_order(
    session: AsyncSession, chunk_ids: list[uuid.UUID]
) -> list[DocumentChunkModel]:
    """Fetches chunks by primary key and returns them in `chunk_ids` order.

    Uses a plain WHERE ... IN query (portable across dialects) rather than the
    Postgres-specific hybrid search operators, so it works against both a real
    Postgres/pgvector database and a mock DB in tests.
    """
    if not chunk_ids:
        return []
    result = await session.execute(
        select(DocumentChunkModel).where(DocumentChunkModel.id.in_(chunk_ids))
    )
    chunks_by_id = {chunk.id: chunk for chunk in result.scalars().all()}
    return [chunks_by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in chunks_by_id]


async def _stream_query_events(
    payload: QueryRequest,
    session: AsyncSession,
    retriever: HybridRetriever,
) -> AsyncIterator[dict]:
    ranked = await retriever.hybrid_search(payload.workspace_id, payload.query, top_k=payload.top_k)
    chunks = await _fetch_chunks_in_order(session, [chunk_id for chunk_id, _ in ranked])

    if not chunks:
        yield {"event": "error", "data": json.dumps({"detail": "No relevant context found"})}
        return

    context_texts = [chunk.content for chunk in chunks]

    generation_service = GenerationService()
    answer_tokens: list[str] = []
    async for token in generation_service.stream_answer(payload.query, context_texts):
        answer_tokens.append(token)
        yield {"event": "token", "data": json.dumps({"token": token})}
    await generation_service.aclose()

    full_answer = "".join(answer_tokens)
    verification = NLIVerifierService().verify_answer(full_answer, context_texts)
    for claim in verification.claims:
        yield {
            "event": "verification",
            "data": json.dumps(
                {"claim": claim.claim, "label": claim.label.value, "score": claim.score}
            ),
        }

    yield {
        "event": "done",
        "data": json.dumps(
            {
                "answer": full_answer,
                "overall_score": verification.overall_score,
                "is_fully_supported": verification.is_fully_supported,
            }
        ),
    }


@router.post("/stream")
async def stream_query(
    payload: QueryRequest,
    session: AsyncSession = Depends(get_authenticated_db),
    user_id: uuid.UUID = Depends(get_current_user),
    retriever: HybridRetriever = Depends(get_hybrid_retriever),
) -> EventSourceResponse:
    workspace = await session.get(Workspace, payload.workspace_id)
    ensure_workspace_owner(workspace, user_id)

    return EventSourceResponse(_stream_query_events(payload, session, retriever))
