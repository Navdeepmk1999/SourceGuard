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

            if not chunks:
                # Previously this committed as 'completed' with chunk_count 0:
                # the upload looked successful, the document appeared in the
                # sidebar, and it was silently absent from every answer. The
                # usual cause is a PDF with no text layer (a scan), which this
                # pipeline cannot read - there is no OCR step.
                raise IngestionError(
                    f"No extractable text found in '{filename}'. If this is a scanned "
                    "PDF, it needs OCR, which is not supported."
                )

            await _finalize(
                session,
                document_id,
                status=DocumentStatus.COMPLETED,
                chunk_count=len(chunks),
            )
            logger.info("Ingested document %s (%d chunks)", document_id, len(chunks))

    except Exception as exc:
        logger.exception("Ingestion failed for document %s", document_id)
        await _record_failure(document_id, user_id, exc, factory)


class IngestionError(RuntimeError):
    """An ingestion failure whose message is safe to show the user."""


async def _finalize(
    session: AsyncSession,
    document_id: uuid.UUID,
    *,
    status: DocumentStatus,
    chunk_count: int | None = None,
    error_message: str | None = None,
) -> None:
    """Writes a status transition and verifies it actually landed.

    The rowcount check is the important part. An UPDATE that matches no rows
    is not an error in SQL - it commits happily - so if the document were
    deleted mid-ingestion, or the RLS tenant context were missing and the
    policy filtered the row out, this would report success while writing
    nothing. That is the exact shape of a silent failure.
    """
    values: dict = {"status": status.value, "error_message": error_message}
    if chunk_count is not None:
        values["chunk_count"] = chunk_count

    result = await session.execute(
        update(Document).where(Document.id == document_id).values(**values)
    )
    if result.rowcount == 0:
        raise IngestionError(
            f"Document {document_id} was not updated - it may have been deleted, "
            "or the tenant context is missing and RLS filtered it out."
        )
    await session.commit()


async def _set_status(session, document_id: uuid.UUID, status: DocumentStatus) -> None:
    await _finalize(session, document_id, status=status)


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
        # A FRESH session, deliberately. Whatever failed above may have left
        # the original one in an aborted transaction, where every further
        # statement raises InFailedSqlTransaction - so reusing it would lose
        # the only record the user will ever see.
        async with session_factory(user_id) as session:
            await _finalize(
                session,
                document_id,
                status=DocumentStatus.FAILED,
                error_message=_user_facing_message(exc),
            )
    except Exception:
        # The failure could not even be recorded. Escalated to CRITICAL
        # because the document is now stranded at 'processing' with no
        # explanation anywhere except this line.
        logger.critical(
            "Could not record ingestion failure for %s - document is stranded",
            document_id,
            exc_info=True,
        )


def _user_facing_message(exc: Exception) -> str:
    """A short reason suitable for display next to the document.

    `.detail` is preferred over `str(exc)` for HTTPException, whose str() is
    formatted as "400: ..." and reads like a bug report rather than an
    explanation.
    """
    detail = getattr(exc, "detail", None)
    message = str(detail) if detail else str(exc)
    return (message or exc.__class__.__name__)[:1000]


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
