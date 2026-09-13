import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, future=True)

AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

# Name of the Postgres session variable the RLS policies read. Defined here
# because both the setter (app/api/deps.py::get_authenticated_db) and the
# teardown reset below must agree on it.
TENANT_SETTING = "app.current_user_id"


async def _clear_tenant_context(session: AsyncSession) -> None:
    """Clears the tenant variable before the connection returns to the pool.

    Required because the variable is set at *session* scope rather than
    transaction scope (see `get_authenticated_db` for why). A session-scoped
    setting outlives the request on a pooled connection, so without this a
    later request that never sets it - an unauthenticated route, or a code
    path using plain `get_db` - could inherit the previous user's tenant
    context and read their rows.

    Set to '' rather than dropped: Postgres offers no "unset a custom GUC"
    for a session, and the policies wrap the read in
    `NULLIF(current_setting(...), '')::uuid`, so an empty value reads as NULL
    and fails closed to "no rows visible".
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    try:
        await session.execute(
            text("SELECT set_config(:name, '', false)"), {"name": TENANT_SETTING}
        )
        await session.commit()
    except Exception:
        # The session may already be unusable (failed transaction, dropped
        # connection). That is safe to ignore: a connection that cannot be
        # reset is discarded rather than pooled, so no context leaks either
        # way. Swallowing here keeps teardown from masking the real error
        # that put the session in this state.
        pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await _clear_tenant_context(session)


@asynccontextmanager
async def tenant_session(user_id: uuid.UUID) -> AsyncGenerator[AsyncSession, None]:
    """A session with the RLS tenant context set, for work outside a request.

    `get_authenticated_db` cannot be reused by a background task: it is a
    FastAPI dependency whose session is closed - and whose tenant context is
    cleared - as soon as the response is sent. A task that outlives the
    response would otherwise write with no tenant set, and every INSERT would
    be rejected by the RLS policies it is supposed to be governed by.

    Mirrors the request path exactly: session scope (`false`), because
    ingestion commits several times, and a transaction-scoped setting is
    discarded at each commit.
    """
    async with AsyncSessionLocal() as session:
        try:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                await session.execute(
                    text("SELECT set_config(:name, :user_id, false)"),
                    {"name": TENANT_SETTING, "user_id": str(user_id)},
                )
            yield session
        finally:
            await _clear_tenant_context(session)
