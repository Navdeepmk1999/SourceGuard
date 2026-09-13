"""Background ingestion: parse, chunk, embed, and persist an uploaded file.

Runs *after* the HTTP response is sent, via FastAPI's BackgroundTasks. That
choice is deliberate over a broker-backed queue (ARQ/Celery): those need a
separate worker process, which Render's free tier does not offer, so adopting
one would force a paid plan. The tradeoff is real and worth stating - an
in-process task does not survive a restart or a deploy, and a document left
mid-flight stays 'processing' forever. `reap_stale_documents` exists for that.

The work itself is unchanged; what changed is who waits for it. Embedding is
paced to 15 requests/minute by the provider's free-tier limit, so a 50-chunk
document takes minutes - far longer than any browser or proxy will hold a
connection open.
"""

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import tenant_session
from app.models.chunk import DocumentChunk as DocumentChunkModel
from app.models.document import Document
from app.schemas.document import DocumentStatus
from app.services.document_parser import DocumentParser
from app.services.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

# Injectable so tests can bind ingestion to their own engine. A background
# task builds its session outside the request, so it never passes through
# FastAPI's dependency overrides - without this seam a test would silently
# run against whatever DATABASE_URL the environment happens to hold.
SessionFactory = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


async def ingest_document(
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    content: bytes,
    session_factory: SessionFactory | None = None,
) -> None:
    """Parses and embeds one uploaded file, updating its row as it goes.

    Never raises. A background task has no caller to propagate to - an
    exception here would be swallowed by the event loop and the document would
    sit at 'processing' with no explanation. Failures are recorded on the row
    instead, which is the only channel the client can still observe.
    """
    factory = session_factory or tenant_session
    try:
        async with factory(user_id) as session:
            await _set_status(session, document_id, DocumentStatus.PROCESSING)

            parser = DocumentParser()
            parsing_result = parser.parse(filename, content)

            chunks = parsing_result.chunks
            if chunks:
                embedding_service = EmbeddingService()
                embeddings = await embedding_service.embed_batch(
                    [chunk.content for chunk in chunks]
                )
                for chunk, embedding in zip(chunks, embeddings, strict=True):
                    session.add(
                        DocumentChunkModel(
                            id=uuid.UUID(chunk.chunk_id),
                            document_id=document_id,
                            content=chunk.content,
                            chunk_index=chunk.chunk_index,
                            embedding=embedding,
                            chunk_metadata=chunk.metadata,
                        )
                    )

            await session.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(
                    status=DocumentStatus.COMPLETED.value,
                    chunk_count=len(chunks),
                    error_message=None,
                )
            )
            await session.commit()
            logger.info("Ingested document %s (%d chunks)", document_id, len(chunks))

    except Exception as exc:
        logger.exception("Ingestion failed for document %s", document_id)
        await _record_failure(document_id, user_id, exc, factory)


async def _set_status(session, document_id: uuid.UUID, status: DocumentStatus) -> None:
    await session.execute(
        update(Document).where(Document.id == document_id).values(status=status.value)
    )
    await session.commit()


async def _record_failure(
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    exc: Exception,
    session_factory: SessionFactory,
) -> None:
    """Writes the failure onto the row, in its own session.

    A fresh session is required: whatever failed above may have left the
    original one in a broken transaction, and this write is the only way the
    user ever learns the upload did not work.
    """
    try:
        async with session_factory(user_id) as session:
            await session.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(
                    status=DocumentStatus.FAILED.value,
                    # str(exc) rather than repr: this reaches the UI, and
                    # HTTPException's repr is noise to an end user.
                    error_message=str(exc)[:1000] or exc.__class__.__name__,
                )
            )
            await session.commit()
    except Exception:
        # Nothing further can be done - the document stays 'processing' and
        # will be picked up by the stale reaper.
        logger.exception("Could not record ingestion failure for %s", document_id)


async def reap_stale_documents(
    user_id: uuid.UUID,
    older_than_minutes: int = 60,
    session_factory: SessionFactory | None = None,
) -> int:
    """Marks long-'processing' documents as failed.

    In-process tasks die with the process, so a restart mid-ingestion strands
    rows at 'processing' with no task left to finish or fail them. Without
    this the UI polls them forever.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    async with (session_factory or tenant_session)(user_id) as session:
        result = await session.execute(
            select(Document.id).where(
                Document.status == DocumentStatus.PROCESSING.value,
                Document.updated_at < cutoff,
            )
        )
        stale = [row[0] for row in result]
        if stale:
            await session.execute(
                update(Document)
                .where(Document.id.in_(stale))
                .values(
                    status=DocumentStatus.FAILED.value,
                    error_message="Ingestion did not complete (server restarted).",
                )
            )
            await session.commit()
        return len(stale)
