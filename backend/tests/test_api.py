import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_current_user, get_hybrid_retriever, get_redis_client
from app.db.session import get_db
from app.main import app
from app.models import (
    Base,
    ChatMessage,
    ChatSession,
    Document,
    DocumentChunk,
    MessageRole,
    Workspace,
)
from app.services import conversation
from app.services.generation import GenerationService

_TEST_USER_ID = uuid.uuid4()


class _FakeRedis:
    """In-memory reimplementation of `_SLIDING_WINDOW_SCRIPT`'s exact
    semantics (app/api/deps.py), so rate-limited routes work in tests
    without a real Redis connection. Needed for more than convenience: the
    real async Redis client's connection pool binds to the event loop it was
    created in, and a module-level cached client (matching `_get_jwk_client`'s
    pattern) breaks across pytest-asyncio's per-test event loops with
    `RuntimeError: Event loop is closed`."""

    def __init__(self) -> None:
        self._zsets: dict[str, dict[str, float]] = {}

    async def eval(self, script, numkeys, key, now, window, limit, member):
        now, window, limit = float(now), float(window), int(limit)
        zset = self._zsets.setdefault(key, {})
        cutoff = now - window
        for m, score in list(zset.items()):
            if score < cutoff:
                del zset[m]
        if len(zset) >= limit:
            return 0
        zset[member] = now
        return 1


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
    # Every route now requires auth (get_current_user, transitively via
    # get_authenticated_db); this is the one place to stub it for every test.
    app.dependency_overrides[get_current_user] = lambda: _TEST_USER_ID
    # rate_limit_user/rate_limit_upload need a Redis client. One fake instance
    # per test (not a fresh one per request - dependency overrides are called
    # per resolution, and the fake's whole point is to accumulate state
    # across requests within a test), so no test can exhaust another's budget.
    fake_redis = _FakeRedis()
    app.dependency_overrides[get_redis_client] = lambda: fake_redis
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
                    Workspace(name="Oldest Co", user_id=_TEST_USER_ID, created_at=base),
                    Workspace(name="Middle Co", user_id=_TEST_USER_ID, created_at=base + timedelta(hours=1)),
                    Workspace(name="Newest Co", user_id=_TEST_USER_ID, created_at=base + timedelta(hours=2)),
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


class TestConversationMemory:
    """Module 10: chat sessions and the sliding-window history replayed into
    each generation prompt."""

    async def _seeded_workspace(self, client, session_maker, name: str) -> uuid.UUID:
        """Creates a workspace with one retrievable chunk and points the stub
        retriever at it - without retrievable context the endpoint emits an
        `error` event and returns before any conversation logic runs."""
        ws_resp = await client.post("/api/v1/workspaces", json={"name": name})
        workspace_id = uuid.UUID(ws_resp.json()["id"])

        async with session_maker() as session:
            document = Document(workspace_id=workspace_id, filename="a.txt", document_type="txt")
            session.add(document)
            await session.flush()
            chunk = DocumentChunk(
                document_id=document.id,
                content="SourceGuard verifies every claim against its source.",
                chunk_index=0,
            )
            session.add(chunk)
            await session.commit()
            chunk_id = chunk.id

        app.dependency_overrides[get_hybrid_retriever] = lambda: _StubRetriever([chunk_id])
        return workspace_id

    @staticmethod
    def _session_id_from_body(body: str) -> uuid.UUID:
        """Pulls the id off the `session` SSE frame the stream opens with."""
        for line in body.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line.removeprefix("data:").strip())
                if "session_id" in payload:
                    return uuid.UUID(payload["session_id"])
        raise AssertionError(f"no session_id found in stream body: {body!r}")

    async def _run_query(self, client, workspace_id, query, session_id=None) -> str:
        payload = {"workspace_id": str(workspace_id), "query": query}
        if session_id is not None:
            payload["session_id"] = str(session_id)
        async with client.stream("POST", "/api/v1/query/stream", json=payload) as response:
            assert response.status_code == 200
            return "".join([chunk async for chunk in response.aiter_text()])

    async def test_new_session_is_created_when_none_provided(
        self, client, session_maker
    ):
        workspace_id = await self._seeded_workspace(client, session_maker, "New Session Co")

        body = await self._run_query(client, workspace_id, "what does SourceGuard do?")

        assert "event: session" in body
        session_id = self._session_id_from_body(body)

        async with session_maker() as session:
            chat_session = await session.get(ChatSession, session_id)
            assert chat_session is not None
            assert chat_session.workspace_id == workspace_id
            assert chat_session.user_id == _TEST_USER_ID

    async def test_both_turns_are_persisted(self, client, session_maker):
        workspace_id = await self._seeded_workspace(client, session_maker, "Persist Co")

        body = await self._run_query(client, workspace_id, "first question")
        session_id = self._session_id_from_body(body)

        async with session_maker() as session:
            result = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
            )
            messages = list(result.scalars().all())

        assert [m.role for m in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
        assert messages[0].content == "first question"
        assert messages[1].content  # the streamed answer was saved, not empty

    async def test_existing_session_is_reused_and_accumulates_history(
        self, client, session_maker
    ):
        workspace_id = await self._seeded_workspace(client, session_maker, "Reuse Co")

        first = await self._run_query(client, workspace_id, "first question")
        session_id = self._session_id_from_body(first)

        second = await self._run_query(
            client, workspace_id, "second question", session_id=session_id
        )
        assert self._session_id_from_body(second) == session_id

        async with session_maker() as session:
            result = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
            )
            messages = list(result.scalars().all())

        # Two turns x (user + assistant), in chronological order.
        assert len(messages) == 4
        assert [m.role for m in messages] == [
            MessageRole.USER,
            MessageRole.ASSISTANT,
            MessageRole.USER,
            MessageRole.ASSISTANT,
        ]
        assert messages[0].content == "first question"
        assert messages[2].content == "second question"

    async def test_history_is_loaded_in_chronological_order(self, client, session_maker):
        """The prompt history must read oldest-to-newest; `load_recent_messages`
        queries newest-first with LIMIT (so the DB does the windowing) and
        reverses, so the reversal is worth pinning down directly."""
        workspace_id = await self._seeded_workspace(client, session_maker, "History Order Co")
        body = await self._run_query(client, workspace_id, "turn one")
        session_id = self._session_id_from_body(body)

        async with session_maker() as session:
            messages = await conversation.load_recent_messages(session, session_id)

        assert [m.role for m in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
        assert messages[0].created_at <= messages[1].created_at

    async def test_history_window_caps_at_ten_messages(self, client, session_maker):
        """Sliding window: a long conversation must not replay unboundedly.

        Builds the session and its messages directly rather than via a query,
        so the window under test contains exactly the 30 backfilled rows -
        a real turn would add two `datetime.now()` messages that, being
        newer than any backfilled timestamp, would themselves occupy slots
        in the window and obscure the boundary being asserted.
        """
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "Window Co"})
        workspace_id = uuid.UUID(ws_resp.json()["id"])

        async with session_maker() as session:
            chat_session = ChatSession(workspace_id=workspace_id, user_id=_TEST_USER_ID)
            session.add(chat_session)
            await session.flush()
            session_id = chat_session.id

            base = datetime(2024, 1, 1, tzinfo=UTC)
            session.add_all(
                [
                    ChatMessage(
                        session_id=session_id,
                        role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
                        content=f"message {i}",
                        created_at=base + timedelta(minutes=i),
                    )
                    for i in range(30)
                ]
            )
            await session.commit()

            messages = await conversation.load_recent_messages(session, session_id)

        assert len(messages) == conversation.HISTORY_WINDOW_SIZE
        # The *most recent* ten (20..29), still oldest-first.
        assert [m.content for m in messages] == [f"message {i}" for i in range(20, 30)]

    async def test_history_is_injected_into_the_generation_prompt(
        self, client, session_maker, monkeypatch
    ):
        """End-to-end proof that prior turns actually reach GenerationService -
        the mock token stream ignores history, so without capturing the call
        every other test here would pass even if history were never passed."""
        workspace_id = await self._seeded_workspace(client, session_maker, "Inject Co")
        captured: list[list[dict[str, str]] | None] = []

        original = GenerationService.stream_answer

        def _spy(self, query, context_chunks, history=None):
            captured.append(history)
            return original(self, query, context_chunks, history=history)

        monkeypatch.setattr(GenerationService, "stream_answer", _spy)

        first = await self._run_query(client, workspace_id, "first question")
        session_id = self._session_id_from_body(first)
        await self._run_query(client, workspace_id, "second question", session_id=session_id)

        assert captured[0] == [], "first turn should have no prior history"
        assert captured[1] == [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": captured[1][1]["content"]},
        ]
        # The current question is NOT pre-loaded into history (it goes in the
        # final prompt message instead) - otherwise it'd be sent twice.
        assert all(m["content"] != "second question" for m in captured[1])

    async def test_unknown_session_id_returns_404(self, client, session_maker):
        workspace_id = await self._seeded_workspace(client, session_maker, "Bad Session Co")

        response = await client.post(
            "/api/v1/query/stream",
            json={
                "workspace_id": str(workspace_id),
                "query": "anything",
                "session_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 404

    async def test_session_from_another_workspace_returns_404(self, client, session_maker):
        """A session id is only valid within the workspace it was created in -
        otherwise it could be used to splice one workspace's conversation
        into another's retrieval context."""
        workspace_a = await self._seeded_workspace(client, session_maker, "Session WS A")
        body = await self._run_query(client, workspace_a, "question in A")
        session_id = self._session_id_from_body(body)

        workspace_b = await self._seeded_workspace(client, session_maker, "Session WS B")

        response = await client.post(
            "/api/v1/query/stream",
            json={
                "workspace_id": str(workspace_b),
                "query": "anything",
                "session_id": str(session_id),
            },
        )
        assert response.status_code == 404
