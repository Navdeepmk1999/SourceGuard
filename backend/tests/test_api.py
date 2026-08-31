import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_hybrid_retriever
from app.db.session import get_db
from app.main import app
from app.models import Base, Document, DocumentChunk, Workspace


class _StubRetriever:
    """Bypasses the Postgres-only hybrid search SQL (pgvector cosine distance,
    full-text search) so the query endpoint is testable against a mock SQLite
    DB. Returns a fixed, already-ranked list of chunk ids."""

    def __init__(self, chunk_ids: list[uuid.UUID]) -> None:
        self._chunk_ids = chunk_ids

    async def hybrid_search(self, workspace_id, query_text, top_k=10):
        return [(chunk_id, 1.0 / (rank + 1)) for rank, chunk_id in enumerate(self._chunk_ids[:top_k])]


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_maker(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def client(session_maker):
    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class TestWorkspaceCreation:
    async def test_create_workspace_returns_201(self, client):
        response = await client.post("/api/v1/workspaces", json={"name": "Acme Research"})
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Acme Research"
        assert uuid.UUID(body["id"])
        assert "created_at" in body

    async def test_create_workspace_rejects_empty_name(self, client):
        response = await client.post("/api/v1/workspaces", json={"name": ""})
        assert response.status_code == 422

    async def test_create_workspace_duplicate_name_returns_409(self, client):
        await client.post("/api/v1/workspaces", json={"name": "Duplicate Co"})
        response = await client.post("/api/v1/workspaces", json={"name": "Duplicate Co"})
        assert response.status_code == 409


class TestWorkspaceListing:
    async def test_list_workspaces_returns_empty_list(self, client):
        response = await client.get("/api/v1/workspaces")
        assert response.status_code == 200
        assert response.json() == []

    async def test_list_workspaces_orders_newest_first(self, client, session_maker):
        # Inserted directly with explicit timestamps: server_default=func.now() has
        # only second-level resolution on SQLite, so three rapid API-created rows
        # would tie and make ordering unverifiable.
        async with session_maker() as session:
            base = datetime(2024, 1, 1, tzinfo=UTC)
            session.add_all(
                [
                    Workspace(name="Oldest Co", created_at=base),
                    Workspace(name="Middle Co", created_at=base + timedelta(hours=1)),
                    Workspace(name="Newest Co", created_at=base + timedelta(hours=2)),
                ]
            )
            await session.commit()

        response = await client.get("/api/v1/workspaces")
        assert response.status_code == 200
        names = [workspace["name"] for workspace in response.json()]
        assert names == ["Newest Co", "Middle Co", "Oldest Co"]

    async def test_list_workspaces_returns_workspace_read_shape(self, client):
        created = await client.post("/api/v1/workspaces", json={"name": "Shape Co"})
        created_body = created.json()

        response = await client.get("/api/v1/workspaces")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        workspace = body[0]
        assert set(workspace.keys()) == {"id", "name", "created_at"}
        assert workspace["id"] == created_body["id"]
        assert workspace["name"] == "Shape Co"
        assert uuid.UUID(workspace["id"])
        assert "created_at" in workspace


class TestWorkspaceDocuments:
    async def test_list_documents_unknown_workspace_returns_404(self, client):
        response = await client.get(f"/api/v1/workspaces/{uuid.uuid4()}/documents")
        assert response.status_code == 404

    async def test_list_documents_returns_empty_list(self, client):
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "Empty Docs Co"})
        workspace_id = ws_resp.json()["id"]

        response = await client.get(f"/api/v1/workspaces/{workspace_id}/documents")
        assert response.status_code == 200
        assert response.json() == []

    async def test_list_documents_orders_newest_first(self, client, session_maker):
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "Ordered Docs Co"})
        workspace_id = uuid.UUID(ws_resp.json()["id"])

        # Inserted directly with explicit timestamps: server_default=func.now() has
        # only second-level resolution on SQLite, so three rapid inserts would tie.
        async with session_maker() as session:
            base = datetime(2024, 1, 1, tzinfo=UTC)
            session.add_all(
                [
                    Document(
                        workspace_id=workspace_id, filename="oldest.txt",
                        document_type="txt", created_at=base,
                    ),
                    Document(
                        workspace_id=workspace_id, filename="middle.txt",
                        document_type="txt", created_at=base + timedelta(hours=1),
                    ),
                    Document(
                        workspace_id=workspace_id, filename="newest.txt",
                        document_type="txt", created_at=base + timedelta(hours=2),
                    ),
                ]
            )
            await session.commit()

        response = await client.get(f"/api/v1/workspaces/{workspace_id}/documents")
        assert response.status_code == 200
        filenames = [doc["filename"] for doc in response.json()]
        assert filenames == ["newest.txt", "middle.txt", "oldest.txt"]

    async def test_list_documents_returns_document_read_shape_with_chunk_counts(
        self, client, session_maker
    ):
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "Shaped Docs Co"})
        workspace_id = uuid.UUID(ws_resp.json()["id"])

        async with session_maker() as session:
            with_chunks = Document(
                workspace_id=workspace_id, filename="report.pdf", document_type="pdf"
            )
            without_chunks = Document(
                workspace_id=workspace_id, filename="empty.txt", document_type="txt"
            )
            session.add_all([with_chunks, without_chunks])
            await session.flush()
            session.add_all(
                [
                    DocumentChunk(document_id=with_chunks.id, content="first chunk", chunk_index=0),
                    DocumentChunk(document_id=with_chunks.id, content="second chunk", chunk_index=1),
                ]
            )
            await session.commit()

        response = await client.get(f"/api/v1/workspaces/{workspace_id}/documents")
        assert response.status_code == 200
        body = response.json()
        by_filename = {doc["filename"]: doc for doc in body}

        report = by_filename["report.pdf"]
        assert set(report.keys()) == {"id", "filename", "document_type", "created_at", "total_chunks"}
        assert uuid.UUID(report["id"])
        assert report["document_type"] == "pdf"
        assert report["total_chunks"] == 2

        empty = by_filename["empty.txt"]
        assert empty["document_type"] == "txt"
        assert empty["total_chunks"] == 0


class TestDocumentUpload:
    async def test_upload_txt_document_succeeds(self, client):
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "Upload Co"})
        workspace_id = ws_resp.json()["id"]

        response = await client.post(
            "/api/v1/documents/upload",
            data={"workspace_id": workspace_id},
            files=[("files", ("note.txt", b"Hello world, this is a test document.", "text/plain"))],
        )

        assert response.status_code == 201
        body = response.json()
        assert body["workspace_id"] == workspace_id
        assert len(body["documents"]) == 1
        assert body["documents"][0]["filename"] == "note.txt"
        assert body["documents"][0]["total_chunks"] >= 1

    async def test_upload_rejects_invalid_extension(self, client):
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "Reject Co"})
        workspace_id = ws_resp.json()["id"]

        response = await client.post(
            "/api/v1/documents/upload",
            data={"workspace_id": workspace_id},
            files=[("files", ("malware.exe", b"MZ\x90\x00\x03\x00\x00\x00", "application/octet-stream"))],
        )

        assert response.status_code == 400

    async def test_upload_rejects_path_traversal_filename(self, client):
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "Traversal Co"})
        workspace_id = ws_resp.json()["id"]

        response = await client.post(
            "/api/v1/documents/upload",
            data={"workspace_id": workspace_id},
            files=[("files", ("../../../etc/passwd.txt", b"payload", "text/plain"))],
        )

        assert response.status_code == 400

    async def test_upload_unknown_workspace_returns_404(self, client):
        response = await client.post(
            "/api/v1/documents/upload",
            data={"workspace_id": str(uuid.uuid4())},
            files=[("files", ("note.txt", b"hello", "text/plain"))],
        )
        assert response.status_code == 404


class TestQueryStream:
    async def test_stream_returns_sse_events_for_seeded_chunk(self, client, session_maker):
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "Query Co"})
        workspace_id = uuid.UUID(ws_resp.json()["id"])

        async with session_maker() as session:
            document = Document(workspace_id=workspace_id, filename="a.txt", document_type="txt")
            session.add(document)
            await session.flush()
            chunk = DocumentChunk(
                document_id=document.id,
                content="SourceGuard uses pgvector for hybrid search.",
                chunk_index=0,
            )
            session.add(chunk)
            await session.commit()
            chunk_id = chunk.id

        app.dependency_overrides[get_hybrid_retriever] = lambda: _StubRetriever([chunk_id])

        async with client.stream(
            "POST",
            "/api/v1/query/stream",
            json={"workspace_id": str(workspace_id), "query": "what does SourceGuard use?"},
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            body = "".join([chunk async for chunk in response.aiter_text()])

        assert "event: token" in body
        assert "event: done" in body
        assert "data:" in body

    async def test_stream_returns_error_event_when_no_context_found(self, client):
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "Empty Co"})
        workspace_id = ws_resp.json()["id"]

        app.dependency_overrides[get_hybrid_retriever] = lambda: _StubRetriever([])

        async with client.stream(
            "POST",
            "/api/v1/query/stream",
            json={"workspace_id": workspace_id, "query": "anything"},
        ) as response:
            assert response.status_code == 200
            body = "".join([chunk async for chunk in response.aiter_text()])

        assert "event: error" in body

    async def test_stream_unknown_workspace_returns_404(self, client):
        app.dependency_overrides[get_hybrid_retriever] = lambda: _StubRetriever([])

        response = await client.post(
            "/api/v1/query/stream",
            json={"workspace_id": str(uuid.uuid4()), "query": "anything"},
        )
        assert response.status_code == 404
