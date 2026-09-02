import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AuditLog, Document, DocumentChunk, Workspace
from app.models.base import Base


@pytest_asyncio.fixture
async def async_session():
    """A fresh in-memory SQLite database (schema created from the same ORM
    models used against Postgres) for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        yield session

    await engine.dispose()


class TestWorkspaceModel:
    async def test_create_workspace(self, async_session):
        workspace = Workspace(name="Acme Corp", user_id=uuid.uuid4())
        async_session.add(workspace)
        await async_session.commit()

        assert workspace.id is not None
        assert workspace.created_at is not None

    async def test_workspace_name_must_be_unique(self, async_session):
        async_session.add(Workspace(name="Acme Corp", user_id=uuid.uuid4()))
        await async_session.commit()

        async_session.add(Workspace(name="Acme Corp", user_id=uuid.uuid4()))
        with pytest.raises(Exception):
            await async_session.commit()


class TestWorkspaceDocumentChunkRelationships:
    async def test_workspace_to_document_to_chunk_relationship(self, async_session):
        workspace = Workspace(name="Acme Corp", user_id=uuid.uuid4())
        async_session.add(workspace)
        await async_session.flush()

        document = Document(workspace_id=workspace.id, filename="report.pdf", document_type="pdf")
        async_session.add(document)
        await async_session.flush()

        chunk = DocumentChunk(
            document_id=document.id,
            content="hello world",
            chunk_index=0,
            embedding=[0.1] * 1536,
            chunk_metadata={"source": "report.pdf"},
        )
        async_session.add(chunk)
        await async_session.commit()

        await async_session.refresh(workspace, attribute_names=["documents"])
        assert len(workspace.documents) == 1
        assert workspace.documents[0].id == document.id

        await async_session.refresh(document, attribute_names=["chunks", "workspace"])
        assert len(document.chunks) == 1
        assert document.chunks[0].content == "hello world"
        assert document.chunks[0].chunk_metadata == {"source": "report.pdf"}
        assert len(document.chunks[0].embedding) == 1536
        assert document.workspace.id == workspace.id

    async def test_cascade_delete_workspace_removes_documents_and_chunks(self, async_session):
        workspace = Workspace(name="Cascade Co", user_id=uuid.uuid4())
        async_session.add(workspace)
        await async_session.flush()

        document = Document(workspace_id=workspace.id, filename="a.txt", document_type="txt")
        async_session.add(document)
        await async_session.flush()

        chunk = DocumentChunk(document_id=document.id, content="chunk 1", chunk_index=0)
        async_session.add(chunk)
        await async_session.commit()

        document_id = document.id
        chunk_id = chunk.id

        await async_session.delete(workspace)
        await async_session.commit()

        assert (await async_session.get(Document, document_id)) is None
        assert (await async_session.get(DocumentChunk, chunk_id)) is None

    async def test_query_chunks_via_join(self, async_session):
        workspace = Workspace(name="Query Co", user_id=uuid.uuid4())
        async_session.add(workspace)
        await async_session.flush()

        document = Document(workspace_id=workspace.id, filename="b.txt", document_type="txt")
        async_session.add(document)
        await async_session.flush()

        async_session.add_all(
            [
                DocumentChunk(document_id=document.id, content="chunk 1", chunk_index=0),
                DocumentChunk(document_id=document.id, content="chunk 2", chunk_index=1),
            ]
        )
        await async_session.commit()

        result = await async_session.execute(
            select(DocumentChunk)
            .join(Document)
            .where(Document.workspace_id == workspace.id)
            .order_by(DocumentChunk.chunk_index)
        )
        chunks = result.scalars().all()

        assert [c.content for c in chunks] == ["chunk 1", "chunk 2"]


class TestAuditLogModel:
    async def test_create_audit_log(self, async_session):
        entity_id = uuid.uuid4()
        log = AuditLog(
            action="document.created",
            entity_type="document",
            entity_id=entity_id,
            details={"filename": "report.pdf"},
        )
        async_session.add(log)
        await async_session.commit()

        assert log.id is not None
        assert log.entity_id == entity_id
        assert log.details["filename"] == "report.pdf"
