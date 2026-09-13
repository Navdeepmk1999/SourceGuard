import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ensure_workspace_owner,
    get_authenticated_db,
    get_current_user,
    rate_limit_upload,
    rate_limit_user,
)
from app.models import Document
from app.models import DocumentChunk as DocumentChunkModel
from app.models import Workspace
from app.schemas.document import (
    DeletionResult,
    DocumentAccepted,
    DocumentStatus,
    DocumentStatusRead,
    DocumentUploadAccepted,
)
from app.services.document_parser import DocumentParser
from app.services.ingestion import ingest_document

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


async def _load_owned_document(
    session: AsyncSession, document_id: uuid.UUID, user_id: uuid.UUID
) -> Document:
    """Fetches a document the caller owns, or raises 404.

    Joins through `workspaces` rather than trusting RLS alone. RLS is the
    backstop; this is the application-layer half of the same guarantee, and it
    is what produces a clean 404 instead of an empty result the handler would
    have to interpret.

    Returns the SAME 404 for "no such document" and "not yours", so document
    ids cannot be enumerated by comparing responses.
    """
    result = await session.execute(
        select(Document)
        .join(Workspace, Document.workspace_id == Workspace.id)
        .where(Document.id == document_id, Workspace.user_id == user_id)
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


@router.post(
    "/upload",
    response_model=DocumentUploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_upload)],
)
async def upload_documents(
    background_tasks: BackgroundTasks,
    workspace_id: uuid.UUID = Form(...),
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_authenticated_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> DocumentUploadAccepted:
    """Accepts files for ingestion and returns immediately.

    202, not 201: nothing has been parsed or embedded when this returns. The
    provider's free tier paces embedding to 15 requests/minute and each chunk
    is its own request, so a 50-chunk PDF takes minutes - well past what a
    browser or proxy will hold a connection open for. The client polls
    `status_url` instead.

    Validation that can be done cheaply still happens here, synchronously, so
    a malformed filename is a 400 on upload rather than a 'failed' row the
    user has to go looking for.
    """
    workspace = await session.get(Workspace, workspace_id)
    ensure_workspace_owner(workspace, user_id)

    parser = DocumentParser()
    accepted: list[DocumentAccepted] = []
    queued: list[tuple[uuid.UUID, str, bytes]] = []

    for upload in files:
        if not upload.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename")

        content = await upload.read()
        # Rejects path traversal, disguised extensions, and unsupported types
        # before anything is persisted. Cheap, and a synchronous 400 is a far
        # better experience than a background failure.
        safe_name = parser.validate_upload(upload.filename, content)

        document = Document(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            filename=safe_name,
            document_type=parser.detect_type(safe_name).value,
            status=DocumentStatus.PENDING.value,
        )
        session.add(document)
        queued.append((document.id, safe_name, content))
        accepted.append(
            DocumentAccepted(
                document_id=document.id,
                filename=safe_name,
                status=DocumentStatus.PENDING,
                status_url=f"/api/v1/documents/{document.id}/status",
            )
        )

    # Committed before the tasks are scheduled: a task that starts against an
    # uncommitted row would find nothing to update.
    await session.commit()

    for document_id, filename, content in queued:
        background_tasks.add_task(ingest_document, document_id, user_id, filename, content)

    return DocumentUploadAccepted(workspace_id=workspace_id, documents=accepted)


@router.get(
    "/{document_id}/status",
    response_model=DocumentStatusRead,
    dependencies=[Depends(rate_limit_user)],
)
async def get_document_status(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_authenticated_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> DocumentStatusRead:
    """Ingestion progress for one document. Poll until `status` is terminal."""
    document = await _load_owned_document(session, document_id, user_id)
    return DocumentStatusRead.model_validate(document)


@router.delete("/{document_id}", response_model=DeletionResult)
async def delete_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_authenticated_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> DeletionResult:
    """Deletes a document and, by cascade, all of its chunks.

    Chunks are removed by the FK's ON DELETE CASCADE rather than by the ORM's
    delete-orphan, so a document with thousands of chunks is one statement in
    the database instead of thousands of loaded objects.
    """
    document = await _load_owned_document(session, document_id, user_id)

    chunk_count = await session.scalar(
        select(func.count())
        .select_from(DocumentChunkModel)
        .where(DocumentChunkModel.document_id == document.id)
    )
    await session.execute(delete(Document).where(Document.id == document.id))
    await session.commit()

    return DeletionResult(id=document_id, deleted_documents=1, deleted_chunks=chunk_count or 0)
