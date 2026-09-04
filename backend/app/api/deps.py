import logging
import time
import uuid
from functools import lru_cache

import jwt
import redis.asyncio as redis_async
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWKClient
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models import Workspace
from app.services.embeddings import EmbeddingService
from app.services.retriever import HybridRetriever

logger = logging.getLogger(__name__)


async def get_hybrid_retriever(session: AsyncSession = Depends(get_db)) -> HybridRetriever:
    """Overridable in tests so callers don't need a live Postgres/pgvector instance."""
    return HybridRetriever(session=session, embedding_service=EmbeddingService())


@lru_cache
def _get_jwk_client(supabase_url: str) -> PyJWKClient:
    """One `PyJWKClient` per URL, reused across requests. `PyJWKClient` itself
    caches the fetched JWKS (5 min default TTL) - this just avoids throwing
    that cache away by constructing a fresh client on every call."""
    return PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json")


async def get_current_user(authorization: str | None = Header(default=None)) -> uuid.UUID:
    """Extracts and verifies the Supabase-issued JWT from the `Authorization`
    header, returning the authenticated user's id (the token's `sub` claim).

    Verifies against the project's JWKS endpoint with `algorithms=["ES256"]`,
    not a shared HS256 secret: fetching `{SUPABASE_URL}/auth/v1/.well-known/
    jwks.json` directly showed this project's published signing key is
    ES256 (Supabase's newer, asymmetric JWT-signing-keys scheme) - a static
    HS256 secret can never verify those tokens, which is exactly what
    surfaced as `InvalidAlgorithmError` before this fix.

    Fails closed: `HTTPException(401)` for a missing/malformed header, an
    unresolvable signing key, or a token that fails signature/audience
    verification; `HTTPException(500)` if `SUPABASE_URL` isn't configured.
    Never lets a request through unauthenticated.
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )

    settings = get_settings()
    if not settings.supabase_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_URL is not configured",
        )

    token = authorization.removeprefix("Bearer ").strip()
    jwk_client = _get_jwk_client(settings.supabase_url)

    try:
        signing_key = jwk_client.get_signing_key_from_jwt(token)
    except jwt.PyJWTError as exc:
        # Broad on purpose: get_signing_key_from_jwt() parses the token's
        # header before any key lookup happens, so a malformed token (e.g.
        # not enough "."-separated segments) raises jwt.exceptions.DecodeError
        # here - not jwt.PyJWKClientError - and both are PyJWTError subclasses.
        # Catching only PyJWKClientError let a bad token string bubble up as
        # an unhandled 500 instead of the intended 401.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to resolve token signing key",
        ) from exc

    try:
        payload = jwt.decode(
            token, signing_key.key, algorithms=["ES256"], audience="authenticated"
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc

    try:
        return uuid.UUID(str(payload.get("sub")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 'sub' claim is not a valid UUID",
        ) from exc


async def get_authenticated_db(
    session: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user),
) -> AsyncSession:
    """A `get_db`-shaped session with the RLS tenant context set.

    Sets the Postgres session-local `app.current_user_id` variable via
    `set_config(..., true)` - not a literal `SET LOCAL app.current_user_id =
    ...` - since a bare `SET` statement can't take a bind parameter under
    asyncpg's extended query protocol; `set_config` is a normal parameterized
    function call and is the standard idiom for this exact multi-tenant RLS
    pattern. The `workspace_isolation` policy on `workspaces` (see
    app/db/init_db.py) then resolves to the caller's own rows.

    A no-op on non-Postgres dialects (e.g. the SQLite test engine), since
    neither RLS nor `set_config` exist there - mirrors `PortableVector`'s
    dialect-gated behavior elsewhere in this codebase.
    """
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.current_user_id', :user_id, true)"),
            {"user_id": str(user_id)},
        )
    return session


# Sliding-window log via a Redis sorted set, scored by request timestamp.
# Atomic (single EVAL): ZREMRANGEBYSCORE (drop entries older than the window)
# + ZCARD (count what's left) + the limit check + ZADD all happen as one
# Redis-server-side operation, so concurrent requests from the same user
# can't race between "check" and "record" and both slip through over the
# limit. member is timestamp:uuid4, not just the timestamp, since two
# requests can land on the same float and ZADD would otherwise collapse
# them into a single set member.
_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window)
return 1
"""

QUERY_RATE_LIMIT_MAX_REQUESTS = 10
QUERY_RATE_LIMIT_WINDOW_SECONDS = 60

UPLOAD_RATE_LIMIT_MAX_REQUESTS = 20
UPLOAD_RATE_LIMIT_WINDOW_SECONDS = 60


@lru_cache
def _get_redis_client(redis_url: str) -> redis_async.Redis:
    """One client (and its connection pool) per URL, reused across requests."""
    return redis_async.from_url(redis_url, decode_responses=True)


def get_redis_client() -> redis_async.Redis:
    """Overridable in tests so callers don't need a live Redis instance."""
    return _get_redis_client(get_settings().redis_url)


async def _check_rate_limit(
    redis_client: redis_async.Redis, key: str, max_requests: int, window_seconds: int
) -> bool:
    """Runs the sliding-window script; returns True (and records this
    request) if the caller is still under `max_requests` within
    `window_seconds`, False if the limit was already reached.

    FAILS OPEN on any Redis error: if the rate-limit backend is unreachable
    or erroring, callers are allowed through rather than blocked. The
    tradeoff is deliberate and worth stating plainly - a Redis outage would
    otherwise take the entire API down (every rate-limited route 500s),
    turning a degraded-protection incident into a total outage. The cost is
    that Denial-of-Wallet protection is *disabled* for the duration of such
    an outage, so Redis availability is itself part of the cost-control
    story, not an optional dependency. Logged at WARNING so an outage is
    visible rather than silently un-protected.
    """
    now = time.time()
    member = f"{now}:{uuid.uuid4()}"
    try:
        allowed = await redis_client.eval(
            _SLIDING_WINDOW_SCRIPT, 1, key, now, window_seconds, max_requests, member
        )
    except RedisError as exc:
        logger.warning(
            "Rate limiter failing open for key %s - Redis unavailable: %r", key, exc
        )
        return True
    return bool(allowed)


async def rate_limit_user(
    user_id: uuid.UUID = Depends(get_current_user),
    redis_client: redis_async.Redis = Depends(get_redis_client),
) -> None:
    """Per-user sliding-window rate limit for `/api/v1/query/stream` - the
    LLM-backed endpoint and the primary Denial-of-Wallet exposure. Keyed by
    the authenticated `user_id` (not IP), so it can't be sidestepped by
    rotating source addresses; scoped separately from upload's limit
    (`ratelimit:query:*` vs `ratelimit:upload:*`) so the two don't share a
    budget. Raises `HTTPException(429)` once the limit is hit.
    """
    key = f"ratelimit:query:{user_id}"
    allowed = await _check_rate_limit(
        redis_client, key, QUERY_RATE_LIMIT_MAX_REQUESTS, QUERY_RATE_LIMIT_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: max {QUERY_RATE_LIMIT_MAX_REQUESTS} requests "
                f"per {QUERY_RATE_LIMIT_WINDOW_SECONDS}s. Try again shortly."
            ),
        )


async def rate_limit_upload(
    user_id: uuid.UUID = Depends(get_current_user),
    redis_client: redis_async.Redis = Depends(get_redis_client),
) -> None:
    """Looser per-user sliding-window rate limit for document uploads -
    storage and embedding-cost protection, not the primary LLM-cost target
    but still worth capping (each upload calls EmbeddingService)."""
    key = f"ratelimit:upload:{user_id}"
    allowed = await _check_rate_limit(
        redis_client, key, UPLOAD_RATE_LIMIT_MAX_REQUESTS, UPLOAD_RATE_LIMIT_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: max {UPLOAD_RATE_LIMIT_MAX_REQUESTS} requests "
                f"per {UPLOAD_RATE_LIMIT_WINDOW_SECONDS}s. Try again shortly."
            ),
        )


def ensure_workspace_owner(workspace: Workspace | None, user_id: uuid.UUID) -> Workspace:
    """Raises `HTTPException(404)` unless `workspace` exists and is owned by
    `user_id` - the same 404 whether the workspace doesn't exist at all or
    belongs to someone else, so this can't be used to enumerate other users'
    workspace ids by timing/response-shape.

    This is deliberate defense-in-depth alongside the `workspace_isolation`
    RLS policy (app/db/init_db.py), not a substitute check made redundant by
    it: RLS is Postgres-only and a no-op on the SQLite test database (see
    `get_authenticated_db`), and even on Postgres a superuser or
    `BYPASSRLS`-privileged connection role skips RLS entirely. This
    application-layer check is what actually guarantees - and makes
    testable - that one user can never read or write another's workspace.
    """
    if workspace is None or workspace.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace
