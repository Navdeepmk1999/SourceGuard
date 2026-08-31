import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Document, DocumentChunk, Workspace
from app.schemas.document import DocumentRead
from app.schemas.workspace import WorkspaceCreate, WorkspaceRead

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceRead])
async def list_workspaces(session: AsyncSession = Depends(get_db)) -> list[Workspace]:
    """Returns every workspace, newest first."""
    result = await session.execute(select(Workspace).order_by(Workspace.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{workspace_id}/documents", response_model=list[DocumentRead])
async def list_workspace_documents(
    workspace_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> list[DocumentRead]:
    """Returns every document in a workspace, newest first, each with its chunk count.

    A plain outerjoin + count + group by (portable across dialects, unlike the
    hybrid-search queries in retriever.py) so this runs unmodified against the
    in-memory SQLite test database as well as Postgres.
    """
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    result = await session.execute(
        select(Document, func.count(DocumentChunk.id).label("total_chunks"))
        .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
        .where(Document.workspace_id == workspace_id)
        .group_by(Document.id)
        .order_by(Document.created_at.desc())
    )
    return [
        DocumentRead(
            id=document.id,
            filename=document.filename,
            document_type=document.document_type,
            created_at=document.created_at,
            total_chunks=total_chunks,
        )
        for document, total_chunks in result.all()
    ]


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate, session: AsyncSession = Depends(get_db)
) -> Workspace:
    workspace = Workspace(name=payload.name)
    session.add(workspace)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A workspace named '{payload.name}' already exists",
        ) from exc

    await session.refresh(workspace)
    return workspace
