import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ensure_workspace_owner, get_authenticated_db, get_current_user
from app.models import Document
from app.models import DocumentChunk as DocumentChunkModel
from app.models import Workspace
from app.schemas.document import DocumentIngestSummary, DocumentUploadResponse
from app.services.document_parser import DocumentParser
from app.services.embeddings import EmbeddingService

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_documents(
    workspace_id: uuid.UUID = Form(...),
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_authenticated_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> DocumentUploadResponse:
    workspace = await session.get(Workspace, workspace_id)
    ensure_workspace_owner(workspace, user_id)

    parser = DocumentParser()
    embedding_service = EmbeddingService()

    summaries: list[DocumentIngestSummary] = []
    for upload in files:
        if not upload.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename")

        content = await upload.read()
        # Raises HTTPException(400/422) for path traversal, unsupported
        # extensions, or unparsable content — propagates as-is to the client.
        parsing_result = parser.parse(upload.filename, content)

        document = Document(
            id=uuid.UUID(parsing_result.document_id),
            workspace_id=workspace_id,
            filename=parsing_result.filename,
            document_type=parsing_result.document_type.value,
        )
        session.add(document)
        await session.flush()

        if parsing_result.chunks:
            embeddings = await embedding_service.embed_batch(
                [chunk.content for chunk in parsing_result.chunks]
            )
            for chunk, embedding in zip(parsing_result.chunks, embeddings, strict=True):
                session.add(
                    DocumentChunkModel(
                        id=uuid.UUID(chunk.chunk_id),
                        document_id=document.id,
                        content=chunk.content,
                        chunk_index=chunk.chunk_index,
                        embedding=embedding,
                        chunk_metadata=chunk.metadata,
                    )
                )

        summaries.append(
            DocumentIngestSummary(
                document_id=document.id,
                filename=parsing_result.filename,
                document_type=parsing_result.document_type,
                total_pages=parsing_result.total_pages,
                total_chunks=parsing_result.total_chunks,
            )
        )

    await session.commit()
    return DocumentUploadResponse(workspace_id=workspace_id, documents=summaries)
