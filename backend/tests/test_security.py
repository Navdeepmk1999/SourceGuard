import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import (
    QUERY_RATE_LIMIT_MAX_REQUESTS,
    get_current_user,
    get_redis_client,
)
from app.core.config import get_settings
from app.db.session import get_db
from app.main import app
from app.models import Base, Workspace


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

# Real verification (app/api/deps.py::get_current_user) is JWKS/ES256, not a
# shared HS256 secret - this project's actual Supabase signing key is
# asymmetric (confirmed by reading its JWKS endpoint directly). Tests sign
# with a throwaway EC key pair and stub the JWKS lookup (`_get_jwk_client`)
# to hand back its public half, so no real network call to Supabase happens
# and no real secret is needed.
_TEST_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_TEST_PUBLIC_KEY = _TEST_PRIVATE_KEY.public_key()
_WRONG_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())


class _StubSigningKey:
    def __init__(self, key) -> None:
        self.key = key


class _StubJWKClient:
    """Stands in for `jwt.PyJWKClient`: always resolves to the test public
    key, regardless of the token's `kid` header."""

    def get_signing_key_from_jwt(self, token: str) -> _StubSigningKey:
        return _StubSigningKey(_TEST_PUBLIC_KEY)


def _make_token(
    user_id: uuid.UUID,
    *,
    private_key=_TEST_PRIVATE_KEY,
    audience: str = "authenticated",
    exp_delta: timedelta | None = None,
    include_sub: bool = True,
) -> str:
    payload: dict = {"aud": audience, "role": "authenticated"}
    if include_sub:
        payload["sub"] = str(user_id)
    if exp_delta is not None:
        payload["exp"] = datetime.now(UTC) + exp_delta
    return jwt.encode(payload, private_key, algorithm="ES256")


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
async def real_auth_client(session_maker, monkeypatch):
    """A client that does NOT stub `get_current_user` - the real JWT
    verification in app/api/deps.py runs on every request. `SUPABASE_URL` is
    monkeypatched (a project URL is required to construct the JWKS endpoint,
    but its value doesn't matter here since `_get_jwk_client` itself is
    replaced with `_StubJWKClient`, so no real network call is made)."""
    monkeypatch.setenv("SUPABASE_URL", "https://test-project.supabase.co")
    get_settings.cache_clear()
    monkeypatch.setattr("app.api.deps._get_jwk_client", lambda supabase_url: _StubJWKClient())

    async def override_get_db():
        async with session_maker() as session:
            yield session

    fake_redis = _FakeRedis()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_client] = lambda: fake_redis
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def multi_user_client(session_maker):
    """A client whose authenticated user can be switched mid-test via
    `current_user["id"] = <uuid>`, to exercise cross-user isolation without
    needing real JWTs for every request."""
    current_user: dict[str, uuid.UUID | None] = {"id": None}

    async def override_get_db():
        async with session_maker() as session:
            yield session

    def override_get_current_user() -> uuid.UUID:
        assert current_user["id"] is not None, "set current_user['id'] before making a request"
        return current_user["id"]

    fake_redis = _FakeRedis()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_redis_client] = lambda: fake_redis
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, current_user
    app.dependency_overrides.clear()


class TestJWTValidation:
    """`app/api/deps.py::get_current_user` must reject every unverifiable
    token before any protected route logic runs."""

    async def test_missing_authorization_header_returns_401(self, real_auth_client):
        response = await real_auth_client.get("/api/v1/workspaces")
        assert response.status_code == 401

    async def test_malformed_authorization_header_returns_401(self, real_auth_client):
        response = await real_auth_client.get(
            "/api/v1/workspaces", headers={"Authorization": "NotBearer sometoken"}
        )
        assert response.status_code == 401

    async def test_malformed_token_string_returns_401_not_500(self, real_auth_client):
        """A well-formed `Bearer <token>` header whose token isn't even
        parseable JWT (not enough "."-separated segments) must still 401 -
        not fall through as an unhandled 500. `get_signing_key_from_jwt()`
        raises `jwt.exceptions.DecodeError` here, which is a `PyJWTError`
        but not a `PyJWKClientError`; the except clause around that call
        must catch the broader type."""
        response = await real_auth_client.get(
            "/api/v1/workspaces", headers={"Authorization": "Bearer not.a.real.token"}
        )
        assert response.status_code == 401

    async def test_invalid_signature_returns_401(self, real_auth_client):
        token = _make_token(uuid.uuid4(), private_key=_WRONG_PRIVATE_KEY)
        response = await real_auth_client.get(
            "/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401

    async def test_expired_token_returns_401(self, real_auth_client):
        token = _make_token(uuid.uuid4(), exp_delta=timedelta(hours=-1))
        response = await real_auth_client.get(
            "/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401

    async def test_wrong_audience_returns_401(self, real_auth_client):
        token = _make_token(uuid.uuid4(), audience="not-authenticated")
        response = await real_auth_client.get(
            "/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401

    async def test_missing_sub_claim_returns_401(self, real_auth_client):
        token = _make_token(uuid.uuid4(), include_sub=False)
        response = await real_auth_client.get(
            "/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401

    async def test_valid_token_is_accepted(self, real_auth_client):
        token = _make_token(uuid.uuid4())
        response = await real_auth_client.get(
            "/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200


class TestCrossUserIsolation:
    """Defense-in-depth ownership check (`ensure_workspace_owner` in
    app/api/deps.py) - not Postgres RLS itself, which is inert on this
    SQLite test database - is what's under test here. See DESIGN.md's
    Module 9 section for why RLS alone can't be exercised in this suite."""

    async def test_user_b_does_not_see_user_a_workspace_in_listing(self, multi_user_client):
        client, current_user = multi_user_client
        user_a, user_b = uuid.uuid4(), uuid.uuid4()

        current_user["id"] = user_a
        await client.post("/api/v1/workspaces", json={"name": "User A's Workspace"})

        current_user["id"] = user_b
        response = await client.get("/api/v1/workspaces")
        assert response.status_code == 200
        assert response.json() == []

    async def test_user_b_gets_404_viewing_user_a_workspace_documents(self, multi_user_client):
        client, current_user = multi_user_client
        user_a, user_b = uuid.uuid4(), uuid.uuid4()

        current_user["id"] = user_a
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "User A Docs Co"})
        workspace_id = ws_resp.json()["id"]

        current_user["id"] = user_b
        response = await client.get(f"/api/v1/workspaces/{workspace_id}/documents")
        assert response.status_code == 404

    async def test_user_b_gets_404_uploading_to_user_a_workspace(self, multi_user_client):
        client, current_user = multi_user_client
        user_a, user_b = uuid.uuid4(), uuid.uuid4()

        current_user["id"] = user_a
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "User A Upload Co"})
        workspace_id = ws_resp.json()["id"]

        current_user["id"] = user_b
        response = await client.post(
            "/api/v1/documents/upload",
            data={"workspace_id": workspace_id},
            files=[("files", ("note.txt", b"hello", "text/plain"))],
        )
        assert response.status_code == 404

    async def test_user_b_gets_404_streaming_query_against_user_a_workspace(self, multi_user_client):
        client, current_user = multi_user_client
        user_a, user_b = uuid.uuid4(), uuid.uuid4()

        current_user["id"] = user_a
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "User A Query Co"})
        workspace_id = ws_resp.json()["id"]

        current_user["id"] = user_b
        response = await client.post(
            "/api/v1/query/stream",
            json={"workspace_id": workspace_id, "query": "anything"},
        )
        assert response.status_code == 404

    async def test_user_a_can_still_access_their_own_workspace(self, multi_user_client):
        """Isolation must not be a blanket deny - the owner keeps access."""
        client, current_user = multi_user_client
        user_a = uuid.uuid4()

        current_user["id"] = user_a
        ws_resp = await client.post("/api/v1/workspaces", json={"name": "User A Own Co"})
        workspace_id = ws_resp.json()["id"]

        response = await client.get(f"/api/v1/workspaces/{workspace_id}/documents")
        assert response.status_code == 200
        assert response.json() == []


class TestOwnershipStamping:
    async def test_create_workspace_stamps_user_id_on_the_row(self, multi_user_client, session_maker):
        client, current_user = multi_user_client
        user_id = uuid.uuid4()
        current_user["id"] = user_id

        response = await client.post("/api/v1/workspaces", json={"name": "Stamped Co"})
        assert response.status_code == 201
        workspace_id = uuid.UUID(response.json()["id"])

        async with session_maker() as session:
            workspace = await session.get(Workspace, workspace_id)
            assert workspace is not None
            assert workspace.user_id == user_id


class TestQueryRateLimiting:
    """`app/api/deps.py::rate_limit_user`, applied to `POST /api/v1/query/
    stream` - the most critical endpoint to protect, per Module 9's rate-
    limiting scope. Uses a nonexistent `workspace_id`: requests that get
    past the rate limiter reach normal business logic and 404 (workspace
    not found) - the rate limiter itself doesn't care whether the workspace
    is real, only whether the caller has budget left within the window."""

    async def test_requests_under_the_limit_pass_through_to_normal_processing(
        self, multi_user_client
    ):
        client, current_user = multi_user_client
        current_user["id"] = uuid.uuid4()
        payload = {"workspace_id": str(uuid.uuid4()), "query": "anything"}

        for i in range(QUERY_RATE_LIMIT_MAX_REQUESTS):
            response = await client.post("/api/v1/query/stream", json=payload)
            assert response.status_code == 404, f"request {i + 1} should have passed the rate limiter"

    async def test_request_exceeding_the_limit_returns_429(self, multi_user_client):
        client, current_user = multi_user_client
        current_user["id"] = uuid.uuid4()
        payload = {"workspace_id": str(uuid.uuid4()), "query": "anything"}

        for i in range(QUERY_RATE_LIMIT_MAX_REQUESTS):
            response = await client.post("/api/v1/query/stream", json=payload)
            assert response.status_code == 404, f"request {i + 1} should have passed the rate limiter"

        response = await client.post("/api/v1/query/stream", json=payload)
        assert response.status_code == 429

    async def test_rate_limiter_fails_open_when_redis_is_unavailable(
        self, multi_user_client, monkeypatch
    ):
        """A Redis outage must not take the API down with it. `_check_rate_limit`
        swallows RedisError and allows the request through (see its docstring
        for why availability wins over strict enforcement here) - so far more
        than the limit's worth of requests should still pass to normal
        processing rather than 429-ing or 500-ing."""
        client, current_user = multi_user_client
        current_user["id"] = uuid.uuid4()
        payload = {"workspace_id": str(uuid.uuid4()), "query": "anything"}

        class _BrokenRedis:
            async def eval(self, *args, **kwargs):
                # redis.exceptions.ConnectionError (a RedisError), NOT Python's
                # builtin ConnectionError - the builtin is an OSError and would
                # not be caught by _check_rate_limit's `except RedisError`.
                raise RedisConnectionError("Redis is down")

        app.dependency_overrides[get_redis_client] = _BrokenRedis

        for i in range(QUERY_RATE_LIMIT_MAX_REQUESTS + 5):
            response = await client.post("/api/v1/query/stream", json=payload)
            assert response.status_code == 404, (
                f"request {i + 1} should have failed open past the rate limiter, "
                f"got {response.status_code}"
            )

    async def test_rate_limit_is_scoped_per_user(self, multi_user_client):
        """User A hitting their limit must not affect User B's own budget."""
        client, current_user = multi_user_client
        user_a, user_b = uuid.uuid4(), uuid.uuid4()
        payload = {"workspace_id": str(uuid.uuid4()), "query": "anything"}

        current_user["id"] = user_a
        for _ in range(QUERY_RATE_LIMIT_MAX_REQUESTS):
            await client.post("/api/v1/query/stream", json=payload)
        exhausted = await client.post("/api/v1/query/stream", json=payload)
        assert exhausted.status_code == 429

        current_user["id"] = user_b
        response = await client.post("/api/v1/query/stream", json=payload)
        assert response.status_code == 404
