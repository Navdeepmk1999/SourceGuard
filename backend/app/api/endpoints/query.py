import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import (
    ensure_workspace_owner,
    get_authenticated_db,
    get_current_user,
    get_hybrid_retriever,
    rate_limit_user,
)
from app.models import DocumentChunk as DocumentChunkModel
from app.models import MessageRole, Workspace
from app.schemas.query import QueryRequest
from app.services import conversation
from app.services.generation import GenerationService
from app.services.nli_verifier import NLIVerifierService
from app.services.retriever import HybridRetriever
from app.services.telemetry import traced

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


@traced("retrieve_context", run_type="retriever")
async def _retrieve_context(
    payload: QueryRequest, session: AsyncSession, retriever: HybridRetriever
) -> list[str]:
    ranked = await retriever.hybrid_search(payload.workspace_id, payload.query, top_k=payload.top_k)
    chunks = await _fetch_chunks_in_order(session, [chunk_id for chunk_id, _ in ranked])
    return [chunk.content for chunk in chunks]


@traced("verify_answer")
def _verify_answer(full_answer: str, context_texts: list[str]):
    return NLIVerifierService().verify_answer(full_answer, context_texts)


@traced("rag_query_stream")
async def _stream_query_events(
    payload: QueryRequest,
    session: AsyncSession,
    retriever: HybridRetriever,
    chat_session_id: uuid.UUID,
) -> AsyncIterator[dict]:
    # Emitted first so a client that started a new conversation (session_id
    # null) learns its id immediately, rather than only on `done` - it needs
    # the id to continue the thread even if the stream errors partway.
    yield {"event": "session", "data": json.dumps({"session_id": str(chat_session_id)})}

    context_texts = await _retrieve_context(payload, session, retriever)

    if not context_texts:
        yield {"event": "error", "data": json.dumps({"detail": "No relevant context found"})}
        return

    # Loaded BEFORE persisting the current question, so the current turn
    # isn't replayed back to the model as if it were prior context.
    history = conversation.to_prompt_history(
        await conversation.load_recent_messages(session, chat_session_id)
    )
    await conversation.save_message(session, chat_session_id, MessageRole.USER, payload.query)

    generation_service = GenerationService()
    answer_tokens: list[str] = []
    async for token in generation_service.stream_answer(
        payload.query, context_texts, history=history
    ):
        answer_tokens.append(token)
        yield {"event": "token", "data": json.dumps({"token": token})}
    await generation_service.aclose()

    full_answer = "".join(answer_tokens)
    await conversation.save_message(
        session, chat_session_id, MessageRole.ASSISTANT, full_answer
    )

    verification = _verify_answer(full_answer, context_texts)
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
                "session_id": str(chat_session_id),
                "overall_score": verification.overall_score,
                "is_fully_supported": verification.is_fully_supported,
            }
        ),
    }


@router.post("/stream", dependencies=[Depends(rate_limit_user)])
async def stream_query(
    payload: QueryRequest,
    session: AsyncSession = Depends(get_authenticated_db),
    user_id: uuid.UUID = Depends(get_current_user),
    retriever: HybridRetriever = Depends(get_hybrid_retriever),
) -> EventSourceResponse:
    workspace = await session.get(Workspace, payload.workspace_id)
    ensure_workspace_owner(workspace, user_id)

    # Resolved here rather than inside the generator: a bad session_id must
    # surface as a real 404 response, and once EventSourceResponse is
    # returned the status line is already committed - an error raised inside
    # the generator can only be reported as an in-band SSE `error` event.
    try:
        chat_session = await conversation.get_or_create_session(
            session, payload.workspace_id, user_id, payload.session_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
        ) from exc

    return EventSourceResponse(
        _stream_query_events(payload, session, retriever, chat_session.id)
    )
