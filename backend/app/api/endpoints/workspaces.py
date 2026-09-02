import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ensure_workspace_owner, get_authenticated_db, get_current_user
from app.models import Document, DocumentChunk, Workspace
from app.schemas.document import DocumentRead
from app.schemas.workspace import WorkspaceCreate, WorkspaceRead

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceRead])
async def list_workspaces(
    session: AsyncSession = Depends(get_authenticated_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> list[Workspace]:
    """Returns the caller's own workspaces, newest first.

    Filtered explicitly by `user_id` - not left to Postgres RLS alone, since
    RLS is a no-op on the SQLite test database and would also be skipped by
    a superuser/BYPASSRLS DB role (see `ensure_workspace_owner`).
    """
    result = await session.execute(
        select(Workspace).where(Workspace.user_id == user_id).order_by(Workspace.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{workspace_id}/documents", response_model=list[DocumentRead])
async def list_workspace_documents(
    workspace_id: uuid.UUID,
    session: AsyncSession = Depends(get_authenticated_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> list[DocumentRead]:
    """Returns every document in a workspace, newest first, each with its chunk count.

    A plain outerjoin + count + group by (portable across dialects, unlike the
    hybrid-search queries in retriever.py) so this runs unmodified against the
    in-memory SQLite test database as well as Postgres.
    """
    workspace = await session.get(Workspace, workspace_id)
    ensure_workspace_owner(workspace, user_id)

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
    payload: WorkspaceCreate,
    session: AsyncSession = Depends(get_authenticated_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> Workspace:
    workspace = Workspace(name=payload.name, user_id=user_id)
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
